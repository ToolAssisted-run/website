"""View: tools (renders on import; see views/__init__)."""
from config import OUT
from render import page, tpl

# ---- tools page ----
EMULATORS = [
    ('Chimera', 'https://github.com/ToolAssisted-run/chimera',
     'multi-system (NES, SNES, GameCube, Wii, Genesis, Dreamcast, PS2, PSP, Xbox, 3DO, '
     'Atari 2600, DOS, …)',
     '.chimeraProject'),
    ('BizHawk', 'https://github.com/TASEmulators/BizHawk',
     'multi-system (NES, SNES, Genesis, GB/GBA, N64, PSX, Saturn, …)', '.bk2, .tasproj'),
    ('FCEUX', 'https://github.com/TASEmulators/fceux', 'NES / Famicom', '.fm2, .fm3'),
    ('lsnes', 'https://repo.or.cz/lsnes.git', 'SNES', '.lsmv'),
    ('Gens-rr', 'https://github.com/TASEmulators/gens-rerecording',
     'Genesis / Mega Drive', '.gmv'),
    ('VBA-rr', 'https://github.com/TASEmulators/vba-rerecording',
     'Game Boy / Color / Advance', '.vbm'),
    ('Gambatte', 'https://github.com/sinamas/gambatte', 'Game Boy / Color', '.gbmv'),
    ('DeSmuME', 'https://github.com/TASEmulators/desmume', 'Nintendo DS', '.dsm'),
    ('Mupen64-rr', 'https://github.com/mkdasher/mupen64-rr-lua-', 'Nintendo 64', '.m64'),
    ('Dolphin', 'https://github.com/dolphin-emu/dolphin', 'GameCube / Wii', '.dtm'),
    ('libTAS', 'https://github.com/clementgallet/libTAS',
     'Linux (native games and emulators)', '.ltm'),
    ('Hourglass', 'https://github.com/Hourglass-Resurrection/Hourglass-Resurrection',
     'Windows', '.wtf'),
    ('JPC-rr', 'https://repo.or.cz/jpcrr.git', 'DOS', '.jrsr'),
    ('Citra', 'https://github.com/citra-mirror/citra', 'Nintendo 3DS', '.ctm'),
    ('PCSX2-rr', 'https://github.com/pcsx2/pcsx2', 'PlayStation 2', '.p2m2'),
    ('openMSX', 'https://github.com/openMSX/openMSX', 'MSX', '.omr'),
    ('MAME-rr', 'https://github.com/TASEmulators/mame-rr', 'Arcade', '.mar'),
    ('FBNeo / FB Alpha', 'https://github.com/finalburnneo/FBNeo', 'Arcade', '.fbm'),
]
emulators = [dict(name=n, url=u, systems=sy, formats=x) for n, u, sy, x in EMULATORS]

# The classic rerecording emulators: retired tools whose movies the archive
# still reads in full (frames, time, rerecords), so a historical work arrives
# with its record intact. Every one of these parsers was written from the
# TASVideos format specification and validated against real publications.
HISTORICAL = [
    ('Snes9x-rr', 'https://tasvideos.org/EmulatorResources/Snes9x', 'SNES', '.smv'),
    ('ZSNES-rr', 'https://tasvideos.org/OtherEmulators/ZMV', 'SNES', '.zmv'),
    ('FCEU 0.98', 'https://tasvideos.org/EmulatorResources/FCEU', 'NES / Famicom', '.fcm'),
    ('Famtasia', 'https://tasvideos.org/EmulatorResources/Famtasia', 'NES / Famicom', '.fmv'),
    ('VirtuaNES', 'https://tasvideos.org/OtherEmulators/VMV', 'NES / Famicom', '.vmv'),
    ('Nintendulator', 'https://tasvideos.org/OtherEmulators/NMV', 'NES / Famicom', '.nmv'),
    ('Dega', 'https://tasvideos.org/EmulatorResources/MMV', 'Master System / Game Gear', '.mmv'),
    ('Mednafen-rr', 'https://tasvideos.org/EmulatorResources/Mednafen',
     'PC Engine, PC-FX, WonderSwan, Neo Geo Pocket, Lynx', '.mcm, .mc2'),
    ('PSXjin', 'https://tasvideos.org/EmulatorResources/PSXjin', 'PlayStation', '.pjm'),
    ('PCSX-rr', 'https://tasvideos.org/EmulatorResources/PCSX', 'PlayStation', '.pxm'),
    ('Yabause-rr', 'https://tasvideos.org/EmulatorResources/Yabause', 'Sega Saturn', '.ymv'),
    ('BizHawk 1.x', 'https://tasvideos.org/Bizhawk/BKMFormat', 'multi-system', '.bkm'),
    ("DOSBox-rr (Bisqwit's patch)", 'https://tasvideos.org/EmulatorResources/DOSBox', 'DOS', '.dof'),
]
historical = [dict(name=n, url=u, systems=sy, formats=x) for n, u, sy, x in HISTORICAL]

