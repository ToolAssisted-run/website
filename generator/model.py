"""The Model: the archive loaded, and every derivation from it.

Facts in (runs, games, groups, roles, claims, edits), derived state out
(effective verification/reproduction states, rankings, contributor
points, author stats and news, alias resolution across renames). No
HTML is built here and nothing is written to disk.

Loading happens at import time: the model IS this module, fully
populated, and the views read it as plain names."""
import datetime
import html
import os
import json
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.parse
import providers
from config import (
    ARCHIVE,
    TODAY,
)

def profile_slug(name):
    """Directory and URL segment for an author profile. Every name in the
    corpus is already [a-z0-9._-], so today's URLs are untouched; anything
    else (spaces, accents, markup, path separators) collapses to dashes so a
    username can never shape a path or break a link."""
    return re.sub(r'[^a-z0-9._-]+', '-', name.lower()).strip('-.') or 'author'

PT_REPRO_FIRST = 100

PT_REPRO_LATER = 25

PT_VERIFY = 20

PT_VERIFY_AGE_PER_DAY = 1   # the first verification pays more the longer a

PT_VERIFY_MAX = 1000        # run waits; the whole payout tops out here

PT_NEGLECT_PER_DAY = 2

PT_REPRO_MAX = 2000         # the whole first-reproduction payout tops out here

PT_REPRO_HARD = 50   # extra for hard-to-reproduce systems (systems.json flag)

PT_CONSOLE = 1000

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'archivist'))

systems = json.loads((ARCHIVE / 'systems.json').read_text())

authors = {}

for f in (ARCHIVE / 'authors').glob('*.json'):
    a = json.loads(f.read_text())
    authors[a['username'].lower()] = a

author_alias = {}

for a in authors.values():
    by = (a.get('claimedBy') or '').lower()
    if by and by != a['username'].lower() and by not in authors:
        author_alias[by] = a['username'].lower()

def canon(name):
    n = name.lower()
    return author_alias.get(n, n)

role_events = []

if (ARCHIVE / 'roles.json').exists():
    role_events = json.loads((ARCHIVE / 'roles.json').read_text()).get('events', [])

role_events.sort(key=lambda ev: (ev['date'], ev['user'].lower()))

def held_roles(events):
    held = {}
    for ev in events:
        key = (ev['user'].lower(), ev['role'], ev.get('scope', ''))
        if ev['action'] == 'granted':
            held[key] = ev
        else:
            held.pop(key, None)
    return held

ROLES_NOW = held_roles(role_events)

experts_reg = [{'user': ev['user'], 'scope': ev.get('scope', '')}
               for (u, role, scope), ev in ROLES_NOW.items() if role == 'expert']

ROLE_LABEL = {'committee': 'Steering Committee', 'moderator': 'Moderator',
              'expert': 'Expert', 'editor': 'Editor', 'founder': 'Founder'}

edit_events = []

if (ARCHIVE / 'edits.json').exists():
    edit_events = json.loads((ARCHIVE / 'edits.json').read_text()).get('events', [])

edits_of = {}

for _e in edit_events:
    edits_of.setdefault((_e['kind'], _e['key']), []).append(_e)

committee_now = sorted({ev['user'].lower() for (u, role, scope), ev in ROLES_NOW.items()
                        if role == 'committee'})

founder_now = sorted({ev['user'].lower() for (u, role, sc), ev in ROLES_NOW.items()
                      if role == 'founder'})

def scope_words(scope):
    """A scope in words, for a badge title and the role log."""
    if scope == 'site':
        return 'the whole site'
    if scope.startswith('group:'):
        key = scope[6:]
        gr = next((g for g in groups if g['key'] == key), None)
        return f'the {gr["title"] if gr else key} group'
    if '/' in scope:
        g = games.get(scope)
        return g['title'] if g else scope
    return systems.get(scope, {}).get('name', scope)

# widest first: the Committee chip leads, then what somebody does with runs
BADGED_ROLES = ('committee', 'expert', 'editor')

def roles_of(name):
    """Every role this member holds today, widest first."""
    low = name.lower()
    out = []
    for (u, role, scope), ev in ROLES_NOW.items():
        if u == low:
            out.append((role, scope))
    order = {'committee': 0, 'moderator': 1, 'expert': 2, 'editor': 3}
    return sorted(out, key=lambda rs: (order.get(rs[0], 9), rs[1]))

