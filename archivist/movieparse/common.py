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
        warnings=None, igt=None):
    # Several formats derive the frame count from the file's own length
    # ((len - header) // stride) or from header bytes the uploader controls, so
    # a truncated or hostile file can compute a negative length. Frames feed
    # rankings, so refuse rather than archive nonsense.
    """Build a successful parse result, rejecting negative frames."""
    frames = int(frames)
    if frames < 0:
        return _err(fmt, 'Negative frame count: the file looks truncated')
    return {'ok': True, 'format': fmt, 'frames': frames,
            'rerecords': rerecords, 'start': start, 'system': system,
            'fps': fps, 'igt': igt, 'warnings': warnings or []}


def _err(fmt, msg):
    """Build a failed parse result."""
    return {'ok': False, 'format': fmt, 'error': msg}


# How much the site unpacks from one movie. A small upload can hold a file that
# unpacks to gigabytes, so nothing is unpacked without a bound. A file read
# whole stops at 50 MB. An input log is counted line by line, so neither it
# nor any line of it is ever held whole, and stops at 1 GB: the largest one
# archived (a 2.6 million frame PlayStation run) is 240 MB and counts in under
# half a second. A tar is read header by header, and stops at 10,000 entries.
UNPACK_WHOLE_MAX = 50 * 1024 * 1024
UNPACK_COUNTED_MAX = 1024 * 1024 * 1024
TAR_ENTRIES_MAX = 10_000


class UnpackTooLarge(ValueError):
    """A file inside a movie unpacks past what the site reads. The message
    names the file and the limit, and is shown to the uploader as it is."""


def _mb(n):
    return f'{n // (1 << 30)} GB' if n >= 1 << 30 else f'{n // (1 << 20)} MB'


def _too_large(name, limit, what='file'):
    """The refusal for one file (`name`), or for the whole movie (None)."""
    it = f'the {what} "{name}" inside this movie' if name else 'this movie'
    return UnpackTooLarge(
        f'{it} unpacks to more than {_mb(limit)}, and the site reads at most '
        f'{_mb(limit)} of it; if the movie is right, ask on the forum to have it '
        f'archived')


def zip_read(archive, name):
    """A zip entry's bytes, read whole: at most UNPACK_WHOLE_MAX of them."""
    if archive.getinfo(name).file_size > UNPACK_WHOLE_MAX:
        raise _too_large(name, UNPACK_WHOLE_MAX)
    with archive.open(name) as entry:
        data = entry.read(UNPACK_WHOLE_MAX + 1)
    if len(data) > UNPACK_WHOLE_MAX:
        raise _too_large(name, UNPACK_WHOLE_MAX)
    return data


def count_lines_starting(stream, prefix, name):
    """How many lines of a byte stream start with `prefix` (ASCII), with lines
    split as _lines splits them. Read in pieces: only the start of the line
    being read is kept, and the stream stops at UNPACK_COUNTED_MAX bytes."""
    width = len(prefix)
    count = 0
    total = 0
    head = b''
    while True:
        piece = stream.read(1 << 16)
        if not piece:
            break
        total += len(piece)
        if total > UNPACK_COUNTED_MAX:
            raise _too_large(name, UNPACK_COUNTED_MAX, 'input log')
        parts = re.split(rb'[\r\n]', piece)
        for part in parts[:-1]:
            if len(head) < width:
                head += part[:width - len(head)]
            if head.startswith(prefix):
                count += 1
            head = b''
        if len(head) < width:
            head += parts[-1][:width - len(head)]
    if head.startswith(prefix):
        count += 1
    return count


def zip_count_lines(archive, name, prefix):
    """count_lines_starting over a zip entry."""
    if archive.getinfo(name).file_size > UNPACK_COUNTED_MAX:
        raise _too_large(name, UNPACK_COUNTED_MAX, 'input log')
    with archive.open(name) as entry:
        return count_lines_starting(entry, prefix, name)


