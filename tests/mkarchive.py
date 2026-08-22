"""Build a minimal, fully-controlled archive for tests.

The other suites layer their fixtures on a copy of the real archive, which is
right for coverage but useless when a test must assert an exact number (points,
stars) or that NO generated prose contains something (em dashes, "Legacy") —
real member content would drown the signal. Here every byte is ours.

Hermetic: writes into a caller-provided temp dir, touches nothing else.
"""
import json
import pathlib

PNG = b'\x89PNG\r\n\x1a\n' + b'\0' * 60
JPG = b'\xff\xd8\xff' + b'\0' * 60

# Frame rates match the real systems.json values so clock() output is realistic.
DEFAULT_SYSTEMS = {
    'nes': {'name': 'Nintendo Entertainment System', 'fps': 60.0988138974405},
    'dos': {'name': 'DOS', 'fps': 60.0, 'hardToReproduce': True},
}


_movie_seq = [100]


def unique_movie():
    """A bk2 no other test has submitted. Intake refuses a movie the archive
    already holds, so fixtures that expect to succeed need distinct bytes and
    a distinct frame count (the same-work check looks at frames too)."""
    import io
    import zipfile
    _movie_seq[0] += 1
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('Header.txt', 'Platform NES\nrerecordCount 42\n')
        z.writestr('Input Log.txt', 'LogKey:#Reset|Power|\n' + '|..|........|\n' * _movie_seq[0])
    return buf.getvalue()


def prune_superseded(root):
    """Delete member records whose name another record supersedes (claimedBy).

    Suites that copy the live archive need this until the live backfill lands:
    approving a claim deletes the record it replaces, and validate.py errs on
    a name that is both a member record and a superseded alias."""
    root = pathlib.Path(root)
    recs = {}
    for af in (root / 'authors').glob('*.json'):
        recs[af] = json.loads(af.read_text())
    by_name = {r['username'].lower(): af for af, r in recs.items()}
    alias = {}
    for r in recs.values():
        by = (r.get('claimedBy') or '').lower()
        if by and by != r['username'].lower():
            alias[by] = r['username'].lower()
            if by in by_name:
                by_name[by].unlink(missing_ok=True)
    # A live self-act that only the rename reveals (author under the old name,
    # act under the new, or the reverse). The remedy on the live archive is an
    # expert invalidation; the fixture applies the same so suites keep testing
    # the code rather than live drift the archive's own CI polices.
    canon = lambda n: alias.get(n.lower(), n.lower())
    for rj in root.glob('games/*/*/runs/*/run.json'):
        r = json.loads(rj.read_text())
        if r.get('status', {}).get('reproduced') == 'imported':
            continue
        anames = {canon(a['user']) for a in r.get('authors', [])}
        changed = False
        for kind in ('reproductions', 'verifications', 'consoleVerifications'):
            for act in r.get(kind, []):
                if canon(act['user']) in anames and not act.get('invalidated'):
                    act['invalidated'] = {'by': 'fixture', 'date': '2026-08-19',
                                          'reason': 'self-act through a rename'}
                    changed = True
        r.setdefault('likes', [])
        kept = [l for l in r['likes'] if canon(l['user']) not in anames]
        if kept != r['likes']:
            r['likes'] = kept
            changed = True
        if not r['likes']:
            r.pop('likes')
        if changed:
            live_v = [a for a in r.get('verifications', []) if not a.get('invalidated')]
            live_r = [a for a in r.get('reproductions', []) if not a.get('invalidated')]
            if r['status'].get('reproduced') != 'not-applicable':
                r['status']['reproduced'] = 'community' if live_r else 'none'
            r['status']['verified'] = ('confirmed' if any(a.get('expert') for a in live_v)
                                       else 'provisional' if live_v else 'none')
            rj.write_text(json.dumps(r, indent=1) + '\n')


MOVIE_EXTS = {'.3ct', '.bk2', '.ctas', '.ctm', '.dft', '.dsm', '.dtm', '.fbm',
              '.fm2', '.fm3', '.gbmv', '.gmv', '.gzm', '.jrsr', '.lmp', '.lsmv',
              '.ltm', '.m64', '.mar', '.omr', '.p2m2', '.tas', '.tasproj',
              '.vbm', '.wtf'}


