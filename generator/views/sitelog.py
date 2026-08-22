"""View: sitelog (renders on import; see views/__init__)."""
import json
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
    systems,
    withdrawn_runs,
)
from render import page, tpl

# ---- moderation log ----
# (conduct and terms are sections of the Community Principles on GitHub)
mod_entries = []
for r in runs:
    for kind in ('reproductions', 'verifications'):
        for a in r.get(kind, []):
            inv = a.get('invalidated')
            if inv and inv.get('by') != 'case' and inv['by'].lower() != a['user'].lower():
                mod_entries.append((inv.get('at') or inv.get('date', ''), inv['by'], kind[:-1], a['user'],
                                    inv.get('reason', ''), r))
mod_entries.sort(key=lambda e: e[0], reverse=True)
REPORT_LABELS = {'missing-content-warnings': 'Missing content warnings',
                 'spam-malicious': 'Spam / malicious / deceitful',
                 'miscredited': 'Not credited correctly',
                 'licensing': 'Licensing / copyright problem', 'other': 'Other'}
# a status chip is (css class or '', label); the template draws it
REPORT_CHIPS = {'open': ('pendchip', 'Open'), 'resolved': ('verchip', 'Resolved'),
                'dismissed': ('', 'Dismissed')}
def _arrival_desc(rep):
    # newest arrival first; the id breaks a same-second tie
    return (rep.get('at') or rep['date'], rep['id'])
all_reports = sorted(((rep, r) for r in runs for rep in r.get('reports', [])),
                     key=lambda x: _arrival_desc(x[0]), reverse=True)
all_reports.sort(key=lambda x: x[0]['status'] != 'open')
# Identity attestations: an expert saying, on the record, that a member is a
# given author. It is the one place the site accepts human judgement instead of
# proof, so it is logged like any other act of authority.
attestations = sorted(
    ((a.get('claimedAtTime') or a.get('claimedAt', ''), a.get('attestedBy', ''), a['username'],
      a.get('claimedBy', ''), a.get('attestation', ''))
     for a in authors.values()
     if a.get('claimMethod') == 'attested' and a.get('attestedBy')),
    reverse=True)

# ---- role changes: granted and taken away, site-wide, newest first ----
role_events_desc = sorted(role_events, key=lambda e: e.get('at') or e['date'], reverse=True)

# ---- runs withdrawn, and the ones erased for good ----
withdrawn_desc = sorted(withdrawn_runs,
                        key=lambda x: x['withdrawn'].get('at') or x['withdrawn'].get('date', ''),
                        reverse=True)

# ---- ratifications: an expert saying a game is real and distinct ----
# Seeded games arrived established and were never ratified here, so they carry
# no ratifier and appear nowhere below. A group has no ratification act at all
# yet: groups are edited straight in the archive, by hand.
ratified = sorted(
    ([(g.get('ratifiedAtTime') or g.get('ratifiedAt', ''), g['ratifiedBy'], g['title'],
       f'games/{g["slugpath"]}/', systems[g['system']]['name'])
      for g in games.values() if g.get('ratifiedBy')]
     + [(gr.get('ratifiedAtTime') or gr.get('ratifiedAt', ''), gr['ratifiedBy'], gr['title'],
         # a group with no games in it has no page of its own to point at
         f'groups/{gr["key"]}/' if any(l['key'] == gr['key'] for l in live_groups) else '',
         'group')
        for gr in groups if gr.get('ratifiedBy')]),
    reverse=True)
# refusals belong beside the approvals: they are the same decision, answered
# the other way, and only reading both tells you what the experts actually did
refused = sorted(
    ([(g['rejected'].get('at') or g['rejected']['date'], g['rejected']['by'], g['title'],
       f'games/{g["slugpath"]}/', f'{systems[g["system"]]["name"]} · refused',
       g['rejected']['reason']) for g in rejected_games]
     + [(gr['rejected'].get('at') or gr['rejected']['date'], gr['rejected']['by'], gr['title'], '',
         'group · refused and dissolved', gr['rejected']['reason'])
        for gr in rejected_groups]), reverse=True)
