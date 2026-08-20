"""View: games index (renders on import; see views/__init__)."""
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
    authors,
    cat_label,
    covering_experts,
    eff_state,
    experts_reg,
    games,
    group_games,
    group_runs,
    groups_by_game,
    has_page,
    is_ranked,
    live_groups,
    nlikes,
    systems,
)
from render import (
    FULL_TICK,
    NONE_TICK,
    PROV_TICK,
    SHIPPED_GAME_THUMBS,
    author_chip,
    console_tick,
    dl_games,
    esc,
    inline,
    member_chip,
    page,
    primary_metric_html,
    thumb_html,
    thumb_url,
    tick,
)

# ---- games index ----
by_sys = {}
for key, g in games.items():
    by_sys.setdefault(g['system'], []).append(g)
def game_card(g, prefix='', with_system=False):
    newest = max((r for r in g['runs'] if r.get('thumbnail')),
                 key=lambda r: r.get('submitted') or '', default=None)
    own = SHIPPED_GAME_THUMBS.get(g['key'])
    tm = (f'<span class="thumb"><span class="sys">{esc(g["system"].upper())}</span>'
          f'<img src="/thumbs/{esc(own)}" alt="" loading="lazy"></span>' if own else
          thumb_html(newest) if newest else
          f'<span class="thumb"><span class="sys">{esc(g["system"].upper())}</span></span>')
    gstars = sum(nlikes(r) for r in g['runs'])
    sysline = (f'<span class="csys">{esc(systems[g["system"]]["name"])}</span>'
               if with_system else '')
    return f'''<a class="card" data-stars="{gstars}" data-title="{esc(g['title'])}" href="{prefix}{g['key']}/">
{tm}
<span class="cbody"><b>{esc(g['title'])}</b>{sysline}
<span class="cfoot"><span>{len(g['runs'])} run{'s' if len(g['runs'])!=1 else ''}</span>
<span><span class="starglyph">★</span>{gstars}</span></span></span></a>'''

def card_section(title, gms):
    """One band of game cards, headed with what it holds."""
    stars = sum(nlikes(r) for g in gms for r in g['runs'])
    cards = ''.join(game_card(g) for g in sorted(gms, key=lambda g: g['title']))
    return f'''<section class="syssect" data-stars="{stars}" data-title="{esc(title)}">
<h2>{esc(title)}
<span class="chip">{len(gms)} game{'s' if len(gms) != 1 else ''}</span>
<span class="chip starchip"><span class="starglyph">★</span> {stars}</span></h2>
<div class="grid">{cards}</div></section>'''

sys_sections = [card_section(systems[skey]['name'], by_sys[skey])
                for skey in sorted(by_sys, key=lambda k: systems[k]['name'])]