def role_events_of(name):
    low = name.lower()
    return [ev for ev in role_events if ev['user'].lower() == low]

groups = []          # game groups (group), in title order

rejected_groups = []  # refused by an expert: dissolved, kept only for the log

removed_groups = []   # a granted removal request: same, by a different route

if (ARCHIVE / 'groups.json').exists():
    all_groups = json.loads((ARCHIVE / 'groups.json').read_text()).get('groups', [])
    rejected_groups = [gr for gr in all_groups if gr.get('rejected')]
    removed_groups = [gr for gr in all_groups if gr.get('removed')]
    groups = [gr for gr in all_groups
              if not gr.get('rejected') and not gr.get('removed')]
    groups.sort(key=lambda gr: gr['title'].lower())

groups_by_game = {}  # game key -> [group, ...]; a game may sit in more than one

for gr in groups:
    for gk in gr['games']:
        groups_by_game.setdefault(gk, []).append(gr)

def group_games(gr):
    """The group's games that actually exist here, biggest group first."""
    return sorted((games[k] for k in gr['games'] if k in games),
                  key=lambda g: (systems[g['system']]['name'], g['title']))

def group_runs(gr):
    return [r for g in group_games(gr) for r in g['runs']]

def has_page(gr):
    """Every group has a page, however empty. A group used to need more than
    one game to be worth one, but a group an expert just made IS the state
    worth seeing: where games get added, and where the log can point. Hiding
    it read as the creation having failed."""
    return True

_uncl = {}

def unclassified_group():
    """Every game belongs to a group. The ones nobody has placed in a group
    yet belong to this one, which is derived rather than stored: writing 124
    game keys into groups.json would need an edit every time a game arrives,
    and the archive records facts, not what can be computed from them."""
    if not _uncl:
        placed = {k for gr in groups if has_page(gr) for k in gr['games']}
        _uncl.update({'key': 'uncategorized', 'title': 'Uncategorized',
                      'synthetic': True,
                      'games': sorted(k for k in games if k not in placed)})
    return _uncl

def unclassified_shown():
    """The catch-all only exists next to real group. An archive nobody has
    grouped yet gets no group view at all, rather than one card holding every
    game and saying nothing."""
    return (any(has_page(gr) for gr in groups)
            and bool(unclassified_group()['games']))

def groups_of(game_key):
    """The groups a game's page should link to: the group it belongs to, or
    Uncategorized if no group has claimed it."""
    mine = [gr for gr in groups_by_game.get(game_key, []) if has_page(gr)]
    if mine:
        return mine
    return ([unclassified_group()]
            if unclassified_shown() and game_key in unclassified_group()['games']
            else [])

def scopes_over(game_key):
    """Every expert scope that reaches this game, from widest to narrowest."""
    return ({'site', game_key.split('/')[0], game_key}
            | {'group:' + gr['key'] for gr in groups_by_game.get(game_key, [])})

def covering_experts(game_key):
    reach = scopes_over(game_key)
    return sorted({e['user'].lower() for e in experts_reg if e['scope'] in reach})

games = {}   # key: "sys/slug"

rejected_games = []

removed_games = []

runs = []

for gjson in ARCHIVE.glob('games/*/*/game.json'):
    gdir = gjson.parent
    key = f'{gdir.parent.name}/{gdir.name}'
    g = json.loads(gjson.read_text())
    g['key'] = key
    g['slugpath'] = key
    g['categories'] = json.loads((gdir / 'categories.json').read_text())
    g['runs'] = []
    if g.get('removed'):
        removed_games.append(g)
    if g.get('rejected'):
        # An expert refused this as not a real, distinct game. Its runs are
        # untouched and its page stays, carrying the refusal; it simply stops
        # appearing in the listings and the rankings until somebody re-homes
        # what is inside it.
        rejected_games.append(g)
    games[key] = g
    for rjson in sorted((gdir / 'runs').glob('*/run.json')):
        r = json.loads(rjson.read_text())
        if r.get('videoOnly') and 'movie' not in r:
            # in-memory shim so read paths hold; never written back
            r['movie'] = {'file': None, 'format': None, 'frames': 0}
        r['_dir'] = rjson.parent
        r['_game'] = g
        notes = rjson.parent / 'notes.md'
        r['_notes'] = notes.read_text() if notes.exists() else ''
        g['runs'].append(r)
        runs.append(r)

