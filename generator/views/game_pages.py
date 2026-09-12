"""View: game pages (renders on import; see views/__init__)."""
from config import OUT
from model import (
    CLASSIC_METRICS,
    category_edits_by_game,
    covering_experts,
    edits_of,
    games,
    is_ranked,
    is_unclassified,
    metric_value,
    nlikes,
    rank_key,
    systems,
    withdrawn_by_game,
)
from render import SHIPPED_GAME_THUMBS, SITE_URL, breadcrumb_ld, page, thumb_url, tpl

# ---- game pages (leaderboards with category selector) ----
def combo_iter(dims):
    """Every leaderboard the game has, as lists of (dim, option, sub): the
    cartesian combinations of dimension options, an option with
    subcategories standing once per subcategory (sub is None otherwise)."""
    combos = [[]]
    for d in dims:
        leaves = [(o, s) for o in d['options'] for s in (o.get('subcategories') or [None])]
        combos = [c + [(d, o, s)] for c in combos for o, s in leaves]
    return combos

def leaf_key(o, s):
    return o['key'] + ('/' + s['key'] if s else '')

def author_set(r):
    return frozenset(a['user'].lower() for a in r['authors'])

def behind_text(r, best, prim):
    """How far behind the same authors' best this sits, on the primary
    metric: frames against frames when both sides have them and time rules;
    otherwise the metric's own unit."""
    pv, bv = metric_value(r, prim), metric_value(best, prim)
    if (prim['key'] == 'time' and not r.get('videoOnly') and not best.get('videoOnly')
            and r['movie'].get('frames') and best['movie'].get('frames')
            and not r.get('duration') and not best.get('duration')):
        # frames against frames only while frames are what ranks: a stated
        # duration on either side decides the order, so the gap follows it
        return f"+{r['movie']['frames'] - best['movie']['frames']:,}f"
    if pv is None or bv is None:
        return '—'
    behind = (pv - bv) if prim['better'] == 'lower' else (bv - pv)
    if prim['type'] == 'time':
        return f'+{behind:.2f}s'
    return f'{behind:+g}' + (f' {prim["unit"]}' if prim.get('unit') else '')

def combo_section(g, combo):
    """The data of one leaderboard section (a category, or one of its
    subcategories)."""
    allrs = [r for r in g['runs']
             if all(r['category'].get(d['key']) == o['key']
                    and (s is None or r['category'].get('sub') == s['key'])
                    for d, o, s in combo)]
    mdefs = next((o.get('metrics') for _, o, _s in combo if o.get('metrics')),
                 None) or CLASSIC_METRICS
    ranked_all = sorted([r for r in allrs if is_ranked(r)], key=rank_key)
    # one run per author (set) per category: fastest counts, rest is history
    table_runs, history, seen_sets = [], [], set()
    for r in ranked_all:
        aset = author_set(r)
        if aset in seen_sets:
            history.append(r)
        else:
            seen_sets.add(aset)
            table_runs.append(r)
    pend = sorted([r for r in allrs if not is_ranked(r)],
                  key=lambda r: r.get('submitted') or '', reverse=True)
    hist = []
    for r in history:
        best = next(t for t in table_runs if author_set(t) == author_set(r))
        hist.append((r, behind_text(r, best, mdefs[0])))
    return {'ckey': '|'.join(leaf_key(o, s) for _, o, s in combo),
            'label': ' × '.join(o['label'] + (' · ' + s['label'] if s else '') for _, o, s in combo),
            'rules': '\n\n'.join(t for t in [g.get('rules')] + [t2 for _, o, s in combo
                                  for t2 in (o.get('rule'), (s or {}).get('rule'))] if t),
            'allrs': allrs, 'mdefs': mdefs,
            'custom_metrics': mdefs is not CLASSIC_METRICS,
            'table_runs': table_runs, 'pend': pend, 'history': hist}


