#!/usr/bin/env python3
"""Local development server for toolAssisted.run with real archive data.

Builds the static site from the real archive (or uses an existing build)
and serves it at http://localhost:8000 with live caching disabled,
correct MIME types, 404 handling, and a full mock identity/archivist API
so you can test all expert and editor features seamlessly.
"""
import argparse
import http.server
import json
import os
import pathlib
import re
import socketserver
import subprocess
import sys
import urllib.parse
import webbrowser

REPO_ROOT = pathlib.Path(__file__).resolve().parent

# Ensure Python runs in UTF-8 mode on Windows
os.environ['PYTHONUTF8'] = '1'

# Put archivist on path for wikitext markdown preview rendering and providers
sys.path.insert(0, str(REPO_ROOT / 'archivist'))
try:
    import wikitext
except Exception:
    wikitext = None

try:
    import providers
except Exception:
    providers = None

DEFAULT_ARCHIVE_CANDIDATES = [
    pathlib.Path.home() / '~' / 'ToolAssisted-archive',
    pathlib.Path.home() / 'ToolAssisted-archive',
    REPO_ROOT.parent / 'ToolAssisted-archive',
]


def resolve_archive_dir(custom_path=None):
    if custom_path:
        p = pathlib.Path(custom_path).resolve()
        if p.is_dir() and (p / 'systems.json').exists():
            return p
        print(f"Error: Provided archive path does not exist or missing systems.json: {p}", file=sys.stderr)
        sys.exit(1)

    for p in DEFAULT_ARCHIVE_CANDIDATES:
        if p.is_dir() and (p / 'systems.json').exists():
            return p.resolve()

    print("Error: Could not locate ToolAssisted-archive repository.", file=sys.stderr)
    print("Please clone https://github.com/ToolAssisted-run/archive or specify --archive <path>", file=sys.stderr)
    sys.exit(1)


def build_site(archive_dir, out_dir):
    print(f"--- Building site from archive: {archive_dir} -> {out_dir} ---")
    env = os.environ.copy()
    env['PYTHONUTF8'] = '1'
    # Use relative API path for local dev builds so fetches hit localhost
    env['ARCHIVIST_URL'] = ''
    cmd = [sys.executable, '-X', 'utf8', str(REPO_ROOT / 'generator' / 'build.py'),
           str(archive_dir), str(out_dir)]
    res = subprocess.run(cmd, cwd=REPO_ROOT, env=env)
    if res.returncode != 0:
        print(f"Error: Build failed with return code {res.returncode}", file=sys.stderr)
        sys.exit(res.returncode)
    print("--- Build complete! ---\n")


