"""View: member pages (renders on import; see views/__init__)."""
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
    ARCHIVE_TREE,
    OUT,
)
from model import (
    ROLES_NOW,
    ROLE_LABEL,
    author_news,
    author_stats,
    authors,
    canon,
    cat_label,
    committee_now,
    eff_state,
    founder_now,
    nlikes,
    points,
    profile_slug,
    role_events_of,
    runs,
    scope_words,
    withdrawn_runs,
)
from render import (
    moment,
    author_chip,
    badge_chip,
    console_tick,
    esc,
    inline,
    member_chip,
    page,
    primary_metric_html,
    role_badges,
    tick,
)

# the client-side data feeds beside the pages (names for pickers, the news
# dates for the avatar dot, per-member stats for the menu)
(OUT / 'assets' / 'authornames.json').write_text(json.dumps(
    sorted((a['username'] for a in authors.values()), key=str.lower)))
(OUT / 'assets' / 'news.json').write_text(json.dumps(
    {u: [e['date'] for e in lst] for u, lst in author_news.items()}))
(OUT / 'assets' / 'authorstats.json').write_text(json.dumps(author_stats))

# ---- author pages ----
for uname, a in authors.items():
    mine = [r for r in runs if any(canon(x['user']) == uname for x in r['authors'])]
    rows = []
    for r in sorted(mine, key=lambda r: r.get('submitted') or '', reverse=True):
        g = r['_game']
        rs_, vs_ = eff_state(r)
        rows.append(f'''<tr onclick="if(!event.target.closest('a'))location='../../runs/{r['id']}/'"><td><a href="../../games/{g['key']}/">{esc(g['title'])}</a></td>
<td>{esc(cat_label(r))}</td>
<td class="num"><a href="../../runs/{r['id']}/">{primary_metric_html(r)}</a></td>
<td class="num"><span class="starglyph">★</span>{nlikes(r)}</td>
<td>{esc((r.get('submitted') or '')[:10])}</td>
<td class="ctr">{tick(rs_)}</td><td class="ctr">{tick(vs_)}</td><td class="ctr">{console_tick(r)}</td></tr>''')
    contrib = points.get(uname)
    cpts = contrib['points'] if contrib else 0
    if contrib and contrib['acts']:
        acts = ''.join(f'''<tr onclick="if(!event.target.closest('a'))location='../../runs/{r_['id']}/'"><td>{esc(moment(d))}</td><td>{esc(desc)}</td>
<td><a href="../../runs/{r_['id']}/">{esc(r_['_game']['title'])} ({r_['id']})</a></td>
<td class="num">+{pts}</td></tr>''' for d, desc, pts, r_ in sorted(
            contrib['acts'],
            # sort on comparable fields only: two acts sharing a date,
            # description and payout would otherwise compare run dicts
            key=lambda a: (a[0], a[1], a[2], a[3]['id']), reverse=True))
        contrib_body = f'''<table class="sortable"><thead><tr><th>Date</th><th>Act</th><th>Run</th><th class="num">Points earned</th></tr></thead>
<tbody>{acts}</tbody></table>'''
    else:
        contrib_body = ('<p class="emptynote">No contributions yet. Reproduce or verify runs '
                        'on the <a href="../../contribute/">Contribute board</a> to start earning.</p>')
    contrib_html = f'''<section><details class="secfold" open><summary><h2>Contributions · {cpts} points{badge_chip(cpts)}</h2></summary>
{contrib_body}</details></section>'''
    badge = ''
    st_ = author_stats[uname]
    header_btn = (f'<div class="hbtns"><a class="btn" id="selfimport" hidden '
                  f'data-author="{esc(a["username"])}" href="../../import/">'
                  f'Import runs</a></div>')
    selfimport_html = header_btn
    NEWS_ICON = {'reproduced': '↻', 'verified': '✓', 'liked': '★'}

    def news_line(e):
        return (f'<p class="statline newsline" data-date="{esc(e["date"])}">'
                f'<span class="newsico {e["kind"]}">{NEWS_ICON[e["kind"]]}</span> '
                f'{member_chip(e["actor"], "../../")} {e["kind"]} '
                f'<a href="../../runs/{e["run"]}/">{esc(e["title"])} ({e["run"]})</a>'
                f'<span class="actmeta"> {esc(moment(e.get("at") or e["date"]))}</span></p>')

    my_news = author_news.get(uname, [])[:50]
    news_items = ''.join(news_line(e) for e in my_news[:10])
    if len(my_news) > 10:
        news_items += (
            '<div class="newsrest" hidden>'
            + ''.join(news_line(e) for e in my_news[10:]) + '</div>'
            '<button type="button" class="newsmore">load more news…</button>')
    news_html = (f'<section id="news"><h2>News</h2><div class="factbox newsbox">{news_items}</div></section>'
                 if news_items else
                 '<section id="news"><h2>News</h2><p class="emptynote">Nothing yet. '
                 'reproductions, verifications and stars on your runs will appear here.</p></section>')
    # the roles this member has held, and lost, in order: the log belongs to the
    # person it is about, at the bottom of their own page
    mine_roles = role_events_of(a['username'])
    role_rows = []
    for ev in reversed(mine_roles):
        what = ROLE_LABEL.get(ev['role'], ev['role'])
        if ev['role'] == 'expert' and ev.get('scope'):
            what += f' · {esc(scope_words(ev["scope"]))}'
        act = ('<span class="chip verchip">Granted</span>' if ev['action'] == 'granted'
               else '<span class="chip pendchip">Removed</span>')
        by = ev['by']
        by_cell = ('the founder' if by == 'founder' else
                   'a Committee vote' if by == 'committee' else
                   member_chip(authors.get(by.lower(), {}).get('username', by), '../../'))
        proof = (f' · <a href="{esc(ev["proof"])}">where it was decided</a>'
                 if ev.get('proof') else '')
        role_rows.append(f'<tr><td>{esc(moment(ev.get("at") or ev["date"]))}</td><td>{act}</td>'
                         f'<td>{what}</td><td>{by_cell}</td>'
                         f'<td>{inline(ev["reason"], "../../")}{proof}</td></tr>')
    roles_html = (f'''<section><details class="secfold menufold"><summary><h2>Roles
({len(mine_roles)})</h2></summary>
<table><thead><tr><th>Date</th><th></th><th>Role</th><th>By</th><th>Reason</th></tr></thead>
<tbody>{''.join(role_rows)}</tbody></table>
<p class="legend">Every grant and every removal, with the reason given at the time. Roles
are not a status somebody has always had: this is where that is visible.</p>
</details></section>''' if role_rows else '')

    target_roles_now = sorted({role for (u, role, sc) in ROLES_NOW
                               if u == uname})
    memberact = (
        '<script type="application/json" id="memberactdata">'
        + json.dumps({'target': a['username'],
                      'committee': committee_now,
                      'founders': founder_now,
                      'targetSeated': 'committee' in target_roles_now or
                                      'founder' in target_roles_now}
                     ).replace('<', chr(92) + 'u003c') + '</script>'
        + '<div id="memberacts" class="actzone" hidden>'
        '<details class="actform"><summary>Delete this member (Steering Committee)</summary>'
        '<form id="f-memberdelete">'
        '<p class="rules">For spam and test accounts. Refused while they hold any role or '
        'authored any run: those have their own procedures. A sitting Committee member is '
        'the Founder\'s alone to delete, and the Founder is nobody\'s. Your reason is '
        'public and permanent.</p>'
        f'<input type="hidden" name="target" value="{esc(a["username"])}">'
        '<label>Why <input name="reason" required minlength="8" maxlength="500" '
        'placeholder="a spam account, a test, \u2026"></label>'
        '<button class="btn danger">Delete</button></form></details>'
        '<p id="memberact-msg" class="actmsg" hidden></p></div>')

    body = f'''<header class="ghead"><div>
<h1>{esc(a['username'])} {('🇨🇭' if a.get('country')=='CH' else '')}</h1>{role_badges(a['username'])}{badge}</div>{selfimport_html}</header>
{memberact}
<div class="statstrip">
<div class="stat"><b>{st_['runs']}</b><span>runs published</span></div>
<div class="stat"><b><span class="starglyph">★</span>{st_['author']}</b><span>author score</span></div>
<div class="stat"><b>{st_['contrib']}</b><span>contributor score</span></div>
</div>
{news_html}
<section><details class="secfold" open><summary><h2>Runs ({len(mine)})</h2></summary>
<table class="sortable"><thead><tr><th>Game</th><th>Category</th><th class="num"></th><th class="num"><span class="starglyph">★</span></th><th>Date</th><th class="ctr">Repro</th><th class="ctr">Verified</th><th class="ctr">Console</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></details></section>{contrib_html}{roles_html}'''
    (OUT / 'authors' / profile_slug(uname)).mkdir(parents=True, exist_ok=True)
    (OUT / 'authors' / profile_slug(uname) / 'index.html').write_text(
        page(a['username'], body, '../../', f'<a href="../">Members</a> / {esc(a["username"])}',
             'Members'))

