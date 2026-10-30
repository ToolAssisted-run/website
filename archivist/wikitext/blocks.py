import os
import re
from .markdown import esc
from .inline import inline, _youtube_id, _youtube_embed

_DIRECTIVE = re.compile(r'^%%([A-Z_]+)\s*(.*?)(?:%%)?\s*$', re.I)

LINESEP = os.linesep


def line_separator():
    """Cross-platform line-breaker provider"""
    return os.linesep


def normalize_newlines(text):
    """Normalize CRLF (\r\n) and CR (\r) line breaks to standard LF (\n)."""
    return str(text or '').replace('\r\n', '\n').replace('\r', '\n')


def _parse_table_cells(s):
    """Split a wiki table row into (cell_content, is_header) tuples, the way
    TASVideos' own parser reads one (NewParser.cs, EatPipes).

    A row starts with '|'. Each run of bars opens a cell: one bar a data cell,
    two or more a header cell, mixed freely within a row, so `|||` is one
    header cell and not an empty one beside it. Bars with nothing but spaces
    after them close the row without opening another cell, which is why the
    closing bar is optional. A bar inside brackets belongs to the bracket: a
    `[link|label]` or a `[|]` is one cell's text, never two cells."""
    s = s.rstrip('\r\n')
    cells, cur, pos, n = [], None, 0, len(s)
    while pos < n:
        if s[pos] == '|':
            end = pos
            while end < n and s[end] == '|':
                end += 1
            if cur is not None:
                cells.append(cur)
            if not s[end:].strip():
                cur = None
                break
            cur = ['', end - pos > 1]
            pos = end
            continue
        if cur is None:                      # text before the first bar
            break
        if s.startswith('[[', pos):         # a literal bracket, opening nothing
            cur[0] += '[['
            pos += 2
            continue
        if s[pos] == '[':
            close = s.find(']', pos)
            close = n - 1 if close < 0 else close
            cur[0] += s[pos:close + 1]
            pos = close + 1
            continue
        cur[0] += s[pos]
        pos += 1
    if cur is not None:
        cells.append(cur)
    return [(text.replace('[|]', '|').strip(), th) for text, th in cells]

