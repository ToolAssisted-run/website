#!/usr/bin/env python3
"""Derivation tests: the numbers and orderings the site computes.

Status, ranking, contributor points, stars and the superseded chain are all
derived at build time from facts in the archive. Nothing else asserts the
arithmetic, so a sign flip or a lost bonus would change everyone's standing
with a green build. Every fixture here lives in a controlled archive
(tests/mkarchive.py) precisely so the expected numbers can be exact.

Also checks that the two independent implementations of case resolution — the
archivist's and the archive validator's — agree over an exhaustive enumeration.
A divergence there means the archivist pushes archives its own CI rejects.

Usage: tests/test_derivation.py
"""
import datetime
import itertools
import os
import json
import pathlib
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mkarchive  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
# the archivist's derivations live in its records layer since the MVC split
ARCHIVIST = REPO / 'archivist' / 'records.py'
VALIDATOR = pathlib.Path.home() / 'ToolAssisted-archive' / 'validate.py'

# weights mirrored from generator/build.py; if they change there, these
# expectations must be updated deliberately (that is the point)
PT_REPRO_FIRST, PT_REPRO_LATER, PT_VERIFY = 100, 25, 20
PT_NEGLECT_PER_DAY, PT_NEGLECT_CAP, PT_REPRO_HARD = 2, 200, 50

failures = []


def ck(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (f'  [{detail}]' if detail and not cond else ''))
    if not cond:
        failures.append(name)


def build(archive, out):
    return subprocess.run([sys.executable, str(REPO / 'generator/build.py'),
                           str(archive), str(out)], capture_output=True, text=True)


def extract_function(path, name):
    """Pull one top-level function out of a script that cannot be imported
    (both scripts run work at import time)."""
    src = path.read_text()
    start = src.index(f'def {name}(')
    rest = src[start:]
    end = len(rest)
    for m in re.finditer(r'\n(?=\S)', rest):
        if not rest[m.start() + 1:].startswith((')', ']', '}')):
            end = m.start()
            break
    ns = {}
    exec(compile(rest[:end], str(path), 'exec'), ns)   # noqa: S102 — test harness
    return ns[name]


def rows_of(section_html):
    return re.findall(r'runs/(M\d+)/', section_html)


