#!/usr/bin/env python3
"""toolAssisted.run static site generator — the Controller.

Reads the archive (a checkout of ToolAssisted-run/archive) and emits the
site. Facts in, pages out: rankings, records, verification states and
contributor points are derived from the stored facts (rosters), never stored.

Architecture (see ARCHITECTURE.md):
  config.py    deployment constants (Model and View both read it)
  model.py     the archive loaded + every derivation        (Model)
  render.py    HTML helpers, page chrome, asset registries  (View glue)
  assets/      app.js + style.css, real files               (View)
  views/*.py   one page family per module                   (View)
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
# ---- shared client script ----
# The client script is a real file (assets/app.js): the frontend lives
# apart from the backend and meets it only through the archivist's JSON
# API and the JSON blobs embedded in pages. Shipped verbatim, except the
# accepted-platform substitutions, which come from the providers module.
(OUT / 'assets' / 'app.js').write_text(
    (REPO_ROOT / 'assets' / 'app.js').read_text()
    .replace('ENCODE_HOSTS', '|'.join(providers.ALL_HOSTS))
    .replace('ENCODE_NAMES', ' · '.join(providers.names())))

# ---- stylesheet ----
# A real file (assets/style.css), shipped verbatim.
(OUT / 'assets' / 'style.css').write_text(
    (REPO_ROOT / 'assets' / 'style.css').read_text())
print(f'built {sum(1 for _ in OUT.rglob("*") if _.is_file())} files -> {OUT}')
