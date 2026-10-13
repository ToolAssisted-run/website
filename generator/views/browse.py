"""View: browse (renders on import; see views/__init__)."""
from config import OUT
from model import (
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
    tool_family,
)
from render import page, primary_metric_html, tpl

# ---- browse page (client-side search / facets / sort) ----
index = []
tool_names, tool_counts = {}, {}
for r in sorted(runs, key=lambda r: (board_date(r), r.get('submitted') or ''), reverse=True):
    g = r['_game']
    rs, vs = eff_state(r)
    state = ('imported' if rs == 'imported' else
             'unclassified' if is_unclassified(r) else
             'verified' if is_ranked(r) else 'pending')
    grps = [gr['key'] for gr in groups_by_game.get(g['key'], []) if has_page(gr)]
    if not grps:
        grps = ['uncategorized']
    tool_key, tool_name = tool_family(r)
    tool_key = tool_key or 'none'
    tool_names.setdefault(tool_key, tool_name or 'Not stated')
    tool_counts[tool_key] = tool_counts.get(tool_key, 0) + 1
    index.append({
        'id': r['id'], 'title': g['title'], 'sys': g['system'],
        'sysname': systems[g['system']]['name'], 'cat': cat_label(r),
        'groups': grps,
        'tool': tool_key,
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
# every tool family a run was made with, by name; the runs that state none last
tool_opts = sorted(((k, f'{n} ({tool_counts[k]})') for k, n in tool_names.items() if k != 'none'),
                   key=lambda opt: opt[1].lower())
if 'none' in tool_names:
    tool_opts.append(('none', f'Not stated ({tool_counts["none"]})'))
body = tpl('browse.html', index=index, sys_opts=sys_opts, group_opts=group_opts, tool_opts=tool_opts)
(OUT / 'browse').mkdir(exist_ok=True)
(OUT / 'browse' / 'index.html').write_text(page(
    'All TAS runs', body, '../', '', 'Runs',
    seo={'path': 'browse/',
         'description': ('Every tool-assisted speedrun archived on toolAssisted.run, searchable '
                         'by game, category and author, with verification state and stars.')}), encoding='utf-8')
