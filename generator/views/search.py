"""View: search (renders search page and emits search-index.json; see views/__init__)."""
import json
from config import OUT
from model import (
    author_stats,
    authors,
    board_date,
    cat_label,
    eff_state,
    games,
    groups,
    groups_by_game,
    has_page,
    is_ranked,
    is_unclassified,
    nlikes,
    profile_slug,
    run_metric_defs,
    runs,
    systems,
)
from render import page, primary_metric_html, tpl

# ---- Build compact search index dataset ----
# 1. Games: match title and group title
s_games = []
for g in games.values():
    grps = [gr['title'] for gr in groups_by_game.get(g['key'], []) if has_page(gr)]
    s_games.append({
        'k': g['key'],
        't': g['title'],
        's': g['system'],
        'sn': systems[g['system']]['name'],
        'g': grps,
    })

# 2. Game Groups: match title or title of any game in group
s_groups = []
for gr in groups:
    if gr.get('synthetic'):
        continue
    member_titles = [games[gk]['title'] for gk in gr.get('games', []) if gk in games]
    gr_runs = [r for r in runs if any(g_['key'] == gr['key'] for g_ in groups_by_game.get(r['_game']['key'], []))]
    s_groups.append({
        'k': gr['key'],
        't': gr['title'],
        'c': len(member_titles),
        'gc': len(member_titles),
        'rc': len(gr_runs),
        'gt': member_titles,
    })

# 3. Systems: match title or slug
s_systems = []
for k, v in sorted(systems.items()):
    sys_games = [g for g in games.values() if g['system'] == k]
    sys_runs = [r for r in runs if r['_game']['system'] == k]
    s_systems.append({
        'k': k,
        'n': v['name'],
        'gc': len(sys_games),
        'rc': len(sys_runs),
    })

# 4. Authors: match username only
s_authors = []
for uname, a in authors.items():
    st = author_stats.get(uname, {})
    s_authors.append({
        'u': a['username'],
        'p': profile_slug(uname),
        'r': st.get('runs', 0),
        's': st.get('author', 0),
    })

# 5. Runs: exactly as browse (title, cat, author username, run ID, metric)
s_runs = []
for r in runs:
    g = r['_game']
    rs, vs = eff_state(r)
    st = ('imported' if rs == 'imported' else
          'unclassified' if is_unclassified(r) else
          'verified' if is_ranked(r) else 'pending')
    s_runs.append({
        'id': r['id'],
        't': g['title'],
        's': g['system'],
        'sn': systems[g['system']]['name'],
        'c': cat_label(r),
        'a': [a['user'] for a in r['authors']],
        'm': run_metric_defs(r)[0]['label'],
        'res': primary_metric_html(r),
        'stars': nlikes(r),
        'd': board_date(r),
        'st': 'verified' if st == 'imported' else st,
    })

search_dataset = {
    'games': s_games,
    'groups': s_groups,
    'systems': s_systems,
    'authors': s_authors,
    'runs': s_runs,
}

# Write static search index JSON file
(OUT / 'assets').mkdir(exist_ok=True)
(OUT / 'assets' / 'search-index.json').write_text(
    json.dumps(search_dataset, separators=(',', ':')),
    encoding='utf-8'
)

# Render search page
body = tpl('search.html')
(OUT / 'search').mkdir(exist_ok=True)
(OUT / 'search' / 'index.html').write_text(page(
    'Search archive', body, '../', '', '',
    scripts=['page-search.js'],
    seo={'path': 'search/',
         'description': 'Search toolAssisted.run for games, game groups, systems, runs, and authors.'}),
    encoding='utf-8'
)