if live_groups:
    (OUT / 'groups').mkdir(parents=True, exist_ok=True)
    for gr in live_groups:
        ggames = group_games(gr)
        grunts = group_runs(gr)
        gstars = sum(nlikes(r) for r in grunts)
        gexperts = sorted({u for g in ggames for u in covering_experts(g['key'])})
        cards = ''.join(game_card(g, '../../games/', with_system=True) for g in ggames)
        rows = []
        for r in sorted((r for r in grunts if is_ranked(r)),
                        key=lambda r: (r['_game']['title'], cat_label(r))):
            rs_, vs_ = eff_state(r)
            rows.append(f'''<tr onclick="if(!event.target.closest('a'))location='../../runs/{r['id']}/'"><td><a href="../../games/{r['_game']['key']}/">{esc(r['_game']['title'])}</a></td>
<td>{esc(cat_label(r))}</td>
<td>{', '.join(author_chip(a['user'], '../../') for a in r['authors'])}</td>
<td class="num"><a href="../../runs/{r['id']}/">{primary_metric_html(r)}</a></td>
<td class="num"><span class="starglyph">★</span>{nlikes(r)}</td>
<td class="ctr">{tick(rs_)}</td><td class="ctr">{tick(vs_)}</td><td class="ctr">{console_tick(r)}</td></tr>''')
        table = ('' if gr.get('synthetic') else f'''<h2>Records across the group</h2>
<table class="rtab"><thead><tr><th>Game</th><th>Category</th><th>Author</th>
<th class="num"></th><th class="num">Stars</th>
<th class="ctr">Rep</th><th class="ctr">Ver</th><th class="ctr">Con</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<p class="legend">{FULL_TICK} verified (expert) &nbsp; {PROV_TICK} verified &nbsp;
{NONE_TICK} pending</p>''' if rows else
                 '<p class="authline">No ranked run in this group yet.</p>')
        synthetic = gr.get('synthetic')
        expline = ('' if synthetic else
                   '<p class="authline">Group experts and above: '
                   + ', '.join(member_chip(authors.get(u, {}).get('username', u),
                                           '../../') for u in gexperts) + '</p>'
                   if gexperts else '')
        blurb = ('These games are not part of any group yet. Every game belongs '
                 'to one, so they wait here until an expert places them in the '
                 'group they belong to.' if synthetic else
                 'A game group: one family of games, across every system it '
                 'appeared on. Experts may hold a scope over a whole group.')
        gopen = next((r for r in gr.get('removalRequests', [])
                      if r['status'] == 'open'), None)
        gact_data = {'group': gr['key'], 'experts': gexperts,
                     'editorZone': True,
                     'system_options': [{'key': s, 'name': systems[s]['name']}
                                        for s in sorted(systems)],
                     'openRequest': bool(gopen)}
        gacts = ('' if gr.get('synthetic') else
                 '<script type="application/json" id="groupactdata">'
                 + json.dumps(gact_data).replace('<', chr(92) + 'u003c') + '</script>'
                 + '<div id="groupacts" class="actzone expertmenu" hidden>'
                 '<h2>Expert menu</h2>'
                 '<p class="rules">Only experts whose scope covers this group, and editors, '
                 'see this box; every action here is logged in the open.</p>'
                 '<details class="actform"><summary>Add a game to this group</summary>'
                 '<form id="f-groupaddgame">'
                 '<p class="rules">Creates the game inside this group; it exists the '
                 'moment you make it. It has no '
                 'runs until somebody archives one.</p>'
                 f'<input type="hidden" name="group" value="{esc(gr["key"])}">'
                 '<label>System <select name="system" id="ga-system"></select></label>'
                 '<label>Title <input name="title" required maxlength="120" '
                 'placeholder="e.g. Mega Man 3"></label>'
                 '<button class="btn">Add</button></form></details>'
                 '<details class="actform"><summary>Ask for this group to be removed</summary>'
                 '<form id="f-groupremove">'
                 '<p class="rules">Files a request; it never removes anything by itself. A '
                 'site-wide expert answers it. If it is granted the group is dissolved and '
                 'its games are ungrouped; no game and no run is deleted.</p>'
                 f'<input type="hidden" name="group" value="{esc(gr["key"])}">'
                 '<label>Why <input name="reason" required minlength="8" maxlength="500" '
                 'placeholder="these are not one family, …"></label>'
                 '<button class="btn quiet">File</button></form></details>'
                 '<details class="actform"><summary>Delete this group</summary>'
                 '<form id="f-groupdelete">'
                 '<p class="rules">Outright: the grouping goes, and every game in it becomes '
                 'ungrouped, gathered by Unclassified until somebody re-homes it. No game and '
                 'no run is deleted. Your reason is public and permanent.</p>'
                 f'<input type="hidden" name="group" value="{esc(gr["key"])}">'
                 '<label>Why <input name="reason" required minlength="8" maxlength="500" '
                 'placeholder="a test, a mistake, …"></label>'
                 '<button class="btn danger">Delete</button></form></details>'
                 '<p id="groupact-msg" class="actmsg" hidden></p></div>')
        gremoval = ''
        if gopen:
            gremoval = (f'<div class="warnbox"><b>A removal has been asked for</b>'
                        f'<p class="statline">By {member_chip(gopen["by"], "../../")} on '
                        f'{esc(gopen["date"])}. A site-wide expert decides it.</p>'
                        f'<p class="actnote">{inline(gopen["reason"], "../../")}</p></div>')

        gbody = f'''<header class="ghead"><div>
<div class="chips"><span class="chip">{len(ggames)} games</span>
<span class="chip">{len(grunts)} run{'s' if len(grunts)!=1 else ''}</span>
<span class="chip starchip"><span class="starglyph">★</span> {gstars}</span></div>
<h1>{esc(gr['title'])}</h1>

<p class="authline">{blurb}</p>{expline}</div></header>
{'<div class="grid">' + cards + '</div>' if ggames else
 '<p class="emptynote">No games in this group yet. Experts covering it add them '
 'right here, and anybody can put one in it at submission time.</p>'}
{gremoval}
{table}
{gacts}'''
        gdir = OUT / 'groups' / gr['key']
        gdir.mkdir(parents=True, exist_ok=True)
        (gdir / 'index.html').write_text(page(
            gr['title'], gbody, '../../',
            f'<a href="../../games/">Games</a> / {esc(gr["title"])}', 'Games'))

    def group_collage(gr, ggames):
        """Up to four games of the group, one tile each, each showing that
        game's most starred run. A group is a family of games, so the card
        shows the family rather than one member of it; the tiles are drawn
        from distinct games, best liked first."""
        tiles = []
        for g in sorted(ggames, key=lambda g: (-sum(nlikes(r) for r in g['runs']),
                                               g['title'])):
            best = max((r for r in g['runs'] if r.get('thumbnail')),
                       key=lambda r: (nlikes(r), r.get('submitted') or ''), default=None)
            if best:
                tiles.append(best)
            if len(tiles) == 4:
                break
        if not tiles:
            # no thumbnails, or no games at all: the card still needs a face
            word = ggames[0]['system'].upper() if ggames else 'NEW'
            return f'<span class="thumb"><span class="sys">{esc(word)}</span></span>'
        nsfw = any('sexual' in r.get('contentWarnings', []) for r in tiles)
        cells = ''.join(
            f'<span class="tile"><img class="'
            f'{"nsfwblur" if "sexual" in r.get("contentWarnings", []) else ""}" '
            f'src="{esc(thumb_url(r))}" alt="" loading="lazy"></span>'
            for r in tiles)
        badge = '<span class="nsfw18">18+</span>' if nsfw else ''
        return (f'<span class="thumb collage" data-n="{len(tiles)}">{cells}{badge}</span>')

    def group_card(gr):
        """One card for a whole group, thumbnailed with a collage of its
        games."""
        ggames = group_games(gr)
        grunts = group_runs(gr)
        stars = sum(nlikes(r) for r in grunts)
        tm = group_collage(gr, ggames)
        nsys = len({g['system'] for g in ggames})
        return f'''<a class="card" data-stars="{stars}" data-title="{esc(gr['title'])}"
data-last="{1 if gr.get('synthetic') else 0}" href="../groups/{gr['key']}/">
{tm}
<span class="cbody"><b>{esc(gr['title'])}</b>
<span class="csys">{len(ggames)} games · {nsys} system{'s' if nsys != 1 else ''}</span>
<span class="cfoot"><span>{len(grunts)} run{'s' if len(grunts) != 1 else ''}</span>
<span><span class="starglyph">★</span>{stars}</span></span></span></a>'''

    group = [gr for gr in live_groups if not gr.get('synthetic')]
    grp_view = f'''<div class="grid">{''.join(group_card(gr) for gr in live_groups)}</div>
<p class="authline">{len(group)} groups, and every game belongs to one:
those no group has claimed yet are gathered under Unclassified.</p>'''
else:
    grp_view = ''          # nothing is grouped yet, so there is no group view

