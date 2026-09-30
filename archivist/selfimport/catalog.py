"""Self-service TASVideos import: the engine behind /api/import/*.

Reads the operator's personal tasvideos backup (DUMPS_DIR: metadata/,
movies/, submission-notes/, thumbnails/) — never crawls tasvideos.org —
and writes imported-run folders straight into the archive checkout.
Refreshing the backup is what makes newly published movies importable.

Licensing rules are the same as the operator tool (tools/
import_tasvideos_author.py): movie files, submission metadata and the
author's own notes only; judge/staff text is stripped at the
`----`/[user:...] boundary; publication descriptions are never used.
"""
import datetime
import json
import pathlib
import re
import urllib.request

import providers

MOVIE_MAX = 32 * 1024 * 1024   # the same cap the archivist applies at submit
import zipfile

SYSTEM_NAMES = {
    'A2600': 'Atari 2600', 'A5200': 'Atari 5200', 'A7800': 'Atari 7800', 'NES': 'Nintendo Entertainment System',
    'SNES': 'Super Nintendo Entertainment System', 'N64': 'Nintendo 64',
    'GB': 'Game Boy', 'GBC': 'Game Boy Color', 'GBA': 'Game Boy Advance',
    'DS': 'Nintendo DS', 'Genesis': 'Sega Genesis', 'SMS': 'Sega Master System',
    'GG': 'Sega Game Gear', 'Saturn': 'Sega Saturn', 'PSX': 'PlayStation',
    'PSP': 'PlayStation Portable', 'PS2': 'PlayStation 2',
    'PS3': 'PlayStation 3', 'Xbox': 'Xbox', 'Symbian': 'Symbian (N-Gage)',
    'C64': 'Commodore 64', 'DOS': 'DOS',
    'PC': 'PC', 'Amiga': 'Amiga', '3DO': '3DO', 'Arcade': 'Arcade',
    'MSX': 'MSX', 'MSX2': 'MSX2', 'PCE': 'PC Engine / TurboGrafx-16',
    'SG': 'Sega SG-1000', 'BSX': 'Satellaview',
    'MCD32X': 'Sega CD 32X', 'WSWAN': 'WonderSwan',
    'Lynx': 'Atari Lynx', 'NGP': 'Neo Geo Pocket', 'NeoGeo': 'Neo Geo AES', 'VBoy': 'Virtual Boy',
    'Coleco': 'ColecoVision', 'INTV': 'Intellivision', 'Dreamcast': 'Sega Dreamcast',
    'GC': 'GameCube', 'Wii': 'Wii', 'Windows': 'Windows', 'Linux': 'Linux',
    'ZXS': 'ZX Spectrum', 'A800': 'Atari 800',
    'Apple2': 'Apple II', 'AppleII': 'Apple II',
    'X68K': 'Sharp X68000', 'PC88': 'NEC PC-8801', 'PC98': 'NEC PC-9801',
    'FDS': 'Famicom Disk System', 'SGX': 'SuperGrafx', 'Vectrex': 'Vectrex',
    'O2': 'Odyssey 2', 'Uzebox': 'Uzebox', 'TI83': 'TI-83',
    'SG1000': 'Sega SG-1000',
    '32X': 'Sega 32X', 'SegaCD': 'Sega CD', 'PCECD': 'PC Engine CD',
}
EXACT_FPS = {
    'nes': 60.0988138974405, 'fds': 60.0988138974405,
    'snes': 60.0988118623484, 'sgb': 59.7275005696058,
    'a2600': 59.9227510135505, 'c64': 50.1245421245421,
    'genesis': 59.922751013551, 'gb': 59.7275005696058,
    'gbc': 59.7275005696058, 'gba': 59.7275005696058,
    'gg': 59.922751013551, 'sms': 59.922751013551,
    'n64': 60.0, 'psx': 59.29286256195557,
    # the machines Chimera brought: the rate each core declares for its own
    # (waterbox.config vsync), which is the rate a movie of it really ran at
    'ps2': 59.94005994005994, 'psp': 59.94005994005994,
    'dreamcast': 59.94005994005994, 'xbox': 59.94005994005994,
    'ps3': 59.94005994005994,
    'symbian': 60.0,   # EKA2L1 declares 60/1 for the handset
    'a5200': 59.9227510135505,   # the Atari 8-bit clock, as the 2600's
    'neogeo': 59.18560606060606,   # ares hints 6000000/(384*264)
    'ngp': 59.95023661999317,      # ares hints 6144000/(515*199)
    # each shares its video timing with a machine already here
    'sg1000': 59.922751013551, '32x': 59.922751013551,
    'segacd32x': 59.922751013550524, 'bsx': 60.0988118623484,
    'sgx': 59.8261054534819, 'msx2': 59.9227510135505,
    'appleii': 59.9227510135505,   # AppleWin: (157500000/11 * 65/912) / 17030
}
START_TYPES = {None: 'power-on', 0: 'power-on', 1: 'savestate', 2: 'sram'}
HARD_SYSTEMS = {'dos', 'amiga', 'pc', 'linux', 'windows', 'arcade', 'psx',
                'saturn', '3do', 'segacd', 'pcecd', 'dreamcast', 'gc', 'wii',
                'ps2', 'psp', 'ps3', 'xbox', 'symbian', 'a5200',
                'neogeo', 'ngp', 'bsx', '32x', 'segacd32x', 'msx2',
                'pc88', 'pc98', 'x68k', 'msx', 'apple2', 'appleii', 'a800', 'zxs',
                'c64'}

