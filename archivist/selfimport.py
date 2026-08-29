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
    'A2600': 'Atari 2600', 'A7800': 'Atari 7800', 'NES': 'Nintendo Entertainment System',
    'SNES': 'Super Nintendo Entertainment System', 'N64': 'Nintendo 64',
    'GB': 'Game Boy', 'GBC': 'Game Boy Color', 'GBA': 'Game Boy Advance',
    'DS': 'Nintendo DS', 'Genesis': 'Sega Genesis', 'SMS': 'Sega Master System',
    'GG': 'Sega Game Gear', 'Saturn': 'Sega Saturn', 'PSX': 'PlayStation',
    'PSP': 'PlayStation Portable', 'PS2': 'PlayStation 2',
    'C64': 'Commodore 64', 'DOS': 'DOS',
    'PC': 'PC', 'Amiga': 'Amiga', '3DO': '3DO', 'Arcade': 'Arcade',
    'MSX': 'MSX', 'PCE': 'PC Engine / TurboGrafx-16', 'WSWAN': 'WonderSwan',
    'Lynx': 'Atari Lynx', 'NGP': 'Neo Geo Pocket', 'VBoy': 'Virtual Boy',
    'Coleco': 'ColecoVision', 'INTV': 'Intellivision', 'Dreamcast': 'Sega Dreamcast',
    'GC': 'GameCube', 'Wii': 'Wii', 'Windows': 'Windows', 'Linux': 'Linux',
    'ZXS': 'ZX Spectrum', 'A800': 'Atari 800', 'Apple2': 'Apple II',
    'X68K': 'Sharp X68000', 'PC88': 'NEC PC-8801', 'PC98': 'NEC PC-9801',
    'FDS': 'Famicom Disk System', 'SGX': 'SuperGrafx', 'Vectrex': 'Vectrex',
    'O2': 'Odyssey 2', 'Uzebox': 'Uzebox', 'TI83': 'TI-83', 'SG1000': 'SG-1000',
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
    # the three Chimera brought: the rate each core declares for its machine
    # (waterbox.config vsync), which is the rate a movie of it really ran at
    'ps2': 59.94005994005994, 'psp': 59.94005994005994,
    'dreamcast': 59.94005994005994,
}
START_TYPES = {None: 'power-on', 0: 'power-on', 1: 'savestate', 2: 'sram'}
HARD_SYSTEMS = {'dos', 'amiga', 'pc', 'linux', 'windows', 'arcade', 'psx',
                'saturn', '3do', 'segacd', 'pcecd', 'dreamcast', 'gc', 'wii',
                'ps2', 'psp',
                'pc88', 'pc98', 'x68k', 'msx', 'apple2', 'a800', 'zxs', 'c64'}

_pub_cache = {'mtime': None, 'pubs': None, 'subs': None}


def slugify(s):
    s = re.sub(r"['’]", '', s.lower())
    s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')
    return s or 'unknown'


def disclaimer(pub_id):
    # the source is named by its link and nothing else: the site this came
    # from is one trusted source among the ones we may read from
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
    """Cut everything from the judge/staff boundary onward; same rules as
    the operator tool. Returns (clean, flags)."""
    flags = []
    text = re.sub(r'\A(?:\[#\d+:[^\n]*\]\n|\[status:[^\n]*\]\n|\n)+', '', text)
    m = re.search(r'^-{4,}\s*\n\s*\[user:', text, re.M)
    if m:
        text = text[:m.start()]
    else:
        flags.append('no judge boundary found in notes; review that nothing staff-written remains')
    if re.search(r'\[user:', text):
        flags.append('notes still mention [user:...] after stripping; review manually')
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


