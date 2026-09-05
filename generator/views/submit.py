"""View: submit (renders on import; see views/__init__)."""
import datetime
import providers
from config import ARCHIVE_RAW, ARCHIVIST, OUT
from model import authors, credited, emulators, games, systems
from render import page, tpl

# ---- submit page (session-authenticated, posts to the archivist) ----
# key -> "System · Title" only: categories are fetched from the archive when
# a game is picked, so the page carries no payload that grows with the corpus
gamedata = {key: f'{systems[g["system"]]["name"]} · {g["title"]}'
            for key, g in sorted(games.items(),
                                 key=lambda kv: (kv[1]['system'], kv[1]['title']))}
authornames = sorted({**credited, **{k: a['username'] for k, a in authors.items()}}.values(),
                     key=str.lower)
# the system selector's own list: every system, whether or not it holds a
# game yet, because a machine added a moment ago is exactly the one a
# submitter is here about
systemdata = {k: sy['name'] for k, sy in sorted(systems.items(),
                                                key=lambda kv: kv[1]['name'].lower())}
body = tpl('submit.html', ARCHIVE_RAW=ARCHIVE_RAW, ARCHIVIST=ARCHIVIST, gamedata=gamedata,
           systemdata=systemdata, emulatordata=emulators,
           authornames=authornames, provider_names=' · '.join(providers.names()),
           today=datetime.date.today().isoformat())
(OUT / 'submit').mkdir(exist_ok=True)
(OUT / 'submit' / 'index.html').write_text(page('Submit', body, '../', '', 'Submit',
    seo={'path': 'submit/', 'noindex': True}, scripts=['page-submit.js']), encoding='utf-8')