def tar_files(archive):
    """A tar's regular files, read header by header. Stops past
    TAR_ENTRIES_MAX entries, or when an entry would reach past
    UNPACK_COUNTED_MAX bytes of the unpacked tar, before unpacking it."""
    files = []
    entries = 0
    while True:
        member = archive.next()
        if member is None:
            return files
        entries += 1
        if entries > TAR_ENTRIES_MAX:
            raise UnpackTooLarge(
                f'this movie holds more than {TAR_ENTRIES_MAX:,} files, and the site '
                f'reads at most {TAR_ENTRIES_MAX:,}; if the movie is right, ask on '
                f'the forum to have it archived')
        if member.offset_data + member.size > UNPACK_COUNTED_MAX:
            raise _too_large(member.name, UNPACK_COUNTED_MAX)
        if member.isfile():
            files.append(member)


def tar_read(archive, member):
    """A tar entry's bytes, read whole: at most UNPACK_WHOLE_MAX of them."""
    if member.size > UNPACK_WHOLE_MAX:
        raise _too_large(member.name, UNPACK_WHOLE_MAX)
    return archive.extractfile(member).read()


def inflate(data, name, wbits):
    """zlib (wbits 15) or gzip (wbits 31, every member) data, unpacked whole:
    at most UNPACK_WHOLE_MAX bytes. zlib.error when it is not such data,
    EOFError when it ends early."""
    out = []
    size = 0
    while True:
        unpacker = zlib.decompressobj(wbits)
        piece = unpacker.decompress(data, UNPACK_WHOLE_MAX + 1 - size)
        size += len(piece)
        if size > UNPACK_WHOLE_MAX or unpacker.unconsumed_tail:
            raise _too_large(name, UNPACK_WHOLE_MAX)
        out.append(piece)
        if not unpacker.eof:
            raise EOFError('the compressed data ends early')
        data = unpacker.unused_data
        if wbits == 31:
            data = data.lstrip(b'\0')     # gzip's padding between members
        if wbits != 31 or not data:
            return b''.join(out)


def _lines(text):
    """Return nonempty lines from text in any newline convention."""
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
    """Check whether a header contains a nonempty value."""
    key_l = key.lower()
    return any(l.lower().startswith(key_l) and l.lower().replace(key_l, '').strip()
               for l in lines)


def _bool_for(lines, key):
    """Read a boolean movie header value."""
    v = _value_for(lines, key)
    if not v:
        return False
    try:
        return int(v) == 1
    except ValueError:
        return v == 'true'


def _int_for(lines, key):
    """Read a nonnegative integer movie header value."""
    v = _value_for(lines, key)
    try:
        n = int(v)
        return n if n >= 0 else None
    except ValueError:
        return None


def _pipe_header_and_frames(text):
    """Separate text movie headers from input frames."""
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
               'dc': 'dreamcast',      # Chimera writes the Dreamcast as DC
               # ares names each of its machines its own way, and a project
               # made on one carries that name: these are the ones the archive
               # keeps under a different key
               'ng': 'neogeo', 'ngpc': 'ngp', 'a52': 'a5200', 'ps1': 'psx',
               'sfc': 'snes', 'cv': 'coleco', 'ws': 'wswan', 'wsc': 'wswan',
               'mcd': 'segacd', 'sg': 'sg1000', 'mcd32x': 'segacd32x',
               # FBNeo's boards arrive under their own ids (CPS1, CPS2, CPS3,
               # SYS16 are keys here already). Its NEOGEO is the arcade MVS,
               # not the AES cartridge machine ares runs as NG, so it must not
               # fall through to that one; 'system16' is its setting's spelling
               # of the same board.
               'neogeo': 'mvs', 'system16': 'sys16',
               # MAME's X68000 names itself in full; the archive keeps the
               # short key the TASVideos corpus uses
               'x68000': 'x68k',
               # A game core IS the game, with no machine under it, and every
               # one of them is the Native system: the project names the game
               # (the core's systemId), and the game is the game, filed under
               # native/<slug>. The rate comes from the movie here and never
               # from the system, because each of these states its own (12 fps
               # for SDLPoP, 35 for DSDA-Doom, 70.09 for OpenSamurai).
               'princeofpersia': 'native', 'princeofpersia2': 'native',
               'swordofthesamurai': 'native', 'syndicate': 'native',
               'anotherworld': 'native', 'doom': 'native', '4dsportsboxing': 'native',
               'ikemengo': 'native', 'srb2': 'native'}
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

