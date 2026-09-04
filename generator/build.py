#!/usr/bin/env python3
"""toolAssisted.run static site generator — the Controller.

Reads the archive (a checkout of ToolAssisted-run/archive) and emits the
site. Facts in, pages out: rankings, records, verification states and
contributor points are derived from the stored facts (rosters), never stored.

Architecture (see ARCHITECTURE.md):
  config.py    deployment constants (Model and View both read it)
  model.py     the archive loaded + every derivation        (Model)
  render.py    tpl() + the HTML helpers templates call    (View glue)
  assets/      app.js + style.css, real files               (View)
  templates/   the markup, Jinja2, one file per page       (View)
  views/*.py   one page family per module, data prep only (View)
  build.py     scaffolding and build order                  (Controller)

Usage: build.py [archive_dir] [out_dir]
"""
import datetime
import json
import pathlib
import shutil

from config import ARCHIVE_SERIAL, OUT, REPO_ROOT, SITE_COMMIT
import providers  # noqa: E402  (config put archivist/ on the path)
from render import ship_thumbnails

# ---------------- output scaffolding ----------------
# ---------------- build ----------------
if OUT.exists(): shutil.rmtree(OUT)
(OUT / 'assets').mkdir(parents=True)
ship_thumbnails()          # before any page is written: the pages point at them
for asset in (pathlib.Path(__file__).resolve().parent / 'assets').glob('*'):
    shutil.copy2(asset, OUT / 'assets' / asset.name)
# Frontend modules (assets/app.js, assets/page-*.js) live with the website
# source rather than the generator scaffolding; shipped further down,
# alongside the provider-name substitution they may need.
# ship the HTTPS-redirect .htaccess with every deploy (site lives at the root).
# Apache reads it; GitHub Pages ignores it and does HTTPS and HSTS itself. The
# one thing Pages cannot do is a rewrite, so the /stage/ links that predate the
# root launch are forwarded by the 404 page below instead.
_htaccess = pathlib.Path(__file__).resolve().parent.parent / '.htaccess'
if _htaccess.exists():
    shutil.copy2(_htaccess, OUT / '.htaccess')
# design mocks ride along at /mock/ so a console-bound reviewer can look at
# them on the live site; the page is self-contained, noindexed, and exempt
# from the site-chrome output invariants
_mock = pathlib.Path(__file__).resolve().parent.parent / 'mockups' / 'index.html'
if _mock.exists():
    (OUT / 'mock').mkdir(exist_ok=True)
    shutil.copy2(_mock, OUT / 'mock' / 'index.html')
# GitHub Pages reads the custom domain from the repository settings, but a
# CNAME in the upload is what keeps it from ever being dropped by a deploy.
(OUT / 'CNAME').write_text('toolassisted.run\n')
# the freshness beacon: Pages serves every page with max-age=600, so a
# browser can show a 10-minute-old leaderboard while the site is long since
# rebuilt. Each page knows the build it came from (window.TAR.v); the client
# fetches this tiny file uncached and offers a refresh when they differ.
(OUT / 'assets' / 'buildstamp.json').write_text(json.dumps(
    {'v': SITE_COMMIT or 'dev',
     'serial': ARCHIVE_SERIAL,
     'built': datetime.datetime.now(datetime.timezone.utc)
              .strftime('%Y-%m-%dT%H:%M:%SZ')}))
(OUT / 'runs').mkdir()
(OUT / 'authors').mkdir()
(OUT / 'policy').mkdir()



# ---------------- the pages, in build order ----------------
import views.run_pages  # noqa: F401,E402  (renders its pages on import)
import views.game_pages  # noqa: F401,E402  (renders its pages on import)
import views.member_pages  # noqa: F401,E402  (renders its pages on import)
import views.browse  # noqa: F401,E402  (renders its pages on import)
import views.games_index  # noqa: F401,E402  (renders its pages on import)
import views.members_index  # noqa: F401,E402  (renders its pages on import)
import views.panels  # noqa: F401,E402  (renders its pages on import)
import views.contribute  # noqa: F401,E402  (renders its pages on import)
import views.submit  # noqa: F401,E402  (renders its pages on import)
import views.create_pages  # noqa: F401,E402  (renders its pages on import)
import views.tools  # noqa: F401,E402  (renders its pages on import)
import views.claim_import  # noqa: F401,E402  (renders its pages on import)
import views.home  # noqa: F401,E402  (renders its pages on import)
import views.sitelog  # noqa: F401,E402  (renders its pages on import)

# ---------------- shared assets ----------------
# ---- shared client scripts ----
# The client scripts are real files (assets/app.js, assets/page-*.js): the
# frontend lives apart from the backend and meets it only through the
# archivist's JSON API and the JSON blobs embedded in pages. Shipped
# verbatim, except the accepted-platform substitutions (from the providers
# module), which land wherever ENCODE_HOSTS/ENCODE_NAMES actually appear
# (today, only the submit page's encode-link check).
def _ship_script(name):
    (OUT / 'assets' / name).write_text(
        (REPO_ROOT / 'assets' / name).read_text(encoding='utf-8')
        .replace('ENCODE_HOSTS', '|'.join(providers.ALL_HOSTS))
        .replace('ENCODE_NAMES', ' · '.join(providers.names())), encoding='utf-8')


for _script in (REPO_ROOT / 'assets').glob('*.js'):
    _ship_script(_script.name)

# ---- stylesheet ----
# A real file (assets/style.css), shipped verbatim.
(OUT / 'assets' / 'style.css').write_text(
    (REPO_ROOT / 'assets' / 'style.css').read_text(encoding='utf-8'),
    encoding='utf-8')
print(f'built {sum(1 for _ in OUT.rglob("*") if _.is_file())} files -> {OUT}')

# ---------------- search engines: sitemap and robots ----------------
# Every public content page, with the day it last changed; tooling pages
# (submit, panels, editors, mocks) carry noindex and stay out of the map.
import re as _re
SITE = 'https://toolassisted.run'
_skip = _re.compile(r'^(submit|claim|import|expert|founder|committee|create-game|'
                    r'create-category|mock)/|/edit/$')
_entries = []
for _p in sorted(OUT.rglob('index.html')):
    _relp = _p.relative_to(OUT).as_posix()[:-len('index.html')]
    if _skip.search(_relp) or '<meta name="robots" content="noindex">' in _p.read_text():
        continue
    _entries.append(_relp)
_today = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')
(OUT / 'sitemap.xml').write_text(
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    + ''.join(f'<url><loc>{SITE}/{_e}</loc><lastmod>{_today}</lastmod></url>\n' for _e in _entries)
    + '</urlset>\n')
(OUT / 'robots.txt').write_text(
    'User-agent: *\nAllow: /\n'
    + ''.join(f'Disallow: /{_d}/\n' for _d in ('submit', 'claim', 'import', 'expert', 'founder',
                                                 'committee', 'create-game', 'create-category', 'mock'))
    + 'Disallow: /games/*/edit/\n'
    + f'Sitemap: {SITE}/sitemap.xml\n')
print(f'sitemap: {len(_entries)} urls')
