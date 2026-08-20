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
    covering_experts,
    eff_state,
    games,
    is_ranked,
    is_unclassified,
    nlikes,
    run_seconds,
    systems,
)
from render import (
    FULL_TICK,
    IMPORTED_TICK,
    NONE_TICK,
    PROV_TICK,
    SHIPPED_GAME_THUMBS,
    author_chip,
    console_tick,
    esc,
    expert_line,
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
        ranked_all = sorted([r for r in allrs if is_ranked(r)],
                            key=lambda r: run_seconds(r) or 0)
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
<td class="num">{run_clock(r)}</td>
<td class="num"><span class="starglyph">★</span>{nlikes(r)}</td>
<td>{esc((r.get('submitted') or '')[:10])}</td>
<td class="ctr">{tick(rs_)}</td><td class="ctr">{tick(vs_)}</td><td class="ctr">{console_tick(r)}</td></tr>''')
        prows = []
        for r in pend:
            rs_, vs_ = eff_state(r)
            au = ' · '.join(author_chip(a['user'], rel) for a in r['authors'])
            prows.append(f'''<tr onclick="if(!event.target.closest('a'))location='{rel}runs/{r['id']}/'"><td class="rank">·</td><td>{au}</td>
<td class="num"><a href="{rel}runs/{r['id']}/">{frames_html(r)}</a></td>
<td class="num">{run_clock(r)}</td>
<td class="num"><span class="starglyph">★</span>{nlikes(r)}</td>
<td>{esc((r.get('submitted') or '')[:10])}</td>
<td class="ctr">{tick(rs_)}</td><td class="ctr">{tick(vs_)}</td><td class="ctr">{console_tick(r)}</td></tr>''')
        hrows = []
        for r in history:
            best = next(t for t in table_runs
                        if frozenset(a['user'].lower() for a in t['authors'])
                        == frozenset(a['user'].lower() for a in r['authors']))
            # frames against frames when both sides have them; otherwise the
            # honest unit is seconds
            if r.get('videoOnly') or best.get('videoOnly'):
                delta = None
                delta_s = (run_seconds(r) or 0) - (run_seconds(best) or 0)
            else:
                delta = r['movie']['frames'] - best['movie']['frames']
            au = ' · '.join(author_chip(a['user'], rel) for a in r['authors'])
            hrows.append(f'''<tr onclick="if(!event.target.closest('a'))location='{rel}runs/{r['id']}/'"><td class="rank">·</td><td>{au}</td>
<td class="num"><a href="{rel}runs/{r['id']}/">{frames_html(r)}</a></td>
<td class="num muted">{f'+{delta:,}f' if delta is not None else f'+{delta_s:.2f}s'}</td>
<td class="num"><span class="starglyph">★</span>{nlikes(r)}</td>
<td>{esc((r.get('submitted') or '')[:10])}</td>
<td class="ctr">{tick(eff_state(r)[0])}</td><td class="ctr">{tick(eff_state(r)[1])}</td><td class="ctr">{console_tick(r)}</td></tr>''')
        if allrs:
            content = f'''<p class="rules"><b>Rules:</b> {esc(rules)}</p>
{'<table><thead><tr><th>#</th><th>Author</th><th class="num">Frames</th><th class="num">Time</th><th class="num"><span class="starglyph">★</span></th><th>Date</th><th class="ctr">Repro</th><th class="ctr">Verified</th><th class="ctr">Console</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table>' if rows else '<p class="emptynote">No ranked runs yet in this category.</p>'}
{f'<h3 class="pendh">Pending: awaiting reproduction and verification</h3><table><tbody>' + ''.join(prows) + '</tbody></table>' if prows else ''}
{f'<h3 class="histh">History: earlier runs superseded by the same authors</h3><table><tbody>' + ''.join(hrows) + '</tbody></table>' if hrows else ''}'''
        else:
            content = (f'<p class="rules"><b>Rules:</b> {esc(rules)}</p>'
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

    # ratifying is an expert act on the game itself, so it belongs on this page

    # An expert never deletes a game: they ask, in public, and a site-wide
    # expert answers. The movies inside it are other people's work.
    open_removal = next((r for r in g.get('removalRequests', [])
                         if r['status'] == 'open'), None)
    ratify_box = ''
    if not g.get('established', True):
        ratify_data = {'game': g['key'], 'experts': covering_experts(g['key'])}
        ratify_box = (
            '<script type="application/json" id="ratifydata">'
            + json.dumps(ratify_data).replace('<', chr(92) + 'u003c') + '</script>'
            + '<div id="f-ratify-wrap" hidden>'
            '<form id="f-ratify" class="actform">'
            '<h3>Ratify this game</h3>'
            '<p class="rules">Anyone may create a game at submission time; it stays '
            'provisional until an expert confirms it is a real, distinct game with a '
            'sensible title, unless the person who created it already covered it. '
            'Ratifying does not judge any run in it.</p>'
            f'<input type="hidden" name="game" value="{esc(g["key"])}">'
            '<button class="btn">Ratify</button></form>'
            '<p id="ratify-msg" class="actmsg" hidden></p></div>')

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
        'The runs inside a removed game keep their pages: the movies were never what '
        'was in question.</p>'
        f'<input type="hidden" name="game" value="{esc(g["key"])}">'
        '<label>Why <input name="reason" required minlength="8" maxlength="500" '
        'placeholder="a duplicate of another game, a nonsense title, …"></label>'
        '<button class="btn quiet">File</button></form></details>'
        '<details class="actform"><summary>Rename this game</summary>'
        '<form id="f-gamerename">'
        '<p class="rules">The title is the record\'s, not any member\'s; renaming is '
        'logged with your name, the old title and the new.</p>'
        '<input type="hidden" name="kind" value="game">'
        f'<input type="hidden" name="target" value="{esc(g["key"])}">'
        '<input type="hidden" name="field" value="title">'
        f'<label>New title <input name="value" required maxlength="120" '
        f'value="{esc(g["title"])}"></label>'
        '<label>Why <input name="reason" required minlength="8" maxlength="500"></label>'
        '<button class="btn">Rename</button></form></details>'
        '<details class="actform"><summary>Set the game thumbnail</summary>'
        '<form id="f-gamethumb">'
        '<p class="rules">Shown on this page and on the game\'s card. An image you '
        'answer for, logged like every edit.</p>'
        '<input type="hidden" name="kind" value="game">'
        f'<input type="hidden" name="target" value="{esc(g["key"])}">'
        '<input type="hidden" name="field" value="thumbnail">'
        '<label>Image (png/jpg/webp, under 256 KB) '
        '<input name="thumbnail" type="file" accept=".png,.jpg,.jpeg,.webp" required></label>'
        '<label>Why <input name="reason" required minlength="8" maxlength="500"></label>'
        '<button class="btn">Set</button></form></details>'
        '<details class="actform"><summary>Edit a category</summary>'
        '<form id="f-gamecat">'
        '<p class="rules">The label is what the rankings say; the rule is what a '
        'verifier holds a run to. Logged with the old wording and the new.</p>'
        '<input type="hidden" name="kind" value="category">'
        '<label>Category <select name="target">'
        + ''.join(f'<option value="{esc(g["key"])}:{esc(o["key"])}">{esc(o["label"])}'
                  f'{" (provisional)" if o.get("provisional") else ""}</option>'
                  for d_ in g['categories']['dimensions'] for o in d_['options'])
        + '</select></label>'
        '<label>Field <select name="field"><option value="label">Label</option>'
        '<option value="rule">Rule</option></select></label>'
        '<label>New wording <input name="value" required maxlength="500"></label>'
        '<label>Why <input name="reason" required minlength="8" maxlength="500"></label>'
        '<button class="btn">Change</button></form></details>'
        '<details class="actform"><summary>Delete this game</summary>'
        '<form id="f-gamedelete">'
        '<p class="rules">Outright: the game record goes, and every movie in it survives, '
        'moved to this system\'s Uncategorized game where it ranks by likes until somebody '
        're-homes it. Your reason is public and permanent.</p>'
        f'<input type="hidden" name="game" value="{esc(g["key"])}">'
        '<label>Why <input name="reason" required minlength="8" maxlength="500" '
        'placeholder="a test, spam, a duplicate record, …"></label>'
        '<button class="btn danger">Delete</button></form></details>'
        + ratify_box +
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
{'' if g.get('established', True) else '<span class="chip pendchip">Provisional game</span>'}</div>
<h1>{esc(g['title'])}</h1>{group_chip(g['key'], rel)}
{expert_line(g['key'], rel)}</div>
<div class="hbtns">{f'<img class="gface" src="/thumbs/{esc(SHIPPED_GAME_THUMBS[g["key"]])}" alt="">' if g['key'] in SHIPPED_GAME_THUMBS else ''}<a class="btn" href="{rel}submit/?game={esc(g['key'])}">Submit a run</a>
<a class="btn quiet" href="{FORUM}/tags/c/games/12/{g['system']}-{g['key'].split('/')[1]}">Discuss on the forum</a></div></header>
{selector}
{''.join(sections)}
{sel_js if multi else ''}
{removal_note}
<p class="legend">{IMPORTED_TICK} Imported: verified at the trusted site it came from &nbsp;
{FULL_TICK} verified (expert) &nbsp; {PROV_TICK} verified &nbsp; {NONE_TICK} pending</p>
{gameacts}'''
    crumb = f'<a href="{rel}games/">Games</a> / {esc(g["title"])}'
    (gd / 'index.html').write_text(page(g['title'], body, rel, crumb, 'Games'))

