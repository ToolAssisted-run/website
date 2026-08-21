"""View: contribute (renders on import; see views/__init__)."""
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
    OUT,
)
from model import (
    PT_CONSOLE,
    PT_NEGLECT_CAP,
    PT_NEGLECT_PER_DAY,
    PT_REPRO_FIRST,
    PT_REPRO_HARD,
    PT_REPRO_LATER,
    PT_VERIFY,
    verify_bounty,
    cat_label,
    covering_experts,
    days_pending,
    eff_state,
    is_unclassified,
    points,
    repro_bounty,
    runs,
    systems,
)
from render import (
    moment,
    badge_chip,
    esc,
    member_chip,
    page,
)

# ---- contribute page ----
need_repro = sorted([r for r in runs if eff_state(r)[0] == 'none' and not r.get('videoOnly')],
                    key=lambda r: repro_bounty(r), reverse=True)
need_verify = [r for r in runs
               if eff_state(r)[0] != 'imported' and not is_unclassified(r)
               and eff_state(r)[1] != 'confirmed']
nr_rows = ''.join(f'''<tr data-sys="{esc(r['_game']['system'])}" onclick="window.open('../runs/{r['id']}/', '_blank')">
<td><b>{esc(r['_game']['title'])}</b><span class="bcat">{esc(cat_label(r))}</span></td>
<td>{esc(systems[r['_game']['system']]['name'])}</td>
<td>{', '.join(esc(a['user']) for a in r['authors'])}</td>
<td>{days_pending(r)} day{'s' if days_pending(r)!=1 else ''}</td>
<td class="num"><b class="bounty">{repro_bounty(r)}</b> pts</td></tr>''' for r in need_repro)
nv_rows = ''.join(f'''<tr data-sys="{esc(r['_game']['system'])}"'''
                  f'''{' data-verified-ranked="1"' if eff_state(r)[1] == 'provisional' else ''}'''
                  f''' data-experts="{esc(','.join(covering_experts(r['_game']['key'])))}"'''
                  f''' onclick="window.open('../runs/{r['id']}/', '_blank')">'''
                  f'''<td><b>{esc(r['_game']['title'])}</b><span class="bcat">{esc(cat_label(r))}</span></td>
<td>{esc(systems[r['_game']['system']]['name'])}</td>
<td>{', '.join(esc(a['user']) for a in r['authors'])}</td>
<td class="num"><b class="bounty">{verify_bounty(r)}</b> pts</td></tr>''' for r in need_verify)
nv_table_html = f'<div class="contscroll"><table id="nv-table"><thead><tr><th>Run</th><th>System</th><th>Authors</th><th class="num">Bounty</th></tr></thead><tbody>{nv_rows}</tbody></table></div><p id="nv-empty" class="emptynote" style="display: none;">Nothing waiting: every run that can be verified has been.</p>' if nv_rows else '<p class="emptynote">Nothing waiting: every run that can be verified has been.</p>'
nr_table_html = f'<div class="contscroll nrgap" id="nr-scroll"><table><thead><tr><th>Run</th><th>System</th><th>Authors</th><th>Waiting</th><th class="num">Bounty</th></tr></thead><tbody>{nr_rows}</tbody></table></div>' if nr_rows else '<p class="emptynote">Nothing waiting: every archived run has been reproduced. New submissions will appear here the moment they arrive.</p>'
# the filter serves the reproduction list alone: verifying only takes
# watching a video, so what systems you can RUN is irrelevant there
worklist_systems = sorted({r['_game']['system'] for r in need_repro})
sysfilter = ''
if worklist_systems:
    btns = ''.join(f'<button class="dimopt sysopt" data-sys="{esc(s)}">{esc(systems[s]["name"])}</button>'
                   for s in worklist_systems)
    sysfilter = f'''<div class="dimrow" id="sysfilter"><span class="dimname">Systems I can run</span>
{btns}</div>
<script>
(function(){{
  var KEY = 'tar-my-systems';
  var btns = document.querySelectorAll('#sysfilter .sysopt');
  var mine = null;
  try {{ mine = JSON.parse(localStorage.getItem(KEY) || 'null'); }} catch (e) {{}}
  function apply(){{
    var active = mine === null ? null : mine;
    btns.forEach(function(b){{
      b.classList.toggle('on', active === null || active.indexOf(b.dataset.sys) >= 0);
    }});
    document.querySelectorAll('#nr-scroll tr[data-sys]').forEach(function(tr){{
      tr.style.display = (active === null || active.indexOf(tr.dataset.sys) >= 0) ? '' : 'none';
    }});
  }}
  btns.forEach(function(b){{
    b.addEventListener('click', function(){{
      var all = Array.prototype.map.call(btns, function(x){{ return x.dataset.sys; }});
      if (mine === null) mine = all.slice();
      var i = mine.indexOf(b.dataset.sys);
      if (i >= 0) mine.splice(i, 1); else mine.push(b.dataset.sys);
      if (mine.length === all.length) mine = null;
      try {{ mine === null ? localStorage.removeItem(KEY)
                           : localStorage.setItem(KEY, JSON.stringify(mine)); }} catch (e) {{}}
      apply();
    }});
  }});
  apply();
}})();
</script>'''
open_cases = [(r, c) for r in runs for c in r.get('cases', []) if c['status'] == 'open']
open_cases_box = ''
if open_cases:
    items = ''.join(
        f'<p class="statline"><a href="../runs/{r["id"]}/">{esc(r["_game"]["title"])} · case {c["id"]}</a> '
        f'({len(c.get("reaffirmations", []))} of {len(c["verifiers"])} votes)</p>'
        for r, c in open_cases)
    open_cases_box = f'<div class="factbox"><h4>Open cases</h4>{items}</div>'
