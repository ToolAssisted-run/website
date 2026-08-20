"""View: panels (renders on import; see views/__init__)."""
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
    ROLES_NOW,
    authors,
    committee_now,
    experts_reg,
    games,
    groups,
    live_groups,
    scope_words,
    systems,
)
from render import (
    dl_members,
    esc,
    member_chip,
    page,
)

# ---- expert panel ----
# Everything an expert can do lived behind curl. The powers are real and the
# Principles already grant them, so they belong in the site, in one place, and
# only in front of the people who hold them. The roster is public either way:
# what the panel adds is knowing which of it is yours.
# href is a page that exists or nothing at all: the whole site and a single
# system have no page of their own, so they are named and not linked
panel_scopes = ([{'key': 'site', 'label': 'the whole site', 'href': ''}]
                + [{'key': s, 'label': systems[s]['name'], 'href': ''}
                   for s in sorted(systems)]
                + [{'key': 'group:' + gr['key'], 'label': gr['title'],
                    'href': (f'groups/{gr["key"]}/'
                             if any(l['key'] == gr['key'] for l in live_groups) else '')}
                   for gr in groups]
                + [{'key': k, 'label': g['title'], 'href': f'games/{g["slugpath"]}/'}
                   for k, g in sorted(games.items())])
panel_data = {
    'roster': sorted(({'user': ev['user'], 'scope': ev.get('scope', ''),
                       'label': scope_words(ev.get('scope', '')),
                       'by': ev['by'], 'date': ev['date'],
                       'href': next((s['href'] for s in panel_scopes
                                     if s['key'] == ev.get('scope', '')), '')}
                      for (u, role, scope), ev in ROLES_NOW.items() if role == 'expert'),
                     key=lambda e: (e['user'].lower(), e['scope'])),
    'scopes': panel_scopes,
    'committee': committee_now,
    # refused and removed games are offered nowhere: nothing can be done to
    # one that the archivist would not refuse
    'games': [{'key': k, 'title': g['title'], 'system': g['system'],
               'group': next((gr['key'] for gr in groups if k in gr.get('games', [])), ''),
               'rejected': bool(g.get('rejected'))}
              for k, g in sorted(games.items())
              if not g.get('rejected') and not g.get('removed')],
    'groups': [{'key': gr['key'], 'title': gr['title'], 'games': gr.get('games', []),
                } for gr in groups],
    # every member, so the appointment picker can offer people rather than ask
    # for a name to be typed exactly right
    'members': sorted((a['username'] for a in authors.values()), key=str.lower),
    # open removal requests. Only a site-wide expert answers one, so the panel
    # shows them to nobody else, but the request itself is public on the page
    # it is about and in the site log.
    'removals': ([{'kind': 'game', 'key': k, 'title': g['title'],
                   'by': r['by'], 'date': r['date'], 'reason': r['reason']}
                  for k, g in sorted(games.items())
                  for r in g.get('removalRequests', []) if r['status'] == 'open']
                 + [{'kind': 'group', 'key': gr['key'], 'title': gr['title'],
                     'by': r['by'], 'date': r['date'], 'reason': r['reason']}
                    for gr in groups
                    for r in gr.get('removalRequests', []) if r['status'] == 'open']),
}
body = f'''<header class="ghead"><div><h1>Expert panel</h1>
<p class="authline">The scopes you hold, and the things holding them lets you do. Every
one of these is public the moment you do it: it lands in the archive with your name, your
reason and the date, and shows in the role log on the member\'s page.</p></div></header>
<p class="msg" id="panel-gate">Checking who you are…</p>
<div id="panel" hidden>
<section><h2>Your scopes</h2>
<div id="panel-scopes" class="factbox"></div>
<p class="legend">A scope nests: the whole site covers every system, a system covers its
games, a game group covers the games in it. You may appoint anybody to a scope you
already cover, never wider than your own.</p></section>

<section id="pending-wrap"><h2>Waiting on you</h2>
<p class="rules">Removal requests filed inside your jurisdiction that nobody has answered
yet. Granting takes the thing out of the index; declining asks for a note the person who
filed can read and answer.</p>
<div id="pending-list" class="factbox"></div>
<form id="f-decide" class="actform" hidden>
  <input type="hidden" name="kind" id="decide-kind">
  <input type="hidden" name="key" id="decide-key">
  <input type="hidden" id="decide-sub">
  <p class="statline" id="decide-what"></p>
  <label>Reason, if you are saying no <input name="reason" maxlength="500"
    placeholder="what is wrong with it; required to refuse"></label>
  <button class="btn" id="decide-yes" type="button">Approve</button>
  <button class="btn quiet" id="decide-no" type="button">Refuse</button>
  <button class="btn quiet" id="decide-cancel" type="button">Cancel</button>
</form></section>

<section><details class="secfold menufold"><summary><h2>Make somebody an expert for a game</h2></summary>
<form id="f-appoint-game" class="actform">
  <p class="rules">Downward only: you may hand out authority over a game you already
  cover. Members who already speak for it, through the game, its group, its system or
  the whole site, are not listed: a narrower appointment would add nothing.</p>
  <label>Game <select name="scope" id="appoint-game"></select></label>
  <label>Member <select name="user" id="appoint-game-user"></select></label>
  <label>Why them <input name="reason" required minlength="8" maxlength="500"
    placeholder="what makes them the right person for it"></label>
  <button class="btn">Appoint</button>
</form></details></section>

<section><details class="secfold menufold"><summary><h2>Make somebody an expert for a group</h2></summary>
<form id="f-appoint-group" class="actform">
  <p class="rules">A group expert speaks for every game in it, now and as it grows.
  Members who already cover the group are not listed.</p>
  <label>Group <select name="scope" id="appoint-group"></select></label>
  <label>Member <select name="user" id="appoint-group-user"></select></label>
  <label>Why them <input name="reason" required minlength="8" maxlength="500"
    placeholder="what makes them the right person for it"></label>
  <button class="btn">Appoint</button>
</form></details></section>

<section id="appoint-wide-wrap" hidden><details class="secfold menufold">
<summary><h2>Make somebody an expert for a system</h2></summary>
<form id="f-appoint-wide" class="actform">
  <p class="rules">A whole system, every game in it. Wider than this, the whole site, is
  the Steering Committee's to give, from its own panel.</p>
  <label>Scope <select name="scope" id="appoint-wide"></select></label>
  <label>Member <select name="user" id="appoint-wide-user"></select></label>
  <label>Why them <input name="reason" required minlength="8" maxlength="500"
    placeholder="what makes them the right person for it"></label>
  <button class="btn">Appoint</button>
</form></details></section>

<section><details class="secfold menufold"><summary><h2>Step down</h2></summary>
<form id="f-resign" class="actform">
  <p class="rules">Yours alone: it needs nobody\'s agreement and happens at once. Leave the
  scope empty to step down from all of them.</p>
  <label>Scope <select name="scope" id="resign-scope"></select></label>
  <button class="btn quiet">Step down</button>
</form></details></section>

<section><details class="secfold menufold"><summary><h2>Create a group</h2></summary>
<form id="f-groupnew" class="actform">
  <p class="rules">A group is one family of games, across every system it appeared on. You
  may gather games you already speak for. It exists the moment you create it; a mistaken
  one is deleted on the record. A game belongs to exactly one group.</p>
  <label>Key <input name="group" required pattern="[a-z0-9]+(-[a-z0-9]+)*"
    placeholder="lowercase-with-hyphens, used in the address"></label>
  <label>Title <input name="title" required maxlength="80" placeholder="Mega Man"></label>
  <label>Games <input name="games"></label>
  <datalist id="panel-gamelist"></datalist>
  <button class="btn">Create</button>
</form></details></section>

<section><details class="secfold menufold"><summary><h2>Change a group</h2></summary>
<form id="f-groupedit" class="actform">
  <label>Group <select name="group" id="groupedit-key"></select></label>
  <label>Add games <input name="add"></label>
  <label>Remove games <input name="remove"></label>
  <label>New title <input name="title" maxlength="80" placeholder="leave empty to keep it"></label>
  <button class="btn quiet">Save</button>
</form></details></section>

<section id="panel-annul-wrap" hidden>
<details class="secfold menufold"><summary><h2>Annul an appointment</h2></summary>
<form id="f-annul" class="actform">
  <p class="rules">The Committee decides this, not you: point at the post carrying its
  poll (Principles 2.5.4). The poll must be restricted to the Committee, public, and
  closed, and a simple majority of the whole Committee must have voted for it.</p>
  <label>Expert <input name="target" list="panel-expertlist" required
    placeholder="type to find the expert"></label>
  <datalist id="panel-expertlist"></datalist>
  <label>Scope <input name="scope" list="panel-scopelist"
    placeholder="leave empty for every scope they hold"></label>
  <label>Forum post id <input name="post" required pattern="[0-9]+"
    placeholder="the post carrying the poll"></label>
  <button class="btn quiet">Apply</button>
</form></details></section>
<datalist id="panel-scopelist"></datalist>
<p class="msg" id="panel-msg" hidden></p>
</div>
<script type="application/json" id="paneldata">{json.dumps(panel_data).replace('<', chr(92) + 'u003c')}</script>'''
(OUT / 'expert').mkdir(parents=True, exist_ok=True)
(OUT / 'expert' / 'index.html').write_text(
    page('Expert panel', body, '../', '<a href="../">Home</a> / Expert panel'))