withdrawn_runs = [r for r in runs if r.get('withdrawn')]

runs = [r for r in runs if not r.get('withdrawn')]

for g in games.values():
    g['runs'] = [r for r in g['runs'] if not r.get('withdrawn')]

credited = {}          # lower -> the name as the run spells it

for r in runs:
    people = [x['user'] for x in r['authors']]
    for roster in ('reproductions', 'verifications', 'consoleVerifications', 'likes'):
        people += [x['user'] for x in r.get(roster, [])]
    for name in people:
        credited.setdefault(name.lower(), name)

def is_member(name):
    a = authors.get(canon(name))
    return bool(a and a.get('claimed'))

def run_fps(r):
    return (r.get('movie') or {}).get('fps') or systems[r['_game']['system']]['fps']

def run_seconds(r):
    """How long the run takes, whatever kind it is: the stated duration is
    the record (the author states it, importing from the movie if they like);
    frames over fps stand in for older runs that never stated one."""
    if r.get('duration'):
        return r['duration']
    if not r.get('videoOnly') and r['movie'].get('frames'):
        return r['movie']['frames'] / run_fps(r)
    return None

def parse_date(s):
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', s or '')
    return datetime.date(*map(int, m.groups())) if m else None

# ---- per-category metrics ----
# A category option may define `metrics`: an ordered hierarchy of what it
# ranks by. Absent means the classic implicit metric: real time, lower is
# better. The reserved key 'time' is always derived (frames/fps or stated
# duration), never typed for movie runs.
CLASSIC_METRICS = [{'key': 'time', 'label': 'Time', 'type': 'time',
                    'better': 'lower'}]

def run_metric_defs(r):
    """The metric hierarchy of the run's category. The first entry is the
    primary metric, shown wherever time shows classically."""
    if is_unclassified(r):
        return CLASSIC_METRICS
    for d in r['_game']['categories']['dimensions']:
        o = next((o for o in d['options']
                  if o['key'] == r['category'].get(d['key'])), None)
        if o and o.get('metrics'):
            return o['metrics']
    return CLASSIC_METRICS

def metric_value(r, mdef):
    """One run's value for one metric: derived seconds for the reserved
    'time' key, the stated number otherwise. None means not stated; a stored
    0 also means "not yet stated" and is folded into None here."""
    if mdef['key'] == 'time':
        return run_seconds(r)
    return (r.get('metrics') or {}).get(mdef['key']) or None

def rank_key(r):
    """Leaderboard order within one category: walk the metric hierarchy in
    order, direction-aware; an unstated value sorts after every real value at
    its level whichever way the metric points (a blank is never a winning
    result) and falls through to the next metric. The final tie-break is the
    submission date, earlier wins, so ranks are always plain 1, 2, 3."""
    parts = []
    for m in run_metric_defs(r):
        v = metric_value(r, m)
        parts.append((1, 0) if v is None else
                     (0, v if m['better'] == 'lower' else -v))
    return (parts, r.get('submitted') or '9999-99-99', r['id'])

RUN_BY_ID = {r['id']: r for r in runs}

def is_unclassified(r):
    return (r.get('category') or {}).get('goal') == 'unclassified'

def option_of(r):
    """The run's goal option dict (None for Unclassified)."""
    if is_unclassified(r):
        return None
    goal = r['category'].get('goal')
    return next((o for d in r['_game']['categories']['dimensions'] for o in d['options']
                 if o['key'] == goal), None)

def sub_of(r):
    """The run's subcategory dict, when its category has them."""
    o = option_of(r)
    sub = (r.get('category') or {}).get('sub')
    if not o or not sub:
        return None
    return next((s for s in o.get('subcategories', []) if s['key'] == sub), None)

def cat_label(r):
    """What the ranking calls the run's category: the option label, and the
    subcategory after a middle dot when the category has them."""
    if is_unclassified(r):
        return 'Unclassified'
    g = r['_game']
    label = ' × '.join(next(o['label'] for o in d['options'] if o['key'] == r['category'][d['key']])
                       for d in g['categories']['dimensions'])
    sub = sub_of(r)
    return f'{label} · {sub["label"]}' if sub else label

