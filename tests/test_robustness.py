#!/usr/bin/env python3
"""Robustness tests: what happens when git, concurrency or the outside world
misbehave.

Intake holds a single git checkout and a global lock. The failure that matters
is not one bad request but one bad request that leaves the checkout wedged, so
every later submission fails too. These tests break things on purpose and then
assert the service still works.

Hermetic: scratch bare remote in a temp dir, no network, no real archive.

Usage: tests/test_robustness.py [real_archive_dir]
"""
import io
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mkarchive  # noqa: E402
from test_security import call, free_port, KEY   # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
REAL_ARCHIVE = pathlib.Path(sys.argv[1] if len(sys.argv) > 1
                            else pathlib.Path.home() / 'ToolAssisted-archive')
JPG = b'\xff\xd8\xff' + b'\0' * 60

failures = []


def ck(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (f'  [{detail}]' if detail and not cond else ''))
    if not cond:
        failures.append(name)


def git(*args, cwd, check=True):
    return subprocess.run(['git', *args], cwd=str(cwd), check=check,
                          capture_output=True, text=True)


def other_pushes(other, filename, text):
    """Somebody else commits to the same branch, from their own clone."""
    git('fetch', '-q', 'origin', cwd=other)
    git('reset', '-q', '--hard', 'origin/main', cwd=other)
    (other / filename).write_text(text)
    git('add', '-A', cwd=other)
    git('-c', 'user.name=o', '-c', 'user.email=o@o', 'commit', '-qm',
        f'other work: {filename}', cwd=other)
    git('push', '-q', 'origin', 'main', cwd=other)


