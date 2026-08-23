"""The notes dialect, rendered: one implementation, shared by the site
generator (published run pages) and the archivist (the submit preview), so
a preview can never drift from the page it previews (issue #30).

The dialect is TASVideos' TextFormattingRules (tasvideos.org/TextFormattingRules),
as far as the imported corpus uses them (issue #46), plus this site's
cross-references. Notes are stored exactly as written; this is display.

Cross-references ([M100001], [user:Name]) need the archive to resolve; the
caller passes `refs`, a function over the already-escaped inline text.
Everything else is pure text processing."""
import html
import re

def esc(s):
    """Text-safe: &, <, > and the double quote. The single quote stays, the
    dialect spells italics with it and it is harmless in a text node."""
    return html.escape(str(s), quote=False).replace('"', '&quot;')

# ---------------------------------------------------------------- inline ----

_IMG = r'https?://[^\s|\]\[<>"]+?\.(?:png|jpe?g|gif|webp|svg)(?:\?[^\s|\]\[<>"]*)?'
_URL = r'https?://[^\s<>\[\]"]+'
_TV_ROOTS = ('Forum/', 'UserFiles/', 'GameResources/', 'HomePages/', 'Wiki/',
             'Games/', 'Movies/', 'Submissions/', 'EmulatorResources/', 'Publications/')
_SAFE_HREF = re.compile(r'^(https?://|#)', re.I)

def _youtube_id(params):
    for p in params:
        if p.lower().startswith('v='):
            vid = p[2:].split('?')[0].split('&')[0]
            if re.fullmatch(r'[\w-]{6,}', vid):
                return vid
    return None

def _youtube_embed(vid):
    return (f'<div class="notes-embed"><iframe src="https://www.youtube-nocookie.com/embed/{vid}" '
            f'allowfullscreen loading="lazy"></iframe></div>')

def _module(params):
    """[module:name|...]: YouTube as a link (the block form embeds), the
    frame counter as its number; any other module is a site function that
    has no meaning here and leaves nothing."""
    name = params[0].lower()
    if name == 'youtube':
        vid = _youtube_id(params[1:])
        return f'<a href="https://youtu.be/{vid}">▶ video</a>' if vid else ''
    if name == 'frames':
        for p in params[1:]:
            if p.lower().startswith('amount='):
                try:
                    return f'{int(p[7:]):,} frames'
                except ValueError:
                    return p[7:]
    return ''

def _image(src, opts, link=None):
    cls, attrs = 'noteimg', ''
    for o in opts:
        lo = o.lower()
        if lo in ('left', 'right'):
            cls += ' ' + lo
        elif lo.startswith('alt='):
            attrs += f' alt="{o[4:]}"'
        elif lo.startswith('title='):
            attrs += f' title="{o[6:]}"'
        elif lo.startswith('w=') and o[2:].isdigit():
            attrs += f' width="{o[2:]}"'
        elif lo.startswith('h=') and o[2:].isdigit():
            attrs += f' height="{o[2:]}"'
    if ' alt=' not in attrs:
        attrs += ' alt=""'
    return (f'<a href="{link or src}"><img class="{cls}" src="{src}"{attrs} loading="lazy"></a>')

def _bracket(body):
    """One [...] construct, already escaped. Returns HTML, or None to leave
    the brackets as written (prose, or a cross-reference for `refs`)."""
    if body == '|':
        return '|'
    parts = body.split('|')
    head = parts[0]
    if head.lower().startswith('module:'):
        return _module([head[7:]] + parts[1:])
    if head.lower().startswith(('if:', 'expr:')) or head.lower() == 'endif':
        return ''
    m = re.fullmatch(r'#(\d+)', head)
    if m:
        return f'<a href="#fn-{m.group(1)}" id="fnref-{m.group(1)}" class="fnref">[{m.group(1)}]</a>'
    if re.fullmatch(_URL, head, re.I):
        url = head
        if re.fullmatch(_IMG, url, re.I):
            return _image(url, parts[1:])
        if len(parts) > 1 and re.fullmatch(_IMG, parts[1], re.I):
            return _image(parts[1], parts[2:], link=url)
        if len(parts) > 1 and parts[1].startswith('='):
            return _image('https://tasvideos.org/' + parts[1][1:].lstrip('/'), parts[2:], link=url)
        label = '|'.join(parts[1:]).strip() or url
        return f'<a href="{url}">{label}</a>'
    if head.endswith(' ') and re.fullmatch(_URL, head.strip(), re.I):
        # "a space after the URL" asks for a plain link, image or not
        return f'<a href="{head.strip()}">{head.strip()}</a>'
    m = re.fullmatch(r'(\d+)([MS])', head)
    if m:
        label = '|'.join(parts[1:]).strip() or f'{m.group(1)}{m.group(2)}'
        return f'<a href="https://tasvideos.org/{m.group(1)}{m.group(2)}">{label}</a>'
    if head.startswith('='):
        path = head[1:].lstrip('/')
        if re.fullmatch(_IMG, 'https://x/' + path, re.I):
            return _image('https://tasvideos.org/' + path, parts[1:])
        label = '|'.join(parts[1:]).strip() or path
        return f'<a href="https://tasvideos.org/{path}">{label}</a>'
    if head.startswith(_TV_ROOTS) and ' ' not in head:
        label = '|'.join(parts[1:]).strip() or head
        return f'<a href="https://tasvideos.org/{head}">{label}</a>'
    if re.match(r'^[a-z]+:', head, re.I) and not head.lower().startswith(('user:', 'm')):
        return esc(body)   # javascript:, data: and friends: text, never a link
    return None

