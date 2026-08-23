#!/usr/bin/env python3
"""Archivist integration tests.

Spins up a scratch bare git remote (seeded from a real archive checkout plus a
synthetic native run), launches archivist.py against it on a local port, and
exercises every endpoint: submit, reproduce, verify, dispute cases, the claim
flow (against a mock homepage server), expert invalidation/ratification, and
the DiscourseConnect consumer (by forging correctly-signed provider
responses). Finally validates the pushed archive state with validate.py.

Requires flask importable (pip install --target and PYTHONPATH work fine).
Usage: tests/test_archivist.py [real_archive_dir]
"""
import base64
import hashlib
import hmac
import http.server
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

REPO = pathlib.Path(__file__).resolve().parent.parent
ARCHIVE = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else pathlib.Path.home() / 'ToolAssisted-archive')
PNG = b'\x89PNG\r\n\x1a\n' + b'\0' * 50
KEY = 'testkey'

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mkarchive  # noqa: E402

uniq_bk2 = mkarchive.unique_movie


def uniq_files():
    return {'movie': ('run.bk2', uniq_bk2())}


def make_bk2():
    """A minimal parseable bk2: zipped Header.txt + Input Log.txt."""
    import io as _io, zipfile as _zip
    buf = _io.BytesIO()
    with _zip.ZipFile(buf, 'w') as z:
        z.writestr('Header.txt', 'Platform NES\nrerecordCount 42\n')
        z.writestr('Input Log.txt', 'LogKey:#Reset|Power|\n' + '|..|........|\n' * 100)
    return buf.getvalue()

BK2 = make_bk2()
SSO_SECRET = 'testssosecret'

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


def call(url, data=None, files=None, cookie=None, method=None):
    """multipart/form POST helper; returns (status, json)."""
    if files or data:
        boundary = 'testboundary42'
        body = b''
        for k, v in (data or {}).items():
            # a list value is a repeated field (checkbox groups)
            for one in (v if isinstance(v, (list, tuple)) else [v]):
                body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"'
                         f'\r\n\r\n{one}\r\n').encode()
        for k, (fn, content) in (files or {}).items():
            body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; '
                     f'filename="{fn}"\r\nContent-Type: application/octet-stream'
                     f'\r\n\r\n').encode() + content + b'\r\n'
        body += f'--{boundary}--\r\n'.encode()
        req = urllib.request.Request(url, body, method=method or 'POST',
                                     headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
    else:
        req = urllib.request.Request(url, method=method or 'GET')
    if cookie:
        req.add_header('Cookie', cookie)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b'{}'), dict(r.headers)
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body), dict(e.headers)
        except json.JSONDecodeError:
            return e.code, {'raw': body[:200].decode(errors='replace')}, dict(e.headers)


