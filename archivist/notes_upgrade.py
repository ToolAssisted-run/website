"""The one-way upgrade of a run's notes from the legacy dialect (wikitext.py)
to the current language (markup.py).

Used on the edit page of a run whose notes are still in the legacy dialect:
the author sees the upgraded text, edits it, and saving stores it in the
current language. Nothing is ever upgraded behind anybody's back.

The legacy text is parsed with the legacy renderer's own rules and written
out again as the current language, escaping whatever literal text would
otherwise turn into markup. Things with no exact equivalent are upgraded as
closely as possible and named in `warnings`, so the editor can say so.

    upgrade(text, external_base='https://…/', local_run=lambda n: None)
        -> (markdown, [warning, ...])

`external_base` is where legacy references to other sites (movie and
submission numbers, wiki paths) point; `local_run(number)` answers the id of
the run on this site that stands for such a number, or None."""
import os
import re

import wikitext

LINESEP = os.linesep
line_separator = wikitext.line_separator
normalize_newlines = wikitext.normalize_newlines

# private-use markers carried through the escaping pass
BOLD, BOLD_, EMPH, EMPH_, STRIKE, STRIKE_ = '', '', '', '', '', ''
QUOTE, QUOTE_, SUP, SUP_, SUB, SUB_ = '', '', '', '', '', ''
SMALL, SMALL_, BREAK, FOOTNOTE = '\ue00c', '\ue00d', '\ue010', '\ue011'
MARKERS = ''.join(chr(c) for c in range(0xe000, 0xe020))
EMPHASIS_MARKERS = ''.join(chr(c) for c in range(0xe000, 0xe010))   # all but BREAK and FOOTNOTE
_SPLIT = re.compile(r'(\x00\d+\x00|[' + MARKERS + '])')

_PAIRS = (
    (re.compile(BOLD + r'(.*?)' + BOLD_), '**', '**'),
    (re.compile(EMPH + r'(.*?)' + EMPH_), '*', '*'),
    (re.compile(STRIKE + r'(.*?)' + STRIKE_), '~~', '~~'),
)
_SPANS = (
    (re.compile(QUOTE + r'(.*?)' + QUOTE_), '{.quote}'),
    (re.compile(SUP + r'(.*?)' + SUP_), '{.sup}'),
    (re.compile(SUB + r'(.*?)' + SUB_), '{.sub}'),
    (re.compile(SMALL + r'(.*?)' + SMALL_), '{.small}'),
)

_LINE_START = (
    (re.compile(r'^(\s*)([-+*])(\s|$)'), r'\1\\\2\3'),
    (re.compile(r'^(\s*\d+)([.)])(\s|$)'), r'\1\\\2\3'),
    (re.compile(r'^(\s*)(#{1,6})(\s|$)'), r'\1\\\2\3'),
    (re.compile(r'^(\s*)>'), r'\1\\>'),
    (re.compile(r'^(\s*)(`{3,}|~{3,})'), r'\1\\\2'),
    (re.compile(r'^(\s*)([-=_*])([-=_*\s]*)$'), r'\1\\\2\3'),
    (re.compile(r'^(\s*):{3,}'), lambda m: m.group(0).replace(':', r'\:', 1)),
    (re.compile(r'^(\s*)\|'), r'\1\\|'),
    (re.compile(r'^(\s*)\[\^'), r'\1\\[^'),
)


FORMAT_KEY = 'notesFormat'
CURRENT_FORMAT = 'markdown'


def is_legacy(run):
    """A run's notes are in the legacy dialect until the run says otherwise:
    every run written before the flag existed is one, and so is every import."""
    return run.get(FORMAT_KEY) != CURRENT_FORMAT