# (tool, link, game/engine, movie format, parsed mechanically at submission)
# surveyed from the community's collections; ordered by game
GAME_TOOLS = [
    ('hatTAS', 'https://github.com/doesthisusername/hat-tas',
     'A Hat in Time', '.htas', True),
    ('BallanceModLoader (TASSupport)', 'https://github.com/Gamepiaynmo/BML-Mods',
     'Ballance', '.tas', True),
    ('Greasemonkey TAS script', 'https://pastebin.com/d0RHZHn2',
     'Candy Box 2', 'no movie file (runs are video-only)', False),
    ('CelesteTAS + Celeste Studio',
     'https://github.com/EverestAPI/CelesteTAS-EverestInterop',
     'Celeste', '.tas', True),
    ('UniversalClassicTas / ClassicTAS',
     'https://github.com/CelesteClassic/UniversalClassicTas',
     'Celeste Classic (PICO-8)', '.tas', True),
    ('DarkSouls-TAS', 'https://github.com/DavidCEllis/DarkSouls-TAS',
     'Dark Souls', 'JSON key lists (.txt)', False),
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
     'FromSoftware games (Dark Souls III, Sekiro, Elden Ring)', 'TAS scripts (.txt)', False),
    ('OpenGMK / GM8emulator', 'https://github.com/OpenGMK/OpenGMK',
     'GameMaker 8 games', '.gmtas', True),
    ('ReplayBot', 'https://github.com/matcool/ReplayBot',
     'Geometry Dash', '.replay', True),
    ('Bunnymod XT', 'https://github.com/YaLTeR/BunnymodXT',
     'Half-Life and other GoldSrc games', '.hltas', True),
    ('Iji TAS mod', 'https://github.com/Kataiser/Iji-TAS-mod',
     'Iji', '.itf', True),
    ('jazz2tas', 'https://github.com/BinaryBlob92/jazz2tas',
     'Jazz Jackrabbit 2', 'TAS projects (.xml)', False),
    ('JumpKingTAS', 'https://github.com/ShootMe/JumpKingTAS',
     'Jump King', '.tas', True),
    ('KalimbaTAS', 'https://github.com/ShootMe/KalimbaTAS',
     'Kalimba', '.tas', True),
    ('LaMulanaTAS', 'https://github.com/worsety/LaMulanaTAS',
     'La-Mulana (remake)', 'script.txt', False),
    ('Left4TAS', 'https://github.com/sw1ft747/Left4TAS',
     'Left 4 Dead 1 & 2', 'TAS scripts', False),
    ('LoTAS', 'https://www.curseforge.com/minecraft/mc-mods/lotas',
     'Minecraft (Java Edition)', 'no movie file (runs are video-only)', False),
    ('TASmod', 'https://github.com/MinecraftTAS/TASmod',
     'Minecraft (Java Edition)', '.mctas', True),
    ('OriDETAS', 'https://github.com/ShootMe/OriDETAS',
     'Ori and the Blind Forest (Definitive Edition)', '.tas', True),
    ('OTS TAS Tool', 'https://github.com/thisishowmymindworks/ots-tas-tool',
     'Out There Somewhere', '.otts', True),
    ('SourceAutoRecord', 'https://sar.portal2.sr/',
     'Portal 2', '.p2tas', True),
    ('TASQuake', 'https://github.com/lipsanen/TASQuake',
     'Quake', '.qtas', True),
    ('racket science', 'https://github.com/doesthisusername/racket-science',
     'Ratchet & Clank', 'input scripts', False),
    ('Refunct TAS Tool', 'https://github.com/oberien/refunct-tas',
     'Refunct', 'Lua scripts', False),
    ('naezith_tas', 'https://github.com/negative-seven/naezith_tas',
     'Remnants of Naezith', 'replay text (.ronr in-game)', False),
    ('SmolTAS', 'https://github.com/Sh1r0Yaksha/SmolTAS',
     'Smol Ame', 'per-level key lists (.txt)', False),
    ('SourcePauseTool (SPT)', 'https://github.com/YaLTeR/SourcePauseTool',
     'Source engine (Half-Life 2, Portal)', '.srctas', True),
    ('SplasherTAS', 'https://github.com/ShootMe/SplasherTAS',
     'Splasher', '.tas', True),
    ('wafel', 'https://github.com/branpk/wafel',
     'Super Mario 64', '.m64 (standard Mupen movies)', True),
    ('TAS Plugin', 'https://jump.tf/forum/index.php/topic,1350.0.html',
     'Team Fortress 2', 'plugin recordings', False),
    ('TeslagradTAS', 'https://github.com/ShootMe/TeslagradTAS',
     'Teslagrad', '.tas', True),
    ('TinertiaTAS', 'https://github.com/ShootMe/TinertiaTAS',
     'Tinertia', '.tas', True),
    ('TMInterface', 'https://donadigo.com/tminterface',
     'TrackMania Nations / United Forever', '.inputs', True),
    ('UniTAS', 'https://github.com/eddio0141/UniTAS',
     'Unity games (generic; in development)', 'Lua movie scripts', False),
    ('Elasto Mania (built-in replays)', 'https://elmaonline.net',
     'Elasto Mania', '.rec', True),
    ('gz / practice macros', 'https://github.com/glankk/gz',
     "Zelda: Ocarina of Time and Majora's Mask (N64)", '.gzm', True),
]
# parsed formats first, then the rest in survey order
game_tools = [dict(name=n, url=u, game=g, format=x, parsed=p)
              for n, u, g, x, p in sorted(GAME_TOOLS, key=lambda t: not t[4])]
