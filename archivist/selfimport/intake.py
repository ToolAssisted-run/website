import datetime
import json
import pathlib
import re
import zipfile
import providers
from .catalog import (MOVIE_MAX, SYSTEM_NAMES, EXACT_FPS, START_TYPES, HARD_SYSTEMS,
                      _pub_cache, slugify, disclaimer, strip_judge_text, _zip_movie_size,
                      pubs_for, archived_sources)

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


def _import_system(archive, p, rid, flags):
    """Create an unknown system with the source's frame rate and reproduction flag."""
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
    return sys_slug, systems


def _import_game(archive, sys_slug, p, sub):
    """Ensure the imported game and its goal category exist in the checkout."""
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
    return gdir, gslug, okey


def _import_notes(dumps, p, rid, flags):
    """Keep only author-written submission notes and flag uncertain boundaries."""
    notes_file = dumps / 'submission-notes' / f'S{p["submissionId"]}.txt'
    if notes_file.exists():
        clean, nflags = strip_judge_text(notes_file.read_text(errors='replace'))
        flags += [f'{rid}: {f}' for f in nflags]
        return disclaimer(p['id']) + '\n' + clean
    flags.append(f'{rid}: no submission notes in the backup')
    return disclaimer(p['id'])


def _import_files(sub, notes_md):
    """Build the cartridge file contract using version metadata or notes SHA1."""
    rom_name = sub.get('romName') or sub.get('gameVersion') or ''
    sha1 = ''
    version_row = _pub_cache.get('versions', {}).get(str(sub.get('gameVersionId') or ''))
    if version_row and version_row.get('sha1'):
        sha1 = version_row['sha1'].lower()
    if not sha1:
        m = re.search(r'SHA-?1:?\s*\*?\s*([0-9a-fA-F]{40})', notes_md)
        if m:
            sha1 = m.group(1).lower()
    files = []
    if rom_name:
        files.append({'name': rom_name, **({'sha1': sha1} if sha1 else {})})
    return files


def _import_run(p, sub, rid, sys_slug, gslug, okey, authors, systems,
                ext, thumb_name, files, encodes, username, today):
    """Assemble the imported run record without writing it to disk."""
    start = START_TYPES.get(sub.get('movieStartType'), 'power-on')
    return {
        'id': rid, 'game': f'{sys_slug}/{gslug}', 'category': {'goal': okey},
        'authors': [{'user': a} for a in authors],
        'tools': [],
        'movie': {'file': f'{rid}.{ext}', 'format': ext,
                  'frames': p.get('frames') or 0,
                  **({'fps': float(p['systemFrameRate'])}
                     if p.get('systemFrameRate') and abs(
                         float(p['systemFrameRate'])
                         - systems[sys_slug]['fps']) > 0.01 else {}),
                  'rerecords': p.get('rerecordCount'),
                  'start': start},
        'thumbnail': thumb_name,
        'contract': {'emulator': p.get('emulatorVersion') or sub.get('emulatorVersion') or '',
                     **({'files': files} if files else {})},
        'status': {'reproduced': 'imported', 'verified': 'imported'},
        'imported': {'source': f'https://tasvideos.org/{p["id"]}M',
                     'importedBy': username, 'importedAt': today},
        'encodes': encodes,
        'submitted': p.get('createTimestamp') or '',
        'attachments': [],
    }


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

    sys_slug, systems = _import_system(archive, p, rid, flags)
    gdir, gslug, okey = _import_game(archive, sys_slug, p, sub)
    notes_md = _import_notes(dumps, p, rid, flags)
    files = _import_files(sub, notes_md)

    # Only the importer gets a record: they are a member here, having claimed
    # this identity. Coauthors are credited by name in the run and nothing
    # else, until they claim their own name.
    adir = archive / 'authors'
    adir.mkdir(exist_ok=True)
    afile = adir / f'{slugify(username)}.json'
    if not afile.exists():
        afile.write_text(json.dumps({'username': username, 'claimed': True},
                                    indent=1) + '\n')

    run = _import_run(p, sub, rid, sys_slug, gslug, okey, all_authors,
                      systems, ext, thumb_name, files, encodes, username, today)
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