# Game cores that end a step at the GAME's tick rather than at a fixed
# interval, so the project's VsyncNumerator/VsyncDenominator is the rate of
# the one step that ran last and not the rate the run held. SDLPoP and
# SDLPoP2 step the frames up to the next game tick (their own frame_delay,
# base_speed in play and fight_speed in a fight) and a single frame in the
# title, the story scenes and the menus, so one run visits several rates:
# for SDLPoP2 that is 70.0863 / 5, / 6 and / 1 Hz, and up to / 70 for a step
# that reads nothing. Frames over such a rate is an estimate, and the project
# says so rather than being quoted as exact. A core that writes CycleCount
# and ClockRate needs none of this: the measured rate IS the run's average,
# and _chimera_cycle_rate prefers it.
VARIABLE_STEP_CORES = {'sdlpop', 'sdlpop2'}

# A whole 50 or 60 with no cycle count behind it is, for a console core, a
# placeholder: the machine's real rate is a fraction near it, and the archive
# keeps that. These cores have no machine under them whose rate could be
# kept: what they play states its own rate, and the core reports exactly
# that. A Flash movie's rate is a number in its header, 50 as readily as 24,
# so the project's rate is the run's and the system's is only a default.
# MAME's Arcade is every board there is, each with a refresh of its own, and
# the core reports the running board's: one at exactly 60 Hz says 60 and
# means it, where the archive's figure for Arcade is one board's 59.637.
# (MAME X68000 is another core, by its full name, and is not one of these.)
# Godot runs a game at its Frame Rate setting, 60 by default and part of
# the machine (a movie needs the same value), and reports that setting as
# the project's rate: a game made for 50 keeps its 50. Each Godot 4 version
# is its own core, named with its version except the newest.
CONTENT_RATE_CORES = {'ruffle', 'mame', 'godot', 'godot 4.6', 'godot 4.5', 'godot 4.4'}

# A game core may let the player choose what its own timer counts from, which
# changes what the time MEANS and so what it may be ranked against. SDLPoP2's
# clock starts only with the first story scene after level 4; its
# igt_from_level_1 setting (the default) adds the play before that, so the
# time runs from the start of level 1. The project records the setting; the
# header carries only the number, so the basis is stated here instead.
IGT_BASIS_SETTINGS = {
    'igt_from_level_1': ("the game's clock starts after level 4, and this "
                         "project turned off counting the play before it: "
                         "levels 1 to 4 are not in this time"),
}


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



__all__ = ['_too_large', 'UNPACK_WHOLE_MAX', 'UNPACK_COUNTED_MAX', 'TAR_ENTRIES_MAX', 'UnpackTooLarge', 'zip_read', 'count_lines_starting', 'zip_count_lines', 'tar_files', 'tar_read', 'inflate', 'NTSC_NES', 'NTSC_SNES', 'PAL_SNES', 'NTSC_SAT', 'NTSC_PSX', 'PAL_PSX', 'DOOM_FPS', '_ok', '_err', '_lines', '_value_for', '_has_value', '_bool_for', '_int_for', '_pipe_header_and_frames', 'BIZ_TO_TASV', 'CYCLE_BASED_CORES', 'VALID_CLOCK_RATES', 'BK2_INVALID', 'TASPROJ_INVALID', 'CHIMERA_LAST_INPUT_KEYS', 'VARIABLE_STEP_CORES', 'CONTENT_RATE_CORES', 'IGT_BASIS_SETTINGS', '_chimera_neutral_axes', '_chimera_split', 'gzip', 'io', 'json', 'math', 're', 'struct', 'tarfile', 'zipfile', 'zlib', 'ET']
