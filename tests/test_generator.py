#!/usr/bin/env python3
"""Generator rendering tests: build a synthetic archive covering every state
(imported, pending, provisional, full, disputed, invalidated) and assert the
generated pages show what they must.

Usage: tests/test_generator.py [real_archive_dir]
The synthetic archive is layered on top of a copy of the real archive
(default ~/ToolAssisted-archive) so imported pages are exercised too.
"""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mkarchive  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
ARCHIVE = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else pathlib.Path.home() / 'ToolAssisted-archive')
PNG = b'\x89PNG\r\n\x1a\n' + b'\0' * 50

failures = []
def ck(name, cond):
    print(('PASS ' if cond else 'FAIL ') + name)
    if not cond:
        failures.append(name)


def make_run(tmp, rid, frames, status, extra):
    rd = tmp / 'games/nes/pinball/runs' / rid
    rd.mkdir(parents=True)
    (rd / f'{rid}.bk2').write_bytes(b'test')
    (rd / 'thumb.png').write_bytes(PNG)
    status = dict(status)
    status.setdefault('console', 'none')
    run = {'id': rid, 'game': 'nes/pinball', 'category': {'goal': '100k-glitched'},
           'authors': [{'user': 'TestAuthor'}],
           'movie': {'file': f'{rid}.bk2', 'format': 'bk2', 'frames': frames,
                     'rerecords': 10, 'start': 'power-on'},
           'thumbnail': 'thumb.png',
           'contract': {'emulator': 'BizHawk 2.11'},
           'status': status,
           'encodes': [{'kind': 'youtube', 'url': 'https://www.youtube.com/watch?v=abc123DEF45'}],
           'submitted': '2026-08-01T10:00:00Z', 'submittedBy': 'TestAuthor'}
    run.update(extra)
    (rd / 'run.json').write_text(json.dumps(run, indent=1))
    return rd


