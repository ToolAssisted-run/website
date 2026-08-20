#!/usr/bin/env python3
"""Archivist security and branch-coverage tests.

test_archivist.py walks the happy paths of the community loop. This suite goes
after the ways the service could be made to misbehave: cross-site writes,
forged or expired sessions, tampered SSO responses, names that try to shape
filesystem paths, files that lie about what they are, bodies that are too big,
and the expert-scope rules that decide who may act on what.

Everything runs against a scratch bare git remote and a local mock of the
outside world: no real archive is touched, nothing is pushed anywhere.

Usage: tests/test_security.py
"""
import base64
import hashlib
import hmac
import http.server
import io
import json
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mkarchive  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
REAL_ARCHIVE = pathlib.Path(sys.argv[1] if len(sys.argv) > 1
                            else pathlib.Path.home() / 'ToolAssisted-archive')
KEY = 'testkey'
SSO_SECRET = 'testssosecret'
SESSION_SECRET = 'testsessionsecret'
SITE_ORIGIN = 'https://toolassisted.run'
FORUM_ORIGIN = None   # set to the local mock once its port is known
PNG = mkarchive.PNG
JPG = b'\xff\xd8\xff' + b'\0' * 60

failures = []


def ck(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (f'  [{detail}]' if detail and not cond else ''))
    if not cond:
        failures.append(name)


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def bk2():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('Header.txt', 'Platform NES\nrerecordCount 7\n')
        z.writestr('Input Log.txt', 'LogKey:#Reset|Power|\n' + '|..|........|\n' * 60)
    return buf.getvalue()


def call(url, data=None, files=None, cookie=None, origin=None, method='POST'):
    boundary = 'secboundary42'
    body = b''
    for k, v in (data or {}).items():
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"'
                 f'\r\n\r\n{v}\r\n').encode()
    for k, (fn, content) in (files or {}).items():
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; '
                 f'filename="{fn}"\r\nContent-Type: application/octet-stream'
                 f'\r\n\r\n').encode() + content + b'\r\n'
    body += f'--{boundary}--\r\n'.encode()
    req = urllib.request.Request(url, body if (data or files) else None, method=method,
                                 headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
    if cookie:
        req.add_header('Cookie', cookie)
    if origin:
        req.add_header('Origin', origin)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b'{}'), dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw), dict(e.headers)
        except json.JSONDecodeError:
            return e.code, {'raw': raw[:120].decode(errors='replace')}, dict(e.headers)
    except Exception as e:                                   # noqa: BLE001
        return 0, {'error': f'{e.__class__.__name__}: {e}'}, {}


