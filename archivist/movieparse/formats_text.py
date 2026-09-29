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
from .common import *

def parse_fm2(data, fmt='fm2'):
    """Parse FM2 movie metadata and input length."""
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
    """Parse DSM movie metadata and input length."""
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
    """Parse GMV movie metadata and input length."""
    if not data[:16].decode('latin-1').startswith('Gens Movie'):
        return _err('gmv', 'Invalid file format, does not seem to be a gmv')
    rerecords = struct.unpack_from('<i', data, 16)[0]
    flags = data[22]
    start = 'savestate' if flags & 0x40 else 'power-on'
    frames = (len(data) - 64) // 3
    return _ok('gmv', frames, rerecords, start, 'genesis')


def parse_vbm(data):
    """Parse VBM movie metadata and input length."""
    if not data[:4].decode('latin-1').startswith('VBM'):
        return _err('vbm', 'Invalid file format, does not seem to be a vbm')
    frames, rerecords = struct.unpack_from('<ii', data, 12)
    t = data[20]
    start = 'savestate' if t & 1 else ('sram' if t & 2 else 'power-on')
    s = data[22]
    system = 'gba' if s & 1 else 'gbc' if s & 2 else 'sgb' if s & 4 else 'gb'
    return _ok('vbm', frames, rerecords, start, system)


def parse_dtm(data):
    """Parse DTM movie metadata and input length."""
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
    """Parse M64 movie metadata and input length."""
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
    """Parse MAR movie metadata and input length."""
    if data[:8] != b'MAMETAS\x00':
        return _err('mar', 'Invalid file format, does not seem to be a mar')
    fps = struct.unpack_from('<d', data, 48)[0]
    frames, rerecords = struct.unpack_from('<ii', data, 56)
    return _ok('mar', frames, rerecords, 'power-on', 'arcade',
               fps if fps > 0 else None)


def parse_fbm(data):
    """Parse FBM movie metadata and input length."""
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
    """Parse P2M2 movie metadata and input length."""
    if data[1:6] != b'PCSX2':
        return _err('p2m2', 'Invalid file format, does not seem to be a p2m2')
    pos = 1 + 5 + 2 + 43 + 255 + 255
    frames, rerecords = struct.unpack_from('<ii', data, pos)
    start = 'savestate' if data[pos + 8] > 0 else 'power-on'
    return _ok('p2m2', frames, rerecords, start, 'ps2')


def parse_ctm(data):
    """Parse CTM movie metadata and input length."""
    if data[:4] != b'CTM\x1b':
        return _err('ctm', 'Invalid file format, does not seem to be a ctm')
    pos = 4 + 8 + 20 + 8 + 8 + 32
    rerecords = struct.unpack_from('<i', data, pos)[0]
    inputs = struct.unpack_from('<Q', data, pos + 4)[0]
    frame_rate = 268111856.0 / 4481136.0
    frames = math.ceil(inputs / 234 * frame_rate)
    return _ok('ctm', frames, rerecords, 'power-on', '3ds')


def parse_wtf(data):
    """Parse WTF movie metadata and input length."""
    if struct.unpack_from('<i', data, 0)[0] != 41374822:
        return _err('wtf', 'Invalid file format, does not seem to be a wtf')
    rerecords = struct.unpack_from('<i', data, 8)[0]
    fps = struct.unpack_from('<I', data, 20)[0]
    frames = (len(data) - 1024) // 8
    return _ok('wtf', frames, rerecords, 'power-on', 'pc',
               float(fps - 1) if fps > 1 else None)


def parse_gzm(data):
    """Parse GZM movie metadata and input length."""
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
def _lsmv_system(line, warnings):
    """Translate lsnes game types, warning on unknown region and system."""
    if line in ('snes_ntsc', 'bsx', 'bsxslotted', 'sufamiturbo', 'snes_pal'):
        return 'snes'
    if line in ('sgb_ntsc', 'sgb_pal'):
        return 'sgb'
    if line == 'gdmg':
        return 'gb'
    if line in ('ggbc', 'ggbca'):
        return 'gbc'
    warnings += ['system id inferred', 'region inferred']
    return 'snes'


