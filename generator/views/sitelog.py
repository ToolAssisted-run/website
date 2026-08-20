"""View: sitelog (renders on import; see views/__init__)."""
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
    ARCHIVE,
    OUT,
)
from model import (
    ROLE_LABEL,
    authors,
    edit_events,
    games,
    groups,
    live_groups,
    rejected_games,
    rejected_groups,
    removed_groups,
    role_events,
    runs,
    scope_words,
    systems,
    withdrawn_runs,
)
from render import (
    esc,
    inline,
    member_chip,
    page,
)

# ---- moderation log ----
# (conduct and terms are sections of the Community Principles on GitHub)
mod_entries = []
for r in runs:
    for kind in ('reproductions', 'verifications'):
        for a in r.get(kind, []):
            inv = a.get('invalidated')
            if inv and inv.get('by') != 'case' and inv['by'].lower() != a['user'].lower():
                mod_entries.append((inv.get('date', ''), inv['by'], kind[:-1], a['user'],
                                    inv.get('reason', ''), r))
REPORT_LABELS = {'missing-content-warnings': 'Missing content warnings',
                 'spam-malicious': 'Spam / malicious / deceitful',
                 'miscredited': 'Not credited correctly',
                 'licensing': 'Licensing / copyright problem', 'other': 'Other'}
all_reports = sorted(((rep, r) for r in runs for rep in r.get('reports', [])),
                     key=lambda x: (x[0]['status'] != 'open', -x[0]['id']))
report_items = []
for rep, r_ in all_reports:
    chip = {'open': '<span class="chip pendchip">Open</span>',
            'resolved': '<span class="chip verchip">Resolved</span>',
            'dismissed': '<span class="chip">Dismissed</span>'}[rep['status']]
    res = (f'<p class="statline">Resolution by expert {esc(rep["resolvedBy"])} on '
           f'{esc(rep.get("resolvedAt", ""))}: {inline(rep.get("resolution", ""))}</p>'
           if rep['status'] != 'open' else '')
    report_items.append(f'''<div class="act" id="R{rep['id']}">
<div class="acthead"><b><a href="#R{rep['id']}">R{rep['id']}</a></b> {chip}
<span class="actmeta">{esc(rep['date'])} · by {esc(rep['by'])}</span></div>
<p class="actnote">{esc(REPORT_LABELS.get(rep['kind'], rep['kind']))} ·
<a href="../../runs/{r_['id']}/">{esc(r_['_game']['title'])} ({r_['id']})</a>
{(': ' + inline(rep.get('details', ''))) if rep.get('details') else ''}</p>{res}</div>''')
reports_html = (f'<section id="reports"><h2>Movie reports ({len(report_items)})</h2>'
                f'<div class="roster">'
                f'{"".join(report_items)}</div></section>' if report_items else
                '<section id="reports"><h2>Movie reports</h2><p class="emptynote">'
                'No reports have been filed.</p></section>')
mod_rows = ''.join(f'''<tr><td>{esc(d)}</td><td>{esc(by)}</td>
<td>Invalidated a {kind} by {esc(target)} on
<a href="../../runs/{r_['id']}/">{esc(r_['_game']['title'])} ({r_['id']})</a></td>
<td>{esc(reason)}</td></tr>'''
                   for d, by, kind, target, reason, r_ in sorted(mod_entries, reverse=True,
                                                                key=lambda e: e[0]))
# Identity attestations: an expert saying, on the record, that a member is a
# given author. It is the one place the site accepts human judgement instead of
# proof, so it is logged like any other act of authority.
attestations = sorted(
    ((a.get('claimedAt', ''), a.get('attestedBy', ''), a['username'],
      a.get('claimedBy', ''), a.get('attestation', ''))
     for a in authors.values()
     if a.get('claimMethod') == 'attested' and a.get('attestedBy')),
    reverse=True)
att_rows = ''.join(f'''<tr><td>{esc(d)}</td><td>{member_chip(by, '../../')}</td>
<td>Attested that {member_chip(who, '../../')} is <b>{esc(name)}</b></td>
<td>{inline(how, '../../')}</td></tr>'''
                   for d, by, name, who, how in attestations)