class DevHandler(http.server.SimpleHTTPRequestHandler):
    mock_user = None

    def __init__(self, *args, directory=None, **kwargs):
        super().__init__(*args, directory=str(directory), **kwargs)

    def end_headers(self):
        # Disable caching completely for local development
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        # Enable CORS for local testing
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Credentials', 'true')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Requested-With')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS, PUT, DELETE')
        super().end_headers()

    def guess_type(self, path):
        # Ensure correct MIME types on Windows
        p = str(path).lower()
        if p.endswith(('.js', '.mjs')):
            return 'application/javascript'
        if p.endswith('.css'):
            return 'text/css'
        if p.endswith('.svg'):
            return 'image/svg+xml'
        if p.endswith('.json'):
            return 'application/json'
        return super().guess_type(path)

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        url_path = parsed.path

        if url_path in ('/api/me', '/archivist/api/me'):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            if DevHandler.mock_user:
                payload = {'loggedIn': True, 'user': DevHandler.mock_user}
            else:
                payload = {'loggedIn': False}
            self.wfile.write(json.dumps(payload).encode('utf-8'))
            return

        if url_path == '/api/encode/check':
            qs = urllib.parse.parse_qs(parsed.query)
            url = (qs.get('url', [''])[0]).strip()
            if not providers:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(json.dumps({'ok': True, 'name': 'Video', 'thumb': ''}).encode('utf-8'))
                return

            encode_provider = providers.resolve(url)
            if not encode_provider:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'ok': False,
                    'error': 'not a link from ' + ', '.join(providers.names())
                }).encode('utf-8'))
                return

            thumb = providers.thumbnail_url(encode_provider['kind'], encode_provider['id'])
            if not thumb and providers.BY_KIND[encode_provider['kind']].get('thumbs'):
                try:
                    thumb = providers.thumbnail_source(encode_provider['kind'], encode_provider['id'], 256 * 1024)
                except Exception:
                    thumb = None

            seconds = None
            try:
                seconds = providers.duration_seconds(encode_provider['kind'], encode_provider['id'])
            except Exception:
                pass

            payload = {
                'ok': bool(thumb or encode_provider),
                'kind': encode_provider['kind'],
                'name': encode_provider['name'],
                'id': encode_provider['id'],
                'thumb': thumb or '',
                'seconds': seconds
            }
            if not payload['ok']:
                payload['error'] = f"that {encode_provider['name']} video does not exist, or is private"

            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode('utf-8'))
            return

        if url_path == '/api/run/record':
            qs = urllib.parse.parse_qs(parsed.query)
            run_id = (qs.get('run', [''])[0]).strip()
            archive_dir = DevHandler.archive_dir or resolve_archive_dir()
            run_dir = next((archive_dir / 'games').glob(f'*/*/runs/{run_id}'), None)
            if not run_dir or not (run_dir / 'run.json').exists():
                self.send_response(404)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(json.dumps({'ok': False, 'error': f'unknown run {run_id}'}).encode('utf-8'))
                return

            run = json.loads((run_dir / 'run.json').read_text(encoding='utf-8'))
            notes_file = run_dir / 'notes.md'
            notes = notes_file.read_text(encoding='utf-8') if notes_file.exists() else ''
            lines = notes.splitlines()
            n = 0
            while n < len(lines) and lines[n].startswith('>'):
                n += 1
            notes = '\n'.join(lines[n:]).strip()
            if notes:
                notes += '\n'

            game_dir = run_dir.parent.parent
            game = json.loads((game_dir / 'game.json').read_text(encoding='utf-8'))
            categories = json.loads((game_dir / 'categories.json').read_text(encoding='utf-8'))
            game_key = f"{game_dir.parent.name}/{game_dir.name}"

            author_names = {a['user'].lower() for a in run.get('authors', []) if 'user' in a}
            current_user = (DevHandler.mock_user or '').lower()
            is_author = current_user in author_names or DevHandler.mock_user == 'GMP' or not DevHandler.mock_user
            may = {'author': is_author, 'expert': True, 'editor': True}

            seconds = None
            seconds_source = None
            if run.get('duration'):
                seconds = run['duration']
                seconds_source = 'record'
            elif not run.get('videoOnly') and (run.get('movie') or {}).get('frames'):
                fps = (run.get('movie') or {}).get('fps')
                if not fps:
                    try:
                        systems_file = archive_dir / 'systems.json'
                        if systems_file.exists():
                            systems_data = json.loads(systems_file.read_text(encoding='utf-8'))
                            fps = systems_data.get(game_key.split('/')[0], {}).get('fps')
                    except (OSError, ValueError):
                        fps = None
                if fps:
                    seconds = run['movie']['frames'] / fps
                    seconds_source = 'movie'

            payload = {
                'ok': True,
                'run': run,
                'notes': notes,
                'game': {'key': game_key, 'title': game.get('title', ''), 'system': game.get('system', '')},
                'categories': categories,
                'may': may,
                'seconds': seconds,
                'secondsSource': seconds_source,
            }
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode('utf-8'))
            return

        if url_path == '/login':
            qs = urllib.parse.parse_qs(parsed.query)
            user = qs.get('user', ['GMP'])[0]
            DevHandler.mock_user = user
            print(f"[*] Logged in as: {DevHandler.mock_user}")
            referer = self.headers.get('Referer', '/')
            self.send_response(302)
            self.send_header('Location', referer)
            self.end_headers()
            return

        if url_path == '/logout':
            print(f"[*] Logged out (was: {DevHandler.mock_user})")
            DevHandler.mock_user = None
            referer = self.headers.get('Referer', '/')
            self.send_response(302)
            self.send_header('Location', referer)
            self.end_headers()
            return

        if url_path == '/api/switch-user':
            qs = urllib.parse.parse_qs(parsed.query)
            new_user = qs.get('user', [None])[0]
            DevHandler.mock_user = new_user
            print(f"[*] Switched user to: {DevHandler.mock_user}")
            referer = self.headers.get('Referer', '/')
            self.send_response(302)
            self.send_header('Location', referer)
            self.end_headers()
            return

        if url_path.startswith('/assets/'):
            repo_asset = REPO_ROOT / url_path.lstrip('/')
            if repo_asset.is_file():
                try:
                    if repo_asset.suffix == '.js':
                        text = repo_asset.read_text(encoding='utf-8')
                        if providers:
                            text = (text
                                    .replace('ENCODE_HOSTS', '|'.join(providers.ALL_HOSTS))
                                    .replace('ENCODE_NAMES', ' · '.join(providers.names())))
                        data = text.encode('utf-8')
                    else:
                        with open(repo_asset, 'rb') as f:
                            data = f.read()
                    self.send_response(200)
                    self.send_header('Content-Type', self.guess_type(repo_asset))
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                except Exception:
                    pass

        local_path = self.translate_path(self.path)
        if os.path.isdir(local_path):
            local_path = os.path.join(local_path, 'index.html')

        if os.path.isfile(local_path) and local_path.endswith('.html'):
            try:
                with open(local_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                # Dynamic rewrite: Ensure window.TAR.api points to localhost
                content = content.replace("api: 'https://forum.toolassisted.run/archivist'", "api: ''")
                content = content.replace('api: "https://forum.toolassisted.run/archivist"', 'api: ""')
                content = content.replace("api: 'https://forum.toolassisted.run'", "api: ''")

                encoded = content.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                return
            except Exception:
                pass

        if not os.path.exists(local_path):
            custom_404 = os.path.join(self.directory, '404.html')
            if os.path.isfile(custom_404):
                self.send_response(404)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                with open(custom_404, 'rb') as f:
                    self.wfile.write(f.read())
                return

        super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        url_path = parsed.path
        content_length = int(self.headers.get('Content-Length', 0))
        raw_body = self.rfile.read(content_length)

        print(f"[DevServer Mock Action] POST {url_path} ({content_length} bytes)")

        # Handle Markdown / rules preview
        if url_path == '/api/preview':
            text = ''
            # Extract notes or rules from multipart or form body
            m = re.search(rb'name="notes"\r?\n\r?\n(.*?)\r?\n--', raw_body, re.S)
            if not m:
                m = re.search(rb'name="rules"\r?\n\r?\n(.*?)\r?\n--', raw_body, re.S)
            if m:
                text = m.group(1).decode('utf-8', errors='ignore')
            elif b'=' in raw_body:
                qs = urllib.parse.parse_qs(raw_body.decode('utf-8', errors='ignore'))
                text = (qs.get('notes') or qs.get('rules') or [''])[0]

            rendered = wikitext.md_html(text) if wikitext else f"<p>{text}</p>"
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({'ok': True, 'html': rendered}).encode('utf-8'))
            return

        # Handle run submission
        if url_path == '/api/submit':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            resp = {'ok': True, 'id': 'M100055', 'serial': 1076, 'url': '/runs/M100055/'}
            self.wfile.write(json.dumps(resp).encode('utf-8'))
            return

        # Handle emulator preset & quick chips curation
        if url_path == '/api/emulators/edit':
            try:
                content_type = self.headers.get('Content-Type', '')
                form_fields = {}
                if 'multipart/form-data' in content_type:
                    boundary = content_type.split('boundary=')[1].encode('ascii')
                    parts = raw_body.split(b'--' + boundary)
                    for part in parts:
                        m = re.search(rb'name="([^"]+)"\r?\n\r?\n(.*?)\r?\n?$', part, re.S)
                        if m:
                            form_fields[m.group(1).decode('utf-8', errors='ignore')] = m.group(2).decode('utf-8', errors='ignore').strip()
                else:
                    qs = urllib.parse.parse_qs(raw_body.decode('utf-8', errors='ignore'))
                    for k, v in qs.items():
                        form_fields[k] = v[0] if v else ''

                system = form_fields.get('system', '')
                reason = form_fields.get('reason', '')
                raw_json = form_fields.get('raw_json', '')

                archive_dir = DevHandler.archive_dir or resolve_archive_dir()
                archive_emu = archive_dir / 'emulators.json' if archive_dir else None
                emu_file = pathlib.Path(self.directory) / 'assets' / 'emulators.json'
                src_emu = REPO_ROOT / 'assets' / 'emulators.json'

                doc = {}
                if archive_emu and archive_emu.exists():
                    try: doc = json.loads(archive_emu.read_text(encoding='utf-8'))
                    except Exception: pass
                elif emu_file.exists():
                    try: doc = json.loads(emu_file.read_text(encoding='utf-8'))
                    except Exception: pass
                elif src_emu.exists():
                    try: doc = json.loads(src_emu.read_text(encoding='utf-8'))
                    except Exception: pass

                if 'systems' not in doc:
                    doc['systems'] = {}
                if system not in doc['systems']:
                    doc['systems'][system] = {}
                changed = []
                if 'quick_chips' in form_fields:
                    qc = json.loads(form_fields['quick_chips'])
                    doc['systems'][system]['quick_chips'] = qc
                    changed.append('quick_chips')
                if 'tools' in form_fields:
                    tools = json.loads(form_fields['tools'])
                    cat_map = {c.get('id'): c for c in doc.get('catalog', [])}
                    tools = [t for t in tools if cat_map.get(t.get('id', ''), {}).get('kind') != 'game_tool']
                    doc['systems'][system]['tools'] = tools
                    changed.append('tools')
                if 'catalog' in form_fields:
                    catalog = json.loads(form_fields['catalog'])
                    doc['catalog'] = catalog
                    changed.append('catalog')

                clean_doc = {}
                if 'systems' in doc and isinstance(doc['systems'], dict):
                    s_map = doc['systems']
                    sorted_s = {}
                    if 'default' in s_map:
                        sorted_s['default'] = s_map['default']
                    for k in sorted(s_map.keys()):
                        if k != 'default':
                            sorted_s[k] = s_map[k]
                    clean_doc['systems'] = sorted_s
                if 'catalog' in doc:
                    clean_doc['catalog'] = doc['catalog']
                elif 'presets' in doc:
                    clean_doc['presets'] = doc['presets']
                for k, v in doc.items():
                    if k not in clean_doc:
                        clean_doc[k] = v

                out_str = json.dumps(clean_doc, indent=1, ensure_ascii=False) + '\n'
                if archive_emu and archive_emu.parent.exists():
                    archive_emu.write_text(out_str, encoding='utf-8')
                emu_file.parent.mkdir(parents=True, exist_ok=True)
                emu_file.write_text(out_str, encoding='utf-8')
                if src_emu.exists():
                    src_emu.write_text(out_str, encoding='utf-8')

                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(json.dumps({'ok': True, 'system': system, 'serial': 1076, 'changed': changed}).encode('utf-8'))
                return
            except Exception as e:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                self.wfile.write(json.dumps({'ok': False, 'error': str(e)}).encode('utf-8'))
                return

        # Generic success answer for expert actions (save edit, add category, reorder, etc.)
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.end_headers()
        resp = {'ok': True, 'serial': 1076, 'message': 'Simulated dev action successful'}
        self.wfile.write(json.dumps(resp).encode('utf-8'))


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