# ---- founder panel ----
founder_now = sorted({ev['user'].lower() for (u, role, sc), ev in ROLES_NOW.items()
                      if role == 'founder'})
committee_members = sorted((ev['user'] for (u, role, sc), ev in ROLES_NOW.items()
                            if role == 'committee'), key=str.lower)
body = f'''<header class="ghead"><div><h1>Founder</h1>
<p class="authline">Seating and unseating Steering Committee members. Every use of this is a
role event with your name on it, public in the <a href="../policy/site-log/#roles">site
log</a> and on the member\'s own page, and the person is told. The Committee\'s own route,
deciding by poll, exists alongside this and keeps its thresholds.</p></div></header>
<p class="msg" id="fpanel-gate">Checking who you are…</p>
<div id="fpanel" hidden>
<section><h2>The Committee today</h2>
<div class="factbox">{''.join(f'<p class="statline">{member_chip(m, "../")}</p>'
                              for m in committee_members)
                      or '<p class="emptynote">Nobody sits on it yet.</p>'}</div></section>
<section><h2>Seat somebody</h2>
<form id="f-seat" class="actform">
  <input type="hidden" name="action" value="granted">
  <label>Member <input name="target" list="dl-members" required
    placeholder="type to find a member"></label>
  <label>Why them <input name="reason" required minlength="8" maxlength="500"
    placeholder="published with the decision"></label>
  <button class="btn">Seat</button>
</form></section>
<section><h2>Unseat somebody</h2>
<form id="f-unseat" class="actform">
  <input type="hidden" name="action" value="revoked">
  <label>Member <select name="target">{''.join(f'<option>{esc(m)}</option>'
                                               for m in committee_members)}</select></label>
  <label>Why <input name="reason" required minlength="8" maxlength="500"
    placeholder="published with the decision, and they are told it"></label>
  <button class="btn quiet">Unseat</button>
</form></section>
<p class="msg" id="fpanel-msg" hidden></p>
</div>
{dl_members(exclude=committee_members)}
<script type="application/json" id="fpaneldata">{json.dumps({'founders': founder_now}).replace('<', chr(92) + 'u003c')}</script>'''
(OUT / 'founder').mkdir(parents=True, exist_ok=True)
(OUT / 'founder' / 'index.html').write_text(
    page('Founder', body, '../', '<a href="../">Home</a> / Founder'))