# (when, by, title, href, what, why): ratifications carry no reason
decisions = sorted([(w, b, ti, h, wh, '') for w, b, ti, h, wh in ratified] + refused,
                   reverse=True)

# ---- name claims: asked, answered, and never carrying an address ----
CLAIM_CHIPS = {'open': ('pendchip', 'Open'), 'approved': ('verchip', 'Approved'),
               'denied': ('', 'Denied')}
claim_reqs = []
if (ARCHIVE / 'claims.json').exists():
    claim_reqs = json.loads((ARCHIVE / 'claims.json').read_text()).get('requests', [])
claim_reqs = sorted(claim_reqs, key=lambda r: (r['status'] != 'open', r.get('at') or r['date']),
                    reverse=True)

# ---- edits: git carries the diff, this carries the account ----
edits_desc = sorted(edit_events, key=lambda e: e.get('at') or e['date'], reverse=True)

# ---- deletions: the log is all that remains, so it renders always ----
del_events = []
if (ARCHIVE / 'deletions.json').exists():
    del_events = json.loads((ARCHIVE / 'deletions.json').read_text()).get('events', [])
del_events.sort(key=lambda e: e.get('at') or e['date'], reverse=True)
DEL_KIND = {'run': 'movie', 'game': 'game', 'group': 'group', 'member': 'member'}

# ---- removal requests: asked in public, answered in public ----
REMOVAL_CHIPS = {'open': ('pendchip', 'Open'), 'granted': ('verchip', 'Granted'),
                 'declined': ('', 'Declined')}
all_removals = sorted(
    ([(r, g['title'], f'games/{g["slugpath"]}/', 'game')
      for g in list(games.values()) for r in g.get('removalRequests', [])]
     + [(r, gr['title'],
         f'groups/{gr["key"]}/' if any(l['key'] == gr['key'] for l in live_groups) else '',
         'group')
        for gr in groups + removed_groups + rejected_groups
        for r in gr.get('removalRequests', [])]),
    key=lambda x: (x[0]['status'] != 'open', x[0].get('at') or x[0]['date']),
    reverse=True)

open_cases = [(r_, c) for r_ in runs for c in r_.get('cases', []) if c['status'] == 'open']

modlog = tpl('sitelog.html',
             role_events=role_events_desc, attestations=attestations,
             claim_reqs=claim_reqs, CLAIM_CHIPS=CLAIM_CHIPS,
             all_reports=all_reports, REPORT_LABELS=REPORT_LABELS, REPORT_CHIPS=REPORT_CHIPS,
             decisions=decisions,
             all_removals=all_removals, REMOVAL_CHIPS=REMOVAL_CHIPS,
             edit_events=edits_desc,
             del_events=del_events, DEL_KIND=DEL_KIND,
             withdrawn_runs=withdrawn_desc,
             open_cases=open_cases,
             mod_entries=mod_entries)
# Conduct and terms are sections of the Community Principles on GitHub, so
# there is one text to read and one place to amend it; only the moderation
# log is generated here, because it is derived from archive data.
(OUT / 'policy' / 'site-log').mkdir(parents=True, exist_ok=True)
(OUT / 'policy' / 'site-log' / 'index.html').write_text(page(
    'Site log', modlog, '../../',
    seo={'path': 'policy/site-log/',
         'description': 'Every act of authority on toolAssisted.run, in the open: appointments, edits, deletions, decisions.'}))
# The log used to live at /policy/moderation-log/ and that address is in the
# footer of every page ever served, in forum posts and in the archivist's own
# replies. Pages has no rewrites, so the old path keeps a page of its own that
# forwards; it costs one file and it keeps every link anybody ever shared.
(OUT / 'policy' / 'moderation-log').mkdir(parents=True, exist_ok=True)
(OUT / 'policy' / 'moderation-log' / 'index.html').write_text(page(
    'Moved: the site log', tpl('sitelog_moved.html'), '../../', '',
    head_extra=tpl('sitelog_moved_head.html'),
    seo={'path': 'policy/site-log/', 'noindex': True}))
