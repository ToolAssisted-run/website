"""View: create-game and create-category (render on import; see views/__init__).

Creation is everybody's; curation is the experts'. Both pages are reached
from the submit form's "not there? Create it" buttons (opened in a new tab
so a half-written submission survives), from /games/ and from every game
page. Skipping the metrics editor yields the classic category: real time,
lower is better.
"""
import json
from config import (
    OUT,
    ARCHIVIST,
)
from model import (
    games,
    systems,
)
from render import (
    METRICS_ED,
    esc,
    page,
)

sys_opts = ''.join(f'<option value="{esc(k)}">{esc(v["name"])}</option>'
                   for k, v in sorted(systems.items(), key=lambda kv: kv[1]['name']))

game_body = f'''<header class="ghead"><div><h1>Create a game</h1>
<p class="authline">Anyone can create a game; it is real the moment you press the button.
Experts curate afterwards, and a mistaken creation is deleted on the record.</p></div></header>
<p id="cg-login" hidden><a href="{ARCHIVIST}/login">Log in via the forum</a> to create a game.</p>
<form id="creategameform" class="actform bigform" hidden>
  <label>Game title</label><input name="title" required placeholder="e.g. Solomon's Key">
  <label>System</label><select name="system">{sys_opts}</select>
  <h2>Its first category</h2>
  <p class="rules">Every game is born with one category. Leave the defaults for the classic
  fastest-completion board, or define your own.</p>
  <label>Category label</label><input name="cat_label" placeholder="fastest completion">
  <label>Rules</label><input name="cat_rule" placeholder="Complete the game as fast as possible.">
  {METRICS_ED}
  <button class="btn" id="cg-submit">Create the game</button>
</form>
<p id="cg-msg" class="actmsg" hidden></p>'''

gamedata = {key: f'{systems[g["system"]]["name"]} · {g["title"]}'
            for key, g in sorted(games.items(),
                                 key=lambda kv: (kv[1]['system'], kv[1]['title']))}
cat_body = f'''<header class="ghead"><div><h1>Create a category</h1>
<p class="authline">Anyone can create a category; it is real the moment you press the button.
Experts refine the wording and manage the metrics afterwards.</p></div></header>
<script type="application/json" id="ccgamedata">{json.dumps(gamedata).replace('<', chr(92) + 'u003c')}</script>
<p id="cc-login" hidden><a href="{ARCHIVIST}/login">Log in via the forum</a> to create a category.</p>
<p class="statline" id="cc-nogame" hidden>No game picked. Come here from a game page or the
submit form; a category always belongs to a game.</p>
<form id="createcatform" class="actform bigform" hidden>
  <p class="statline">A new category in <b id="cc-gamename"></b></p>
  <input type="hidden" name="game" id="cc-game">
  <label>Category label</label><input name="label" required placeholder="e.g. 100k points">
  <label>Rules</label><input name="rule" required placeholder="What must a run do to belong here?">
  {METRICS_ED}
  <button class="btn" id="cc-submit">Create the category</button>
</form>
<p id="cc-msg" class="actmsg" hidden></p>'''

(OUT / 'create-game').mkdir(exist_ok=True)
(OUT / 'create-game' / 'index.html').write_text(
    page('Create a game', game_body, '../', '<a href="../games/">Games</a> / Create a game',
         'Games'))
(OUT / 'create-category').mkdir(exist_ok=True)
(OUT / 'create-category' / 'index.html').write_text(
    page('Create a category', cat_body, '../',
         '<a href="../games/">Games</a> / Create a category', 'Games'))
