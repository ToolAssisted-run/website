"""View: member pages (renders on import; see views/__init__)."""
import json
from config import ARCHIVE_TREE, OUT
from model import (
    board_date,
    ROLE_LABEL,
    author_news,
    author_stats,
    authors,
    canon,
    points,
    profile_slug,
    role_events_of,
    runs,
    scope_words,
    withdrawn_runs,
)
from render import SITE_URL, page, tpl

# the client-side data feeds beside the pages (names for pickers, the news
# dates for the avatar dot, per-member stats for the menu)
(OUT / 'assets' / 'authornames.json').write_text(json.dumps(
    sorted((a['username'] for a in authors.values()), key=str.lower)))
(OUT / 'assets' / 'news.json').write_text(json.dumps(
    {u: [e['date'] for e in lst] for u, lst in author_news.items()}))
(OUT / 'assets' / 'authorstats.json').write_text(json.dumps(author_stats))

NEWS_ICON = {'reproduced': '↻', 'verified': '✓', 'liked': '★'}

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
             scripts=['page-member.js']))

# withdrawn runs still get a page: an honest tombstone, not a 404
for r in withdrawn_runs:
    g = r['_game']
    body = tpl('member_pages_withdrawn.html', r=r, ARCHIVE_TREE=ARCHIVE_TREE)
    d = OUT / 'runs' / r['id']
    d.mkdir(parents=True, exist_ok=True)
    (d / 'index.html').write_text(page(f"{g['title']} (withdrawn)", body, '../../',
                                       tpl('member_pages_crumb.html', kind='run', label=r['id']),
                                       seo={'path': f'runs/{r["id"]}/', 'noindex': True}))
