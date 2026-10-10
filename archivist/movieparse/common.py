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
               'anotherworld': 'native', 'doom': 'native'}
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



__all__ = ['NTSC_NES', 'NTSC_SNES', 'PAL_SNES', 'NTSC_SAT', 'NTSC_PSX', 'PAL_PSX', 'DOOM_FPS', '_ok', '_err', '_lines', '_value_for', '_has_value', '_bool_for', '_int_for', '_pipe_header_and_frames', 'BIZ_TO_TASV', 'CYCLE_BASED_CORES', 'VALID_CLOCK_RATES', 'BK2_INVALID', 'TASPROJ_INVALID', 'CHIMERA_LAST_INPUT_KEYS', 'VARIABLE_STEP_CORES', 'IGT_BASIS_SETTINGS', '_chimera_neutral_axes', '_chimera_split', 'gzip', 'io', 'json', 'math', 're', 'struct', 'tarfile', 'zipfile', 'zlib', 'ET']