def _autolink(m):
    url = m.group(1)
    # trailing punctuation belongs to the sentence; a closing paren only
    # when the URL opened none
    tail = ''
    while url and url[-1] in '.,;:!?\'"' or (url.endswith(')') and url.count('(') < url.count(')')):
        tail = url[-1] + tail
        url = url[:-1]
    if re.fullmatch(_IMG, url, re.I):
        return _image(url, []) + tail
    return f'<a href="{url}">{url}</a>' + tail

def inline(s, refs=lambda s: s):
    """Inline markup over one run of text. Links, images and modules are cut
    out first, into placeholders, so emphasis never reaches inside a URL and
    a link is never linked twice; the emphasis pass runs on what is left."""
    s = esc(s)
    tokens = []

    def hold(h):
        tokens.append(h)
        return f'\x00{len(tokens) - 1}\x00'

    s = re.sub(r'\[if:0\].*?\[endif\]', '', s, flags=re.S | re.I)
    s = s.replace('[[', hold('[')).replace(']]', hold(']'))

    def bracket(m):
        h = _bracket(m.group(1))
        return m.group(0) if h is None else hold(h)
    s = re.sub(r'\[([^\[\]\x00]+)\]', bracket, s)
    # bare URLs: not inside a placeholder (already cut out), not preceded by
    # '!' (the suppression mark, which drops out), not glued to a word
    s = re.sub(r'!(' + _URL + ')', lambda m: hold(m.group(1)), s)
    s = re.sub(r'(?<![\w/=\x00])(' + _URL + ')', lambda m: hold(_autolink(m)), s)
    s = refs(s)
    s = s.replace('%%%', '<br>')
    # '''' is the breaker: emphasis never spans it, so a run of underscores
    # in ASCII art can be cut in two and stay underscores
    s = ''.join(_emphasis(seg) for seg in s.split("''''"))
    return re.sub(r'\x00(\d+)\x00', lambda m: tokens[int(m.group(1))], s)

def _emphasis(s):
    s = re.sub(r'__(.+?)__', r'<b>\1</b>', s)
    s = re.sub(r"''(.+?)''", r'<em>\1</em>', s)
    s = re.sub(r'\{\{(.+?)\}\}', r'<code>\1</code>', s)
    s = re.sub(r'---(.+?)---', r'<s>\1</s>', s)
    s = re.sub(r'««(.+?)»»', r'<q>\1</q>', s)
    s = re.sub(r'⸢⸢(.+?)⸣⸣', r'<sup>\1</sup>', s)
    s = re.sub(r'⸤⸤(.+?)⸥⸥', r'<sub>\1</sub>', s)
    for _ in range(3):   # ((small)) nests
        s, n = re.subn(r'\(\(([^()]+?)\)\)', r'<small>\1</small>', s)
        if not n:
            break
    return s

# ----------------------------------------------------------------- blocks ----

_DIRECTIVE = re.compile(r'^%%([A-Z_]+)\s*(.*?)(?:%%)?\s*$', re.I)

