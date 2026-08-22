"""View: games index, system pages and group pages (renders on import; see
views/__init__). Markup lives in templates/games_*.html and _game_cards.html."""
from config import OUT
from model import (
    authors,
    nvisits,
    cat_label,
    covering_experts,
    experts_reg,
    games,
    group_games,
    group_runs,
    groups_by_game,
    has_page,
    is_ranked,
    live_groups,
    nlikes,
    systems,
)
from render import (
    SITE_URL,
    SHIPPED_GAME_THUMBS,
    breadcrumb_ld,
    page,
    thumb_url,
    tpl,
)


def stars_of(rlist): return sum(nlikes(r) for r in rlist)
def views_of(rlist): return sum(nvisits(r) for r in rlist)
def runs_of(gms): return [r for g in gms for r in g['runs']]
def nsystems(gms): return len({g['system'] for g in gms})

def newest_thumb(g):
    """The game's most recently submitted run that has a thumbnail."""
    return max((r for r in g['runs'] if r.get('thumbnail')),
               key=lambda r: r.get('submitted') or '', default=None)

def best_thumb(rlist):
    """The most liked run with a thumbnail: the face of a page for SEO."""
    return max((r for r in rlist if thumb_url(r)),
               key=lambda r: (nlikes(r), r.get('submitted') or ''), default=None)

def collage_tiles(ggames):
    """Up to four games, one tile each, each showing that game's most
    starred run: the face of a family (a group) or a library (a system),
    drawn from distinct games, best liked first."""
    tiles = []
    for g in sorted(ggames, key=lambda g: (-stars_of(g['runs']), g['title'])):
        best = max((r for r in g['runs'] if r.get('thumbnail')),
                   key=lambda r: (nlikes(r), r.get('submitted') or ''), default=None)
        if best:
            tiles.append(best)
        if len(tiles) == 4:
            break
    return tiles

def ranked_rows(rlist):
    return sorted((r for r in rlist if is_ranked(r)),
                  key=lambda r: (r['_game']['title'], cat_label(r)))

def plural(n, word): return f'{n} {word}{"s" if n != 1 else ""}'

HELPERS = dict(stars_of=stars_of, views_of=views_of, runs_of=runs_of,
               nsystems=nsystems, newest_thumb=newest_thumb,
               collage_tiles=collage_tiles, SHIPPED_GAME_THUMBS=SHIPPED_GAME_THUMBS)

def crumb(name): return tpl('games_crumb.html', title=name).rstrip()

# ---- games index ----
by_sys = {}
for key, g in games.items():
    by_sys.setdefault(g['system'], []).append(g)

# ---- system pages: a system's whole library, exactly like a group page ----
(OUT / 'systems').mkdir(parents=True, exist_ok=True)
for skey in sorted(by_sys):
    sgames = sorted(by_sys[skey], key=lambda g: g['title'].lower())
    sruns = runs_of(sgames)
    sname = systems[skey]['name']
    sbody = tpl('games_system.html', sname=sname, sgames=sgames, sruns=sruns,
                srows=ranked_rows(sruns), **HELPERS)
    sdir = OUT / 'systems' / skey
    sdir.mkdir(parents=True, exist_ok=True)
    sbest = best_thumb(sruns)
    (sdir / 'index.html').write_text(page(
        f'{sname} TAS runs and leaderboards', sbody, '../../', crumb(sname), 'Games',
        seo={'path': f'systems/{skey}/',
             'description': (f'Tool-assisted speedruns on {sname}: '
                             f'{plural(len(sgames), "game")}, '
                             f'{plural(len(sruns), "run")}, leaderboards, '
                             f'encodes and movie files.'),
             'image': (SITE_URL + thumb_url(sbest)) if sbest else None,
             'ld': [breadcrumb_ld([('Games', 'games/'),
                                   (sname, f'systems/{skey}/')])]}))

if live_groups:
    (OUT / 'groups').mkdir(parents=True, exist_ok=True)
    for gr in live_groups:
        ggames = group_games(gr)
        grunts = group_runs(gr)
        gexperts = sorted({u for g in ggames for u in covering_experts(g['key'])})
        # the move form lists every game not already here, with the group
        # each would leave
        placed_in = {k: grx['title'] for grx in live_groups
                     for k in grx.get('games', []) if grx['key'] != gr['key']}
        gact_data = {'group': gr['key'], 'experts': gexperts,
                     'editorZone': True,
                     'movable': [{'key': k, 'title': games[k]['title'],
                                  'group': placed_in.get(k, '')}
                                 for k in sorted(games,
                                                 key=lambda k: games[k]['title'].lower())
                                 if k not in gr.get('games', [])]}
        gbody = tpl('games_group.html', gr=gr, ggames=ggames, grunts=grunts,
                    gexperts=gexperts, synthetic=bool(gr.get('synthetic')),
                    rows=ranked_rows(grunts), gact_data=gact_data, **HELPERS)
        gdir = OUT / 'groups' / gr['key']
        gdir.mkdir(parents=True, exist_ok=True)
        gnsys = nsystems(ggames)
        gbest = best_thumb(grunts)
        (gdir / 'index.html').write_text(page(
            f'{gr["title"]} TAS runs across {plural(gnsys, "system")}',
            gbody, '../../', crumb(gr['title']), 'Games',
            seo={'path': f'groups/{gr["key"]}/',
                 'description': (f'{gr["title"]} tool-assisted speedruns across '
                                 f'{plural(gnsys, "system")}: {plural(len(ggames), "game")}, '
                                 f'{plural(len(grunts), "run")}, leaderboards and records.'),
                 'image': (SITE_URL + thumb_url(gbest)) if gbest else None,
                 'ld': [breadcrumb_ld([('Games', 'games/'),
                                       (gr['title'], f'groups/{gr["key"]}/')])]}))

# the list view: every game alphabetically, with the groups that hold it
list_games = [(g, [gr for gr in groups_by_game.get(g['key'], []) if has_page(gr)])
              for g in sorted(games.values(), key=lambda g: g['title'].lower())]
site_experts_now = sorted({e['user'].lower() for e in experts_reg if e['scope'] == 'site'})
body = tpl('games_index.html', by_sys=by_sys,
           sys_keys=sorted(by_sys, key=lambda k: systems[k]['name']),
           ngroups=sum(1 for gr in live_groups if not gr.get('synthetic')),
           list_games=list_games, site_experts_now=site_experts_now, **HELPERS)
(OUT / 'games' / 'index.html').write_text(page(
    'Games with TAS runs', body, '../', '', 'Games',
    seo={'path': 'games/',
         'description': (f'{len(games)} games across {len(by_sys)} systems with tool-assisted '
                         f'speedruns on toolAssisted.run, by system, by game group, and as a list.'),
         'ld': [breadcrumb_ld([('Games', 'games/')])]}))

# The expert roster is not a page of its own: a role is a property of a
# member, so it shows as a badge on the members list and as a history at the
# bottom of that member's own page. scope_label survives because the role log
# uses it.
