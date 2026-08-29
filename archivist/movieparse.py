#!/usr/bin/env python3
# movieparse.py — TAS movie-file parsers for toolAssisted.run
#
# This module is a Python re-implementation of TASVideos' movie parsers
# (TASVideos.Parsers, C#), studied and ported from the TASVideos source code:
#   https://github.com/TASVideos/tasvideos  (TASVideos.Parsers project)
# Credit for the format knowledge and parsing logic belongs to the TASVideos
# contributors — they are the primary authors of the source material.
#
# The TASVideos code base is licensed under the GNU General Public License
# v3.0; accordingly, THIS FILE is likewise distributed under the GPL-3.0
# (unlike the rest of this repository, which is MIT). See
# https://www.gnu.org/licenses/gpl-3.0.html
"""Parse TAS movie files: frames, rerecords, start type, system, frame rate.

parse(filename, data) -> dict with keys:
  ok (bool) · format · frames · rerecords (None if absent) · start
  ('power-on'|'savestate'|'sram') · system (tasvideos system code or None) ·
  fps (float override or None) · warnings (list) · error (when not ok)
"""
import gzip
import io
import json
import math
import re
import struct
import tarfile
import zipfile
import zlib
import xml.etree.ElementTree as ET

NTSC_NES = 60.0988138974405
NTSC_SNES = 60.0988138974405
PAL_SNES = 50.0069789081886
NTSC_SAT = 59.8830284837373
NTSC_PSX = 59.94006013870239
PAL_PSX = 50.00028192996979
DOOM_FPS = 35.0029869215506


def _ok(fmt, frames=0, rerecords=None, start='power-on', system=None, fps=None,
        warnings=None):
    # Several formats derive the frame count from the file's own length
    # ((len - header) // stride) or from header bytes the uploader controls, so
    # a truncated or hostile file can compute a negative length. Frames feed
    # rankings, so refuse rather than archive nonsense.
    frames = int(frames)
    if frames < 0:
        return _err(fmt, 'Negative frame count: the file looks truncated')
    return {'ok': True, 'format': fmt, 'frames': frames,
            'rerecords': rerecords, 'start': start, 'system': system,
            'fps': fps, 'warnings': warnings or []}


def _err(fmt, msg):
    return {'ok': False, 'format': fmt, 'error': msg}


def _lines(text):
    return [l for l in re.split(r'\r\n|\r|\n', text) if l]


def _value_for(lines, key):
    """Space-separated key/value lookup, case-insensitive; value lowercased
    (mirrors TASVideos' GetValueFor)."""
    key_l = key.lower()
    for l in lines:
        if l.lower().startswith(key_l):
            return l.lower().replace(key_l, '').strip()
    return ''


def _has_value(lines, key):
    key_l = key.lower()
    return any(l.lower().startswith(key_l) and l.lower().replace(key_l, '').strip()
               for l in lines)


def _bool_for(lines, key):
    v = _value_for(lines, key)
    if not v:
        return False
    try:
        return int(v) == 1
    except ValueError:
        return v == 'true'


def _int_for(lines, key):
    v = _value_for(lines, key)
    try:
        n = int(v)
        return n if n >= 0 else None
    except ValueError:
        return None


def _pipe_header_and_frames(text):
    header, frames = [], 0
    for line in text.splitlines():
        if line.startswith('|'):
            frames += 1
        else:
            header.append(line)
    return header, frames


# ---------------------------------------------------------------- bk2 family
BIZ_TO_TASV = {'gen': 'genesis', 'sat': 'saturn', 'dgb': 'gb', 'gb3x': 'gb',
               'gb4x': 'gb', 'gbl': 'gb', 'gbal': 'gba', 'a26': 'a2600',
               'a78': 'a7800', 'uze': 'uzebox', 'vb': 'vboy',
               'zxspectrum': 'zxs', 'nds': 'ds',
               'dc': 'dreamcast'}      # Chimera writes the Dreamcast as DC
CYCLE_BASED_CORES = {'subgbhawk': 4194304, 'gambatte': 2097152}
VALID_CLOCK_RATES = {'4194304', '2097152', '5369318.18181818', '5320342.5',
                     '33868800', '21477272.7272727', '21281370', '16777216'}
BK2_INVALID = ['greenzonesettings.txt', 'laglog', 'markers.txt',
               'clientsettings.json', 'session.txt', 'greenzone']
TASPROJ_INVALID = ['greenzone']


# Chimera's own format: a project IS the movie (docs/project.md in
# ToolAssisted-run/chimera). One JSON file holding the core pin, the file
# manifest by SHA1, the settings, and the [Input] lump verbatim.
#
# The run's own markers (Run start, Last input, Run end) are DERIVED, never
# stored: Chimera recomputes them on load. Since 2026-08-28 a save writes
# the answer down as the LastInputFrame header, and the rate it actually
# ran at as VsyncNumerator / VsyncDenominator, so a project written by a
# current build is read exactly. Older ones are walked instead, by the rule
# Chimera itself uses: "the last frame anything is pressed on". Either way
# that is what the frame count reports, because idle frames a TASer left
# after the last press are not part of the run's time.
#
# A project saved before that date may hold ORDINARY markers named "Run
# start", "Last input" and "Run end" (a round-trip bug in Chimera, fixed
# there): they are stale snapshots of an old save, so nothing here reads a
# marker.
CHIMERA_LAST_INPUT_KEYS = ('lastInputFrame', 'lastInput')


def _chimera_neutral_axes(rows):
    """The value each analog axis rests at, taken as the one it holds most.

    A digital button says plainly whether it is pressed ('.' or a letter);
    an axis does not, and its neutral belongs to the core package rather
    than to the movie. The value an axis spends most of the run at is that
    neutral in every real movie; a run that holds one axis off-centre for
    most of its length is the case this cannot see, and it is warned about.
    """
    seen = {}
    for row in rows:
        for i, field in enumerate(row):
            if field is None:
                continue
            seen.setdefault(i, {})
            seen[i][field] = seen[i].get(field, 0) + 1
    return {i: max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
            for i, counts in seen.items()}


def _chimera_split(line):
    """One input line as (masks, axes): the dot-masks and the numeric fields."""
    masks, axes = [], []
    for section in line.strip('|').split('|'):
        if ',' in section or section.strip().lstrip('-').isdigit():
            axes.extend(v.strip() for v in section.split(','))
        else:
            masks.append(section)
    return masks, axes


def parse_chimeraproject(data):
    fmt = 'chimeraProject'
    try:
        doc = json.loads(data.decode('utf-8', 'replace'))
    except ValueError:
        return _err(fmt, 'Invalid file format, does not seem to be a chimeraProject')
    if not isinstance(doc, dict) or 'input' not in doc:
        return _err(fmt, 'Missing the input log, can not parse')
    warnings = []

    headers = doc.get('headers') if isinstance(doc.get('headers'), dict) else {}
    lower = {str(k).lower(): v for k, v in headers.items()}
    platform = str(lower.get('platform') or '').strip().lower()
    system = BIZ_TO_TASV.get(platform, platform) or None
    if not system:
        warnings.append('the project names no platform; the game decides the frame rate')

    # Chimera carries no per-system rate table of its own (its
    # PlatformFrameRates answers a flat 50 or 60), and the project pins no
    # vsync, so the rate is left to the game's system here, which is the
    # exact one. A PAL project on an NTSC system would be rated wrongly, so
    # it says so; and when the format grows the numbers, they win.
    fps = None
    num, den = lower.get('vsyncnumerator'), lower.get('vsyncdenominator')
    try:
        if num and den and float(den):
            fps = float(num) / float(den)
    except (TypeError, ValueError):
        fps = None
    if fps is None and str(lower.get('pal') or '').strip().lower() in ('1', 'true', 'yes'):
        warnings.append('the project says PAL: the rate applied is the system\'s own')

    rerecords = doc.get('rerecords')
    if not isinstance(rerecords, int) or isinstance(rerecords, bool) or rerecords < 0:
        rerecords = None
        warnings.append('missing rerecord count')

    lines = [l for l in re.split(r'\r\n|\r|\n', str(doc.get('input') or ''))
             if l.startswith('|')]
    if not lines:
        return _err(fmt, 'The input log holds no frames, can not parse')

    rows = [_chimera_split(l) for l in lines]
    axis_count = max((len(a) for _, a in rows), default=0)
    if axis_count:
        warnings.append('analog axes: their resting value is read off the log, '
                        'not off the core')
    neutral = _chimera_neutral_axes([a for _, a in rows]) if axis_count else {}

    def pressed(row):
        masks, axes = row
        if any(c not in '. ' for mask in masks for c in mask):
            return True
        return any(v != neutral.get(i) for i, v in enumerate(axes))

    last_input = next((i for i in range(len(rows) - 1, -1, -1) if pressed(rows[i])), 0)
    for stated in [lower.get('lastinputframe')] + [doc.get(k) for k in CHIMERA_LAST_INPUT_KEYS]:
        try:                                   # the project's own answer, when it has one
            frame = int(str(stated).strip())
        except (TypeError, ValueError):
            continue
        if 0 <= frame < len(rows):
            last_input = frame
            break
    # Frame zero is where "the last input" sits both when the run's only
    # press is on it and when the run has no press at all, and neither the
    # header nor the walk can tell those apart. The log can: if frame zero
    # is neutral too, nothing is pressed anywhere, and the honest length of
    # a movie whose input never starts is the log's own.
    if last_input == 0 and not pressed(rows[0]):
        warnings.append('nothing is pressed anywhere in this project: the '
                        'length is the input log, not the run')
        return _ok(fmt, len(rows), rerecords, 'power-on', system, fps, warnings)
    idle = len(rows) - 1 - last_input
    if idle > 0:
        warnings.append(f'{idle} frame{"s" if idle != 1 else ""} after the last '
                        f'input are not counted as run time')
    return _ok(fmt, last_input + 1, rerecords, 'power-on', system, fps, warnings)


