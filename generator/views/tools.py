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
    ('DSDA-Doom / PrBoom+', 'Doom engine', '.lmp'),
    ('openMSX', 'MSX', '.omr'),
    ('MAME-rr', 'Arcade', '.mar'),
    ('FBNeo / FB Alpha', 'Arcade', '.fbm'),
    ('Celeste Studio', 'Celeste', '.tas'),
    ('gz / practice macros', 'Nintendo 64 (Ocarina of Time)', '.gzm'),
    ('specialty formats', 'PC and console-verification tooling', '.ctas, .dft, .3ct'),
]
emu_rows = ''.join(f'<tr><td><b>{esc(n)}</b></td><td>{esc(s)}</td>'
                   f'<td class="num"><code>{esc(x)}</code></td></tr>'
                   for n, s, x in EMULATORS)
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
<section><h2>Other supported movie formats</h2>
<p class="rules fullw">Submissions accept the movie files of these emulators; upload the format
your emulator records. If your tool is missing, tell us on the forum; adding a format is easy.</p>
<table><thead><tr><th>Emulator</th><th>Systems</th><th class="num">Movie format</th></tr></thead>
<tbody>{emu_rows}</tbody></table>
<div class="resourcebox"><b>New to one of these emulators?</b>
TASVideos maintains the definitive guides to all of them: setup, recording, rerecording
workflow, and the quirks of every system:
<a href="https://tasvideos.org/EmulatorResources">the TASVideos Emulator Resources</a>.
An excellent place to start.</div></section>'''
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

