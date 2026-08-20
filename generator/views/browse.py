"""View: browse (renders on import; see views/__init__)."""
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
    run_seconds,
    archived_at,
    cat_label,
    eff_state,
    is_ranked,
    is_unclassified,
    nlikes,
    runs,
    systems,
)
from render import (
    esc,
    page,
    run_clock,
)

# ---- browse page (client-side search / facets / sort) ----
index = []
for r in sorted(runs, key=archived_at, reverse=True):
    g = r['_game']
    rs, vs = eff_state(r)
    state = ('imported' if rs == 'imported' else
             'unclassified' if is_unclassified(r) else
             'verified' if is_ranked(r) and vs in ('confirmed', 'imported') else
             'provisional' if is_ranked(r) else 'pending')
    index.append({
        'id': r['id'], 'title': g['title'], 'sys': g['system'],
        'sysname': systems[g['system']]['name'], 'cat': cat_label(r),
        'authors': [a['user'] for a in r['authors']],
        'frames': (None if r.get('videoOnly') else r['movie']['frames']),
        'secs': run_seconds(r),
        'time': run_clock(r),
        'stars': nlikes(r),
        'date': archived_at(r)[:10], 'state': state,
    })
sys_opts = ''.join(f'<option value="{esc(k)}">{esc(v["name"])}</option>' for k, v in sorted(systems.items()))
browse_js = '''<script>
var RUNS = INDEX_JSON;
var q = document.getElementById('bq'), sysf = document.getElementById('bsys'),
    stf = document.getElementById('bst'), sortf = document.getElementById('bsort'),
    tbody = document.getElementById('brows'), count = document.getElementById('bcount');
var params = new URLSearchParams(location.search);
if (params.get('q')) q.value = params.get('q');
function chip(state){
  return {imported:'<span class="chip importedchip">Imported</span>',
          verified:'<span class="chip verchip">Verified (expert)</span>',
          provisional:'<span class="chip provchip">Verified</span>',
          unclassified:'<span class="chip unclchip">Unclassified</span>',
          pending:'<span class="chip pendchip">Pending</span>'}[state];
}
function render(){
  var needle = q.value.toLowerCase(), sys = sysf.value, st = stf.value;
  var rs = RUNS.filter(function(r){
    if (sys && r.sys !== sys) return false;
    if (st === 'ranked' && (r.state === 'pending')) return false;
    if (st && st !== 'ranked' && r.state !== st) return false;
    if (needle){
      var hay = (r.title + ' ' + r.cat + ' ' + r.authors.join(' ') + ' ' + r.id).toLowerCase();
      if (hay.indexOf(needle) === -1) return false;
    }
    return true;
  });
  if (sortf.value === 'stars') rs.sort(function(a,b){ return b.stars - a.stars; });
  else if (sortf.value === 'title') rs.sort(function(a,b){ return a.title.localeCompare(b.title); });
  else rs.sort(function(a,b){ return b.date.localeCompare(a.date); });
  count.textContent = rs.length + ' run' + (rs.length === 1 ? '' : 's');
  tbody.innerHTML = rs.map(function(r){
    return '<tr onclick="location=\\'../runs/' + r.id + '/\\'">' +
      '<td><b>' + r.title + '</b><span class="bcat">' + r.cat + '</span></td>' +
      '<td class="bsys">' + r.sysname + '</td>' +
      '<td>' + r.authors.join(', ') + '</td>' +
      '<td class="num">' + (r.frames === null ? '<span class="u">video</span>'
                                  : r.frames.toLocaleString() + '<span class="u">f</span>') + '</td>' +
      '<td class="num">' + r.time + '</td>' +
      '<td class="num"><span class="starglyph">★</span>' + r.stars + '</td>' +
      '<td>' + r.date + '</td>' +
      '<td>' + chip(r.state) + '</td></tr>';
  }).join('');
}
[q, sysf, stf, sortf].forEach(function(el){
  el.addEventListener('input', render); el.addEventListener('change', render);
});
render();
</script>'''.replace('INDEX_JSON', json.dumps(index).replace('<', chr(92) + 'u003c'))
body = f'''<header class="ghead"><div><h1>Movies</h1>
<p class="authline">Every run in the archive, the moment it arrives.</p></div></header>
<div class="filters">
<input id="bq" type="search" placeholder="Search game, category, author…">
<select id="bsys"><option value="">All systems</option>{sys_opts}</select>
<select id="bst"><option value="">All statuses</option><option value="ranked">Ranked</option>
<option value="verified">Verified (expert)</option><option value="provisional">Verified</option>
<option value="pending">Pending</option><option value="imported">Imported</option>
<option value="unclassified">Unclassified</option></select>
<select id="bsort"><option value="date">Newest first</option>
<option value="stars">Most stars</option>
<option value="title">By title</option></select>
<span id="bcount" class="bcount"></span></div>
<table class="btable"><thead><tr><th>Game</th><th>System</th><th>Authors</th>
<th class="num">Frames</th><th class="num">Time</th><th class="num"><span class="starglyph">★</span></th><th>Archived</th><th>Status</th></tr></thead>
<tbody id="brows"></tbody></table>
{browse_js}'''
(OUT / 'browse').mkdir(exist_ok=True)
(OUT / 'browse' / 'index.html').write_text(page('Movies', body, '../', '', 'Movies'))

