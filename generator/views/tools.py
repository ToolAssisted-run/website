"""View: tools (renders on import; see views/__init__)."""
from config import OUT
from render import page, tpl

from model import emulators as emu_catalog, systems as sys_catalog

# ---- tools page ----
emulators = []
historical = []
game_tools = []

tools_catalog = emu_catalog.get('catalog', emu_catalog.get('presets', []))
systems_map = emu_catalog.get('systems', {})

# Precompute mapping from tool_id to list of systems
tool_to_systems = {}
for skey, sdata in systems_map.items():
    if skey == 'default':
        continue
    for t in sdata.get('tools', []):
        tid = t.get('id') if isinstance(t, dict) else str(t)
        tool_to_systems.setdefault(tid, []).append(skey)

for p in tools_catalog:
    kind = p.get('kind', 'emulator')
    pid = p.get('id')
    sys_disp = p.get('systems_display')
    if not sys_disp:
        if p.get('multi'):
            sys_disp = 'multi-system'
        else:
            mapped = tool_to_systems.get(pid, p.get('systems', []))
            names = [sys_catalog.get(s, {}).get('name', s.upper()) for s in mapped]
            sys_disp = ', '.join(names) if names else '—'

    if kind == 'emulator':
        emulators.append(dict(
            name=p['name'],
            url=p.get('url', ''),
            systems=sys_disp,
            formats=p.get('formats') or p.get('format', ''),
            parsed=p.get('parsed', True),
        ))
    elif kind == 'legacy':
        historical.append(dict(
            name=p['name'],
            url=p.get('url', ''),
            systems=sys_disp,
            formats=p.get('formats') or p.get('format', ''),
            parsed=p.get('parsed', True),
        ))
    elif kind == 'game_tool':
        game_tools.append(dict(
            name=p['name'],
            url=p.get('url', ''),
            game=p.get('game', ''),
            format=p.get('formats') or p.get('format', ''),
            parsed=p.get('parsed', False),
        ))

# parsed formats first, then the rest
game_tools.sort(key=lambda t: not t['parsed'])
body = tpl('tools.html', emulators=emulators, historical=historical, game_tools=game_tools)
(OUT / 'tools').mkdir(exist_ok=True)
(OUT / 'tools' / 'index.html').write_text(page(
    'TAS tools: emulators and game-specific tooling', body, '../', '', 'Tools',
    seo={'path': 'tools/',
         'description': ('Emulators with rerecording and movie formats, plus game-specific '
                         'tool-assisted speedrun tools, each linked to its home.')}), encoding='utf-8')

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
    seo={'path': 'formatting/', 'description': 'The markup accepted in run notes on toolAssisted.run.'}), encoding='utf-8')