def lighten(root, keep_under=1024 * 1024):
    """Shrink the movie blobs in a COPY of the archive.

    A suite that copies and then clones the archive several times was moving
    hundreds of megabytes around, one file of it 98 MB, and CI started killing
    the job mid-clone. No suite reads a movie's bytes: they are checked for
    existence and size, so a placeholder is as good as the real thing and the
    fixture stops being the heaviest part of the run.

    Only ever call this on a throwaway copy.
    """
    freed = 0
    for f in root.rglob('*'):
        if f.is_file() and f.suffix.lower() in MOVIE_EXTS:
            size = f.stat().st_size
            if size > keep_under:
                f.write_bytes(b'PK\x03\x04 placeholder for a movie the tests never read')
                freed += size
    return freed


def run_spec(rid, game='nes/testgame', goal='fastest', authors=('Ada',),
             frames=6000, **extra):
    """One run; `extra` overrides or adds any run.json field."""
    spec = {'id': rid, 'game': game, 'goal': goal, 'authors': list(authors),
            'frames': frames}
    spec.update(extra)
    return spec


def make_archive(root, runs, systems=None, experts=None, authors_extra=None, ratified=None, empty_games=None, claims=None,
                 game_titles=None, categories=None, nonmembers=None, groups=None, role_events=None,
                 game_props=None):
    """Write a valid archive containing exactly `runs`.

    runs: list of dicts from run_spec(). Recognised keys beyond run.json's own
    fields: game ('sys/slug'), goal (category option key), authors (names),
    frames, notes (notes.md body).
    Returns the archive path.
    """
    root = pathlib.Path(root)
    systems = dict(systems or DEFAULT_SYSTEMS)
    # {'sys/slug': {'ratifiedBy': 'Who', 'ratifiedAt': '2026-01-01'}}
    ratified = dict(ratified or {})
    game_titles = dict(game_titles or {})
    game_props = dict(game_props or {})   # game key -> extra game.json fields (#44)
    (root / 'authors').mkdir(parents=True, exist_ok=True)
    (root / 'systems.json').write_text(json.dumps(systems, indent=1) + '\n')
    # roles are a log of grants, so a fixture roster is a list of grant events
    events = [{'user': e['user'], 'role': 'expert', 'scope': e['scope'],
               'action': 'granted', 'by': e.get('appointedBy', 'founder'),
               'date': e.get('appointedAt', '2026-01-01'),
               'reason': e.get('reason', 'fixture appointment')}
              for e in (experts or [{'user': 'Root', 'scope': 'site'}])]
    events += list(role_events or [])
    (root / 'roles.json').write_text(json.dumps({'events': events}, indent=1) + '\n')
    if groups:
        (root / 'groups.json').write_text(json.dumps({'groups': list(groups)}, indent=1) + '\n')
    # a game with no runs yet, exactly as /api/game/create writes one: an
    # empty goal list and nothing else. The build crashed on the first real
    # one, so every fixture archive can carry one from now on.
    for gk in (empty_games or []):
        gdir = root / 'games' / gk
        (gdir / 'runs').mkdir(parents=True, exist_ok=True)
        (gdir / 'game.json').write_text(json.dumps(
            {'title': gk.split('/')[1].replace('-', ' ').title(),
             'system': gk.split('/')[0], 'established': True,
             'ratifiedBy': 'Root', 'ratifiedAt': '2026-03-01'}, indent=1) + '\n')
        (gdir / 'categories.json').write_text(json.dumps(
            {'dimensions': [{'key': 'goal', 'name': 'Goal', 'options': []}]},
            indent=1) + '\n')
    if claims is not None:
        (root / 'claims.json').write_text(json.dumps({'requests': list(claims)},
                                                     indent=1) + '\n')

    names = {}
    for spec in runs:
        for a in spec['authors']:
            names.setdefault(a.lower(), a)
        for act in ('reproductions', 'verifications', 'consoleVerifications'):
            for x in spec.get(act, []):
                names.setdefault(x['user'].lower(), x['user'])
        for l in spec.get('likes', []):
            names.setdefault(l['user'].lower(), l['user'])
    for e in (experts or []):          # an expert is a member like anyone else
        names.setdefault(e['user'].lower(), e['user'])
    for a in (authors_extra or []):
        names.setdefault(a.lower(), a)
    # authors/ is the member list: a name credited on a run without a record is
    # a non-member, and gets nothing but the credit
    outsiders = {n.lower() for n in (nonmembers or [])}
    for low, canon in names.items():
        if low in outsiders:
            continue
        (root / 'authors' / f'{low}.json').write_text(json.dumps(
            {'username': canon, 'claimed': True}, indent=1) + '\n')

    seen_games = {}
    for spec in runs:
        sys_slug, gslug = spec['game'].split('/')
        gdir = root / 'games' / sys_slug / gslug
        if spec['game'] not in seen_games:
            gdir.mkdir(parents=True, exist_ok=True)
            (gdir / 'game.json').write_text(json.dumps(
                {'title': game_titles.get(spec['game'], gslug.replace('-', ' ').title()),
                 'system': sys_slug, 'established': True,
                 **(ratified.get(spec['game']) or {}),
                 **(game_props.get(spec['game']) or {})}, indent=1) + '\n')
            seen_games[spec['game']] = categories.copy() if categories else {
                'dimensions': [{'key': 'goal', 'name': 'Goal', 'options': []}]}
        cats = seen_games[spec['game']]
        goal_dim = cats['dimensions'][0]
        if spec['goal'] != 'unclassified' and spec['goal'] not in {
                o['key'] for o in goal_dim['options']}:
            goal_dim['options'].append({'key': spec['goal'],
                                        'label': spec['goal'].replace('-', ' '),
                                        'rule': 'Test rule.',
                                        **({'metrics': spec['goal_metrics']}
                                           if spec.get('goal_metrics') else {})})

        rid = spec['id']
        rdir = gdir / 'runs' / rid
        rdir.mkdir(parents=True, exist_ok=True)
        video_only = bool(spec.get('videoOnly'))
        if not video_only:
            (rdir / f'{rid}.bk2').write_bytes(b'PK\x03\x04 test movie')
        (rdir / 'thumb.png').write_bytes(PNG)
        run = {
            'id': rid, 'game': spec['game'], 'category': {'goal': spec['goal']},
            'authors': [{'user': a} for a in spec['authors']],
            **({} if video_only else
               {'movie': {'file': f'{rid}.bk2', 'format': 'bk2',
                          'frames': spec['frames'], 'rerecords': 10,
                          'start': 'power-on'}}),
            'thumbnail': 'thumb.png',
            'contract': {'emulator': 'BizHawk 2.11'},
            'status': spec.get('status',
                               {'reproduced': 'not-applicable', 'verified': 'none',
                                'console': 'not-applicable'} if video_only else
                               {'reproduced': 'none', 'verified': 'none'}),
            'encodes': [{'kind': 'youtube', 'url': 'https://www.youtube.com/watch?v=abc123DEF45'}],
            'submitted': spec.get('submitted', '2026-01-01T00:00:00Z'),
            'submittedBy': spec['authors'][0],
        }
        for k, v in spec.items():
            if k in ('id', 'game', 'goal', 'authors', 'frames', 'notes', 'goal_metrics'):
                continue
            run[k] = v
        # the third signal is part of the checked status cache
        run['status'].setdefault(
            'console', 'community' if run.get('consoleVerifications') else 'none')
        for act, prefix in (('reproductions', 'reproductions'),):
            if run.get(act):
                (rdir / prefix).mkdir(exist_ok=True)
                for i, x in enumerate(run[act], 1):
                    shot = x.setdefault('screenshot', f'{prefix}/{i}-{x["user"]}.png')
                    (rdir / shot).write_bytes(PNG)
        (rdir / 'run.json').write_text(json.dumps(run, indent=1) + '\n')
        if spec.get('notes'):
            (rdir / 'notes.md').write_text(spec['notes'])

    for gkey, cats in seen_games.items():
        sys_slug, gslug = gkey.split('/')
        (root / 'games' / sys_slug / gslug / 'categories.json').write_text(
            json.dumps(cats, indent=1) + '\n')
    return root