def wiki_html(text, refs=lambda s: s):
    """The whole notes text as HTML."""
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
        return inline(s, refs)

    def close_lists():
        while lists:
            out.append('</li></' + lists.pop() + '>')

    def flush(keep_lists=False):
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

    for raw in text.splitlines():
        line = raw.rstrip()
        s = line.strip()

        if code is not None:
            if s.upper().startswith('%%END_EMBED'):
                out.append('<pre class="codebox"><code>' + esc('\n'.join(code)) + '</code></pre>')
                code = None
            else:
                code.append(line)
            continue

        d = _DIRECTIVE.match(s)
        if d:
            name, arg = d.group(1).upper(), d.group(2).strip()
            if name == 'SRC_EMBED':
                flush(); code = []
            elif name in ('QUOTE_END', 'END_QUOTE'):
                flush()
                if open_quotes:
                    out.append('</blockquote>'); open_quotes -= 1
            elif name == 'QUOTE':
                flush()
                out.append('<blockquote class="wquote">'
                           + (f'<p class="qwho">{inl(arg)}:</p>' if arg else ''))
                open_quotes += 1
            elif name in ('TAB_START', 'TAB_HSTART'):
                flush(); tabsets.append({'n': 0, 'open': False})
            elif name == 'TAB':
                # a tabset's tabs become collapsible sections, the first
                # one open: the page shows what the site showed first
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
            elif name in ('TOC', 'DIV', 'DIV_END', 'END_EMBED'):
                flush()
            else:
                para.append(line)   # not a directive we know: it is text
            continue

        if line.startswith(' ') and s:
            # a leading space is preformatted text, the rule says; a list
            # item indented for looks is the one exception the corpus needs
            if lists and re.match(r'^[*#]+\s', s):
                pass
            else:
                if para or quote or table or dl or lists:
                    flush()
                pre.append(line)
                continue
        elif pre:
            flush(keep_lists=True)

        if not s:
            flush()
            continue

        m = re.fullmatch(r'\[module:youtube((?:\|[^\]]*)?)\]', s, re.I)
        if m:
            flush()
            vid = _youtube_id(m.group(1).split('|')[1:])
            out.append(_youtube_embed(vid) if vid else '')
            continue
        if re.fullmatch(r'-{4,}', s):
            flush(); out.append('<hr>'); continue
        m = re.match(r'^(!{1,4})\s*(.*)', s)
        if m:
            flush()
            tag = {1: 'h4', 2: 'h3', 3: 'h2', 4: 'h2'}[len(m.group(1))]
            out.append(f'<{tag}>{inl(m.group(2))}</{tag}>')
            continue
        if s.startswith('>'):
            flush(keep_lists=False) if not quote else None
            quote.append(s[1:].strip())
            continue
        if s.startswith('||') or (s.startswith('|') and s.endswith('|') and len(s) > 1):
            if not table:
                flush()
                out.append('<div class="tblwrap"><table><tbody>')
                table = True
            row = s.replace('[|]', '\x01')
            if row.startswith('||'):
                cells = row.strip('|').split('||')
                out.append('<tr>' + ''.join(f'<th>{inl(c.strip().replace(chr(1), "|"))}</th>' for c in cells) + '</tr>')
            else:
                cells = row[1:-1].split('|')
                out.append('<tr>' + ''.join(f'<td>{inl(c.strip().replace(chr(1), "|"))}</td>' for c in cells) + '</tr>')
            continue
        m = re.match(r'^([*#]+)\s*(.*)', s)
        if m:
            if para or pre or quote or table or dl:
                flush(keep_lists=True)
            markers = m.group(1)
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
            out.append('<li>' + inl(m.group(2)))
            continue
        m = re.match(r'^;\s*([^:]+?)\s*:\s*(.*)', s)
        if m:
            if not dl:
                flush(); out.append('<dl>'); dl = True
            out.append(f'<dt>{inl(m.group(1))}</dt><dd>{inl(m.group(2))}</dd>')
            continue
        # `[1] text` and the `[1]: text` form TASVideos notes also use
        m = re.match(r'^\[(\d+)\]:?\s+(.*)', s)
        if m:
            flush()
            out.append(f'<p class="footnote" id="fn-{m.group(1)}"><a href="#fnref-{m.group(1)}">[{m.group(1)}]</a> {inl(m.group(2))}</p>')
            continue
        if lists or table or dl or quote:
            flush()
        para.append(s)

    if code:
        out.append('<pre class="codebox"><code>' + esc('\n'.join(code)) + '</code></pre>')
    flush()
    while tabsets:
        close_tab(); tabsets.pop()
    out.extend(['</blockquote>'] * open_quotes)   # unterminated quotes still close
    return '\n'.join(x for x in out if x)
