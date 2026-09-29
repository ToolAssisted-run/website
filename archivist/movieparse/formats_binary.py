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
    """Parse PJM movie metadata and input length."""
    return _psx_movie(data, 'pjm', b'PJM ', True)


def parse_pxm(data):
    """Parse PXM movie metadata and input length."""
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
        pal = pal or _header_true(low, pal_keys)
        savestate = savestate or _header_true(low, state_keys)
    if not frames:
        return _err(fmt, 'No input lines found')
    fps = (fps_pal if pal and fps_pal else fps_ntsc)
    return _ok(fmt, frames, rerecords, 'savestate' if savestate else 'power-on',
               system, fps)


def _header_true(line, keys):
    """Check a movie header's boolean key against accepted true values."""
    return any(line.startswith(key) and line.split(None, 1)[1:]
               and line.split(None, 1)[1] in ('1', 'true') for key in keys)


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