def _lsmv_rerecords(archive, warnings):
    """Read lsnes rerecord count, warning if absent or malformed."""
    rr_name = next((n for n in archive.namelist()
                    if n.lower().startswith('rerecords')), None)
    rerecords = None
    if rr_name is not None:
        rr = _lines(archive.read(rr_name).decode('utf-8', 'replace'))
        try:
            rerecords = int(rr[0]) if rr else None
        except ValueError:
            rerecords = None
    if rerecords is None:
        warnings.append('missing rerecord count')
    return rerecords


def parse_lsmv(data):
    """Parse LSMV movie metadata and input length."""
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
    gt_name = next((n for n in z.namelist() if n.lower().startswith('gametype')), None)
    if gt_name is None:
        return _err('lsmv', 'Could not determine the System Code')
    gt = _lines(z.read(gt_name).decode('utf-8', 'replace'))
    line = gt[0].lower() if gt else None
    system = _lsmv_system(line, warnings)
    rerecords = _lsmv_rerecords(z, warnings)
    input_name = next((n for n in z.namelist()
                       if n.lower().startswith('input')
                       and not any(c.isdigit() for c in n)), None)
    if input_name is None:
        return _err('lsmv', 'Missing input, can not parse')
    frames = sum(1 for l in _lines(z.read(input_name).decode('utf-8', 'replace'))
                 if l.startswith('F'))
    return _ok('lsmv', frames, rerecords, start, system, None, warnings)


def _ltm_config(text, values):
    """Read libTAS configuration values used for run length and timing."""
    for s in text.splitlines():
        key, sep, value = s.partition('=')
        if sep:
            _ltm_config_value(key, value, values)


def _ltm_config_value(key, value, values):
    """Apply a libTAS configuration entry in file order."""
    if key in ('frame_count', 'rerecord_count', 'savestate_frame_count',
               'framerate_den', 'framerate_num', 'length_sec', 'length_nsec'):
        _ltm_numeric(key, value, values)
    elif key == 'variable_framerate':
        values[key] = value.strip().lower() in ('1', 'true')
    elif key == 'game_name' and 'ruffle' in f'{key}={value}'.lower():
        values['system'] = 'flash'


def _ltm_numeric(key, value, values):
    """Read a numeric libTAS header, including state-start detection."""
    if key in ('frame_count', 'rerecord_count'):
        values[key] = int(value)
    elif key == 'savestate_frame_count':
        number = int(value)
        if number > 0 and number != values.get('frame_count', 0):
            values['start'] = 'savestate'
    else:
        values[key] = float(value)


def _ltm_members(archive):
    """Read libTAS config and optional platform annotation from archive members."""
    values = {}
    system = 'pc'
    for member in archive.getmembers():
        if not member.isfile():
            continue
        base = member.name.split('/')[-1]
        if base == 'config.ini':
            text = archive.extractfile(member).read().decode('utf-8', 'replace')
            _ltm_config(text, values)
            system = values.get('system', system)
        elif base == 'annotations.txt':
            text = archive.extractfile(member).read().decode('utf-8', 'replace')
            for line in text.splitlines():
                if line.lower().startswith('platform:'):
                    system = line.split(':', 1)[1].strip().lower() or system
    return values, system


def parse_ltm(data):
    """Parse LTM movie metadata and input length."""
    try:
        tf = tarfile.open(fileobj=io.BytesIO(data), mode='r:*')
    except tarfile.TarError:
        return _err('ltm', 'Invalid file format, does not seem to be a ltm')
    values, system = _ltm_members(tf)
    frames = values.get('frame_count', 0)
    rerecords = values.get('rerecord_count')
    start = values.get('start', 'power-on')
    fps = 60.0
    num = values.get('framerate_num')
    den = values.get('framerate_den')
    if values.get('variable_framerate') and 'length_sec' in values:
        total = values['length_sec'] + values.get('length_nsec', 0) / 1e9
        fps = frames / total if total else fps
    elif num and den:
        fps = num / den
    return _ok('ltm', frames, rerecords, start, system, fps)


def _omr_last_timestamp(replay):
    """Find the last non-EndLog MSX replay event's state-change timestamp."""
    events = [it for ev in replay.iter('events') for it in ev.findall('item')]
    last = None
    for it in events:
        if it.attrib.get('type') != 'EndLog':
            last = it
    if last is None:
        return None
    tnode = None
    for sc in last.iter('StateChange'):
        for tt in sc.iter('time'):
            tnode = tt
    if tnode is None:
        return None
    inner = list(tnode.iter('time'))
    return int((inner[-1] if inner else tnode).text)