def render_game_log(g, face, total_likes):
    gdir = OUT / 'games' / g['key'] / 'logs'
    gdir.mkdir(parents=True, exist_ok=True)
    lrel = '../../../../'
    g_edits = sorted(edits_of.get(('game', g['key']), []),
                     key=lambda e: e.get('at') or e.get('date', ''), reverse=True)
    c_edits = sorted(category_edits_by_game.get(g['key'], []),
                     key=lambda e: e.get('at') or e.get('date', ''), reverse=True)
    w_runs = withdrawn_by_game.get(g['key'], [])
    all_game_runs = g['runs'] + w_runs
    g_reports = sorted(
        ((rep, r_) for r_ in all_game_runs for rep in r_.get('reports', [])),
        key=lambda x: x[0].get('at') or x[0].get('date', ''), reverse=True
    )
    g_reports.sort(key=lambda x: x[0].get('status') != 'open')
    g_cases = [(c, r_) for r_ in all_game_runs for c in r_.get('cases', [])]
    g_cases.sort(key=lambda x: x[0].get('date') or x[0].get('opened', ''), reverse=True)
    g_cases.sort(key=lambda x: x[0].get('status') != 'open')
    g_mods = []
    for r_ in all_game_runs:
        for kind, roster in [
            ('reproduction', 'reproductions'),
            ('verification', 'verifications'),
            ('console verification', 'consoleVerifications'),
        ]:
            for a in r_.get(roster, []):
                inv = a.get('invalidated')
                if inv and inv.get('by') != 'case' and inv.get('cause') != 'edit' and inv['by'].lower() != a['user'].lower():
                    g_mods.append((inv.get('at') or inv.get('date', ''), inv['by'], kind, a['user'], inv.get('reason', ''), r_))
    g_mods.sort(key=lambda e: e[0], reverse=True)
    g_withdrawn = [(r_.get('withdrawn'), r_) for r_ in w_runs if r_.get('withdrawn')]
    g_withdrawn.sort(key=lambda x: x[0].get('at') or x[0].get('date', ''), reverse=True)
    g_removals = sorted(g.get('removalRequests', []),
                        key=lambda x: (x.get('status') != 'open', x.get('at') or x.get('date', '')),
                        reverse=True)
    totals = {
        'game_edits': len(g_edits),
        'cat_edits': len(c_edits),
        'edits': len(g_edits) + len(c_edits),
        'reports': len(g_reports),
        'cases': len(g_cases),
        'mods': len(g_mods),
        'withdrawn': len(g_withdrawn),
    }
    body = tpl('game_log.html', g=g, rel=lrel, face=face, total_likes=total_likes,
               g_edits=g_edits, c_edits=c_edits, g_reports=g_reports,
               g_cases=g_cases, g_mods=g_mods, g_withdrawn=g_withdrawn,
               g_removals=g_removals, totals=totals)
    crumb = tpl('game_log_crumb.html', g=g, rel=lrel).strip()
    sysname = systems[g['system']]['name']
    title = f'Log · {g["title"]} ({g["system"].upper()})'
    seo_desc = f'Audit log, revisions, category changes, reports, and moderation history for {g["title"]} on toolAssisted.run.'
    ld = [breadcrumb_ld([('Games', 'games/'), (sysname, f'systems/{g["system"]}/'),
                          (g['title'], f'games/{g["key"]}/'), ('Log', f'games/{g["key"]}/logs/')])]
    (gdir / 'index.html').write_text(
        page(title, body, lrel, crumb, 'Games', wide=True,
             seo={'path': f'games/{g["key"]}/logs/', 'description': seo_desc, 'ld': ld}),
        encoding='utf-8'
    )


