"""View: member pages (renders on import; see views/__init__)."""
import json
from config import ARCHIVE_TREE, OUT
from model import (
    REPORT_CHIPS,
    REPORT_LABELS,
    ROLE_LABEL,
    author_news,
    author_stats,
    authors,
    board_date,
    canon,
    diff_games,
    edit_events,
    games,
    groups_by_key,
    points,
    profile_slug,
    role_events_of,
    runs,
    runs_by_id,
    scope_words,
    systems,
    withdrawn_runs,
)
from render import SITE_URL, breadcrumb_ld, page, tpl

# the client-side data feeds beside the pages (names for pickers, the news
# dates for the avatar dot, per-member stats for the menu)
(OUT / 'assets' / 'authornames.json').write_text(json.dumps(
    sorted((a['username'] for a in authors.values()), key=str.lower)), encoding='utf-8')
(OUT / 'assets' / 'news.json').write_text(json.dumps(
    {u: [e['date'] for e in lst] for u, lst in author_news.items()}), encoding='utf-8')
(OUT / 'assets' / 'authorstats.json').write_text(json.dumps(author_stats), encoding='utf-8')

NEWS_ICON = {'reproduced': '↻', 'verified': '✓', 'liked': '★'}

# Pre-index audit log events across all runs and edits
all_runs = runs + withdrawn_runs

edits_by_author = {}
for e in edit_events:
    b = e.get('by', '').lower()
    if b:
        edits_by_author.setdefault(b, []).append(e)
for lst in edits_by_author.values():
    lst.sort(key=lambda e: e.get('at') or e.get('date', ''), reverse=True)

mods_on_author = {}
mods_by_author = {}
for r_ in all_runs:
    for kind, roster in [
        ('reproduction', 'reproductions'),
        ('verification', 'verifications'),
        ('console verification', 'consoleVerifications'),
    ]:
        for a_entry in r_.get(roster, []):
            inv = a_entry.get('invalidated')
            if inv and inv.get('by') != 'case' and inv.get('cause') != 'edit':
                by_u = inv.get('by', '').lower()
                tgt_u = a_entry.get('user', '').lower()
                entry = (inv.get('at') or inv.get('date', ''), inv['by'], kind, a_entry['user'], inv.get('reason', ''), r_)
                if by_u != tgt_u:
                    mods_on_author.setdefault(tgt_u, []).append(entry)
                    mods_by_author.setdefault(by_u, []).append(entry)
for lst in mods_on_author.values():
    lst.sort(key=lambda e: e[0], reverse=True)
for lst in mods_by_author.values():
    lst.sort(key=lambda e: e[0], reverse=True)

reports_on_author = {}
reports_by_author = {}
reports_resolved_by = {}
cases_on_author = {}
cases_opened_by = {}
cases_voted_by = {}

for r_ in all_runs:
    auths = [canon(x['user']) for x in r_.get('authors', [])]
    for rep in r_.get('reports', []):
        for auth in auths:
            reports_on_author.setdefault(auth, []).append((rep, r_))
        rep_by = rep.get('by', '').lower()
        if rep_by:
            reports_by_author.setdefault(rep_by, []).append((rep, r_))
        res_by = rep.get('resolvedBy', '').lower()
        if res_by:
            reports_resolved_by.setdefault(res_by, []).append((rep, r_))

    for c in r_.get('cases', []):
        for auth in auths:
            cases_on_author.setdefault(auth, []).append((c, r_))
        op_by = c.get('openedBy', '').lower()
        if op_by:
            cases_opened_by.setdefault(op_by, []).append((c, r_))
        for v in c.get('reaffirmations', []):
            v_u = v.get('user', '').lower()
            if v_u:
                cases_voted_by.setdefault(v_u, []).append((v, c, r_))

for lst in reports_on_author.values():
    lst.sort(key=lambda x: x[0].get('at') or x[0].get('date', ''), reverse=True)
    lst.sort(key=lambda x: x[0].get('status') != 'open')
for lst in reports_by_author.values():
    lst.sort(key=lambda x: x[0].get('at') or x[0].get('date', ''), reverse=True)
    lst.sort(key=lambda x: x[0].get('status') != 'open')
for lst in reports_resolved_by.values():
    lst.sort(key=lambda x: x[0].get('resolvedAt') or x[0].get('at') or x[0].get('date', ''), reverse=True)

for lst in cases_on_author.values():
    lst.sort(key=lambda x: x[0].get('date') or x[0].get('opened', ''), reverse=True)
    lst.sort(key=lambda x: x[0].get('status') != 'open')
for lst in cases_opened_by.values():
    lst.sort(key=lambda x: x[0].get('date') or x[0].get('opened', ''), reverse=True)
    lst.sort(key=lambda x: x[0].get('status') != 'open')
for lst in cases_voted_by.values():
    lst.sort(key=lambda x: x[0].get('date', ''), reverse=True)

attestations_by_author = {}
for other_uname, other_a in authors.items():
    att_by = other_a.get('attestedBy', '').lower()
    if att_by:
        attestations_by_author.setdefault(att_by, []).append(other_a)


