#!/usr/bin/env python3
"""Voluntary import: generate archive draft folders for one TASVideos author.

Reads the local tasvideos backups (~/tasvideos-dumps) — never crawls the site —
and produces per-run draft folders in the layout of ToolAssisted-run/archive,
plus a REPORT.md of everything that needs human/expert attention before the
drafts are applied. Imports are voluntary and per-author: run this only for an
author who has claimed their identity and asked for the import.

The tool NEVER pushes. --apply copies the drafts into an archive checkout and
runs its validate.py; reviewing and committing stays with the operator.

Licensing rules (DESIGN.md §7): movie files, submission metadata and the
author's own submission notes only. Judge/staff text appended to notes is
stripped at the `----`/[user:...] boundary; suspicious leftovers are flagged
for manual review. Publication descriptions are never used.

Usage:
  import_tasvideos_author.py <username> [--dumps DIR] [--out DIR]
                             [--archive DIR] [--apply] [--current-only]
"""
import argparse
import datetime
import json
import pathlib
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

TODAY = datetime.date.today().isoformat()

SYSTEM_NAMES = {
    'A2600': 'Atari 2600', 'A7800': 'Atari 7800', 'NES': 'Nintendo Entertainment System',
    'SNES': 'Super Nintendo Entertainment System', 'N64': 'Nintendo 64',
    'GB': 'Game Boy', 'GBC': 'Game Boy Color', 'GBA': 'Game Boy Advance',
    'DS': 'Nintendo DS', 'Genesis': 'Sega Genesis', 'SMS': 'Sega Master System',
    'GG': 'Sega Game Gear', 'Saturn': 'Sega Saturn', 'PSX': 'PlayStation',
    'PSP': 'PlayStation Portable', 'C64': 'Commodore 64', 'DOS': 'DOS',
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
# Exact NTSC/PAL rates where the community-standard value is known; other
# systems fall back to the backup's systemFrameRate and get flagged.
EXACT_FPS = {
    'nes': 60.0988138974405, 'fds': 60.0988138974405,
    'snes': 60.0988118623484, 'sgb': 59.7275005696058,
    'a2600': 59.9227510135505, 'c64': 50.1245421245421,
    'genesis': 59.922751013551, 'gb': 59.7275005696058,
    'gbc': 59.7275005696058, 'gba': 59.7275005696058,
    'gg': 59.922751013551, 'sms': 59.922751013551,
    'n64': 60.0, 'psx': 59.29286256195557,
}
START_TYPES = {None: 'power-on', 0: 'power-on', 1: 'savestate', 2: 'sram'}
# Systems where setting up a faithful reproduction environment is genuinely
# painful (BIOS/disk images, library versions, ROM sets…) — reproductions
# there earn the hard-system bonus. Provisional list, experts refine.
HARD_SYSTEMS = {'dos', 'amiga', 'pc', 'linux', 'windows', 'arcade', 'psx',
                'saturn', '3do', 'segacd', 'pcecd', 'dreamcast', 'gc', 'wii',
                'pc88', 'pc98', 'x68k', 'msx', 'apple2', 'a800', 'zxs', 'c64'}


def fetch(url, timeout=20):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'toolAssisted.run import tool (thumbnails)'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def fetch_thumbnail(pub, encodes):
    """Mandatory thumbnail for an imported run — YouTube-first (author's own
    encode frame), tasvideos publication screenshot as fallback.
    Returns (filename, bytes) or (None, None)."""
    for e in encodes:
        m = re.search(r'(?:v=|youtu\.be/)([\w-]+)', e.get('url', ''))
        if not m:
            continue
        for variant in ('maxresdefault', 'hqdefault'):
            data = fetch(f'https://img.youtube.com/vi/{m.group(1)}/{variant}.jpg')
            if data and data.startswith(b'\xff\xd8\xff') and len(data) <= 256 * 1024:
                time.sleep(0.3)
                return 'thumb.jpg', data
    for path in (pub.get('filePaths') or []):
        if not path.lower().endswith('.png'):
            continue
        data = fetch(f'https://tasvideos.org/media/{path}')
        time.sleep(1.5)   # polite pacing on tasvideos, same discipline as the backups
        if data and data.startswith(b'\x89PNG') and len(data) <= 256 * 1024:
            return 'thumb.png', data
    return None, None


def slugify(s):
    s = re.sub(r"['’]", '', s.lower())
    s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')
    return s or 'unknown'


def disclaimer(pub_id):
    return f'''> **Imported from TASVideos**
> This run was originally published at https://tasvideos.org/{pub_id}M and entered this archive as a voluntary
> import by one of its authors. On a collaborative work, any one author may authorize
> this republication. The notes below are the authors' own, reproduced under their
> Creative Commons license; text not written by the authors (judging feedback, staff
> annotations) has been removed. The original publication was verified and reproduced
> by TASVideos staff, a trustworthy TASing source; it is marked fully verified here
> without passing through this site's standard procedure. The movie file and these
> notes were obtained freely from tasvideos.org and are redistributed in observance
> of the Creative Commons Attribution 2.0 license under which they were published there.
'''


def strip_judge_text(text):
    """Cut everything from the judge/staff boundary onward.

    TASVideos judging feedback is appended to submission notes as
    `----` followed by a `[user:...]` line. Returns (clean, truncated, flags).
    """
    flags = []
    # drop the crawl header our backup prepended (submission title/status lines)
    text = re.sub(r'\A(?:\[#\d+:[^\n]*\]\n|\[status:[^\n]*\]\n|\n)+', '', text)
    m = re.search(r'^-{4,}\s*\n\s*\[user:', text, re.M)
    truncated = False
    if m:
        text = text[:m.start()]
        truncated = True
    if re.search(r'\[user:', text):
        flags.append('notes still contain a [user:...] mention after stripping — '
                     'REVIEW MANUALLY (author-edited-after-judge case?)')
    for word in ('judge', 'claiming for', 'accepting'):
        if re.search(rf'^!+.*{word}', text, re.I | re.M):
            flags.append(f'notes heading mentions {word!r} — review for staff text')
    return text.rstrip() + '\n', truncated, flags


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('username')
    ap.add_argument('--dumps', default=str(pathlib.Path.home() / 'tasvideos-dumps'))
    ap.add_argument('--out', default=None)
    ap.add_argument('--archive', default=str(pathlib.Path.home() / 'ToolAssisted-archive'))
    ap.add_argument('--apply', action='store_true',
                    help='copy drafts into the archive checkout and run validate.py')
    ap.add_argument('--current-only', action='store_true',
                    help='skip obsoleted publications')
    args = ap.parse_args()

    dumps = pathlib.Path(args.dumps)
    archive = pathlib.Path(args.archive)
    out = pathlib.Path(args.out or (pathlib.Path.home() / 'ToolAssisted-imports' / args.username.lower()))
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    pubs = json.loads((dumps / 'metadata/publications.json').read_text())
    subs = {s['id']: s for s in json.loads((dumps / 'metadata/submissions.json').read_text())}
    uname = args.username.lower()
    mine = [p for p in pubs
            if uname in [a.lower() for a in (p.get('authors') or [])]
            or uname in [a.strip().lower() for a in (p.get('additionalAuthors') or '').split(',') if a.strip()]]
    if not mine:
        sys.exit(f'no publications found for {args.username!r}')
    if args.current_only:
        mine = [p for p in mine if not p.get('obsoletedById')]

    # dedupe: publication ids already present in the archive checkout
    existing = set()
    for rj in archive.glob('games/*/*/runs/*/run.json'):
        src = json.loads(rj.read_text()).get('imported', {}).get('source', '')
        m = re.search(r'/(\d+)M$', src)
        if m:
            existing.add(int(m.group(1)))

    systems = json.loads((archive / 'systems.json').read_text()) if (archive / 'systems.json').exists() else {}
    new_systems = {}
    games = {}       # key -> {'game': ..., 'cats': ..., 'new': bool}
    authors_seen = {}
    report = {'imported': [], 'skipped': [], 'flags': [], 'new_games': set(), 'new_systems': set(),
              'new_options': set()}

    for p in sorted(mine, key=lambda p: p['id']):
        pid = p['id']
        rid = f'M{pid}'
        if pid in existing:
            report['skipped'].append(f'{rid} — already in the archive')
            continue
        sub = subs.get(p['submissionId'], {})
        flags = []

        sys_slug = slugify(p['systemCode'])
        if sys_slug not in systems and sys_slug not in new_systems:
            fps = EXACT_FPS.get(sys_slug)
            if fps is None:
                fps = float(p.get('systemFrameRate') or 60)
                flags.append(f'system {sys_slug!r}: fps {fps} taken from the backup — '
                             f'refine to the exact community value')
            new_systems[sys_slug] = {'name': SYSTEM_NAMES.get(p['systemCode'], p['systemCode']),
                                     'fps': fps,
                                     **({'hardToReproduce': True} if sys_slug in HARD_SYSTEMS else {})}
            report['new_systems'].add(sys_slug)

        gname = sub.get('gameName') or re.sub(r'^\S+ ', '', p['title']).split(' by ')[0]
        gslug = slugify(gname)
        gkey = f'{sys_slug}/{gslug}'
        goal = p.get('goal') or p.get('branch') or 'baseline'
        okey = slugify(goal)
        if goal == 'baseline':
            label, rule = 'fastest completion', 'Complete the game as fast as possible.'
        else:
            label = goal
            rule = (f'Imported from TASVideos as "{goal}"; rules to be formalized '
                    f'by the game\'s experts.')
            report['new_options'].add(f'{gkey}: {okey}')

        if gkey not in games:
            gdir = archive / 'games' / sys_slug / gslug
            if (gdir / 'game.json').exists():
                games[gkey] = {'game': json.loads((gdir / 'game.json').read_text()),
                               'cats': json.loads((gdir / 'categories.json').read_text()),
                               'new': False}
            else:
                games[gkey] = {'game': {'title': gname, 'system': sys_slug,
                                        'established': True,
                                        'tasvideosGameId': p.get('gameId')},
                               'cats': {'dimensions': [{'key': 'goal', 'name': 'Goal',
                                                        'options': []}]},
                               'new': True}
                report['new_games'].add(gkey)
        cats = games[gkey]['cats']
        goal_dim = next(d for d in cats['dimensions'] if d['key'] == 'goal')
        if okey not in {o['key'] for o in goal_dim['options']}:
            goal_dim['options'].append({'key': okey, 'label': label, 'rule': rule})

        # movie file
        zips = list((dumps / 'movies').glob(f'M{pid}-*.zip'))
        if not zips:
            report['skipped'].append(f'{rid} — movie zip not found in the backup')
            continue
        with zipfile.ZipFile(zips[0]) as z:
            names = [n for n in z.namelist() if not n.endswith('/')]
            if len(names) != 1:
                flags.append(f'movie zip holds {len(names)} files — took {names[0]!r}')
            movie_bytes = z.read(names[0])
            ext = pathlib.Path(names[0]).suffix.lstrip('.').lower()

        # notes
        notes_file = dumps / 'submission-notes' / f'S{p["submissionId"]}.txt'
        notes_html = ''
        if notes_file.exists():
            raw = notes_file.read_text(errors='replace')
            clean, truncated, nflags = strip_judge_text(raw)
            flags += nflags
            if not truncated:
                flags.append('no judge boundary found in notes — verify nothing '
                             'staff-written remains')
            notes_html = disclaimer(pid) + '\n' + clean
        else:
            flags.append('no submission notes in the backup')
            notes_html = disclaimer(pid)

        rom_name = sub.get('romName') or sub.get('gameVersion') or ''
        sha1 = ''
        m = re.search(r'SHA-?1:?\s*\*?\s*([0-9a-fA-F]{40})', notes_html)
        if m:
            sha1 = m.group(1).lower()
        start = START_TYPES.get(sub.get('movieStartType'), 'power-on')
        if start != 'power-on':
            flags.append(f'movie starts from {start} — double-check the contract')

        all_authors = list(p.get('authors') or [])
        for extra in (p.get('additionalAuthors') or '').split(','):
            if extra.strip() and extra.strip() not in all_authors:
                all_authors.append(extra.strip())
        for a in all_authors:
            authors_seen.setdefault(a.lower(), a)

        rom = {}
        if rom_name: rom['name'] = rom_name
        if sha1: rom['sha1'] = sha1
        encodes = [{'kind': 'youtube', 'url': u} for u in (p.get('urls') or [])
                   if 'youtu' in u]
        thumb_name, thumb_bytes = fetch_thumbnail(p, encodes)
        if not thumb_name:
            flags.append('no thumbnail obtainable (YouTube + tasvideos both failed) — '
                         'the author must supply one before this draft validates')
        run = {
            'id': rid, 'game': gkey, 'category': {'goal': okey},
            'authors': [{'user': a} for a in all_authors],
            'tools': [],
            'movie': {'file': f'{rid}.{ext}', 'format': ext,
                      'frames': p.get('frames') or 0,
                      'rerecords': p.get('rerecordCount'),
                      'start': start},
            **({'thumbnail': thumb_name} if thumb_name else {}),
            'contract': {'emulator': p.get('emulatorVersion') or sub.get('emulatorVersion') or '',
                         **({'rom': rom} if rom else {})},
            'status': {'reproduced': 'imported', 'verified': 'imported',
                   # TASVideos' flag token "Verified" is its Console-verified flag
                   'console': 'imported' if 'Verified' in (p.get('flags') or []) else 'none'},
            'imported': {'source': f'https://tasvideos.org/{pid}M',
                       'importedBy': args.username, 'importedAt': TODAY},
            'encodes': encodes,
            'submitted': p.get('createTimestamp') or '',
            'attachments': [],
        }
        if not run['encodes']:
            flags.append('no YouTube encode among the publication urls')

        rdir = out / 'games' / sys_slug / gslug / 'runs' / rid
        rdir.mkdir(parents=True)
        (rdir / f'{rid}.{ext}').write_bytes(movie_bytes)
        if thumb_name:
            (rdir / thumb_name).write_bytes(thumb_bytes)
        (rdir / 'run.json').write_text(json.dumps(run, indent=1) + '\n')
        (rdir / 'notes.md').write_text(notes_html)
        report['imported'].append(rid)
        for fl in flags:
            report['flags'].append(f'{rid}: {fl}')

    # game/category/system/author scaffolding
    for gkey, g in games.items():
        gdir = out / 'games' / pathlib.Path(gkey)
        if not any(gdir.rglob('run.json')):
            continue
        gdir.mkdir(parents=True, exist_ok=True)
        (gdir / 'game.json').write_text(json.dumps(g['game'], indent=1) + '\n')
        (gdir / 'categories.json').write_text(json.dumps(g['cats'], indent=1) + '\n')
    if new_systems:
        merged = {**systems, **new_systems}
        (out / 'systems.json').write_text(json.dumps(merged, indent=1) + '\n')
    (out / 'authors').mkdir(exist_ok=True)
    for low, canon in sorted(authors_seen.items()):
        afile = archive / 'authors' / f'{slugify(low)}.json'
        if afile.exists():
            continue
        (out / 'authors' / f'{slugify(low)}.json').write_text(json.dumps({
            'username': canon,
            'claimed': low == uname,
            'tasvideosProfile': f'https://tasvideos.org/Users/Profile/{canon}',
        }, indent=1) + '\n')

    rep = [f'# Import draft for {args.username} — {TODAY}', '',
           f'{len(report["imported"])} runs drafted · {len(report["skipped"])} skipped',
           '']
    if report['new_systems']:
        rep += ['## New systems (verify names + exact fps)',
                *(f'- {s}' for s in sorted(report['new_systems'])), '']
    if report['new_games']:
        rep += [f'## New games created ({len(report["new_games"])}) — slugs/titles to review',
                *(f'- {g}' for g in sorted(report['new_games'])), '']
    if report['new_options']:
        rep += ['## Category options needing expert rule wording',
                *(f'- {o}' for o in sorted(report['new_options'])), '']
    if report['flags']:
        rep += [f'## Flags ({len(report["flags"])})', *(f'- {f}' for f in report['flags']), '']
    if report['skipped']:
        rep += ['## Skipped', *(f'- {s}' for s in report['skipped']), '']
    rep += ['', 'Review the drafts, then re-run with --apply to copy them into the '
            'archive checkout (validate.py runs automatically; committing stays manual).']
    (out / 'REPORT.md').write_text('\n'.join(rep) + '\n')
    print(f'{len(report["imported"])} drafts -> {out}')
    print(f'{len(report["flags"])} flags, {len(report["new_games"])} new games, '
          f'{len(report["new_systems"])} new systems — see {out / "REPORT.md"}')

    if args.apply:
        for src in out.rglob('*'):
            if src.is_file() and src.name != 'REPORT.md':
                dst = archive / src.relative_to(out)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        print('applied to', archive)
        r = subprocess.run([sys.executable, str(archive / 'validate.py')])
        sys.exit(r.returncode)


if __name__ == '__main__':
    main()