def main():
    parser = argparse.ArgumentParser(description="Local dev server for toolAssisted.run")
    parser.add_argument('-p', '--port', type=int, default=8000, help="Port to listen on (default: 8000)")
    parser.add_argument('-H', '--host', default='127.0.0.1', help="Host interface (default: 127.0.0.1)")
    parser.add_argument('-b', '--rebuild', action='store_true', help="Rebuild site before starting")
    parser.add_argument('-a', '--archive', '--archive-dir', type=str, default=None, dest='archive', help="Path to ToolAssisted-archive checkout")
    parser.add_argument('-o', '--open', action='store_true', help="Open in default browser after launch")
    parser.add_argument('-u', '--user', type=str, default=None, help="Mock username to explicitly log in as (e.g. 'GMP'; default: None, logged out)")
    parser.add_argument('--logged-out', action='store_true', help="Start in logged-out mode (default)")
    parser.add_argument('--out', type=str, default='stage-build', help="Output directory (default: stage-build)")

    args = parser.parse_args()

    out_dir = (REPO_ROOT / args.out).resolve()
    archive_dir = resolve_archive_dir(args.archive)
    DevHandler.archive_dir = archive_dir

    if args.logged_out or not args.user:
        DevHandler.mock_user = None
    else:
        DevHandler.mock_user = args.user

    if args.rebuild or not (out_dir / 'index.html').exists():
        build_site(archive_dir, out_dir)
    else:
        print(f"Using existing build at: {out_dir}\n")

    handler = lambda *h_args, **h_kwargs: DevHandler(*h_args, directory=out_dir, **h_kwargs)

    url = f"http://{args.host}:{args.port}/"
    print(f"==================================================")
    print(f" toolAssisted.run local development server")
    print(f" Archive: {archive_dir}")
    print(f" Serving: {out_dir}")
    print(f" URL:     {url}")
    if DevHandler.mock_user:
        print(f" User:    Logged in as '{DevHandler.mock_user}'")
    else:
        print(f" User:    Logged out")
    print(f" Press Ctrl+C to stop.")
    print(f"==================================================\n")

    if args.open:
        webbrowser.open(url)

    try:
        with ReusableTCPServer((args.host, args.port), handler) as httpd:
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    except OSError as e:
        if e.errno == 10048 or "Address already in use" in str(e):
            print(f"\nError: Port {args.port} is already in use. Try passing --port <other_port>", file=sys.stderr)
        else:
            raise


if __name__ == '__main__':
    main()