body = tpl('tools.html', emulators=emulators, historical=historical, game_tools=game_tools)
(OUT / 'tools').mkdir(exist_ok=True)
(OUT / 'tools' / 'index.html').write_text(page(
    'TAS tools: emulators and game-specific tooling', body, '../', '', 'Tools',
    seo={'path': 'tools/',
         'description': ('Emulators with rerecording and movie formats, plus game-specific '
                         'tool-assisted speedrun tools, each linked to its home.')}))

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
    ("__bold__ and ''italic''", 'Bold and italic'),
    ('# first&#10;# second', 'Numbered list'),
    ('| cell | cell | (and || header || cells)', 'Tables, one row per line'),
    ('%%%', 'Forced line break'),
    ('https://example.com', 'Bare URLs link by themselves; put a ! in front to keep one as text'),
    ('[https://example.com/shot.png|w=480|right]', 'Image, embedded (options: w=, h=, alt=, title=, left, right); a bare image URL embeds too'),
    ('** nested item&#10;## nested number', 'Nested lists: one more * or # per level'),
    (';Term: definition', 'Definition list'),
    (' preformatted', 'A line starting with a space is preformatted (monospace, kept as written)'),
    ('((small)) {{teletype}} ---struck---', 'Small text, teletype, strikethrough'),
    ('%%TAB Show inputs%%&#10;long text&#10;%%TAB_END%%', 'Foldable section (TASVideos tabs); an empty first tab makes the next one start closed'),
    ('[#1] in the text, then [1] note at the end', 'Footnotes'),
    ('[1032S] or [Forum/Topics/629|label]', 'TASVideos submissions, forum topics and wiki pages link to tasvideos.org'),
]
# the source column is written with &#10; standing for a newline inside one cell
examples = [dict(src=src.replace('&#10;', chr(10)), what=what) for src, what in FMT_EXAMPLES]
body = tpl('tools_formatting.html', examples=examples)
(OUT / 'formatting').mkdir(exist_ok=True)
(OUT / 'formatting' / 'index.html').write_text(page(
    'Formatting guide', body, '../',
    seo={'path': 'formatting/', 'description': 'The markup accepted in run notes on toolAssisted.run.'}))