attest_html = (f'''<section id="attestations"><h2>Identity attestations ({len(attestations)})</h2>
<p class="authline">A name held for an author elsewhere is handed to a member when a
Steering Committee member vouches for the identity, because a proof that depends on another
site's permissions leaves out the people most likely to need it. Each entry names who vouched
and the method, and can be challenged.</p>
<table><thead><tr><th>Date</th><th>Expert</th><th>Action</th><th>Method</th></tr></thead>
<tbody>{att_rows}</tbody></table></section>''' if attestations else
    '<section id="attestations"><h2>Identity attestations</h2><p class="emptynote">'
    'Nobody has needed one yet.</p></section>')

# ---- role changes: granted and taken away, site-wide, newest first ----
role_rows_all = ''.join(
    f'''<tr><td>{esc(ev['date'])}</td><td>{member_chip(ev['user'], '../../')}</td>
<td>{'<span class="chip verchip">Granted</span>' if ev['action'] == 'granted'
     else '<span class="chip pendchip">Removed</span>'}
{esc(ROLE_LABEL.get(ev['role'], ev['role']))}{
    ' · ' + esc(scope_words(ev['scope'])) if ev.get('scope') else ''}</td>
<td>{'the founder' if ev['by'] == 'founder' else
     'a Committee vote' if ev['by'] == 'committee' else member_chip(ev['by'], '../../')}</td>
<td>{inline(ev['reason'], '../../')}{
    f' · <a href="{esc(ev["proof"])}">where it was decided</a>' if ev.get('proof') else ''}</td></tr>'''
    for ev in sorted(role_events, key=lambda e: e['date'], reverse=True))
roles_html = (f'''<section id="roles"><h2>Role changes ({len(role_events)})</h2>
<p class="authline">Every grant and every removal, with the reason given at the time. Roles
are not a status somebody has always had, and this is the only record of them: the forum's
groups are printed from it.</p>
<table><thead><tr><th>Date</th><th>Member</th><th>Change</th><th>By</th><th>Reason</th></tr></thead>
<tbody>{role_rows_all}</tbody></table></section>''' if role_events else
    '<section id="roles"><h2>Role changes</h2><p class="emptynote">No roles have changed '
    'hands yet.</p></section>')

# ---- runs withdrawn, and the ones erased for good ----
wd_rows = ''.join(
    f'''<tr><td>{esc(r_['withdrawn'].get('date', ''))}</td>
<td>{member_chip(r_['withdrawn']['by'], '../../')}</td>
<td><a href="../../runs/{r_['id']}/">{esc(r_['_game']['title'])} ({r_['id']})</a>
{' <span class="chip pendchip">movie erased</span>' if r_['withdrawn'].get('contentRemoved') else ''}
<span class="actmeta"> as {esc(r_['withdrawn'].get('role') or 'author')}</span></td>
<td>{inline(r_['withdrawn'].get('reason', ''), '../../')}</td></tr>'''
    for r_ in sorted(withdrawn_runs, key=lambda x: x['withdrawn'].get('date', ''), reverse=True))
withdrawn_html = (f'''<section id="withdrawn"><h2>Withdrawals and erasures ({len(withdrawn_runs)})</h2>
<p class="authline">A withdrawn run keeps its page as a tombstone, because a hole in the
record is worse than an honest gap. Where every author asked for it, the movie file itself
was permanently erased, and that is marked here.</p>
<table><thead><tr><th>Date</th><th>By</th><th>Run</th><th>Reason</th></tr></thead>
<tbody>{wd_rows}</tbody></table></section>''' if withdrawn_runs else
    '<section id="withdrawn"><h2>Withdrawals and erasures</h2><p class="emptynote">'
    'Nothing has been withdrawn.</p></section>')

# ---- ratifications: an expert saying a game is real and distinct ----
# Seeded games arrived established and were never ratified here, so they carry
# no ratifier and appear nowhere below. A group has no ratification act at all
# yet: groups are edited straight in the archive, by hand.
ratified = sorted(
    ([(g.get('ratifiedAt', ''), g['ratifiedBy'], g['title'],
       f'games/{g["slugpath"]}/', systems[g['system']]['name'])
      for g in games.values() if g.get('ratifiedBy')]
     + [(gr.get('ratifiedAt', ''), gr['ratifiedBy'], gr['title'],
         # a group with no games in it has no page of its own to point at
         f'groups/{gr["key"]}/' if any(l['key'] == gr['key'] for l in live_groups) else '',
         'group')
        for gr in groups if gr.get('ratifiedBy')]),
    reverse=True)
