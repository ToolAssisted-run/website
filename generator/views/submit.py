"""View: submit (renders on import; see views/__init__)."""
import datetime
import html
import os
import json
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.parse
import providers
from config import (
    ARCHIVE_RAW,
    ARCHIVIST,
    OUT,
)
from model import (
    authors,
    credited,
    games,
    systems,
)
from render import (
    esc,
    page,
)

# ---- submit page (session-authenticated, posts to the archivist) ----
# key -> "System · Title" only: categories are fetched from the archive when
# a game is picked, so the page carries no payload that grows with the corpus
gamedata = {key: f'{systems[g["system"]]["name"]} · {g["title"]}'
            for key, g in sorted(games.items(),
                                 key=lambda kv: (kv[1]['system'], kv[1]['title']))}
sys_opts_submit = ''.join(f'<option value="{esc(k)}">{esc(v["name"])}</option>'
                          for k, v in sorted(systems.items(), key=lambda kv: kv[1]['name']))
body = f'''<header class="ghead"><div><h1>Submit a run</h1>
<p class="authline">Your run is archived instantly and appears immediately, as pending.
Honest attribution of every author is the one hard rule. Never upload ROMs.</p></div></header>
<script type="application/json" id="gamedata">{json.dumps({'raw': ARCHIVE_RAW, 'games': gamedata}).replace('<', chr(92) + 'u003c')}</script>
<script type="application/json" id="authordata">{json.dumps(sorted({**credited, **{k: a['username'] for k, a in authors.items()}}.values(), key=str.lower)).replace('<', chr(92) + 'u003c')}</script>
<p id="s-login" hidden><a href="{ARCHIVIST}/login">Log in via the forum</a> to submit a run.</p>
<form id="submitform" class="actform bigform" hidden>
  <label>Game</label>
  <div class="gamepick" id="s-gamepick">
    <input class="gamesearch" id="s-gamesearch" placeholder="Type to find the game…" autocomplete="off">
    <div class="gamelist" hidden></div>
    <input type="hidden" name="game" id="s-game">
  </div>
  <p class="statline" id="s-gamelocked" hidden>Submitting to <b id="s-gamelockname"></b> ·
  <a href="#" id="s-gameunlock">a different game?</a></p>
  <div id="s-newgame" hidden>
    <p class="rules">Anyone can create a game; it exists immediately as <b>provisional</b>
    until an expert ratifies it. If you are yourself an expert covering it, it is
    established as you create it, with your name on it: authority does not need to
    consult itself.</p>
    <label>System</label><select name="system">{sys_opts_submit}</select>
    <label>Game title</label><input name="new_game_title" placeholder="e.g. Solomon's Key">
  </div>
  <label>Category</label>
  <select id="s-goal" name="goal"></select>
  <div id="s-newgoal" hidden>
    <p class="rules">New categories are provisional too; experts refine the rule wording.</p>
    <label>Category label</label><input id="s-goallabel" name="new_goal_label" placeholder="e.g. fastest completion">
    <label>Rules</label><input name="new_goal_rule" placeholder="e.g. Complete the game as fast as possible.">
  </div>
  <div id="s-uncldesc" hidden>
    <p class="rules">Unclassified: entertainment, experiments, playarounds; no defined goal,
    never verified, ranked purely by ★ likes. Describe what your run does.</p>
    <label>What does this run do? (shown in the ranking)</label>
    <input name="goal_description" maxlength="200" placeholder="e.g. beats the game using only the credits sequence">
  </div>
  <label>Authors (credit every human who worked on it. Type to search, click to add;
  a coauthor who is not a member here is credited by name, as text)</label>
  <div class="authpick">
    <div class="authchips"></div>
    <input class="authsearch" placeholder="Type a username…" autocomplete="off">
    <div class="authlist" hidden></div>
    <input type="hidden" name="authors">
  </div>
  <label>Encode link (required; verification and the run's thumbnail derive from it).
  Accepted: {' · '.join(providers.names())}.</label>
  <input id="s-encode" name="encode" type="url" required placeholder="https://youtu.be/…">
  <div id="enc-check" class="enccheck" hidden><img id="enc-thumb" alt="" hidden>
  <span id="enc-status"></span></div>
  <label>Emulator / core (optional)</label><input name="emulator" placeholder="e.g. BizHawk 2.11 (QuickerNES)">
  <label>ROM used (optional. Pick the file; name and SHA1 are derived from it,
  hashed locally: the ROM <b>never leaves your machine</b>)</label>
  <input type="file" id="s-romfile">
  <p id="s-romnote" class="rules fullw" hidden></p>
  <label>ROM name (derived)</label><input name="rom_name" id="s-romname" readonly tabindex="-1">
  <label>ROM sha1 (derived)</label><input name="rom_sha1" id="s-romsha1" readonly tabindex="-1">
  <label class="cwlab" style="margin:8px 0"><input type="checkbox" id="s-videoonly"
    name="video_only" value="1"> This is a <b>video-only</b> run: no input movie exists.
    It can never be reproduced, in emulator or on console, and the page will say so;
    one verification still ranks it like any other run.</label>
  <div id="s-moviewrap">
  <label>Movie file</label><input name="movie" type="file" required>
  </div>
  <div id="s-timewrap" hidden>
  <label>Run time, stated by you (required for video-only)</label>
  <div class="timepick" id="s-timepick">
    <span class="tseg"><input id="t-h" type="number" inputmode="numeric" min="0" max="999" placeholder="0"><label for="t-h">h</label></span>
    <span class="tseg"><input id="t-m" type="number" inputmode="numeric" min="0" max="59" placeholder="00"><label for="t-m">m</label></span>
    <span class="tseg"><input id="t-s" type="number" inputmode="numeric" min="0" max="59" placeholder="00"><label for="t-s">s</label></span>
    <span class="tseg"><input id="t-ms" type="number" inputmode="numeric" min="0" max="999" placeholder="000"><label for="t-ms">ms</label></span>
  </div>
  <input type="hidden" name="time" id="s-time">
  </div>
  <label>Voluntary content disclosures, to warn viewers about:</label>
  <div class="cwrow">
    <label class="cwlab"><input type="checkbox" name="content_warnings" value="mature-violence"> Mature / violent</label>
    <label class="cwlab"><input type="checkbox" name="content_warnings" value="sexual"> Sexual content (18+ gate)</label>
    <label class="cwlab"><input type="checkbox" name="content_warnings" value="photosensitivity"> Photosensitivity (flashing lights)</label>
    <label class="cwlab"><input type="checkbox" name="content_warnings" value="strong-language"> Strong language</label>
  </div>
  <label>Attachments (optional: text configs, or additional movie files)</label>
  <input name="attachments" type="file" multiple>
  <label>When was the run completed? (optional; a run finished long before it was
  submitted may say so, and the page shows both dates)</label>
  <input name="completed" type="date" max="{datetime.date.today().isoformat()}">
  <label>Notes (your write-up; see the <a href="../formatting/" target="_blank">formatting guide</a>)</label><textarea name="notes" rows="10"></textarea>
  <button type="button" class="btn quiet" id="s-preview-btn">Preview</button>
  <div id="s-preview" hidden><h2>Preview</h2>
  <p class="rules fullw">Approximate; the live page is rendered by the site generator.</p>
  <div class="ghead previewhead"><div><div class="chips" id="pv-chips"></div>
  <h1 id="pv-title"></h1><p class="authline" id="pv-authors"></p></div></div>
  <div class="poster" id="pv-poster" hidden><img id="pv-thumb" alt=""></div>
  <div class="notes" id="pv-notes"></div></div>
  <label class="cwlab consent"><input type="checkbox" name="consent" value="yes" required>
  <span>I license this submission under <b>CC BY 4.0</b>, I have read and agree with the
  <a href="https://github.com/ToolAssisted-run#1-community-principles" target="_blank">Community Principles</a>,
  <a href="https://github.com/ToolAssisted-run#3-terms-of-use" target="_blank">Terms of Use</a>,
  <a href="https://github.com/ToolAssisted-run#4-code-of-conduct" target="_blank">Code of Conduct</a> and
  <a href="https://github.com/ToolAssisted-run#5-privacy-policy" target="_blank">Privacy Policy</a>,
  and I confirm everything here is complete and truthful, especially the authorship.</span></label>
  <button class="btn" id="s-submit">Submit</button>
</form></details></div>
<p id="s-msg" class="actmsg" hidden></p>'''
(OUT / 'submit').mkdir(exist_ok=True)
(OUT / 'submit' / 'index.html').write_text(page('Submit', body, '../', '', 'Submit'))