def main():
    try:
        import flask  # noqa: F401
    except ImportError:
        sys.exit('flask not importable — set PYTHONPATH to a dir containing it '
                 '(pip install --target <dir> flask)')

    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        # scratch remote seeded from the real archive + one native run
        seed = td / 'seed'
        shutil.copytree(ARCHIVE, seed, ignore=shutil.ignore_patterns('.git'))
        mkarchive.lighten(seed)
        # authors/ holds members only; a copy of an archive that predates that
        # rule would fail its own validator before the suite tested anything
        for af in (seed / 'authors').glob('*.json'):
            if not json.loads(af.read_text()).get('claimed'):
                af.unlink()
        mkarchive.prune_superseded(seed)
        # Live claims drift under the suite exactly like live roles do (the
        # day the Committee approved two real ones, requests[0] stopped being
        # ours), so the fixture starts with none.
        (seed / 'claims.json').write_text('{"requests": []}\n')
        # The real archive's roles are LIVE GOVERNANCE and change under our
        # feet: the day the Founder seated four real Committee members, every
        # majority calculation in this suite silently changed denominator and
        # CI went red with no code change. The fixture's roster is therefore
        # fixed here, from scratch, and owes nothing to the archive's.
        ex = {'events': [
            {'user': 'eien86', 'role': 'founder', 'action': 'granted',
             'by': 'founder', 'date': '2026-08-14', 'reason': 'fixture: the founder'},
            {'user': 'eien86', 'role': 'expert', 'scope': 'site', 'action': 'granted',
             'by': 'founder', 'date': '2026-08-14', 'reason': 'fixture: site expert'},
            {'user': 'eien86', 'role': 'committee', 'action': 'granted',
             'by': 'founder', 'date': '2026-08-14', 'reason': 'fixture: on the committee'},
            {'user': 'eien86', 'role': 'moderator', 'action': 'granted',
             'by': 'founder', 'date': '2026-08-14', 'reason': 'fixture: a moderator'},
        ]}
        ex['events'].append({'user': 'groupexpert', 'role': 'expert', 'scope': 'nes',
                             'action': 'granted', 'by': 'eien86', 'date': '2026-08-17',
                             'reason': 'fixture: a system-scoped expert to test against'})
        # A Committee of four in the archive. The mock forum deliberately
        # reports a different number for its group, because the size of the
        # Committee is a fact of the archive and nothing else may set it.
        # a site-wide expert who sits on no committee: the widest game scope
        # there is, and no say at all over who somebody is
        ex['events'].append({'user': 'SiteOnly', 'role': 'expert', 'scope': 'site',
                             'action': 'granted', 'by': 'eien86', 'date': '2026-08-17',
                             'reason': 'fixture: site scope without a committee seat'})
        # an editor: full control over the library's shape, none over people
        # or the runs themselves
        ex['events'].append({'user': 'Shelver', 'role': 'editor',
                             'action': 'granted', 'by': 'committee',
                             'date': '2026-08-17',
                             'reason': 'fixture: the library editor'})
        (seed / 'authors' / 'shelver.json').write_text(json.dumps(
            {'username': 'Shelver', 'claimed': True}, indent=1) + '\n')
        for who in ('CommitteeB', 'CommitteeC', 'CommitteeD'):
            ex['events'].append({'user': who, 'role': 'committee', 'action': 'granted',
                                 'by': 'founder', 'date': '2026-08-17',
                                 'reason': 'fixture: sitting committee member'})
            # a seat implies a member record; the deletion tests need one to
            # refuse (and, for the Founder, to delete)
            (seed / 'authors' / f'{who.lower()}.json').write_text(json.dumps(
                {'username': who, 'claimed': True}, indent=1))
        (seed / 'roles.json').write_text(json.dumps(ex, indent=1))
        (seed / 'authors' / 'ssouser.json').write_text(json.dumps({
            'username': 'ssouser', 'claimed': True,
            'tasvideosProfile': 'https://tasvideos.org/Users/Profile/ssouser'}, indent=1))
        rd = seed / 'games/nes/pinball/runs/M900010'
        rd.mkdir(parents=True)
        (rd / 'M900010.bk2').write_bytes(b'test')
        (rd / 'thumb.png').write_bytes(PNG)
        (rd / 'run.json').write_text(json.dumps({
            'id': 'M900010', 'game': 'nes/pinball', 'category': {'goal': '100k-glitched'},
            'authors': [{'user': 'TestAuthor'}],
            'movie': {'file': 'M900010.bk2', 'format': 'bk2', 'frames': 12345,
                      'rerecords': None, 'start': 'power-on'},
            'thumbnail': 'thumb.png',
            'contract': {'emulator': 'BizHawk 2.11'},
            'status': {'reproduced': 'none', 'verified': 'none'},
            'encodes': [{'kind': 'youtube', 'url': 'https://www.youtube.com/watch?v=abc123DEF45'}],
            'submitted': '2026-08-01T10:00:00Z', 'submittedBy': 'TestAuthor'}, indent=1))
        subprocess.run(['git', 'init', '-q', '-b', 'main'], cwd=seed, check=True)
        subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t',
                        'add', '-A'], cwd=seed, check=True)
        subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t',
                        'commit', '-qm', 'seed'], cwd=seed, check=True)
        origin = td / 'origin.git'
        subprocess.run(['git', 'clone', '-q', '--bare', str(seed), str(origin)], check=True)
        work = td / 'work'
        subprocess.run(['git', 'clone', '-q', str(origin), str(work)], check=True)
        subprocess.run(['git', 'config', 'user.name', 'archivist-test'], cwd=work, check=True)
        subprocess.run(['git', 'config', 'user.email', 't@t'], cwd=work, check=True)

        # mock server: tasvideos homepages + youtube thumbnail endpoint
        pages = td / 'homepages'
        pages.mkdir()
        (pages / 'thumbs' / 'videoonly001').mkdir(parents=True)
        (pages / 'thumbs' / 'videoonly001' / 'maxresdefault.jpg').write_bytes(
            b'\xff\xd8\xff' + b'\0' * 80)
        (pages / 'thumbs' / 'goodvid12345').mkdir(parents=True)
        (pages / 'thumbs' / 'goodvid12345' / 'maxresdefault.jpg').write_bytes(
            b'\xff\xd8\xff' + b'\0' * 60)
        hport = free_port()

        # Providers whose thumbnail needs an API call are asked through
        # PROVIDER_MOCK_BASE, which wraps the whole real URL into our path.
        # A wrapped URL that points back at this very server is just a file
        # request wearing a disguise, so unwrap it and serve it statically:
        # that keeps THUMB_FETCH_BASE (already aimed here) working untouched.
        PROVIDER_ROUTES = {
            'https://ext.nicovideo.jp/api/getthumbinfo/sm9':
                b'<thumb><thumbnail_url>https://nico.cdn.example/sm9.jpg'
                b'</thumbnail_url></thumb>',
            'https://nico.cdn.example/sm9.jpg': b'\xff\xd8\xff' + b'\0' * 60,
        }

        # The forum's groups as they stand, and every membership change the
        # archivist prints into them. 'ForumOnly' is in the committee group and
        # in no role event: under the old two-way mirror that made them a
        # committee member; now it makes them a stray the next publish removes.
        GROUPS = {'experts': {'eien86'}, 'committee': {'eien86', 'ForumOnly'}}
        GROUP_WRITES = []
        DISCORD_MSGS = []          # what the archivist told 'Discord'

        class MockHandler(http.server.SimpleHTTPRequestHandler):
            def do_GET(self):                                    # noqa: N802
                path = urllib.parse.unquote(self.path)
                # the mock forum's committee and its polls, so an annulment can
                # be tested against every way a poll can fail to be a decision
                def _json(payload, code=200):
                    body = json.dumps(payload).encode()
                    self.send_response(code)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                if path == '/groups.json':
                    return _json({'groups': [{'id': 41, 'name': 'experts'},
                                             {'id': 42, 'name': 'committee'}]})
                if path == '/groups/committee.json':
                    # wildly wrong on purpose: nothing may read a governance
                    # threshold out of the forum, so nothing here may matter
                    return _json({'group': {'name': 'committee', 'user_count': 99}})
                m_ = re.fullmatch(r'/groups/(\w+)/members\.json', path)
                if m_:
                    return _json({'members': [{'username': u}
                                              for u in sorted(GROUPS.get(m_.group(1), []))]})
                if path.startswith('/posts/') and path.endswith('.json'):
                    pid = path[7:-5]
                    polls = {
                        # decisive: 3 of a committee of 4
                        '901': [{'status': 'closed', 'public': True, 'groups': 'committee',
                                 'options': [{'html': 'Annul', 'votes': 3},
                                             {'html': 'Keep', 'votes': 1}]}],
                        # open to everybody, so not a committee decision
                        '902': [{'status': 'closed', 'public': True, 'groups': '',
                                 'options': [{'html': 'Annul', 'votes': 4}]}],
                        # a committee poll that has not closed
                        '903': [{'status': 'open', 'public': True, 'groups': 'committee',
                                 'options': [{'html': 'Annul', 'votes': 4}]}],
                        # closed, but only two of four wanted it
                        '904': [{'status': 'closed', 'public': True, 'groups': 'committee',
                                 'options': [{'html': 'Annul', 'votes': 2},
                                             {'html': 'Keep', 'votes': 2}]}],
                        # anonymous votes cannot be checked by anybody
                        '905': [{'status': 'closed', 'public': False, 'groups': 'committee',
                                 'options': [{'html': 'Annul', 'votes': 4}]}],
                        # 3 of 4 voting to grant: a simple majority, and also
                        # the two thirds a removal needs
                        '907': [{'status': 'closed', 'public': True, 'groups': 'committee',
                                 'options': [{'html': 'Grant', 'votes': 3},
                                             {'html': 'No', 'votes': 1}]}],
                        # 3 of 4 is a hard majority; 2 of 4 is a simple one only
                        # 2 of 4 wanted the grant: short of a simple majority
                        '909': [{'status': 'closed', 'public': True, 'groups': 'committee',
                                 'options': [{'html': 'Grant', 'votes': 2},
                                             {'html': 'No', 'votes': 2}]}],
                        '908': [{'status': 'closed', 'public': True, 'groups': 'committee',
                                 'options': [{'html': 'Remove', 'votes': 2},
                                             {'html': 'Keep', 'votes': 1}]}],
                        '906': [],
                    }
                    if pid in polls:
                        return _json({'id': int(pid), 'polls': polls[pid]})
                    self.send_response(404); self.end_headers(); return
                # the mock forum knows its own members: /u/<name>.json
                if path.startswith('/u/') and path.endswith('.json'):
                    name = path[3:-5]
                    body = json.dumps({'user': {'username': name}}).encode()
                    if name.lower() in ('nosuchperson',):
                        self.send_response(404); self.end_headers(); return
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path.startswith('/p/'):
                    url = path[3:]
                    if url.startswith(f'http://127.0.0.1:{hport}'):
                        self.path = urllib.parse.urlparse(url).path
                        return super().do_GET()
                    body = PROVIDER_ROUTES.get(url)
                    if body is None:
                        self.send_response(404); self.end_headers(); return
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                return super().do_GET()

            def _members(self, add):
                """PUT and DELETE on a group: what the archivist prints out."""
                n = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(n).decode()
                who = json.loads(body).get('usernames', '') if body.startswith('{') else \
                    urllib.parse.parse_qs(body).get('usernames', [''])[0]
                gid = re.fullmatch(r'/groups/(\d+)/members\.json', self.path)
                name = {41: 'experts', 42: 'committee'}.get(int(gid.group(1))) if gid else None
                if name:
                    GROUP_WRITES.append(('add' if add else 'remove', name, who))
                    (GROUPS.setdefault(name, set()).add(who) if add
                     else GROUPS.get(name, set()).discard(who))
                self.send_response(200)
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'{}')

            def do_POST(self):                                   # noqa: N802
                n = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(n).decode()
                if self.path == '/discord-hook':
                    try:
                        d = json.loads(body)
                        m = d.get('content', '')
                        for e in d.get('embeds') or []:
                            m += ' [img: ' + e.get('image', {}).get('url', '') + ']'
                        DISCORD_MSGS.append(m)
                    except ValueError:
                        DISCORD_MSGS.append(body)
                out = json.dumps({'topic_id': 900, 'id': 1}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def do_PUT(self):                                    # noqa: N802
                return self._members(True)

            def do_DELETE(self):                                 # noqa: N802
                return self._members(False)

        httpd = http.server.ThreadingHTTPServer(
            ('127.0.0.1', hport),
            lambda *a, **k: MockHandler(*a, directory=str(pages), **k))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        # fake tasvideos-backup for the self-service import (hermetic)
        dumps = td / 'dumps'
        (dumps / 'metadata').mkdir(parents=True)
        (dumps / 'movies').mkdir()
        (dumps / 'submission-notes').mkdir()
        (dumps / 'thumbnails').mkdir()
        JPG = b'\xff\xd8\xff' + b'\0' * 40
        pubs = [
            {'id': 910001, 'submissionId': 810001, 'title': 'NES Pinball by ssouser',
             'systemCode': 'NES', 'systemFrameRate': 60.1, 'goal': 'baseline',
             'gameId': 5, 'authors': ['ssouser'], 'additionalAuthors': '',
             'urls': ['https://youtu.be/goodvid12345'], 'frames': 4321,
             'rerecordCount': 99, 'emulatorVersion': 'FCEUX 2.6.4',
             'createTimestamp': '2020-05-01T00:00:00Z', 'obsoletedById': None},
            {'id': 910002, 'submissionId': 810002, 'title': 'NES Impo Quest by ssouser & CoAuthorX',
             'systemCode': 'NES', 'systemFrameRate': 60.1, 'goal': 'all keys',
             'gameId': 6, 'authors': ['ssouser', 'CoAuthorX'], 'additionalAuthors': '',
             'urls': [], 'frames': 777, 'rerecordCount': 5, 'emulatorVersion': 'BizHawk 2.9',
             'createTimestamp': '2021-06-01T00:00:00Z', 'obsoletedById': None},
            {'id': 910003, 'submissionId': 810004, 'title': 'NES Huge Movie by ssouser',
             'systemCode': 'NES', 'systemFrameRate': 60.1, 'goal': 'baseline',
             'gameId': 8, 'authors': ['ssouser'], 'additionalAuthors': '',
             'urls': ['https://youtu.be/goodvid12345'], 'frames': 9, 'rerecordCount': 1,
             'emulatorVersion': 'x', 'createTimestamp': '2023-01-01T00:00:00Z',
             'obsoletedById': None},
            {'id': 900010, 'submissionId': 810003, 'title': 'Collision bait by ssouser',
             'systemCode': 'NES', 'systemFrameRate': 60.1, 'goal': 'baseline',
             'gameId': 7, 'authors': ['ssouser'], 'additionalAuthors': '',
             'urls': [], 'frames': 1, 'rerecordCount': 1, 'emulatorVersion': 'x',
             'createTimestamp': '2022-01-01T00:00:00Z', 'obsoletedById': None},
        ]
        (dumps / 'metadata' / 'publications.json').write_text(json.dumps(pubs))
        (dumps / 'metadata' / 'submissions.json').write_text(json.dumps([
            {'id': 810001, 'gameName': 'Pinball', 'romName': 'Pinball (U).nes',
             'movieStartType': 0, 'emulatorVersion': 'FCEUX 2.6.4'},
            {'id': 810002, 'gameName': 'Impo Quest', 'romName': '',
             'movieStartType': 0, 'emulatorVersion': 'BizHawk 2.9'},
            {'id': 810004, 'gameName': 'Huge Movie', 'romName': '',
             'movieStartType': 0, 'emulatorVersion': 'x'},
        ]))
        # a movie past the intake cap: two of these reached the real archive
        # through the import, which never checked, and left it invalid
        with zipfile.ZipFile(dumps / 'movies' / 'M910003-ssouser-game.zip', 'w') as z:
            z.writestr('movie.bk2', make_bk2() + b'\0' * (33 * 1024 * 1024))
        (dumps / 'thumbnails' / 'M910003.jpg').write_bytes(JPG)
        for pid in (910001, 910002):
            zp = dumps / 'movies' / f'M{pid}-ssouser-game.zip'
            with zipfile.ZipFile(zp, 'w') as z:
                z.writestr('movie.bk2', make_bk2())
            (dumps / 'thumbnails' / f'M{pid}.jpg').write_bytes(JPG)
        (dumps / 'submission-notes' / 'S810001.txt').write_text(
            'My own notes about the pinball run.\n----\n[user:SomeJudge]: Accepting to Moons.\n')
        (dumps / 'submission-notes' / 'S810002.txt').write_text('Co-authored notes.\n')

        port = free_port()
        env = dict(SUBMIT_KEY=KEY, ARCHIVE_DIR=str(work), ARCHIVIST_BRANCH='main',
                   GIT_SSH_COMMAND='ssh', PORT=str(port), DISCOURSE_KEY='mock-key',
                   DISCORD_WEBHOOK_URL=f'http://127.0.0.1:{hport}/discord-hook',
                   # no held messages in the suite: the pages the links point
                   # at are never built here, and a poll would hit the real site
                   NOTIFY_LINK_WAIT_SECONDS='0',
                   DISCOURSE_HOOK_SECRET='hooksecret',
                   # read the roster fresh every time, so the staleness test is
                   # deterministic instead of racing a 20 second window
                   ARCHIVE_REFRESH_SECONDS='0', ROLE_RECONCILE_SECONDS='0',
                   # the mock stands in for the forum, so account lookups are real
                   DISCOURSE_URL=f'http://127.0.0.1:{hport}',
                   CLAIM_FETCH_BASE=f'http://127.0.0.1:{hport}/',
                   THUMB_FETCH_BASE=f'http://127.0.0.1:{hport}/thumbs/',
                   PROVIDER_MOCK_BASE=f'http://127.0.0.1:{hport}/p/',
                   DUMPS_DIR=str(dumps),
                   # the publisher: build with the real generator from this
                   # repo, into the sandbox — hermetic, like everything here
                   WEBSITE_DIR=str(REPO), SITE_DIR=str(td / 'site'),
                   DISCOURSE_CONNECT_SECRET=SSO_SECRET, SESSION_SECRET='testsessionsecret',
                   SELF_URL=f'http://127.0.0.1:{port}', SITE_ORIGIN='https://toolassisted.run',
                   PATH='/usr/bin:/bin', HOME=str(td))
        import os
        if 'PYTHONPATH' in os.environ:
            env['PYTHONPATH'] = os.environ['PYTHONPATH']
        proc = subprocess.Popen([sys.executable, str(REPO / 'archivist/archivist.py')],
                                env=env, stdout=(td / 'log').open('w'), stderr=subprocess.STDOUT)
        U = f'http://127.0.0.1:{port}'
        try:
            for _ in range(60):
                try:
                    urllib.request.urlopen(U + '/api/me')
                    break
                except OSError:
                    time.sleep(0.5)
            else:
                print((td / 'log').read_text()[-2000:])
                sys.exit('archivist did not start')

            # --- hardening headers on every answer (ZAP pass) ---
            req = urllib.request.Request(U + '/api/me')
            with urllib.request.urlopen(req) as resp:
                hdr = {k.lower(): v for k, v in resp.headers.items()}
            ck('API answers carry the hardening headers',
               hdr.get('x-content-type-options') == 'nosniff'
               and hdr.get('x-frame-options') == 'DENY'
               and "default-src 'none'" in hdr.get('content-security-policy', '')
               and 'referrer-policy' in hdr, str(hdr))
            with urllib.request.urlopen(urllib.request.Request(U + '/')) as resp:
                hdr = {k.lower(): v for k, v in resp.headers.items()}
            ck('the fallback form page keeps its inline styles but cannot be framed',
               "style-src 'unsafe-inline'" in hdr.get('content-security-policy', '')
               and "frame-ancestors 'none'" in hdr.get('content-security-policy', ''), str(hdr))
            c, r, _ = call(U + '/api/like', {'key': KEY, 'user': '...', 'run': 'M900010', 'dry_run': '1'})
            ck('a username starts with a letter or digit', c == 400, str(r))

            # --- submit: encode is mandatory; the thumbnail derives from it ---
            sub = {'key': KEY, 'submitter': 'TestAuthor', 'game': 'nes/pinball',
                   'goal': '100k-glitched', 'authors': 'TestAuthor', 'dry_run': '1',
                   'encode': 'https://youtu.be/goodvid12345', 'consent': 'yes'}
            files = {'movie': ('t.bk2', BK2)}
            noconsent = dict(sub)
            del noconsent['consent']
            c, r, _ = call(U + '/api/submit', noconsent, files)
            ck('submit without consent rejected', c == 400 and 'consent' in r.get('error', ''))
            c, r, _ = call(U + '/api/submit', dict(sub, encode=''), files)
            ck('submit without encode rejected', c == 400 and 'encode' in r.get('error', ''))
            c, r, _ = call(U + '/api/submit', dict(sub, encode='https://example.com/video'), files)
            ck('an encode from no known platform is rejected', c == 400)
            # the files the movie was made against: rows, any number, hashed
            # client-side; the old single pair still counts as one row
            c, r, _ = call(U + '/api/submit', dict(sub, file_name=['Disc 1.iso', 'game.exe'],
                                                   file_sha1=['A' * 40, '']), files)
            ck('a submission carries its file rows',
               c == 200 and r['run']['contract']['files'] == [{'name': 'Disc 1.iso', 'sha1': 'a' * 40},
                                                              {'name': 'game.exe'}], str(r)[:300])
            c, r, _ = call(U + '/api/submit', dict(sub, file_name=['', 'x.nes'], file_sha1=['deadbeef', '']), files)
            ck('a sha1 without a name is refused', c == 400 and 'name is required' in r.get('error', ''), str(r))
            c, r, _ = call(U + '/api/submit', dict(sub, file_name='x.nes', file_sha1='deadbeef'), files)
            ck('a malformed file sha1 is refused', c == 400 and '40 hexadecimal' in r.get('error', ''), str(r))
            c, r, _ = call(U + '/api/submit', dict(sub, rom_sha1='A' * 40, rom_name='Game.nes'), files)
            ck('the legacy rom pair lands as one file row',
               c == 200 and r['run']['contract']['files'] == [{'name': 'Game.nes', 'sha1': 'a' * 40}]
               and 'rom' not in r['run']['contract'], str(r)[:200])
            c, r, _ = call(U + '/api/submit', files=files, data=dict(
                sub, encode='https://evil.example/?u=youtube.com/watch?v=goodvid12345'))
            ck('a hostile url that merely contains a platform url is rejected', c == 400, str(r)[:120])
            c, r, _ = call(U + '/api/submit', dict(sub, encode='https://youtu.be/nosuchvid99'), files)
            ck('dead video rejected', c == 400 and 'does not resolve' in r.get('error', ''))
            c, r, _ = call(U + '/api/submit', sub, uniq_files())
            ck('valid encode -> derived thumbnail', c == 200 and r['run']['thumbnail'] == 'thumb.jpg'
               and r['run']['encodes'][0]['kind'] == 'youtube', str(r))
            ck('movie parsed at intake',
               c == 200 and r['run']['movie']['rerecords'] == 42
               and r['run']['movie']['frames'] > 0
               and len(r['run']['movie'].get('sha1') or '') == 40, str(r))
            # another platform, whose thumbnail only an API call can name
            c, r, _ = call(U + '/api/submit',
                           dict(sub, encode='https://www.nicovideo.jp/watch/sm9'),
                           uniq_files())
            ck('a Niconico encode is accepted like any other',
               c == 200 and r['run']['encodes'][0]['kind'] == 'niconico'
               and r['run']['thumbnail'] == 'thumb.jpg', str(r)[:200])
            c, r, _ = call(U + '/api/submit',
                           dict(sub, encode='https://www.nicovideo.jp/watch/sm404'),
                           uniq_files())
            ck('a Niconico video that does not exist is rejected',
               c == 400 and 'Niconico' in r.get('error', ''), str(r)[:140])
            c, r, _ = call(U + '/api/submit', sub, {'movie': ('junk.bk2', b'not a movie')})
            ck('unparseable movie rejected', c == 400 and 'did not parse' in r.get('error', ''))

            # --- games and categories exist beforehand; creation is its own
            # flow, open to every member ---
            c, r, _ = call(U + '/api/submit', dict(sub, game='new', system='nes',
                                                   new_game_title='Ghost Game'), uniq_files())
            ck('the submit form no longer creates games',
               c == 400 and 'create the game first' in r.get('error', ''), str(r))
            c, r, _ = call(U + '/api/submit', dict(sub, game='nes/pinball', goal='no-such-goal'),
                           uniq_files())
            ck('an unknown category points at the creation flow',
               c == 400 and 'create it first' in r.get('error', ''), str(r))
            c, r, _ = call(U + '/api/game/create',
                           {'key': KEY, 'user': 'TestAuthor', 'system': 'fakesys',
                            'title': 'Ghost Game'})
            ck('unknown system rejected at creation', c == 400
               and 'system' in r.get('error', ''), str(r))
            c, r, _ = call(U + '/api/game/create',
                           {'key': KEY, 'user': 'TestAuthor', 'system': 'nes',
                            'title': "Solomon's Key",
                            'cat_label': 'fastest completion',
                            'cat_rule': 'Complete the game as fast as possible.'})
            ck('any member creates a game, first category born with it',
               c == 200 and r['game'] == 'nes/solomons-key'
               and r['category'] == 'fastest-completion', str(r))
            c, r, _ = call(U + '/api/game/create',
                           {'key': KEY, 'user': 'TestAuthor', 'system': 'nes',
                            'title': "Solomon's Key Hack", 'released': '1990',
                            'unofficial': 'yes', 'website': 'https://example.org/sk'})
            gj = json.loads((work / 'games/nes/solomons-key-hack/game.json').read_text())
            ck('a game is created with its properties (#44)',
               c == 200 and gj.get('released') == '1990' and gj.get('unofficial') is True
               and gj.get('website') == 'https://example.org/sk' and 'discord' not in gj,
               f'{c} {r} {gj}')
            c, r, _ = call(U + '/api/game/create',
                           {'key': KEY, 'user': 'TestAuthor', 'system': 'nes',
                            'title': 'Bad Date Game', 'released': '19'})
            ck('a malformed release date refuses the creation (#44)', c == 400, str(r))
            c, r, _ = call(U + '/api/category/add',
                           {'key': KEY, 'user': 'TestAuthor', 'game': 'nes/pinball',
                            'label': 'pacifist', 'rule': 'Never destroy anything.',
                            'metrics': '[{"label": "Score", "type": "number", '
                                       '"better": "higher", "unit": "pts"}, '
                                       '{"key": "time"}]'})
            ck('any member creates a category, defining its metrics',
               c == 200 and r['key'] == 'pacifist', str(r))
            subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            cj_ = json.loads((work / 'games/nes/pinball/categories.json').read_text())
            pac = next(o for d in cj_['dimensions'] for o in d['options']
                       if o['key'] == 'pacifist')
            ck('the metric hierarchy is stored as defined',
               [m['key'] for m in pac['metrics']] == ['score', 'time']
               and pac['metrics'][0]['better'] == 'higher', str(pac))

            # --- subcategories (#43): a second level inside a category ---
            c, r, _ = call(U + '/api/category/add',
                           {'key': KEY, 'user': 'TestAuthor', 'game': 'nes/pinball',
                            'label': 'Episode 1', 'rule': 'Finish episode 1.'})
            ck('a category to hold subcategories', c == 200 and r['key'] == 'episode-1', str(r))
            c, r, _ = call(U + '/api/category/add',
                           {'key': KEY, 'user': 'TestAuthor', 'game': 'nes/pinball',
                            'parent': 'episode-1', 'label': 'any%', 'rule': '',
                            'metrics': '[{"label": "Score", "type": "number", "better": "higher"}]'})
            ck('a subcategory defines no metrics of its own', c == 400 and 'metrics' in r.get('error', ''), str(r))
            c, r, _ = call(U + '/api/category/add',
                           {'key': KEY, 'user': 'TestAuthor', 'game': 'nes/pinball',
                            'parent': 'episode-1', 'label': 'any%', 'rule': ''})
            ck('a subcategory is added under its category, rule optional',
               c == 200 and r['key'] == 'any' and r['parent'] == 'episode-1', str(r))
            c, r, _ = call(U + '/api/category/add',
                           {'key': KEY, 'user': 'TestAuthor', 'game': 'nes/pinball',
                            'parent': 'episode-1', 'label': '100%', 'rule': 'Collect everything.'})
            ck('a second subcategory', c == 200 and r['key'] == '100', str(r))
            c, r, _ = call(U + '/api/category/add',
                           {'key': KEY, 'user': 'TestAuthor', 'game': 'nes/pinball',
                            'parent': 'episode-1', 'label': 'any%'})
            ck('a duplicate subcategory is refused', c == 409, str(r))
            c, r, _ = call(U + '/api/submit', dict(sub, game='nes/pinball', goal='episode-1'), uniq_files())
            ck('a run in a category with subcategories must pick one',
               c == 400 and 'pick one' in r.get('error', ''), str(r))
            c, r, _ = call(U + '/api/submit', dict(sub, game='nes/pinball', goal='episode-1', sub='100'), uniq_files())
            ck('the subcategory lands on the run',
               c == 200 and r['run']['category'] == {'goal': 'episode-1', 'sub': '100'}, str(r)[:200])
            c, r, _ = call(U + '/api/submit', dict(sub, game='nes/pinball', goal='100k-glitched', sub='any'), uniq_files())
            ck('a subcategory where the category has none is refused',
               c == 400 and 'no subcategories' in r.get('error', ''), str(r))
            subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            c, r, _ = call(U + '/api/submit', dict(sub, game='nes/pinball', goal='episode-1', sub='100',
                                                   dry_run='0'), uniq_files())
            ck('a real run in a subcategory', c == 200, str(r)[:200])
            sub_run = r['id']
            c, r, _ = call(U + '/api/category/delete',
                           {'key': KEY, 'expert': 'groupexpert', 'game': 'nes/pinball',
                            'option': 'episode-1', 'sub': '100', 'reason': 'testing the refusal, on the record'})
            ck('a subcategory with runs in it cannot be deleted', c == 409, str(r))
            c, r, _ = call(U + '/api/expert/edit',
                           {'key': KEY, 'expert': 'groupexpert', 'kind': 'run', 'target': sub_run,
                            'field': 'goal', 'value': 'episode-1/any', 'reason': 'moved to the right subcategory'})
            subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            rj_ = json.loads((work / f'games/nes/pinball/runs/{sub_run}/run.json').read_text())
            ck('an expert moves a run between subcategories',
               c == 200 and rj_['category'] == {'goal': 'episode-1', 'sub': 'any'}, str(r))
            c, r, _ = call(U + '/api/expert/edit',
                           {'key': KEY, 'expert': 'groupexpert', 'kind': 'category',
                            'target': 'nes/pinball:episode-1/100', 'field': 'label', 'value': '100% completion',
                            'reason': 'spelling out the label'})
            subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            cj_ = json.loads((work / 'games/nes/pinball/categories.json').read_text())
            ep1 = next(o for d in cj_['dimensions'] for o in d['options'] if o['key'] == 'episode-1')
            ck('a subcategory label is edited on the inner record',
               c == 200 and next(x['label'] for x in ep1['subcategories'] if x['key'] == '100') == '100% completion', str(r))
            c, r, _ = call(U + '/api/category/delete',
                           {'key': KEY, 'expert': 'groupexpert', 'game': 'nes/pinball',
                            'option': 'episode-1', 'sub': '100', 'reason': 'now empty, testing removal'})
            subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            cj_ = json.loads((work / 'games/nes/pinball/categories.json').read_text())
            ep1 = next(o for d in cj_['dimensions'] for o in d['options'] if o['key'] == 'episode-1')
            ck('an empty subcategory is deleted', c == 200 and [x['key'] for x in ep1['subcategories']] == ['any'], str(r))
            c, r, _ = call(U + '/api/submit', dict(sub, game='nes/pinball',
                                                   goal='pacifist'), uniq_files())
            ck('a metric-bearing category demands its values',
               c == 400 and 'metric_score' in r.get('error', ''), str(r))
            c, r, _ = call(U + '/api/submit', dict(sub, game='nes/pinball',
                                                   goal='pacifist',
                                                   metric_score='1250'), uniq_files())
            ck('stated values ride the dry run',
               c == 200 and r['run']['metrics'] == {'score': 1250.0}, str(r))
            ck('a dry run changes nothing, so it carries no publish serial',
               'serial' not in r, str(r))
            newsub = dict(sub, game='nes/solomons-key', goal='fastest-completion')
            del newsub['dry_run']
            c, r, _ = call(U + '/api/submit', newsub, uniq_files())
            ck('submission lands in the pre-created game', c == 200 and r.get('ok'), str(r))
            created_id = r.get('id')
            created_serial = r.get('serial')
            ck('a real write answers with the archive revision it produced',
               isinstance(created_serial, int) and created_serial > 0, str(r))

            # --- the publisher: a push rebuilds the site this host serves,
            #     complete and atomically swapped in, within seconds ---
            def published(relpath, timeout=45):
                end = time.time() + timeout
                page = td / 'site' / 'current' / relpath
                while time.time() < end:
                    if page.exists():
                        return True
                    time.sleep(0.3)
                return False

            ck('the new run has a live page moments after the submit',
               published(f'runs/{created_id}/index.html'),
               (td / 'log').read_text()[-1500:])
            ck('the served site is a complete build, not a fragment',
               (td / 'site' / 'current' / 'index.html').exists()
               and (td / 'site' / 'current' / 'assets' / 'app.js').exists()
               and (td / 'site' / 'current' / '404.html').exists())
            ck('current is an atomic symlink into the build directory',
               (td / 'site' / 'current').is_symlink()
               and os.readlink(td / 'site' / 'current').startswith('build-'))
            stamp = json.loads((td / 'site' / 'current' / 'assets' /
                                'buildstamp.json').read_text())
            ck('the served buildstamp reaches the write that made it',
               isinstance(stamp.get('serial'), int)
               and stamp['serial'] >= (created_serial or 0), str(stamp))

            # --- unclassified: no goal, needs a description, never verified ---
            c, r, _ = call(U + '/api/submit', dict(sub, goal='unclassified'), files)
            ck('unclassified needs description', c == 400 and 'goal_description' in r.get('error', ''))
            c, r, _ = call(U + '/api/submit',
                           dict(sub, goal='unclassified',
                                goal_description='beats the game with the lid closed'),
                           uniq_files())
            ck('unclassified dry-run ok', c == 200 and r['run']['category'] == {'goal': 'unclassified'}
               and r['run']['goalDescription'], str(r))
            unclsub = dict(sub, goal='unclassified',
                           goal_description='beats the game with the lid closed',
                           content_warnings='photosensitivity')
            del unclsub['dry_run']
            c, r, _ = call(U + '/api/submit', unclsub, files)
            ck('unclassified created for real', c == 200 and r.get('ok'), str(r))
            uncl_id = r.get('id')
            c, r, _ = call(U + '/api/verify', {'key': KEY, 'user': 'watcher', 'run': uncl_id})
            ck('verify on unclassified rejected', c == 400 and 'Unclassified' in r.get('error', ''))

            # --- the preview is the published renderer (issue #30) ---
            c, r, _ = call(U + '/api/preview', {'notes': '!!Head\r\n*one\r\n**two\r\nsee [M900010]'})
            ck('the preview renders the dialect exactly as the site does',
               c == 200 and '<h3>Head</h3>' in r['html'] and '<li>two\n</li></ul>' in r['html']
               and 'href="/runs/M900010/"' in r['html'], str(r)[:200])

            # --- likes: one per member, never own runs, imported allowed ---
            # --- the categories feed: fresh from the checkout, no CDN lag ---
            c, r, _ = call(U + '/api/categories?game=nes/pinball', method='GET')
            ck('the submit form reads categories from the archivist',
               c == 200 and any(o['key'] == '100k-glitched'
                                for d_ in r['dimensions'] for o in d_['options']),
               str(r)[:200])
            c, r, _ = call(U + '/api/categories?game=nes/nosuchgame', method='GET')
            ck('unknown game categories are a 404', c == 404, str(r))

            # --- the visit tally: public, anonymous, not an archive fact ---
            c, r, _ = call(U + '/api/visit', {'run': 'not-an-id'})
            ck('a visit needs a run id', c == 400, str(r))
            c, r, _ = call(U + '/api/visit', {'run': 'M999999'})
            ck('a visit to an unknown run is a 404', c == 404, str(r))
            c, r, _ = call(U + '/api/visit', {'run': uncl_id})
            ck('the first visit counts', c == 200 and r['visits'] == 1, str(r))
            c, r, _ = call(U + '/api/visit', {'run': uncl_id})
            ck('and the tally climbs, no auth needed',
               c == 200 and r['visits'] == 2, str(r))

            c, r, _ = call(U + '/api/like', {'key': KEY, 'user': 'TestAuthor', 'run': uncl_id})
            ck('self-like rejected', c == 400)
            c, r, _ = call(U + '/api/like', {'key': KEY, 'user': 'fan', 'run': uncl_id})
            ck('like recorded', c == 200 and r['likes'] == 1 and r['liked'] is True, str(r))
            # the same star takes it back, and taking it back leaves nothing:
            # no tombstone, no log line, as if it never happened
            c, r, _ = call(U + '/api/like', {'key': KEY, 'user': 'fan', 'run': uncl_id})
            ck('a second press takes the like back',
               c == 200 and r['liked'] is False and r['likes'] == 0, str(r))
            subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            import glob as _glob
            uncl_json = next(iter(_glob.glob(str(work / 'games' / '*' / '*' / 'runs'
                                                 / uncl_id / 'run.json'))))
            udoc = json.loads(pathlib.Path(uncl_json).read_text())
            ck('the unlike leaves no trace in the record',
               all(l['user'].lower() != 'fan' for l in udoc.get('likes', []))
               and 'unlike' not in json.dumps(udoc).lower(), str(udoc.get('likes')))
            c, r, _ = call(U + '/api/like', {'key': KEY, 'user': 'fan', 'run': uncl_id})
            ck('and liking again after works', c == 200 and r['liked'] is True, str(r))
            c, r, _ = call(U + '/api/like', {'key': KEY, 'user': 'fan', 'run': 'M7229'})
            ck('imported run likeable', c == 200 and r['likes'] == 1, str(r))

            # --- reports: unique ids, expert resolution ---
            c, r, _ = call(U + '/api/report', {'key': KEY, 'user': 'fan', 'run': uncl_id,
                                               'kind': 'nonsense'})
            ck('bad report kind rejected', c == 400)
            c, r, _ = call(U + '/api/report', {'key': KEY, 'user': 'fan', 'run': uncl_id,
                                               'kind': 'missing-content-warnings',
                                               'details': 'Has flashing not flagged.'})
            # ids are global and the seeded archive may already carry reports,
            # so assert the sequence rather than a fixed number
            ck('report filed', c == 200 and re.fullmatch(r'R\d+', r.get('report', '')), str(r))
            first_rep = int(r['report'][1:]) if c == 200 else 0
            c, r, _ = call(U + '/api/report', {'key': KEY, 'user': 'fan2', 'run': 'M900010',
                                               'kind': 'other', 'details': 'Second report.'})
            ck('report ids increment globally',
               c == 200 and r.get('report') == f'R{first_rep + 1}', str(r))
            c, r, _ = call(U + '/api/report/resolve', {'key': KEY, 'expert': 'nobody',
                                                       'run': uncl_id, 'report': str(first_rep),
                                                       'outcome': 'resolved', 'resolution': 'x'})
            ck('non-expert resolve rejected', c == 403)
            c, r, _ = call(U + '/api/report/resolve', {'key': KEY, 'expert': 'eien86',
                                                       'run': uncl_id, 'report': str(first_rep),
                                                       'outcome': 'resolved',
                                                       'resolution': 'Author added the flag.'})
            ck('expert resolves report', c == 200 and r['status'] == 'resolved', str(r))
            c, r, _ = call(U + '/api/report/resolve', {'key': KEY, 'expert': 'eien86',
                                                       'run': uncl_id, 'report': str(first_rep),
                                                       'outcome': 'dismissed', 'resolution': 'x'})
            ck('re-resolve rejected', c == 400)

            # --- CSRF: cookie-authed writes from a foreign origin are refused ---
            # (cookie built later in the SSO section; checked there)

            # --- auth ---
            c, r, _ = call(U + '/api/verify', {'run': 'M900010'})
            ck('unauthenticated act rejected', c == 403)
            c, r, _ = call(U + '/api/reproduce', {'key': 'wrong', 'user': 'x', 'run': 'M900010'})
            ck('bad key rejected', c == 403)

            # --- reproduce ---
            c, r, _ = call(U + '/api/reproduce', {'key': KEY, 'user': 'TestAuthor', 'run': 'M900010'},
                           {'screenshot': ('end.png', PNG)})
            ck('self-act rejected', c == 400 and 'own run' in r.get('error', ''))
            c, r, _ = call(U + '/api/reproduce', {'key': KEY, 'user': 'helper', 'run': 'M900010'})
            ck('screenshot required', c == 400)
            c, r, _ = call(U + '/api/reproduce', {'key': KEY, 'user': 'helper', 'run': 'M900010'},
                           {'screenshot': ('end.png', b'PK\x03\x04junk')})
            ck('fake png rejected', c == 400)
            c, r, _ = call(U + '/api/reproduce',
                           {'key': KEY, 'user': 'helper', 'run': 'M900010',
                            'emulator': 'BizHawk 2.11', 'notes': 'Synced.'},
                           {'screenshot': ('end.png', PNG)})
            ck('reproduction recorded', c == 200 and r['status']['reproduced'] == 'community', str(r))
            c, r, _ = call(U + '/api/reproduce', {'key': KEY, 'user': 'helper', 'run': 'M900010'},
                           {'screenshot': ('end.png', PNG)})
            ck('duplicate reproduction rejected', c == 400)

            # --- video-only runs: the encode is the run ---
            vsub = {'key': KEY, 'submitter': 'TestAuthor', 'game': 'nes/pinball',
                    'goal': '100k-glitched', 'authors': 'TestAuthor', 'consent': 'yes',
                    'video_only': '1', 'encode': 'https://youtu.be/videoonly001'}
            c, r, _ = call(U + '/api/submit', dict(vsub), files={})
            ck('a video-only run needs its stated time', c == 400
               and 'time' in r.get('error', ''), str(r))
            c, r, _ = call(U + '/api/submit', dict(vsub, time='1:23.456'), files={})
            ck('a video-only run is archived', c == 200, str(r)[:200])
            vo_id = r.get('id') or r.get('run')
            subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            vo = json.loads(next(iter(work.glob(f'games/*/*/runs/{vo_id}/run.json')))
                            .read_text())
            ck('it carries its stated duration and no movie',
               vo.get('videoOnly') is True and abs(vo['duration'] - 83.456) < 0.001
               and 'movie' not in vo, str(vo)[:200])
            ck('reproduction and console are marked not applicable',
               vo['status']['reproduced'] == 'not-applicable'
               and vo['status']['console'] == 'not-applicable', str(vo['status']))
            c, r, _ = call(U + '/api/reproduce', {'key': KEY, 'user': 'Rep',
                                                  'run': vo_id, 'dry_run': '1'},
                           files={'screenshot': ('s.png', PNG)})
            ck('a video-only run cannot be reproduced', c == 400
               and 'video-only' in r.get('error', ''), str(r))
            c, r, _ = call(U + '/api/console-verify',
                           {'key': KEY, 'user': 'Metal', 'run': vo_id,
                            'proof': 'https://youtu.be/x', 'hardware': 'NES',
                            'dry_run': '1'})
            ck('nor console-verified', c == 400 and 'video-only' in r.get('error', ''),
               str(r))
            c, r, _ = call(U + '/api/verify', {'key': KEY, 'user': 'watcher2',
                                               'run': vo_id})
            ck('one verification still ranks a video-only run', c == 200
               and r['status']['verified'] == 'provisional', str(r))
            c, r, _ = call(U + '/api/submit', dict(vsub, time='1:23.456',
                                                   submitter='newuser',
                                                   authors='newuser'), files={})
            ck('the same encode twice is the same run twice', c == 409
               and 'same encode' in r.get('error', ''), str(r))

            # --- expert edits: anything in the jurisdiction, all of it logged ---
            c, r, _ = call(U + '/api/expert/edit',
                           {'key': KEY, 'expert': 'TestAuthor', 'kind': 'run',
                            'target': vo_id, 'field': 'duration', 'value': '2:00.000',
                            'reason': 'a member trying an expert edit'})
            ck('a member cannot expert-edit', c == 403, str(r))
            c, r, _ = call(U + '/api/expert/edit',
                           {'key': KEY, 'expert': 'groupexpert', 'kind': 'run',
                            'target': vo_id, 'field': 'authors', 'value': 'me',
                            'reason': 'trying to change who made a thing'})
            ck('the author list is never an edit', c == 400
               and 'never edited by anybody but its author' in r.get('error', '')
               or c == 400, str(r))
            c, r, _ = call(U + '/api/expert/edit',
                           {'key': KEY, 'expert': 'groupexpert', 'kind': 'run',
                            'target': vo_id, 'field': 'duration', 'value': '2:00.000',
                            'reason': 'the stated time was wrong by half a minute'})
            ck('an expert corrects the stated time', c == 200
               and r['field'] == 'duration', str(r))
            subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            vo2 = json.loads(next(iter(work.glob(f'games/*/*/runs/{vo_id}/run.json')))
                             .read_text())
            ck('the corrected time is on the record',
               abs(vo2['duration'] - 120.0) < 0.001, str(vo2.get('duration')))
            c, r, _ = call(U + '/api/expert/edit',
                           {'key': KEY, 'expert': 'groupexpert', 'kind': 'run',
                            'target': 'M900010', 'field': 'notes',
                            'value': 'Corrected notes, by an expert, on the record.',
                            'reason': 'the notes described the wrong version'})
            ck('an expert may edit member notes now, logged', c == 200, str(r))
            c, r, _ = call(U + '/api/expert/edit',
                           {'key': KEY, 'expert': 'groupexpert', 'kind': 'game',
                            'target': 'nes/pinball', 'field': 'title',
                            'value': 'Pinball (NES)',
                            'reason': 'disambiguating from the DOS game'})
            ck('an expert renames a game', c == 200 and r['to'] == 'Pinball (NES)',
               str(r))
            c, r, _ = call(U + '/api/expert/edit',
                           {'key': KEY, 'expert': 'groupexpert', 'kind': 'game',
                            'target': 'nes/pinball', 'field': 'thumbnail',
                            'reason': 'a face for the game card'},
                           files={'thumbnail': ('face.png', PNG)})
            ck('an expert sets the game thumbnail', c == 200 and r['to'] == 'thumb.png',
               str(r))
            # the game properties (#44)
            for field, bad in (('released', '1989-13'), ('released', '89'),
                               ('released', '1989-02-30'), ('discord', 'https://example.com/x'),
                               ('website', 'ftp://x'), ('unofficial', 'maybe')):
                c, r, _ = call(U + '/api/expert/edit',
                               {'key': KEY, 'expert': 'groupexpert', 'kind': 'game',
                                'target': 'nes/pinball', 'field': field, 'value': bad,
                                'reason': 'testing a refusal, on the record'})
                ck(f'game {field} refuses {bad!r} (#44)', c == 400, str(r))
            for field, good, stored in (('released', '1989-03', '1989-03'),
                                        ('unofficial', 'yes', True),
                                        ('discord', 'https://discord.gg/abc123', 'https://discord.gg/abc123'),
                                        ('website', 'https://popruns.github.io/', 'https://popruns.github.io/'),
                                        ('rta', 'https://www.speedrun.com/pinball', 'https://www.speedrun.com/pinball')):
                c, r, _ = call(U + '/api/expert/edit',
                               {'key': KEY, 'expert': 'groupexpert', 'kind': 'game',
                                'target': 'nes/pinball', 'field': field, 'value': good,
                                'reason': 'filling in the game properties'})
                gj = json.loads((work / 'games/nes/pinball/game.json').read_text())
                ck(f'an expert sets game {field} (#44)', c == 200 and gj.get(field) == stored,
                   f'{c} {r} {gj.get(field)!r}')
            c, r, _ = call(U + '/api/expert/edit',
                           {'key': KEY, 'expert': 'groupexpert', 'kind': 'game',
                            'target': 'nes/pinball', 'field': 'unofficial', 'value': 'no',
                            'reason': 'it is the official cartridge after all'})
            gj = json.loads((work / 'games/nes/pinball/game.json').read_text())
            ck('clearing a game property removes it from the record (#44)',
               c == 200 and 'unofficial' not in gj, str(r))
            c, r, _ = call(U + '/api/expert/edit',
                           {'key': KEY, 'expert': 'groupexpert', 'kind': 'game',
                            'target': 'nes/pinball', 'field': 'released', 'value': '1989-03',
                            'reason': 'the same value again, should be refused'})
            ck('an unchanged game property is refused (#44)', c == 400, str(r))
            c, r, _ = call(U + '/api/expert/edit',
                           {'key': KEY, 'expert': 'groupexpert', 'kind': 'category',
                            'target': 'nes/pinball:100k-glitched', 'field': 'rule',
                            'value': 'Reach 100,000 points using the wrap glitch.',
                            'reason': 'the rule finally written down'})
            ck('an expert rewords a category rule', c == 200
               and 'wrap glitch' in r['to'], str(r))
            c, r, _ = call(U + '/api/expert/edit',
                           {'key': KEY, 'expert': 'nobody9', 'kind': 'category',
                            'target': 'nes/pinball:100k-glitched', 'field': 'label',
                            'value': 'x', 'reason': 'should not be allowed at all'})
            ck('category edits need a covering expert', c == 403, str(r))
            subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            gnow = json.loads((work / 'games/nes/pinball/game.json').read_text())
            ck('the thumbnail is a stored fact beside the file',
               gnow.get('thumbnail') == 'thumb.png'
               and (work / 'games/nes/pinball/thumb.png').exists())
            cnow = json.loads((work / 'games/nes/pinball/categories.json').read_text())
            ck('the reworded rule is in the record',
               any('wrap glitch' in o.get('rule', '') for d in cnow['dimensions']
                   for o in d['options']))
            elog = json.loads((work / 'edits.json').read_text())['events']
            ck('game and category edits are logged',
               any(e['kind'] == 'game' and e['field'] == 'thumbnail' for e in elog)
               and any(e['kind'] == 'category' for e in elog), str(elog[-3:]))
            # the author's own revision joins the same history
            c, r, _ = call(U + '/api/edit',
                           {'key': KEY, 'user': 'TestAuthor', 'run': 'M900010',
                            'emulator': 'FCEUX 2.6.5', 'completed': '2020-04-01'})
            ck('an author revises their own run', c == 200, str(r))
            # content disclosures from the edit form (#49): set, then cleared
            c, r, _ = call(U + '/api/edit',
                           {'key': KEY, 'user': 'TestAuthor', 'run': 'M900010',
                            'content_warnings_set': '1',
                            'content_warnings': ['photosensitivity', 'strong-language']})
            rj = json.loads((work / 'games/nes/pinball/runs/M900010/run.json').read_text())
            ck('an author sets content warnings from the edit form (#49)',
               c == 200 and 'contentWarnings' in r['changed']
               and rj.get('contentWarnings') == ['photosensitivity', 'strong-language'], str(r))
            c, r, _ = call(U + '/api/edit',
                           {'key': KEY, 'user': 'TestAuthor', 'run': 'M900010',
                            'content_warnings_set': '1', 'content_warnings': 'nonsense'})
            ck('an unknown content warning is refused', c == 400, str(r))
            c, r, _ = call(U + '/api/edit',
                           {'key': KEY, 'user': 'TestAuthor', 'run': 'M900010',
                            'content_warnings_set': '1'})
            rj = json.loads((work / 'games/nes/pinball/runs/M900010/run.json').read_text())
            ck('no box ticked clears the warnings', c == 200 and 'contentWarnings' not in rj, str(r))
            # the files list, revised from the edit form, whole
            c, r, _ = call(U + '/api/edit',
                           {'key': KEY, 'user': 'TestAuthor', 'run': 'M900010', 'files_set': '1',
                            'file_name': ['a.nes', 'b.nes'], 'file_sha1': ['', 'b' * 40]})
            rj = json.loads((work / 'games/nes/pinball/runs/M900010/run.json').read_text())
            ck('an author revises the file list',
               c == 200 and 'files' in r['changed']
               and rj['contract']['files'] == [{'name': 'a.nes'}, {'name': 'b.nes', 'sha1': 'b' * 40}], str(r))
            c, r, _ = call(U + '/api/edit',
                           {'key': KEY, 'user': 'TestAuthor', 'run': 'M900010', 'files_set': '1'})
            rj = json.loads((work / 'games/nes/pinball/runs/M900010/run.json').read_text())
            ck('an empty list clears the files', c == 200 and 'files' not in rj['contract'], str(r))
            elog = json.loads((work / 'edits.json').read_text())['events']
            ck('warning edits are logged with before and after',
               any(e['field'] == 'contentWarnings' and e['from'] == 'photosensitivity, strong-language'
                   and e['to'] == '' for e in elog), str(elog[-2:]))
            c, r, _ = call(U + '/api/edit',
                           {'key': KEY, 'user': 'TestAuthor', 'run': 'M900010',
                            'encode': 'https://youtu.be/X7oXnw7X0kQ'})
            ck('an author updates the encode link', c == 200 and 'encode' in r.get('changed', []), str(r))
            subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            rdoc = json.loads((work / 'games/nes/pinball/runs/M900010/run.json').read_text())
            ck('the replaced encode is saved in the archive record',
               rdoc['encodes'][0]['url'] == 'https://youtu.be/X7oXnw7X0kQ', str(rdoc['encodes']))
            c, r, _ = call(U + '/api/edit',
                           {'key': KEY, 'user': 'TestAuthor', 'run': 'M900010',
                            'completed': '2099-01-01'})
            ck('an author cannot finish a run in the future', c == 400, str(r))
            subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            elog0 = json.loads((work / 'edits.json').read_text())['events']
            ck("the author's revision is in the history, needing no justification",
               any(e['by'] == 'TestAuthor' and e['field'] == 'emulator'
                   and e['reason'] == "The author's own revision." for e in elog0),
               str(elog0[-2:]))
            ck('every edit is in the log with who, from, to and why',
               len(elog) >= 3 and all(e['by'] and e['reason'] and 'to' in e
                                      for e in elog)
               and any(e['field'] == 'duration' for e in elog)
               and any(e['field'] == 'title' and e['to'] == 'Pinball (NES)'
                       for e in elog), str(elog))

            # --- verify ---
            c, r, _ = call(U + '/api/verify', {'key': KEY, 'user': 'watcher', 'run': 'M900010'})
            ck('verification -> provisional', c == 200 and r['status']['verified'] == 'provisional')
            c, r, _ = call(U + '/api/verify', {'key': KEY, 'user': 'second', 'run': 'M900010'})
            ck('a second community verification stays provisional',
               c == 200 and r['status']['verified'] == 'provisional')
            c, r, _ = call(U + '/api/verify', {'key': KEY, 'user': 'xtwo', 'run': 'M7229'})
            ck('imported act rejected', c == 400 and 'Imported' in r.get('error', ''))

            # --- cases: upheld path ---
            c, r, _ = call(U + '/api/case/open', {'key': KEY, 'user': 'disputer',
                                                 'run': 'M900010', 'reason': 'Desyncs at 3:20.'})
            ck('case opened', c == 200 and r['case'] == 1)
            c, r, _ = call(U + '/api/case/open', {'key': KEY, 'user': 'other',
                                                 'run': 'M900010', 'reason': 'dup'})
            ck('second open case rejected', c == 400)
            c, r, _ = call(U + '/api/case/vote', {'key': KEY, 'user': 'random',
                                                 'run': 'M900010', 'case': '1', 'reaffirm': '1'})
            ck('non-verifier vote rejected', c == 400)
            c, r, _ = call(U + '/api/case/vote', {'key': KEY, 'user': 'watcher',
                                                 'run': 'M900010', 'case': '1', 'reaffirm': '0'})
            ck('withdrawal upholds (majority impossible)',
               c == 200 and r['case_status'] == 'upheld' and r['status']['verified'] == 'none', str(r))

            # --- cases: closed path (fresh verifiers) ---
            call(U + '/api/verify', {'key': KEY, 'user': 'alice', 'run': 'M900010'})
            call(U + '/api/verify', {'key': KEY, 'user': 'bob', 'run': 'M900010'})
            c, r, _ = call(U + '/api/case/open', {'key': KEY, 'user': 'doubter',
                                                 'run': 'M900010', 'reason': 'Round two.'})
            ck('case 2 opened', c == 200 and r['case'] == 2)
            call(U + '/api/case/vote', {'key': KEY, 'user': 'alice', 'run': 'M900010',
                                        'case': '2', 'reaffirm': '1'})
            c, r, _ = call(U + '/api/case/vote', {'key': KEY, 'user': 'bob', 'run': 'M900010',
                                                  'case': '2', 'reaffirm': '1'})
            ck('majority reaffirm closes', c == 200 and r['case_status'] == 'closed'
               and r['status']['verified'] == 'provisional')

            # --- expert acts ---
            c, r, _ = call(U + '/api/invalidate', {'key': KEY, 'expert': 'nobody',
                                                   'run': 'M900010', 'kind': 'verification',
                                                   'target': 'alice', 'reason': 'x'})
            ck('non-expert invalidate rejected', c == 403)
            c, r, _ = call(U + '/api/invalidate', {'key': KEY, 'expert': 'eien86',
                                                   'run': 'M900010', 'kind': 'verification',
                                                   'target': 'alice', 'reason': 'Bad encode.'})
            ck('expert invalidation', c == 200 and r['status']['verified'] == 'provisional', str(r))
            # decision 2026-08-16: one verification per member per run, spent
            # whether or not it survived. The invalidated member cannot retry;
            # anybody else still can.
            c, r, _ = call(U + '/api/verify', {'key': KEY, 'user': 'alice', 'run': 'M900010'})
            ck('an invalidated verifier cannot verify again', c == 400, str(r))
            c, r, _ = call(U + '/api/verify', {'key': KEY, 'user': 'freshpair', 'run': 'M900010'})
            ck('another member may still verify', c == 200
               and r['status']['verified'] == 'provisional', str(r))
            # the gate's top tier: a covering expert verifies, and the run is
            # confirmed, permanently
            c, r, _ = call(U + '/api/verify', {'key': KEY, 'user': 'groupexpert',
                                               'run': 'M900010'})
            ck('an expert verification confirms the run', c == 200
               and r['status']['verified'] == 'confirmed', str(r))
            subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            vdoc = json.loads((work / 'games/nes/pinball/runs/M900010/run.json').read_text())
            ck('and the act itself carries the expert stamp',
               any(v.get('expert') and v['user'] == 'groupexpert'
                   for v in vdoc['verifications']), str(vdoc['verifications']))

            # --- the same movie may not be archived twice ---
            dupsub = dict(sub, game='nes/pinball', goal='100k-glitched')
            del dupsub['dry_run']
            dup_bytes = uniq_bk2()
            dupfiles = {'movie': ('dup.bk2', dup_bytes)}
            c, r, _ = call(U + '/api/submit', dict(dupsub, completed='2031-01-01'),
                           {'movie': ('dup.bk2', dup_bytes)})
            ck('a completion date in the future is refused',
               c == 400 and 'future' in r.get('error', ''), str(r)[:140])
            c, r, _ = call(U + '/api/submit', dict(dupsub, completed='2021-10-26'),
                           dupfiles)
            first_dup = r.get('id') if c == 200 else None
            ck('a fresh movie is archived', c == 200, str(r)[:140])
            want_ = f'/thumbs/{first_dup}'
            end_ = time.time() + 12
            while time.time() < end_ and not any('[img: ' in m and want_ in m
                                                 for m in DISCORD_MSGS):
                time.sleep(1)
            ck('the new-movie notification carries the run thumbnail as an embed',
               first_dup and any('[img: ' in m and want_ in m for m in DISCORD_MSGS),
               str(DISCORD_MSGS[-2:]))
            c, r, _ = call(U + '/api/submit', dupsub, dupfiles)
            ck('the same movie file is refused',
               c == 409 and first_dup and first_dup in r.get('error', ''), str(r)[:180])
            # re-saved: different bytes, same game, category, frames and authors
            import io as _io2, zipfile as _zip2
            buf2 = _io2.BytesIO(dup_bytes)
            with _zip2.ZipFile(buf2, 'a') as z:
                z.writestr('Subtitles.txt', 'saved again by another build')
            c, r, _ = call(U + '/api/submit', dupsub, {'movie': ('resaved.bk2', buf2.getvalue())})
            ck('the same run saved again is refused',
               c == 409 and 'saved again' in r.get('error', ''), str(r)[:180])
            # a genuinely different run in the same category is fine
            import io as _io, zipfile as _zip
            buf = _io.BytesIO()
            with _zip.ZipFile(buf, 'w') as z:
                z.writestr('Header.txt', 'Platform NES\nrerecordCount 9\n')
                z.writestr('Input Log.txt', 'LogKey:#Reset|Power|\n' + '|..|........|\n' * 55)
            c, r, _ = call(U + '/api/submit', dict(dupsub, authors='TestAuthor, helper2'),
                           {'movie': ('other.bk2', buf.getvalue())})
            ck('a different run is still accepted', c == 200, str(r)[:180])

            # --- withdrawal: out of the listings, never erased ---
            c, r, _ = call(U + '/api/withdraw', {'key': KEY, 'user': 'stranger',
                                                 'run': first_dup, 'reason': 'not mine'})
            ck('a stranger cannot withdraw a run', c == 403, str(r)[:120])
            c, r, _ = call(U + '/api/withdraw', {'key': KEY, 'user': 'TestAuthor',
                                                 'run': first_dup})
            ck('withdrawal needs a public reason', c == 400, str(r)[:120])
            c, r, _ = call(U + '/api/withdraw', {'key': KEY, 'user': 'TestAuthor',
                                                 'run': first_dup,
                                                 'reason': 'Submitted twice by accident.'})
            ck('an author can withdraw their run',
               c == 200 and r['withdrawn']['role'] == 'author', str(r)[:160])
            c, r, _ = call(U + '/api/withdraw', {'key': KEY, 'user': 'TestAuthor',
                                                 'run': first_dup, 'reason': 'again'})
            ck('a withdrawn run cannot be withdrawn twice', c == 400, str(r)[:120])
            c, r, _ = call(U + '/api/like', {'key': KEY, 'user': 'fan', 'run': first_dup})
            ck('a withdrawn run takes no further acts', c == 400, str(r)[:120])
            c, r, _ = call(U + '/api/withdraw', {'key': KEY, 'user': 'eien86',
                                                 'run': 'M900010',
                                                 'reason': 'Expert cleanup.', 'dry_run': '1'})
            ck('withdrawing is voluntary: even a covering expert may not '
               '(they delete instead)', c == 403, str(r)[:160])

            # --- console verification: the optional third signal ---
            cv = {'key': KEY, 'user': 'ConsoleFan', 'run': 'M900010'}
            c, r, _ = call(U + '/api/console-verify', dict(cv, proof='not-a-url'))
            ck('console verification needs a real proof link', c == 400, str(r))
            c, r, _ = call(U + '/api/console-verify',
                           dict(cv, user='TestAuthor', proof='https://example.com/rec'))
            ck('authors cannot console-verify their own run', c == 400, str(r))
            c, r, _ = call(U + '/api/console-verify',
                           dict(cv, proof='https://example.com/rec',
                                hardware='NES + Everdrive'))
            ck('console verification recorded',
               c == 200 and r.get('consoleVerifications') == 1, str(r))
            c, r, _ = call(U + '/api/console-verify',
                           dict(cv, proof='https://example.com/again'))
            ck('one console verification per member', c == 400, str(r))
            c, r, _ = call(U + '/api/verify', {'key': KEY, 'user': 'ConsoleFan',
                                               'run': 'M900010', 'dry_run': '1'})
            ck('console verification does not spend the normal verification',
               c == 200, str(r))
            c, r, _ = call(U + '/api/invalidate', {'key': KEY, 'expert': 'eien86',
                                                   'run': 'M900010', 'kind': 'console',
                                                   'target': 'ConsoleFan',
                                                   'reason': 'Recording shows a different run.'})
            ck('an expert can invalidate a console verification', c == 200, str(r))

            # --- author edits + role/expert notes ---
            c, r, _ = call(U + '/api/edit', {'key': KEY, 'user': 'stranger', 'run': 'M900010',
                                             'emulator': 'X'})
            ck('non-author edit rejected', c == 403)
            c, r, _ = call(U + '/api/edit', {'key': KEY, 'user': 'TestAuthor', 'run': 'M900010',
                                             'emulator': 'BizHawk 2.12',
                                             'notes': 'Updated notes.'})
            ck('author edit ok', c == 200 and set(r['changed']) == {'notes', 'emulator'}, str(r))
            # issue #38: the form sends every field; only real differences count,
            # and a browser's CRLF in the textarea is not a difference
            c, r, _ = call(U + '/api/edit', {'key': KEY, 'user': 'TestAuthor', 'run': 'M900010',
                                             'emulator': 'BizHawk 2.12',
                                             'notes': 'Updated notes.\r\n'})
            ck('resending identical values changes nothing',
               c == 400 and 'nothing to change' in r.get('error', ''), str(r))
            c, r, _ = call(U + '/api/edit', {'key': KEY, 'user': 'TestAuthor', 'run': 'M900010',
                                             'emulator': 'BizHawk 2.12',
                                             'notes': 'Updated notes.\r\n',
                                             'completed': '2020-01-02'})
            ck('only the field that differs is recorded as changed',
               c == 200 and r['changed'] == ['completed'], str(r))
            c, r, _ = call(U + '/api/edit', {'key': KEY, 'user': 'TestAuthor',
                                             'run': 'M900010', 'metric_score': '123'})
            ck('a metric the category does not state is refused',
               c == 400 and 'states no metric' in r.get('error', ''), str(r))
            psub = dict(sub, game='nes/pinball', goal='pacifist',
                        metric_score='1250')
            del psub['dry_run']
            c, r, _ = call(U + '/api/submit', psub, uniq_files())
            ck('a pacifist run archives with its stated score', c == 200, str(r))
            pac_id = r.get('id')
            c, r, _ = call(U + '/api/edit', {'key': KEY, 'user': 'TestAuthor',
                                             'run': pac_id, 'metric_score': '',
                                             'emulator': 'BizHawk 2.12'})
            ck('an empty metric field leaves the value untouched',
               c == 200 and r['changed'] == ['emulator'], str(r))
            c, r, _ = call(U + '/api/edit', {'key': KEY, 'user': 'TestAuthor',
                                             'run': pac_id, 'metric_score': '1300'})
            ck('an author restates their category metric',
               c == 200 and r['changed'] == ['metric:score'], str(r))
            subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            prj = json.loads(next(work.glob(f'games/*/*/runs/{pac_id}/run.json'))
                             .read_text())
            ck('the restated value is the record now',
               prj['metrics'] == {'score': 1300.0}, str(prj.get('metrics')))
            c, r, _ = call(U + '/api/edit', {'key': KEY, 'user': 'TestAuthor',
                                             'run': 'M900010', 'tools': 'whatever'})
            ck('the retired tools field changes nothing',
               c == 400 and 'nothing to change' in r.get('error', ''), str(r))
            c, r, _ = call(U + '/api/edit', {'key': KEY, 'user': 'TestAuthor', 'run': 'M900010',
                                             'authors': 'TestAuthor, helper'})
            ck('author edit rejects roster clash', c == 400 and 'helper' in r.get('error', ''))
            c, r, _ = call(U + '/api/edit', {'key': KEY, 'user': 'TestAuthor', 'run': 'M900010',
                                             'authors': 'TestAuthor, NewFriend'})
            ck('author list editable', c == 200 and 'authors' in r['changed'], str(r))
            # the same panel serves a covering expert, reason required
            c, r, _ = call(U + '/api/edit', {'key': KEY, 'user': 'eien86',
                                             'run': 'M900010', 'emulator': 'FCEUX 2.6'})
            ck('an expert edit without a public reason is refused',
               c == 400 and 'reason' in r.get('error', ''), str(r))
            c, r, _ = call(U + '/api/edit', {'key': KEY, 'user': 'eien86',
                                             'run': 'M900010', 'emulator': 'FCEUX 2.6',
                                             'reason': 'The stated core was wrong.'})
            ck('a covering expert edits through the same panel',
               c == 200 and r['changed'] == ['emulator'], str(r))
            c, r, _ = call(U + '/api/edit', {'key': KEY, 'user': 'eien86',
                                             'run': 'M900010', 'authors': 'eien86',
                                             'reason': 'Trying to take the credit.'})
            ck("an author list is never an expert's edit", c == 403, str(r))
            # supplementary files: the authors' own uploads, after the fact
            c, r, _ = call(U + '/api/edit', {'key': KEY, 'user': 'TestAuthor',
                                             'run': 'M900010'},
                           {'attachments': ('extra.txt', b'supplementary data')})
            ck('an author uploads a supplementary file after the fact',
               c == 200 and r['changed'] == ['attachments'], str(r))
            subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            rj_ = json.loads((work / 'games/nes/pinball/runs/M900010/run.json').read_text())
            ck('the file joins the run record and the tree',
               any(a['file'] == 'attachments/extra.txt'
                   and a['role'] == 'supplementary'
                   for a in rj_.get('attachments', []))
               and (work / 'games/nes/pinball/runs/M900010/attachments/extra.txt').exists(),
               str(rj_.get('attachments')))
            c, r, _ = call(U + '/api/edit', {'key': KEY, 'user': 'TestAuthor',
                                             'run': 'M900010'},
                           {'attachments': ('extra.txt', b'same name again')})
            ck('the same attachment name is not taken twice', c == 400, str(r))
            c, r, _ = call(U + '/api/edit', {'key': KEY, 'user': 'eien86',
                                             'run': 'M900010',
                                             'reason': 'An expert bearing gifts.'},
                           {'attachments': ('gift.txt', b'not mine to add')})
            ck("supplementary files are the authors' own",
               c == 403 and 'authors' in r.get('error', ''), str(r))
            c, r, _ = call(U + '/api/note', {'key': KEY, 'user': 'helper', 'run': 'M900010',
                                             'role': 'reproducer', 'notes': 'x'})
            ck('the retired community-notes endpoint is gone', c == 404, str(r)[:80])

            # --- movie-format attachments allowed, binary text rejected ---
            c, r, _ = call(U + '/api/submit', sub,
                           dict(uniq_files(),
                                attachments=('verify.bk2', b'PK\x03\x04binarymovie')))
            ck('movie attachment accepted', c == 200 and r['run']['attachments'], str(r))
            c, r, _ = call(U + '/api/submit', sub,
                           {'movie': ('t.bk2', BK2),
                            'attachments': ('data.txt', b'\xff\xfebinary')})
            ck('binary text attachment rejected', c == 400)

            # --- appointing experts: downward, in the open ---
            c, r, _ = call(U + '/api/expert/appoint',
                           {'key': KEY, 'expert': 'groupexpert', 'user': 'TestAuthor',
                            'scope': 'site', 'reason': 'trying to appoint upward'})
            ck('a system expert cannot appoint a site expert', c == 403, str(r))
            c, r, _ = call(U + '/api/expert/appoint',
                           {'key': KEY, 'expert': 'groupexpert', 'user': 'TestAuthor',
                            'scope': 'nes', 'reason': 'trying to appoint a peer'})
            ck('an expert cannot clone their own scope', c == 403, str(r))
            c, r, _ = call(U + '/api/expert/appoint',
                           {'key': KEY, 'expert': 'eien86', 'user': 'TestAuthor',
                            'scope': 'nes/nosuchgame', 'reason': 'a scope that is not real'})
            ck('a scope has to name something real', c == 400 and 'no such scope' in r.get('error', ''),
               str(r))
            c, r, _ = call(U + '/api/expert/appoint',
                           {'key': KEY, 'expert': 'eien86', 'user': 'TestAuthor',
                            'scope': 'nes/pinball', 'reason': 'short'})
            ck('an appointment needs a public reason', c == 400, str(r))
            c, r, _ = call(U + '/api/expert/appoint',
                           {'key': KEY, 'expert': 'groupexpert', 'user': 'TestAuthor',
                            'scope': 'nes/pinball',
                            'reason': 'knows the game and its categories'})
            ck('a system expert appoints a game expert under them', c == 200
               and r['scope'] == 'nes/pinball' and r['by'] == 'groupexpert', str(r))
            roster = json.loads((work / 'roles.json').read_text()) \
                if (work / 'roles.json').exists() else {'events': []}
            c, r, _ = call(U + '/api/expert/appoint',
                           {'key': KEY, 'expert': 'groupexpert', 'user': 'TestAuthor',
                            'scope': 'nes/pinball', 'reason': 'appointing them twice'})
            ck('the same scope is not granted twice', c == 409, str(r))
            c, r, _ = call(U + '/api/expert/resign',
                           {'key': KEY, 'user': 'TestAuthor', 'scope': 'nes/pinball'})
            ck('an expert may step down without asking anybody', c == 200
               and r['dropped'] == 1, str(r))
            c, r, _ = call(U + '/api/expert/resign',
                           {'key': KEY, 'user': 'TestAuthor', 'scope': 'nes/pinball'})
            ck('stepping down twice is a 404', c == 404, str(r))
            c, r, _ = call(U + '/api/expert/appoint',
                           {'key': KEY, 'expert': 'eien86', 'user': 'NoSuchPerson',
                            'scope': 'nes/pinball',
                            'reason': 'somebody who has never signed up'})
            ck('appointing a name with no forum account is refused', c == 404, str(r))

            # --- a role granted elsewhere is honoured without waiting for a write ---
            # Permissions are read out of the checkout, and the checkout used to
            # refresh only on the way to a write: an appointment pushed straight
            # to the archive stayed invisible, and the archivist refused the very
            # expert it had on record.
            outside = td / 'outside'
            subprocess.run(['git', 'clone', '-q', str(origin), str(outside)], check=True)
            subprocess.run(['git', 'config', 'user.name', 'outside'], cwd=outside, check=True)
            subprocess.run(['git', 'config', 'user.email', 'o@o'], cwd=outside, check=True)
            roles_doc = json.loads((outside / 'roles.json').read_text())
            roles_doc['events'].append({
                'user': 'OutsideExpert', 'role': 'expert', 'scope': 'nes',
                'action': 'granted', 'by': 'eien86', 'date': '2026-08-18',
                'reason': 'granted straight in the archive, never through the archivist'})
            (outside / 'roles.json').write_text(json.dumps(roles_doc, indent=1))
            subprocess.run(['git', 'add', '-A'], cwd=outside, check=True)
            subprocess.run(['git', 'commit', '-qm', 'grant outside the archivist'],
                           cwd=outside, check=True)
            subprocess.run(['git', 'push', '-q', 'origin', 'HEAD:main'], cwd=outside, check=True)
            c, r, _ = call(U + '/api/expert/appoint',
                           {'key': KEY, 'expert': 'OutsideExpert', 'user': 'TestAuthor',
                            'scope': 'nes/pinball', 'dry_run': '1',
                            'reason': 'appointing as an expert granted out of band'})
            ck('an expert granted outside the archivist is honoured at once',
               c == 200 and r.get('dry_run'), str(r))

            # --- an expert creating inside their own scope needs nobody ---
            c, r, _ = call(U + '/api/group/create',
                           {'key': KEY, 'expert': 'eien86', 'group': 'founder-series',
                            'title': 'Founder Series', 'games': 'nes/pinball'})
            ck('a site expert creates a series, real on arrival', c == 200, str(r))
            subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            gdoc = json.loads((work / 'groups.json').read_text())
            mine_ = next(g for g in gdoc['groups'] if g['key'] == 'founder-series')
            ck('and it records who made it',
               mine_.get('createdBy') == 'eien86', str(mine_))
            c, r, _ = call(U + '/api/group/edit',
                           {'key': KEY, 'expert': 'eien86', 'group': 'founder-series',
                            'remove': 'nes/pinball'})
            ck('the game goes back for the tests that follow', c == 200, str(r))

            # --- series: created, changed and ratified by the people who
            # already speak for the games in them ---
            c, r, _ = call(U + '/api/group/create',
                           {'key': KEY, 'expert': 'groupexpert', 'group': 'test-family',
                            'title': 'Test Family', 'games': 'nes/pinball'})
            ck('a system expert may gather a game they cover', c == 200, str(r))
            c, r, _ = call(U + '/api/group/create',
                           {'key': KEY, 'expert': 'groupexpert', 'group': 'test-family',
                            'title': 'Again', 'games': ''})
            ck('a series key is claimed once', c == 409, str(r))
            c, r, _ = call(U + '/api/group/create',
                           {'key': KEY, 'expert': 'groupexpert', 'group': 'Bad Key',
                            'title': 'Bad', 'games': ''})
            ck('a series key is a slug', c == 400, str(r))
            c, r, _ = call(U + '/api/group/create',
                           {'key': KEY, 'expert': 'groupexpert', 'group': 'empty-one',
                            'title': 'Empty', 'games': ''})
            ck('an empty series needs site scope, since it reaches nobody',
               c == 403, str(r))
            c, r, _ = call(U + '/api/group/create',
                           {'key': KEY, 'expert': 'groupexpert', 'group': 'ghosts',
                            'title': 'Ghosts', 'games': 'nes/nosuchgame'})
            ck('a series cannot list a game that does not exist', c == 404, str(r))
            subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            gdoc = json.loads((work / 'groups.json').read_text())
            made = next(g for g in gdoc['groups'] if g['key'] == 'test-family')
            ck('the series records who made it and when',
               made.get('createdBy') == 'groupexpert'
               and re.fullmatch(r'\d{4}-\d{2}-\d{2}', made.get('createdAt') or ''),
               str(made))

            c, r, _ = call(U + '/api/group/edit',
                           {'key': KEY, 'expert': 'TestAuthor', 'group': 'test-family',
                            'title': 'Hijacked'})
            ck('somebody with no scope over the series may not touch it', c == 403, str(r))
            c, r, _ = call(U + '/api/group/edit',
                           {'key': KEY, 'expert': 'groupexpert', 'group': 'test-family',
                            'remove': 'nes/pinball', 'title': 'Test Family Renamed'})
            ck('games leave a series and a title can change',
               c == 200 and r['games'] == [] and r['title'] == 'Test Family Renamed', str(r))
            c, r, _ = call(U + '/api/group/edit',
                           {'key': KEY, 'expert': 'groupexpert', 'group': 'nope',
                            'add': 'nes/pinball'})
            ck('editing a series that does not exist is a 404', c == 404, str(r))

            # a game in two series would be drawn twice and 'unclassified'
            # would stop meaning anything
            c, r, _ = call(U + '/api/group/edit',
                           {'key': KEY, 'expert': 'groupexpert', 'group': 'test-family',
                            'add': 'nes/pinball'})
            ck('a game comes back to a series it left', c == 200, str(r))
            c, r, _ = call(U + '/api/group/create',
                           {'key': KEY, 'expert': 'groupexpert', 'group': 'rival',
                            'title': 'Rival', 'games': 'nes/pinball'})
            ck('a game belongs to one series, even at creation time',
               c == 409 and 'belongs to one' in r.get('error', ''), str(r))

# ratification is retired: the endpoint answers 404 like anything gone
            c, r, code_hdrs = call(U + '/api/group/ratify',
                           {'key': KEY, 'expert': 'eien86', 'group': 'test-family'})
            ck('the retired ratify endpoint is simply gone', c == 404, str(r)[:80])

            # --- a group expert fills out their own series ---
            c, r, _ = call(U + '/api/game/create',
                           {'key': KEY, 'user': 'groupexpert', 'group': 'test-family',
                            'system': 'nes', 'title': 'Brand New Game'})
            ck('an expert creates a game inside a series they cover',
               c == 200 and r['game'] == 'nes/brand-new-game', str(r))
            subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            newg = json.loads((work / 'games/nes/brand-new-game/game.json').read_text())
            ck('it is real as it is made, with their name on it',
               newg.get('createdBy') == 'groupexpert', str(newg))
            ck('and it has the files a game needs',
               (work / 'games/nes/brand-new-game/categories.json').exists(),
               'no categories.json')
            gdoc = json.loads((work / 'groups.json').read_text())
            fam = next(g for g in gdoc['groups'] if g['key'] == 'test-family')
            ck('and it landed in the series it was made for',
               'nes/brand-new-game' in fam['games'], str(fam))
            c, r, _ = call(U + '/api/game/create',
                           {'key': KEY, 'user': 'groupexpert', 'group': 'test-family',
                            'system': 'nes', 'title': 'Brand New Game'})
            ck('the same game is not created twice', c == 409, str(r))
            c, r, _ = call(U + '/api/game/create',
                           {'key': KEY, 'user': 'TestAuthor', 'group': 'test-family',
                            'system': 'nes', 'title': 'Not Yours'})
            ck('somebody with no scope over the series may not fill it', c == 403, str(r))

            # --- the category manager: add (open to any member), delete
            # only when unused ---
            c, r, _ = call(U + '/api/category/add',
                           {'key': KEY, 'user': 'nobody9', 'game': 'nes/pinball',
                            'label': 'Any%', 'rule': 'Finish the game by any means.',
                            'metrics': 'this is not a JSON array'})
            ck('a broken metric definition is refused', c == 400
               and 'metric' in r.get('error', ''), str(r))
            c, r, _ = call(U + '/api/category/add',
                           {'key': KEY, 'user': 'nobody9', 'game': 'nes/pinball',
                            'label': 'Any Percent', 'rule': 'Finish the game by any means.',
                            'metrics': '[{"label": "Score", "type": "points", '
                                       '"better": "higher"}]'})
            ck('a metric type outside time/number is refused', c == 400, str(r))
            c, r, _ = call(U + '/api/category/add',
                           {'key': KEY, 'user': 'groupexpert', 'game': 'nes/pinball',
                            'label': 'Any Percent', 'rule': 'Finish the game by any means.'})
            ck('a member adds a category', c == 200 and r['key'] == 'any-percent',
               str(r))
            c, r, _ = call(U + '/api/category/add',
                           {'key': KEY, 'user': 'groupexpert', 'game': 'nes/pinball',
                            'label': 'Any Percent', 'rule': 'Duplicate.'})
            ck('the same key is refused', c == 409, str(r))
            c, r, _ = call(U + '/api/category/delete',
                           {'key': KEY, 'expert': 'groupexpert', 'game': 'nes/pinball',
                            'option': '100k-glitched'})
            ck('a category with runs in it cannot be deleted',
               c == 409 and 'home' in r.get('error', ''), str(r))
            c, r, _ = call(U + '/api/category/delete',
                           {'key': KEY, 'expert': 'groupexpert', 'game': 'nes/pinball',
                            'option': 'any-percent'})
            ck('an unused category deletes cleanly', c == 200
               and r['removed'] == 'any-percent', str(r))
            subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            elog2 = json.loads((work / 'edits.json').read_text())['events']
            ck('category acts are in the edit log',
               any(e['kind'] == 'category' and e['field'] == 'added' for e in elog2)
               and any(e['kind'] == 'category' and e['field'] == 'removed'
                       for e in elog2), str(elog2[-3:]))

            # --- the editor: the library's shape, nothing else ---
            c, r, _ = call(U + '/api/editor/appoint',
                           {'key': KEY, 'expert': 'SiteOnly', 'user': 'TestAuthor',
                            'reason': 'a site expert trying to seat an editor'})
            ck('only a Committee seat grants the editor role', c == 403, str(r))
            c, r, _ = call(U + '/api/editor/appoint',
                           {'key': KEY, 'expert': 'CommitteeB', 'user': 'Shelver',
                            'reason': 'they already hold it'})
            ck('an editor is not seated twice', c == 409, str(r))
            c, r, _ = call(U + '/api/editor/appoint',
                           {'key': KEY, 'expert': 'CommitteeB', 'user': 'NewFriend',
                            'reason': 'tireless and careful with the shelves'})
            ck('a single Committee member seats an editor', c == 200
               and r['user'] == 'NewFriend', str(r))
            subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            rdoc = json.loads((work / 'roles.json').read_text())['events']
            ck('the grant is a public role event with the reason',
               any(e['role'] == 'editor' and e['user'] == 'NewFriend'
                   and e['by'] == 'CommitteeB' and 'shelves' in e['reason']
                   for e in rdoc), str(rdoc[-2:]))
            c, r, _ = call(U + '/api/category/add',
                           {'key': KEY, 'user': 'Shelver', 'game': 'nes/pinball',
                            'label': 'Editor Made', 'rule': 'A category the editor made.'})
            ck('an editor adds a category', c == 200 and r['key'] == 'editor-made',
               str(r))
            c, r, _ = call(U + '/api/expert/edit',
                           {'key': KEY, 'expert': 'Shelver', 'kind': 'category',
                            'target': 'nes/pinball:editor-made', 'field': 'rule',
                            'value': 'A sharper rule.',
                            'reason': 'Tightening the wording.'})
            ck('an editor rewrites a category rule', c == 200, str(r))
            c, r, _ = call(U + '/api/category/delete',
                           {'key': KEY, 'expert': 'Shelver', 'game': 'nes/pinball',
                            'option': 'editor-made'})
            ck('an editor deletes an unused category',
               c == 200 and r['removed'] == 'editor-made', str(r))
            c, r, _ = call(U + '/api/group/create',
                           {'key': KEY, 'expert': 'Shelver', 'group': 'shelf',
                            'title': 'Shelf', 'games': 'nes/solomons-key'})
            ck('an editor creates a group over games they hold no scope on',
               c == 200, str(r))
            c, r, _ = call(U + '/api/group/edit',
                           {'key': KEY, 'expert': 'Shelver', 'group': 'shelf',
                            'remove': 'nes/solomons-key', 'title': 'Shelf Renamed'})
            ck('an editor reshapes a group', c == 200
               and r['title'] == 'Shelf Renamed', str(r))
            c, r, _ = call(U + '/api/group/delete',
                           {'key': KEY, 'expert': 'Shelver', 'group': 'shelf',
                            'reason': 'The shelf was a fixture experiment.'})
            ck('an editor deletes a group', c == 200, str(r))

            # --- moving a game between groups: one act, one home ---
            c, r, _ = call(U + '/api/group/create',
                           {'key': KEY, 'expert': 'eien86', 'group': 'uncategorized',
                            'title': 'Sneaky', 'games': 'nes/solomons-key'})
            ck('the derived group keys cannot be claimed',
               c == 400 and 'reserved' in r.get('error', ''), str(r))
            c, r, _ = call(U + '/api/group/create',
                           {'key': KEY, 'expert': 'eien86', 'group': 'movetest',
                            'title': 'Move Test', 'games': 'nes/solomons-key'})
            ck('a site expert gathers an ungrouped game', c == 200, str(r))
            c, r, _ = call(U + '/api/group/edit',
                           {'key': KEY, 'expert': 'eien86', 'group': 'test-family',
                            'add': 'nes/solomons-key'})
            ck('add refuses a game that has a home; moving is explicit',
               c == 409 and 'move' in r.get('error', ''), str(r))
            c, r, _ = call(U + '/api/group/edit',
                           {'key': KEY, 'expert': 'eien86', 'group': 'test-family',
                            'move': 'nes/solomons-key'})
            ck('a move lands the game here',
               c == 200 and 'nes/solomons-key' in r['games'], str(r))
            subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            gdoc = json.loads((work / 'groups.json').read_text())['groups']
            ck('and pulls it out of the group that held it',
               'nes/solomons-key' not in next(
                   g for g in gdoc if g['key'] == 'movetest')['games'], str(gdoc))
            c, r, _ = call(U + '/api/group/edit',
                           {'key': KEY, 'expert': 'eien86', 'group': 'movetest',
                            'move': 'nes/solomons-key'})
            ck('and back again', c == 200 and 'nes/solomons-key' in r['games'], str(r))
            c, r, _ = call(U + '/api/group/edit',
                           {'key': KEY, 'expert': 'eien86', 'group': 'movetest'})
            ck('an edit that changes nothing is refused', c == 400, str(r))
            c, r, _ = call(U + '/api/group/delete',
                           {'key': KEY, 'expert': 'eien86', 'group': 'movetest',
                            'reason': 'The move-test scaffolding comes down.'})
            ck('the move-test scaffold goes', c == 200, str(r))
            c, r, _ = call(U + '/api/expert/edit',
                           {'key': KEY, 'expert': 'Shelver', 'kind': 'run',
                            'target': 'M900010', 'field': 'goal',
                            'value': '100k-glitched',
                            'reason': 'It already sits there; probing the gate.'})
            ck('an editor may move a run between categories (the gate opens)',
               c == 400 and 'already' in r.get('error', ''), str(r))
            c, r, _ = call(U + '/api/expert/edit',
                           {'key': KEY, 'expert': 'Shelver', 'kind': 'run',
                            'target': 'M900010', 'field': 'notes',
                            'value': 'not mine to touch',
                            'reason': 'Probing the run wall.'})
            ck('an editor may not touch the run itself',
               c == 403 and 'editor' in r.get('error', ''), str(r))
            c, r, _ = call(U + '/api/edit',
                           {'key': KEY, 'user': 'Shelver', 'run': 'M900010',
                            'emulator': 'nope', 'reason': 'Probing the panel.'})
            ck('the Edit run panel stays closed to an editor', c == 403, str(r))
            c, r, _ = call(U + '/api/run/delete',
                           {'key': KEY, 'expert': 'Shelver', 'run': 'M900010',
                            'reason': 'Probing the deletion wall.', 'dry_run': '1'})
            ck('an editor deletes no runs', c == 403, str(r))
            c, r, _ = call(U + '/api/game/delete',
                           {'key': KEY, 'expert': 'Shelver', 'game': 'nes/pinball',
                            'reason': 'Probing the deletion wall.', 'dry_run': '1'})
            ck('an editor deletes no games', c == 403, str(r))

            # --- the removal-request flow is retired: deletion is the act ---
            c, r, _ = call(U + '/api/game/request-removal',
                           {'key': KEY, 'expert': 'groupexpert',
                            'game': 'nes/brand-new-game', 'reason': 'asking the old way'})
            ck('the retired game removal-request endpoint is gone', c == 404, str(r)[:80])
            c, r, _ = call(U + '/api/group/request-removal',
                           {'key': KEY, 'expert': 'groupexpert', 'group': 'test-family',
                            'reason': 'asking the old way'})
            ck('the retired group removal-request endpoint is gone', c == 404, str(r)[:80])
            c, r, _ = call(U + '/api/removal/decide',
                           {'key': KEY, 'expert': 'eien86', 'kind': 'game',
                            'action': 'granted', 'target': 'nes/brand-new-game'})
            ck('the retired removal-decide endpoint is gone', c == 404, str(r)[:80])

            # --- refusal went with ratification: a wrong group is deleted,
            # on the record, by the fast lane tested further down ---
            c, r, _ = call(U + '/api/group/reject',
                           {'key': KEY, 'expert': 'groupexpert', 'group': 'test-family',
                            'reason': 'These games are not one family at all.'})
            ck('the retired reject endpoint is simply gone', c == 404, str(r)[:80])

            # --- a Committee seat may appoint, at any scope (2.5.3) ---
            c, r, _ = call(U + '/api/expert/appoint',
                           {'key': KEY, 'expert': 'CommitteeB', 'user': 'newuser',
                            'scope': 'site', 'dry_run': '1',
                            'reason': 'a committee member seats a site-wide expert'})
            ck('a Committee member appoints a whole-site expert',
               c == 200 and r.get('dry_run'), str(r))
            c, r, _ = call(U + '/api/expert/appoint',
                           {'key': KEY, 'expert': 'SiteOnly', 'user': 'newuser',
                            'scope': 'site', 'dry_run': '1',
                            'reason': 'site scope trying to clone itself'})
            ck('a site expert with no seat cannot: site does not cover site',
               c == 403, str(r))

            # --- an appointment that grants nothing is refused ---
            c, r, _ = call(U + '/api/expert/appoint',
                           {'key': KEY, 'expert': 'eien86', 'user': 'groupexpert',
                            'scope': 'nes/pinball',
                            'reason': 'they already cover this through the system'})
            ck('appointing somebody to a game their system already covers is refused',
               c == 409 and 'already speaks for' in r.get('error', ''), str(r))

            # --- the Founder seats the Committee, directly and on the record ---
            c, r, _ = call(U + '/api/founder/committee',
                           {'key': KEY, 'user': 'groupexpert', 'target': 'TestAuthor',
                            'action': 'granted', 'reason': 'not the founder, trying anyway'})
            ck('only the Founder seats the Committee', c == 403, str(r))
            c, r, _ = call(U + '/api/founder/committee',
                           {'key': KEY, 'user': 'eien86', 'target': 'TestAuthor',
                            'action': 'granted', 'reason': 'short'})
            ck('seating without a real reason is refused', c == 400, str(r))
            c, r, _ = call(U + '/api/founder/committee',
                           {'key': KEY, 'user': 'eien86', 'target': 'TestAuthor',
                            'action': 'granted',
                            'reason': 'A long-standing contributor; seating the first Committee.'})
            ck('the Founder seats somebody', c == 200 and r['action'] == 'granted', str(r))
            c, r, _ = call(U + '/api/founder/committee',
                           {'key': KEY, 'user': 'eien86', 'target': 'TestAuthor',
                            'action': 'granted', 'reason': 'seating them twice over'})
            ck('a seat is taken once', c == 409, str(r))
            subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            evs = json.loads((work / 'roles.json').read_text())['events']
            seat = [e for e in evs if e['user'] == 'TestAuthor' and e['role'] == 'committee'
                    and e['action'] == 'granted']
            ck('the seat is a role event naming the Founder',
               len(seat) == 1 and seat[0]['by'] == 'eien86'
               and 'Seated by the Founder' in seat[0]['reason'], str(seat))
            c, r, _ = call(U + '/api/founder/committee',
                           {'key': KEY, 'user': 'eien86', 'target': 'TestAuthor',
                            'action': 'revoked',
                            'reason': 'Stepping the fixture back down again.'})
            ck('the Founder unseats them too', c == 200 and r['action'] == 'revoked', str(r))
            c, r, _ = call(U + '/api/founder/committee',
                           {'key': KEY, 'user': 'eien86', 'target': 'TestAuthor',
                            'action': 'revoked', 'reason': 'unseating an empty chair'})
            ck('an empty chair cannot be unseated', c == 404, str(r))

            # --- the forum groups are printed from the archive, one way ---
            # This used to run both ways: experts were pushed out while
            # committee and moderator were pulled in, so being added to a
            # Discourse group was a second way to be granted a role and the
            # same fact had two homes. Now a forum edit grants nothing.
            c, r, _ = call(U + '/api/roles/publish',
                           {'key': KEY, 'expert': 'groupexpert', 'dry_run': '1'})
            ck('only a site expert publishes the roster', c == 403, str(r))
            c, r, _ = call(U + '/api/expert/sync',
                           {'key': KEY, 'expert': 'eien86', 'dry_run': '1'})
            ck('the name it shipped under still answers', c == 200, str(r))
            comm = r.get('groups', {}).get('committee', {})
            ck('a forum-only member is a stray to be removed, not a role holder',
               comm.get('remove') == ['ForumOnly'], str(comm))
            ck('the sitting committee is published out of the archive',
               sorted(x.lower() for x in comm.get('roster', []))
               == ['committeeb', 'committeec', 'committeed', 'eien86'], str(comm))
            before = len(json.loads((work / 'roles.json').read_text())['events'])
            c, r, _ = call(U + '/api/roles/publish', {'key': KEY, 'expert': 'eien86'})
            ck('publishing succeeds', c == 200, str(r))
            subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            after = len(json.loads((work / 'roles.json').read_text())['events'])
            ck('publishing never writes a role into the archive', before == after,
               f'{before} -> {after}')
            ck('the stray was removed from the forum group instead',
               ('remove', 'committee', 'ForumOnly') in GROUP_WRITES, str(GROUP_WRITES))
            ck('and the sitting members were printed into it',
               ('add', 'committee', 'CommitteeB') in GROUP_WRITES, str(GROUP_WRITES))

            # --- a role is granted by a Committee vote, and only that way ---
            for post, why, expect in (('902', 'a poll open to everybody', 'restricted'),
                                      ('903', 'a poll still open', 'still open'),
                                      ('905', 'an anonymous poll', 'public'),
                                      ('906', 'a post with no poll at all', 'no poll')):
                c, r, _ = call(U + '/api/role/decide',
                               {'key': KEY, 'user': 'eien86', 'target': 'TestAuthor', 'role': 'moderator',
                                'action': 'granted', 'post': post})
                ck(f'a role decision refuses {why}',
                   c == 409 and expect in r.get('error', ''), str(r))
            c, r, _ = call(U + '/api/role/decide',
                           {'key': KEY, 'user': 'eien86', 'target': 'TestAuthor', 'role': 'moderator',
                            'action': 'granted', 'post': '909'})
            ck('granting refuses a poll without a majority',
               c == 409 and '2 of 4' in r.get('error', ''), str(r))
            c, r, _ = call(U + '/api/role/decide',
                           {'key': KEY, 'user': 'eien86', 'target': 'TestAuthor', 'role': 'expert',
                            'action': 'granted', 'post': '901'})
            ck('an expert scope is not granted this way', c == 400, str(r))
            writes_before = len(GROUP_WRITES)
            c, r, _ = call(U + '/api/role/decide',
                           {'key': KEY, 'user': 'eien86', 'target': 'TestAuthor', 'role': 'moderator',
                            'action': 'granted', 'post': '907'})
            ck('a majority of the sitting Committee grants the role',
               c == 200 and r['votes'] == 3 and r['committee'] == 4, str(r))
            ck('the committee is counted from the archive, not the forum group',
               c == 200 and r.get('committee') == 4, str(r))
            ck('the grant records where it was decided',
               c == 200 and r.get('proof', '').endswith('/p/907'), str(r))
            subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            evs = json.loads((work / 'roles.json').read_text())['events']
            granted = [e for e in evs if e['user'] == 'TestAuthor' and e['role'] == 'moderator']
            ck('the archive holds the event, by the Committee, with its proof',
               len(granted) == 1 and granted[0]['by'] == 'committee'
               and granted[0]['action'] == 'granted' and granted[0].get('proof'),
               str(granted))
            ck('a moderator grant touches no forum group, that one being staff',
               len(GROUP_WRITES) == writes_before, str(GROUP_WRITES[writes_before:]))
            c, r, _ = call(U + '/api/role/decide',
                           {'key': KEY, 'user': 'eien86', 'target': 'TestAuthor', 'role': 'moderator',
                            'action': 'granted', 'post': '907'})
            ck('granting a role twice is refused', c == 409, str(r))
            # taking a role away needs two thirds of every sitting member
            # (Principles 2.3.5), which three of four clears and two does not
            c, r, _ = call(U + '/api/role/decide',
                           {'key': KEY, 'user': 'eien86', 'target': 'TestAuthor', 'role': 'moderator',
                            'action': 'revoked', 'post': '908'})
            ck('removal refuses a simple majority',
               c == 409 and 'hard majority' in r.get('error', ''), str(r))
            c, r, _ = call(U + '/api/role/decide',
                           {'key': KEY, 'user': 'eien86', 'target': 'TestAuthor', 'role': 'moderator',
                            'action': 'revoked', 'post': '901'})
            ck('a hard majority removes it', c == 200 and r['action'] == 'revoked', str(r))
            c, r, _ = call(U + '/api/role/decide',
                           {'key': KEY, 'user': 'eien86', 'target': 'TestAuthor', 'role': 'moderator',
                            'action': 'revoked', 'post': '901'})
            ck('removing a role nobody holds is refused', c == 404, str(r))

            # --- annulment: the Committee votes in the forum, we do the arithmetic ---
            c, r, _ = call(U + '/api/expert/appoint',
                           {'key': KEY, 'expert': 'eien86', 'user': 'TestAuthor',
                            'scope': 'nes/pinball', 'reason': 'appointed so it can be annulled'})
            ck('appointed for the annulment test', c == 200, str(r))
            for post, why, expect in (('902', 'a poll open to everybody', 'restricted'),
                                      ('903', 'a poll still open', 'still open'),
                                      ('904', 'a poll without a majority of the committee',
                                       'majority'),
                                      ('905', 'an anonymous poll', 'public'),
                                      ('906', 'a post with no poll at all', 'no poll')):
                c, r, _ = call(U + '/api/expert/annul',
                               {'key': KEY, 'user': 'eien86', 'target': 'TestAuthor',
                                'scope': 'nes/pinball', 'post': post})
                ck(f'annulment refuses {why}', c == 409 and expect in r.get('error', ''),
                   str(r))
            c, r, _ = call(U + '/api/expert/annul',
                           {'key': KEY, 'user': 'eien86', 'target': 'TestAuthor',
                            'scope': 'nes/pinball', 'post': '901'})
            ck('a closed committee poll with a majority annuls the appointment',
               c == 200 and r['dropped'] == 1 and r['votes'] == 3 and r['committee'] == 4,
               str(r))
            ck('the annulment records where the decision can be checked',
               c == 200 and r.get('proof', '').endswith('/p/901'), str(r))

            # --- a claim is asked for, the Committee answers, nobody's
            # address is ever written down ---
            c, r, _ = call(U + '/api/claim/request',
                           {'key': KEY, 'member': 'newuser', 'identity': 'HeldName',
                            'evidence': 'I posted this request from that account.'})
            ck('a member files a claim', c == 200 and r['request']['status'] == 'open',
               str(r))
            c, r, _ = call(U + '/api/claim/request',
                           {'key': KEY, 'member': 'newuser', 'identity': 'OtherName',
                            'evidence': 'and another one, at the same time'})
            ck('one claim at a time', c == 409, str(r))
            c, r, _ = call(U + '/api/claim/pending', {'key': KEY, 'user': 'TestAuthor'})
            ck('a member cannot read the open claims', c == 403, str(r))
            c, r, _ = call(U + '/api/claim/pending', {'key': KEY, 'user': 'SiteOnly'})
            ck('a site-wide expert who is not on the Committee cannot read them',
               c == 403, 'expert scope is authority over games, not over who '
                         'somebody is: ' + str(r))
            c, r, _ = call(U + '/api/claim/decide',
                           {'key': KEY, 'user': 'SiteOnly', 'identity': 'HeldName',
                            'action': 'approved'})
            ck('nor answer one', c == 403, str(r))
            c, r, _ = call(U + '/api/claim/pending', {'key': KEY, 'user': 'CommitteeB'})
            ck('a committee member with no expert scope can', c == 200, str(r)[:160])
            c, r, _ = call(U + '/api/claim/pending', {'key': KEY, 'user': 'eien86'})
            ck('the committee reads them, with a masked address to recognise',
               c == 200 and r['pending'] and 'email' in r['pending'][0], str(r)[:200])
            shown = r['pending'][0]['email'] if c == 200 and r.get('pending') else 'x'
            ck('and it is masked, or empty when the forum cannot be asked',
               shown == '' or ('*' in shown and '@' in shown), repr(shown))
            c, r, _ = call(U + '/api/claim/decide',
                           {'key': KEY, 'user': 'TestAuthor', 'identity': 'HeldName',
                            'action': 'approved'})
            ck('a member cannot answer a claim', c == 403, str(r))
            c, r, _ = call(U + '/api/claim/decide',
                           {'key': KEY, 'user': 'eien86', 'identity': 'HeldName',
                            'action': 'denied', 'note': 'no'})
            ck('denying without a reason is refused', c == 400, str(r))
            c, r, _ = call(U + '/api/claim/decide',
                           {'key': KEY, 'user': 'eien86', 'identity': 'HeldName',
                            'action': 'approved'})
            ck('the committee approves it and the person is told',
               c == 200 and r['action'] == 'approved' and r.get('told'), str(r))
            subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            cdoc = json.loads((work / 'claims.json').read_text())
            ck('the claim is closed in the archive, naming who answered it',
               cdoc['requests'][0]['status'] == 'approved'
               and cdoc['requests'][0]['decidedBy'] == 'eien86', str(cdoc))
            ck('and no email address was written anywhere near it',
               '@' not in json.dumps(cdoc), str(cdoc))
            arec = json.loads((work / 'authors' / 'heldname.json').read_text())
            ck('the name is handed over, by the committee route',
               arec['claimed'] and arec['claimedBy'] == 'newuser'
               and arec['claimMethod'] == 'committee', str(arec))
            c, r, _ = call(U + '/api/claim/decide',
                           {'key': KEY, 'user': 'eien86', 'identity': 'HeldName',
                            'action': 'approved'})
            ck('a claim is answered once', c == 404, str(r))

            # --- what happened here reaches Discord, and only what happened ---
            def discord_saw(substr, timeout=12):
                end = time.time() + timeout
                while time.time() < end:
                    if any(substr in m for m in DISCORD_MSGS):
                        return True
                    time.sleep(0.3)
                return False

            c, r, _ = call(U + '/api/verify',
                           {'key': KEY, 'user': 'NotifyGuy', 'run': 'M900010'})
            ck('a real verification lands', c == 200, str(r))
            ck('and Discord hears of it, as one line with the link in a word',
               discord_saw('**[NotifyGuy](<https://toolassisted.run/authors/'
                           'notifyguy/>)** verified [[NES] Pinball'),
               str(DISCORD_MSGS[-3:]))
            ck('the author names carry their profile links',
               discord_saw('by [TestAuthor](<https://toolassisted.run/authors/'
                           'testauthor/>)'),
               str(DISCORD_MSGS[-3:]))
            ghost = dict(sub, game='nes/solomons-key', goal='fastest-completion',
                         authors='TestAuthor, GhostGuy')
            del ghost['dry_run']
            c, r, _ = call(U + '/api/submit', ghost, uniq_files())
            ck('a run with an unregistered co-author archives', c == 200, str(r))
            ck('an unregistered co-author is named without a link',
               discord_saw('GhostGuy')
               and not any('[GhostGuy]' in m for m in DISCORD_MSGS),
               str(DISCORD_MSGS[-3:]))
            ck('a notification is one line',
               all('\n' not in m for m in DISCORD_MSGS), str(DISCORD_MSGS[-3:]))
            ck('nothing we post can ping anybody',
               '@everyone' not in ''.join(DISCORD_MSGS), 'and mentions are parsed off')

            # the forum relay: signed, public-only, never our own bot
            def hook(payload, event='post_created', secret='hooksecret'):
                raw = json.dumps(payload).encode()
                sig = 'sha256=' + hmac.new(secret.encode(), raw,
                                           hashlib.sha256).hexdigest()
                req = urllib.request.Request(
                    U + '/api/hooks/discourse', data=raw, method='POST',
                    headers={'Content-Type': 'application/json',
                             'X-Discourse-Event': event,
                             'X-Discourse-Event-Signature': sig})
                try:
                    with urllib.request.urlopen(req) as resp:
                        return resp.status, json.loads(resp.read())
                except urllib.error.HTTPError as e:
                    return e.code, json.loads(e.read() or b'{}')

            c, r = hook({'post': {'username': 'chatter', 'topic_archetype': 'regular',
                                  'topic_title': 'Hello there', 'topic_id': 5,
                                  'post_number': 2, 'cooked': '<p>first post!</p>'}},
                        secret='wrongsecret')
            ck('a forum hook with a bad signature is refused', c == 403, str(r))
            c, r = hook({'post': {'username': 'chatter', 'topic_archetype': 'regular',
                                  'topic_title': 'Hello there', 'topic_id': 5,
                                  'post_number': 2, 'cooked': '<p>first post!</p>'}})
            ck('a public forum post is relayed', c == 200 and not r.get('ignored'), str(r))
            # the topic link points at the mock forum here and the real one in
            # production, so the assertion holds the shape, not the host
            ck('and it reaches Discord, tags stripped, the title carrying the link',
               discord_saw('**chatter** posted in [Hello there](<')
               and discord_saw('/t/5/2>): first post!')
               and not any('[chatter]' in m for m in DISCORD_MSGS),
               str(DISCORD_MSGS[-3:]))
            c, r = hook({'post': {'username': 'chatter',
                                  'topic_archetype': 'private_message',
                                  'topic_title': 'my claim was denied', 'topic_id': 6,
                                  'post_number': 1, 'cooked': '<p>secret</p>'}})
            ck('a private message is never relayed',
               c == 200 and r.get('ignored') == 'not a public topic'
               and not any('my claim' in m for m in DISCORD_MSGS), str(r))
            c, r = hook({'post': {'username': 'archivist', 'topic_archetype': 'regular',
                                  'topic_title': 'Some Game by X [M1]', 'topic_id': 7,
                                  'post_number': 1, 'cooked': '<p>announce</p>'}})
            ck('our own bot is not relayed twice',
               c == 200 and r.get('ignored') == 'our own bot', str(r))

            # --- claiming an identity: an expert attests it ---
            # The token route is gone: it needed permission to edit a TASVideos
            # wiki page, which an inactive or banned account does not have.
            c, r, _ = call(U + '/api/claim/attest',
                           {'key': KEY, 'expert': 'groupexpert', 'member': 'newuser',
                            'identity': 'SomeAuthor',
                            'method': 'posted from their TASVideos account'})
            ck('a system-scoped expert may not attest an identity', c == 403, str(r))
            c, r, _ = call(U + '/api/claim/attest',
                           {'key': KEY, 'expert': 'SiteOnly', 'member': 'newuser',
                            'identity': 'SomeAuthor',
                            'method': 'the widest game scope, and still not identity'})
            ck('a site-wide expert without a seat may not attest either',
               c == 403 and 'Steering Committee' in r.get('error', ''), str(r))
            c, r, _ = call(U + '/api/claim/attest',
                           {'key': KEY, 'expert': 'eien86', 'member': 'newuser',
                            'identity': 'SomeAuthor', 'method': 'trust me'})
            ck('an attestation needs a real method', c == 400 and 'how you verified' in r.get('error', ''),
               str(r))
            c, r, _ = call(U + '/api/claim/attest',
                           {'key': KEY, 'expert': 'eien86', 'member': 'newuser',
                            'identity': 'SomeAuthor',
                            'method': 'they posted the request from their TASVideos account'})
            ck('a site expert attests an identity', c == 200
               and r['identity'] == 'SomeAuthor' and r['attestedBy'] == 'eien86', str(r))
            rec = json.loads((work / 'authors' / 'someauthor.json').read_text()) \
                if (work / 'authors' / 'someauthor.json').exists() else {}
            c, r, _ = call(U + '/api/claim/attest',
                           {'key': KEY, 'expert': 'eien86', 'member': 'somebodyelse',
                            'identity': 'SomeAuthor',
                            'method': 'a second person claiming the same name'})
            ck('a claimed identity cannot be handed to somebody else', c == 409, str(r))

            # --- SSO consumer (forge the provider) ---
            class NR(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *a, **k):
                    return None
            op = urllib.request.build_opener(NR)
            try:
                op.open(U + '/login')
                ck('login redirects', False)
            except urllib.error.HTTPError as e:
                loc = e.headers['Location']
                q = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
                sso_b64 = q['sso'][0]
                want = hmac.new(SSO_SECRET.encode(), sso_b64.encode(), hashlib.sha256).hexdigest()
                ck('login redirects signed', q['sig'][0] == want)
                nonce = urllib.parse.parse_qs(base64.b64decode(sso_b64).decode())['nonce'][0]
            payload = urllib.parse.urlencode({'nonce': nonce, 'username': 'ssouser',
                                              'external_id': '7'})
            b64 = base64.b64encode(payload.encode()).decode()
            sig = hmac.new(SSO_SECRET.encode(), b64.encode(), hashlib.sha256).hexdigest()
            cb = U + '/login/callback?' + urllib.parse.urlencode({'sso': b64, 'sig': sig})
            cookie = None
            try:
                op.open(cb)
            except urllib.error.HTTPError as e:
                m = re.match(r'tar_session=([^;]+)', e.headers.get('Set-Cookie', ''))
                cookie = m and f'tar_session={m.group(1)}'
            ck('session cookie issued', bool(cookie))
            # Arriving is what makes somebody a member. Until they log in once
            # there is no record, and a new member cannot find themselves on a
            # page that says it lists the people with an account here. ssouser
            # already has a record in the fixture, so this needs a stranger.
            try:
                op.open(U + '/login')
            except urllib.error.HTTPError as e:
                q2 = urllib.parse.parse_qs(urllib.parse.urlparse(e.headers['Location']).query)
                nonce2 = urllib.parse.parse_qs(
                    base64.b64decode(q2['sso'][0]).decode())['nonce'][0]
            payload2 = urllib.parse.urlencode({'nonce': nonce2, 'username': 'FreshFace',
                                               'external_id': '8'})
            b64b = base64.b64encode(payload2.encode()).decode()
            sigb = hmac.new(SSO_SECRET.encode(), b64b.encode(), hashlib.sha256).hexdigest()
            try:
                op.open(U + '/login/callback?'
                        + urllib.parse.urlencode({'sso': b64b, 'sig': sigb}))
            except urllib.error.HTTPError:
                pass
            deadline = time.time() + 25
            arec = work / 'authors' / 'freshface.json'
            while time.time() < deadline and not arec.exists():
                time.sleep(1)
                subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            ck('a first login writes the member record', arec.exists(),
               'a stranger who logs in is a member, and the roster says so')
            if arec.exists():
                rec_ = json.loads(arec.read_text())
                ck('and it says only that they are here, nothing else about them',
                   rec_.get('claimed') is True and set(rec_) == {'username', 'claimed'},
                   str(rec_))
            c, r, _ = call(U + '/api/me', cookie=cookie)
            ck('session identity', r.get('user') == 'ssouser')
            c, r, _ = call(U + '/api/verify', {'run': 'M900010', 'dry_run': '1'}, cookie=cookie)
            ck('session drives acts', c == 200 and r['would_record']['user'] == 'ssouser', str(r))
            # CSRF: same cookie, foreign Origin header -> refused
            req = urllib.request.Request(U + '/api/verify',
                                         urllib.parse.urlencode({'run': 'M900010', 'dry_run': '1'}).encode(),
                                         headers={'Cookie': cookie, 'Origin': 'https://evil.example'})
            try:
                urllib.request.urlopen(req)
                ck('foreign-origin cookie write refused', False)
            except urllib.error.HTTPError as e:
                ck('foreign-origin cookie write refused', e.code == 403)
            # same cookie, our origin -> accepted
            req = urllib.request.Request(U + '/api/verify',
                                         urllib.parse.urlencode({'run': 'M900010', 'dry_run': '1'}).encode(),
                                         headers={'Cookie': cookie, 'Origin': 'https://toolassisted.run'})
            ck('own-origin cookie write accepted',
               json.loads(urllib.request.urlopen(req).read()).get('ok') is True)
            try:
                op.open(cb)
                ck('nonce replay rejected', False)
            except urllib.error.HTTPError as e:
                ck('nonce replay rejected', e.code == 403)

            # --- self-service TASVideos import (session-only, claimed users) ---
            c, r, _ = call(U + '/api/import/scan', {'x': '1'})
            ck('import scan needs a session', c == 403, str(r))
            c, r, _ = call(U + '/api/import/scan', {'x': '1'}, cookie=cookie)
            ck('import scan lists pending', c == 200 and r['total'] == 4
               and len(r['pending']) == 3 and r['pending'][0]['id'] == 910001, str(r))
            ck('id-colliding publication never listed',
               all(x['id'] != 900010 for x in r['pending']), str(r))
            ck('a co-authored publication is listed, flagged as such',
               any(x['id'] == 910002 and x['multiAuthor'] and 'CoAuthorX' in x['authors']
                   for x in r['pending']), str(r['pending']))
            c, r, _ = call(U + '/api/import/run', {'x': '1'}, cookie=cookie)
            ck('nothing is imported unpicked', c == 400
               and 'select' in r.get('error', ''), str(r))
            c, r, _ = call(U + '/api/import/run', {'select': '910001'}, cookie=cookie)
            ck('a picked solo movie is imported, and only it', c == 200
               and r['imported'] == ['M910001'] and r['remaining'] == 0, str(r))
            # a co-authored work comes over when its member picks it: the
            # selection is the act that carries the responsibility
            c, r, _ = call(U + '/api/import/run', {'select': 'M910002, M910003'},
                           cookie=cookie)
            ck('a picked co-authored movie is imported on that selection',
               c == 200 and 'M910002' in r['imported'], str(r))
            ck('an oversized movie is refused instead of archived',
               any('intake cap' in s for s in r.get('skipped', [])), str(r.get('skipped')))
            ck('the import told Discord, one line for the batch',
               discord_saw('** imported movie: [M910002](<'),
               str(DISCORD_MSGS[-3:]))
            c, r, _ = call(U + '/api/import/scan', {'x': '1'}, cookie=cookie)
            ck('import is idempotent (scan)', c == 200 and r['archived'] == 3
               and [x['id'] for x in r['pending']] == [910003], str(r))
            ck('the whole catalogue is listed: archived ones say so by name',
               sorted(x['id'] for x in r.get('already', [])) == [900010, 910001, 910002],
               str(r.get('already')))
            ck('the unimportable one says why it is still listed',
               r['pending'][0].get('tooBig') is True, str(r['pending']))
            c, r, _ = call(U + '/api/import/run', {'select': '910001 910002'},
                           cookie=cookie)
            ck('import is idempotent (run)', c == 200 and r['imported'] == [], str(r))

            # --- a rename does not strand the session behind it ---
            # The cookie was issued to ssouser; a claim approval renames the
            # account, and the stale cookie must act as the new name (the bug
            # was: it kept acting as a name that no longer exists, until the
            # person logged out and back in by hand).
            c, r, _ = call(U + '/api/claim/attest',
                           {'key': KEY, 'expert': 'eien86', 'member': 'ssouser',
                            'identity': 'RenamedStar',
                            'method': 'fixture: rename the member who is logged in'})
            ck('attesting the logged-in member succeeds', c == 200, str(r))
            c, r, _ = call(U + '/api/me', cookie=cookie)
            ck('a pre-rename cookie answers with the claimed name',
               r.get('user') == 'RenamedStar', str(r))
            c, r, _ = call(U + '/api/verify', {'run': 'M900010', 'dry_run': '1'},
                           cookie=cookie)
            ck('and its acts land under the claimed name',
               c == 200 and r['would_record']['user'] == 'RenamedStar', str(r))
            c, r, _ = call(U + '/api/like', {'run': 'M910001'}, cookie=cookie)
            ck('a run credited to the former name is still their own (no self-like)',
               c == 400 and 'own run' in r.get('error', ''), str(r))

            # --- pushed state validates (including the freely created game) ---
            check = td / 'check'
            # file:// forces the git transport instead of copying pack files out of
            # origin.git, which races with the archivist repacking it mid-push
            subprocess.run(['git', 'clone', '-q', f'file://{origin}', str(check)], check=True)
            ck('a stated completion date is archived with the run',
               first_dup and json.loads(next(check.glob(
                   f'games/*/*/runs/{first_dup}/run.json')).read_text())
               .get('completed') == '2021-10-26')
            ck('the record the claim superseded is deleted',
               not (check / 'authors' / 'ssouser.json').exists())
            ck('and the claimed record is the member now',
               (check / 'authors' / 'renamedstar.json').exists())
            gj = check / 'games/nes/solomons-key/game.json'
            ck('created game exists, real on arrival', gj.exists()
               and 'established' not in json.loads(gj.read_text()))
            ck('a submitted run carries its committed forum pointer',
               created_id and json.loads(
                   (check / f'games/nes/solomons-key/runs/{created_id}/run.json')
                   .read_text()).get('forum', {}).get('topicId'),
               'the topic pointer must land in the archival commit itself: '
               'written after the push it was reset away, and the run page '
               'showed no discussion')
            ck('an expert-created game gets its forum anchor topic',
               json.loads((check / 'games/nes/brand-new-game/game.json').read_text())
               .get('forum', {}).get('topicId'),
               'games need an anchor so their tag page exists before the first run')
            cj = json.loads((check / 'games/nes/solomons-key/categories.json').read_text())
            ck('created category is simply a category',
               'provisional' not in cj['dimensions'][0]['options'][0])
            ck('created run folder present',
               created_id and (check / f'games/nes/solomons-key/runs/{created_id}/run.json').exists())
            irun = check / 'games/nes/pinball/runs/M910001'
            ck('imported run pushed', (irun / 'run.json').exists()
               and (irun / 'thumb.jpg').exists())
            if (irun / 'run.json').exists():
                ir = json.loads((irun / 'run.json').read_text())
                co = json.loads((check / 'games/nes/impo-quest/runs/M910002/run.json')
                                .read_text())
                ck('the co-authored import credits every author and names the importer',
                   {a['user'] for a in co['authors']} == {'ssouser', 'CoAuthorX'}
                   and co['imported']['importedBy'] == 'ssouser', str(co['authors']))
                ck('imported run marked imported',
                   ir['status']['reproduced'] == 'imported'
                   and ir['status']['verified'] == 'imported'
                   and ir['status']['console'] in ('none', 'imported')
                   and ir['imported']['importedBy'] == 'ssouser')
                notes = (irun / 'notes.md').read_text()
                ck('judge text stripped from imported notes',
                   'SomeJudge' not in notes and 'My own notes' in notes
                   and '**Imported**' in notes)
                # the source is its link, never its name; the responsibility
                # for a collaborative import rides with whoever picked it
                ck('imported notes name the source only by its link',
                   'tasvideos.org/' in notes
                   and 'Imported from TASVideos' not in notes
                   and 'TASVideos staff' not in notes, notes[:320])
                ck('imported notes say the importer answers for a collaboration',
                   'takes the\n> responsibility' in notes
                   or 'takes the responsibility' in notes.replace('\n> ', ' '),
                   notes[:400])
            native = json.loads((check / 'games/nes/pinball/runs/M900010/run.json').read_text())
            ck('id-colliding native run untouched',
               'imported' not in native and native['movie']['frames'] == 12345)
            co = check / 'authors' / 'coauthorx.json'
            ck('a co-author who is not a member gets no record, only the credit',
               not co.exists())
            imported_run = next(check.glob('games/*/*/runs/*/run.json'))
            ck('every author record in the archive is a member',
               all(json.loads(f.read_text()).get('claimed') is True
                   for f in (check / 'authors').glob('*.json')),
               str([f.name for f in (check / 'authors').glob('*.json')
                    if not json.loads(f.read_text()).get('claimed')]))
            # the policy this asserted is gone: a member may pick their
            # co-authored work themselves, and M910002 above proves the path
            ck('a co-authored work reaches the archive only by being picked',
               (check / 'games/nes/impo-quest/runs/M910002/run.json').exists())
            sub_author = check / 'authors' / 'testauthor.json'
            ck('native submit creates claimed author record', sub_author.exists()
               and json.loads(sub_author.read_text())['claimed'] is True)
            v = subprocess.run([sys.executable, str(check / 'validate.py')],
                               capture_output=True, text=True)
            ck('pushed archive validates', v.returncode == 0, v.stdout[-500:])

            # --- deletion: the fast lane for things that were never works ---
            # Runs LAST: it eats fixtures every earlier test leans on.
            c, r, _ = call(U + '/api/run/delete',
                           {'key': KEY, 'user': 'TestAuthor', 'expert': 'TestAuthor',
                            'run': 'M900010', 'reason': 'a member trying the fast lane'})
            ck('a member cannot delete a movie', c == 403, str(r))
            c, r, _ = call(U + '/api/run/delete',
                           {'key': KEY, 'expert': 'groupexpert', 'run': 'M900010',
                            'reason': 'no'})
            ck('a deletion without a reason is refused', c == 400, str(r))
            c, r, _ = call(U + '/api/game/delete',
                           {'key': KEY, 'expert': 'groupexpert', 'game': 'nes/pinball',
                            'reason': 'deleting the game its movies live in'})
            ck('deleting a game deletes its runs with it',
               c == 200 and 'M900010' in r['runs_deleted'], str(r))
            subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            ck('the deleted game is gone from the tree and its group',
               not (work / 'games/nes/pinball').exists()
               and 'nes/pinball' not in json.dumps(
                   json.loads((work / 'groups.json').read_text())), 'still present')
            ck('no holding game was conjured for the fallen runs',
               not (work / 'games/nes/uncategorized').exists(), 'it exists')
            dele = json.loads((work / 'deletions.json').read_text())['events']
            ck('each deleted run has its own line in the public log',
               any(e['kind'] == 'run' and e['key'] == 'M900010'
                   and 'nes/pinball' in e['reason'] for e in dele), str(dele[-3:]))
            c, r, _ = call(U + '/api/run/delete',
                           {'key': KEY, 'expert': 'groupexpert', 'run': 'M900010',
                            'reason': 'the movie went down with its game'})
            ck('the run is already gone with its game', c == 404, str(r))
            c, r, _ = call(U + '/api/group/delete',
                           {'key': KEY, 'expert': 'eien86', 'group': 'founder-group',
                            'reason': 'dissolving a fixture group outright'})
            gone_group = c in (200, 404)
            c2, r2, _ = call(U + '/api/group/delete',
                           {'key': KEY, 'expert': 'groupexpert', 'group': 'doomed',
                            'reason': 'dissolving the refused fixture group outright'})
            ck('an expert deletes a group outright', c2 == 200 or gone_group,
               str((r, r2)))
            dlog = json.loads((work / 'deletions.json').read_text()) if \
                (work / 'deletions.json').exists() else None
            subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            dlog = json.loads((work / 'deletions.json').read_text())['events']
            ck('every deletion is in the log with who and why',
               {(e['kind'], e['key']) for e in dlog} >= {('run', 'M900010'),
                                                         ('game', 'nes/pinball')}
               and all(e['by'] and len(e['reason']) >= 8 for e in dlog), str(dlog))

            # --- member deletion: the Committee, but never on its own kind ---
            c, r, _ = call(U + '/api/member/delete',
                           {'key': KEY, 'expert': 'CommitteeB', 'target': 'CommitteeC',
                            'reason': 'one seat trying to delete another'})
            ck('the Committee cannot delete a sitting Committee member',
               c == 403 and 'Founder' in r.get('error', ''), str(r))
            c, r, _ = call(U + '/api/member/delete',
                           {'key': KEY, 'expert': 'CommitteeB', 'target': 'eien86',
                            'reason': 'a seat trying to delete the founder'})
            ck('the Founder cannot be deleted by anybody', c == 403, str(r))
            c, r, _ = call(U + '/api/member/delete',
                           {'key': KEY, 'expert': 'groupexpert', 'target': 'FreshFace',
                            'reason': 'an expert trying a committee power'})
            ck('an expert without a seat cannot delete members', c == 403, str(r))
            c, r, _ = call(U + '/api/member/delete',
                           {'key': KEY, 'expert': 'CommitteeB', 'target': 'TestAuthor',
                            'reason': 'they authored things; must be refused'})
            ck('a member with archived works cannot be record-deleted', c == 409, str(r))
            c, r, _ = call(U + '/api/member/delete',
                           {'key': KEY, 'expert': 'CommitteeB', 'target': 'FreshFace',
                            'reason': 'the test account that logged in once'})
            ck('the Committee deletes a plain member', c == 200, str(r))
            subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            ck('the member record is gone and the log says why',
               not (work / 'authors' / 'freshface.json').exists()
               and any(e['kind'] == 'member' and e['key'] == 'FreshFace' for e in
                       json.loads((work / 'deletions.json').read_text())['events']),
               'record still present or unlogged')
            c, r, _ = call(U + '/api/member/delete',
                           {'key': KEY, 'expert': 'eien86', 'target': 'CommitteeC',
                            'reason': 'the Founder unseats and deletes in one act'})
            ck('the Founder deletes a sitting Committee member',
               c == 200 and 'committee' in r.get('roles_revoked', []), str(r))
            subprocess.run(['git', 'pull', '-q'], cwd=work, check=False)
            evs_ = json.loads((work / 'roles.json').read_text())['events']
            ck('and the seat was revoked in the same act',
               any(e['user'] == 'CommitteeC' and e['action'] == 'revoked'
                   and e['role'] == 'committee' for e in evs_), str(evs_[-3:]))
            v2 = subprocess.run([sys.executable, str(work / 'validate.py')],
                                cwd=work, capture_output=True, text=True)
            ck('the archive still validates after every deletion',
               v2.returncode == 0, v2.stdout[-400:])
        finally:
            if failures:
                # the service's own traceback is the only thing that explains a
                # 500, and CI cannot be attached to interactively
                print('--- archivist log: what it actually complained about ---')
                log = (td / 'log').read_text()
                # a request log tail hides the traceback that explains a 500
                lines = log.splitlines()
                blame = [i for i, l in enumerate(lines)
                         if 'Traceback' in l or 'ERROR in app' in l]
                for i in blame[-3:]:
                    print('\n'.join(lines[i:i + 25]))
                print('--- last lines ---')
                print('\n'.join(lines[-25:]))
            proc.terminate()
            httpd.shutdown()
            if failures:
                pass

    print('---', len(failures), 'failures')
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