def parse_bk2(data, fmt='bk2'):
    invalid_entries = TASPROJ_INVALID if fmt == 'tasproj' else BK2_INVALID
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return _err(fmt, f'Invalid file format, does not seem to be a {fmt}')
    names = {n.lower(): n for n in z.namelist()}
    for bad in invalid_entries:
        if bad in names:
            return _err(fmt, f'Invalid {fmt}, cannot contain a {bad} file')
    header_name = next((n for n in z.namelist()
                        if n.lower().startswith('header')
                        and not any(c.isdigit() for c in n)), None)
    if header_name is None:
        return _err(fmt, 'Missing header, can not parse')
    header = _lines(z.read(header_name).decode('utf-8', 'replace'))

    warnings = []
    platform = _value_for(header, 'platform')
    if not platform:
        return _err(fmt, 'Could not determine the System Code')
    rerecords = _int_for(header, 'rerecordcount')
    if rerecords is None:
        warnings.append('missing rerecord count')
    pal = _bool_for(header, 'pal')
    platform = BIZ_TO_TASV.get(platform, platform)
    fps = None
    if _bool_for(header, 'is32x'):
        platform = '32x'
    elif _bool_for(header, 'iscgbmode'):
        platform = 'gbc'
    elif _value_for(header, 'boardname') == 'fds':
        platform = 'fds'
    elif _bool_for(header, 'isvs'):
        platform = 'arcade'
        fps = NTSC_NES
    elif _bool_for(header, 'isstv'):
        platform = 'arcade'
        fps = NTSC_SAT
    elif _value_for(header, 'boardname') == 'sgb':
        platform = 'sgb'
        fps = PAL_SNES if pal else NTSC_SNES
    elif _bool_for(header, 'issegacdmode'):
        platform = 'segacd'
    elif _bool_for(header, 'isggmode'):
        platform = 'gg'
    elif _bool_for(header, 'issgmode'):
        platform = 'sg1000'
    elif _bool_for(header, 'isdsi'):
        platform = 'dsi'
    elif _bool_for(header, 'isdd'):
        platform = 'n64dd'
    elif _bool_for(header, 'isjaguarcd'):
        platform = 'jaguarcd'

    start = 'power-on'
    if _bool_for(header, 'startsfromsavestate'):
        start = 'savestate'
    elif _bool_for(header, 'startsfromsaveram'):
        start = 'sram'

    vsync_atto = _int_for(header, 'vsyncattoseconds')
    vblank_count = _int_for(header, 'vblankcount')
    cycle_count = _int_for(header, 'cyclecount')
    clock_rate = _value_for(header, 'clockrate').replace(',', '.')
    core = _value_for(header, 'core')

    input_name = next((n for n in z.namelist()
                       if n.lower().startswith('input log')), None)
    if input_name is None:
        return _err(fmt, 'Missing input log, can not parse')
    frames = 0
    with z.open(input_name) as f:
        for line in io.TextIOWrapper(f, encoding='utf-8', errors='replace'):
            if line.startswith('|'):
                frames += 1

    if core == 'octoshock':
        fps = PAL_PSX if pal else NTSC_PSX

    if cycle_count is not None:
        if clock_rate == '1000':
            # DOSBox-X: the cycle count is a millisecond count. The frames
            # stay the input log's own lines; the rate follows (issue #67:
            # storing the milliseconds as frames showed 1.9M "frames" on a
            # 113k-frame movie)
            seconds = cycle_count / 1000.0
            fps = frames / seconds if seconds else None
        elif clock_rate in VALID_CLOCK_RATES:
            seconds = cycle_count / float(clock_rate)
            fps = frames / seconds if seconds else None
        elif core in CYCLE_BASED_CORES:
            seconds = cycle_count / CYCLE_BASED_CORES[core]
            fps = frames / seconds if seconds else None
        else:
            return _err(fmt, 'Missing or invalid ClockRate, could not parse movie time')
    else:
        if core == 'subneshawk':
            if vblank_count is None:
                return _err(fmt, 'Missing VBlankCount, could not parse movie time')
            frames = vblank_count
        elif core == 'mame':
            if vsync_atto is None:
                return _err(fmt, 'Missing VsyncAttoseconds, could not parse movie time')
            fps = 1e18 / vsync_atto

    return _ok(fmt, frames, rerecords, start, platform, fps, warnings)


# ---------------------------------------------------------------- text logs
def parse_fm2(data, fmt='fm2'):
    header, frames = _pipe_header_and_frames(data.decode('utf-8', 'replace'))
    warnings = []
    if fmt == 'fm3':
        if _int_for(header, 'version') != 3:
            return _err(fmt, 'Invalid FM3 version')
        for req in ('romFilename', 'romChecksum', 'guid'):
            if not _value_for(header, req):
                return _err(fmt, f'Missing required {req} field')
    if _bool_for(header, 'binary'):
        n = _int_for(header, 'length')
        if n is None:
            return _err(fmt, 'No frame count found for binary format')
        frames = n
    system = 'fds' if _bool_for(header, 'fds') else 'nes'
    rerecords = _int_for(header, 'rerecordCount')
    if rerecords is None:
        warnings.append('missing rerecord count')
    start = 'savestate' if _has_value(header, 'savestate') else 'power-on'
    return _ok(fmt, frames, rerecords, start, system, None, warnings)


def parse_dsm(data):
    header, frames = _pipe_header_and_frames(data.decode('utf-8', 'replace'))
    warnings = []
    rerecords = _int_for(header, 'rerecordcount')
    if rerecords is None:
        warnings.append('missing rerecord count')
    start = 'power-on'
    sv = _value_for(header, 'savestate')
    if sv and sv != '0':
        start = 'savestate'
    if _has_value(header, 'sram'):
        start = 'sram'
    return _ok('dsm', frames, rerecords, start, 'ds', None, warnings)


# ---------------------------------------------------------------- binary
def parse_gmv(data):
    if not data[:16].decode('latin-1').startswith('Gens Movie'):
        return _err('gmv', 'Invalid file format, does not seem to be a gmv')
    rerecords = struct.unpack_from('<i', data, 16)[0]
    flags = data[22]
    start = 'savestate' if flags & 0x40 else 'power-on'
    frames = (len(data) - 64) // 3
    return _ok('gmv', frames, rerecords, start, 'genesis')


def parse_vbm(data):
    if not data[:4].decode('latin-1').startswith('VBM'):
        return _err('vbm', 'Invalid file format, does not seem to be a vbm')
    frames, rerecords = struct.unpack_from('<ii', data, 12)
    t = data[20]
    start = 'savestate' if t & 1 else ('sram' if t & 2 else 'power-on')
    s = data[22]
    system = 'gba' if s & 1 else 'gbc' if s & 2 else 'sgb' if s & 4 else 'gb'
    return _ok('vbm', frames, rerecords, start, system)


def parse_dtm(data):
    if data[:4] != b'DTM\x1a':
        return _err('dtm', 'Invalid file format, does not seem to be a dtm')
    is_wii = data[10] > 0
    system = 'wii' if is_wii else 'gc'
    start = 'savestate' if data[12] > 0 else 'power-on'
    frames = struct.unpack_from('<q', data, 13)[0]
    rerecords = struct.unpack_from('<i', data, 45)[0]
    has_cards = data[151] > 0
    card_blank = data[152] > 0
    if has_cards and not card_blank:
        start = 'sram'
    cycles = struct.unpack_from('<q', data, 237)[0]
    warnings = []
    if cycles:
        hertz = 729000000.0 if is_wii else 486000000.0
        frames = math.ceil(cycles / hertz * 60.0)
    else:
        warnings.append('movie length inferred from VI count')
    return _ok('dtm', frames, rerecords, start, system, None, warnings)


