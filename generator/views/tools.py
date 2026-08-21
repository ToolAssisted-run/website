"""View: tools (renders on import; see views/__init__)."""
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
from config import (
    FORUM,
    OUT,
)
from render import (
    esc,
    page,
)

# ---- tools page ----
EMULATORS = [
    ('BizHawk', 'multi-system (NES, SNES, Genesis, GB/GBA, N64, PSX, Saturn, …)', '.bk2, .tasproj'),
    ('FCEUX', 'NES / Famicom', '.fm2, .fm3'),
    ('lsnes', 'SNES', '.lsmv'),
    ('Gens-rr', 'Genesis / Mega Drive', '.gmv'),
    ('VBA-rr', 'Game Boy / Color / Advance', '.vbm'),
    ('Gambatte', 'Game Boy / Color', '.gbmv'),
    ('DeSmuME', 'Nintendo DS', '.dsm'),
    ('Mupen64-rr', 'Nintendo 64', '.m64'),
    ('Dolphin', 'GameCube / Wii', '.dtm'),
    ('libTAS', 'Linux (native games and emulators)', '.ltm'),
    ('Hourglass', 'Windows', '.wtf'),
    ('JPC-rr', 'DOS', '.jrsr'),
    ('Citra', 'Nintendo 3DS', '.ctm'),
    ('PCSX2-rr', 'PlayStation 2', '.p2m2'),
    ('openMSX', 'MSX', '.omr'),
    ('MAME-rr', 'Arcade', '.mar'),
    ('FBNeo / FB Alpha', 'Arcade', '.fbm'),
]
emu_rows = ''.join(f'<tr><td><b>{esc(n)}</b></td><td>{esc(s)}</td>'
                   f'<td class="num"><code>{esc(x)}</code></td></tr>'
                   for n, s, x in EMULATORS)

