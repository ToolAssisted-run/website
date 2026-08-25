"""View: run pages (renders on import; see views/__init__)."""
import datetime
import re
import providers  # noqa: E402  (config put archivist/ on the path)
from config import ARCHIVE_RAW, OUT
from model import (
    canon,
    cat_label,
    groups,
    nlikes,
    nvisits,
    console_state,
    covering_experts,
    edits_of,
    eff_state,
    is_ranked,
    is_unclassified,
    live,
    metric_value,
    run_metric_defs,
    run_seconds,
    runs,
    systems,
)
from render import SITE_URL, breadcrumb_ld, page, primary_metric_text, run_clock, thumb_url, tpl

# ---- run pages ----
# ---- "You may also like": eight runs, closest first ----
# Buckets in priority order: the run's own designated picks (related, kept
# in the author's order), same category different subcategory, same game,
# same group, same system, then the site's most liked, most viewed, most
# recent. Within every computed bucket the verified come first, then the
# most liked, then the most recent.
REEL_SIZE = 8
_runs_by_id = {r_['id']: r_ for r_ in runs}
_group_of_game = {}
for _gr in groups:
    for _k in _gr.get('games', []):
        _group_of_game.setdefault(_k, set()).add(_gr['key'])


def _bucket_order(cands):
    c = sorted(cands, key=lambda x: x.get('submitted') or '', reverse=True)
    c = sorted(c, key=nlikes, reverse=True)
    return sorted(c, key=is_ranked, reverse=True)


def reel_for(r):
    picked = []
    seen = {r['id']}

    def take(cands):
        for c in cands:
            if len(picked) >= REEL_SIZE:
                return
            if c['id'] in seen:
                continue
            seen.add(c['id'])
            picked.append(c)
    take(_runs_by_id[i] for i in r.get('related', []) if i in _runs_by_id)
    goal = (r.get('category') or {}).get('goal')
    sub = (r.get('category') or {}).get('sub')
    same_game = [x for x in runs if x['game'] == r['game']]
    take(_bucket_order([x for x in same_game
                        if (x.get('category') or {}).get('goal') == goal
                        and (x.get('category') or {}).get('sub') != sub]))
    take(_bucket_order(same_game))
    my_groups = _group_of_game.get(r['game'], set())
    if my_groups:
        take(_bucket_order([x for x in runs
                            if _group_of_game.get(x['game'], set()) & my_groups]))
    system = r['game'].split('/')[0]
    take(_bucket_order([x for x in runs if x['game'].split('/')[0] == system]))
    if len(picked) < REEL_SIZE:
        rest = sorted(runs, key=lambda x: x.get('submitted') or '', reverse=True)
        take(sorted(sorted(rest, key=nlikes, reverse=True),
                    key=is_ranked, reverse=True))                    # most liked
        take(sorted(sorted(rest, key=nvisits, reverse=True),
                    key=is_ranked, reverse=True))                    # most viewed
        take(sorted(rest, key=is_ranked, reverse=True))              # most recent
    return picked


