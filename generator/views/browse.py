"""View: browse (renders on import; see views/__init__)."""
from config import OUT
from model import (
    archived_at,
    board_date,
    cat_label,
    eff_state,
    groups_by_game,
    has_page,
    is_ranked,
    is_unclassified,
    live_groups,
    nlikes,
    run_metric_defs,
    runs,
    systems,
)
from render import page, primary_metric_html, tpl

# ---- browse page (client-side search / facets / sort) ----
index = []
for r in sorted(runs, key=lambda r: (board_date(r), r.get('submitted') or ''), reverse=True):
    g = r['_game']
    rs, vs = eff_state(r)
    state = ('imported' if rs == 'imported' else
             'unclassified' if is_unclassified(r) else
             'verified' if is_ranked(r) else 'pending')
    grps = [gr['key'] for gr in groups_by_game.get(g['key'], []) if has_page(gr)]
    if not grps:
        grps = ['uncategorized']
    index.append({
        'id': r['id'], 'title': g['title'], 'sys': g['system'],
        'sysname': systems[g['system']]['name'], 'cat': cat_label(r),
        'groups': grps,
        'authors': [a['user'] for a in r['authors']],
        'metric': run_metric_defs(r)[0]['label'],
        'result': primary_metric_html(r),
        'stars': nlikes(r),
        # follows the same date logic used in author's runs / leaderboard (board_date)
        'date': board_date(r), 'state': 'verified' if state == 'imported' else state,
    })
sys_opts = [(k, v['name']) for k, v in sorted(systems.items())]
regular_groups = sorted([gr for gr in live_groups if not gr.get('synthetic')],
                        key=lambda gr: gr['title'].lower())
group_opts = [(gr['key'], gr['title']) for gr in regular_groups]
if any(gr.get('synthetic') for gr in live_groups):
    group_opts.append(('uncategorized', 'Uncategorized'))
body = tpl('browse.html', index=index, sys_opts=sys_opts, group_opts=group_opts)
(OUT / 'browse').mkdir(exist_ok=True)
(OUT / 'browse' / 'index.html').write_text(page(
    'All TAS runs', body, '../', '', 'Runs',
    seo={'path': 'browse/',
         'description': ('Every tool-assisted speedrun archived on toolAssisted.run, searchable '
                         'by game, category and author, with verification state and stars.')}), encoding='utf-8')