def parse_m64(data):
    if data[:4] != b'M64\x1a':
        return _err('m64', 'Invalid file format, does not seem to be a m64')
    frames = struct.unpack_from('<I', data, 12)[0]
    rerecords = struct.unpack_from('<I', data, 16)[0]
    fps = data[20]
    t = data[28]
    start = ('savestate' if t & 1 else 'power-on' if t & 2 else
             'sram' if t & 4 else 'power-on')
    return _ok('m64', frames, rerecords, start, 'n64',
               50.0 if fps == 50 else None)


def parse_mar(data):
    if data[:8] != b'MAMETAS\x00':
        return _err('mar', 'Invalid file format, does not seem to be a mar')
    fps = struct.unpack_from('<d', data, 48)[0]
    frames, rerecords = struct.unpack_from('<ii', data, 56)
    return _ok('mar', frames, rerecords, 'power-on', 'arcade',
               fps if fps > 0 else None)


def parse_fbm(data):
    if data[:4] != b'FB1 ':
        return _err('fbm', 'Invalid file format, does not seem to be a fbm')
    pos = 5
    start = 'power-on'
    nxt = data[pos:pos + 4]
    pos += 4
    if nxt == b'FS1 ':
        start = 'savestate'
        pos += 16
        state_len = struct.unpack_from('<i', data, pos)[0]
        pos += 4 + 32 + 4 + 12 + state_len
        nxt = data[pos:pos + 4]
        pos += 4
    if nxt != b'FR1 ':
        return _err('fbm', 'Input data not found')
    pos += 4
    frames, rerecords = struct.unpack_from('<ii', data, pos)
    return _ok('fbm', frames, rerecords, start, 'arcade')


def parse_p2m2(data):
    if data[1:6] != b'PCSX2':
        return _err('p2m2', 'Invalid file format, does not seem to be a p2m2')
    pos = 1 + 5 + 2 + 43 + 255 + 255
    frames, rerecords = struct.unpack_from('<ii', data, pos)
    start = 'savestate' if data[pos + 8] > 0 else 'power-on'
    return _ok('p2m2', frames, rerecords, start, 'ps2')


def parse_ctm(data):
    if data[:4] != b'CTM\x1b':
        return _err('ctm', 'Invalid file format, does not seem to be a ctm')
    pos = 4 + 8 + 20 + 8 + 8 + 32
    rerecords = struct.unpack_from('<i', data, pos)[0]
    inputs = struct.unpack_from('<Q', data, pos + 4)[0]
    frame_rate = 268111856.0 / 4481136.0
    frames = math.ceil(inputs / 234 * frame_rate)
    return _ok('ctm', frames, rerecords, 'power-on', '3ds')


def parse_wtf(data):
    if struct.unpack_from('<i', data, 0)[0] != 41374822:
        return _err('wtf', 'Invalid file format, does not seem to be a wtf')
    rerecords = struct.unpack_from('<i', data, 8)[0]
    fps = struct.unpack_from('<I', data, 20)[0]
    frames = (len(data) - 1024) // 8
    return _ok('wtf', frames, rerecords, 'power-on', 'pc',
               float(fps - 1) if fps > 1 else None)


def parse_gzm(data):
    try:
        pos = 0
        frame_count, seed = struct.unpack_from('>II', data, pos)
        pos += 8 + 2 + 1 + 1
        pos += frame_count * 6
        pos += seed * 12
        oca_input, oca_sync, room_load = struct.unpack_from('>III', data, pos)
        pos += 12
        pos += oca_input * 8 + oca_sync * 8 + room_load * 4
        rerecords, frames = struct.unpack_from('>II', data, pos)
        pos += 8
        if pos != len(data):
            return _err('gzm', 'Invalid file format, does not seem to be a gzm')
        return _ok('gzm', frames, rerecords, 'power-on', 'n64', 60.0)
    except struct.error:
        return _err('gzm', 'Misformatted file')


# ---------------------------------------------------------------- archives
def parse_lsmv(data):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return _err('lsmv', 'Invalid file format, does not seem to be a lsmv')
    names = {n.lower(): n for n in z.namelist()}
    if 'savestate' in names:
        return _err('lsmv', 'This is a savestate file, not a movie file')
    start = 'power-on'
    if any(n.lower().startswith('savestate.anchor') for n in z.namelist()):
        start = 'savestate'
    elif 'moviesram' in names and z.getinfo(names['moviesram']).file_size > 0:
        start = 'sram'
    warnings = []
    system, fps_region = 'snes', None
    gt_name = next((n for n in z.namelist() if n.lower().startswith('gametype')), None)
    if gt_name is None:
        return _err('lsmv', 'Could not determine the System Code')
    gt = _lines(z.read(gt_name).decode('utf-8', 'replace'))
    line = gt[0].lower() if gt else None
    if line in ('snes_ntsc', 'bsx', 'bsxslotted', 'sufamiturbo'):
        system = 'snes'
    elif line == 'snes_pal':
        system = 'snes'
    elif line == 'sgb_ntsc':
        system = 'sgb'
    elif line == 'sgb_pal':
        system = 'sgb'
    elif line == 'gdmg':
        system = 'gb'
    elif line in ('ggbc', 'ggbca'):
        system = 'gbc'
    else:
        warnings += ['system id inferred', 'region inferred']
    rerecords = None
    rr_name = next((n for n in z.namelist() if n.lower().startswith('rerecords')), None)
    if rr_name is not None:
        rr = _lines(z.read(rr_name).decode('utf-8', 'replace'))
        try:
            rerecords = int(rr[0]) if rr else None
        except ValueError:
            rerecords = None
    if rerecords is None:
        warnings.append('missing rerecord count')
    input_name = next((n for n in z.namelist()
                       if n.lower().startswith('input')
                       and not any(c.isdigit() for c in n)), None)
    if input_name is None:
        return _err('lsmv', 'Missing input, can not parse')
    frames = sum(1 for l in _lines(z.read(input_name).decode('utf-8', 'replace'))
                 if l.startswith('F'))
    return _ok('lsmv', frames, rerecords, start, system, None, warnings)


def parse_ltm(data):
    try:
        tf = tarfile.open(fileobj=io.BytesIO(data), mode='r:*')
    except tarfile.TarError:
        return _err('ltm', 'Invalid file format, does not seem to be a ltm')
    frames = 0
    rerecords = None
    start = 'power-on'
    system = 'pc'
    fps = 60.0
    num = den = None
    variable = False
    length_sec = length_nsec = None
    for member in tf.getmembers():
        if not member.isfile():
            continue
        base = member.name.split('/')[-1]
        if base == 'config.ini':
            text = tf.extractfile(member).read().decode('utf-8', 'replace')
            for s in text.splitlines():
                if s.startswith('frame_count='):
                    frames = int(s.split('=', 1)[1])
                elif s.startswith('rerecord_count='):
                    rerecords = int(s.split('=', 1)[1])
                elif s.startswith('savestate_frame_count='):
                    sc = int(s.split('=', 1)[1])
                    if sc > 0 and sc != frames:
                        start = 'savestate'
                elif s.startswith('framerate_den='):
                    den = float(s.split('=', 1)[1])
                elif s.startswith('framerate_num='):
                    num = float(s.split('=', 1)[1])
                elif s.startswith('game_name=') and 'ruffle' in s.lower():
                    system = 'flash'
                elif s.startswith('variable_framerate='):
                    v = s.split('=', 1)[1].strip().lower()
                    variable = v in ('1', 'true')
                elif s.startswith('length_sec='):
                    length_sec = float(s.split('=', 1)[1])
                elif s.startswith('length_nsec='):
                    length_nsec = float(s.split('=', 1)[1])
        elif base == 'annotations.txt':
            text = tf.extractfile(member).read().decode('utf-8', 'replace')
            for line in text.splitlines():
                if line.lower().startswith('platform:'):
                    system = line.split(':', 1)[1].strip().lower() or system
    if variable and length_sec is not None:
        total = length_sec + (length_nsec or 0) / 1e9
        fps = frames / total if total else fps
    elif num and den:
        fps = num / den
    return _ok('ltm', frames, rerecords, start, system, fps)