# refusals belong beside the approvals: they are the same decision, answered
# the other way, and only reading both tells you what the experts actually did
refused = sorted(
    ([(g['rejected']['date'], g['rejected']['by'], g['title'],
       f'games/{g["slugpath"]}/', f'{systems[g["system"]]["name"]} · refused',
       g['rejected']['reason']) for g in rejected_games]
     + [(gr['rejected']['date'], gr['rejected']['by'], gr['title'], '',
         'group · refused and dissolved', gr['rejected']['reason'])
        for gr in rejected_groups]), reverse=True)
rat_rows = ''.join(
    f'''<tr><td>{esc(when)}</td><td>{member_chip(by, '../../')}</td>
<td>{f'<a href="../../{href}">{esc(title)}</a>' if href else f'<b>{esc(title)}</b>'}
<span class="actmeta"> {esc(what)}</span>{f'<p class="actnote">{inline(why, "../../")}</p>' if why else ''}</td></tr>'''
    for when, by, title, href, what, why in
    sorted([(w, b, ti, h, wh, '') for w, b, ti, h, wh in ratified] + refused,
           reverse=True))
ratify_html = (f'''<section id="ratifications"><h2>Game and group decisions ({len(ratified) + len(refused)})</h2>
<p class="authline">A game created here is provisional until an expert whose scope covers it
confirms it is a real, distinct game, and a group is provisional until an expert confirms it
is a real family rather than one person's filing preference. Games that came in with the
seeding import were established before this archive existed and were ratified by nobody here,
so they are not listed as if they had been.</p>
<table><thead><tr><th>Date</th><th>Expert</th><th>Game</th></tr></thead>
<tbody>{rat_rows}</tbody></table></section>''' if (ratified or refused) else
    '<section id="ratifications"><h2>Game and group decisions</h2>'
    '<p class="emptynote">Nothing has been ratified here yet. Games and group from the '
    'seeding import arrived established and were ratified by nobody on this site.'
    '</p></section>')

# ---- name claims: asked, answered, and never carrying an address ----
REQ_CHIP_CLAIM = {'open': '<span class="chip pendchip">Open</span>',
                  'approved': '<span class="chip verchip">Approved</span>',
                  'denied': '<span class="chip">Denied</span>'}
claim_reqs = []
if (ARCHIVE / 'claims.json').exists():
    claim_reqs = json.loads((ARCHIVE / 'claims.json').read_text()).get('requests', [])
claim_rows = ''.join(
    f'''<tr><td>{esc(r['date'])}</td><td>{member_chip(r['member'], '../../')}</td>
<td><b>{esc(r['identity'])}</b><p class="actnote">{inline(r['evidence'], '../../')}</p></td>
<td>{REQ_CHIP_CLAIM[r['status']]}{
    f'<span class="actmeta"> by ' + member_chip(r.get('decidedBy', ''), '../../')
    + ' on ' + esc(r.get('decidedAt', '')) + '</span>' if r.get('decidedBy') else ''}{
    f'<p class="actnote">' + inline(r.get('note', ''), '../../') + '</p>' if r.get('note') else ''}
</td></tr>'''
    for r in sorted(claim_reqs, key=lambda r: (r['status'] != 'open', r['date']),
                    reverse=True))
claims_html = (f'''<section id="claims"><h2>Name claims ({len(claim_reqs)})</h2>
<p class="authline">A name held for an author elsewhere is handed over when the Steering
Committee agrees it is theirs. The asking and the answer are both here. What the Committee
saw of the claimant's email address is not, and never was: they see a masked form of it,
worked out when they look, written into neither this archive nor anywhere else.</p>
<table><thead><tr><th>Date</th><th>Member</th><th>Name and evidence</th><th>Answer</th></tr></thead>
<tbody>{claim_rows}</tbody></table></section>''' if claim_reqs else
    '<section id="claims"><h2>Name claims</h2>'
    '<p class="emptynote">Nobody has filed one yet.</p></section>')