# The board says who has done the most; this says what was done last. A
# worklist with nothing moving on it looks abandoned, and the contribution
# that lands today is the best argument that the next one is worth making.
# Ten, not five: act dates are day-granular, so a busy day ties and the
# oldest ids fall off first; five was small enough for one day to evict a
# member's act before anybody saw it.
LATEST_N = 10
ACT_ICON = {'first reproduction': ('reproduced', '↻'), 'reproduction': ('reproduced', '↻'),
            'verification': ('verified', '✓'), 'console verification': ('console', '✓')}
latest_acts = sorted(((date, desc, pts, r, p['user'])
                      for p in points.values() for date, desc, pts, r in p['acts'] if date),
                     key=lambda a: (a[0], a[3]['id']), reverse=True)[:LATEST_N]
latest_items = ''.join(
    f'<p class="statline newsline">'
    f'<span class="newsico {ACT_ICON.get(desc, ("verified", "✓"))[0]}">'
    f'{ACT_ICON.get(desc, ("verified", "✓"))[1]}</span> '
    f'{member_chip(who, "../")} {esc(desc.replace("console verification", "played on hardware")
                                     .replace("first reproduction", "first-reproduced")
                                     .replace("reproduction", "reproduced")
                                     .replace("verification", "verified"))} '
    f'<a href="../runs/{r["id"]}/">{esc(r["_game"]["title"])}</a>'
    f'<span class="actmeta"> {esc(moment(date))} · +{pts}</span></p>'
    for date, desc, pts, r, who in latest_acts)
latest_box = (f'<div class="factbox"><h4>Latest contributions</h4>{latest_items}</div>'
              if latest_items else
              '<div class="factbox"><h4>Latest contributions</h4>'
              '<p class="emptynote">Nothing yet. The first reproduction or verification '
              'here will be the first thing on this list.</p></div>')
top = sorted(points.values(), key=lambda p: -p['points'])[:10]
top_rows = ''.join(f'''<tr><td class="rank">{i+1}</td><td>{member_chip(p['user'], '../')}{badge_chip(p['points'])}</td>
<td class="num">{p['points']}</td></tr>''' for i, p in enumerate(top))
board = (f'<table><thead><tr><th>#</th><th>Member</th><th class="num">Points</th></tr></thead>'
         f'<tbody>{top_rows}</tbody></table>' if top else
         '<p class="emptynote">Nobody has earned contributor points yet. The board is wide open.</p>')
body = f'''<header class="ghead"><div><h1>Contribute</h1>
<p class="authline">The public worklist. No claiming, no assignment; anyone may do anything,
anytime; the first to finish earns the points.</p></div></header>
<div class="cols">
<div class="main">
<section><h2>Needs verification</h2>
<p class="rules fullw">Watch the encode and confirm the run achieves its stated category goal. <b>One
verification ranks the run</b>, shown as verified; a covering expert's makes it
permanent. The bounty <b>rises one point per day</b> the run waits, up to double.</p>
{nv_table_html}</section>
<section><h2>Needs reproduction</h2>
<p class="rules fullw">Load the movie file on your own setup, confirm it syncs to the end, and submit an
ending screenshot as proof. Reproduction is the archive's assurance that the movie really
plays, recorded and paid; it does not gate ranking. The bounty <b>rises the longer a run sits
unreproduced</b>: the obscure long tail is the best-paying work on the board.</p>
{sysfilter}
<<<<<<< HEAD
{f'<div class="contscroll nrgap" id="nr-scroll"><table><thead><tr><th>Run</th><th>System</th><th>Authors</th><th>Waiting</th><th class="num">Bounty</th></tr></thead><tbody>{nr_rows}</tbody></table></div>' if nr_rows else '<p class="emptynote">Nothing waiting: every archived run has been reproduced. New submissions will appear here the moment they arrive.</p>'}</section>
=======
{nr_table_html}</section>
>>>>>>> 225c7cf ([Contribute] Regular users don't see verified runs in "Need verification" section)

</div>
<aside class="side">
{latest_box}
<div class="factbox"><h4>Contributor board</h4>{board}</div>
<div class="factbox"><h4>How points work</h4>
<ul class="factlist">
<li>First reproduction: <b>{PT_REPRO_FIRST}+</b> pts, rising {PT_NEGLECT_PER_DAY}/day while the run waits (up to +{PT_NEGLECT_CAP})</li>
<li>Later reproductions: <b>{PT_REPRO_LATER}</b> pts</li>
<li>Hard-to-reproduce systems: <b>+{PT_REPRO_HARD}</b> pts on any reproduction</li>
<li>Verification: <b>{PT_VERIFY}</b> pts</li>
<li>Console verification: <b>{PT_CONSOLE}</b> pts; real hardware and a public recording, never required for ranking</li>
<li>Badges at <b>1k / 5k / 10k / 25k</b> points</li>
</ul>
<p class="statline muted">Weights are provisional while the community settles them.</p></div>
<div class="factbox"><h4>The rules</h4>
<ul class="factlist">
<li>You cannot verify or confirm reproduction for your own run.</li>
<li>One reproduction and one verification per member per run.</li>
<li>You keep the points, even if an expert later overrules your contribution.</li>
<li>Contradictions open a case; nothing is removed automatically.</li>
</ul></div>
{open_cases_box}
</aside></div>'''
(OUT / 'contribute').mkdir(exist_ok=True)
(OUT / 'contribute' / 'index.html').write_text(page('Contribute', body, '../', '', 'Contribute'))