def hard_bonus(r):
    return PT_REPRO_HARD if systems[r['_game']['system']].get('hardToReproduce') else 0

def nlikes(r):
    return len(r.get('likes', []))

def live(acts):
    return [a for a in acts if not a.get('invalidated')]

def earning(acts):
    """The acts that earned their points: the live ones, and those an edit
    of the run voided afterwards (the act was honest work on what the run
    was then; only an expert finding it faulty forfeits the points)."""
    return [a for a in acts if not a.get('invalidated') or a['invalidated'].get('cause') == 'edit']

# the systems a movie can be played back on real hardware (a replay device
# on the console): systems.json marks them hardwareVerifiable (issue #53);
# everywhere else the console signal does not exist, and the site says
# nothing about it
HW_SYSTEMS = {k for k, v in systems.items() if v.get('hardwareVerifiable')}

def hw_verifiable(system):
    return system in HW_SYSTEMS

def console_applicable(r):
    """Whether hardware verification is a thing for this run at all: a real
    input movie, on a system that can play one back."""
    return not r.get('videoOnly') and hw_verifiable(r['_game']['system'])

def console_state(r):
    """'imported' when TASVideos had already console-verified it, 'community'
    when somebody here played it back on hardware, 'not-applicable' when the
    run cannot be (video-only, or a system nobody plays back on hardware),
    else 'none'."""
    if (r.get('status') or {}).get('console') == 'imported':
        return 'imported'
    if live(r.get('consoleVerifications', [])):
        return 'community'
    return 'none' if console_applicable(r) else 'not-applicable'

def eff_state(r):
    """(reproduced, verified) derived from rosters; 'imported' passes through.

    Verification is the ranking gate: one live verification, from anybody,
    makes a run verified (no tiers; an expert may later invalidate it).
    Reproduction is a recorded, paid act of assurance and gates nothing."""
    st = r.get('status', {})
    if st.get('reproduced') == 'imported':
        return ('imported', 'imported')
    reps = live(r.get('reproductions', []))
    vers = live(r.get('verifications', []))
    rs = 'community' if reps else 'none'
    vs = 'verified' if vers else 'none'
    return (rs, vs)

def is_ranked(r):
    # verification alone is the gate; reproduction never was one after
    # 2026-08-19, and Unclassified runs rank by likes in their own section
    rs, vs = eff_state(r)
    return vs != 'none'

def arrival_times():
    """When each run actually entered the archive, read from git: the commit
    that first added its run.json. `importedAt` is date-only, so a day's worth
    of imports ties; the commit stamp is exact to the second and covers native
    submissions too. Empty when the archive is not a git checkout (tests)."""
    out = {}
    try:
        res = subprocess.run(
            ['git', 'log', '--diff-filter=A', '--name-only', '--format=C %cI',
             '--reverse', '--', 'games'],
            cwd=ARCHIVE, capture_output=True, text=True, timeout=120)
        if res.returncode:
            return out
    except Exception:
        return out
    stamp = None
    for line in res.stdout.splitlines():
        if line.startswith('C '):
            stamp = line[2:].strip()
        elif stamp and line.endswith('/run.json'):
            out.setdefault(line.rsplit('/', 2)[-2], stamp)
    return out

ARRIVALS = arrival_times()

def archived_at(r):
    """When the run entered this archive. For imported runs `submitted` is the
    original TASVideos publication date (often years old), so anything that
    means "new here" uses the git arrival stamp, falling back to the import
    date and finally to the submission date."""
    stamp = ARRIVALS.get(r['id'])
    if stamp:
        return stamp
    imp = r.get('imported') or {}
    return imp.get('importedAt') or r.get('submitted') or ''

def is_pending(r):
    """Waiting on the one gate there is: verification (2026-08-19).
    Unclassified runs cannot be verified and rank by likes, so nothing gates
    them and they are never pending."""
    rs, vs = eff_state(r)
    if rs == 'imported' or is_unclassified(r):
        return False
    return vs == 'none'