# (tool, link, game/engine, movie format, parsed mechanically at submission)
# surveyed from the community's collections; ordered by game
GAME_TOOLS = [
    ('hatTAS', 'https://github.com/doesthisusername/hat-tas',
     'A Hat in Time', 'input scripts', False),
    ('BallanceModLoader (built-in TAS)', 'https://github.com/Gamepiaynmo/BallanceModLoader',
     'Ballance', 'mod recordings', False),
    ('Greasemonkey TAS script', 'https://pastebin.com/d0RHZHn2',
     'Candy Box 2', 'browser script', False),
    ('CelesteTAS + Celeste Studio',
     'https://github.com/EverestAPI/CelesteTAS-EverestInterop',
     'Celeste', '.tas', True),
    ('UniversalClassicTas / ClassicTAS',
     'https://github.com/CelesteClassic/UniversalClassicTas',
     'Celeste Classic (PICO-8)', 'input scripts', False),
    ('DarkSouls-TAS', 'https://github.com/DavidCEllis/DarkSouls-TAS',
     'Dark Souls', 'input scripts', False),
    ('DSDA-Doom / PrBoom+', 'https://github.com/kraflab/dsda-doom',
     'Doom engine (Doom, Doom II, Heretic, Hexen)', '.lmp', True),
    ('Dustmod', 'https://dustmod.com', 'Dustforce', '.dft', True),
    ('factorio-tas-playback', 'https://github.com/Bilka2/factorio-tas-playback',
     'Factorio', 'run scripts', False),
    ('TAS-Helper-for-Factorio', 'https://github.com/MortenTobiasNielsen/TAS-Helper-for-Factorio',
     'Factorio', 'run scripts', False),
    ('Factorio-AnyPct-TAS', 'https://github.com/gotyoke/Factorio-AnyPct-TAS',
     'Factorio', 'run scripts', False),
    ('FireBoyWaterGirlTAS', 'https://github.com/pixelchai/FireBoyWaterGirlTAS',
     'Fireboy & Watergirl', 'input scripts', False),
    ('SoulsTAS', 'https://github.com/Vinjul1704/SoulsTAS',
     'FromSoftware games (Dark Souls III, Sekiro, Elden Ring)', 'TAS scripts', False),
    ('OpenGMK / GM8emulator', 'https://github.com/OpenGMK/OpenGMK',
     'GameMaker 8 games', 'recordings', False),
    ('ReplayBot', 'https://github.com/matcool/ReplayBot',
     'Geometry Dash', 'bot macros', False),
    ('Bunnymod XT', 'https://github.com/YaLTeR/BunnymodXT',
     'Half-Life and other GoldSrc games', '.hltas', False),
    ('Iji TAS mod', 'https://github.com/Kataiser/Iji-TAS-mod',
     'Iji', 'mod recordings', False),
    ('jazz2tas', 'https://github.com/BinaryBlob92/jazz2tas',
     'Jazz Jackrabbit 2', 'input scripts', False),
    ('JumpKingTAS', 'https://github.com/ShootMe/JumpKingTAS',
     'Jump King', 'input scripts', False),
    ('KalimbaTAS', 'https://github.com/ShootMe/KalimbaTAS',
     'Kalimba', 'input scripts', False),
    ('LaMulanaTAS', 'https://github.com/worsety/LaMulanaTAS',
     'La-Mulana (remake)', 'input scripts', False),
    ('Left4TAS', 'https://github.com/sw1ft747/Left4TAS',
     'Left 4 Dead 1 & 2', 'TAS scripts', False),
    ('LoTAS', 'https://www.curseforge.com/minecraft/mc-mods/lotas',
     'Minecraft (Java Edition)', 'tick and savestate tools', False),
    ('TASmod', 'https://github.com/MinecraftTAS/TASmod',
     'Minecraft (Java Edition)', 'recording files', False),
    ('OriDETAS', 'https://github.com/ShootMe/OriDETAS',
     'Ori and the Blind Forest (Definitive Edition)', 'input scripts', False),
    ('OTS TAS Tool', 'https://github.com/thisishowmymindworks/ots-tas-tool',
     'Out There Somewhere', 'input scripts', False),
    ('SourceAutoRecord', 'https://sar.portal2.sr/',
     'Portal 2', 'TAS scripts', False),
    ('TASQuake', 'https://github.com/lipsanen/TASQuake',
     'Quake', 'TAS scripts', False),
    ('racket science', 'https://github.com/doesthisusername/racket-science',
     'Ratchet & Clank', 'input scripts', False),
    ('Refunct TAS Tool', 'https://github.com/oberien/refunct-tas',
     'Refunct', 'Lua scripts', False),
    ('naezith_tas', 'https://github.com/negative-seven/naezith_tas',
     'Remnants of Naezith', 'input scripts', False),
    ('SmolTAS', 'https://github.com/Sh1r0Yaksha/SmolTAS',
     'Smol Ame', 'input scripts', False),
    ('SourcePauseTool (SPT)', 'https://github.com/YaLTeR/SourcePauseTool',
     'Source engine (Half-Life 2, Portal)', 'afterframes scripts', False),
    ('SplasherTAS', 'https://github.com/ShootMe/SplasherTAS',
     'Splasher', 'input scripts', False),
    ('wafel', 'https://github.com/branpk/wafel',
     'Super Mario 64', 'savestate tooling', False),
    ('TAS Plugin', 'https://jump.tf/forum/index.php/topic,1350.0.html',
     'Team Fortress 2', 'plugin recordings', False),
    ('TeslagradTAS', 'https://github.com/ShootMe/TeslagradTAS',
     'Teslagrad', 'input scripts', False),
    ('TinertiaTAS', 'https://github.com/ShootMe/TinertiaTAS',
     'Tinertia', 'input scripts', False),
    ('TMInterface', 'https://donadigo.com/tminterface',
     'TrackMania Nations / United Forever', 'input scripts', False),
    ('UniTAS', 'https://github.com/eddio0141/UniTAS',
     'Unity games (generic; in development)', 'movie scripts', False),
    ('gz / practice macros', 'https://github.com/glankk/gz',
     "Zelda: Ocarina of Time and Majora's Mask (N64)", '.gzm', True),
]
gt_rows = ''.join(
    f'<tr><td><b><a href="{esc(u)}">{esc(n)}</a></b></td><td>{esc(g)}</td>'
    f'<td><code>{esc(x)}</code></td>'
    f'<td class="num">{"<span class=tick-yes>✓</span>" if p else "<span class=tick-no>—</span>"}</td></tr>'
    for n, u, g, x, p in GAME_TOOLS)