def parse_omr(data):
    """Parse OMR movie metadata and input length."""
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
    stamp = _omr_last_timestamp(replay)
    if stamp is None:
        events = [it for ev in replay.iter('events') for it in ev.findall('item')]
        if any(it.attrib.get('type') != 'EndLog' for it in events):
            return _err('omr', 'Could not find final timestamp')
        return _err('omr', 'No events found')
    seconds = stamp / 3579545.0 / 960.0
    fps = 50.1589758045661 if pal else 59.9227510135505
    frames = round(seconds * fps)
    return _ok('omr', frames, rerecords, start, 'msx', fps)


# ---------------------------------------------------------------- text misc
def _jrsr_events(lines):
    """Accumulate relative and absolute event timestamps, ignoring saved state."""
    last_ts = 0
    last_nonspecial_ts = 0
    relative = False
    for line in lines:
        if not line.startswith('+'):
            continue
        tokens = [t for t in re.split(r'[ (]+', line[1:].replace(')', ' ')) if t]
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
        elif ev != 'SAVESTATE':
            last_nonspecial_ts = last_ts
    return last_nonspecial_ts


def _jrsr_sections(lines):
    """Separate JPC-RR header and event lines from embedded savestate baggage."""
    section = None
    header = []
    events = []
    for raw in lines:
        line = raw.strip()
        if line.startswith('!BEGIN'):
            section = line[6:].strip()
            continue
        if line.startswith('!END'):
            section = None
            continue
        if section == 'header':
            header.append(line)
        elif section == 'events':
            events.append(line)
    return header, events


def parse_jrsr(data):
    """Parse JRSR movie metadata and input length."""
    text = data.decode('utf-8', 'replace')
    lines = text.splitlines()
    if not lines or not lines[0].startswith('JRSR'):
        return _err('jrsr', 'Invalid file format, does not seem to be a jrsr')
    rerecords = None
    header, events = _jrsr_sections(lines[1:])
    for line in header:
        if not line.startswith('+'):
            continue
        tokens = [t for t in re.split(r'[ (]+', line[1:].replace(')', ' ')) if t]
        if tokens and tokens[0] == 'RERECORDS' and len(tokens) >= 2:
            try:
                rerecords = int(tokens[1])
            except ValueError:
                pass
    last_nonspecial_ts = _jrsr_events(events)
    duration = last_nonspecial_ts / 1e9
    frames = int(math.floor(duration * 60.0 + 1e-6))
    fps = frames / duration if duration > 0 else 60.0
    return _ok('jrsr', frames, rerecords, 'power-on', 'dos', fps)


def parse_lmp(data):
    """Parse LMP movie metadata and input length."""
    def calc_frames(header_len, input_len, players):
        """Count fixed-width Doom input frames until the terminator."""
        n = 0
        p = header_len
        while p < len(data):
            if data[p] == 0x80:
                return n
            n += 1
            p += input_len * players
        return -1

    def players_at(addr, count=4, stride=1):
        """Count active players in a Doom movie header."""
        players = 0
        for i in range(count):
            b = data[addr + i * stride]
            if b == 1:
                players += 1
            elif b != 0:
                return None
        return players

    def try_classic():
        """Read classic Doom demo layout."""
        if len(data) < 14 + 4 + 1 or data[0] != 111:
            return -1
        players = players_at(10)
        if not players:
            return -1
        if len(data) < 14 + 84 * players + 1:
            return -1
        return calc_frames(14 + 84 * players, 4, players)

    def try_strife():
        """Read Strife demo layout."""
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
        """Read newer Doom demo layout."""
        if len(data) < 13 + 4 + 1 or not (104 <= data[0] <= 110):
            return -1
        players = players_at(9)
        return calc_frames(13, 4, players) if players else -1

    def try_old_hexen():
        """Read old Hexen demo layout."""
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
        """Read newer Hexen demo layout."""
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
        """Read Heretic demo layout."""
        if len(data) < 7 + 6 + 1:
            return -1
        players = players_at(3)
        return calc_frames(7, 6, players) if players else -1

    def try_old_doom():
        """Read old Doom demo layout."""
        if len(data) < 7 + 4 + 1:
            return -1
        players = players_at(3)
        return calc_frames(7, 4, players) if players else -1

    def try_boom():
        """Read Boom demo layout."""
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
    """Parse CTAS movie metadata and input length."""
    if struct.unpack_from('<I', data, 0)[0] != 0x53415443:
        return _err('ctas', 'Invalid file format, does not seem to be a ctas')
    version, framecount, rng_len = struct.unpack_from('<III', data, 4)
    rerecords = None
    if version >= 4:
        rerecords = struct.unpack_from('<I', data, 16)[0]
    return _ok('ctas', framecount, rerecords, 'power-on', 'pc', 60.0)


