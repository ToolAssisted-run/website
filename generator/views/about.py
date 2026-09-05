"""View: about (renders on import; see views/__init__)."""
from config import OUT
from model import games, group_games, groups
from render import page, tpl

# ---- about page ----
# The examples are picked from the archive, never hardcoded: this page is
# built against fixtures too, and a link to a game that only exists in the
# live archive is a dead link everywhere else. Prince of Persia is the one
# we point at while it is here (a series across systems, categories with
# subcategories); otherwise the widest group and its biggest game stand in.
group_example = next((gr for gr in groups if gr['key'] == 'prince-of-persia'), None)
if group_example is None:
    group_example = max(groups, key=lambda gr: len(group_games(gr)), default=None)

game_example = games.get('gc/prince-of-persia-the-two-thrones')
if game_example is None and group_example is not None:
    game_example = max(group_games(group_example), key=lambda g: len(g['runs']),
                       default=None)
if game_example is None:
    game_example = max(games.values(), key=lambda g: len(g['runs']), default=None)

body = tpl('about.html', group_example=group_example, game_example=game_example)
(OUT / 'about').mkdir(exist_ok=True)
(OUT / 'about' / 'index.html').write_text(page(
    'About us', body, '../', '', 'About us',
    seo={'path': 'about/',
         'description': ('toolAssisted.run archives and showcases tool-assisted '
                         'speedruns: who we are, how to submit a run, and how '
                         'this community works.')}), encoding='utf-8')