_pub_cache = {'mtime': None, 'pubs': None, 'subs': None}


def slugify(s):
    """Turn a source name into a safe lowercase slug."""
    s = re.sub(r"['’]", '', s.lower())
    s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')
    return s or 'unknown'


def disclaimer(pub_id):
    # the source is named by its link and nothing else: the site this came
    # from is one trusted source among the ones we may read from
    """Write the licensing and provenance notice for an import."""
    return f'''> **Imported**
> This run was originally published at https://tasvideos.org/{pub_id}M and entered this archive as a voluntary
> import by one of its authors, who takes the responsibility for importing a
> collaborative work. The notes below are the author's own, reproduced under their
> Creative Commons license; text not written by the authors (judging feedback, staff
> annotations) has been removed. The original publication was verified and reproduced
> at its source, a trusted site; it is marked fully verified here
> without passing through this site's standard procedure. The movie file and these
> notes were obtained freely from the source and are redistributed in observance
> of the Creative Commons Attribution 2.0 license under which they were published there.
'''


def strip_judge_text(text):
    """Cut everything from the judge/staff boundary onward. The operator tool
    (tools/import_tasvideos_author.py) calls this one, so a licensing rule is
    written once. Returns (clean, flags)."""
    flags = []
    text = re.sub(r'\A(?:\[#\d+:[^\n]*\]\n|\[status:[^\n]*\]\n|\n)+', '', text)
    m = re.search(r'^-{4,}\s*\n\s*\[user:', text, re.M)
    if m:
        text = text[:m.start()]
    else:
        flags.append('no judge boundary found in notes; review that nothing staff-written remains')
    if re.search(r'\[user:', text):
        flags.append('notes still mention [user:...] after stripping; review manually')
    for word in ('judge', 'claiming for', 'accepting'):
        if re.search(rf'^!+.*{word}', text, re.I | re.M):
            flags.append(f'notes heading mentions {word!r}; review for staff text')
    return text.rstrip() + '\n', flags


def load_pubs(dumps):
    """Publications + submissions from the backup, cached on mtime."""
    pfile = dumps / 'metadata' / 'publications.json'
    mtime = pfile.stat().st_mtime
    if _pub_cache['mtime'] != mtime:
        _pub_cache['pubs'] = json.loads(pfile.read_text())
        _pub_cache['subs'] = {s['id']: s for s in json.loads(
            (dumps / 'metadata' / 'submissions.json').read_text())}
        versions_file = dumps / 'metadata' / 'game-versions.json'
        _pub_cache['versions'] = (json.loads(versions_file.read_text())
                                  if versions_file.exists() else {})
        _pub_cache['mtime'] = mtime
    return _pub_cache['pubs'], _pub_cache['subs']


def pubs_for(dumps, username):
    """Find publications credited to a specific author."""
    pubs, _ = load_pubs(dumps)
    u = username.lower()
    return [p for p in pubs
            if u in [a.lower() for a in (p.get('authors') or [])]
            or u in [a.strip().lower() for a in (p.get('additionalAuthors') or '').split(',') if a.strip()]]


def archived_sources(archive):
    """Publication ids that must never be imported (again): every id named by
    a run's imported.source, plus — belt and braces — every id whose run
    FOLDER already exists, whatever its origin. Guarantees an import can
    never overwrite or duplicate an existing run."""
    existing = set()
    for rj in archive.glob('games/*/*/runs/*/run.json'):
        try:
            src = json.loads(rj.read_text()).get('imported', {}).get('source', '')
        except Exception:
            continue
        m = re.search(r'/(\d+)M$', src)
        if m:
            existing.add(int(m.group(1)))
    for rdir in archive.glob('games/*/*/runs/M*'):
        m = re.fullmatch(r'M(\d+)', rdir.name)
        if m:
            existing.add(int(m.group(1)))
    return existing


def _zip_movie_size(dumps, pid):
    """How big the movie inside the backup zip is, without unpacking it."""
    zips = sorted((dumps / 'movies').glob(f'M{pid}-*.zip'))
    if not zips:
        return None
    try:
        with zipfile.ZipFile(zips[0]) as z:
            sizes = [i.file_size for i in z.infolist() if not i.filename.endswith('/')]
        return max(sizes) if sizes else 0
    except Exception:                                       # noqa: BLE001
        return None