def parse_3ct(data):
    """Parse 3CT movie metadata and input length."""
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


def _dft_step(unresolved, totals):
    """Resolve one round of nonrecursive include references."""
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
    return progressed


def _dft_totals(parsed):
    """Resolve included input scripts bottom-up, rejecting recursive includes."""
    unresolved = {n: dict(v['includes']) for n, v in parsed.items() if v['includes']}
    totals = {n: v['frames'] for n, v in parsed.items()}
    for _ in range(len(parsed) + 1):
        if not unresolved:
            break
        if not _dft_step(unresolved, totals):
            return None
    return totals['main.txt']


def parse_dft(data):
    """Parse DFT movie metadata and input length."""
    try:
        tf = tarfile.open(fileobj=io.BytesIO(data), mode='r:*')
    except tarfile.TarError:
        return _err('dft', 'Invalid file format, does not seem to be a dft')
    all_members = [m for m in tf.getmembers() if m.isfile()]

    def find_member(path):
        # includes reference relative paths; match by suffix like TASVideos does
        """Find a named movie archive member by suffix."""
        return next((m for m in all_members if m.name.endswith(path)), None)

    if find_member('main.txt') is None:
        return _err('dft', 'Invalid file format, does not seem to be a dft')

    parsed = {}

    def parse_input_file(name):
        """Count frames and include references in a script."""
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

    total = _dft_totals(parsed)
    if total is None:
        return _err('dft', 'Recursive includes detected, cannot parse')
    return _ok('dft', total, None, 'power-on', 'pc', 60.0)


# ---- game-specific TAS tools (surveyed from their own sources; the tools
# page names each). The stated time is the record either way: these read
# frames and, where the file carries it, the wall time, for Import from movie.

def _tas_ballance(data, fmt):
    """Read Ballance's compressed eight-byte frame records when present."""
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
    return None


def _tas_celeste(text, fmt):
    """Read CelesteTAS header frame counts and rerecords."""
    frames = 0
    rerecords = None
    file_time_found = False
    for s in text.splitlines():
        if s.startswith('FileTime:'):
            file_time_found = True
            frames = _celeste_frames(s, frames)
        elif not file_time_found and s.startswith('ChapterTime:'):
            frames = _celeste_frames(s, frames)
        elif s.startswith('TotalRecordCount:'):
            rerecords = _celeste_rerecords(s, rerecords)
        elif rerecords is None and s.startswith('RecordCount:'):
            rerecords = _celeste_rerecords(s, rerecords)
    if frames:
        return _ok(fmt, frames, rerecords, 'power-on', 'pc', 1000.0 / 17.0)
    return None


def _celeste_frames(line, previous):
    """Read a CelesteTAS frame total enclosed in parentheses."""
    match = re.search(r'\((\d+)\)', line)
    return int(match.group(1)) if match else previous


def _celeste_rerecords(line, previous):
    """Read a valid CelesteTAS rerecord header, retaining the previous value."""
    try:
        return int(line.split(':', 1)[1].strip())
    except ValueError:
        return previous


def _tas_shootme(text, fmt):
    """Sum ShootMe frame runs and single-frame pointer inputs."""
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


def parse_tas(data, fmt='tas'):
    """.tas is four formats: Ballance, PICO-8 Celeste, CelesteTAS, ShootMe."""
    ballance = _tas_ballance(data, fmt)
    if ballance is not None:
        return ballance
    text = data.decode('utf-8', 'replace')
    stripped = text.strip()
    m = re.fullmatch(r'\[[0-9, ]*\]([0-9]+(?:, ?[0-9]+)*),?', stripped, re.S)
    if m and '\n' not in stripped:
        frames = len([t for t in m.group(1).split(',') if t.strip()])
        return _ok(fmt, frames, None, 'power-on', 'pc', 30.0)
    return _tas_celeste(text, fmt) or _tas_shootme(text, fmt)