class Upgrader:
    def __init__(self, external_base='', local_run=None):
        self.base = external_base.rstrip('/') + '/' if external_base else ''
        self.local_run = local_run or (lambda n: None)
        self.warnings = []
        self.held = []

    def warn(self, message):
        if message not in self.warnings:
            self.warnings.append(message)

    # ------------------------------------------------------------ inline ----

    def hold(self, md):
        self.held.append(md)
        return f'\x00{len(self.held) - 1}\x00'

    def external(self, path):
        """A link to a legacy reference, or None when no base is configured."""
        if not self.base:
            self.warn('Some references to the original site were left as plain text.')
            return None
        return self.base + path.lstrip('/')

    def escape(self, s, cell=False):
        s = s.replace('\\', '\\\\')
        s = re.sub(r'\*(?=\S)|(?<=\S)\*', r'\\*', s)
        s = re.sub(r'(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])', r'\\_', s)
        s = s.replace('`', r'\`')
        s = s.replace('~~', r'\~\~')
        s = re.sub(r'&(?=#?\w+;)', r'\\&', s)
        s = re.sub(r'\{(?=[.#])', r'\\{', s)
        if re.search(r'\]\(|\]\{|\[\^|\]:', s):
            s = s.replace('[', r'\[').replace(']', r'\]')
        if cell:
            s = s.replace('|', r'\|')
        return s

    def dest(self, url):
        return f'<{url}>' if re.search(r'[()\s]', url) else url

    def label(self, text):
        text = text.replace('\\', '\\\\').replace('[', r'\[').replace(']', r'\]')
        return text.replace('*', r'\*').replace('`', r'\`').replace('~~', r'\~\~')

    def image(self, src, opts, link=None):
        alt, title, attrs = '', '', []
        for o in opts:
            lo = o.lower()
            if lo in ('left', 'right'):
                attrs.append(f'align={lo}')
            elif lo.startswith('alt='):
                alt = o[4:]
            elif lo.startswith('title='):
                title = o[6:]
            elif lo.startswith('w=') and o[2:].isdigit():
                attrs.append(f'width={o[2:]}')
            elif lo.startswith('h=') and o[2:].isdigit():
                attrs.append(f'height={o[2:]}')
        t = f' "{title.replace(chr(34), chr(92) + chr(34))}"' if title else ''
        md = f'![{self.label(alt)}]({self.dest(src)}{t})' + ('{' + ' '.join(attrs) + '}' if attrs else '')
        if link and link != src:
            md = f'[{md}]({self.dest(link)})'
        return md

    def link(self, url, label):
        label = label.strip()
        if not label or label == url:
            return f'<{url}>' if not re.search(r'[()\s]', url) else f'[{self.label(url)}]({self.dest(url)})'
        return f'[{self.label(label)}]({self.dest(url)})'

    def ref_link(self, path, label, default):
        url = self.external(path)
        text = label.strip() or default
        return self.label(text) if url is None else f'[{self.label(text)}]({self.dest(url)})'

    def bracket(self, body):
        """One legacy [...] construct as the current language, or None to
        leave it as written. Mirrors wikitext._bracket case by case."""
        if body == '|':
            return '|'
        parts = body.split('|')
        head = parts[0]
        low = head.lower()
        if low.startswith('module:'):
            return self.module([head[7:]] + parts[1:])
        if low.startswith(('if:', 'expr:')) or low == 'endif':
            self.warn('Conditional blocks were flattened: their text is now always shown.')
            return ''
        m = re.fullmatch(r'#(\d+)', head)
        if m:
            return f'[^{m.group(1)}]'
        if re.fullmatch(wikitext._URL, head, re.I):
            url = head
            if re.fullmatch(wikitext._IMG, url, re.I):
                return self.image(url, parts[1:])
            if len(parts) > 1 and re.fullmatch(wikitext._IMG, parts[1], re.I):
                return self.image(parts[1], parts[2:], link=url)
            if len(parts) > 1 and parts[1].startswith('='):
                src = self.external(parts[1][1:])
                return self.image(src, parts[2:], link=url) if src else ''
            return self.link(url, '|'.join(parts[1:]))
        if head.endswith(' ') and re.fullmatch(wikitext._URL, head.strip(), re.I):
            return f'<{head.strip()}>'
        m = re.fullmatch(r'(\d+)([MS])', head)
        if m:
            label = '|'.join(parts[1:]).strip()
            local = self.local_run(m.group(1)) if m.group(2) == 'M' else None
            if local:
                return f'[{self.label(label)}](run:{local})' if label else f'[{local}]'
            return self.ref_link(f'{m.group(1)}{m.group(2)}', label, head)
        if head.startswith('='):
            path = head[1:].lstrip('/')
            if re.fullmatch(wikitext._IMG, 'https://x/' + path, re.I):
                src = self.external(path)
                return self.image(src, parts[1:]) if src else ''
            return self.ref_link(path, '|'.join(parts[1:]), path)
        if head.startswith(wikitext._TV_ROOTS) and ' ' not in head:
            return self.ref_link(head, '|'.join(parts[1:]), head)
        if re.match(r'^[a-z]+:', head, re.I) and not low.startswith(('user:', 'm')):
            return self.escape(body)
        return None

    def module(self, params):
        name = params[0].lower()
        if name == 'youtube':
            vid = wikitext._youtube_id(params[1:])
            return f'[▶ video](https://youtu.be/{vid})' if vid else ''
        if name == 'frames':
            for p in params[1:]:
                if p.lower().startswith('amount='):
                    digits = p[7:].strip().replace(',', '')
                    return f'[{digits}]{{.frames}}' if digits.isdigit() else self.escape(digits)
        return ''

    def autolink(self, m):
        url = m.group(1)
        tail = ''
        while url and url[-1] in '.,;:!?\'"' or (url.endswith(')') and url.count('(') < url.count(')')):
            tail = url[-1] + tail
            url = url[:-1]
        if re.fullmatch(wikitext._IMG, url, re.I):
            return self.hold(self.image(url, [])) + tail
        return self.hold(url) + tail

    def inline(self, s, cell=False, single=False):
        """One run of legacy text as the current language."""
        if '[if:0]' in s.lower():
            s = re.sub(r'\[if:0\](.*?)\[endif\]',
                       lambda m: self.hold('<!-- ' + m.group(1).replace('--', '- -').strip() + ' -->'),
                       s, flags=re.S | re.I)
        s = s.replace('[[', self.hold(r'\[')).replace(']]', self.hold(r'\]'))

        def bracket(m):
            md = self.bracket(m.group(1))
            return m.group(0) if md is None else self.hold(md)

        if '[' in s:
            s = wikitext._RE_BRACKET.sub(bracket, s)
        if '!' in s and ('http' in s or 'www' in s):
            # a leading ! kept a URL as text: break the autolink after the scheme
            s = wikitext._RE_SUPPRESSED_URL.sub(
                lambda m: self.hold(m.group(1).replace('://', r':\/\/', 1)), s)
        if 'http' in s or 'www' in s:
            s = wikitext._RE_BARE_URL.sub(self.autolink, s)
        s = s.replace('%%%', ' ' if (cell or single) else BREAK)
        parts = s.split("''''") if "''''" in s else [s]
        s = ''.join(self.emphasis(p) for p in parts)
        out = []
        spans = any(c in s for c in (QUOTE, SUP, SUB, SMALL))
        for piece in _SPLIT.split(s):
            if piece and not _SPLIT.fullmatch(piece):
                piece = self.escape(piece, cell)
                if spans:
                    piece = re.sub(r'(?<!\\)([\[\]])', r'\\\1', piece)
            out.append(piece)
        s = ''.join(out)
        for _ in range(3):   # spans nest
            for rx, attrs in _SPANS:
                s = rx.sub(lambda m, a=attrs: '[' + m.group(1) + ']' + a, s)
        for rx, left, right in _PAIRS:
            s = rx.sub(lambda m, l=left, r=right: self.delimit(m.group(1), l, r), s)
        s = re.sub('[' + EMPHASIS_MARKERS + ']', '', s)
        for _ in range(4):
            s, n = re.subn(r'\x00(\d+)\x00', lambda m: self.held[int(m.group(1))], s)
            if not n:
                break
        return s

    def emphasis(self, s):
        """Legacy emphasis becomes private markers, in the legacy order."""
        s = wikitext._RE_BOLD.sub(BOLD + r'\1' + BOLD_, s)
        s = wikitext._RE_EMPH.sub(EMPH + r'\1' + EMPH_, s)
        s = wikitext._RE_CODE.sub(lambda m: self.code_run(m.group(1)), s)
        s = wikitext._RE_STRIKE.sub(STRIKE + r'\1' + STRIKE_, s)
        s = wikitext._RE_QUOTE.sub(QUOTE + r'\1' + QUOTE_, s)
        s = wikitext._RE_SUP.sub(SUP + r'\1' + SUP_, s)
        s = wikitext._RE_SUB.sub(SUB + r'\1' + SUB_, s)
        for _ in range(3):
            s, n = wikitext._RE_SMALL.subn(SMALL + r'\1' + SMALL_, s)
            if not n:
                break
        return s

    def code_run(self, content):
        """{{...}}: its text is code; links and images inside it stay what
        they are, and so does emphasis around them."""
        out = []
        for piece in _SPLIT.split(content):
            if piece and piece.strip() and not _SPLIT.fullmatch(piece):
                piece = self.hold(self.code(piece))
            out.append(piece)
        return ''.join(out)

    def code(self, text):
        text = re.sub('[' + MARKERS + ']', '', text)
        # a literal bracket held for escaping is just the bracket in code
        text = re.sub(r'\x00(\d+)\x00', lambda m: self.held[int(m.group(1))].lstrip('\\'), text)
        ticks = '`' * (max((len(r) for r in re.findall(r'`+', text)), default=0) + 1)
        pad = ' ' if text.startswith('`') or text.endswith('`') else ''
        return f'{ticks}{pad}{text}{pad}{ticks}'

    @staticmethod
    def delimit(core, left, right):
        """Emphasis cannot open before or close after a space: the spaces
        move outside the delimiters."""
        lead = core[:len(core) - len(core.lstrip())]
        trail = core[len(core.rstrip()):]
        core = core.strip()
        return f'{lead}{left}{core}{right}{trail}' if core else lead + trail

    @staticmethod
    def starters(line):
        for rx, repl in _LINE_START:
            line = rx.sub(repl, line, count=1)
        return line

    def paragraph(self, text):
        """Inline text as one paragraph block: hard breaks become a trailing
        backslash, and each line is guarded against reading as a block."""
        text = text.strip(BREAK + ' ')
        # a comment opening a line makes the whole line a comment block
        text = re.sub(r'^(<!--.*?-->)\s*(?=\S)', '\\1\n\n', text, flags=re.S)
        lines = [self.starters(x.strip()) for x in text.split(BREAK)]
        return '\\\n'.join(x for x in lines if x)