# run visit counts: operational state the archivist keeps beside the
# checkout, never in the archive. Present on the live origin, absent on CI
# and the Pages standby, where the most-viewed shelf simply does not render.
_VISITS_FILE = pathlib.Path(os.environ.get('SITE_VISITS_FILE',
                                           str(ARCHIVE.parent / 'visits.json')))
try:
    _visits = json.loads(_VISITS_FILE.read_text())
    visits_known = True
except (OSError, ValueError):
    _visits = {}
    visits_known = False   # no host state: cards hide the eye entirely

def nvisits(r):
    return int(_visits.get(r['id'], 0))

def days_pending(r):
    d = parse_date(r.get('submitted'))
    return max(0, (TODAY - d).days) if d else 0

def repro_bounty(r):
    """Current first-reproduction payout for a still-unreproduced run."""
    return min(PT_REPRO_FIRST + hard_bonus(r) + days_pending(r) * PT_NEGLECT_PER_DAY,
               PT_REPRO_MAX)

def verify_bounty(r):
    """Current first-verification payout for a still-unverified run:
    the base, plus one point per day waiting, capped at double."""
    return min(PT_VERIFY + days_pending(r) * PT_VERIFY_AGE_PER_DAY, PT_VERIFY_MAX)

BADGES = [(25000, '25k'), (10000, '10k'), (5000, '5k'), (1000, '1k')]

def contrib_tier(pts):
    """(label, tier class) for a contributor, or None for somebody who has
    yet to earn a point. The first act earns the plain badge; the tiers are
    milestones on top of it."""
    if pts < 1:
        return None
    for threshold, lbl in BADGES:
        if pts >= threshold:
            return f'{lbl} Contributor', f'tier-{lbl}'
    return 'Contributor', 'tier-first'

def badge_for(pts):
    tier = contrib_tier(pts)
    return tier[0] if tier and tier[1] != 'tier-first' else None

points = {}   # user(lower) -> {'points': n, 'acts': [(date, html-free desc, pts, run)]}

def award(user, pts, desc, r, date):
    p = points.setdefault(canon(user), {'user': user, 'points': 0, 'acts': []})
    p['points'] += pts
    p['acts'].append((date or '', desc, pts, r))

for r in runs:
    if r.get('status', {}).get('reproduced') == 'imported':
        continue
    reps = sorted(earning(r.get('reproductions', [])), key=lambda a: a.get('date') or '')
    sub = parse_date(r.get('submitted'))
    for i, act in enumerate(reps):
        if i == 0:
            ad = parse_date(act.get('date'))
            neglect = (ad - sub).days * PT_NEGLECT_PER_DAY if ad and sub and ad > sub else 0
            award(act['user'], min(PT_REPRO_FIRST + hard_bonus(r) + neglect, PT_REPRO_MAX),
                  'first reproduction', r, act.get('at') or act.get('date'))
        else:
            award(act['user'], PT_REPRO_LATER + hard_bonus(r), 'reproduction', r,
              act.get('at') or act.get('date'))
    vers = sorted(earning(r.get('verifications', [])), key=lambda a: a.get('date') or '')
    for i, act in enumerate(vers):
        if i == 0:
            ad = parse_date(act.get('date'))
            aged = (ad - sub).days * PT_VERIFY_AGE_PER_DAY if ad and sub and ad > sub else 0
            award(act['user'], min(PT_VERIFY + aged, PT_VERIFY_MAX), 'first verification', r,
                  act.get('at') or act.get('date'))
        else:
            award(act['user'], PT_VERIFY, 'verification', r,
                  act.get('at') or act.get('date'))
    for act in earning(r.get('consoleVerifications', [])):
        award(act['user'], PT_CONSOLE, 'console verification', r,
              act.get('at') or act.get('date'))