# withdrawn runs still get a page: an honest tombstone, not a 404
for r in withdrawn_runs:
    w = r['withdrawn']
    g = r['_game']
    body = f'''<header class="ghead"><div>
<h1>{esc(g['title'])}</h1>
<p class="authline">{' · '.join(author_chip(a['user'], '../../') for a in r['authors'])}</p></div></header>
<div class="warnbox"><b>This run was withdrawn</b>
<p class="statline">Withdrawn by {member_chip(w['by'], '../../')} on {esc(w['date'])}
({esc(w.get('role') or 'author')}).</p>
<p class="actnote">{inline(w['reason'], '../../')}</p>
{'''<p class="rules">The movie file, the notes and the thumbnail were taken down with
this withdrawal, because publishing them was the problem. The record above stays,
so the id is never reused and the history stays legible, and any of the authors
can bring the work back by importing it with the others' agreement.</p>'''
 if w.get('contentRemoved') else
 f'''<p class="rules">Nothing is erased here: the movie file and this record stay in
<a href="{ARCHIVE_TREE}/games/{g['key']}/runs/{r['id']}">the archive</a>. The run is
simply no longer listed or ranked.</p>'''}</div>
<p class="statline"><a class="btn quiet" href="../../games/{g['key']}/">Back to {esc(g['title'])}</a></p>'''
    d = OUT / 'runs' / r['id']
    d.mkdir(parents=True, exist_ok=True)
    (d / 'index.html').write_text(page(f"{g['title']} (withdrawn)", body, '../../',
                                       f'<a href="../../browse/">Runs</a> / {esc(r["id"])}'))