# --------------------------------------------------------------- blocks ----

class Frame:
    def __init__(self, kind, attrs=''):
        self.kind, self.attrs, self.blocks = kind, attrs, []

    def height(self):
        return max((b.height() + 1 for b in self.blocks if isinstance(b, Frame)), default=0)

    def render(self):
        colons = ':' * (3 + self.height())
        body = '\n\n'.join(b.render() if isinstance(b, Frame) else b for b in self.blocks if b)
        return f'{colons}{self.kind}{self.attrs}\n' + (body + '\n' if body else '') + colons


def _fence(lines, lang=''):
    ticks = '`' * max(3, max((len(r) for r in re.findall(r'`+', '\n'.join(lines))), default=0) + 1)
    return f'{ticks}{lang}\n' + '\n'.join(lines) + f'\n{ticks}'


def upgrade(text, external_base='', local_run=None):
    """(markdown, warnings) for legacy notes text."""
    up = Upgrader(external_base, local_run)
    text = normalize_newlines(text)
    lines = text.split('\n')
    # footnote definitions are only definitions when something refers to them
    referenced = set(re.findall(r'\[#(\d+)\]', text))

    root = Frame('root')
    stack = [root]

    def add(block):
        if block:
            stack[-1].blocks.append(block)

    para, pre, quote = [], [], []
    code = None
    table, lists, dls = [], [], []
    list_kinds = []

    def flush_para():
        if para:
            text = ' '.join(para)
            note = re.match(FOOTNOTE + r'(\d+)' + FOOTNOTE + ' ', text)
            if note:
                add(f'[^{note.group(1)}]: ' + up.paragraph(up.inline(text[note.end():])).replace('\\\n', ' '))
            else:
                add(up.paragraph(up.inline(text)))
            para.clear()
        if pre:
            add(_fence([x[1:] if x.startswith(' ') else x for x in pre]))
            pre.clear()
        if quote:
            add('> ' + up.paragraph(up.inline(' '.join(quote), single=True)).replace('\n', '\n> '))
            quote.clear()

    def flush_table():
        if not table:
            return
        width = max(len(cells) for _, cells in table)
        rows = [(th, cells + [(' ', False)] * (width - len(cells))) for th, cells in table]
        if any(len(c) != width for _, c in table):
            up.warn('Some table rows had uneven cell counts and were padded.')
        first_header = all(is_th for _, is_th in rows[0][1])
        any_header = any(is_th for _, cells in rows for _, is_th in cells)
        if first_header:
            header, body = rows[0][1], rows[1:]
        elif not any_header:
            header, body = [('', False)] * width, rows
            up.warn('A table had no header row; an empty one was added.')
        else:
            header, body = rows[0][1], rows[1:]
            up.warn('A table had header cells outside its first row; they are bold now.')

        def cell(c, bold):
            text = c[0].strip()
            return f'**{text}**' if (bold and c[1] and text) else text

        out = ['| ' + ' | '.join(cell(c, False) for c in header) + ' |',
               '|' + '|'.join(' --- ' for _ in header) + '|']
        for _, cells in body:
            out.append('| ' + ' | '.join(cell(c, True) for c in cells) + ' |')
        add('\n'.join(out))
        table.clear()

    def flush_lists():
        if lists:
            add('\n'.join(lists))
            lists.clear()
            list_kinds.clear()

    def flush_dl():
        for item in dls:
            add(item)
        dls.clear()

    def flush():
        flush_para()
        flush_table()
        flush_lists()
        flush_dl()

    def push(kind, attrs=''):
        frame = Frame(kind, attrs)
        stack[-1].blocks.append(frame)
        stack.append(frame)

    def pop_tab():
        """Close the open tab; one with nothing in it is the "Hide X" half
        of a show/hide pair and vanishes, the next tab starting folded."""
        flush()
        if stack[-1].kind == 'tab':
            tab = stack.pop()
            if not tab.blocks:
                stack[-1].blocks.remove(tab)
                stack[-1].hid = True

    for raw in lines:
        line = raw.rstrip()
        s = line.strip()

        if code is not None:
            if s.upper().startswith('%%END_EMBED'):
                add(_fence(code[1], code[0]))
                code = None
            else:
                code[1].append(line)
            continue

        d = wikitext._DIRECTIVE.match(s)
        if d:
            name, arg = d.group(1).upper(), d.group(2).strip()
            if name == 'SRC_EMBED':
                flush()
                code = (re.sub(r'[^\w+#-]', '', arg.split(' ')[0] if arg else ''), [])
            elif name in ('QUOTE_END', 'END_QUOTE'):
                flush()
                if stack[-1].kind == 'quote':
                    stack.pop()
            elif name == 'QUOTE':
                flush()
                who = re.sub(r'[-]', '', arg).replace('"', "'")
                push('quote', '{author="' + who + '"}' if who else '')
            elif name in ('TAB_START', 'TAB_HSTART'):
                flush()
                push('tabs')
            elif name == 'TAB':
                if stack[-1].kind == 'tab':
                    pop_tab()
                flush()
                if stack[-1].kind != 'tabs':
                    push('tabs')
                folded = getattr(stack[-1], 'hid', False)
                title = re.sub(r'[-"]', '', arg) or 'Show'
                stack[-1].hid = False
                push('tab', '{title="' + title + '"' + (' closed' if folded else '') + '}')
            elif name == 'TAB_END':
                if stack[-1].kind == 'tab':
                    pop_tab()
                if stack[-1].kind == 'tabs':
                    stack.pop()
            elif name == 'TOC':
                flush()
                stack[-1].blocks.append(Frame('toc'))
            elif name == 'DIV':
                flush()
                classes = ''.join(f' .{c}' for c in arg.split() if c in ('small', 'center', 'pre'))
                push('div', '{' + classes.strip() + '}' if classes else '')
            elif name == 'DIV_END':
                flush()
                if stack[-1].kind == 'div':
                    stack.pop()
            elif name == 'END_EMBED':
                flush()
            else:
                para.append(line)
            continue

        if line.startswith(' ') and s:
            if lists and re.match(r'^[*#]+\s', s):
                pass
            else:
                if para or quote or table or dls or lists:
                    flush()
                pre.append(line)
                continue
        elif pre:
            flush_para()

        if not s:
            flush()
            continue

        m = re.fullmatch(r'\[module:youtube((?:\|[^\]]*)?)\]', s, re.I)
        if m:
            flush()
            params = m.group(1).split('|')[1:]
            vid = wikitext._youtube_id(params)
            if vid:
                extra = ''
                for p in params:
                    lp = p.lower()
                    for key in ('w', 'h', 'start'):
                        if lp.startswith(key + '=') and lp[len(key) + 1:].isdigit():
                            extra += f' {key}={lp[len(key) + 1:]}'
                    if lp == 'loop':
                        extra += ' loop'
                stack[-1].blocks.append(Frame('embed', f'{{type=youtube v={vid}{extra}}}'))
            continue
        if re.fullmatch(r'-{4,}', s):
            flush()
            add('---')
            continue
        m = re.match(r'^(!{1,4})\s*(.*)', s)
        if m:
            flush()
            hashes = {1: '####', 2: '###', 3: '##', 4: '#'}[len(m.group(1))]
            add(f'{hashes} {up.inline(m.group(2), single=True).strip()}')
            continue
        if s.startswith('>'):
            if not quote:
                flush()
            if s[1:].strip():
                quote.append(s[1:].strip())
            continue
        if s.startswith('|'):
            if not table:
                flush()
            cells = [(up.inline(c, cell=True), is_th)
                     for c, is_th in wikitext._parse_table_cells(s)]
            if cells:                       # a line of bars alone is an empty row
                table.append((None, cells))
            continue
        m = re.match(r'^([*#]+)\s*(.*)', s)
        if m:
            if para or pre or quote or table or dls:
                flush()
            markers, item = m.group(1), up.starters(up.inline(m.group(2), single=True).strip())
            depth = min(len(markers), len(list_kinds) + 1)
            del list_kinds[depth - 1:]
            list_kinds.extend(markers[len(list_kinds):depth])
            indent = ''.join('  ' if k == '*' else '   ' for k in list_kinds[:-1])
            lead = '- ' if list_kinds[-1] == '*' else '1. '
            lists.append(indent + lead + item)
            continue
        m = re.match(r'^;\s*([^:]+?)\s*:\s*(.*)', s)
        if m:
            if not dls:
                flush()
            term = up.inline(m.group(1), single=True).strip()
            dls.append(up.starters(term) + '\n: ' + up.inline(m.group(2), single=True).strip())
            continue
        m = re.match(r'^\[(\d+)\]:?\s+(.*)', s)
        if m:
            flush()
            if m.group(1) in referenced:
                flush()
                para.append(FOOTNOTE + m.group(1) + FOOTNOTE + ' ' + m.group(2))
                continue
            para.append(s.replace('[', '[[', 1).replace(']', ']]', 1))
            continue
        if lists or table or dls or quote:
            flush()
        para.append(s)

    if code is not None:
        add(_fence(code[1], code[0]))
    flush()
    while len(stack) > 1:
        if stack[-1].kind == 'tab':
            pop_tab()
        if len(stack) > 1:
            stack.pop()
    blocks = [b.render() if isinstance(b, Frame) else b for b in root.blocks if b]
    return '\n\n'.join(blocks).strip() + ('\n' if blocks else ''), up.warnings