# ---- edits: git carries the diff, this carries the account ----
edit_rows = ''.join(
    f'''<tr><td>{esc(e['date'])}</td><td>{member_chip(e['by'], '../../')}</td>
<td><b>{esc(e['key'])}</b><span class="actmeta"> {esc(e['kind'])} \u00b7 {esc(e['field'])}</span>
<p class="actnote">{esc((e.get('from') or '(empty)'))} \u2192 {esc((e.get('to') or '(empty)'))}</p></td>
<td>{inline(e['reason'], '../../')}</td></tr>'''
    for e in sorted(edit_events, key=lambda e: e['date'], reverse=True))
edits_html = (f'''<section id="edits"><h2>Edits ({len(edit_events)})</h2>
<p class="authline">Authors revise their own runs; experts may correct anything inside their
jurisdiction, member content included, and answer for it. Every revision says who, what it
was, what it became, and why. Git history carries the exact diff and the way back.</p>
<table><thead><tr><th>Date</th><th>By</th><th>What</th><th>Why</th></tr></thead>
<tbody>{edit_rows}</tbody></table></section>''' if edit_events else
    '<section id="edits"><h2>Edits</h2>'
    '<p class="emptynote">Nothing has been revised.</p></section>')

# ---- deletions: the log is all that remains, so it renders always ----
del_events = []
if (ARCHIVE / 'deletions.json').exists():
    del_events = json.loads((ARCHIVE / 'deletions.json').read_text()).get('events', [])
DEL_KIND = {'run': 'movie', 'game': 'game', 'group': 'group', 'member': 'member'}
del_rows = ''.join(
    f'''<tr><td>{esc(e['date'])}</td><td>{member_chip(e['by'], '../../')}</td>
<td><b>{esc(e['title'])}</b><span class="actmeta"> {esc(DEL_KIND.get(e['kind'], e['kind']))} \u00b7 {esc(e['key'])}</span>{
    f'<span class="actmeta"> \u00b7 its movies moved to {esc(e["movedTo"])}</span>' if e.get('movedTo') else ''}</td>
<td>{inline(e['reason'], '../../')}</td></tr>'''
    for e in sorted(del_events, key=lambda e: e['date'], reverse=True))
deletions_html = (f'''<section id="deletions"><h2>Deletions ({len(del_events)})</h2>
<p class="authline">Deleted outright by an expert (movies, games, groups) or the Steering
Committee (members): tests, spam, mistakes, things that were never really works. The thing
is gone; who deleted it and why is not, and never will be.</p>
<table><thead><tr><th>Date</th><th>By</th><th>What</th><th>Why</th></tr></thead>
<tbody>{del_rows}</tbody></table></section>''' if del_events else
    '<section id="deletions"><h2>Deletions</h2>'
    '<p class="emptynote">Nothing has been deleted outright.</p></section>')

# ---- removal requests: asked in public, answered in public ----
REQ_CHIP = {'open': '<span class="chip pendchip">Open</span>',
            'granted': '<span class="chip verchip">Granted</span>',
            'declined': '<span class="chip">Declined</span>'}
all_removals = sorted(
    ([(r, g['title'], f'games/{g["slugpath"]}/', 'game')
      for g in list(games.values()) for r in g.get('removalRequests', [])]
     + [(r, gr['title'],
         f'groups/{gr["key"]}/' if any(l['key'] == gr['key'] for l in live_groups) else '',
         'group')
        for gr in groups + removed_groups + rejected_groups
        for r in gr.get('removalRequests', [])]),
    key=lambda x: (x[0]['status'] != 'open', x[0]['date']), reverse=True)

def removal_row(r, title, href, kind):
    what = (f'<a href="../../{href}">{esc(title)}</a>' if href else f'<b>{esc(title)}</b>')
    answer = REQ_CHIP[r['status']]
    if r.get('decidedBy'):
        answer += (f'<span class="actmeta"> by {member_chip(r["decidedBy"], "../../")} '
                   f'on {esc(r.get("decidedAt", ""))}</span>')
    if r.get('note'):
        answer += f'<p class="actnote">{inline(r["note"], "../../")}</p>'
    return (f'<tr><td>{esc(r["date"])}</td><td>{member_chip(r["by"], "../../")}</td>'
            f'<td>{what}<span class="actmeta"> {esc(kind)}</span>'
            f'<p class="actnote">{inline(r["reason"], "../../")}</p></td>'
            f'<td>{answer}</td></tr>')

