"""View: browse (renders on import; see views/__init__)."""
from config import OUT
from model import (
    archived_at,
    cat_label,
    eff_state,
    is_ranked,
    is_unclassified,
    nlikes,
    run_metric_defs,
    runs,
    systems,
)
from render import page, primary_metric_html, tpl

# ---- browse page (client-side search / facets / sort) ----
index = []
for r in sorted(runs, key=archived_at, reverse=True):
    g = r['_game']
    rs, vs = eff_state(r)
    state = ('imported' if rs == 'imported' else
             'unclassified' if is_unclassified(r) else
             'verified' if is_ranked(r) else 'pending')
    index.append({
        'id': r['id'], 'title': g['title'], 'sys': g['system'],
        'sysname': systems[g['system']]['name'], 'cat': cat_label(r),
        'authors': [a['user'] for a in r['authors']],
        'metric': run_metric_defs(r)[0]['label'],
        'result': primary_metric_html(r),
        'stars': nlikes(r),
        # an import is simply verified here (the run page says where)
        'date': archived_at(r)[:10], 'state': 'verified' if state == 'imported' else state,
    })
sys_opts = [(k, v['name']) for k, v in sorted(systems.items())]
body = tpl('browse.html', index=index, sys_opts=sys_opts)
(OUT / 'browse').mkdir(exist_ok=True)
(OUT / 'browse' / 'index.html').write_text(page(
    'All TAS runs', body, '../', '', 'Runs',
    seo={'path': 'browse/',
         'description': ('Every tool-assisted speedrun archived on toolAssisted.run, searchable '
                         'by game, category and author, with verification state and stars.')}), encoding='utf-8')