games_sort_js = '''<script>
(function(){
  var byStars = true, view = 'systems';
  // a remembered view whose section is gone (an archive with no groups yet)
  // must not blank the page
  try {
    var v = localStorage.getItem('tar-games-view');
    if (v && document.getElementById('v-' + v)) view = v;
  } catch(e){}
  function order(a, b){
    // Unclassified is a holding pen, not a group: it stays last either way
    if ((a.dataset.last || 0) !== (b.dataset.last || 0))
      return (a.dataset.last || 0) - (b.dataset.last || 0);
    return byStars ? (b.dataset.stars - a.dataset.stars)
                   : a.dataset.title.localeCompare(b.dataset.title);
  }
  function resort(){
    document.querySelectorAll('.gsects').forEach(function(wrap){   // bands
      Array.prototype.slice.call(wrap.children).sort(order)
        .forEach(function(s){ wrap.appendChild(s); });
    });
    // every grid: the cards inside a band, and the group cards, which stand
    // in a grid of their own with no band around them
    document.querySelectorAll('.grid').forEach(function(grid){
      Array.prototype.slice.call(grid.children).sort(order)
        .forEach(function(c){ grid.appendChild(c); });
    });
    document.querySelectorAll('.gsort').forEach(function(b){
      b.classList.toggle('on', (b.dataset.mode === 'stars') === byStars);
    });
  }
  function apply(){
    ['groups', 'systems', 'list'].forEach(function(k){
      var el = document.getElementById('v-' + k);
      if (el) el.hidden = (k !== view);
    });
    document.querySelectorAll('.gview-btn').forEach(function(b){
      b.classList.toggle('on', b.dataset.view === view);
    });
    // the list is alphabetical by definition, so a sort control there is a lie
    var row = document.getElementById('gsortrow');
    if (row) row.hidden = (view === 'list');
    resort();
  }
  document.querySelectorAll('.gsort').forEach(function(b){
    b.addEventListener('click', function(){ byStars = b.dataset.mode === 'stars'; resort(); });
  });
  document.querySelectorAll('.gview-btn').forEach(function(b){
    b.addEventListener('click', function(){
      view = b.dataset.view;
      try { localStorage.setItem('tar-games-view', view); } catch(e){}
      apply();
    });
  });
  apply();
})();
</script>'''