# ---- medals: achievements read off the acts above (issue #59) ----
# Nothing is stored: every medal is recomputed from the recorded acts at
# build time, so the board cannot disagree with the archive. Each is
# (key, metal, mark, words); the words are the tooltip.
MEDAL_RULES = [
    ('console-1',   'bronze', 'H', 1,   'console verification', 'Hardware verifier: played a run back on original hardware'),
    ('console-10',  'gold',   'H', 10,  'console verification', 'Hardware verifier: ten console verifications'),
    ('repro-10',    'bronze', 'R', 10,  'reproduction',         'Reproducer: ten reproductions'),
    ('repro-100',   'silver', 'R', 100, 'reproduction',         'Reproducer: a hundred reproductions'),
    ('repro-500',   'gold',   'R', 500, 'reproduction',         'Reproducer: five hundred reproductions'),
    ('verify-10',   'bronze', 'V', 10,  'verification',         'Verifier: ten verifications'),
    ('verify-100',  'silver', 'V', 100, 'verification',         'Verifier: a hundred verifications'),
    ('verify-500',  'gold',   'V', 500, 'verification',         'Verifier: five hundred verifications'),
    ('first-25',    'silver', '1', 25,  'first',                'Pathfinder: the first to reproduce or verify twenty-five runs'),
    ('first-100',   'gold',   '1', 100, 'first',                'Pathfinder: the first to reproduce or verify a hundred runs'),
]

def _act_kind(desc):
    if desc == 'console verification':
        return 'console verification'
    return 'reproduction' if 'reproduction' in desc else 'verification'

def _recent_leaders(days):
    """Who earned the most points in the last `days` days (ties share it)."""
    since = (TODAY - datetime.timedelta(days=days)).isoformat()
    tally = {}
    for p in points.values():
        got = sum(pts for (d, desc, pts, r) in p['acts'] if (d or '')[:10] >= since)
        if got > 0:
            tally[p['user'].lower()] = got
    if not tally:
        return {}
    best = max(tally.values())
    return {u: n for u, n in tally.items() if n == best}

_week_leaders = _recent_leaders(7)
_month_leaders = _recent_leaders(30)

def medals_of(user):
    """The medals a member holds right now: [(key, metal, mark, words)]."""
    p = points.get(canon(user))
    if not p:
        return []
    out = []
    low = p['user'].lower()
    if low in _week_leaders:
        out.append(('week', 'gold', 'W', f'Top contributor this week: {_week_leaders[low]} points in the last seven days'))
    if low in _month_leaders:
        out.append(('month', 'gold', 'M', f'Top contributor this month: {_month_leaders[low]} points in the last thirty days'))
    counts = {}
    for (d, desc, pts, r) in p['acts']:
        counts[_act_kind(desc)] = counts.get(_act_kind(desc), 0) + 1
        if desc.startswith('first '):
            counts['first'] = counts.get('first', 0) + 1
    best = {}   # one medal per family: the highest earned
    for key, metal, mark, need, kind, words in MEDAL_RULES:
        if counts.get(kind, 0) >= need:
            best[mark] = (key, metal, mark, words)
    out.extend(best.values())
    return out

# ---- author news: events on your runs (reproduced / verified / liked) ----
author_news = {}
for r in runs:
    a_low = [canon(a['user']) for a in r['authors']]
    events = ([('reproduced', act) for act in r.get('reproductions', [])]
              + [('verified', act) for act in r.get('verifications', [])]
              + [('liked', l) for l in r.get('likes', [])])
    for kind, act in events:
        for uname in a_low:
            author_news.setdefault(uname, []).append(
                {'date': act.get('date', ''), 'at': act.get('at') or '',
                 'kind': kind, 'actor': act['user'], 'run': r['id'],
                 'title': r['_game']['title']})
for lst in author_news.values():
    # the arrival second when the record carries one, the day otherwise;
    # ISO strings order correctly either way
    lst.sort(key=lambda e: e['at'] or e['date'], reverse=True)

def board_date(r):
    """The date a board shows and sorts by (#39, #47): the completion date
    the authors stated, the submission day otherwise. One rule for both, so
    a list never looks unsorted against its own column."""
    return (r.get('completed') or '').strip() or (r.get('submitted') or '')[:10]

# ---- author stats (menu + profiles) ----
author_stats = {}
for uname, a in authors.items():
    mine = [r for r in runs if any(canon(x['user']) == uname for x in r['authors'])]
    author_stats[uname] = {
        'runs': len(mine),
        'author': sum(nlikes(r) for r in mine),
        'views': sum(nvisits(r) for r in mine),   # across every run they authored
        'contrib': points.get(uname, {}).get('points', 0),
    }


live_groups = [gr for gr in groups if has_page(gr)]
if unclassified_shown():
    live_groups = live_groups + [unclassified_group()]