def parse_omr(data):
    try:
        xml_text = gzip.decompress(data).decode('utf-8', 'replace')
    except OSError:
        return _err('omr', 'Invalid file format, does not seem to be a omr')
    root = ET.fromstring(xml_text)
    replay = root.iter('replay').__next__()
    rerecords = int(next(replay.iter('reRecordCount')).text)
    times = [t for sn in replay.iter('snapshots')
             for t in sn.iter('time')]
    is_power_on = any(t.text == '0' for sn in replay.iter('scheduler')
                      for t in sn.iter('time'))
    start = 'power-on' if is_power_on else 'savestate'
    pal = any(x.text == 'true' for x in replay.iter('palTiming'))
    events = [it for ev in replay.iter('events') for it in ev.findall('item')]
    last = None
    for it in events:
        if it.attrib.get('type') != 'EndLog':
            last = it
    if last is None:
        return _err('omr', 'No events found')
    tnode = None
    for sc in last.iter('StateChange'):
        for tt in sc.iter('time'):
            tnode = tt
    stamp = None
    if tnode is not None:
        inner = list(tnode.iter('time'))
        stamp = int((inner[-1] if inner else tnode).text)
    if stamp is None:
        return _err('omr', 'Could not find final timestamp')
    seconds = stamp / 3579545.0 / 960.0
    fps = 50.1589758045661 if pal else 59.9227510135505
    frames = round(seconds * fps)
    return _ok('omr', frames, rerecords, start, 'msx', fps)


# ---------------------------------------------------------------- text misc
def parse_jrsr(data):
    text = data.decode('utf-8', 'replace')
    lines = text.splitlines()
    if not lines or not lines[0].startswith('JRSR'):
        return _err('jrsr', 'Invalid file format, does not seem to be a jrsr')
    section = None
    rerecords = None
    start = 'power-on'
    last_ts = 0
    last_nonspecial_ts = 0
    relative = False
    for raw in lines[1:]:
        line = raw.strip()
        if line.startswith('!BEGIN'):
            section = line[6:].strip()
            continue
        if line.startswith('!END'):
            section = None
            continue
        if not line.startswith('+'):
            continue
        body = line[1:]
        tokens = [t for t in re.split(r'[ (]+', body.replace(')', ' ')) if t]
        if section == 'savestate':
            # JPC-RR embeds the machine state it was saved from; the events
            # still run from the initialization, so the state is baggage for
            # our purposes, not a different kind of movie (issue #31). A
            # movie that genuinely starts from a state says SAVESTATEID in
            # its header, handled below.
            continue
        if section == 'header':
            if tokens and tokens[0] == 'RERECORDS' and len(tokens) >= 2:
                try:
                    rerecords = int(tokens[1])
                except ValueError:
                    pass
            # SAVESTATEID names the snapshot JPC-RR embedded when the file was
            # saved from a running machine; the events still begin at +0 and
            # replay from the initialization, so it is not a savestate start
        elif section == 'events':
            if len(tokens) < 2:
                continue
            try:
                ts = int(tokens[0])
            except ValueError:
                continue
            if relative:
                ts = last_ts + ts
            last_ts = ts
            ev = tokens[1]
            if ev == 'OPTION' and len(tokens) >= 3:
                relative = tokens[2] == 'RELATIVE'
            elif ev == 'SAVESTATE':
                pass
            elif not ev.isupper() or any(c.islower() for c in ev) or True:
                # non-special events count toward the movie length; special
                # classes are OPTION/SAVESTATE (handled above)
                last_nonspecial_ts = last_ts
    duration = last_nonspecial_ts / 1e9
    frames = int(math.floor(duration * 60.0 + 1e-6))
    fps = frames / duration if duration > 0 else 60.0
    return _ok('jrsr', frames, rerecords, start, 'dos', fps)


def parse_lmp(data):
    def calc_frames(header_len, input_len, players):
        n = 0
        p = header_len
        while p < len(data):
            if data[p] == 0x80:
                return n
            n += 1
            p += input_len * players
        return -1

    def players_at(addr, count=4, stride=1):
        players = 0
        for i in range(count):
            b = data[addr + i * stride]
            if b == 1:
                players += 1
            elif b != 0:
                return None
        return players

    def try_classic():
        if len(data) < 14 + 4 + 1 or data[0] != 111:
            return -1
        players = players_at(10)
        if not players:
            return -1
        if len(data) < 14 + 84 * players + 1:
            return -1
        return calc_frames(14 + 84 * players, 4, players)

    def try_strife():
        if len(data) < 16 + 6 + 1 or data[0] != 101:
            return -1
        players = 0
        for i in range(8):
            b = data[8 + i]
            if b == 1:
                players += 1
            elif b != 0:
                return -1
        return calc_frames(16, 6, players) if players else -1

    def try_new_doom():
        if len(data) < 13 + 4 + 1 or not (104 <= data[0] <= 110):
            return -1
        players = players_at(9)
        return calc_frames(13, 4, players) if players else -1

    def try_old_hexen():
        if len(data) < 11 + 6 + 1:
            return -1
        players = 0
        for i in range(4):
            a = data[3 + i * 2]
            b = data[3 + i * 2 + 1]
            if a == 1:
                players += 1
            if a not in (0, 1) or b > 2:
                return -1
        return calc_frames(11, 6, players) if players else -1

    def try_new_hexen():
        if len(data) < 19 + 6 + 1:
            return -1
        players = 0
        for i in range(8):
            a = data[3 + i * 2]
            b = data[3 + i * 2 + 1]
            if a == 1:
                players += 1
            if a not in (0, 1) or b > 2:
                return -1
        return calc_frames(19, 6, players) if players else -1

    def try_heretic():
        if len(data) < 7 + 6 + 1:
            return -1
        players = players_at(3)
        return calc_frames(7, 6, players) if players else -1

    def try_old_doom():
        if len(data) < 7 + 4 + 1:
            return -1
        players = players_at(3)
        return calc_frames(7, 4, players) if players else -1

    def try_boom():
        if len(data) < 109 + 4 + 1 or not (200 <= data[0] <= 221):
            return -1
        players = players_at(0x4D)
        return calc_frames(109, 4, players) if players else -1

    for attempt in (try_classic, try_strife, try_new_doom, try_old_hexen,
                    try_new_hexen, try_heretic, try_old_doom, try_boom):
        frames = attempt()
        if frames and frames > 0:
            return _ok('lmp', frames, None, 'power-on', 'pc', DOOM_FPS,
                       ['lmp carries no rerecord count'])
    return _err('lmp', 'Invalid file format, does not seem to be a lmp')


def parse_ctas(data):
    if struct.unpack_from('<I', data, 0)[0] != 0x53415443:
        return _err('ctas', 'Invalid file format, does not seem to be a ctas')
    version, framecount, rng_len = struct.unpack_from('<III', data, 4)
    rerecords = None
    if version >= 4:
        rerecords = struct.unpack_from('<I', data, 16)[0]
    return _ok('ctas', framecount, rerecords, 'power-on', 'pc', 60.0)


def parse_3ct(data):
    text = data.decode('utf-8', 'replace')
    last = ''
    for line in text.splitlines():
        if line.strip():
            last = line
    try:
        cycles = int(last.split(' ')[0])
    except (ValueError, IndexError):
        return _err('3ct', 'Invalid file format, does not seem to be a 3ct')
    return _ok('3ct', cycles - 1, None, 'power-on', 'nes', 5369318.18181818)


def parse_dft(data):
    try:
        tf = tarfile.open(fileobj=io.BytesIO(data), mode='r:*')
    except tarfile.TarError:
        return _err('dft', 'Invalid file format, does not seem to be a dft')
    all_members = [m for m in tf.getmembers() if m.isfile()]

    def find_member(path):
        # includes reference relative paths; match by suffix like TASVideos does
        return next((m for m in all_members if m.name.endswith(path)), None)

    if find_member('main.txt') is None:
        return _err('dft', 'Invalid file format, does not seem to be a dft')

    parsed = {}

    def parse_input_file(name):
        frames = 0
        includes = {}
        text = tf.extractfile(find_member(name)).read().decode('utf-8', 'replace')
        for line in text.splitlines():
            if line.startswith('#') or line.startswith('MOUSE'):
                continue
            if line.startswith('INCLUDE:'):
                inc = line[8:]
                includes[inc] = includes.get(inc, 0) + 1
            elif line.strip():
                frames += 1
        return {'frames': frames, 'includes': includes}

    pending = ['main.txt']
    while pending:
        name = pending.pop(0)
        if name in parsed:
            continue
        if find_member(name) is None:
            return _err('dft', f'Missing included file {name}, cannot parse')
        parsed[name] = parse_input_file(name)
        for inc in parsed[name]['includes']:
            if inc not in parsed and inc not in pending:
                pending.append(inc)

    unresolved = {n: dict(v['includes']) for n, v in parsed.items() if v['includes']}
    totals = {n: v['frames'] for n, v in parsed.items()}
    for _ in range(len(parsed) + 1):
        if not unresolved:
            break
        progressed = False
        for name in list(unresolved):
            incs = unresolved[name]
            for inc in list(incs):
                if inc not in unresolved:
                    totals[name] += totals[inc] * incs[inc]
                    del incs[inc]
                    progressed = True
            if not incs:
                del unresolved[name]
                progressed = True
        if not progressed:
            return _err('dft', 'Recursive includes detected, cannot parse')
    return _ok('dft', totals['main.txt'], None, 'power-on', 'pc', 60.0)