def scan(dumps, archive, username):
    """What the backup holds for this author vs what the archive already has."""
    mine = pubs_for(dumps, username)
    existing = archived_sources(archive)
    pending = []
    for p in sorted(mine, key=lambda p: p['id']):
        if p['id'] in existing:
            continue
        size = _zip_movie_size(dumps, p['id'])
        authors = list(p.get('authors') or [])
        for extra in (p.get('additionalAuthors') or '').split(','):
            if extra.strip() and extra.strip() not in authors:
                authors.append(extra.strip())
        pending.append({
            'id': p['id'], 'title': p.get('title') or f'M{p["id"]}',
            'system': p.get('systemCode') or '?',
            'goal': p.get('goal') or p.get('branch') or 'baseline',
            'obsolete': bool(p.get('obsoletedById')),
            'movieMissing': size is None,
            # says so up front, instead of leaving a publication that can never
            # be imported sitting in the list with no explanation
            'tooBig': bool(size and size > MOVIE_MAX),
            'authors': authors,
            'multiAuthor': len(authors) > 1})
    # the already-archived ones are listed too: the page shows the member's
    # whole catalogue, and a movie that silently never appears reads as lost
    already = []
    for p in sorted(mine, key=lambda p: p['id']):
        if p['id'] in existing:
            already.append({'id': p['id'],
                            'title': p.get('title') or f'M{p["id"]}'})
    backup_date = datetime.date.fromtimestamp(
        (dumps / 'metadata' / 'publications.json').stat().st_mtime).isoformat()
    return {'total': len(mine), 'archived': len(mine) - len(pending),
            'pending': pending, 'already': already, 'backupDate': backup_date}


def _thumbnail(dumps, pid, encodes, thumb_base):
    """Local backup thumbnail first, then the encode's own platform.

    Older TASVideos publications are not all on YouTube (plenty live on the
    Internet Archive), so the fallback goes through the same platform registry
    the rest of the site uses instead of assuming one of them.
    """
    local = dumps / 'thumbnails' / f'M{pid}.jpg'
    if local.is_file():
        data = local.read_bytes()
        if data.startswith(b'\xff\xd8\xff') and len(data) <= 256 * 1024:
            return 'thumb.jpg', data
    for e in encodes:
        pv = providers.resolve(e.get('url', ''))
        if not pv:
            continue
        data, ext = providers.thumbnail(pv['kind'], pv['id'], 256 * 1024)
        if data:
            return 'thumb' + ext, data
    return None, None