def wiki_html(text, refs=lambda s: s):
    """The whole notes text as HTML."""
    text = normalize_newlines(text)
    out = []
    para = []          # lines of the paragraph being gathered
    pre = []           # lines of a preformatted block
    code = None        # lines of a %%SRC_EMBED block, or None
    quote = []         # lines of a > block
    lists = []         # open list types, innermost last
    table = False
    dl = False
    tabsets = []       # per open tabset: {'n': tabs so far, 'open': one is open}
    open_quotes = 0    # %%QUOTE blocks awaiting their %%QUOTE_END

    def inl(s):
        """Render inline wiki text with caller references."""
        return inline(s, refs)

    def close_lists():
        """Close all open wiki list elements."""
        while lists:
            out.append('</li></' + lists.pop() + '>')

    def flush(keep_lists=False):
        """Emit and close pending wiki block elements."""
        nonlocal para, pre, quote, table, dl
        if para:
            out.append('<p>' + inl(' '.join(para)) + '</p>')
            para = []
        if pre:
            out.append('<pre class="codebox"><code>' + esc('\n'.join(pre)) + '</code></pre>')
            pre = []
        if quote:
            out.append('<blockquote class="wquote"><p>' + inl(' '.join(quote)) + '</p></blockquote>')
            quote = []
        if table:
            out.append('</tbody></table></div>')
            table = False
        if dl:
            out.append('</dl>')
            dl = False
        if not keep_lists:
            close_lists()

    def close_tab():
        """Close the current wiki tab details element."""
        if tabsets and tabsets[-1]['open']:
            flush()
            start = tabsets[-1]['at']
            if len(out) == start + 1:
                # an empty tab: the "Hide X" half of the site's show/hide
                # idiom. Nothing to show, so nothing is shown, and the next
                # tab of the set starts closed, as the idiom intends
                del out[start]
                tabsets[-1]['hid'] = True
            else:
                out.append('</details>')
            tabsets[-1]['open'] = False

    def quote_directive(name, arg):
        """Open or close a named quote directive."""
        nonlocal open_quotes
        if name in ('QUOTE_END', 'END_QUOTE'):
            flush()
            if open_quotes:
                out.append('</blockquote>'); open_quotes -= 1
        else:
            flush()
            out.append('<blockquote class="wquote">'
                       + (f'<p class="qwho">{inl(arg)}:</p>' if arg else ''))
            open_quotes += 1

    def tab_directive(name, arg):
        """Open, select, or close one collapsible wiki tab."""
        if name in ('TAB_START', 'TAB_HSTART'):
            flush(); tabsets.append({'n': 0, 'open': False})
        elif name == 'TAB':
            if not tabsets:
                tabsets.append({'n': 0, 'open': False})
            close_tab()
            flush()
            first = tabsets[-1]['n'] == 0 and not tabsets[-1].get('hid')
            tabsets[-1]['at'] = len(out)
            out.append(f'<details class="wtab"{" open" if first else ""}><summary>{inl(arg)}</summary>')
            tabsets[-1].update(n=tabsets[-1]['n'] + 1, open=True)
        elif name == 'TAB_END':
            close_tab()
            if tabsets:
                tabsets.pop()

    def directive(name, arg, line):
        """Apply a block directive while retaining open quote and tab state."""
        nonlocal code
        if name == 'SRC_EMBED':
            flush(); code = []
        elif name in ('QUOTE_END', 'END_QUOTE', 'QUOTE'):
            quote_directive(name, arg)
        elif name in ('TAB_START', 'TAB_HSTART', 'TAB', 'TAB_END'):
            tab_directive(name, arg)
        elif name in ('TOC', 'DIV', 'DIV_END', 'END_EMBED'):
            flush()
        else:
            para.append(line)

    def list_item(markers, value):
        """Keep nested ordered and unordered lists balanced across items."""
        if para or pre or quote or table or dl:
            flush(keep_lists=True)
        depth = len(markers)
        while len(lists) > depth:
            out.append('</li></' + lists.pop() + '>')
        if lists and len(lists) == depth:
            want = 'ul' if markers[-1] == '*' else 'ol'
            if lists[-1] != want:
                out.append('</li></' + lists.pop() + '>')
            else:
                out.append('</li>')
        while len(lists) < depth:
            kind = 'ul' if markers[len(lists)] == '*' else 'ol'
            lists.append(kind)
            out.append(f'<{kind}>')
        out.append('<li>' + inl(value))

    def table_row(s):
        """Emit a header or data row, opening the table on its first row."""
        nonlocal table
        if not table:
            flush()
            out.append('<div class="tblwrap"><table><tbody>')
            table = True
        cells = _parse_table_cells(s)
        row_html = ''.join(
            f'<th>{inl(c)}</th>' if is_th else f'<td>{inl(c)}</td>'
            for c, is_th in cells
        )
        out.append(f'<tr>{row_html}</tr>')

    def content(s):
        """Render a regular wiki line into its heading, table, list or paragraph."""
        nonlocal dl
        m = re.fullmatch(r'\[module:youtube((?:\|[^\]]*)?)\]', s, re.I)
        if m:
            flush()
            vid = _youtube_id(m.group(1).split('|')[1:])
            out.append(_youtube_embed(vid) if vid else '')
            return
        if re.fullmatch(r'-{4,}', s):
            flush(); out.append('<hr>'); return
        m = re.match(r'^(!{1,4})\s*(.*)', s)
        if m:
            flush()
            tag = {1: 'h4', 2: 'h3', 3: 'h2', 4: 'h2'}[len(m.group(1))]
            out.append(f'<{tag}>{inl(m.group(2))}</{tag}>')
            return
        if s.startswith('>'):
            flush(keep_lists=False) if not quote else None
            quote.append(s[1:].strip())
            return
        if s.startswith('|'):
            table_row(s)
            return
        m = re.match(r'^([*#]+)\s*(.*)', s)
        if m:
            list_item(m.group(1), m.group(2))
            return
        m = re.match(r'^;\s*([^:]+?)\s*:\s*(.*)', s)
        if m:
            if not dl:
                flush(); out.append('<dl>'); dl = True
            out.append(f'<dt>{inl(m.group(1))}</dt><dd>{inl(m.group(2))}</dd>')
            return
        m = re.match(r'^\[(\d+)\]:?\s+(.*)', s)
        if m:
            flush()
            out.append(f'<p class="footnote" id="fn-{m.group(1)}"><a href="#fnref-{m.group(1)}">[{m.group(1)}]</a> {inl(m.group(2))}</p>')
            return
        if lists or table or dl or quote:
            flush()
        para.append(s)

    def embedded_code(line, s):
        """Consume a line inside a verbatim source embed, closing on its marker."""
        nonlocal code
        if code is None:
            return False
        if s.upper().startswith('%%END_EMBED'):
            out.append('<pre class="codebox"><code>' + esc('\n'.join(code)) + '</code></pre>')
            code = None
        else:
            code.append(line)
        return True

    def preformatted(line, s):
        """Collect indented source lines unless the indent only aligns a list."""
        if line.startswith(' ') and s:
            if lists and re.match(r'^[*#]+\s', s):
                return False
            if para or quote or table or dl or lists:
                flush()
            pre.append(line)
            return True
        if pre:
            flush(keep_lists=True)
        return False

    for raw in text.splitlines():
        line = raw.rstrip()
        s = line.strip()

        if embedded_code(line, s):
            continue

        d = _DIRECTIVE.match(s)
        if d:
            directive(d.group(1).upper(), d.group(2).strip(), line)
            continue

        if preformatted(line, s):
            continue

        if not s:
            flush()
            continue

        content(s)

    if code:
        out.append('<pre class="codebox"><code>' + esc('\n'.join(code)) + '</code></pre>')
    flush()
    while tabsets:
        close_tab(); tabsets.pop()
    out.extend(['</blockquote>'] * open_quotes)   # unterminated quotes still close
    return '\n'.join(x for x in out if x)
