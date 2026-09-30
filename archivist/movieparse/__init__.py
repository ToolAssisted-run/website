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

from .bizhawk import parse_chimeraproject, parse_bk2
from .formats_text import *
from .formats_binary import *
from .formats_text import _lz4_block
from .formats_binary import _psx_movie, _pipe_text_movie

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
    """Check whether a movie extension has an accepted parser."""
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