def session_cookie(username, ttl=14 * 24 * 3600, sig_ok=True, exp='auto', fields=3):
    exp_v = int(time.time()) + ttl if exp == 'auto' else exp
    parts = [username, '7', str(exp_v)][:fields]
    body = '|'.join(parts)
    sig = hmac.new(SESSION_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not sig_ok:
        sig = ('0' if sig[0] != '0' else '1') + sig[1:]
    return f'tar_session={body}|{sig}'


def main():
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)

        # ---------- scratch archive + bare remote ----------
        seed = td / 'seed'
        mkarchive.make_archive(seed, [
            mkarchive.run_spec('M900401', frames=5000, authors=['Ada']),
            mkarchive.run_spec('M900402', game='dos/hardgame', frames=6000, authors=['Bo']),
        ], experts=[{'user': 'SiteExpert', 'scope': 'site'},
                    {'user': 'NesExpert', 'scope': 'nes'},
                    {'user': 'GroupExpert', 'scope': 'group:fam'}],
            groups=[{'key': 'fam', 'title': 'Fam', 'games': ['nes/testgame']}])
        # ratify only means something on a provisional game
        for gj in seed.glob('games/*/*/game.json'):
            g = json.loads(gj.read_text())
            g['established'] = False
            gj.write_text(json.dumps(g, indent=1) + '\n')
        shutil.copy2(REAL_ARCHIVE / 'validate.py', seed / 'validate.py')
        shutil.copytree(REAL_ARCHIVE / 'schema', seed / 'schema', dirs_exist_ok=True)
        for u in ('Member', 'Rep', 'SiteExpert', 'NesExpert'):
            (seed / 'authors' / f'{u.lower()}.json').write_text(
                json.dumps({'username': u, 'claimed': True}, indent=1))
        for cmd in (['git', 'init', '-q', '-b', 'main'],
                    ['git', '-c', 'user.name=t', '-c', 'user.email=t@t', 'add', '-A'],
                    ['git', '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'seed']):
            subprocess.run(cmd, cwd=seed, check=True)
        origin = td / 'origin.git'
        subprocess.run(['git', 'clone', '-q', '--bare', str(seed), str(origin)], check=True)
        work = td / 'work'
        subprocess.run(['git', 'clone', '-q', '--no-hardlinks', str(origin), str(work)], check=True)
        subprocess.run(['git', 'config', 'user.name', 'sec-test'], cwd=work, check=True)
        subprocess.run(['git', 'config', 'user.email', 't@t'], cwd=work, check=True)

        # ---------- mock outside world ----------
        pages = td / 'mock'
        (pages / 'thumbs' / 'goodvid12345').mkdir(parents=True)
        (pages / 'thumbs' / 'goodvid12345' / 'maxresdefault.jpg').write_bytes(JPG)
        (pages / 'thumbs' / 'hqonly12345').mkdir(parents=True)
        (pages / 'thumbs' / 'hqonly12345' / 'hqdefault.jpg').write_bytes(JPG)
        (pages / 'thumbs' / 'htmlonly1234').mkdir(parents=True)
        for v in ('maxresdefault.jpg', 'hqdefault.jpg'):
            (pages / 'thumbs' / 'htmlonly1234' / v).write_bytes(b'<html>404</html>')
        hport = free_port()
        global FORUM_ORIGIN
        FORUM_ORIGIN = f'http://127.0.0.1:{hport}'
        class QuietHandler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *a):          # keep CI output readable
                pass

        httpd = http.server.ThreadingHTTPServer(
            ('127.0.0.1', hport),
            lambda *a, **k: QuietHandler(*a, directory=str(pages), **k))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        # ---------- launch the archivist ----------
        port = free_port()
        env = dict(SUBMIT_KEY=KEY, ARCHIVE_DIR=str(work), ARCHIVIST_BRANCH='main',
                   GIT_SSH_COMMAND='ssh', PORT=str(port), DISCOURSE_KEY='',
                   CLAIM_FETCH_BASE=f'http://127.0.0.1:{hport}/',
                   THUMB_FETCH_BASE=f'http://127.0.0.1:{hport}/thumbs/',
                   DISCOURSE_CONNECT_SECRET=SSO_SECRET, SESSION_SECRET=SESSION_SECRET,
                   SELF_URL=f'http://127.0.0.1:{port}', SITE_ORIGIN=SITE_ORIGIN,
                   DISCOURSE_URL=FORUM_ORIGIN,
                   PATH='/usr/bin:/bin', HOME=str(td))
        import os
        if 'PYTHONPATH' in os.environ:
            env['PYTHONPATH'] = os.environ['PYTHONPATH']
        log = (td / 'log').open('w')
        proc = subprocess.Popen([sys.executable, str(REPO / 'archivist/archivist.py')],
                                env=env, stdout=log, stderr=subprocess.STDOUT)
        U = f'http://127.0.0.1:{port}'
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

            cookie = session_cookie('Member')

            # ---------------- CSRF matrix ----------------
            # Cookie-authenticated writes must be refused from foreign origins,
            # allowed from ours, and allowed with no Origin at all (scripted
            # clients carry no ambient cookie authority).
            endpoints = [
                ('/api/reproduce', {'run': 'M900401', 'dry_run': '1'}),
                ('/api/verify', {'run': 'M900401', 'dry_run': '1'}),
                ('/api/like', {'run': 'M900401', 'dry_run': '1'}),
                ('/api/edit', {'run': 'M900401', 'notes': 'x', 'dry_run': '1'}),
                ('/api/note', {'run': 'M900401', 'role': 'reproducer', 'text': 'x', 'dry_run': '1'}),
                ('/api/report', {'run': 'M900401', 'kind': 'other', 'details': 'x', 'dry_run': '1'}),
                ('/api/case/open', {'run': 'M900401', 'reason': 'x', 'dry_run': '1'}),
                ('/api/case/vote', {'run': 'M900401', 'case': '1', 'reaffirm': '1', 'dry_run': '1'}),
                ('/api/expert/appoint', {'user': 'someone', 'scope': 'nes/testgame',
                                         'reason': 'a stated reason', 'dry_run': '1'}),
                ('/api/expert/resign', {'scope': 'nes/testgame', 'dry_run': '1'}),
                ('/api/claim/attest', {'member': 'someone', 'identity': 'Someone',
                                       'method': 'a stated method, long enough',
                                       'dry_run': '1'}),
                ('/api/invalidate', {'run': 'M900401', 'kind': 'verification',
                                     'user': 'x', 'reason': 'y', 'dry_run': '1'}),
                ('/api/game/ratify', {'game': 'nes/testgame', 'dry_run': '1'}),
                ('/api/import/scan', {}),
                ('/api/import/run', {}),
                ('/api/discussion/reply', {'topic': '1', 'body': 'a reply here'}),
            ]
            evil = []
            for path, data in endpoints:
                code, r, _ = call(U + path, data, cookie=cookie, origin='https://evil.example')
                if code != 403 or 'cross-origin' not in str(r.get('error', '')):
                    evil.append(f'{path}: {code} {r.get("error")}')
            ck('every cookie-authed write refuses a foreign origin', not evil, str(evil[:4]))

            allowed = []
            for path, data in endpoints:
                for ok_origin in (SITE_ORIGIN, FORUM_ORIGIN, None):
                    code, r, _ = call(U + path, data, cookie=cookie, origin=ok_origin)
                    if code == 403 and 'cross-origin' in str(r.get('error', '')):
                        allowed.append(f'{path} origin={ok_origin}')
            ck('our own origins and origin-less clients are accepted',
               not allowed, str(allowed[:4]))

            # the shared key must never be able to speak as somebody else
            code, r, _ = call(U + '/api/discussion/reply',
                              {'key': KEY, 'user': 'Member', 'topic': '1',
                               'body': 'posting as someone else'})
            ck('the submitter key cannot post to the forum', code == 403, str(r)[:120])

            # ---------------- session forgery ----------------
            bad_sessions = {
                'tampered signature': session_cookie('Member', sig_ok=False),
                'expired': session_cookie('Member', exp=int(time.time()) - 10),
                'non-numeric expiry': session_cookie('Member', exp='soon'),
                'truncated token': session_cookie('Member', fields=2),
            }
            leaks = []
            for label, c in bad_sessions.items():
                code, r, _ = call(U + '/api/me', method='GET', cookie=c)
                if r.get('loggedIn'):
                    leaks.append(label)
                code, r, _ = call(U + '/api/like', {'run': 'M900401', 'dry_run': '1'}, cookie=c)
                if code == 200:
                    leaks.append(f'{label} (write accepted)')
            ck('forged and expired sessions are anonymous', not leaks, str(leaks))

            # ---------------- cookie + CORS headers ----------------
            code, _, h = call(U + '/api/me', method='GET', cookie=cookie)
            ck('CORS allows only our origin',
               h.get('Access-Control-Allow-Origin') == SITE_ORIGIN, str(h.get('Access-Control-Allow-Origin')))
            ck('CORS allows credentials', h.get('Access-Control-Allow-Credentials') == 'true')

            # ---------------- SSO negatives ----------------
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *a, **k):
                    return None

            def sso_login():
                op = urllib.request.build_opener(NoRedirect)
                try:
                    r = op.open(U + '/login', timeout=15)
                    return r.headers.get('Location', '')
                except urllib.error.HTTPError as e:
                    return e.headers.get('Location', '')

            def fresh_nonce():
                loc = sso_login()
                q = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
                sso = q.get('sso', [''])[0]
                if not sso:
                    return ''
                return urllib.parse.parse_qs(base64.b64decode(sso).decode()).get('nonce', [''])[0]
            nonce = fresh_nonce()
            ck('login redirect carries a signed nonce', bool(nonce))

            def callback(payload, sig=None):
                b64 = base64.b64encode(urllib.parse.urlencode(payload).encode()).decode()
                s = sig or hmac.new(SSO_SECRET.encode(), b64.encode(), hashlib.sha256).hexdigest()
                url = U + '/login/callback?' + urllib.parse.urlencode({'sso': b64, 'sig': s})
                try:
                    with urllib.request.urlopen(url, timeout=15) as r:
                        return r.status, dict(r.headers)
                except urllib.error.HTTPError as e:
                    return e.code, dict(e.headers)

            code, _ = callback({'nonce': fresh_nonce(), 'username': 'x'}, sig='deadbeef' * 8)
            ck('SSO rejects a bad signature', code == 403, str(code))
            code, _ = callback({'nonce': 'not-a-real-nonce', 'username': 'x'})
            ck('SSO rejects an unknown nonce', code == 403, str(code))
            code, _ = callback({'nonce': fresh_nonce()})
            ck('SSO rejects a response without a username', code == 502, str(code))

            # ---------------- path traversal ----------------
            traversal = session_cookie('../../../../etc/passwd')
            code, r, _ = call(U + '/api/reproduce', {'run': 'M900401'},
                              files={'screenshot': ('s.png', PNG)}, cookie=traversal)
            ck('a path-shaped session username cannot act', code >= 400, f'{code} {r}')
            longname = session_cookie('a' * 200)
            code, r, _ = call(U + '/api/like', {'run': 'M900401'}, cookie=longname)
            ck('an absurdly long session username cannot act', code >= 400, f'{code} {r}')
            escaped = [p for p in work.rglob('*') if 'etc' in str(p) and 'passwd' in str(p)]
            ck('nothing was written outside the run tree', not escaped, str(escaped[:2]))

            sub = {'key': KEY, 'submitter': 'Member', 'game': 'new', 'system': 'nes',
                   'goal': 'new', 'new_goal_label': 'fastest', 'new_goal_rule': 'Be fast.',
                   'authors': 'Member', 'consent': 'yes', 'dry_run': '1',
                   'encode': 'https://youtu.be/goodvid12345'}
            code, r, _ = call(U + '/api/submit', dict(sub, new_game_title='../../etc/passwd'),
                              files={'movie': ('m.bk2', bk2())})
            ck('a traversal game title cannot escape the tree',
               code == 200 and '..' not in str(r.get('run', {}).get('game', '')), str(r)[:160])
            code, r, _ = call(U + '/api/submit', dict(sub, new_game_title='!!!'),
                              files={'movie': ('m.bk2', bk2())})
            ck('a title with no usable characters is refused', code == 400, str(r)[:120])

            # A game created by somebody with no authority is provisional and
            # waits for an expert. A game created by the expert who would be
            # asked is not: authority does not consult itself, and their name
            # goes on it so the record reads the same either way.
            for who, want, why in (('Member', False, 'a member'),
                                   ('NesExpert', True, 'the system expert')):
                code, r, _ = call(U + '/api/submit',
                                  dict(sub, submitter=who, dry_run='',
                                       new_game_title=f'Fresh Game By {who}'),
                                  files={'movie': ('m.bk2', mkarchive.unique_movie())})
                ck(f'{why} submits a new game', code == 200, str(r)[:160])
                gj = work / 'games' / 'nes' / f'fresh-game-by-{who.lower()}' / 'game.json'
                doc_ = json.loads(gj.read_text()) if gj.exists() else {}
                ck(f'a game created by {why} is '
                   f'{"established at once" if want else "provisional"}',
                   bool(doc_.get('established')) is want, str(doc_))
                if want:
                    ck('and it names them as the one who vouched',
                       doc_.get('ratifiedBy') == who and doc_.get('ratifiedAt'), str(doc_))

            # An address is shown to the Committee to be recognised, not to be
            # read: the whole thing never leaves the forum, and neither form is
            # ever stored. This is the function that promise rests on.
            sys.path.insert(0, str(REPO / 'archivist'))
            import importlib
            # importing it needs the env it runs with; nothing here starts a
            # server, the module only defines things at import time
            os.environ.setdefault('SUBMIT_KEY', 'unit-check')
            os.environ.setdefault('ARCHIVE_DIR', str(work))
            arch_mod = importlib.import_module('identity')     # email masking
            notify_mod = importlib.import_module('notify')
            for raw, want in (
                    ('johndoe@email.com', 'jo***oe@e****.com'),
                    ('a@b.c', 'a*@b*.c'),
                    ('notanemail', ''),
                    ('', '')):
                got = arch_mod.mask_email(raw)
                ck(f'{raw!r} is shown as {want!r}', got == want, repr(got))
            ck('a masked address keeps nothing readable of the local part',
               'johndoe' not in arch_mod.mask_email('johndoe@email.com'))

            # A Discord message that carries a link waits for the page behind
            # it: the site rebuilds after every archive change, and a link
            # posted before the deploy lands is a 404 with our name on it.
            import threading as _threading
            site_dir = pages / 'fakesite' / 'runs' / 'M1'
            probe = f'{FORUM_ORIGIN}/fakesite/runs/M1/'
            ck('wait_until_live gives up honestly on a page that never comes',
               notify_mod.wait_until_live(probe, time.time() + 2, poll=0.5) is False)
            def build_late():
                time.sleep(1.5)
                site_dir.mkdir(parents=True, exist_ok=True)
                (site_dir / 'index.html').write_text('here now')
            _threading.Thread(target=build_late, daemon=True).start()
            ck('and holds until the deploy lands when it does',
               notify_mod.wait_until_live(probe, time.time() + 10, poll=0.5) is True)

            # ---------------- files that lie ----------------
            code, r, _ = call(U + '/api/reproduce', {'key': KEY, 'user': 'Rep', 'run': 'M900401',
                                                    'dry_run': '1'},
                              files={'screenshot': ('shot.png', JPG)})
            ck('a jpeg named .png is refused', code == 400, str(r)[:120])
            code, r, _ = call(U + '/api/reproduce', {'key': KEY, 'user': 'Rep', 'run': 'M900401',
                                                    'dry_run': '1'},
                              files={'screenshot': ('shot.jpg', PNG)})
            ck('a png named .jpg is refused', code == 400, str(r)[:120])
            code, r, _ = call(U + '/api/reproduce', {'key': KEY, 'user': 'Rep', 'run': 'M900401',
                                                    'dry_run': '1'},
                              files={'screenshot': ('shot.png', PNG + b'\0' * (600 * 1024))})
            ck('an oversized screenshot is refused', code == 400, str(r)[:120])

            # ---------------- encode links ----------------
            for shape in ('https://www.youtube.com/watch?v=goodvid12345',
                          'https://youtu.be/goodvid12345',
                          'https://www.youtube.com/shorts/goodvid12345',
                          'https://www.youtube.com/watch?list=PL1&v=goodvid12345'):
                code, r, _ = call(U + '/api/submit', dict(sub, game='nes/testgame',
                                                          goal='fastest', encode=shape),
                                  files={'movie': ('m.bk2', bk2())})
                ck(f'accepts encode shape {shape.split("/")[-1][:22]}', code == 200, str(r)[:120])
            for bad in ('https://evil.example/#youtu.be/goodvid12345',
                        'https://notyoutube.com/watch?v=goodvid12345'):
                code, r, _ = call(U + '/api/submit', dict(sub, game='nes/testgame',
                                                          goal='fastest', encode=bad),
                                  files={'movie': ('m.bk2', bk2())})
                ck(f'refuses a non-YouTube host ({bad.split("/")[2]})', code == 400, str(r)[:120])
            code, r, _ = call(U + '/api/submit', dict(sub, game='nes/testgame', goal='fastest',
                                                     encode='https://youtu.be/hqonly12345'),
                              files={'movie': ('m.bk2', bk2())})
            ck('falls back to the hq thumbnail', code == 200, str(r)[:140])
            code, r, _ = call(U + '/api/submit', dict(sub, game='nes/testgame', goal='fastest',
                                                     encode='https://youtu.be/htmlonly1234'),
                              files={'movie': ('m.bk2', bk2())})
            ck('an encode whose thumbnail is not an image is refused', code == 400, str(r)[:140])

            # ---------------- expert scope ----------------
            # a system-scoped expert may act inside their system only
            code, r, _ = call(U + '/api/game/ratify', {'key': KEY, 'expert': 'NesExpert',
                                                       'game': 'nes/testgame', 'dry_run': '1'})
            ck('system expert may ratify inside their system', code == 200, str(r)[:140])
            code, r, _ = call(U + '/api/game/ratify', {'key': KEY, 'expert': 'NesExpert',
                                                       'game': 'dos/hardgame', 'dry_run': '1'})
            ck('system expert may not ratify another system', code == 403, str(r)[:140])
            code, r, _ = call(U + '/api/game/ratify', {'key': KEY, 'expert': 'SiteExpert',
                                                       'game': 'dos/hardgame', 'dry_run': '1'})
            ck('site expert may ratify anywhere', code == 200, str(r)[:140])
            # a ratification that records no ratifier is not an act, it is a
            # flag: the site log has to be able to say who vouched, and when.
            # The checks above are all dry runs, so this one has to land.
            code, r, _ = call(U + '/api/game/ratify', {'key': KEY, 'expert': 'SiteExpert',
                                                       'game': 'dos/hardgame'})
            ck('the ratification lands', code == 200, str(r)[:140])
            gdoc = json.loads((work / 'games/dos/hardgame/game.json').read_text())
            ck('ratifying records who did it and when',
               gdoc.get('ratifiedBy') == 'SiteExpert'
               and re.fullmatch(r'\d{4}-\d{2}-\d{2}', gdoc.get('ratifiedAt') or ''),
               str(gdoc))
            code, r, _ = call(U + '/api/game/ratify', {'key': KEY, 'expert': 'Member',
                                                       'game': 'nes/testgame', 'dry_run': '1'})
            ck('a member may not ratify', code == 403, str(r)[:140])
            # a group scope reaches every game in the group, and nothing else
            code, r, _ = call(U + '/api/game/ratify', {'key': KEY, 'expert': 'GroupExpert',
                                                       'game': 'nes/testgame', 'dry_run': '1'})
            ck('group expert may ratify a game in their group', code == 200, str(r)[:140])
            code, r, _ = call(U + '/api/game/ratify', {'key': KEY, 'expert': 'GroupExpert',
                                                       'game': 'dos/hardgame', 'dry_run': '1'})
            ck('group expert may not ratify outside their group', code == 403, str(r)[:140])
            code, r, _ = call(U + '/api/game/ratify', {'key': KEY, 'expert': 'SiteExpert',
                                                       'game': 'nes/nosuchgame', 'dry_run': '1'})
            ck('ratifying an unknown game is a 404', code == 404, str(r)[:140])

            # ---------------- branch coverage on rejections ----------------
            rejections = [
                ('report kind must be known',
                 '/api/report', {'key': KEY, 'user': 'Member', 'run': 'M900401',
                                 'kind': 'made-up', 'dry_run': '1'}, 400),
                ('report other needs details',
                 '/api/report', {'key': KEY, 'user': 'Member', 'run': 'M900401',
                                 'kind': 'other', 'dry_run': '1'}, 400),
                ('report on an unknown run is 404',
                 '/api/report', {'key': KEY, 'user': 'Member', 'run': 'M999999',
                                 'kind': 'other', 'details': 'x', 'dry_run': '1'}, 404),
                ('invalidate needs a reason',
                 '/api/invalidate', {'key': KEY, 'user': 'SiteExpert', 'run': 'M900401',
                                     'kind': 'verification', 'target': 'Rep', 'dry_run': '1'}, 400),
                ('invalidate needs a known kind',
                 '/api/invalidate', {'key': KEY, 'user': 'SiteExpert', 'run': 'M900401',
                                     'kind': 'nonsense', 'target': 'Rep',
                                     'reason': 'x', 'dry_run': '1'}, 400),
                ('note role must be known',
                 '/api/note', {'key': KEY, 'user': 'Member', 'run': 'M900401',
                               'role': 'nonsense', 'text': 'x', 'dry_run': '1'}, 400),
                ('edit needs something to change',
                 '/api/edit', {'key': KEY, 'user': 'Ada', 'run': 'M900401', 'dry_run': '1'}, 400),
                ('edit needs at least one author',
                 '/api/edit', {'key': KEY, 'user': 'Ada', 'run': 'M900401',
                               'authors': '', 'dry_run': '1'}, 400),
                ('case vote needs a numeric case id',
                 '/api/case/vote', {'key': KEY, 'user': 'Ver', 'run': 'M900401',
                                    'case': 'abc', 'reaffirm': '1', 'dry_run': '1'}, 400),
                ('case vote on an unknown case is 404',
                 '/api/case/vote', {'key': KEY, 'user': 'Ver', 'run': 'M900401',
                                    'case': '99', 'reaffirm': '1', 'dry_run': '1'}, 404),
                ('a case needs live verifications',
                 '/api/case/open', {'key': KEY, 'user': 'Member', 'run': 'M900401',
                                    'reason': 'nothing to dispute', 'dry_run': '1'}, 400),
                ('like on an unknown run is 404',
                 '/api/like', {'key': KEY, 'user': 'Member', 'run': 'M999999', 'dry_run': '1'}, 404),
            ]
            wrong = []
            for label, path, data, want in rejections:
                code, r, _ = call(U + path, data)
                if code != want:
                    wrong.append(f'{label}: got {code} want {want} ({str(r)[:60]})')
            ck('rejection branches answer with the right status', not wrong, str(wrong[:5]))

            # ---------------- request size cap ----------------
            code, r, _ = call(U + '/api/submit', dict(sub, game='nes/testgame', goal='fastest'),
                              files={'movie': ('m.bk2', b'\0' * (33 * 1024 * 1024))})
            ck('an oversized movie is refused', code in (400, 413), f'{code} {str(r)[:80]}')

            # ---------------- the archive is still sane ----------------
            check = td / 'check'
            # file:// forces the git transport instead of copying pack files out of
            # origin.git, which races with the archivist repacking it mid-push
            subprocess.run(['git', 'clone', '-q', f'file://{origin}', str(check)],
                           check=True)
            v = subprocess.run([sys.executable, str(check / 'validate.py')],
                               capture_output=True, text=True)
            ck('archive still validates after the hostile pass', v.returncode == 0,
               v.stdout[-300:])
        finally:
            proc.terminate()
            httpd.shutdown()

    print('---', len(failures), 'failures')
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