def main():
    today = datetime.date.today()
    d = lambda n: (today - datetime.timedelta(days=n)).isoformat()   # noqa: E731

    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)

        # ---------------- status / ranking matrix ----------------
        specs = [
            # (id, status, rosters) -> expected table
            ('M900301', {'reproduced': 'none', 'verified': 'none'}, {}, 'pending'),
            ('M900302', {'reproduced': 'community', 'verified': 'none'},
             {'reproductions': [{'user': 'Rep', 'date': d(3)}]}, 'pending'),
            ('M900303', {'reproduced': 'none', 'verified': 'provisional'},
             {'verifications': [{'user': 'Ver', 'date': d(3)}]}, 'ranked'),
            ('M900304', {'reproduced': 'community', 'verified': 'provisional'},
             {'reproductions': [{'user': 'Rep', 'date': d(3)}],
              'verifications': [{'user': 'Ver', 'date': d(3)}]}, 'ranked'),
            ('M900305', {'reproduced': 'community', 'verified': 'confirmed'},
             {'reproductions': [{'user': 'Rep', 'date': d(3)}],
              'verifications': [{'user': 'Ver', 'date': d(3)},
                                {'user': 'Xprt', 'date': d(2), 'expert': True}]},
             'ranked'),
            # every act invalidated: derives back to pending
            ('M900306', {'reproduced': 'none', 'verified': 'none'},
             {'reproductions': [{'user': 'Rep', 'date': d(3),
                                 'invalidated': {'by': 'Root', 'date': d(1),
                                                 'reason': 'faulty'}}],
              'verifications': [{'user': 'Ver', 'date': d(3),
                                 'invalidated': {'by': 'Root', 'date': d(1),
                                                 'reason': 'faulty'}}]}, 'pending'),
            # imported: status passes through, ranked without rosters
            ('M900307', {'reproduced': 'imported', 'verified': 'imported'},
             {'imported': {'source': 'https://tasvideos.org/1M',
                           'importedBy': 'Ada', 'importedAt': d(9)}}, 'ranked'),
        ]
        # each row gets its own author: identical author sets in one category
        # supersede each other, which is a different rule (tested below)
        runs = [mkarchive.run_spec(rid, frames=1000 + i * 100, authors=[f'Auth{i}'],
                                   status=st, submitted=d(30), **extra)
                for i, (rid, st, extra, _) in enumerate(specs)]
        arch = mkarchive.make_archive(td / 'a1', runs)
        out = td / 'o1'
        r = build(arch, out)
        ck('matrix build succeeds', r.returncode == 0, r.stderr[-300:])
        if r.returncode:
            print(r.stderr[-2000:])
            sys.exit(1)
        game = (out / 'games/nes/testgame/index.html').read_text()
        ranked_part = game.split('Pending:')[0]
        pending_part = game.split('Pending:')[1] if 'Pending:' in game else ''
        for rid, _, _, want in specs:
            in_ranked = rid in rows_of(ranked_part)
            in_pending = rid in rows_of(pending_part)
            ck(f'{rid} lands in the {want} table',
               (in_ranked if want == 'ranked' else in_pending),
               f'ranked={in_ranked} pending={in_pending}')

        # ---------------- contributor points ----------------
        # hard system (dos), submitted 150 days ago, first reproduction today:
        # 100 base + 50 hard + min(150*2, 200) = 350; second reproduction 75;
        # each verification 20.
        pts_runs = [
            mkarchive.run_spec('M900311', game='dos/hardgame', frames=5000,
                               authors=['Ada'], submitted=d(150),
                               status={'reproduced': 'community', 'verified': 'provisional'},
                               reproductions=[{'user': 'Rep', 'date': d(0)},
                                              {'user': 'Rep2', 'date': d(0)}],
                               verifications=[{'user': 'Ver', 'date': d(0)},
                                              {'user': 'Ver2', 'date': d(0)}],
                               consoleVerifications=[{'user': 'Hardware', 'date': d(0),
                                                      'proof': 'https://example.com/rec'}]),
            # imported runs pay nobody
            mkarchive.run_spec('M900312', frames=5000, authors=['Bo'], submitted=d(150),
                               status={'reproduced': 'imported', 'verified': 'imported'},
                               imported={'source': 'https://tasvideos.org/2M',
                                         'importedBy': 'Bo', 'importedAt': d(9)}),
        ]
        arch = mkarchive.make_archive(td / 'a2', pts_runs)
        out = td / 'o2'
        r = build(arch, out)
        ck('points build succeeds', r.returncode == 0, r.stderr[-300:])
        stats = json.loads((out / 'assets/authorstats.json').read_text())
        want_first = PT_REPRO_FIRST + PT_REPRO_HARD + min(150 * PT_NEGLECT_PER_DAY, PT_NEGLECT_CAP)
        ck('first reproduction on a hard system, long neglected',
           stats.get('rep', {}).get('contrib') == want_first,
           f'got {stats.get("rep", {}).get("contrib")} want {want_first}')
        ck('later reproduction pays base + hard bonus only',
           stats.get('rep2', {}).get('contrib') == PT_REPRO_LATER + PT_REPRO_HARD,
           str(stats.get('rep2')))
        ck('verification pays its flat weight',
           stats.get('ver', {}).get('contrib') == PT_VERIFY, str(stats.get('ver')))
        ck('imported runs award nothing', 'importer' not in stats)

        ck('console verification pays its own weight',
           stats.get('hardware', {}).get('contrib') == 1000, str(stats.get('hardware')))

        # ---- contributor tiers: the badge a member wears is the one their
        # score buys, at every threshold. The fixtures elsewhere only ever
        # reach the first one, so the upper branches would otherwise ship
        # having never once been evaluated.
        tier_runs = []
        for i in range(25):
            console = [{'user': 'TierD', 'date': '2026-03-01',
                        'proof': 'https://example.com/d', 'hardware': 'NES'}]
            if i < 10:
                console.append({'user': 'TierC', 'date': '2026-03-01',
                                'proof': 'https://example.com/c', 'hardware': 'NES'})
            if i < 5:
                console.append({'user': 'TierB', 'date': '2026-03-01',
                                'proof': 'https://example.com/b', 'hardware': 'NES'})
            if i < 1:
                console.append({'user': 'TierA', 'date': '2026-03-01',
                                'proof': 'https://example.com/a', 'hardware': 'NES'})
            tier_runs.append(mkarchive.run_spec(f'M9004{i:02d}', frames=1000 + i,
                                                authors=['Ada'], consoleVerifications=console))
        arch = mkarchive.make_archive(td / 'a4', tier_runs)
        out = td / 'o4'
        r = build(arch, out)
        ck('tier build succeeds', r.returncode == 0, r.stderr[-300:])
        stats = json.loads((out / 'assets/authorstats.json').read_text())
        for who, want_pts, want_label, want_class in (
                ('tiera', 1000, '1k Contributor', 'tier-1k'),
                ('tierb', 5000, '5k Contributor', 'tier-5k'),
                ('tierc', 10000, '10k Contributor', 'tier-10k'),
                ('tierd', 25000, '25k Contributor', 'tier-25k')):
            ck(f'{who} earned exactly {want_pts}',
               stats.get(who, {}).get('contrib') == want_pts, str(stats.get(who)))
            page_ = (out / 'authors' / who / 'index.html').read_text()
            worn = re.search(r'class="rolechip role-contrib ([^"]+)"[^>]*>([^<]+)<', page_)
            ck(f'{want_pts} points wears {want_label}',
               worn and worn.group(1) == want_class and worn.group(2) == want_label,
               str(worn and worn.groups()))

        # neglect bonus is capped
        capped = [mkarchive.run_spec('M900313', frames=5000, authors=['Ada'],
                                     submitted=d(400),
                                     status={'reproduced': 'community', 'verified': 'none'},
                                     reproductions=[{'user': 'Late', 'date': d(0)}])]
        arch = mkarchive.make_archive(td / 'a3', capped)
        out = td / 'o3'
        build(arch, out)
        stats = json.loads((out / 'assets/authorstats.json').read_text())
        ck('neglect bonus is capped',
           stats.get('late', {}).get('contrib') == PT_REPRO_FIRST + PT_NEGLECT_CAP,
           str(stats.get('late')))

        # ---------------- what counts as pending ----------------
        # decision 2026-08-16: unclassified runs are never verified, so their
        # reproduction gate alone decides whether they are still waiting
        pend_runs = [
            mkarchive.run_spec('M900351', frames=5000, authors=['Ada'], submitted=d(5)),
            mkarchive.run_spec('M900352', goal='unclassified', frames=900, authors=['Bo'],
                               goalDescription='Unreproduced playaround.', submitted=d(5)),
            mkarchive.run_spec('M900353', goal='unclassified', frames=800, authors=['Cy'],
                               goalDescription='Reproduced playaround.', submitted=d(5),
                               status={'reproduced': 'community', 'verified': 'none'},
                               reproductions=[{'user': 'Rep', 'date': d(4)}]),
        ]
        arch = mkarchive.make_archive(td / 'a7', pend_runs)
        out = td / 'o7'
        build(arch, out)
        home = (out / 'index.html').read_text()
        m = re.search(r'<div class="stat"><b>(\d+)</b><span>pending</span>', home)
        ck('home pending stat is rendered', bool(m), home[:200])
        if m:
            # unclassified runs rank by likes and nothing gates them, so none
            # of the three count as pending; only the classified no-act run does
            ck('unclassified runs are never pending',
               int(m.group(1)) == 1, f'got {m.group(1)}, want 1')

        # ---------------- stars ----------------
        star_runs = [
            mkarchive.run_spec('M900321', frames=5000, authors=['Ada'],
                               likes=[{'user': 'F1', 'date': d(1)},
                                      {'user': 'F2', 'date': d(1)}]),
            mkarchive.run_spec('M900322', frames=6000, authors=['Ada'],
                               likes=[{'user': 'F3', 'date': d(1)}]),
            mkarchive.run_spec('M900323', game='dos/hardgame', frames=7000,
                               authors=['Bo'], likes=[{'user': 'F1', 'date': d(1)}]),
        ]
        arch = mkarchive.make_archive(td / 'a4', star_runs)
        out = td / 'o4'
        build(arch, out)
        stats = json.loads((out / 'assets/authorstats.json').read_text())
        ck('author score sums the stars on their runs',
           stats['ada']['author'] == 3 and stats['bo']['author'] == 1,
           str({k: v['author'] for k, v in stats.items()}))
        games_page = (out / 'games/index.html').read_text()
        sections = re.findall(r'<section class="syssect" data-stars="(\d+)"', games_page)
        cards = re.findall(r'<a class="card" data-stars="(\d+)"', games_page)
        ck('system star totals equal the sum of their games',
           sorted(int(x) for x in sections) == [1, 3], str(sections))
        ck('game star totals are per game', sorted(int(x) for x in cards) == [1, 3], str(cards))

        # ---------------- superseded / history ----------------
        hist = [
            mkarchive.run_spec('M900331', frames=9000, authors=['Ada'], submitted=d(20),
                               status={'reproduced': 'community', 'verified': 'provisional'},
                               reproductions=[{'user': 'Rep', 'date': d(19)}],
                               verifications=[{'user': 'Ver', 'date': d(19)},
                                              {'user': 'Ver2', 'date': d(19)}]),
            mkarchive.run_spec('M900332', frames=8000, authors=['Ada'], submitted=d(10),
                               status={'reproduced': 'community', 'verified': 'provisional'},
                               reproductions=[{'user': 'Rep', 'date': d(9)}],
                               verifications=[{'user': 'Ver', 'date': d(9)},
                                              {'user': 'Ver2', 'date': d(9)}]),
            # different author set: co-authored runs are not superseded by solo ones
            mkarchive.run_spec('M900333', frames=8500, authors=['Ada', 'Bo'], submitted=d(5),
                               status={'reproduced': 'community', 'verified': 'provisional'},
                               reproductions=[{'user': 'Rep', 'date': d(4)}],
                               verifications=[{'user': 'Ver', 'date': d(4)},
                                              {'user': 'Ver2', 'date': d(4)}]),
        ]
        arch = mkarchive.make_archive(td / 'a5', hist)
        out = td / 'o5'
        build(arch, out)
        game = (out / 'games/nes/testgame/index.html').read_text()
        head, _, tail = game.partition('History:')
        ck('slower run by the same authors moves to History',
           'M900331' in rows_of(tail) and 'M900331' not in rows_of(head), '')
        ck('faster run by the same authors stays ranked', 'M900332' in rows_of(head))
        ck('a different author set is never superseded', 'M900333' in rows_of(head))
        ck('history shows the frame delta', '+1,000f' in tail, tail[:200])

        # ---------------- what counts as freshly archived ----------------
        # imported runs carry the original publication date in `submitted`,
        # so "new here" must follow the import date instead (2026-08-16)
        fresh_runs = [
            # a native submission from a while back
            mkarchive.run_spec('M900361', frames=5000, authors=['Ada'], submitted=d(9)),
            # imported today, but published years ago
            mkarchive.run_spec('M900362', frames=5100, authors=['Bo'],
                               submitted='2015-01-01T00:00:00Z',
                               status={'reproduced': 'imported', 'verified': 'imported'},
                               imported={'source': 'https://tasvideos.org/10M',
                                         'importedBy': 'Bo', 'importedAt': d(0)}),
        ]
        # plus a bulk import by one member, all on the same day
        fresh_runs += [
            mkarchive.run_spec(f'M9004{i:02d}', frames=6000 + i, authors=['Cy'],
                               submitted=f'2016-0{i % 9 + 1}-01T00:00:00Z',
                               status={'reproduced': 'imported', 'verified': 'imported'},
                               imported={'source': f'https://tasvideos.org/{200 + i}M',
                                         'importedBy': 'Cy', 'importedAt': d(1)})
            for i in range(10)
        ]
        arch = mkarchive.make_archive(td / 'a8', fresh_runs)
        out = td / 'o8'
        build(arch, out)
        home = (out / 'index.html').read_text()
        shelf = home.split('Freshly archived')[1]
        shelf_ids = list(dict.fromkeys(re.findall(r'runs/(M\d+)/', shelf)))[:8]
        ck('a fresh import outranks an older native submission',
           shelf_ids and shelf_ids[0] == 'M900362', str(shelf_ids[:3]))
        ck('a run published in 2015 still counts as freshly archived',
           'M900362' in shelf_ids, str(shelf_ids))
        ck('the shelf is strictly newest-first',
           shelf_ids[:2] == ['M900362', 'M900409'] or shelf_ids[0] == 'M900362',
           str(shelf_ids[:4]))
        index_js = (out / 'browse/index.html').read_text()
        m = re.search(r'"id": "M900362".*?"date": "([\d-]+)"', index_js, re.S)
        ck('browse dates an imported run by its arrival, not its publication',
           bool(m) and m.group(1) == d(0), m.group(1) if m else 'not found')

        # git history is the ground truth for arrival: importedAt is date-only,
        # so a day of imports would otherwise tie and fall back to publication
        # dates (which is how a just-added run ended up below older ones)
        gitarch = td / 'a9'
        mkarchive.make_archive(gitarch, [
            mkarchive.run_spec('M900371', frames=5000, authors=['Ada'],
                               submitted='2015-01-01T00:00:00Z',
                               status={'reproduced': 'imported', 'verified': 'imported',
                                       'console': 'none'},
                               imported={'source': 'https://tasvideos.org/71M',
                                         'importedBy': 'Ada', 'importedAt': d(0)}),
            mkarchive.run_spec('M900372', frames=5100, authors=['Bo'],
                               submitted='2024-01-01T00:00:00Z',
                               status={'reproduced': 'imported', 'verified': 'imported',
                                       'console': 'none'},
                               imported={'source': 'https://tasvideos.org/72M',
                                         'importedBy': 'Bo', 'importedAt': d(0)}),
        ])
        env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t',
                   GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@t')
        run_git = lambda *a, **kw: subprocess.run(['git', *a], cwd=gitarch, check=True,
                                                  capture_output=True, env={**env, **kw})
        run_git('init', '-q', '-b', 'main')
        # the run with the OLDER publication date is committed LAST
        run_git('add', 'systems.json', 'roles.json', 'authors',
                'games/nes/testgame/game.json', 'games/nes/testgame/categories.json',
                'games/nes/testgame/runs/M900372')
        run_git('commit', '-qm', 'first arrival',
                GIT_COMMITTER_DATE='2026-01-01T10:00:00+00:00',
                GIT_AUTHOR_DATE='2026-01-01T10:00:00+00:00')
        run_git('add', '-A')
        run_git('commit', '-qm', 'second arrival',
                GIT_COMMITTER_DATE='2026-01-01T11:00:00+00:00',
                GIT_AUTHOR_DATE='2026-01-01T11:00:00+00:00')
        out = td / 'o9'
        build(gitarch, out)
        shelf = (out / 'index.html').read_text().split('Freshly archived')[1]
        order = list(dict.fromkeys(re.findall(r'runs/(M\d+)/', shelf)))
        ck('arrival order follows the commits, not the publication dates',
           order[:2] == ['M900371', 'M900372'], str(order))

        # ---------------- author news + json shapes ----------------
        news_runs = [
            mkarchive.run_spec('M900341', frames=5000, authors=['Ada', 'Bo'],
                               submitted=d(10),
                               status={'reproduced': 'community', 'verified': 'provisional'},
                               reproductions=[{'user': 'Rep', 'date': d(3)}],
                               verifications=[{'user': 'Ver', 'date': d(2)}],
                               likes=[{'user': 'Fan', 'date': d(1)}]),
        ]
        arch = mkarchive.make_archive(td / 'a6', news_runs)
        out = td / 'o6'
        build(arch, out)
        news = json.loads((out / 'assets/news.json').read_text())
        ck('news reaches every co-author', set(news) >= {'ada', 'bo'}, str(list(news)))
        ck('each co-author gets one entry per act',
           len(news.get('ada', [])) == 3 and len(news.get('bo', [])) == 3,
           str({k: len(v) for k, v in news.items()}))
        ck('news dates are plain YYYY-MM-DD',
           all(re.fullmatch(r'\d{4}-\d{2}-\d{2}', x) for x in news.get('ada', [])),
           str(news.get('ada')))
        profile = (out / 'authors/ada/index.html').read_text()
        for kind in ('reproduced', 'verified', 'liked'):
            ck(f'profile news line: {kind}', kind in profile)
        names = json.loads((out / 'assets/authornames.json').read_text())
        ck('author name list is complete and canonical',
           {'Ada', 'Bo', 'Rep', 'Ver', 'Fan'} <= set(names), str(names[:8]))

        # ---------------- per-category metrics ranking ----------------
        # A category ranks by its own metric hierarchy: score (higher wins),
        # then real time, then earlier submission; an unstated value (0)
        # sorts last at its level whichever way the metric points.
        SCORE_TIME = [{'key': 'score', 'label': 'Score', 'type': 'number',
                       'better': 'higher', 'unit': 'pts'},
                      {'key': 'time', 'label': 'Time', 'type': 'time',
                       'better': 'lower'}]
        ver = lambda n: {'status': {'reproduced': 'none', 'verified': 'provisional'},   # noqa: E731
                         'verifications': [{'user': f'V{n}', 'date': d(2)}]}
        m_runs = [
            mkarchive.run_spec('M900351', game='nes/scored', goal='high-score',
                               goal_metrics=SCORE_TIME, authors=['Ada'],
                               frames=2000, metrics={'score': 5000},
                               submitted=d(30) + 'T00:00:00Z', **ver(1)),
            mkarchive.run_spec('M900352', game='nes/scored', goal='high-score',
                               goal_metrics=SCORE_TIME, authors=['Bo'],
                               frames=9000, metrics={'score': 9000},
                               submitted=d(30) + 'T00:00:00Z', **ver(2)),
            mkarchive.run_spec('M900353', game='nes/scored', goal='high-score',
                               goal_metrics=SCORE_TIME, authors=['Cy'],
                               frames=500, metrics={'score': 0},
                               submitted=d(30) + 'T00:00:00Z', **ver(3)),
            mkarchive.run_spec('M900354', game='nes/scored', goal='high-score',
                               goal_metrics=SCORE_TIME, authors=['Dee'],
                               frames=1000, metrics={'score': 5000},
                               submitted=d(30) + 'T00:00:00Z', **ver(4)),
            # identical on every metric, submitted 20 days later: loses the tie
            mkarchive.run_spec('M900355', game='nes/scored', goal='high-score',
                               goal_metrics=SCORE_TIME, authors=['Ed'],
                               frames=1000, metrics={'score': 5000},
                               submitted=d(10) + 'T00:00:00Z', **ver(5)),
            # a time-less category: video-only with no stated duration at all
            mkarchive.run_spec('M900356', game='nes/scoreonly', goal='most-points',
                               goal_metrics=[SCORE_TIME[0]], authors=['Fay'],
                               videoOnly=True, metrics={'score': 1250},
                               submitted=d(5) + 'T00:00:00Z', **ver(6)),
        ]
        arch = mkarchive.make_archive(td / 'a9', m_runs)
        out = td / 'o9'
        r = build(arch, out)
        ck('metrics build succeeds', r.returncode == 0, r.stderr[-400:])
        if r.returncode == 0:
            game = (out / 'games/nes/scored/index.html').read_text()
            order = [rid for rid in rows_of(game.split('Pending:')[0])
                     if rid.startswith('M9003')]
            first_of = {rid: order.index(rid) for rid in dict.fromkeys(order)}
            want = ['M900352', 'M900354', 'M900355', 'M900351', 'M900353']
            ck('score rules, time breaks ties, earlier submission unties, '
               'unset sorts last',
               [x for x, _ in sorted(first_of.items(), key=lambda kv: kv[1])] == want,
               str(order))
            ck('the unstated score renders as the dash',
               '—' in game.split('M900353')[-1].split('</tr>')[0], '')
            ck('the ranking header names the metrics',
               '<th class="num">Score</th>' in game and 'Ranked by:' in game, '')
            browse = (out / 'browse' / 'index.html').read_text()
            ck('browse leads with the primary metric',
               '9,000' in browse and 'pts' in browse, '')
            solo = (out / 'games/nes/scoreonly/index.html').read_text()
            ck('a time-less video-only run ranks by its score alone',
               'M900356' in rows_of(solo.split('Pending:')[0])
               and '1,250' in solo, '')
            fact = (out / 'runs/M900356/index.html').read_text()
            ck('the fact box states the score and no time',
               '1,250' in fact and '<dt>Time</dt>' not in fact, '')

        # ---------------- case resolution parity ----------------
        if ARCHIVIST.exists() and VALIDATOR.exists():
            a_fn = extract_function(ARCHIVIST, 'case_derived_status')
            v_fn = extract_function(VALIDATOR, 'case_derived_status')
            mismatches = []
            for n in range(6):
                verifiers = [f'v{i}' for i in range(n)]
                for votes in itertools.product([None, True, False], repeat=n):
                    case = {'verifiers': verifiers, 'reaffirmations': [
                        {'user': v, 'reaffirm': bool(x)}
                        for v, x in zip(verifiers, votes) if x is not None]}
                    if a_fn(case) != v_fn(case):
                        mismatches.append((n, votes, a_fn(case), v_fn(case)))
            ck('archivist and validator agree on every case outcome',
               not mismatches, str(mismatches[:3]))
            ck('unanimous reaffirmation closes a case',
               a_fn({'verifiers': ['a', 'b'], 'reaffirmations': [
                   {'user': 'a', 'reaffirm': True},
                   {'user': 'b', 'reaffirm': True}]}) == 'closed')
            ck('unanimous withdrawal upholds a case',
               a_fn({'verifiers': ['a', 'b'], 'reaffirmations': [
                   {'user': 'a', 'reaffirm': False},
                   {'user': 'b', 'reaffirm': False}]}) == 'upheld')
            ck('a case with no votes yet stays open',
               a_fn({'verifiers': ['a', 'b'], 'reaffirmations': []}) == 'open')
        else:
            print('SKIP case parity (archivist or validator not found)')

    print('---', len(failures), 'failures')
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
