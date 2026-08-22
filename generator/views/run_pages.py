"""View: run pages (renders on import; see views/__init__)."""
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
import providers  # noqa: E402  (config put archivist/ on the path)
from config import (
    ARCHIVE_RAW,
    ARCHIVE_REF,
    ARCHIVIST,
    FORUM,
    OUT,
)
from model import (
    PT_CONSOLE,
    archived_at,
    canon,
    cat_label,
    console_state,
    covering_experts,
    edits_of,
    eff_state,
    is_ranked,
    is_unclassified,
    live,
    metric_value,
    nlikes,
    repro_bounty,
    run_metric_defs,
    run_seconds,
    runs,
    systems,
)
from render import (
    CW_LABELS,
    NONE_TICK,
    author_chip,
    console_chip,
    esc,
    fmt_metric,
    inline,
    member_chip,
    page,
    run_clock,
    shot_url,
    state_chip,
    thumb_url,
    tick,
    wiki_html,
)

# ---- run pages ----
for r in runs:
    g = r['_game']
    t = run_clock(r)
    cl = cat_label(r)
    rs, vs = eff_state(r)
    auth_html = ' · '.join(author_chip(a['user'], '../../') for a in r['authors'])
    enc = next((e for e in r.get('encodes', []) if providers.resolve(e['url'])), None)
    enc_url = (r.get('encodes') or [{}])[0].get('url', '')
    vid = ''
    if enc:
        pv = providers.resolve(enc['url'])
        if pv:
            vid = (f'<div class="player"><iframe src="{esc(pv["embed"])}" '
                   f'title="Run encode" allowfullscreen></iframe></div>'
                   f'<p class="srcnote">External embedded video, not hosted at '
                   f'toolAssisted.run · '
                   f'<a href="{esc(enc["url"])}">watch on {esc(pv["name"])}</a></p>')
    if not vid and thumb_url(r):
        vid = (f'<div class="poster"><img src="{esc(thumb_url(r))}" alt="run thumbnail"></div>'
               f'<p class="srcnote">Thumbnail derived from the encode</p>')
    warns = r.get('contentWarnings', [])
    warn_html = ''
    if warns:
        wchips = ''.join(f'<span class="chip warnchip">{esc(CW_LABELS.get(w, w))}</span>' for w in warns)
        warn_html = (f'<div class="warnbox"><b>Content notes (disclosed by the author)</b>'
                     f'<div class="chips">{wchips}</div></div>')
    if 'sexual' in warns and vid:
        gate_img = (f'<img class="nsfwblur" src="{esc(thumb_url(r))}" alt="">'
                    if thumb_url(r) else '')
        vid = (f'<template id="nsfwreal">{vid}</template>'
               f'<div class="nsfw-gate" id="nsfwgate">{gate_img}'
               f'<div class="nsfw-overlay"><p>This run is flagged for sexual content.</p>'
               f'<button id="nsfwok" class="btn">I confirm I am 18 or older</button></div></div>')
    rom = r.get('contract', {}).get('rom', {})
    imported = r.get('imported', {})
    is_leg = rs == 'imported'
    imported_panel = ''
    if is_leg:
        imported_panel = f'''<div class="importedbox"><b>Imported</b>
  This run was originally published at <a href="{esc(imported.get('source',''))}">{esc(imported.get('source',''))}</a>
  and entered this archive as a voluntary import by one of its authors, who takes the
  responsibility for importing a collaborative work. The notes below are the author's own;
  text not written by the authors has been removed. The original publication was verified and reproduced
  at its source, a trusted site, so it is marked fully verified here without passing
  through this site's standard procedure. The movie file and notes were obtained freely from
  the source and are redistributed in observance of the Creative Commons Attribution 2.0
  license under which they were published there.</div>'''
    atts = r.get('attachments', [])
    attach_box = ''
    if atts:
        items = ''.join(
            f'<p class="statline"><a href="{ARCHIVE_RAW}/games/{g["key"]}/runs/{r["id"]}/{esc(a["file"])}">'
            f'{esc(a["file"].split("/")[-1])}</a> · {esc(a.get("role",""))}</p>' for a in atts)
        attach_box = f'<div class="factbox"><h4>Attached files</h4>{items}</div>'

    # community rosters
    roster_html = ''
    if not is_leg:
        reps = r.get('reproductions', [])
        vers = r.get('verifications', [])
        rep_items = []
        for act in sorted(reps, key=lambda a: a.get('date') or ''):
            inv = act.get('invalidated')
            shot = (f'<a class="shot" href="{esc(shot_url(r, act["screenshot"]))}">'
                    f'<img src="{esc(shot_url(r, act["screenshot"]))}" '
                    f'alt="ending screenshot by {esc(act["user"])}" loading="lazy"></a>') if act.get('screenshot') else ''
            meta = ' · '.join(x for x in [esc(act.get('date') or ''), esc(act.get('emulator') or '')] if x)
            note = f'<p class="actnote">{inline(act["notes"])}</p>' if act.get('notes') else ''
            invnote = (f'<p class="invnote">Invalidated by expert {esc(inv["by"])} on {esc(inv["date"])}: '
                       f'{esc(inv["reason"])}</p>') if inv else ''
            rep_items.append(f'<div class="act{" invalid" if inv else ""}"><div class="acthead">'
                             f'{member_chip(act["user"], "../../")}<span class="actmeta">{meta}</span></div>'
                             f'{note}{shot}{invnote}</div>')
        ver_items = []
        for act in sorted(vers, key=lambda a: a.get('date') or ''):
            inv = act.get('invalidated')
            note = f'<p class="actnote">{inline(act["notes"])}</p>' if act.get('notes') else ''
            invnote = (f'<p class="invnote">Invalidated by expert {esc(inv["by"])} on {esc(inv["date"])}: '
                       f'{esc(inv["reason"])}</p>') if inv else ''
            ver_items.append(f'<div class="act{" invalid" if inv else ""}"><div class="acthead">'
                             f'{member_chip(act["user"], "../../")}<span class="actmeta">{esc(act.get("date") or "")}</span></div>'
                             f'{note}{invnote}</div>')
        rep_body = ''.join(rep_items) or ('<p class="emptynote">No reproductions yet. Load the movie file on your own '
                                          f'setup, confirm it syncs, and be the first; the current bounty is '
                                          f'<b>{repro_bounty(r)} contributor points</b>.</p>')
        ver_body = ''.join(ver_items) or ('<p class="emptynote">No verifications yet. Watch the encode and confirm the '
                                          'run achieves its stated goal.</p>'
                                          if enc else
                                          '<p class="emptynote">No verifications yet, and no encode is linked, so '
                                          'verification is not possible until one is added.</p>')
        if is_unclassified(r):
            ver_sect = ('<h2>Verifications</h2><p class="emptynote">Unclassified runs are never '
                        'verified because no goal is defined. They rank by likes instead.</p>')
        else:
            ver_sect = f'<h2>Verifications ({len(live(vers))})</h2>\n<div class="roster">{ver_body}</div>'
        cons = r.get('consoleVerifications', [])
        cons_items = []
        for act in sorted(live(cons), key=lambda a: a.get('date') or ''):
            bits = []
            if act.get('hardware'):
                bits.append(esc(act['hardware']))
            bits.append(f'<a href="{esc(act["proof"])}">recording</a>')
            note = f'<p class="actnote">{inline(act["notes"])}</p>' if act.get('notes') else ''
            shot = (f'<a class="shot" href="{esc(shot_url(r, act["screenshot"]))}">'
                    f'<img src="{esc(shot_url(r, act["screenshot"]))}" loading="lazy" alt=""></a>'
                    if act.get('screenshot') else '')
            cons_items.append(
                f'<div class="act"><div class="acthead">{member_chip(act["user"], "../../")}'
                f'<span class="actmeta">{esc(act.get("date") or "")} · {" · ".join(bits)}</span></div>'
                f'{note}{shot}</div>')
        cons_body = ''.join(cons_items) or (
            '<p class="emptynote">Not yet played back on original hardware. This is optional '
            f'and never required for ranking, and it pays <b>{PT_CONSOLE} contributor points</b>.</p>')
        roster_html = f'''<h2>Reproductions ({len(live(reps))})</h2>
<div class="roster">{rep_body}</div>
{ver_sect}
<h2>Console verifications ({len(live(cons))})</h2>
<div class="roster">{cons_body}</div>'''
        cases = r.get('cases', [])
        if cases:
            citems = []
            for c in sorted(cases, key=lambda c: c['id']):
                chip = {'open': '<span class="chip pendchip">Open</span>',
                        'closed': '<span class="chip verchip">Closed: reaffirmed</span>',
                        'upheld': '<span class="chip upheldchip">Upheld: run returned to pending</span>'}[c['status']]
                votes = ''.join(
                    f'<p class="statline">{member_chip(v["user"], "../../")}: '
                    f'{"reaffirmed" if v["reaffirm"] else "withdrew"} on {esc(v["date"])}'
                    f'{" · " + inline(v["notes"]) if v.get("notes") else ""}</p>'
                    for v in c.get('reaffirmations', []))
                pending_voters = [u for u in c['verifiers']
                                  if u.lower() not in {v['user'].lower() for v in c.get('reaffirmations', [])}]
                waiting = (f'<p class="statline muted">Awaiting: {esc(", ".join(pending_voters))}</p>'
                           if c['status'] == 'open' and pending_voters else '')
                citems.append(f'''<div class="act"><div class="acthead"><b>Case {c['id']}</b>
{chip}<span class="actmeta">opened by {esc(c['openedBy'])} · {esc(c['date'])}</span></div>
<p class="actnote">{inline(c['reason'])}</p>{votes}{waiting}</div>''')
            roster_html += f'<h2>Cases ({len(cases)})</h2><div class="roster">{"".join(citems)}</div>'

    # the run's forum topic, read and replied to in place
    topic = (r.get('forum') or {}).get('topicId')
    if topic:
        discussion_html = f'''<section id="discussion" data-topic="{topic}"
 data-url="{esc((r.get('forum') or {}).get('url') or '')}">
<h2>Discussion</h2>
<div id="disc-posts"><p class="emptynote">Loading the discussion…</p></div>
<form id="disc-reply" class="actform" hidden>
  <label>Reply as <b id="disc-who"></b></label>
  <textarea name="body" rows="4" placeholder="Say something about this run…"></textarea>
  <button class="btn">Reply</button>
  <div class="actmsg" id="disc-msg" hidden></div>
</form>
<p class="rules" id="disc-login" hidden>This discussion lives on
<a href="{esc((r.get('forum') or {}).get('url') or FORUM)}">the forum</a>.
Log in (top right) to reply from here.</p></section>'''
    else:
        discussion_html = ''

    # status lines
    if is_leg:
        rep_line = f'{tick(rs)} Reproduced at the trusted site it was imported from'
        ver_line = f'{tick(vs)} Verified at the trusted site it was imported from'
    else:
        nrep, nver = len(live(r.get('reproductions', []))), len(live(r.get('verifications', [])))
        if r.get('videoOnly'):
            rep_line = (f'{NONE_TICK} Reproduction not applicable: video-only, '
                        f'no input movie exists')
        else:
            rep_line = (f'{tick(rs)} Reproduced by {nrep} member{"s" if nrep != 1 else ""}' if nrep
                        else f'{tick(rs)} Not reproduced yet, an optional assurance that '
                             f'the movie really plays')
        if is_unclassified(r):
            ver_line = f'{NONE_TICK} Verification not applicable: Unclassified runs rank by likes'
        else:
            ver_line = (f'{tick(vs)} Verified: ranked' if nver
                        else f'{tick(vs)} Not yet verified: one verification ranks this run')
    ncons = len(live(r.get('consoleVerifications', [])))
    cstate = console_state(r)
    if cstate == 'imported':
        cons_line = ('<span class="tick console">✓</span> Console verified at the '
                     'trusted site it was imported from' + (f', and by {ncons} member{"s" if ncons != 1 else ""} here'
                                        if ncons else ''))
    elif ncons:
        cons_line = (f'<span class="tick console">✓</span> Console verified by {ncons} '
                     f'member{"s" if ncons != 1 else ""}')
    elif is_leg:
        cons_line = ''
    else:
        cons_line = f'{NONE_TICK} Console verification: none yet (optional)'
    open_case = next((c for c in r.get('cases', []) if c['status'] == 'open'), None)

    # session-aware act forms (revealed by app.js based on identity/eligibility)
    act_data = {
        'run': r['id'],
        'imported': is_leg,
        'authors': [canon(a['user']) for a in r['authors']],
        'authorsDisplay': [a['user'] for a in r['authors']],
        'reproduced': [a['user'].lower() for a in r.get('reproductions', [])],
        'verified': [a['user'].lower() for a in r.get('verifications', [])],
        'consoled': [a['user'].lower() for a in r.get('consoleVerifications', [])],
        'experts': covering_experts(r['_game']['key']),
        'hasEncode': bool(enc) and not is_unclassified(r),
        'liveVerifs': len(live(r.get('verifications', []))),
        'openCase': ({'id': open_case['id'],
                      'verifiers': [u.lower() for u in open_case['verifiers']],
                      'voted': [v['user'].lower() for v in open_case.get('reaffirmations', [])]}
                     if open_case else None),
        'emulator': r.get('contract', {}).get('emulator') or '',
        'completed': r.get('completed') or '',
        'notesUrl': f'{ARCHIVE_RAW}/games/{g["key"]}/runs/{r["id"]}/notes.md',
        # two different permissions, and they were both called 'experts': the
        # second overwrote the first, so a site-wide expert saw no act forms at
        # all. Acts use the covering scope; the expert notes are deliberately
        # narrower (the game's or its group's experts, never site-wide).
        'videoOnly': bool(r.get('videoOnly')),
        'reproducedNames': [a['user'] for a in r.get('reproductions', [])
                            if not a.get('invalidated')],
        'verifiedNames': [a['user'] for a in r.get('verifications', [])
                          if not a.get('invalidated')],
        'consoledNames': [a['user'] for a in r.get('consoleVerifications', [])
                          if not a.get('invalidated')],
        'openReports': [{'id': x['id'], 'kind': x['kind'], 'by': x['by']}
                        for x in r.get('reports', []) if x['status'] == 'open'],
    }
    withdraw_form = '''
<div id="f-withdraw-wrap" hidden><details class="actform"><summary>Withdraw</summary>
<form id="f-withdraw">
  <p class="rules">Your own voluntary act as an author. For a mistake: a duplicate,
  the wrong file, a submission that should not have been made. The run leaves the
  listings; nothing is erased, and the reason you give is shown on its page.</p>
  <label>Reason (public, required)</label><input name="reason" required
   placeholder="e.g. duplicate of M100002, submitted twice by accident">
  <button class="btn warn">Withdraw</button>
</form></details></div>
'''
    community_forms = '' if is_leg else '''
<div id="f-repro-wrap" hidden><details class="actform"><summary>I reproduced this run</summary>
<form id="f-repro">
  <p class="rules">You loaded the movie file on your own setup and it synced to the end.
  An ending screenshot is required as proof.</p>
  <label>Emulator / core used</label><input name="emulator" placeholder="e.g. BizHawk 2.11">
  <label>Ending screenshot (png/jpg/webp)</label>
  <input name="screenshot" type="file" accept=".png,.jpg,.jpeg,.webp" required>
  <label>Notes for the next reproducer (optional)</label><textarea name="notes" rows="3"></textarea>
  <button class="btn">Record</button>
</form></details></div>
<div id="f-verify-wrap" hidden><details class="actform"><summary>I verified this run</summary>
<form id="f-verify">
  <p class="rules">You watched the encode and confirm the run achieves its stated category goal.</p>
  <label>Notes (optional)</label><textarea name="notes" rows="2"></textarea>
  <button class="btn">Record</button>
</form></details></div>
<div id="f-console-wrap" hidden><details class="actform"><summary>I played this run back on original hardware</summary>
<form id="f-console">
  <p class="rules">Optional and never required for ranking, and the most expensive act
  here: you replayed the movie on the real console and recorded it. Link the recording.</p>
  <label>Link to the recording (required)</label>
  <input name="proof" type="url" placeholder="https://…" required>
  <label>Hardware and setup used</label><input name="hardware" placeholder="e.g. NES + Replay device">
  <label>Photo of the setup or the ending (optional)</label>
  <input name="screenshot" type="file" accept=".png,.jpg,.jpeg,.webp">
  <label>Notes (optional)</label><textarea name="notes" rows="2"></textarea>
  <button class="btn">Record</button>
</form></details></div>
<form id="f-vote" class="actform" hidden>
  <h3>This run is under dispute, and you are one of its verifiers</h3>
  <p class="rules">Recheck your verification. Reaffirming stands by it; withdrawing retracts it.</p>
  <label>Notes (optional)</label><textarea name="notes" rows="2"></textarea>
  <div class="votebtns"><button class="btn" data-reaffirm="1">Reaffirm</button>
  <button class="btn warn" data-reaffirm="0">Withdraw</button></div>
</form>
<div id="f-case-wrap" hidden><details class="actform"><summary>Dispute this run</summary>
<form id="f-case">
  <p class="rules">A dispute opens a case; it never auto-disqualifies. The run's verifiers
  will be asked to reaffirm; a majority closes the case.</p>
  <label>What is wrong? (required, shown publicly)</label>
  <textarea name="reason" rows="3" required></textarea>
  <button class="btn warn">Dispute</button>
</form></details></div>
'''
    act_html = f'''
<script type="application/json" id="actdata">{json.dumps(act_data).replace('<', chr(92) + 'u003c')}</script>
<div id="actzone" class="actzone" hidden>
<h2>Contribute to this run</h2>
<div class="actforms">
<div id="f-edit-wrap" hidden><details class="actform"><summary>Edit run</summary>
<form id="f-edit">
  <p class="rules">Revise the run's details. Every edit is a public commit;
  history is never erased. Authors revise their own work freely; a covering
  expert's change carries a public reason.</p>
  <div id="fe-authors">
  <label>Authors (every human, honestly; type to search, click to add)</label>
  <div class="authpick">
    <div class="authchips"></div>
    <input class="authsearch" placeholder="Type a username…" autocomplete="off">
    <div class="authlist" hidden></div>
    <input type="hidden" name="authors">
  </div>
  </div>
  <label>Encode link</label><input name="encode" type="url" value="{esc(enc_url)}" placeholder="https://youtu.be/…">
  <label>Emulator / core (optional)</label><input name="emulator">
  <label>When was the run completed? (optional; shown beside the submission date)</label>
  <input name="completed" type="date" max="{datetime.date.today().isoformat()}">
  {''.join(
      f'<label>{esc(m["label"])} '
      f'({esc(m.get("unit") or ("seconds" if m["type"] == "time" else "number"))}; '
      f'this category ranks by it. Leave empty to keep, 0 for not yet stated)</label>'
      f'<input name="metric_{esc(m["key"])}" type="number" step="any" min="0" '
      + (f'value="{mv:g}">' if (mv := (r.get("metrics") or {}).get(m["key"])) else '>')
      for m in run_metric_defs(r) if m['key'] != 'time')}
  <label>Notes (<a href="../../formatting/" target="_blank">formatting guide</a>)</label><textarea name="notes" rows="12"></textarea>
  <div id="fe-attach">
  <label>Add supplementary files (optional: text configs, or additional movie
  files; they join the run's attached files)</label>
  <input name="attachments" type="file" multiple>
  </div>
  <label id="fe-why" hidden>Why (public, shown in the edit log)
  <input name="reason" minlength="8" maxlength="500"
   placeholder="published in the site log beside your name"></label>
  <button class="btn">Save</button>
</form></details></div>
{withdraw_form}{community_forms}
</div>
<p id="act-login" hidden><a href="{ARCHIVIST}/login">Log in via the forum</a> to contribute
to this run.</p>
<p id="act-msg" class="actmsg" hidden></p>
</div>'''

    # every expert-only action, gathered in one clearly-marked box at the
    # very bottom of the page (the orange dashed Expert menu); the forms,
    # their ids and their behavior are unchanged, only the housing moved
    expert_menu = f'''
<div id="expertmenu" class="expertmenu" hidden><h2>Expert menu</h2>
<p class="rules">Only experts whose scope covers this game see this box; every action here is logged in the open.</p>
<div id="f-rundelete-wrap" hidden><details class="actform"><summary>Delete this run</summary>
<form id="f-rundelete">
  <p class="rules">Outright and permanent: for tests, spam, things that are not tool-assisted
  runs, and mistakes. A genuine work is withdrawn or erased through its own procedures, never
  this. Your reason is all that remains, in the site log, with your name.</p>
  <label>Why, publicly (required)</label>
  <textarea name="reason" rows="2" required minlength="8"
            placeholder="e.g. a test submission, not a real run"></textarea>
  <button class="btn danger">Delete</button>
</form></details></div>
<div id="f-invalidate-wrap" hidden><details class="actform"><summary>Invalidate a contribution</summary>
<form id="f-invalidate">
  <p class="rules">For a reproduction, verification or console verification that does not
  hold up. The act stays on the record, marked invalidated, with your reason next to it.
  Anyone may redo it properly afterwards. This is about the quality of the check, never
  about the run's merit.</p>
  <label>Which contribution</label>
  <select id="inv-target" name="target"></select>
  <input type="hidden" name="kind" id="inv-kind">
  <label>Why, publicly (required)</label>
  <textarea name="reason" rows="3" required
            placeholder="e.g. the screenshot is from a different run"></textarea>
  <button class="btn">Invalidate</button>
</form></details></div>
<div id="f-resolve-wrap" hidden><details class="actform"><summary>Close a report</summary>
<form id="f-resolve">
  <p class="rules">Resolve it if something was done, dismiss it if nothing needed doing.
  Either way your text is published with the report in the moderation log.</p>
  <label>Which report</label>
  <select id="res-report" name="report"></select>
  <label>Outcome</label>
  <select name="outcome">
    <option value="resolved">Resolved: something was done</option>
    <option value="dismissed">Dismissed: nothing needed doing</option>
  </select>
  <label>Your resolution, publicly (required)</label>
  <textarea name="resolution" rows="3" required></textarea>
  <button class="btn">Close</button>
</form></details></div>
<p id="expert-msg" class="actmsg" hidden></p></div>'''

    dispute_banner = ''
    if open_case:
        voted = len(open_case.get('reaffirmations', []))
        dispute_banner = f'''<div class="disputebox"><b>This run is under dispute (case {open_case['id']})</b>
Opened by {esc(open_case['openedBy'])} on {esc(open_case['date'])}: {inline(open_case['reason'])}
Its verifiers have been asked to reaffirm ({voted} of {len(open_case['verifiers'])} votes so far).
The run keeps its status until the case resolves; nothing is ever automatic.</div>'''
    body = f'''
<header class="ghead"><div>
  <div class="chips"><span class="chip">{esc(systems[g['system']]['name'])}</span>
  <span class="chip">{esc(cl)}</span>{state_chip(r)}{console_chip(r)}{'<span class="chip pendchip">Under dispute</span>' if open_case else ''}
  <span class="visits" id="visitbadge" title="number of visits" hidden><svg class="eyeic" viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z"/><circle cx="12" cy="12" r="2.6"/></svg>&nbsp;<span id="visitnum"></span></span></div>
  <h1>{esc(g['title'])}</h1>
  <p class="authline">by {auth_html}</p>
  {f'<p class="gdesc">“{esc(r["goalDescription"])}”</p>' if r.get('goalDescription') else ''}</div>
  <div class="hbtns">
    <button id="likebtn" class="likestar" title="Like this run">
      <span class="starglyph">★</span> Like<span class="likecount" id="likecount">{nlikes(r)}</span></button>
    {'<span class="chip provchip" title="No input movie exists; the encode is the run">Video-only</span>' if r.get('videoOnly') else
     f'<a class="btn" href="{ARCHIVE_RAW}/games/{g["key"]}/runs/{r["id"]}/{esc(r["movie"]["file"])}">Download movie file</a>'}
    <a class="srclink" href="https://github.com/ToolAssisted-run/archive/tree/{ARCHIVE_REF}/games/{g['key']}/runs/{r['id']}">view run folder in the archive →</a>
    <a class="srclink" href="https://github.com/ToolAssisted-run/archive/commits/{ARCHIVE_REF}/games/{g['key']}/runs/{r['id']}"
       title="every change to this run, in git, reversible">change history{f" · {len(edits_of[('run', r['id'])])} revision" + ("s" if len(edits_of[('run', r['id'])]) != 1 else "") if ('run', r['id']) in edits_of else ''} →</a>
  </div>
</header>
{vid}
<div class="cols">
<div class="main">
  {warn_html}{imported_panel}{dispute_banner}
  <h2>Author's notes</h2>
  <div class="notes">{wiki_html(re.sub(r'^>.*$', '', r['_notes'], flags=re.M))}</div>
  {roster_html}
  {act_html}
  {discussion_html}
</div>
<aside class="side">
  <div class="factbox"><h4>Run</h4><dl>
    {'<dt>Kind</dt><dd>Video-only: no input movie exists</dd>' if r.get('videoOnly') else
     f'<dt>Frames</dt><dd class="big">{r["movie"]["frames"]:,}</dd>'}
    {f'<dt>Time</dt><dd>{t}{" (stated by the submitter)" if r.get("videoOnly") else ""}</dd>'
     if run_seconds(r) is not None else ''}
    {''.join(f'<dt>{esc(m["label"])}</dt><dd>{fmt_metric(metric_value(r, m), m)}'
             f'{"" if metric_value(r, m) is not None else " (not yet stated)"}</dd>'
             for m in run_metric_defs(r) if m['key'] != 'time')}
    {'' if r.get('videoOnly') else
     f'<dt>Rerecords</dt><dd>{(r["movie"].get("rerecords") or 0):,}</dd>'
     f'<dt>Format</dt><dd>{esc(r["movie"]["format"])}</dd>'}
    {f"<dt>Completed</dt><dd>{esc(r['completed'])}</dd>" if r.get('completed') else ''}
    <dt>{'Published' if is_leg else 'Submitted'}</dt><dd>{esc((r.get('submitted') or '')[:10])}</dd>
    {f"<dt>Archived here</dt><dd>{esc(archived_at(r)[:10])}</dd>" if is_leg else ''}</dl></div>
  {'' if r.get('videoOnly') else f'''<div class="factbox"><h4>Reproduction info</h4><dl>
    <dt>Emulator</dt><dd>{esc(r.get('contract', {}).get('emulator') or '—')}</dd>
    {f"<dt>ROM</dt><dd>{esc(rom.get('name'))}</dd>" if rom.get('name') else ''}
    {f"<dt>ROM sha1</dt><dd class='trunc'>{esc(rom.get('sha1')[:12])}…</dd>" if rom.get('sha1') else ''}