def main():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td) / 'archive'
        out = pathlib.Path(td) / 'out'
        shutil.copytree(ARCHIVE, tmp, ignore=shutil.ignore_patterns('.git'))
        mkarchive.lighten(tmp)
        # authors/ is the member list: everyone who acts on a run is a member,
        # and a copy of an archive predating that rule carries records that no
        # longer belong to anybody
        for af in (tmp / 'authors').glob('*.json'):
            if not json.loads(af.read_text()).get('claimed'):
                af.unlink()
        mkarchive.prune_superseded(tmp)
        for member in ('TestAuthor', 'helper', 'watcher', 'second', 'skeptic', 'fan'):
            (tmp / 'authors' / f'{member.lower()}.json').write_text(
                json.dumps({'username': member, 'claimed': True}, indent=1) + '\n')

        make_run(tmp, 'M900001', 12345, {'reproduced': 'none', 'verified': 'none'}, {})
        # a member who claimed a name: the run is credited to the name they
        # registered under, whose record the approval deleted
        (tmp / 'authors' / 'newstar.json').write_text(json.dumps(
            {'username': 'NewStar', 'claimed': True, 'claimedBy': 'OldTimer',
             'claimedAt': '2026-08-19', 'claimMethod': 'committee',
             'attestedBy': 'Root',
             'attestation': 'fixture: OldTimer claimed the name NewStar'},
            indent=1) + '\n')
        rd4 = make_run(tmp, 'M900004', 4444, {'reproduced': 'none', 'verified': 'none'},
                       {'authors': [{'user': 'OldTimer'}], 'submittedBy': 'OldTimer',
                        'completed': '2021-10-26'})
        make_run(tmp, 'M900005', 1317419, {'reproduced': 'none', 'verified': 'none'},
                 {'movie': {'file': 'M900005.bk2', 'format': 'bk2', 'frames': 1317419,
                            'fps': 1000.0, 'rerecords': 1, 'start': 'power-on'}})
        (rd4 / 'notes.md').write_text(
            'See [=GameResources/NES/Pinball|the resource page] and [=6243M], '
            'plus [UserFiles/Info/639132572975439962] and '
            '[Forum/Posts/482332|a post], and [10255S|GTA2 movies]. '
            '[not a link] stays prose.\n')
        rd = make_run(tmp, 'M900002', 11000,
                      {'reproduced': 'community', 'verified': 'provisional'},
                      {'reproductions': [{'user': 'helper', 'date': '2026-08-10',
                                          'screenshot': 'reproductions/1-helper.png',
                                          'emulator': 'BizHawk 2.11', 'notes': 'Synced.'}],
                       'verifications': [{'user': 'watcher', 'date': '2026-08-11'}]})
        (rd / 'reproductions').mkdir()
        (rd / 'reproductions/1-helper.png').write_bytes(PNG)
        rd = make_run(tmp, 'M900003', 10000,
                      {'reproduced': 'community', 'verified': 'provisional'},
                      {'reproductions': [{'user': 'helper', 'date': '2026-08-10',
                                          'screenshot': 'reproductions/1-helper.png'}],
                       'verifications': [{'user': 'watcher', 'date': '2026-08-11'},
                                         {'user': 'second', 'date': '2026-08-12'}],
                       'cases': [{'id': 1, 'openedBy': 'skeptic', 'date': '2026-08-13',
                                  'reason': 'Render test open case.',
                                  'verifiers': ['watcher', 'second'],
                                  'reaffirmations': [], 'status': 'open'}]})
        (rd / 'reproductions').mkdir()
        (rd / 'reproductions/1-helper.png').write_bytes(PNG)

        r = subprocess.run([sys.executable, str(REPO / 'generator/build.py'), str(tmp), str(out)],
                           capture_output=True, text=True)
        ck('validate accepts synthetic archive', subprocess.run(
            [sys.executable, str(tmp / 'validate.py')], capture_output=True).returncode == 0)
        ck('build succeeds', r.returncode == 0)
        if r.returncode:
            print(r.stderr[-2000:])
            sys.exit(1)

        rd = lambda p: (out / p).read_text()
        home = rd('index.html')
        ren = rd('runs/M900004/index.html')
        ck('wiki-relative links resolve to the site the notes were written on',
           'href="https://tasvideos.org/GameResources/NES/Pinball">the resource page</a>' in ren
           and 'href="https://tasvideos.org/6243M"' in ren and '[=' not in ren)
        ck('bare wiki paths resolve too, and bracketed prose stays prose',
           'href="https://tasvideos.org/UserFiles/Info/639132572975439962"' in ren
           and 'href="https://tasvideos.org/Forum/Posts/482332">a post</a>' in ren
           and 'href="https://tasvideos.org/10255S">GTA2 movies</a>' in ren
           and '[not a link]' in ren)
        ck('a credit under a former name links to the member it became',
           'authors/newstar/"' in ren and '>OldTimer</a>' in ren)
        ck('the members list has no row for the superseded name',
           'OldTimer' not in rd('authors/index.html'))
        ck('the member page carries the run credited to the former name',
           'M900004' in rd('authors/newstar/index.html'))
        ck('a stated completion date shows beside the submission date',
           '<dt>Completed</dt><dd>2021-10-26</dd>' in ren)
        ck('the board leads with the completion date when one is stated',
           'title="completion date">2021-10-26' in rd('games/nes/pinball/index.html'))
        # 1317419 frames at 1000/s is 22 minutes, not 6 hours
        ck('a movie with its own frame rate runs on its own clock',
           '21:57.419' in rd('runs/M900005/index.html'))
        stats = json.loads(rd('assets/authorstats.json'))
        ck('stats count the former-name run for the member',
           stats.get('newstar', {}).get('runs') == 1)
        ck('home hero', 'beyond human limits' in home)
        ck('home stats strip', 'statstrip' in home)
        ck('nav auth probe', 'navauth' in home)
        browse = rd('browse/index.html')
        ck('browse states', all(f'"state": "{s}"' in browse
                                for s in ('imported', 'pending', 'provisional')))
        r1 = rd('runs/M900001/index.html')
        ck('pending run bounty CTA', 'contributor points' in r1)
        ck('home cards use thumbnails the site serves itself', 'src="/thumbs/' in home)
        r2 = rd('runs/M900002/index.html')
        ck('roster + screenshot', '/shots/M900002-reproductions-1-helper.png' in r2)
        ck('community-verified status line says verified, plainly',
           'Verified: ranked' in r2
           and '(provisional)' not in r2 and '(expert)' not in r2)
        r3 = rd('runs/M900003/index.html')
        ck('dispute banner', 'under dispute' in r3)
        leg = rd('runs/M7229/index.html')
        ck('imported panel provenance', 'Creative Commons Attribution 2.0' in leg)
        ck('imported panel credits the author who brought it over',
           'voluntary import by one of its authors' in leg)
        ck('imported panel puts the responsibility on whoever picked it',
           'responsibility for importing a collaborative work' in leg)
        ck('imported panel names the source only by its link',
           'Imported from TASVideos' not in leg and 'TASVideos staff' not in leg
           and 'trusted site' in leg)
        game = rd('games/nes/pinball/index.html')
        ck('category selector', 'dimsel' in game)
        ck('pending table', 'Pending' in game)
        contrib = rd('contribute/index.html')
        ck('needs-reproduction row', 'M900001' in contrib)
        ck('open-cases box', 'Open cases' in contrib)
        ck('moderation log page exists', (out / 'policy/moderation-log/index.html').exists())
        ck('act zone on community run', 'actzone' in r2 and 'f-repro' in r2)
        ck('edit form exposes encode link editor', 'name="encode"' in r2 and 'Encode link' in r2)
        ck('imported act zone: edit+expert only', 'actzone' in leg and 'f-edit' in leg
           and 'f-expertnote' in leg and 'f-repro' not in leg)
        ck('vote form present with open case', 'f-vote' in r3)
        ck('submit page generated', 'submitform' in rd('submit/index.html'))
        claimp = rd('claim/index.html')
        ck('claim page says who answers a claim and points at the log',
           'Steering Committee answers' in claimp and 'site-log/#claims' in claimp)
        # the disclaimer has to say what they actually see, not merely that
        # somebody sees something
        ck('claim page warns that the Committee sees a masked address',
           'jo***oe@e****.com' in claimp and 'obfuscated' in claimp)
        ck('claim page no longer hands out a token',
           'one-time code' not in claimp and 'claim-start' not in claimp)
        ck('console verification section on a community run',
           'Console verifications' in r2 and 'f-console' in r2)
        ck('console verification is absent from imported runs',
           'f-console' not in leg)
        ck('app.js emitted', (out / 'assets/app.js').exists())

    print('---', len(failures), 'failures')
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
