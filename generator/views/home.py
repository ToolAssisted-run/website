"""View: home (renders on import; see views/__init__)."""
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
from config import (
    FORUM,
    OUT,
)
from model import (
    archived_at,
    authors,
    cat_label,
    eff_state,
    games,
    is_pending,
    is_ranked,
    nlikes,
    runs,
    systems,
)
from render import (
    esc,
    page,
    primary_metric_text,
    run_clock,
    thumb_html,
)

# ---- home ----
pending_count = sum(1 for r in runs if is_pending(r))
def fresh_selection(all_runs, slots=8):
    """The last runs to arrive, newest first. No balancing: the shelf answers
    "what was just added", so a bulk import legitimately fills it while it is
    the newest thing here."""
    return sorted(all_runs, key=lambda r: (archived_at(r), r.get('submitted') or '',
                                           r['id']), reverse=True)[:slots]

cards = []
for r in fresh_selection(runs):
    g = r['_game']
    au = ', '.join(a['user'] for a in r['authors'])
    rs, vs = eff_state(r)
    sm = ('<span class="importedsm">Imported</span>' if rs == 'imported' else
          '<span class="versm">Verified</span>' if is_ranked(r) else
          '<span class="pendsm">Pending</span>')
    cards.append(f'''<a class="card" href="runs/{r['id']}/">
{thumb_html(r, f'<span class="dur">{esc(primary_metric_text(r))}</span>')}
<span class="cbody"><b>{esc(g['title'])}</b><span class="ccat">{esc(cat_label(r))}</span>
<span class="cauth">{esc(au)}</span>
<span class="cfoot"><span>{run_clock(r) if r.get('videoOnly') else f"{r['movie']['frames']:,}f"}</span><span><span class="starglyph">★</span>{nlikes(r)}</span>{sm}</span></span></a>''')
stats = f'''<div class="statstrip">
<div class="stat"><b>{len(runs)}</b><span>runs</span></div>
<div class="stat"><b>{len(games)}</b><span>games</span></div>
<div class="stat"><b>{len(systems)}</b><span>systems</span></div>
<div class="stat"><b>{len(authors)}</b><span>authors</span></div>
<div class="stat"><b>{pending_count}</b><span>pending</span></div>
</div>'''
body = f'''<section class="hero">
<div class="herogrid">
<div class="herotext">
<h1>Games, played beyond human limits.</h1>
<p><b>toolAssisted.run</b> is an open community archive of tool-assisted speedruns, score
attacks and superplays. Every verifiable work is preserved the moment it arrives; merit is
decided in the open, by the people who care about it.</p>
<div class="herobtns"><a class="btn" href="browse/"><span class="wide">Browse the archive</span><span class="narrow">Browse</span></a>
<a class="btn quiet" href="contribute/">Contribute</a>
<a class="btn quiet" href="https://github.com/ToolAssisted-run#1-community-principles"><span class="wide">Community Principles</span><span class="narrow">Principles</span></a></div>
{stats}
</div>
<aside class="heronews" id="heronews">
<h2>News &amp; Events</h2>
<div class="bskyfeed" id="bskyfeed" data-handle="toolassisted.run">
<p class="emptynote">Loading the latest posts…</p>
</div>
</aside>
</div></section>
<section><h2>Freshly archived</h2><div class="grid">{''.join(cards)}</div></section>
<section class="homefoot"><div class="cols3">
<div class="factbox"><h4>Archive first</h4><p class="statline">Runs are archived instantly into
<a href="https://github.com/ToolAssisted-run/archive">the public archive</a> and appear
immediately, as pending. Ranking needs one community gate: verification.</p></div>
<div class="factbox"><h4>Open by construction</h4><p class="statline">The whole archive is
<a href="https://github.com/ToolAssisted-run/archive">a public git repository</a> under CC BY 4.0.
The site is generated from it; clone it and you hold the same facts we do.</p></div>
<div class="factbox"><h4>Join in</h4><p class="statline">Reproduce and verify runs on the
<a href="contribute/">Contribute board</a>, talk shop on
<a href="{FORUM}">the forum</a>, or come chat on
<a href="https://discord.gg/VsKDT9XB6u">our Discord</a>.</p></div>
</div></section>'''
(OUT / 'index.html').write_text(page('Home', body))

# ---- 404 ----
# Deliberately self-contained: a 404 is served for a path of any depth, so the
# browser resolves relative links against wherever the reader happened to be.
# It also carries the one rewrite the .htaccess did that GitHub Pages cannot:
# /stage/... predates the root launch and still turns up in old forum posts.
(OUT / '404.html').write_text(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Not found · toolAssisted.run</title>
<style>
body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#EEF2F6;color:#0F172A;font:16px/1.6 Inter,system-ui,sans-serif;padding:24px}}
.box{{background:#fff;border:2.5px solid #C6D2DF;border-radius:12px;padding:28px 32px;max-width:36rem}}
h1{{margin:0 0 6px;font:700 1.6rem/1.2 ui-monospace,monospace}}
p{{margin:10px 0;color:#475569}}
a{{color:#15803D;font-weight:600}}
code{{font-family:ui-monospace,monospace;background:#EEF2F6;padding:1px 5px;border-radius:5px}}
@media (prefers-color-scheme:dark){{body{{background:#070D17;color:#F1F5F9}}
.box{{background:#101A2C;border-color:#22334A}}p{{color:#94A3B8}}a{{color:#22C55E}}
code{{background:#070D17}}}}
</style></head><body>
<div class="box">
<h1>404</h1>
<p>There is nothing at this address. It may have been a run that was withdrawn,
a page that moved, or a typo.</p>
<p><a href="/">Home</a> · <a href="/browse/">Browse the archive</a> ·
<a href="/games/">Games</a> · <a href="{FORUM}">Forum</a></p>
</div>
<script>
// the beta lived under /stage/ for a day in August 2026; those links still
// exist in forum posts, so send them to the same page at the root
(function(){{
  var p = location.pathname;
  if (p.indexOf('/stage/') === 0) location.replace(p.slice(6) + location.search + location.hash);
  else if (p === '/stage') location.replace('/');
}})();
</script>
</body></html>
""")