</dl></div>'''}
  {attach_box}
  <div class="factbox"><h4>Status</h4>
    <p class="statline">{rep_line}</p>
    <p class="statline">{ver_line}</p>
    {f'<p class="statline">{cons_line}</p>' if cons_line else ''}</div>
  {f'<div class="factbox"><h4>Reports</h4><p class="statline">⚑ {sum(1 for x in r.get("reports", []) if x["status"] == "open")} open · <a href="../../policy/site-log/#reports">site log</a></p></div>' if any(x['status'] == 'open' for x in r.get('reports', [])) else ''}
  <details class="actform reportbox" id="reportbox" hidden><summary>⚑ Report this run</summary>
  <form id="f-report">
    <label>Reason</label><select name="kind">
      <option value="missing-content-warnings">Missing content warnings</option>
      <option value="spam-malicious">Spam / malicious / deceitful</option>
      <option value="miscredited">Not credited correctly</option>
      <option value="licensing">Licensing / copyright problem</option>
      <option value="other">Other</option></select>
    <label>Details</label><textarea name="details" rows="3"></textarea>
    <button class="btn warn">Report</button>
    <p class="rules">Reports are public, get a unique id, and are addressed by the
    game's experts; everything lands in the open moderation log.</p>
  </form></details>
  <p id="report-msg" class="actmsg" hidden></p>
  <script type="application/json" id="likedata">{json.dumps({'run': r['id'],
      'authors': [canon(a['user']) for a in r['authors']],
      'likes': [l['user'].lower() for l in r.get('likes', [])]}).replace('<', chr(92) + 'u003c')}</script>
  {'' if is_leg or is_ranked(r) else f'<a class="btn quiet" href="../../contribute/">This run needs help: Contribute</a>'}
</aside></div>
{expert_menu}'''
    crumb = (f'<a href="../../browse/">Runs</a> / '
             f'<a href="../../games/{g["key"]}/">{esc(g["title"])}</a> / {r["id"]}')
    (OUT / 'runs' / r['id']).mkdir(parents=True, exist_ok=True)
    (OUT / 'runs' / r['id'] / 'index.html').write_text(
        page(f'{g["title"]} · {cl}', body, '../../', crumb, 'Runs', wide=True))

