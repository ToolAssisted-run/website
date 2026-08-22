#!/usr/bin/env python3
"""A throwaway archivist for security scanning (OWASP ZAP and the like).

Never point a scanner at the live service: an active scan submits junk,
which the real archivist would archive for good. This starts the same
hermetic instance the test suite uses: a lightened copy of the archive in a
scratch git repo with a local bare "origin" (pushes go nowhere), a mock
forum, no Discord, no e-mail. Kill it when the scan is done and nothing
remains.

Usage: target.py [archive_dir]   (prints the base URL and the submitter
key, then serves until interrupted)"""
import http.server
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / 'tests'))
import mkarchive  # noqa: E402

ARCHIVE = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else pathlib.Path.home() / 'ToolAssisted-archive')
KEY = 'zap-scan-key'

def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

class MockForum(http.server.BaseHTTPRequestHandler):
    """Just enough forum for the archivist to believe it has one."""
    def _json(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == '/groups.json':
            return self._json({'groups': [{'id': 41, 'name': 'experts'}, {'id': 42, 'name': 'committee'}]})
        if self.path.endswith('/members.json'):
            return self._json({'members': []})
        if self.path.startswith('/u/'):
            return self._json({'user': {'username': self.path[3:-5], 'avatar_template': ''}})
        return self._json({}, 404)

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get('Content-Length') or 0))
        return self._json({'id': 1, 'topic_id': 1})

    def do_PUT(self):  # noqa: N802
        return self.do_POST()

    def log_message(self, *a):
        pass

def main():
    td = pathlib.Path(tempfile.mkdtemp(prefix='zap-target-'))
    seed = td / 'seed'
    shutil.copytree(ARCHIVE, seed, ignore=shutil.ignore_patterns('.git'))
    mkarchive.lighten(seed)
    for af in (seed / 'authors').glob('*.json'):
        if not json.loads(af.read_text()).get('claimed'):
            af.unlink()
    mkarchive.prune_superseded(seed)
    (seed / 'claims.json').write_text('{"requests": []}\n')
    # one member with a key-authenticated identity the scanner can use
    (seed / 'authors' / 'zapuser.json').write_text(json.dumps(
        {'username': 'ZapUser', 'claimed': True}, indent=1) + '\n')
    git = lambda *a, cwd: subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *a],  # noqa: E731
                                         cwd=cwd, check=True, capture_output=True)
    git('init', '-q', '-b', 'main', cwd=seed)
    git('add', '-A', cwd=seed)
    git('commit', '-qm', 'seed', cwd=seed)
    origin = td / 'origin.git'
    subprocess.run(['git', 'clone', '-q', '--bare', str(seed), str(origin)], check=True)
    work = td / 'work'
    subprocess.run(['git', 'clone', '-q', str(origin), str(work)], check=True)
    git('config', 'user.name', 'zap', cwd=work)
    git('config', 'user.email', 'zap@t', cwd=work)

    hport = free_port()
    forum = http.server.ThreadingHTTPServer(('127.0.0.1', hport), MockForum)
    threading.Thread(target=forum.serve_forever, daemon=True).start()

    port = free_port()
    env = dict(SUBMIT_KEY=KEY, ARCHIVE_DIR=str(work), ARCHIVIST_BRANCH='main',
               GIT_SSH_COMMAND='ssh', PORT=str(port), DISCOURSE_KEY='mock-key',
               DISCORD_WEBHOOK_URL=f'http://127.0.0.1:{hport}/discord-hook',
               NOTIFY_LINK_WAIT_SECONDS='0', DISCOURSE_HOOK_SECRET='hooksecret',
               ARCHIVE_REFRESH_SECONDS='0', ROLE_RECONCILE_SECONDS='0',
               DISCOURSE_URL=f'http://127.0.0.1:{hport}',
               CLAIM_FETCH_BASE=f'http://127.0.0.1:{hport}/',
               THUMB_FETCH_BASE=f'http://127.0.0.1:{hport}/thumbs/',
               PROVIDER_MOCK_BASE=f'http://127.0.0.1:{hport}/p/',
               DUMPS_DIR=str(td / 'dumps'),
               WEBSITE_DIR=str(REPO), SITE_DIR=str(td / 'site'),
               DISCOURSE_CONNECT_SECRET='zapssosecret', SESSION_SECRET='zapsessionsecret',
               SELF_URL=f'http://127.0.0.1:{port}', SITE_ORIGIN='https://toolassisted.run',
               PATH='/usr/bin:/bin', HOME=str(td))
    if 'PYTHONPATH' in os.environ:
        env['PYTHONPATH'] = os.environ['PYTHONPATH']
    (td / 'dumps').mkdir()
    proc = subprocess.Popen([sys.executable, str(REPO / 'archivist/archivist.py')],
                            env=env, stdout=(td / 'log').open('w'), stderr=subprocess.STDOUT)
    base = f'http://127.0.0.1:{port}'
    for _ in range(60):
        try:
            urllib.request.urlopen(base + '/api/me')
            break
        except OSError:
            time.sleep(0.5)
    else:
        sys.exit((td / 'log').read_text()[-2000:])
    print(f'ZAP_TARGET={base}\nZAP_KEY={KEY}\nZAP_DIR={td}', flush=True)
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
    finally:
        shutil.rmtree(td, ignore_errors=True)

if __name__ == '__main__':
    main()