def render_member_log(uname, a, st, role_rows):
    slug = profile_slug(uname)
    ldir = OUT / 'authors' / slug / 'logs'
    ldir.mkdir(parents=True, exist_ok=True)
    lrel = '../../../'

    m_on = mods_on_author.get(uname, [])
    rep_on = reports_on_author.get(uname, [])
    cas_on = cases_on_author.get(uname, [])

    edits = edits_by_author.get(uname, [])
    m_by = mods_by_author.get(uname, [])
    rep_by = reports_by_author.get(uname, [])
    rep_res = reports_resolved_by.get(uname, [])
    cas_op = cases_opened_by.get(uname, [])
    cas_voted = cases_voted_by.get(uname, [])
    att_by_list = attestations_by_author.get(uname, [])

    totals = {
        'roles': len(role_rows),
        'mods_on': len(m_on),
        'reports_on': len(rep_on),
        'cases_on': len(cas_on),
        'edits_by': len(edits),
        'mods_by': len(m_by),
        'reports_by': len(rep_by) + len(rep_res),
        'cases_by': len(cas_op) + len(cas_voted),
        'attestations_by': len(att_by_list),
    }

    body = tpl(
        'member_log.html',
        a=a,
        st=st,
        rel=lrel,
        role_rows=role_rows,
        mods_on=m_on,
        reports_on=rep_on,
        cases_on=cas_on,
        edits_by=edits,
        mods_by=m_by,
        reports_by=rep_by,
        reports_resolved_by=rep_res,
        cases_opened_by=cas_op,
        cases_voted_by=cas_voted,
        attestations_by=att_by_list,
        totals=totals,
        runs_by_id=runs_by_id,
        games=games,
        groups_by_key=groups_by_key,
        systems=systems,
        diff_games=diff_games,
        REPORT_CHIPS=REPORT_CHIPS,
        REPORT_LABELS=REPORT_LABELS,
    )
    crumb = tpl('member_log_crumb.html', a=a, rel=lrel).strip()
    title = f'Log · {a["username"]}'
    seo_desc = f'Audit log, role history, revisions, reports, disputes, and moderation history for {a["username"]} on toolAssisted.run.'
    ld = [
        breadcrumb_ld([
            ('Members', 'authors/'),
            (a['username'], f'authors/{slug}/'),
            ('Log', f'authors/{slug}/logs/'),
        ])
    ]
    (ldir / 'index.html').write_text(
        page(
            title,
            body,
            lrel,
            crumb,
            'Members',
            seo={
                'path': f'authors/{slug}/logs/',
                'description': seo_desc,
                'ld': ld,
            },
        ),
        encoding='utf-8',
    )


# ---- author pages ----
for uname, a in authors.items():
    mine = sorted((r for r in runs if any(canon(x['user']) == uname for x in r['authors'])),
                  # the date the row shows is the date the list sorts by,
                  # most recent first (#47)
                  key=lambda r: (board_date(r), r.get('submitted') or ''), reverse=True)
    contrib = points.get(uname)
    cpts = contrib['points'] if contrib else 0
    acts = sorted(contrib['acts'],
                  # sort on comparable fields only: two acts sharing a date,
                  # description and payout would otherwise compare run dicts
                  key=lambda a: (a[0], a[1], a[2], a[3]['id']), reverse=True
                  ) if contrib and contrib['acts'] else []
    # the roles this member has held, and lost, in order: the log belongs to the
    # person it is about, at the bottom of their own page
    role_rows = []
    for ev in reversed(role_events_of(a['username'])):
        what = ROLE_LABEL.get(ev['role'], ev['role'])
        if ev['role'] == 'expert' and ev.get('scope'):
            what += f' · {scope_words(ev["scope"])}'
        by = ev['by']
        role_rows.append(dict(ev, what=what,
                              by_name=authors.get(by.lower(), {}).get('username', by)))
    body = tpl('member_pages_author.html', a=a, st=author_stats[uname], mine=mine,
               my_news=author_news.get(uname, [])[:50], NEWS_ICON=NEWS_ICON,
               cpts=cpts, acts=acts, role_rows=role_rows)
    (OUT / 'authors' / profile_slug(uname)).mkdir(parents=True, exist_ok=True)
    (OUT / 'authors' / profile_slug(uname) / 'index.html').write_text(
        page(f'{a["username"]} · TAS runs and contributions', body, '../../',
             tpl('member_pages_crumb.html', kind='author', label=a['username']), 'Members',
             seo={'path': f'authors/{profile_slug(uname)}/',
                  'description': (f'{a["username"]} on toolAssisted.run: '
                                  f'{len(mine)} tool-assisted speedrun{"s" if len(mine) != 1 else ""}, '
                                  f'contributions and role history.'),
                  'type': 'profile',
                  'ld': [{'@context': 'https://schema.org', '@type': 'Person',
                          'name': a['username'],
                          'url': f'{SITE_URL}/authors/{profile_slug(uname)}/'}]},
             scripts=['page-member.js']), encoding='utf-8')
    render_member_log(uname, a, author_stats[uname], role_rows)

# withdrawn runs still get a page: an honest tombstone, not a 404
for r in withdrawn_runs:
    g = r['_game']
    body = tpl('member_pages_withdrawn.html', r=r, ARCHIVE_TREE=ARCHIVE_TREE)
    d = OUT / 'runs' / r['id']
    d.mkdir(parents=True, exist_ok=True)
    (d / 'index.html').write_text(page(f"{g['title']} (withdrawn)", body, '../../',
                                       tpl('member_pages_crumb.html', kind='run', label=r['id']),
                                       seo={'path': f'runs/{r["id"]}/', 'noindex': True}), encoding='utf-8')