for key, g in games.items():
    gd = OUT / 'games' / key
    gd.mkdir(parents=True)
    rel = '../../../'
    dims = g['categories']['dimensions']
    multi = (sum(len(d['options']) for d in dims) > len(dims)
             or any(o.get('subcategories') for d in dims for o in d['options']))
    combos = [combo_section(g, combo) for combo in combo_iter(dims)]
    # the Unclassified shelf is ordered purely by likes
    uncl_runs = sorted([r for r in g['runs'] if is_unclassified(r)],
                       key=lambda r: (-nlikes(r), r.get('submitted') or ''))
    # a game can exist before any run does (an expert filling out a group
    # creates one with an empty goal list), and a dimension with no options
    # has no default to offer
    default_combo = (next((c['ckey'] for c in combos if c['allrs']), None)
                     or (combos[0]['ckey'] if combos else ''))
    gameact_data = {'game': g['key'], 'experts': covering_experts(g['key']),
                    'editorZone': True}
    face = SHIPPED_GAME_THUMBS.get(g['key'])
    total_likes = sum(nlikes(r) for r in g['runs'])
    body = tpl('game_pages.html', g=g, rel=rel, dims=dims, multi=multi, combos=combos,
               uncl_runs=uncl_runs, default_combo=default_combo, face=face,
               total_likes=total_likes, gameact_data=gameact_data)
    crumb = tpl('game_pages_crumb.html', g=g, rel=rel, edit=False)
    sysname = systems[g['system']]['name']
    ncat = sum(len(d['options']) for d in dims)
    best = max((r for r in g['runs'] if thumb_url(r)),
               key=lambda r: (nlikes(r), r.get('submitted') or ''), default=None)
    (gd / 'index.html').write_text(page(
        f'{g["title"]} ({g["system"].upper()}) TAS runs and leaderboard', body, rel, crumb, 'Games',
        seo={'path': f'games/{g["key"]}/',
             'description': (f'All tool-assisted speedruns of {g["title"]} ({sysname}): '
                             f'{len(g["runs"])} run{"s" if len(g["runs"]) != 1 else ""} across '
                             f'{ncat} categor{"ies" if ncat != 1 else "y"}, with leaderboards, '
                             f'encodes and movie files.'),
             'image': (SITE_URL + thumb_url(best)) if best else None,
             'ld': [breadcrumb_ld([('Games', 'games/'), (sysname, f'systems/{g["system"]}/'),
                                   (g['title'], f'games/{g["key"]}/')])]},
         scripts=['page-library.js']), encoding='utf-8')

    # ---- the game editor (templates/game_pages_edit.html) ----
    opt_data = []
    for d_ in dims:
        for o in d_['options']:
            in_opt = [r_ for r_ in g['runs'] if (r_.get('category') or {}).get('goal') == o['key']]
            opt_data.append({
                'key': o['key'], 'label': o['label'], 'rule': o.get('rule', ''),
                'metrics': o.get('metrics'),
                'runs': len(in_opt),
                'subSelector': o.get('subSelector', 'buttons'),
                'subcategories': [{'key': s['key'], 'label': s['label'], 'rule': s.get('rule', ''),
                                   'runs': sum(1 for r_ in in_opt if r_['category'].get('sub') == s['key'])}
                                  for s in o.get('subcategories', [])]})
    goal_dim = next((d_ for d_ in dims if d_['key'] == 'goal'), dims[0] if dims else {})
    edit_data = {'game': g['key'], 'title': g['title'],
                 'rules': g.get('rules', ''),
                 'selector': goal_dim.get('selector', 'buttons'),
                 'experts': covering_experts(g['key']),
                 'options': opt_data}
    erel = rel + '../'
    ebody = tpl('game_pages_edit.html', g=g, face=face, edit_data=edit_data)
    (gd / 'edit').mkdir(exist_ok=True)
    (gd / 'edit' / 'index.html').write_text(page(
        f"Edit {g['title']}", ebody, erel,
        tpl('game_pages_crumb.html', g=g, rel=erel, edit=True),
        'Games', seo={'path': f'games/{g["key"]}/edit/', 'noindex': True},
        scripts=['page-game-edit.js']), encoding='utf-8')

    render_game_log(g, face, total_likes)