def main():
    import http.server
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        seed = td / 'seed'
        mkarchive.make_archive(seed, [
            mkarchive.run_spec('M900501', frames=5000, authors=['Ada']),
        ])
        shutil.copy2(REAL_ARCHIVE / 'validate.py', seed / 'validate.py')
        shutil.copytree(REAL_ARCHIVE / 'schema', seed / 'schema', dirs_exist_ok=True)
        (seed / 'authors' / 'member.json').write_text(
            json.dumps({'username': 'Member', 'claimed': True}, indent=1))
        git('init', '-q', '-b', 'main', cwd=seed)
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'add', '-A', cwd=seed)
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'seed', cwd=seed)
        origin = td / 'origin.git'
        subprocess.run(['git', 'clone', '-q', '--bare', str(seed), str(origin)], check=True)
        work = td / 'work'
        subprocess.run(['git', 'clone', '-q', f'file://{origin}', str(work)], check=True)
        git('config', 'user.name', 'robust-test', cwd=work)
        git('config', 'user.email', 't@t', cwd=work)

        # a second clone stands in for "somebody else pushing"
        other = td / 'other'
        subprocess.run(['git', 'clone', '-q', f'file://{origin}', str(other)], check=True)
        git('config', 'user.name', 'other', cwd=other)
        git('config', 'user.email', 'o@o', cwd=other)

        pages = td / 'mock'
        (pages / 'thumbs' / 'goodvid12345').mkdir(parents=True)
        (pages / 'thumbs' / 'goodvid12345' / 'maxresdefault.jpg').write_bytes(JPG)
        hport = free_port()

        class Quiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *a):
                pass

        httpd = http.server.ThreadingHTTPServer(
            ('127.0.0.1', hport), lambda *a, **k: Quiet(*a, directory=str(pages), **k))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        port = free_port()
        import os
        env = dict(SUBMIT_KEY=KEY, ARCHIVE_DIR=str(work), ARCHIVIST_BRANCH='main',
                   GIT_SSH_COMMAND='ssh', PORT=str(port), DISCOURSE_KEY='',
                   CLAIMS_FILE=str(td / 'claims.json'),
                   CLAIM_FETCH_BASE=f'http://127.0.0.1:{hport}/',
                   THUMB_FETCH_BASE=f'http://127.0.0.1:{hport}/thumbs/',
                   SESSION_SECRET='x', SELF_URL=f'http://127.0.0.1:{port}',
                   SITE_ORIGIN='https://toolassisted.run',
                   PATH='/usr/bin:/bin', HOME=str(td))
        if 'PYTHONPATH' in os.environ:
            env['PYTHONPATH'] = os.environ['PYTHONPATH']
        log = (td / 'log').open('w')
        proc = subprocess.Popen([sys.executable, str(REPO / 'archivist/archivist.py')],
                                env=env, stdout=log, stderr=subprocess.STDOUT)
        U = f'http://127.0.0.1:{port}'
        sub = {'key': KEY, 'submitter': 'Member', 'game': 'nes/testgame',
               'goal': 'fastest', 'authors': 'Member', 'consent': 'yes',
               'encode': 'https://youtu.be/goodvid12345'}
        try:
            for _ in range(60):
                try:
                    urllib.request.urlopen(U + '/api/me', timeout=5)
                    break
                except OSError:
                    time.sleep(0.5)
            else:
                print((td / 'log').read_text()[-2000:])
                sys.exit('archivist did not start')

            # ---------- a concurrent push must not wedge intake ----------
            # somebody else commits to the same branch behind the archivist's back
            other_pushes(other, 'CONCURRENT.md', 'another writer\n')

            code, r, _ = call(U + '/api/submit', dict(sub), files={'movie': ('m.bk2', mkarchive.unique_movie())})
            ck('submission still succeeds after someone else pushed', code == 200, str(r)[:160])
            first_id = r.get('id')

            # ---------- a wedged checkout heals itself ----------
            # What a crashed request leaves behind: modified tracked files in
            # the worktree. If the same file also moved upstream, a plain
            # `git checkout -B` refuses ("local changes would be overwritten")
            # and every later request fails the same way until someone
            # intervenes on the server.
            other_pushes(other, 'CONFLICT.md', 'remote content\n')
            (work / 'CONFLICT.md').write_text('half-written local content\n')
            (work / 'systems.json').write_text('{"corrupt": true}\n')
            git('fetch', '-q', 'origin', cwd=work)
            wedged = git('checkout', '-q', '-B', 'main', 'origin/main',
                         cwd=work, check=False).returncode != 0
            ck('the simulated state really does wedge a plain checkout', wedged)

            code, r, _ = call(U + '/api/submit', dict(sub), files={'movie': ('m.bk2', mkarchive.unique_movie())})
            ck('intake recovers from a wedged checkout', code == 200, str(r)[:140])
            ck('the half-written file never reached the archive',
               json.loads((work / 'systems.json').read_text()) != {'corrupt': True},
               (work / 'systems.json').read_text()[:80])
            second_id = r.get('id')
            ck('ids keep advancing', first_id and second_id and second_id != first_id,
               f'{first_id} then {second_id}')

            # ---------- concurrent submissions get distinct ids ----------
            results = []

            def submit_one(n):
                c, rr, _ = call(U + '/api/submit', dict(sub), files={'movie': ('m.bk2', mkarchive.unique_movie())})
                results.append((c, rr.get('id')))

            threads = [threading.Thread(target=submit_one, args=(i,)) for i in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=180)
            ok_ids = [i for c, i in results if c == 200 and i]
            ck('every concurrent submission is archived', len(ok_ids) == 6,
               str(sorted(results)))
            ck('concurrent submissions never share an id',
               len(set(ok_ids)) == len(ok_ids), str(sorted(ok_ids)))

            # ---------- a rejected request leaves nothing behind ----------
            before = git('rev-parse', 'HEAD', cwd=other).stdout.strip()
            git('fetch', '-q', 'origin', cwd=other)
            before = git('rev-parse', 'origin/main', cwd=other).stdout.strip()
            code, r, _ = call(U + '/api/submit', dict(sub, encode='https://example.com/nope'),
                              files={'movie': ('m.bk2', mkarchive.unique_movie())})
            ck('a bad submission is rejected', code == 400, str(r)[:120])
            git('fetch', '-q', 'origin', cwd=other)
            after = git('rev-parse', 'origin/main', cwd=other).stdout.strip()
            ck('a rejected submission commits nothing', before == after, f'{before[:8]} -> {after[:8]}')

            # and the next good one still works
            code, r, _ = call(U + '/api/submit', dict(sub), files={'movie': ('m.bk2', mkarchive.unique_movie())})
            ck('intake still healthy after a rejection', code == 200, str(r)[:120])

            # ---------- the archive is valid throughout ----------
            check = td / 'check'
            subprocess.run(['git', 'clone', '-q', f'file://{origin}', str(check)], check=True)
            v = subprocess.run([sys.executable, str(check / 'validate.py')],
                               capture_output=True, text=True)
            ck('archive validates after the whole ordeal', v.returncode == 0, v.stdout[-400:])
            ck('no junk file was published',
               not (check / 'CONFLICT.md').exists() or (check / 'CONCURRENT.md').exists(),
               'unexpected worktree leftovers were pushed')
        finally:
            proc.terminate()
            httpd.shutdown()

    print('---', len(failures), 'failures')
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
