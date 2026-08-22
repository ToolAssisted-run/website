"""View: create-game and create-category (render on import; see views/__init__).

Creation is everybody's; curation is the experts'. Both pages are reached
from the submit form's "not there? Create it" buttons (opened in a new tab
so a half-written submission survives), from /games/ and from every game
page. Skipping the metrics editor yields the classic category: real time,
lower is better.
"""
from config import OUT, ARCHIVIST
from model import games, systems
from render import page, tpl

sys_list = sorted(systems.items(), key=lambda kv: kv[1]['name'])
game_body = tpl('create_pages_game.html', ARCHIVIST=ARCHIVIST, sys_list=sys_list)

gamedata = {key: f'{systems[g["system"]]["name"]} · {g["title"]}'
            for key, g in sorted(games.items(),
                                 key=lambda kv: (kv[1]['system'], kv[1]['title']))}
cat_body = tpl('create_pages_category.html', ARCHIVIST=ARCHIVIST, gamedata=gamedata)

crumb = lambda leaf: tpl('create_pages_crumb.html', leaf=leaf).strip()

(OUT / 'create-game').mkdir(exist_ok=True)
(OUT / 'create-game' / 'index.html').write_text(
    page('Create a game', game_body, '../', crumb('Create a game'),
         'Games', seo={'path': 'create-game/', 'noindex': True}))
(OUT / 'create-category').mkdir(exist_ok=True)
(OUT / 'create-category' / 'index.html').write_text(
    page('Create a category', cat_body, '../', crumb('Create a category'), 'Games',
         seo={'path': 'create-category/', 'noindex': True}))
