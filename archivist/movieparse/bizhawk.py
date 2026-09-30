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


def _chimera_rate(lower, lines, warnings):
    """Choose measured cycle timing before recorded vsync or nominal system timing."""
    cycles, clock = lower.get('cyclecount'), lower.get('clockrate')
    fps = _chimera_cycle_rate(cycles, clock, len(lines))
    if fps is None:
        fps = _chimera_vsync_rate(lower)
    if (fps is not None and not (cycles and clock)
            and abs(fps - round(fps)) < 1e-9 and round(fps) in (50, 60)):
        warnings.append(f'the project states a nominal {round(fps)} fps: the '
                        f'archive times this system at its own rate instead')
        fps = None
    if fps is None and str(lower.get('pal') or '').strip().lower() in ('1', 'true', 'yes'):
        warnings.append('the project says PAL: the rate applied is the system\'s own')
    return fps


def _chimera_cycle_rate(cycles, clock, frames):
    """Calculate measured frames per second from Chimera's cycle and clock counts."""
    try:
        if cycles and clock:
            elapsed = float(str(cycles).strip()) / float(str(clock).strip().replace(',', '.'))
            if elapsed > 0:
                return frames / elapsed
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return None


def _chimera_vsync_rate(lower):
    """Read Chimera's recorded vsync rate when measured cycle timing is absent."""
    num, den = lower.get('vsyncnumerator'), lower.get('vsyncdenominator')
    try:
        if num and den and float(den):
            return float(num) / float(den)
    except (TypeError, ValueError):
        pass
    return None


def _chimera_last_input(doc, lower, lines, warnings):
    """Find the final pressed frame, preferring the project's explicit count."""
    rows = [_chimera_split(l) for l in lines]
    axis_count = max((len(a) for _, a in rows), default=0)
    if axis_count:
        warnings.append('analog axes: their resting value is read off the log, '
                        'not off the core')
    neutral = _chimera_neutral_axes([a for _, a in rows]) if axis_count else {}

    def pressed(row):
        """Detect a digital press or an axis away from its inferred rest."""
        masks, axes = row
        if any(c not in '. ' for mask in masks for c in mask):
            return True
        return any(v != neutral.get(i) for i, v in enumerate(axes))

    last_input = next((i for i in range(len(rows) - 1, -1, -1) if pressed(rows[i])), 0)
    for stated in [lower.get('lastinputframe')] + [doc.get(k) for k in CHIMERA_LAST_INPUT_KEYS]:
        try:
            frame = int(str(stated).strip())
        except (TypeError, ValueError):
            continue
        if 0 <= frame < len(rows):
            last_input = frame
            break
    if last_input == 0 and not pressed(rows[0]):
        warnings.append('nothing is pressed anywhere in this project: the '
                        'length is the input log, not the run')
        return len(rows)
    idle = len(rows) - 1 - last_input
    if idle > 0:
        warnings.append(f'{idle} frame{"s" if idle != 1 else ""} after the last '
                        f'input are not counted as run time')
    return last_input + 1


def _bk2_platform(header, pal):
    """Resolve the BizHawk platform and any platform-specific frame rate."""
    platform = _value_for(header, 'platform')
    platform = BIZ_TO_TASV.get(platform, platform)
    for override in (_bk2_primary_mode, _bk2_arcade_mode, _bk2_other_mode):
        mode = override(header, pal)
        if mode is not None:
            return mode
    return platform, None


def _bk2_primary_mode(header, pal):
    """Resolve high-priority console and cartridge modes."""
    if _bool_for(header, 'is32x'):
        return '32x', None
    if _bool_for(header, 'iscgbmode'):
        return 'gbc', None
    if _value_for(header, 'boardname') == 'fds':
        return 'fds', None
    if _bool_for(header, 'isvs'):
        return 'arcade', NTSC_NES
    return None


def _bk2_arcade_mode(header, pal):
    """Resolve arcade, Super Game Boy, and Sega mode timing."""
    if _bool_for(header, 'isstv'):
        return 'arcade', NTSC_SAT
    if _value_for(header, 'boardname') == 'sgb':
        return 'sgb', PAL_SNES if pal else NTSC_SNES
    if _bool_for(header, 'issegacdmode'):
        return 'segacd', None
    if _bool_for(header, 'isggmode'):
        return 'gg', None
    return None


def _bk2_other_mode(header, pal):
    """Resolve remaining BizHawk platform overrides without timing changes."""
    if _bool_for(header, 'issgmode'):
        return 'sg1000', None
    if _bool_for(header, 'isdsi'):
        return 'dsi', None
    if _bool_for(header, 'isdd'):
        return 'n64dd', None
    if _bool_for(header, 'isjaguarcd'):
        return 'jaguarcd', None
    return None