body = f'''<header class="ghead"><div><h1>Tools</h1>
<p class="authline">You can create your run with any emulator and any tools you like; what you
submit, in the end, is simply the encoded video. Below are the tools commonly used to make TASes.</p>
</div></header>
<section><h2>miniHawk <span class="chip pendchip">in development</span></h2>
<div class="policy"><p><b>miniHawk</b> is this site\'s endorsed emulator, a re-implementation
of <a href="https://github.com/TASEmulators/BizHawk">BizHawk</a> built around three ideas:
<b>modular emulation cores</b>, <b>performance</b>, and a <b>higher standard of
reproducibility</b>: every core runs waterboxed, and the movie format carries extended
build environment metadata.
<a href="https://github.com/ToolAssisted-run/miniHawk">Follow it on GitHub</a>.</p></div></section>
<section><h2>Botting &amp; authoring tools</h2>
<div class="cols3">
<div class="factbox"><h4>jaffarPlus</h4><p class="statline">A high-performance botting
engine for routing and solving games by massive state-space search, behind many of the
runs in this archive. <a href="https://github.com/SergioMartin86/jaffarPlus">Source on
GitHub</a>.</p></div>
<div class="factbox"><h4>AdvancedBot</h4><p class="statline">A botting external tool for
BizHawk, searching inputs from inside the emulator itself, by toca.
<a href="https://github.com/toca-1/advancedbot-bizhawk">Source on GitHub</a>.</p></div>
</div></section>
<section><h2>Emulator-based TAS tools</h2>
<p class="rules fullw">These are emulators capable of running many different games. You can
attach their movie files to your submission as supplementary data beside your run video; for
most of these formats we extract the relevant information (time, frames, rerecords) directly
from the file.</p>
<table><thead><tr><th>Emulator</th><th>Systems</th><th class="num">Movie format</th></tr></thead>
<tbody>{emu_rows}</tbody></table></section>
<section><h2>Game-specific TAS tools</h2>
<p class="rules fullw">These tools create tool-assisted runs inside one game or engine, with no
emulator involved. You can attach their input or replay files to your submission as
supplementary data beside your run video; for some of these formats we extract the relevant
information (time, frames, rerecords) directly from the file, marked below.</p>
<table><thead><tr><th>Tool</th><th>Game / engine</th><th>Movie format</th>
<th class="num">Parsed</th></tr></thead>
<tbody>{gt_rows}</tbody></table>
<div class="resourcebox"><b>New to one of these emulators?</b>
Ask on <a href="{FORUM}">our forum</a> or on
<a href="https://discord.gg/VsKDT9XB6u">our Discord server</a>; somebody who uses it
will point you the right way.</div></section>'''
(OUT / 'tools').mkdir(exist_ok=True)
(OUT / 'tools' / 'index.html').write_text(page('Tools', body, '../', '', 'Tools'))

# ---- formatting guide ----
FMT_EXAMPLES = [
    ('!! Big heading', 'Section heading'),
    ('! Small heading', 'Sub-heading'),
    ('* first item&#10;* second item', 'Bulleted list (one * per line)'),
    ('----', 'Horizontal rule (four dashes on their own line)'),
    ('[https://example.com]', 'Bare link'),
    ('[https://example.com|read this]', 'Labelled link'),
    ('[module:youtube|v=JLVLBFjWiG8]', 'YouTube embed (alone on its own line)'),
    ('[M100001]', 'Reference a run on this site: renders game, category, time and authors, with a hover thumbnail'),
    ('[6012M]', 'TASVideos-style movie reference: resolves here when imported, links to tasvideos.org otherwise'),
    ('[user:eien86]', 'Reference a member or author: links their profile'),
    ('%%SRC_EMBED&#10;code here&#10;%%END_EMBED', 'Code box (verbatim, no formatting inside)'),
    ('%%QUOTE name&#10;quoted text&#10;%%QUOTE_END', 'Quotation block with optional attribution'),
    ('__bold__ and &#39;&#39;italic&#39;&#39;', 'Bold and italic'),
    ('# first&#10;# second', 'Numbered list'),
    ('| cell | cell | (and || header || cells)', 'Tables, one row per line'),
    ('%%%', 'Forced line break'),
]
fmt_rows = ''.join(
    f'<tr><td><code>{esc(src.replace("&#10;", chr(10)))}</code></td><td>{esc(what)}</td></tr>'
    for src, what in FMT_EXAMPLES)
body = f'''<header class="ghead"><div><h1>Formatting guide</h1>
<p class="authline">The markup accepted in run notes, on submission and on later edits.
Plain paragraphs need no markup at all; blank lines separate them.</p></div></header>
<table><thead><tr><th>You write</th><th>You get</th></tr></thead>
<tbody>{fmt_rows}</tbody></table>
<section><h2>Notes</h2><div class="policy"><ul>
<li>Anything not listed above renders as plain text; nothing breaks, it just is not special.</li>
<li>This dialect is compatible with the notes of imported runs, so imported write-ups render as intended.</li>
<li>YouTube embeds must sit alone on their own line to become a player; inline they render as a link.</li>
</ul></div></section>'''
(OUT / 'formatting').mkdir(exist_ok=True)
(OUT / 'formatting' / 'index.html').write_text(page('Formatting guide', body, '../'))