def import_one(dumps, archive, p, sub, username, today, thumb_base,
               allow_multi=False):
    """Write one publication into the archive checkout.
    Returns (ok, run_id_or_reason, flags)."""
    pid = p['id']
    rid = f'M{pid}'
    flags = []

    all_authors = list(p.get('authors') or [])
    for extra in (p.get('additionalAuthors') or '').split(','):
        if extra.strip() and extra.strip() not in all_authors:
            all_authors.append(extra.strip())
    if len(all_authors) > 1 and not allow_multi:
        # A blanket batch cannot speak for a collaboration. A member who
        # explicitly ticks a co-authored work can: the selection is their act,
        # the run records them as the importer, and the import page says
        # plainly that the responsibility for it is theirs. Any co-author can
        # still have it withdrawn, or erased with the rest of the authors.
        return (False, f'{rid}: {len(all_authors)} authors ({", ".join(all_authors)}); '
                       f'a collaborative work is only imported when you select it '
                       f'yourself, taking the responsibility for it', flags)


    zips = sorted((dumps / 'movies').glob(f'M{pid}-*.zip'))
    if not zips:
        return False, f'{rid}: movie zip not in the backup', flags
    with zipfile.ZipFile(zips[0]) as z:
        names = [n for n in z.namelist() if not n.endswith('/')]
        if not names:
            return False, f'{rid}: movie zip is empty', flags
        movie_bytes = z.read(names[0])
        ext = pathlib.Path(names[0]).suffix.lstrip('.').lower()
    if len(movie_bytes) > MOVIE_MAX:
        # the intake cap exists so the archive stays a repository people can
        # clone; a movie past it is a decision for a person, not a batch job
        return (False, f'{rid}: movie is {len(movie_bytes) >> 20} MB, over the '
                       f'{MOVIE_MAX >> 20} MB intake cap; ask on the forum to have '
                       f'it archived', flags)

    encodes = [{'kind': 'youtube', 'url': u} for u in (p.get('urls') or []) if 'youtu' in u]
    thumb_name, thumb_bytes = _thumbnail(dumps, pid, encodes, thumb_base)
    if not thumb_name:
        return False, f'{rid}: no thumbnail obtainable (backup + YouTube)', flags
    if not encodes:
        flags.append(f'{rid}: no YouTube encode among the publication urls')

    sys_slug = slugify(p['systemCode'])
    systems_file = archive / 'systems.json'
    systems = json.loads(systems_file.read_text()) if systems_file.exists() else {}
    if sys_slug not in systems:
        fps = EXACT_FPS.get(sys_slug)
        if fps is None:
            fps = float(p.get('systemFrameRate') or 60)
            flags.append(f'{rid}: system {sys_slug!r} fps {fps} taken from the backup; refine')
        systems[sys_slug] = {'name': SYSTEM_NAMES.get(p['systemCode'], p['systemCode']),
                             'fps': fps,
                             **({'hardToReproduce': True} if sys_slug in HARD_SYSTEMS else {})}
        systems_file.write_text(json.dumps(systems, indent=1) + '\n')

    gname = sub.get('gameName') or re.sub(r'^\S+ ', '', p['title']).split(' by ')[0]
    gslug = slugify(gname)
    gdir = archive / 'games' / sys_slug / gslug
    goal = p.get('goal') or p.get('branch') or 'baseline'
    okey = slugify(goal)
    if goal == 'baseline':
        label, rule = 'fastest completion', 'Complete the game as fast as possible.'
    else:
        label = goal
        rule = (f'Imported as "{goal}" from the source it was published at; '
                f'rules to be formalized by the game\'s experts.')
    if (gdir / 'game.json').exists():
        cats = json.loads((gdir / 'categories.json').read_text())
    else:
        gdir.mkdir(parents=True, exist_ok=True)
        (gdir / 'game.json').write_text(json.dumps(
            {'title': gname, 'system': sys_slug,
             'tasvideosGameId': p.get('gameId')}, indent=1) + '\n')
        cats = {'dimensions': [{'key': 'goal', 'name': 'Category', 'options': []}]}
    goal_dim = next((d for d in cats['dimensions'] if d['key'] == 'goal'), None)
    if goal_dim is None:
        goal_dim = {'key': 'goal', 'name': 'Category', 'options': []}
        cats['dimensions'].append(goal_dim)
    if okey not in {o['key'] for o in goal_dim['options']}:
        goal_dim['options'].append({'key': okey, 'label': label, 'rule': rule})
    (gdir / 'categories.json').write_text(json.dumps(cats, indent=1) + '\n')

    notes_file = dumps / 'submission-notes' / f'S{p["submissionId"]}.txt'
    if notes_file.exists():
        clean, nflags = strip_judge_text(notes_file.read_text(errors='replace'))
        flags += [f'{rid}: {f}' for f in nflags]
        notes_md = disclaimer(pid) + '\n' + clean
    else:
        flags.append(f'{rid}: no submission notes in the backup')
        notes_md = disclaimer(pid)

    rom_name = sub.get('romName') or sub.get('gameVersion') or ''
    # the file's SHA1 lives on the source's game-version record (issue #72);
    # the submission notes are the fallback for records that state it there
    sha1 = ''
    version_row = _pub_cache.get('versions', {}).get(str(sub.get('gameVersionId') or ''))
    if version_row and version_row.get('sha1'):
        sha1 = version_row['sha1'].lower()
    if not sha1:
        m = re.search(r'SHA-?1:?\s*\*?\s*([0-9a-fA-F]{40})', notes_md)
        if m:
            sha1 = m.group(1).lower()
    # one file row, as every new record carries them (the legacy single
    # `rom` object stays on the records that already have it)
    files = []
    if rom_name:
        files.append({'name': rom_name, **({'sha1': sha1} if sha1 else {})})
    start = START_TYPES.get(sub.get('movieStartType'), 'power-on')

    # Only the importer gets a record: they are a member here, having claimed
    # this identity. Coauthors are credited by name in the run and nothing
    # else, until they claim their own name.
    adir = archive / 'authors'
    adir.mkdir(exist_ok=True)
    afile = adir / f'{slugify(username)}.json'
    if not afile.exists():
        afile.write_text(json.dumps({'username': username, 'claimed': True},
                                    indent=1) + '\n')

    run = {
        'id': rid, 'game': f'{sys_slug}/{gslug}', 'category': {'goal': okey},
        'authors': [{'user': a} for a in all_authors],
        'tools': [],
        'movie': {'file': f'{rid}.{ext}', 'format': ext,
                  'frames': p.get('frames') or 0,
                  # a movie whose own frame rate differs from the system's
                  # default (BizHawk PC cores tick at 1000/s) carries it, or
                  # the clock reads hours where the run takes minutes
                  **({'fps': float(p['systemFrameRate'])}
                     if p.get('systemFrameRate') and abs(
                         float(p['systemFrameRate'])
                         - systems[sys_slug]['fps']) > 0.01 else {}),
                  'rerecords': p.get('rerecordCount'),
                  'start': start},
        'thumbnail': thumb_name,
        'contract': {'emulator': p.get('emulatorVersion') or sub.get('emulatorVersion') or '',
                     **({'files': files} if files else {})},
        'status': {'reproduced': 'imported', 'verified': 'imported',
                   # TASVideos' flag token "Verified" is its Console-verified flag
                   'console': 'imported' if 'Verified' in (p.get('flags') or []) else 'none'},
        'imported': {'source': f'https://tasvideos.org/{pid}M',
                     'importedBy': username, 'importedAt': today},
        'encodes': encodes,
        'submitted': p.get('createTimestamp') or '',
        'attachments': [],
    }
    clash = next(iter(archive.glob(f'games/*/*/runs/{rid}')), None)
    if clash is not None:
        return False, f'{rid}: a run with this id already exists ({clash.parent.parent.name}); not overwriting', flags
    rdir = gdir / 'runs' / rid
    rdir.mkdir(parents=True)
    (rdir / f'{rid}.{ext}').write_bytes(movie_bytes)
    (rdir / thumb_name).write_bytes(thumb_bytes)
    (rdir / 'run.json').write_text(json.dumps(run, indent=1) + '\n')
    (rdir / 'notes.md').write_text(notes_md)
    return True, rid, flags


def import_batch(dumps, archive, username, today, thumb_base, limit=6,
                 select=None):
    """Import up to `limit` of the SELECTED pending publications.

    The member picks which of their movies come over, one by one; nothing is
    imported that was not asked for by id. A selected co-authored work is
    imported on that selection: ticking it is the member's own act and the
    responsibility for it is theirs, which the import page says in as many
    words. Returns dict with imported ids, skipped reasons, flags, and how
    many of the selection are still pending."""
    wanted = {int(s) for s in (select or [])}
    mine = pubs_for(dumps, username)
    _, subs = load_pubs(dumps)
    existing = archived_sources(archive)
    pending = [p for p in sorted(mine, key=lambda p: p['id'])
               if p['id'] not in existing and p['id'] in wanted]
    imported, skipped, flags = [], [], []
    attempted = 0
    for p in pending:
        if len(imported) >= limit or attempted >= limit * 6:
            break
        attempted += 1
        ok, what, fl = import_one(dumps, archive, p, subs.get(p['submissionId'], {}),
                                  username, today, thumb_base, allow_multi=True)
        flags += fl
        (imported if ok else skipped).append(what)
    return {'imported': imported, 'skipped': skipped, 'flags': flags,
            'remaining': max(0, len(pending) - attempted)}