# ---- steering committee panel ----
# Name claims are answered here rather than on the claim page, because the
# thing that makes them answerable, the requester's email address, must never
# be built into a static page. The list is fetched from the archivist by the
# people entitled to see it, and lives in the browser for as long as they look.
body = f'''<header class="ghead"><div><h1>Steering Committee</h1>
<p class="authline">Claims to held names, waiting to be answered. Approving hands the name
over, renames the forum account and lets that person import their own runs; denying asks
for a reason. Either way they are told, and the answer is public in the
<a href="../policy/site-log/#claims">site log</a>.</p></div></header>
<p class="msg" id="cpanel-gate">Checking who you are…</p>
<div id="cpanel" hidden>
<section><h2>Open claims</h2>
<p class="rules">Each row carries a masked form of the address the forum holds for that
member, worked out as this page loads and kept nowhere. It is enough to tell whether it is
the address that author would have; it is not the whole address, and nothing here or in the
archive ever stores either form. The claim page tells them plainly that you see this
much.</p>
<div id="cpanel-list" class="factbox"></div>
<form id="f-claimdecide" class="actform" hidden>
  <p class="statline" id="cdecide-what"></p>
  <input type="hidden" name="identity" id="cdecide-identity">
  <label>Reason, if you are denying it <input name="note" maxlength="500"
    placeholder="what is missing; the person is told this"></label>
  <button class="btn" id="cdecide-yes" type="button">Approve</button>
  <button class="btn quiet" id="cdecide-no" type="button">Deny</button>
  <button class="btn quiet" id="cdecide-cancel" type="button">Cancel</button>
</form>
<p class="msg" id="cpanel-msg" hidden></p></section>
<section><details class="secfold menufold"><summary><h2>Appoint a whole-site expert</h2></summary>
<form id="f-siteexpert" class="actform">
  <p class="rules">The widest scope there is: every system, every game, every group, and
  the standing to attest identities. It is the Committee's to give (Principles 2.5.3), and
  your name and reason are published with it. Members who already hold it are not
  listed.</p>
  <input type="hidden" name="scope" value="site">
  <label>Member <select name="user" id="siteexpert-user"></select></label>
  <label>Why them <input name="reason" required minlength="8" maxlength="500"
    placeholder="what makes them the right person for the whole site"></label>
  <button class="btn">Appoint</button>
</form></details></section>
<section><details class="secfold menufold"><summary><h2>Record a Committee decision</h2></summary>
<p class="rules">The Committee votes on the forum; this writes the result into the
archive, which is the only place a role is recorded. Point it at the post carrying the
poll: the poll must be restricted to the Committee, public, and closed, and a simple
majority of the whole Committee must have voted for it. Adding somebody to a forum
group grants nothing.</p>
<form id="f-role" class="actform">
  <label>Member <input name="target" list="dl-role-candidates" required
    placeholder="type to find a member"></label>
  <datalist id="dl-role-candidates"></datalist>
  <label>Role <select name="role">
    <option value="committee">Steering Committee</option>
    <option value="moderator">Moderator</option>
    <option value="editor">Editor</option></select></label>
  <label>Decision <select name="action">
    <option value="granted">Grant</option>
    <option value="revoked">Remove</option></select></label>
  <label>Forum post id <input name="post" required pattern="[0-9]+"
    placeholder="the post carrying the poll"></label>
  <label>Anything to add <input name="reason" maxlength="400"
    placeholder="optional; the vote itself is already recorded"></label>
  <button class="btn">Record</button>
</form>
<p class="msg" id="role-msg" hidden></p></details></section>
</div>
<script type="application/json" id="cpaneldata">{json.dumps({
    'committee': committee_now,
    'members': sorted((a['username'] for a in authors.values()), key=str.lower),
    'moderators': sorted({ev['user'] for (u, role, sc), ev in ROLES_NOW.items()
                          if role == 'moderator'}, key=str.lower),
    'editors': sorted({ev['user'] for (u, role, sc), ev in ROLES_NOW.items()
                       if role == 'editor'}, key=str.lower),
    'committeeNames': sorted({ev['user'] for (u, role, sc), ev in ROLES_NOW.items()
                              if role == 'committee'}, key=str.lower),
    'siteExperts': sorted({e['user'].lower() for e in experts_reg
                           if e['scope'] == 'site'})}).replace('<', chr(92) + 'u003c')}</script>'''
(OUT / 'committee').mkdir(parents=True, exist_ok=True)
(OUT / 'committee' / 'index.html').write_text(
    page('Steering Committee', body, '../', '<a href="../">Home</a> / Steering Committee'))