def _bk2_timing(header, core, frames, pal, fps, fmt):
    """Calculate frame count and rate from BizHawk's available clock fields."""
    cycle_count = _int_for(header, 'cyclecount')
    if core == 'octoshock':
        fps = PAL_PSX if pal else NTSC_PSX
    if cycle_count is not None:
        seconds = _bk2_cycle_seconds(header, core, cycle_count)
        if seconds is None:
            return None, None, _err(fmt, 'Missing or invalid ClockRate, could not parse movie time')
        fps = frames / seconds if seconds else None
    elif core == 'subneshawk':
        vblank_count = _int_for(header, 'vblankcount')
        if vblank_count is None:
            return None, None, _err(fmt, 'Missing VBlankCount, could not parse movie time')
        frames = vblank_count
    elif core == 'mame':
        vsync_atto = _int_for(header, 'vsyncattoseconds')
        if vsync_atto is None:
            return None, None, _err(fmt, 'Missing VsyncAttoseconds, could not parse movie time')
        fps = 1e18 / vsync_atto
    return frames, fps, None


def _bk2_cycle_seconds(header, core, cycle_count):
    """Convert BizHawk cycles to seconds using a supported core clock."""
    clock_rate = _value_for(header, 'clockrate').replace(',', '.')
    if clock_rate == '1000':
        return cycle_count / 1000.0
    if clock_rate in VALID_CLOCK_RATES:
        return cycle_count / float(clock_rate)
    if core in CYCLE_BASED_CORES:
        return cycle_count / CYCLE_BASED_CORES[core]
    return None

def _bk2_input_frames(archive):
    """Count pipe-prefixed frames in the BizHawk input-log archive entry."""
    input_name = next((n for n in archive.namelist()
                       if n.lower().startswith('input log')), None)
    if input_name is None:
        return None
    frames = 0
    with archive.open(input_name) as log:
        for line in io.TextIOWrapper(log, encoding='utf-8', errors='replace'):
            if line.startswith('|'):
                frames += 1
    return frames


def _chimera_game_time(lower, log_frames, warnings):
    """Read the game's own timer when Chimera's project carries one."""
    raw = lower.get('gametimems')
    if raw is None or str(raw).strip() == '':
        return None
    try:
        ms = int(str(raw).strip())
    except (TypeError, ValueError):
        warnings.append('the project states a game time that is not a number; '
                        'it is ignored')
        return None
    if ms < 0:
        warnings.append('the project states a negative game time; it is ignored')
        return None
    at = lower.get('gametimeframe')
    if at is not None and str(at).strip() != '':
        try:
            frame = int(str(at).strip())
        except (TypeError, ValueError):
            frame = None
        if frame is not None and frame != log_frames:
            warnings.append(f'the game time was read at frame {frame}, and the '
                            f'input log is {log_frames} frames: it may predate '
                            f'an edit')
    return ms / 1000.0


def parse_chimeraproject(data):
    """Parse Chimera project metadata and its input log's active frames."""
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

    rerecords = doc.get('rerecords')
    if not isinstance(rerecords, int) or isinstance(rerecords, bool) or rerecords < 0:
        rerecords = None
        warnings.append('missing rerecord count')

    lines = [l for l in re.split(r'\r\n|\r|\n', str(doc.get('input') or ''))
             if l.startswith('|')]
    if not lines:
        return _err(fmt, 'The input log holds no frames, can not parse')

    fps = _chimera_rate(lower, lines, warnings)
    # Two of Chimera's cores end a frame when the machine PRESENTS a picture
    # rather than at a vblank, so a game that renders slowly spans more than
    # one vblank in a frame. Frames over the rate then understate the elapsed
    # time by however much the game dropped: a lower bound, not a wrong
    # number, and the author states the time anyway.
    core_name = ((doc.get('core') or {}).get('name') or '') if isinstance(doc.get('core'), dict) else ''
    if str(core_name).strip().lower() in ('pcsx2', 'flycast'):
        warnings.append(f'{core_name} ends a frame when the machine presents a '
                        f'picture, so a game that drops frames makes this time a '
                        f'lower bound')

    igt = _chimera_game_time(lower, len(lines), warnings)
    frames = _chimera_last_input(doc, lower, lines, warnings)
    return _ok(fmt, frames, rerecords, 'power-on', system, fps, warnings, igt)


def parse_bk2(data, fmt='bk2'):
    """Read BizHawk's zipped movie header and input log."""
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
    platform, fps = _bk2_platform(header, pal)

    start = 'power-on'
    if _bool_for(header, 'startsfromsavestate'):
        start = 'savestate'
    elif _bool_for(header, 'startsfromsaveram'):
        start = 'sram'

    core = _value_for(header, 'core')

    frames = _bk2_input_frames(z)
    if frames is None:
        return _err(fmt, 'Missing input log, can not parse')

    frames, fps, error = _bk2_timing(header, core, frames, pal, fps, fmt)
    if error:
        return error
    return _ok(fmt, frames, rerecords, start, platform, fps, warnings)


# ---------------------------------------------------------------- text logs