def _htas_value(line, key, previous, convert):
    """Read one hatTAS metadata value, retaining the last valid one."""
    if not line.startswith(key):
        return previous
    try:
        return convert(line.split(':', 1)[1].strip())
    except ValueError:
        return previous


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
            length = _htas_value(line, 'length:', length, int)
        elif line.startswith('fps:'):
            fps = _htas_value(line, 'fps:', fps, float)
    if not length:
        return _err('htas', 'No length: line found (hatTAS requires one)')
    return _ok('htas', length, None, 'savestate', 'pc', fps if fps > 0 else 60.0)


def _hltas_bulk(fields):
    """Read one timed HLTAS framebulk, ignoring metadata and invalid counts."""
    if len(fields) < 7:
        return 0, 0.0
    try:
        frame_time = float(fields[3])
        count = int(fields[6].split()[0]) if fields[6].strip() else 0
    except (ValueError, IndexError):
        return 0, 0.0
    return (count, frame_time * count) if count > 0 else (0, 0.0)


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
        count, duration = _hltas_bulk(line.split('|'))
        frames += count
        seconds += duration
    if not frames:
        return _err('hltas', 'No framebulks found after the frames line')
    return _ok('hltas', frames, None, 'savestate', 'pc',
               (frames / seconds) if seconds > 0 else None)


def _p2tas_repeat_count(line):
    """Read a repeat-block count, falling back to the original single pass."""
    try:
        return int(line.split()[1])
    except (ValueError, IndexError):
        return 0


def _p2tas_tick(line, current):
    """Advance SourceAutoRecord's absolute or relative tick counter."""
    match = re.match(r'(\+?)(\d+)>', line)
    if not match:
        return current
    return (current + int(match.group(2)) if match.group(1)
            else max(current, int(match.group(2))))


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
        """Count ticks through nested repeat blocks."""
        while i < len(lines):
            ln = lines[i]
            if ln.startswith('repeat'):
                n = _p2tas_repeat_count(ln)
                after, out = i + 1, cur
                for _ in range(max(1, n)):
                    after, out = walk(i + 1, out)
                cur = out if n > 0 else cur
                i = after + 1   # past the matching end
                continue
            if ln == 'end':
                return i, cur
            cur = _p2tas_tick(ln, cur)
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
        """Close an open nested script block."""
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


def _replay_rply(data):
    """Read the versioned ReplayBot frame or x-position layout."""
    if data[4] >= 2:
        rtype = data[5]
        fps = struct.unpack_from('<f', data, 6)[0]
        body = data[10:]
        n = len(body) // 5
        if n and rtype in (0x01, 0x31):
            last = struct.unpack_from('<I', body, (n - 1) * 5)[0]
            if fps and fps > 0:
                return _ok('replay', last, None, 'power-on', 'pc', fps,
                           ['the last input lands on this frame; the run plays on a little longer'])
        if n:
            return _ok('replay', 0, None, 'power-on', 'pc', None,
                       ['x-position replay: no frame count in the file'])
    elif len(data) - 9 >= 5:
        return _ok('replay', 0, None, 'power-on', 'pc', None,
                   ['x-position replay: no frame count in the file'])
    return _err('replay', 'Empty replay')


def parse_replay(data):
    """Read ReplayBot's versioned or legacy Geometry Dash replay layout."""
    if len(data) >= 10 and data[:4] == b'RPLY':
        return _replay_rply(data)
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
        """Convert a time value into milliseconds."""
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


def _lz4_length(src, i, length):
    """Read an LZ4 extended literal or match length."""
    if length == 15:
        while True:
            b = src[i]; i += 1
            length += b
            if b != 255:
                break
    return length, i


def _lz4_block(src, out_size):
    """LZ4 block decompression, the ~20 lines of it: enough to read an
    OpenGMK replay without a native dependency."""
    out = bytearray()
    i = 0
    n = len(src)
    while i < n and len(out) < out_size:
        token = src[i]; i += 1
        lit, i = _lz4_length(src, i, token >> 4)
        out += src[i:i + lit]; i += lit
        if i >= n:
            break
        offset = src[i] | (src[i + 1] << 8); i += 2
        if offset == 0:
            raise ValueError('bad offset')
        mlen, i = _lz4_length(src, i, token & 15)
        mlen += 4
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
