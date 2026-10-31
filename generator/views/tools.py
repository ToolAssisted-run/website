"""View: tools (renders on import; see views/__init__)."""
from config import OUT
from render import page, tpl

from model import chimera_cores, emulators as emu_catalog, systems as sys_catalog, experts_reg

site_experts = sorted({e['user'] for e in experts_reg if e['scope'] == 'site'})

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
            id=pid,
            name=p['name'],
            url=p.get('url', ''),
            systems=sys_disp,
            formats=p.get('formats') or p.get('format', ''),
            parsed=p.get('parsed', True),
        ))
    elif kind == 'legacy':
        historical.append(dict(
            id=pid,
            name=p['name'],
            url=p.get('url', ''),
            systems=sys_disp,
            formats=p.get('formats') or p.get('format', ''),
            parsed=p.get('parsed', True),
        ))
    elif kind == 'game_tool':
        game_tools.append(dict(
            id=pid,
            name=p['name'],
            url=p.get('url', ''),
            game=p.get('game', ''),
            format=p.get('formats') or p.get('format', ''),
            formats=p.get('formats') or p.get('format', ''),
            parsed=p.get('parsed', False),
        ))

# parsed formats first, then the rest
game_tools.sort(key=lambda t: not t['parsed'])
body = tpl('tools.html', emulators=emulators, historical=historical, game_tools=game_tools,
           emulatordata=emu_catalog, site_experts=site_experts, cores=chimera_cores)
(OUT / 'tools').mkdir(exist_ok=True)
(OUT / 'tools' / 'index.html').write_text(page(
    'TAS tools: emulators and game-specific tooling', body, '../', '', 'Tools',
    scripts=['page-core.js', 'page-tools.js'],
    styles=['page-tools.css'],
    seo={'path': 'tools/',
         'description': ('Emulators with rerecording and movie formats, plus game-specific '
                         'tool-assisted speedrun tools, each linked to its home.')}), encoding='utf-8')

# ---- formatting guide ----
FMT_EXAMPLES = [
    ('## Section heading&#10;### Sub-heading', 'Headings (# is the largest, ###### the smallest); {#id} after a heading names its anchor', ''),
    ('**bold**', 'Bold', 'Ctrl/Cmd+B'),
    ('*italic*', 'Italic', 'Ctrl/Cmd+I'),
    ('~~struck~~', 'Strikethrough', ''),
    ('`code`', 'Inline code', ''),
    ('- first item&#10;- second item&#10;  - nested item', 'Bulleted list; indent to nest', ''),
    ('1. first&#10;2. second', 'Numbered list', ''),
    ('[read this](https://example.com)', 'Link with a label', 'Ctrl/Cmd+K; Ctrl/Cmd+V with selected text and an HTTP(S) URL'),
    ('https://example.com or <https://example.com>', 'Bare URLs link by themselves; a backslash before one keeps it as text', ''),
    ('![a shot](https://example.com/shot.png "caption"){width=480 align=right}', 'Image, embedded (options: width, height, align=left or right)', ''),
    ('[M100001]', 'Reference a run on this site: renders game, category, time and authors, with a hover thumbnail', ''),
    ('[user:eien86]', 'Reference a member or author: links their profile', ''),
    ('> quoted text', 'Quotation', ''),
    (':::quote{author="name"}&#10;quoted text&#10;:::', 'Quotation block with an attribution', ''),
    ('```&#10;code here&#10;```', 'Code box (verbatim, no formatting inside)', ''),
    ('| a | b |&#10;| --- | --- |&#10;| 1 | 2 |', 'Tables: a header row, then one row per line', ''),
    ('---', 'Horizontal rule (on its own line, with a blank line before it)', ''),
    ('a line\\&#10;broken here', 'A backslash at the end of a line breaks it', ''),
    ('[small]{.small} [quoted]{.quote} x[2]{.sup} H[2]{.sub}O', 'Small text, inline quote, superscript, subscript', ''),
    ('[9000]{.frames fps=60}', 'A frame count, written out (and timed on hover with the rate)', ''),
    (':::embed{type=youtube v=JLVLBFjWiG8}&#10;:::', 'YouTube embed (a block of its own)', ''),
    ('::::tabs&#10;:::tab{title="Inputs"}&#10;long text&#10;:::&#10;:::tab{title="More" closed}&#10;text&#10;:::&#10;::::', 'Foldable sections; the first starts open unless it says closed', ''),
    (':::toc&#10;:::', 'A table of contents of the notes headings', ''),
    ('Term&#10;: definition', 'Definition list', ''),
    ('Text with a note[^1].&#10;&#10;[^1]: the note', 'Footnotes', ''),
    ('<!-- not shown -->', 'A comment: kept in the notes, never displayed', ''),
]
# the source column is written with &#10; standing for a newline inside one cell
examples = [dict(src=src.replace('&#10;', chr(10)), what=what, hotkey=hotkey)
            for src, what, hotkey in FMT_EXAMPLES]
body = tpl('tools_formatting.html', examples=examples)
(OUT / 'formatting').mkdir(exist_ok=True)
(OUT / 'formatting' / 'index.html').write_text(page(
    'Formatting guide', body, '../',
    seo={'path': 'formatting/', 'description': 'The markup accepted in run notes on toolAssisted.run.'}), encoding='utf-8')
