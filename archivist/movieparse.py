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
import math
import re
import struct
import tarfile
import zipfile
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
               'zxspectrum': 'zxs', 'nds': 'ds'}
CYCLE_BASED_CORES = {'subgbhawk': 4194304, 'gambatte': 2097152}
VALID_CLOCK_RATES = {'4194304', '2097152', '5369318.18181818', '5320342.5',
                     '33868800', '21477272.7272727', '21281370', '16777216'}
BK2_INVALID = ['greenzonesettings.txt', 'laglog', 'markers.txt',
               'clientsettings.json', 'session.txt', 'greenzone']
TASPROJ_INVALID = ['greenzone']


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
            # cycle count is actually a millisecond count
            frames = cycle_count
            fps = 1000.0
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
            return _err('jrsr', 'File contains a savestate')
        if section == 'header':
            if tokens and tokens[0] == 'RERECORDS' and len(tokens) >= 2:
                try:
                    rerecords = int(tokens[1])
                except ValueError:
                    pass
            elif tokens and tokens[0] == 'SAVESTATEID':
                start = 'savestate'
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


def parse_tas(data):
    text = data.decode('utf-8', 'replace')
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
    if not frames:
        return _err('tas', 'No FileTime/ChapterTime duration found')
    return _ok('tas', frames, rerecords, 'power-on', 'pc', 1000.0 / 17.0)


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


PARSERS = {
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
    'tas': parse_tas,
    'ctas': parse_ctas,
    '3ct': parse_3ct,
    'dft': parse_dft,
}


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