# ---- game-specific TAS tools (surveyed from their own sources; the tools
# page names each). The stated time is the record either way: these read
# frames and, where the file carries it, the wall time, for Import from movie.

def parse_tas(data, fmt='tas'):
    """.tas is four formats sharing one extension, told apart by content:
    Ballance TASSupport (binary: u32 size + zlib of 8-byte frame records),
    PICO-8 Celeste Classic (one line: [seeds]bitmask,bitmask,...),
    CelesteTAS (FileTime/ChapterTime headers), and the ShootMe family
    (JumpKing, Kalimba, Ori DE, Splasher, Teslagrad, Tinertia: lines of
    frames,actions summed; the frame rate is assumed 60, Teslagrad's 150
    cannot be told from the file)."""
    # Ballance TASSupport: little-endian byte count, then a zlib stream of
    # FrameData {float deltaTime_ms; uint32 keystates}
    if len(data) >= 6 and data[4] == 0x78:
        try:
            size = struct.unpack_from('<I', data, 0)[0]
            raw = zlib.decompress(data[4:])
        except Exception:   # noqa: BLE001 — not a Ballance record, fall through
            raw = None
        if raw is not None and size == len(raw) and size and size % 8 == 0:
            n = size // 8
            seconds = sum(struct.unpack_from('<f', raw, i * 8)[0] for i in range(n)) / 1000.0
            if seconds <= 0:
                return _err(fmt, 'Ballance record with no elapsed time')
            return _ok(fmt, n, None, 'power-on', 'pc', n / seconds)
    text = data.decode('utf-8', 'replace')
    stripped = text.strip()
    # PICO-8 Celeste Classic: a single line, [rng seeds] then one 6-bit
    # bitmask per frame at 30 fps
    m = re.fullmatch(r'\[[0-9, ]*\]([0-9]+(?:, ?[0-9]+)*),?', stripped, re.S)
    if m and '\n' not in stripped:
        frames = len([t for t in m.group(1).split(',') if t.strip()])
        return _ok(fmt, frames, None, 'power-on', 'pc', 30.0)
    # CelesteTAS: the total is in the FileTime/ChapterTime header
    frames = 0
    rerecords = None
    file_time_found = False
    for s in text.splitlines():
        if s.startswith('FileTime:'):
            file_time_found = True
            m = re.search(r'\((\d+)\)', s)
            if m:
                frames = int(m.group(1))
        elif not file_time_found and s.startswith('ChapterTime:'):
            m = re.search(r'\((\d+)\)', s)
            if m:
                frames = int(m.group(1))
        elif s.startswith('TotalRecordCount:'):
            try:
                rerecords = int(s.split(':', 1)[1].strip())
            except ValueError:
                pass
        elif rerecords is None and s.startswith('RecordCount:'):
            try:
                rerecords = int(s.split(':', 1)[1].strip())
            except ValueError:
                pass
    if frames:
        return _ok(fmt, frames, rerecords, 'power-on', 'pc', 1000.0 / 17.0)
    # ShootMe family: every line whose first token is an integer holds its
    # inputs for that many frames; @x,y lines cost one frame; everything
    # else (labels, ***, comments) costs none
    total = 0
    counted = 0
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('@'):
            total += 1
            counted += 1
            continue
        m = re.match(r'(\d+)(?:[,\s]|$)', line)
        if m:
            total += int(m.group(1))
            counted += 1
    if counted and total:
        return _ok(fmt, total, None, 'power-on', 'pc', 60.0,
                   ['frame rate assumed 60 fps (ShootMe-family .tas); Teslagrad runs at 150'])
    return _err(fmt, 'No FileTime/ChapterTime duration and no frame-count lines found')