removal_rows = ''.join(removal_row(*x) for x in all_removals)
removals_html = (f'''<section id="removals"><h2>Removal requests ({len(all_removals)})</h2>
<p class="authline">An expert never deletes a game or a group: they ask, and a site-wide
expert answers. A granted removal takes the thing out of the index and leaves every page and
every run exactly where it was, because the movies inside were never what was in question.</p>
<table><thead><tr><th>Date</th><th>Asked by</th><th>What</th><th>Answer</th></tr></thead>
<tbody>{removal_rows}</tbody></table></section>''' if removal_rows else
    '<section id="removals"><h2>Removal requests</h2>'
    '<p class="emptynote">Nobody has asked for anything to be removed.</p></section>')

open_case_rows = ''.join(
    f'''<tr><td>{esc(c.get('opened', ''))}</td>
<td><a href="../../runs/{r_['id']}/">{esc(r_['_game']['title'])} ({r_['id']})</a></td>
<td>case {c['id']} · {len(c.get('reaffirmations', []))} of {len(c['verifiers'])} verifiers
have stood by their word</td></tr>'''
    for r_ in runs for c in r_.get('cases', []) if c['status'] == 'open')
cases_html = (f'''<section id="cases"><h2>Open cases</h2>
<p class="authline">A contradiction never removes anybody's word by itself; it opens a case
and asks the people who spoke to say so again.</p>
<table><thead><tr><th>Opened</th><th>Run</th><th>State</th></tr></thead>
<tbody>{open_case_rows}</tbody></table></section>''' if open_case_rows else
    '<section id="cases"><h2>Open cases</h2><p class="emptynote">'
    'No case is open.</p></section>')

modlog = f'''<header class="ghead"><div><h1>Site log</h1>
<p class="authline">Everything done here by anybody holding authority, and everything done
to a run after it was archived. Every entry states who acted, when, and why, and none of it
is ever removed: an act of authority that cannot be read back is not accountable to
anyone.</p>
<p class="statline"><a href="#roles">Role changes</a> · <a href="#attestations">Identity
attestations</a> · <a href="#claims">Name claims</a> ·
<a href="#reports">Movie reports</a> ·
<a href="#ratifications">Ratifications</a> · <a href="#removals">Removal requests</a> ·
<a href="#edits">Expert edits</a> · <a href="#deletions">Deletions</a> · <a href="#withdrawn">Withdrawals</a> ·
<a href="#cases">Cases</a> · <a href="#moderation">Moderation</a></p></div></header>
{roles_html}
{attest_html}
{claims_html}
{reports_html}
{ratify_html}
{removals_html}
{edits_html}
{deletions_html}
{withdrawn_html}
{cases_html}
<section id="moderation"><h2>Moderation actions ({len(mod_entries)})</h2>
<p class="authline">Moderation in moderation: every action is justified, states its reason,
is appealable, and is logged here, in the open, permanently.</p>
{f'<table><thead><tr><th>Date</th><th>Expert</th><th>Action</th><th>Reason</th></tr></thead><tbody>{mod_rows}</tbody></table>' if mod_entries else '<p class="emptynote">No moderation actions have been taken.</p>'}
</section>'''
# Conduct and terms are sections of the Community Principles on GitHub, so
# there is one text to read and one place to amend it; only the moderation
# log is generated here, because it is derived from archive data.
(OUT / 'policy' / 'site-log').mkdir(parents=True, exist_ok=True)
(OUT / 'policy' / 'site-log' / 'index.html').write_text(page('Site log', modlog, '../../'))
# The log used to live at /policy/moderation-log/ and that address is in the
# footer of every page ever served, in forum posts and in the archivist's own
# replies. Pages has no rewrites, so the old path keeps a page of its own that
# forwards; it costs one file and it keeps every link anybody ever shared.
(OUT / 'policy' / 'moderation-log').mkdir(parents=True, exist_ok=True)
(OUT / 'policy' / 'moderation-log' / 'index.html').write_text(page(
    'Moved: the site log',
    '<header class="ghead"><div><h1>This log moved</h1>'
    '<p class="authline">Moderation is now one section of the '
    '<a href="../site-log/">site log</a>, which carries every act of authority '
    'here rather than only that one. Taking you there.</p></div></header>',
    '../../', '',
    head_extra='<link rel="canonical" href="../site-log/">'
               '<meta http-equiv="refresh" content="1; url=../site-log/">'))