for r in runs:
    g = r['_game']
    t = run_clock(r)
    cl = cat_label(r)
    rs, vs = eff_state(r)
    enc = next((e for e in r.get('encodes', []) if providers.resolve(e['url'])), None)
    enc_url = (r.get('encodes') or [{}])[0].get('url', '')
    pv = providers.resolve(enc['url']) if enc else None
    warns = r.get('contentWarnings', [])
    # the files the movie was made against: the list on new records, the
    # legacy single rom (shown as one row) on older ones
    contract = r.get('contract', {})
    files = contract.get('files') or ([contract['rom']] if contract.get('rom') else [])
    files = [f for f in files if f.get('name') or f.get('sha1')]
    imported = r.get('imported', {})
    is_leg = rs == 'imported'
    reps = sorted(r.get('reproductions', []), key=lambda a: a.get('date') or '')
    vers = sorted(r.get('verifications', []), key=lambda a: a.get('date') or '')
    cons = sorted(live(r.get('consoleVerifications', [])), key=lambda a: a.get('date') or '')
    # each case with the verifiers who have not voted on it yet
    cases = []
    for c in sorted(r.get('cases', []), key=lambda c: c['id']):
        voted = {v['user'].lower() for v in c.get('reaffirmations', [])}
        cases.append((c, [u for u in c['verifiers'] if u.lower() not in voted]))
    forum = r.get('forum') or {}
    nrep, nver = len(live(r.get('reproductions', []))), len(live(r.get('verifications', [])))
    ncons = len(cons)
    open_case = next((c for c in r.get('cases', []) if c['status'] == 'open'), None)
    # the secondary metrics of the run's category, with the stated value of each
    metric_rows = [(m, metric_value(r, m)) for m in run_metric_defs(r) if m['key'] != 'time']
    nrev = len(edits_of[('run', r['id'])]) if ('run', r['id']) in edits_of else 0
    # quoted lines are the archive's own headers, not the author's notes
    notes_src = re.sub(r'^>.*$', '', r['_notes'], flags=re.M)

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
        # the game's categories, for the expert's move control: options and
        # their subcategories, and where this run sits now
        'categories': [{'key': o['key'], 'label': o['label'],
                        'subcategories': [{'key': x['key'], 'label': x['label']}
                                          for x in o.get('subcategories', [])]}
                       for d in g['categories']['dimensions'] for o in d['options']],
        'goal': (r.get('category') or {}).get('goal', ''),
        'sub': (r.get('category') or {}).get('sub', ''),
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
    like_data = {'run': r['id'],
                 'authors': [canon(a['user']) for a in r['authors']],
                 'likes': [l['user'].lower() for l in r.get('likes', [])]}
    body = tpl('run_pages.html', r=r, g=g, t=t, cl=cl, rs=rs, vs=vs, enc=enc, enc_url=enc_url,
               reel=reel_for(r),
               pv=pv, warns=warns, files=files, imported=imported, is_leg=is_leg,
               reps=reps, vers=vers, cons=cons, cases=cases,
               topic=forum.get('topicId'), forum_url=forum.get('url'),
               nrep=nrep, nver=nver, ncons=ncons, cstate=console_state(r),
               open_case=open_case, metric_rows=metric_rows, nrev=nrev, notes_src=notes_src,
               atts=r.get('attachments', []), today=datetime.date.today().isoformat(),
               n_open_reports=len(act_data['openReports']),
               act_data=act_data, like_data=like_data)
    crumb = tpl('run_pages_crumb.html', r=r, g=g).strip()
    (OUT / 'runs' / r['id']).mkdir(parents=True, exist_ok=True)
    who = ', '.join(a['user'] for a in r['authors'])
    sysname = systems[g['system']]['name']
    secs = run_seconds(r)
    tu = thumb_url(r)
    seo_title = (f'{g["title"]} ({g["system"].upper()}) TAS in {primary_metric_text(r)} '
                 f'by {who} · {cl}')
    state_word = ('Verified' if (is_leg or is_ranked(r)) else 'Pending verification')
    seo_desc = (f'{g["title"]} ({sysname}) tool-assisted speedrun, {cl}, '
                f'{primary_metric_text(r)} by {who}. {state_word}. Watch the encode'
                + ('' if r.get('videoOnly') else ' and download the movie file')
                + ' on toolAssisted.run.')
    ld = [breadcrumb_ld([('Games', 'games/'), (sysname, f'systems/{g["system"]}/'),
                         (g['title'], f'games/{g["key"]}/'), (cl, f'runs/{r["id"]}/')])]
    if enc:
        video = {'@context': 'https://schema.org', '@type': 'VideoObject',
                 'name': seo_title, 'description': seo_desc,
                 'uploadDate': (r.get('submitted') or '')[:10],
                 'url': f'{SITE_URL}/runs/{r["id"]}/',
                 'embedUrl': providers.resolve(enc['url'])['embed']}
        if tu:
            video['thumbnailUrl'] = SITE_URL + tu
        if secs:
            video['duration'] = f'PT{int(secs // 3600)}H{int(secs % 3600 // 60)}M{int(secs % 60)}S'
        ld.append(video)
    (OUT / 'runs' / r['id'] / 'index.html').write_text(
        page(seo_title, body, '../../', crumb, 'Runs', wide=True,
             seo={'path': f'runs/{r["id"]}/', 'description': seo_desc,
                  'image': (SITE_URL + tu) if tu else None, 'type': 'video.other' if enc else 'article',
                  'ld': ld}))