def parse_htas(data):
    """hatTAS (A Hat in Time): metadata lines (length: required, fps:
    defaults to 60) until the first line starting with a digit."""
    text = data.decode('utf-8', 'replace')
    length = None
    fps = 60.0
    for line in text.splitlines():
        line = line.split('//', 1)[0].strip()
        if not line:
            continue
        if line[0].isdigit():
            break
        if line.startswith('length:'):
            try:
                length = int(line.split(':', 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith('fps:'):
            try:
                fps = float(line.split(':', 1)[1].strip())
            except ValueError:
                pass
    if not length:
        return _err('htas', 'No length: line found (hatTAS requires one)')
    return _ok('htas', length, None, 'savestate', 'pc', fps if fps > 0 else 60.0)


def parse_hltas(data):
    """Bunnymod XT / HLTAS (.hltas): framebulks carry their own frame time,
    so the file is self-timing: seconds = sum(frametime x count)."""
    text = data.decode('utf-8', 'replace')
    lines = text.splitlines()
    if not lines or not lines[0].strip().startswith('version'):
        return _err('hltas', 'Not an HLTAS script: no version line')
    frames = 0
    seconds = 0.0
    in_frames = False
    for line in lines[1:]:
        line = line.split('//', 1)[0].strip()
        if not line:
            continue
        if not in_frames:
            if line == 'frames':
                in_frames = True
            continue
        fields = line.split('|')
        if len(fields) < 7:
            continue   # save/seed/buttons/strafing and friends
        try:
            frame_time = float(fields[3])
            count = int(fields[6].split()[0]) if fields[6].strip() else 0
        except (ValueError, IndexError):
            continue
        if count <= 0:
            continue
        frames += count
        seconds += frame_time * count
    if not frames:
        return _err('hltas', 'No framebulks found after the frames line')
    return _ok('hltas', frames, None, 'savestate', 'pc',
               (frames / seconds) if seconds > 0 else None)


def parse_p2tas(data):
    """SourceAutoRecord (Portal 2, .p2tas): tickbulks at absolute or
    +relative ticks, repeat/end blocks; 60 ticks per second."""
    text = data.decode('utf-8', 'replace')
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    lines = [ln.split('//', 1)[0].strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines or not lines[0].startswith('version'):
        return _err('p2tas', 'Not a p2tas script: no version line')
    if not any(ln.startswith('start') for ln in lines[:4]):
        return _err('p2tas', 'Not a p2tas script: no start line')

    def walk(i, cur):
        # one stretch of lines, until the matching end; returns (index of
        # the end/eof, tick after the stretch)
        while i < len(lines):
            ln = lines[i]
            if ln.startswith('repeat'):
                try:
                    n = int(ln.split()[1])
                except (ValueError, IndexError):
                    n = 0
                after, out = i + 1, cur
                for _ in range(max(1, n)):
                    after, out = walk(i + 1, out)
                cur = out if n > 0 else cur
                i = after + 1   # past the matching end
                continue
            if ln == 'end':
                return i, cur
            m = re.match(r'(\+?)(\d+)>', ln)
            if m:
                cur = cur + int(m.group(2)) if m.group(1) else max(cur, int(m.group(2)))
            i += 1
        return i, cur
    _, ticks = walk(1, 0)
    if not ticks:
        return _err('p2tas', 'No tickbulks found')
    return _ok('p2tas', ticks, None, 'savestate', 'pc', 60.0)


def parse_srctas(data):
    """SourcePauseTool (.srctas): framebulks spend their TICKS field (the
    sixth pipe field); the Source builds these scripts target tick at
    66.67/s (0.015 s)."""
    text = data.decode('utf-8', 'replace')
    ticks = 0
    bulks = 0
    in_frames = False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if not in_frames:
            if line == 'frames':
                in_frames = True
            continue
        fields = line.split('|')
        if len(fields) < 7:
            continue   # ss / sl savestate lines and stray text
        try:
            n = int(fields[5])
        except ValueError:
            continue
        bulks += 1
        if n > 0:
            ticks += n
    if not bulks:
        return _err('srctas', 'No framebulks found after the frames line')
    return _ok('srctas', ticks, None, 'savestate', 'pc', 1.0 / 0.015)


def parse_qtas(data):
    """TASQuake (.qtas): blocks at absolute or +relative frames; the frame
    rate is the cl_maxfps cvar (10..72, default 72), tracked through the
    script so the wall time follows the file itself."""
    text = data.decode('utf-8', 'replace')
    cur = 0
    seconds = 0.0
    fps_state = [72.0]
    blocks = 0
    pending = []   # lines of the open block, scanned for cl_maxfps on close

    def close_block():
        for pending_line in pending:
            m = re.match(r'cl_maxfps\s+"?(\d+(?:\.\d+)?)"?', pending_line)
            if m:
                fps_state[0] = min(72.0, max(10.0, float(m.group(1))))
        del pending[:]
    for line in text.splitlines():
        line = line.split('//', 1)[0].strip()
        if not line:
            continue
        m = re.match(r'(\+?)(\d+):$', line)
        if m:
            new = cur + int(m.group(2)) if m.group(1) else int(m.group(2))
            close_block()
            if new > cur:
                seconds += (new - cur) / fps_state[0]
                cur = new
            blocks += 1
            continue
        pending.append(line)
    close_block()
    if not blocks:
        return _err('qtas', 'No frame blocks found')
    return _ok('qtas', cur, None, 'power-on', 'pc',
               (cur / seconds) if seconds > 0 else 72.0)


def parse_mctas(data):
    """TASmod (Minecraft, .mctas): a text TASfile; ticks are the unindented
    tick|keyboard|mouse|camera lines, 20 per second; the header carries the
    rerecord count."""
    text = data.decode('utf-8', 'replace')
    if 'TASfile' not in text[:512]:
        return _err('mctas', 'Not a TASfile: no header')
    rerecords = None
    ticks = 0
    for line in text.splitlines():
        m = re.match(r'Rerecords:\s*(\d+)', line)
        if m:
            rerecords = int(m.group(1))
        if re.match(r'\d+\|', line):   # unindented: subticks are indented
            ticks += 1
    if not ticks:
        return _err('mctas', 'No tick lines found')
    return _ok('mctas', ticks, rerecords, 'power-on', 'pc', 20.0)


def parse_replay(data):
    """ReplayBot (Geometry Dash, .replay): three layouts. Only the
    frame-typed v2 layout carries a frame number to read a duration from;
    the x-position layouts parse but yield no frames."""
    if len(data) >= 10 and data[:4] == b'RPLY':
        version = data[4]
        if version >= 2:
            rtype = data[5]
            fps = struct.unpack_from('<f', data, 6)[0]
            body = data[10:]
            n = len(body) // 5
            if n and rtype in (0x01, 0x31):   # frame-typed (writer quirk: ASCII '1')
                last = struct.unpack_from('<I', body, (n - 1) * 5)[0]
                if fps and fps > 0:
                    return _ok('replay', last, None, 'power-on', 'pc', fps,
                               ['the last input lands on this frame; the run plays on a little longer'])
            if n:
                return _ok('replay', 0, None, 'power-on', 'pc', None,
                           ['x-position replay: no frame count in the file'])
        else:
            if len(data) - 9 >= 5:
                return _ok('replay', 0, None, 'power-on', 'pc', None,
                           ['x-position replay: no frame count in the file'])
        return _err('replay', 'Empty replay')
    if len(data) >= 10 and (len(data) - 4) % 6 == 0:
        fps = struct.unpack_from('<f', data, 0)[0]
        if 0 < fps <= 100000:
            return _ok('replay', 0, None, 'power-on', 'pc', None,
                       ['legacy x-position replay: no frame count in the file'])
    return _err('replay', 'Not a ReplayBot replay')


def parse_inputs(data):
    """TMInterface (TrackMania, .inputs): timestamped commands; physics
    ticks every 10 ms, and the last timestamp is when the last input lands."""
    text = data.decode('utf-8', 'replace')
    last_ms = -1
    timed = 0

    def to_ms(tok):
        if ':' in tok:
            mnt, rest = tok.split(':', 1)
            return int(round((int(mnt) * 60 + float(rest)) * 1000))
        if '.' in tok:
            return int(round(float(tok) * 1000))
        return int(tok)
    for line in text.splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        for part in line.split(';'):
            m = re.match(r'((?:\d+:)?\d+(?:\.\d+)?)(?:-((?:\d+:)?\d+(?:\.\d+)?))?\s+(press|rel|steer|gas)\b', part.strip())
            if not m:
                continue
            try:
                ms = to_ms(m.group(2) or m.group(1))
            except ValueError:
                continue
            timed += 1
            last_ms = max(last_ms, ms)
    if not timed or last_ms <= 0:
        return _err('inputs', 'No timestamped input commands found')
    return _ok('inputs', last_ms // 10, None, 'power-on', 'pc', 100.0,
               ['the last input lands here; the run drives on to the finish'])


def parse_itf(data):
    """Iji TAS mod (.itf): frames,inputs lines summed; Save:/Skip lines cost
    one frame each; End stops playback; 30 fps."""
    text = data.decode('utf-8', 'replace')
    total = 0
    counted = 0
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('//'):
            continue
        if line == 'End':
            break
        if line.startswith(('Save:', 'Skip')):
            total += 1
            counted += 1
            continue
        m = re.match(r'(\d+)(?:,|$)', line)
        if m:
            total += int(m.group(1))
            counted += 1
    if not counted or not total:
        return _err('itf', 'No frame lines found')
    return _ok('itf', total, None, 'power-on', 'pc', 30.0)


def parse_otts(data):
    """OTS TAS Tool (Out There Somewhere, .otts): a JSON project whose
    action entries carry frame numbers; the game's rate is not in the file,
    so only the frame count is read."""
    try:
        doc = json.loads(data.decode('utf-8', 'replace'))
    except ValueError:
        return _err('otts', 'Not an OTS project: not JSON')
    entries = doc.get('entries') if isinstance(doc, dict) else None
    if not isinstance(entries, list):
        return _err('otts', 'Not an OTS project: no entries list')
    frames = 0
    for e in entries:
        if isinstance(e, dict) and isinstance(e.get('frame'), (int, float)):
            frames = max(frames, int(e['frame']))
    if not frames:
        return _err('otts', 'No frame-numbered entries found')
    return _ok('otts', frames, None, 'savestate', 'pc', None,
               ["the game's frame rate is not in the file"])


def _lz4_block(src, out_size):
    """LZ4 block decompression, the ~20 lines of it: enough to read an
    OpenGMK replay without a native dependency."""
    out = bytearray()
    i = 0
    n = len(src)
    while i < n and len(out) < out_size:
        token = src[i]; i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[i]; i += 1
                lit += b
                if b != 255:
                    break
        out += src[i:i + lit]; i += lit
        if i >= n:
            break
        offset = src[i] | (src[i + 1] << 8); i += 2
        if offset == 0:
            raise ValueError('bad offset')
        mlen = (token & 15) + 4
        if (token & 15) == 15:
            while True:
                b = src[i]; i += 1
                mlen += b
                if b != 255:
                    break
        start = len(out) - offset
        for k in range(mlen):
            out.append(out[start + k])
    return bytes(out)


def parse_gmtas(data):
    """OpenGMK / GM8emulator (.gmtas): u32 version, u64 uncompressed size,
    an LZ4 block of a bincode Replay. The frame count is the frames vector's
    length prefix; GM8's room speed is a property of the game, not the
    movie, so only frames are read."""
    if len(data) < 13 or struct.unpack_from('<I', data, 0)[0] != 1:
        return _err('gmtas', 'Not a .gmtas replay (version != 1)')
    out_size = struct.unpack_from('<Q', data, 4)[0]
    if out_size > 64 * 1024 * 1024:
        return _err('gmtas', 'Replay too large to read')
    try:
        raw = _lz4_block(data[12:], out_size)
    except (ValueError, IndexError):
        return _err('gmtas', 'LZ4 stream is damaged')
    if len(raw) < 36:
        return _err('gmtas', 'Replay too short')
    # bincode: start_time u128 (16) + start_seed i32 (4) + startup_events
    # Vec (u64 count; events are variable-width, so a replay carrying any
    # cannot be walked safely) + frames Vec (u64 count)
    startup = struct.unpack_from('<Q', raw, 20)[0]
    if startup:
        return _err('gmtas', 'Replay carries startup events; frame count not readable')
    frames = struct.unpack_from('<Q', raw, 28)[0]
    if not frames or frames > 100_000_000:
        return _err('gmtas', 'Implausible frame count')
    return _ok('gmtas', frames, None, 'power-on', 'pc', None,
               ["the game's room speed (frame rate) is not in the file"])


# ---- the classic TASVideos emulator formats (specs: tasvideos.org) ----
# NTSC/PAL rates as the site uses them in practice
FPS_NES_NTSC = 60.0988138974405
FPS_NES_PAL = 50.0069789081886
FPS_SMS_NTSC = 59.9227510135505
FPS_SMS_PAL = 49.70146011994839
FPS_PCE = 59.8261054534819


def parse_smv(data):
    """Snes9x (.smv): frame count at 0x10, rerecords at 0x0C; options byte
    0x15 says reset-vs-savestate (bit 0) and PAL (bit 1)."""
    if data[:4] != b'SMV\x1a' or len(data) < 0x20:
        return _err('smv', 'Not an SMV movie')
    rerecords, frames = struct.unpack_from('<II', data, 0x0C)
    options = data[0x15]
    start = 'power-on' if options & 1 else 'savestate'
    fps = FPS_NES_PAL if options & 2 else FPS_NES_NTSC
    return _ok('smv', frames, rerecords, start, 'snes', fps)


def parse_zmv(data):
    """ZSNES (.zmv): frame count at 0x09, rerecords at 0x0D; byte 0x27 has
    the start kind (bits 7-6) and the PAL flag (bit 5)."""
    if data[:3] != b'ZMV' or len(data) < 0x2B:
        return _err('zmv', 'Not a ZMV movie')
    frames, rerecords = struct.unpack_from('<II', data, 0x09)
    flags = data[0x27]
    kind = (flags >> 6) & 3
    start = 'savestate' if kind == 0 else 'power-on'
    fps = FPS_NES_PAL if flags & 0x20 else FPS_NES_NTSC
    return _ok('zmv', frames, rerecords, start, 'snes', fps)


def parse_fcm(data):
    """FCEU 0.98 (.fcm): frame count at 0x0C, rerecords at 0x10; flag byte
    0x08 says reset-based (bit 1) and PAL (bit 2, unreliable in late
    versions, so it only picks the rate)."""
    if data[:4] != b'FCM\x1a' or len(data) < 0x38:
        return _err('fcm', 'Not an FCM movie')
    if struct.unpack_from('<I', data, 4)[0] != 2:
        return _err('fcm', 'Unsupported FCM version')
    flags = data[0x08]
    frames, rerecords = struct.unpack_from('<II', data, 0x0C)
    start = 'power-on' if flags & 2 else 'savestate'
    fps = FPS_NES_PAL if flags & 4 else FPS_NES_NTSC
    return _ok('fcm', frames, rerecords, start, 'nes', fps)


def parse_fmv(data):
    """Famtasia (.fmv): 144-byte header; frames = (size - 144) / stride,
    the stride being one byte per used controller plus one for FDS."""
    if data[:4] != b'FMV\x1a' or len(data) < 0x90:
        return _err('fmv', 'Not an FMV movie')
    flags2 = data[0x05]
    stride = ((1 if flags2 & 0x80 else 0) + (1 if flags2 & 0x40 else 0)
              + (1 if flags2 & 0x20 else 0))
    if not stride:
        return _err('fmv', 'No controllers flagged; frame count not derivable')
    frames = (len(data) - 0x90) // stride
    rerecords = struct.unpack_from('<I', data, 0x0A)[0] + 1
    start = 'savestate' if data[0x04] & 0x80 else 'power-on'
    return _ok('fmv', frames, rerecords, start, 'nes', FPS_NES_NTSC)


def parse_vmv(data):
    """VirtuaNES (.vmv): frame count at 0x38, rerecords at 0x1C; flag bit 6
    of 0x10 is reset-based, byte 0x23 is PAL."""
    if data[:12] != b'VirtuaNES MV' or len(data) < 0x40:
        return _err('vmv', 'Not a VMV movie')
    flags = struct.unpack_from('<I', data, 0x10)[0]
    rerecords = struct.unpack_from('<I', data, 0x1C)[0]
    frames = struct.unpack_from('<I', data, 0x38)[0]
    version = struct.unpack_from('<H', data, 0x0C)[0]
    start = 'power-on' if (flags & 0x40) and version >= 0x0400 else 'savestate'
    fps = FPS_NES_PAL if data[0x23] else FPS_NES_NTSC
    return _ok('vmv', frames, rerecords, start, 'nes', fps)


def parse_nmv(data):
    """Nintendulator (.nmv): a block file; the NMOV block names the bytes
    per frame and holds the input, so frames = data length / stride.
    Savestate blocks before it mean a savestate start."""
    if data[:4] != b'NSS\x1a' or len(data) < 16:
        return _err('nmv', 'Not a Nintendulator movie')
    i = 16
    saw_state = False
    while i + 8 <= len(data):
        sig = data[i:i + 4]
        (length,) = struct.unpack_from('<I', data, i + 4)
        body = data[i + 8:i + 8 + length]
        if sig == b'NMOV':
            if len(body) < 12:
                return _err('nmv', 'NMOV block truncated')
            stride = body[3] & 0x3F
            fps = FPS_NES_PAL if body[3] & 0x80 else FPS_NES_NTSC
            rerecords = struct.unpack_from('<I', body, 4)[0]
            desc_len = struct.unpack_from('<I', body, 8)[0]
            pos = 12 + desc_len
            if not stride or len(body) < pos + 4:
                return _err('nmv', 'NMOV block malformed')
            data_len = struct.unpack_from('<I', body, pos)[0]
            frames = data_len // stride
            return _ok('nmv', frames, rerecords,
                       'savestate' if saw_state else 'power-on', 'nes', fps)
        if sig in (b'CPUS', b'PPUS', b'APUS', b'CTRL', b'NPRA', b'NCRA',
                   b'MAPR', b'GENI', b'DISK'):
            saw_state = True
        i += 8 + length
    return _err('nmv', 'No NMOV block found')


def parse_mmv(data):
    """Dega (.mmv, Master System / Game Gear): frame count at 0x08,
    rerecords at 0x0C, reset flag at 0x10; flags at 0x60 carry PAL and
    Game Gear bits."""
    if data[:4] != b'MMV\x00' or len(data) < 0xF4:
        return _err('mmv', 'Not an MMV movie')
    frames, rerecords, from_reset = struct.unpack_from('<III', data, 0x08)
    flags = struct.unpack_from('<I', data, 0x60)[0]
    fps = FPS_SMS_PAL if flags & 2 else FPS_SMS_NTSC
    system = 'gg' if flags & 8 else 'sms'
    return _ok('mmv', frames, rerecords, 'power-on' if from_reset else 'savestate',
               system, fps)


MCM_STRIDES = {'pce': (11, 'pce', FPS_PCE), 'pcfx': (5, 'pcfx', None),
               'wswan': (3, 'wswan', None), 'ngp': (2, 'ngp', None),
               'lynx': (3, 'lynx', None), 'sms': (3, 'sms', FPS_SMS_NTSC),
               'nes': (5, 'nes', FPS_NES_NTSC)}


def parse_mcm(data):
    """Mednafen (.mcm): 256-byte header naming the console at 0x74; frames =
    (size - 256) / the console's per-frame stride (a lead byte plus each
    port's bytes)."""
    if data[:8] != b'MDFNMOVI' or len(data) < 0x100:
        return _err('mcm', 'Not a Mednafen movie')
    rerecords = struct.unpack_from('<I', data, 0x70)[0]
    console = data[0x74:0x79].split(b'\x00')[0].decode('ascii', 'replace').lower()
    spec = MCM_STRIDES.get(console)
    if not spec:
        return _err('mcm', f'Unknown console {console!r}; frame count not derivable')
    stride, system, fps = spec
    frames = (len(data) - 0x100) // stride
    return _ok('mcm', frames, rerecords, 'power-on', system, fps)


FPS_PSX_NTSC = 59.94005994005994
FPS_PSX_PAL = 50.0
FPS_SATURN_NTSC = 59.8830284837373
FPS_SATURN_PAL = 49.9600319744205


def _psx_movie(data, fmt, magic, flags_u16):
    """PSXjin (.pjm) and PCSX-rr (.pxm) share one header: frame count at
    0x10, rerecords at 0x14; the flags carry savestate (bit 1) and PAL
    (bit 2), as a u16 for PJM and a u8 for PXM."""
    if data[:4] != magic or len(data) < 0x34:
        return _err(fmt, f'Not a {fmt.upper()} movie')
    if struct.unpack_from('<I', data, 4)[0] != 2:
        return _err(fmt, f'Unsupported {fmt.upper()} version')
    flags = struct.unpack_from('<H', data, 0x0C)[0] if flags_u16 else data[0x0C]
    frames, rerecords = struct.unpack_from('<II', data, 0x10)
    start = 'savestate' if flags & 2 else 'power-on'
    fps = FPS_PSX_PAL if flags & 4 else FPS_PSX_NTSC
    return _ok(fmt, frames, rerecords, start, 'psx', fps)


def parse_pjm(data):
    return _psx_movie(data, 'pjm', b'PJM ', True)


def parse_pxm(data):
    return _psx_movie(data, 'pxm', b'PXM ', False)


def _pipe_text_movie(data, fmt, system, fps_ntsc, fps_pal=None,
                     pal_keys=(), state_keys=()):
    """The fm2-family text movies: `key value` headers, one input line per
    frame starting with `|`. Frames = the input lines."""
    text = data.decode('utf-8', 'replace')
    lines = text.splitlines()
    if not lines:
        return _err(fmt, 'Empty file')
    rerecords = None
    pal = False
    savestate = False
    frames = 0
    for line in lines:
        if line.startswith('|'):
            frames += 1
            continue
        low = line.strip().lower()
        m = re.match(r'rerecordcount\s+(\d+)', low)
        if m:
            rerecords = int(m.group(1))
        for k in pal_keys:
            if low.startswith(k) and low.split(None, 1)[1:] and low.split(None, 1)[1] in ('1', 'true'):
                pal = True
        for k in state_keys:
            if low.startswith(k) and low.split(None, 1)[1:] and low.split(None, 1)[1] in ('1', 'true'):
                savestate = True
    if not frames:
        return _err(fmt, 'No input lines found')
    fps = (fps_pal if pal and fps_pal else fps_ntsc)
    return _ok(fmt, frames, rerecords, 'savestate' if savestate else 'power-on',
               system, fps)


def parse_mc2(data):
    """Mednafen-rr / PCEjin (.mc2, PC Engine): text, starts `version 1`;
    frames are the pipe lines, at the PCE's fixed NTSC rate."""
    if not data.startswith(b'version 1'):
        return _err('mc2', 'Not an MC2 movie: no version 1 line')
    return _pipe_text_movie(data, 'mc2', 'pce', FPS_PCE)


def parse_ymv(data):
    """Yabause rerecording (.ymv, Sega Saturn): text, starts `version 1`;
    frames are the pipe lines; isPal picks the rate, savestate anchors."""
    if not data.startswith(b'version 1'):
        return _err('ymv', 'Not a YMV movie: no version 1 line')
    return _pipe_text_movie(data, 'ymv', 'saturn', FPS_SATURN_NTSC,
                            FPS_SATURN_PAL, pal_keys=('ispal',),
                            state_keys=('savestate',))


BKM_SYSTEMS = {'nes': 'nes', 'snes': 'snes', 'sgb': 'snes', 'gb': 'gb',
               'gbc': 'gbc', 'gba': 'gba', 'n64': 'n64', 'gen': 'genesis',
               'sms': 'sms', 'gg': 'gg', 'pce': 'pce', 'pcecd': 'pce',
               'sgx': 'pce', 'sg': 'sms', 'coleco': 'coleco', 'a26': 'a2600',
               'tas': 'nes', 'sat': 'saturn', 'dgb': 'gb', 'a78': 'a7800',
               'c64': 'c64', 'psx': 'psx', 'wswan': 'wswan', 'nds': 'ds'}


def parse_bkm(data):
    """BizHawk 1.x (.bkm): text headers (Platform, PAL, rerecordCount,
    StartsFromSavestate) then one pipe line per frame. The rate is the
    platform's; the archive's system table supplies it, so none is claimed
    here beyond the system itself."""
    text = data.decode('utf-8', 'replace')
    if 'MovieVersion' not in text.split('\n', 1)[0] and 'Platform' not in text[:512]:
        return _err('bkm', 'Not a BKM movie: no MovieVersion/Platform header')
    platform = None
    m = re.search(r'^Platform\s+(\S+)', text, re.M)
    if m:
        platform = m.group(1).lower()
    res = _pipe_text_movie(data, 'bkm', BKM_SYSTEMS.get(platform, platform), None,
                           pal_keys=('pal',), state_keys=('startsfromsavestate',))
    return res


def parse_dof(data):
    """Bisqwit's DOSBox rerecording (.dof): 4320-byte header; the movie
    ticks every InputInterval emulated milliseconds, so the file is
    self-timing: seconds = frames x interval / 1000."""
    # the patch's own constant is 0x1A564F44 ("DOV\x1a") while its comment
    # says DOF^Z; accept both spellings of the magic
    if len(data) < 0x10E0 or data[:4] not in (b'DOF\x1a', b'DOV\x1a'):
        return _err('dof', 'Not a DOF movie')
    interval, frames, rerecords, flags = struct.unpack_from('<iIII', data, 0x08)
    if interval <= 0 or interval > 10000:
        return _err('dof', 'Implausible input interval')
    start = 'power-on' if flags & 1 else 'savestate'
    return _ok('dof', frames, rerecords, start, 'dos', 1000.0 / interval)


def parse_rec(data):
    """Elasto Mania replay (.rec): per-player header carries the frame
    count (version must be 0x83); the game steps 30 frames per second.
    Not a rerecording format, so no rerecord count exists."""
    if len(data) < 36 + 8:
        return _err('rec', 'Not an Elasto Mania replay')
    frames, version, multi = struct.unpack_from('<iIi', data, 0)
    if version != 0x83 or frames <= 0 or frames > 10_000_000:
        return _err('rec', 'Not an Elasto Mania replay (bad version or count)')
    # the columnar frame arrays are 27 bytes per frame; a real file holds
    # at least that much before its event list and end marker
    if len(data) < 36 + frames * 27:
        return _err('rec', 'Replay shorter than its own frame count')
    return _ok('rec', frames, None, 'power-on', 'pc', 30.0)


PARSERS = {
    'chimeraproject': parse_chimeraproject,

    'bk2': lambda d: parse_bk2(d, 'bk2'),
    'tasproj': lambda d: parse_bk2(d, 'tasproj'),
    'gbmv': lambda d: parse_bk2(d, 'gbmv'),
    'fm2': lambda d: parse_fm2(d, 'fm2'),
    'fm3': lambda d: parse_fm2(d, 'fm3'),
    'dsm': parse_dsm,
    'gmv': parse_gmv,
    'vbm': parse_vbm,
    'dtm': parse_dtm,
    'm64': parse_m64,
    'mar': parse_mar,
    'fbm': parse_fbm,
    'p2m2': parse_p2m2,
    'ctm': parse_ctm,
    'wtf': parse_wtf,
    'gzm': parse_gzm,
    'lsmv': parse_lsmv,
    'ltm': parse_ltm,
    'omr': parse_omr,
    'jrsr': parse_jrsr,
    'lmp': parse_lmp,
    'tas': lambda d: parse_tas(d, 'tas'),
    'ctas': parse_ctas,
    '3ct': parse_3ct,
    'dft': parse_dft,
    'htas': parse_htas,
    'hltas': parse_hltas,
    'p2tas': parse_p2tas,
    'srctas': parse_srctas,
    'qtas': parse_qtas,
    'mctas': parse_mctas,
    'replay': parse_replay,
    'inputs': parse_inputs,
    'itf': parse_itf,
    'otts': parse_otts,
    'gmtas': parse_gmtas,
    'smv': parse_smv,
    'zmv': parse_zmv,
    'fcm': parse_fcm,
    'fmv': parse_fmv,
    'vmv': parse_vmv,
    'nmv': parse_nmv,
    'mmv': parse_mmv,
    'mcm': parse_mcm,
    'pjm': parse_pjm,
    'pxm': parse_pxm,
    'mc2': parse_mc2,
    'ymv': parse_ymv,
    'bkm': parse_bkm,
    'dof': parse_dof,
    'rec': parse_rec,
}


# movie formats the archive accepts without reading them: the file is kept,
# the frame count stays unknown and the submitter states the time
# formats the archive accepts without reading: naezith's in-game replays
# (a text format whose tick unit is undocumented). The extensions once
# listed here without a source (pmv, tm2, usb, vbm2, xmv, zrm, yrm, irm,
# ljm, lmp2) turned out not to be TAS movie formats at all and are gone;
# an unknown extension is archived as it is either way, with a warning.
KNOWN_UNPARSED = {'ronr'}

def known_extension(ext):
    return ext in PARSERS or ext in KNOWN_UNPARSED

def parse(filename, data):
    """Parse a movie file by extension. Never raises: returns ok=False on any
    failure."""
    ext = filename.rsplit('.', 1)[-1].lower()
    fn = PARSERS.get(ext)
    if fn is None:
        return _err(ext, f'no parser for .{ext}')
    try:
        return fn(data)
    except Exception as e:   # noqa: BLE001 — a parser bug must never take down intake
        return _err(ext, f'parse failure: {e.__class__.__name__}: {e}')