list_rows = []
for g in sorted(games.values(), key=lambda g: g['title'].lower()):
    mine = [gr for gr in groups_by_game.get(g['key'], []) if has_page(gr)]
    grp = ', '.join(f'<a href="../groups/{gr["key"]}/">{esc(gr["title"])}</a>'
                    for gr in mine) or '<span class="faintcell">—</span>'
    list_rows.append(f'''<tr onclick="if(!event.target.closest('a'))location='{g['key']}/'">
<td><a href="{g['key']}/">{esc(g['title'])}</a></td>
<td>{esc(systems[g['system']]['name'])}</td><td>{grp}</td>
<td class="num">{len(g['runs'])}</td>
<td class="num"><span class="starglyph">★</span>{sum(nlikes(r) for r in g['runs'])}</td></tr>''')
list_view = f'''<table class="rtab"><thead><tr><th>Game</th><th>System</th><th>Group</th>
<th class="num">Runs</th><th class="num">Stars</th></tr></thead>
<tbody>{''.join(list_rows)}</tbody></table>'''

site_experts_now = sorted({e['user'].lower() for e in experts_reg if e['scope'] == 'site'})
games_acts = ('<script type="application/json" id="gamesactdata">'
              + json.dumps({'siteExperts': site_experts_now, 'editorZone': True}) + '</script>'
              + '<div id="gamesacts" class="actzone" hidden>'
              '<details class="actform"><summary>Start a group (site experts and editors)</summary>'
              '<form id="f-newgroup">'
              '<p class="rules">One family of games, across every system it appeared on. '
              'It exists the moment you make it.</p>'
              '<label>Key <input name="group" required pattern="[a-z0-9]+(-[a-z0-9]+)*" '
              'placeholder="lowercase-with-hyphens"></label>'
              '<label>Title <input name="title" required maxlength="80" '
              'placeholder="Mega Man"></label>'
              '<label>Games <input name="games" data-pick="dl-games"></label>'
              '<button class="btn">Create</button></form></details>'
              '<p id="gamesact-msg" class="actmsg" hidden></p></div>' + dl_games())

body = f'''<header class="ghead"><div><h1>Games</h1>
<p class="authline">{len(games)} games across {len(by_sys)} systems. Anyone can create a game;
experts curate afterwards.</p></div>
<div class="hbtns"><div class="dimrow"><span class="dimname">View</span>
{'<button class="dimopt gview-btn" data-view="groups">Groups</button>' if grp_view else ''}
<button class="dimopt gview-btn on" data-view="systems">Systems</button>
<button class="dimopt gview-btn" data-view="list">List</button></div>
<div class="dimrow" id="gsortrow"><span class="dimname">Sort</span>
<button class="dimopt gsort on" data-mode="stars"><span class="starglyph">★</span> Stars</button>
<button class="dimopt gsort" data-mode="title">By title</button></div>
<a class="btn" href="../create-game/">Create a game</a></div></header>
{f'<div class="gview" id="v-groups" hidden>{grp_view}</div>' if grp_view else ''}
<div class="gview gsects" id="v-systems">{''.join(sys_sections)}</div>
<div class="gview" id="v-list" hidden>{list_view}</div>
{games_sort_js}
{games_acts}'''
(OUT / 'games' / 'index.html').write_text(page('Games', body, '../', '', 'Games'))

# The expert roster is not a page of its own: a role is a property of a
# member, so it shows as a badge on the members list and as a history at the
# bottom of that member's own page. scope_label survives because the role log
# uses it.
