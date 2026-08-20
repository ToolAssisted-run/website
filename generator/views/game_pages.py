"""View: game pages (renders on import; see views/__init__)."""
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
    CLASSIC_METRICS,
    covering_experts,
    eff_state,
    games,
    is_ranked,
    is_unclassified,
    metric_value,
    nlikes,
    rank_key,
    run_seconds,
    systems,
)
from render import (
    FULL_TICK,
    IMPORTED_TICK,
    METRICS_ED,
    NONE_TICK,
    PROV_TICK,
    SHIPPED_GAME_THUMBS,
    author_chip,
    console_tick,
    esc,
    expert_line,
    fmt_metric,
    frames_html,
    group_chip,
    inline,
    member_chip,
    page,
    run_clock,
    tick,
)

# ---- game pages (leaderboards with category selector) ----
def combo_iter(dims):
    """All cartesian combinations of dimension options, as lists of (dim, option)."""
    combos = [[]]
    for d in dims:
        combos = [c + [(d, o)] for c in combos for o in d['options']]
    return combos

for key, g in games.items():
    gd = OUT / 'games' / key
    gd.mkdir(parents=True)
    rel = '../../../'
    dims = g['categories']['dimensions']
    multi = sum(len(d['options']) for d in dims) > len(dims)

    # selector: one row of buttons per dimension
    selector = ''
    if multi:
        rows = []
        for d in dims:
            btns = ''.join(f'<button class="dimopt" data-dim="{esc(d["key"])}" data-opt="{esc(o["key"])}">'
                           f'{esc(o["label"])}</button>' for o in d['options'])
            rows.append(f'<div class="dimrow"><span class="dimname">{esc(d["name"])}</span>{btns}</div>')
        selector = f'<div class="dimsel">{"".join(rows)}</div>'

    sections = []
    default_combo = None
    for combo in combo_iter(dims):
        ckey = '|'.join(o['key'] for _, o in combo)
        label = ' × '.join(o['label'] for _, o in combo)
        rules = ' '.join(o['rule'] for _, o in combo)
        allrs = [r for r in g['runs'] if all(r['category'][d['key']] == o['key'] for d, o in combo)]
        mdefs = next((o.get('metrics') for _, o in combo if o.get('metrics')),
                     None) or CLASSIC_METRICS
        mth = ''.join(f'<th class="num">{esc(m["label"])}</th>' for m in mdefs)

        def mcells(r):
            return ''.join(f'<td class="num">{fmt_metric(metric_value(r, m), m)}</td>'
                           for m in mdefs)
        ranked_all = sorted([r for r in allrs if is_ranked(r)], key=rank_key)
        # one run per author (set) per category: fastest counts, rest is history
        table_runs, history, seen_sets = [], [], set()
        for r in ranked_all:
            aset = frozenset(a['user'].lower() for a in r['authors'])
            if aset in seen_sets:
                history.append(r)
            else:
                seen_sets.add(aset)
                table_runs.append(r)
        pend = sorted([r for r in allrs if not is_ranked(r)], key=lambda r: r.get('submitted') or '', reverse=True)
        if allrs and default_combo is None:
            default_combo = ckey
        rows = []
        for i, r in enumerate(table_runs):
            rs_, vs_ = eff_state(r)
            au = ' · '.join(author_chip(a['user'], rel) for a in r['authors'])
            rows.append(f'''<tr class="{'lead' if i==0 else ''}" onclick="if(!event.target.closest('a'))location='{rel}runs/{r['id']}/'">
<td class="rank">{i+1}</td><td>{au}</td>
<td class="num"><a href="{rel}runs/{r['id']}/">{frames_html(r)}</a></td>
{mcells(r)}
<td class="num"><span class="starglyph">★</span>{nlikes(r)}</td>
<td>{esc((r.get('submitted') or '')[:10])}</td>
<td class="ctr">{tick(rs_)}</td><td class="ctr">{tick(vs_)}</td><td class="ctr">{console_tick(r)}</td></tr>''')
        prows = []
        for r in pend:
            rs_, vs_ = eff_state(r)
            au = ' · '.join(author_chip(a['user'], rel) for a in r['authors'])
            prows.append(f'''<tr onclick="if(!event.target.closest('a'))location='{rel}runs/{r['id']}/'"><td class="rank">·</td><td>{au}</td>
<td class="num"><a href="{rel}runs/{r['id']}/">{frames_html(r)}</a></td>
{mcells(r)}
<td class="num"><span class="starglyph">★</span>{nlikes(r)}</td>
<td>{esc((r.get('submitted') or '')[:10])}</td>
<td class="ctr">{tick(rs_)}</td><td class="ctr">{tick(vs_)}</td><td class="ctr">{console_tick(r)}</td></tr>''')
        hrows = []
        for r in history:
            best = next(t for t in table_runs
                        if frozenset(a['user'].lower() for a in t['authors'])
                        == frozenset(a['user'].lower() for a in r['authors']))
            # how far behind the same authors' best this sits, on the
            # primary metric: frames against frames when both sides have
            # them and time rules; otherwise the metric's own unit
            prim = mdefs[0]
            pv, bv = metric_value(r, prim), metric_value(best, prim)
            if (prim['key'] == 'time' and not r.get('videoOnly')
                    and not best.get('videoOnly')):
                dtxt = f"+{r['movie']['frames'] - best['movie']['frames']:,}f"
            elif pv is None or bv is None:
                dtxt = '—'
            else:
                behind = (pv - bv) if prim['better'] == 'lower' else (bv - pv)
                dtxt = (f'+{behind:.2f}s' if prim['type'] == 'time'
                        else f'{behind:+g}'
                        + (f' {esc(prim["unit"])}' if prim.get('unit') else ''))
            au = ' · '.join(author_chip(a['user'], rel) for a in r['authors'])
            hrows.append(f'''<tr onclick="if(!event.target.closest('a'))location='{rel}runs/{r['id']}/'"><td class="rank">·</td><td>{au}</td>
<td class="num"><a href="{rel}runs/{r['id']}/">{frames_html(r)}</a></td>
<td class="num muted">{dtxt}</td>
<td class="num"><span class="starglyph">★</span>{nlikes(r)}</td>
<td>{esc((r.get('submitted') or '')[:10])}</td>
<td class="ctr">{tick(eff_state(r)[0])}</td><td class="ctr">{tick(eff_state(r)[1])}</td><td class="ctr">{console_tick(r)}</td></tr>''')
        ranked_by = ''
        if mdefs is not CLASSIC_METRICS:
            ranked_by = ('<p class="rules"><b>Ranked by:</b> '
                         + ', then '.join(
                             f'{esc(m["label"])} ({"lower" if m["better"] == "lower" else "higher"} is better)'
                             for m in mdefs) + '</p>')
        if allrs:
            content = f'''<p class="rules"><b>Rules:</b> {esc(rules)}</p>{ranked_by}
{'<table><thead><tr><th>#</th><th>Author</th><th class="num">Frames</th>' + mth + '<th class="num"><span class="starglyph">★</span></th><th>Date</th><th class="ctr">Repro</th><th class="ctr">Verified</th><th class="ctr">Console</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table>' if rows else '<p class="emptynote">No ranked runs yet in this category.</p>'}
{f'<h3 class="pendh">Pending: awaiting reproduction and verification</h3><table><tbody>' + ''.join(prows) + '</tbody></table>' if prows else ''}
{f'<h3 class="histh">History: earlier runs superseded by the same authors</h3><table><tbody>' + ''.join(hrows) + '</tbody></table>' if hrows else ''}'''
        else:
            content = (f'<p class="rules"><b>Rules:</b> {esc(rules)}</p>{ranked_by}'
                       '<p class="emptynote">No runs archived yet in this combination.</p>')
        sections.append(f'<section class="combo" data-combo="{esc(ckey)}"><h2>{esc(label)}</h2>{content}</section>')

    # the Unclassified shelf: outside the category selector, always visible,
    # ordered purely by likes
    uncl_runs = sorted([r for r in g['runs'] if is_unclassified(r)],
                       key=lambda r: (-nlikes(r), r.get('submitted') or ''))
    if uncl_runs:
        urows = []
        for i, r in enumerate(uncl_runs):
            au = ' · '.join(author_chip(a['user'], rel) for a in r['authors'])
            urows.append(f'''<tr class="{'lead' if i == 0 else ''}" onclick="if(!event.target.closest('a'))location='{rel}runs/{r['id']}/'">
<td class="rank">{i+1}</td><td>{au}</td>
<td class="udesc">{esc(r.get('goalDescription') or '')}</td>
<td class="num"><span class="starglyph">★</span> {nlikes(r)}</td>
<td class="num"><a href="{rel}runs/{r['id']}/">{frames_html(r)}</a></td>
<td class="num">{run_clock(r)}</td>
<td>{esc((r.get('submitted') or '')[:10])}</td>
<td class="ctr">{tick(eff_state(r)[0])}</td><td class="ctr">{console_tick(r)}</td></tr>''')
        sections.append(f'''<section class="unclsect"><h2>Unclassified</h2>
<p class="rules">Entertainment, experiments, playarounds. No defined goal; each run describes
its own. Never verified; ranked purely by ★ likes.</p>
<table><thead><tr><th>#</th><th>Author</th><th>Goal</th><th class="num"><span class="starglyph">★</span></th>
<th class="num">Frames</th><th class="num">Time</th><th>Date</th><th class="ctr">Repro</th><th class="ctr">Console</th></tr></thead>
<tbody>{''.join(urows)}</tbody></table></section>''')

    # a game can exist before any run does (an expert filling out a group
    # creates one with an empty goal list), and a dimension with no options
    # has no default to offer
    default_combo = default_combo or '|'.join(d['options'][0]['key']
                                              for d in dims if d['options'])
    sel_js = '''<script>
(function(){
  var sel = {};
  var rows = document.querySelectorAll('.dimrow');
  function apply(){
    var key = Array.prototype.map.call(rows, function(row){
      return sel[row.querySelector('.dimopt').dataset.dim];
    }).join('|');
    document.querySelectorAll('.combo').forEach(function(s){
      s.style.display = (s.dataset.combo === key) ? '' : 'none';
    });
    document.querySelectorAll('.dimopt').forEach(function(b){
      b.classList.toggle('on', sel[b.dataset.dim] === b.dataset.opt);
    });
  }
  var def = 'DEFAULT_COMBO'.split('|');
  rows.forEach(function(row, i){
    sel[row.querySelector('.dimopt').dataset.dim] = def[i];
    row.querySelectorAll('.dimopt').forEach(function(b){
      b.addEventListener('click', function(){ sel[b.dataset.dim] = b.dataset.opt; apply(); });
    });
  });
  apply();
})();
</script>'''.replace('DEFAULT_COMBO', default_combo)

    # An expert never deletes a game through a request path: they ask, in
    # public, and a site-wide expert answers. The runs inside it are other
    # people's work.
    open_removal = next((r for r in g.get('removalRequests', [])
                         if r['status'] == 'open'), None)
    gameact_data = {'game': g['key'], 'experts': covering_experts(g['key']),
                    'openRequest': bool(open_removal) or bool(g.get('removed'))}
    gameacts = (
        '<script type="application/json" id="gameactdata">'
        + json.dumps(gameact_data).replace('<', chr(92) + 'u003c') + '</script>'
        + '<div id="f-gameremove-wrap" class="actzone expertmenu" hidden>'
        '<h2>Expert menu</h2>'
        '<p class="rules">Only experts whose scope covers this game see this box; '
        'every action here is logged in the open.</p>'
        '<details class="actform"><summary>Ask for this game to be removed</summary>'
        '<form id="f-gameremove">'
        '<p class="rules">This files a request; it never removes anything by itself. '
        'A site-wide expert answers it, and both the asking and the answer are public. '
        'The runs inside a removed game keep their pages: the runs were never what '
        'was in question.</p>'
        f'<input type="hidden" name="game" value="{esc(g["key"])}">'
        '<label>Why <input name="reason" required minlength="8" maxlength="500" '
        'placeholder="a duplicate of another game, a nonsense title, …"></label>'
        '<button class="btn quiet">File</button></form></details>'
        '<p class="statline"><a class="btn" href="edit/">Edit this game</a> '
        'Title, thumbnail and categories are edited on their own page.</p>'
        '<details class="actform"><summary>Delete this game</summary>'
        '<form id="f-gamedelete">'
        '<p class="rules">Outright: the game record goes, and every run in it survives, '
        'moved to this system\'s Uncategorized game where it ranks by likes until somebody '
        're-homes it. Your reason is public and permanent.</p>'
        f'<input type="hidden" name="game" value="{esc(g["key"])}">'
        '<label>Why <input name="reason" required minlength="8" maxlength="500" '
        'placeholder="a test, spam, a duplicate record, …"></label>'
        '<button class="btn danger">Delete</button></form></details>'
        '<p id="gameact-msg" class="actmsg" hidden></p></div>')
    removal_note = ''
    if g.get('removed'):
        removal_note = (f'<div class="warnbox"><b>This game was removed from the index</b>'
                        f'<p class="statline">Asked for by '
                        f'{member_chip(g["removed"].get("requestedBy", ""), rel)}, granted by '
                        f'{member_chip(g["removed"]["by"], rel)} on {esc(g["removed"]["date"])}.</p>'
                        f'<p class="actnote">{inline(g["removed"]["reason"], rel)}</p>'
                        f'<p class="statline">Its runs are untouched and still have their '
                        f'pages.</p></div>')
    elif open_removal:
        removal_note = (f'<div class="warnbox"><b>A removal has been asked for</b>'
                        f'<p class="statline">By {member_chip(open_removal["by"], rel)} on '
                        f'{esc(open_removal["date"])}. A site-wide expert decides it.</p>'
                        f'<p class="actnote">{inline(open_removal["reason"], rel)}</p></div>')

    body = f'''<header class="ghead"><div>
<div class="chips"><span class="chip">{esc(systems[g['system']]['name'])}</span>
<span class="chip">{len(g['runs'])} run{'s' if len(g['runs'])!=1 else ''}</span>
<span class="chip starchip"><span class="starglyph">★</span> {sum(nlikes(r) for r in g['runs'])}</span>
</div>
<h1>{esc(g['title'])}</h1>{group_chip(g['key'], rel)}
{expert_line(g['key'], rel)}</div>
<div class="hbtns">{f'<img class="gface" src="/thumbs/{esc(SHIPPED_GAME_THUMBS[g["key"]])}" alt="">' if g['key'] in SHIPPED_GAME_THUMBS else ''}<div class="btnrow"><a class="btn" href="{rel}submit/?game={esc(g['key'])}">Submit a run</a>
<a class="btn quiet" href="{rel}create-category/?game={esc(g['key'])}">Create a category</a>
<a class="btn quiet" href="{FORUM}/tags/c/games/12/{g['system']}-{g['key'].split('/')[1]}">Discuss on the forum</a></div></div></header>
{selector}
{''.join(sections)}
{sel_js if multi else ''}
{removal_note}
<p class="legend">{IMPORTED_TICK} Imported: verified at the trusted site it came from &nbsp;
{FULL_TICK} verified (expert) &nbsp; {PROV_TICK} verified &nbsp; {NONE_TICK} pending</p>
{gameacts}'''
    crumb = f'<a href="{rel}games/">Games</a> / {esc(g["title"])}'
    (gd / 'index.html').write_text(page(g['title'], body, rel, crumb, 'Games'))

    # ---- the game editor: everything a covering expert may change, in one
    # place with a real UI (the page is public markup; every form is revealed
    # only to covering experts and the archivist enforces regardless) ----
    opt_data = []
    for d_ in g['categories']['dimensions']:
        for o in d_['options']:
            opt_data.append({
                'key': o['key'], 'label': o['label'], 'rule': o.get('rule', ''),
                'metrics': o.get('metrics'),
                'runs': sum(1 for r_ in g['runs']
                            if (r_.get('category') or {}).get('goal') == o['key'])})
    edit_data = {'game': g['key'], 'title': g['title'],
                 'experts': covering_experts(g['key']),
                 'options': opt_data}
    erel = rel + '../'
    ebody = f"""<header class="ghead"><div>
<div class="chips"><span class="chip">{esc(systems[g['system']]['name'])}</span></div>
<h1>Edit {esc(g['title'])}</h1>
<p class="authline">Everything a covering expert may change about this game. Every edit
is logged in the open with your name, the old value and the new.</p></div>
<div class="hbtns"><a class="btn quiet" href="../">Back to the game</a></div></header>
<script type="application/json" id="gameeditdata">{json.dumps(edit_data).replace('<', chr(92) + 'u003c')}</script>
<p class="msg" id="ge-gate">Checking who you are…</p>
<div id="geditor" hidden>
<section><h2>Identity</h2>
<form id="f-ge-title" class="actform">
  <input type="hidden" name="kind" value="game">
  <input type="hidden" name="target" value="{esc(g['key'])}">
  <input type="hidden" name="field" value="title">
  <label>Title <input name="value" required maxlength="120" value="{esc(g['title'])}"></label>
  <label>Why <input name="reason" required minlength="8" maxlength="500"
    placeholder="the reason is public"></label>
  <button class="btn">Rename</button>
</form>
<form id="f-ge-thumb" class="actform">
  <input type="hidden" name="kind" value="game">
  <input type="hidden" name="target" value="{esc(g['key'])}">
  <input type="hidden" name="field" value="thumbnail">
  {f'<img class="gface" src="/thumbs/{esc(SHIPPED_GAME_THUMBS[g["key"]])}" alt="current thumbnail">' if g['key'] in SHIPPED_GAME_THUMBS else '<p class="rules">No thumbnail set; the card falls back to the newest run frame.</p>'}
  <label>Thumbnail (png/jpg/webp, under 256 KB)
    <input name="thumbnail" type="file" accept=".png,.jpg,.jpeg,.webp" required></label>
  <label>Why <input name="reason" required minlength="8" maxlength="500"></label>
  <button class="btn">Set</button>
</form></section>
<section><h2>Categories</h2>
<p class="rules">The label is what the rankings say; the rule is what a verifier holds a
run to. A category with runs in it cannot be deleted: it is their home.</p>
<template id="med-skeleton">{METRICS_ED}</template>
<div id="ge-cats"></div>
<form id="f-ge-add" class="actform gecard">
  <h3>Add a category</h3>
  <input type="hidden" name="game" value="{esc(g['key'])}">
  <label>Label <input name="label" required maxlength="80" placeholder="e.g. 100% completion"></label>
  <label>Rule <input name="rule" required maxlength="500"
    placeholder="what a verifier holds a run to"></label>
  <label>Key (optional; derived from the label) <input name="option_key" pattern="[a-z0-9-]*"></label>
  {METRICS_ED}
  <button class="btn">Add</button>
</form></section>
<section><h2>Governance</h2>
{f'<p class="rules">Ratified by {esc(g["ratifiedBy"])} on {esc(g["ratifiedAt"])} (historical; ratification is no longer a mechanism).</p>' if g.get('ratifiedBy') else ''}
<form id="f-ge-remove" class="actform">
  <p class="rules">Files a request; a site-wide expert answers it. Runs are untouched.</p>
  <input type="hidden" name="game" value="{esc(g['key'])}">
  <label>Why <input name="reason" required minlength="8" maxlength="500"></label>
  <button class="btn quiet">File</button>
</form>
<form id="f-ge-delete" class="actform">
  <p class="rules">Outright: the record goes; every run survives, moved to this
  system's Uncategorized game. Your reason is public and permanent.</p>
  <input type="hidden" name="game" value="{esc(g['key'])}">
  <label>Why <input name="reason" required minlength="8" maxlength="500"></label>
  <button class="btn danger">Delete</button>
</form></section>
<p class="msg" id="ge-msg" hidden></p>
</div>"""
    (gd / 'edit').mkdir(exist_ok=True)
    (gd / 'edit' / 'index.html').write_text(page(
        f"Edit {g['title']}", ebody, erel,
        f'<a href="{erel}games/">Games</a> / <a href="../">{esc(g["title"])}</a> / Edit',
        'Games'))

