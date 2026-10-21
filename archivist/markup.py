"""The notes and rules language, rendered: one implementation, shared by the
site generator (published pages) and the archivist (every live preview), so a
preview can never drift from the page it previews.

The language is CommonMark with the GitHub extensions (tables, strikethrough,
autolinks, footnotes) plus a few extensions of this site, all built from three
mechanisms:

  * attribute lists   {#id .class key=value}   after a heading, an image,
                                               a fenced block or a span
  * bracketed spans   [text]{.class}           small, quote, sup, sub, frames
  * fenced directives :::name{attrs} ... :::   quote, div, tabs, tab, toc,
                                               embed (more colons nest)

plus two site references: `[M100001]` (a run) and `[user:Name]` (a member),
or the same written as links, `[label](run:M100001)`, `[label](user:Name)`.

Raw HTML is never rendered (it shows as text; a comment shows as nothing).
Notes are stored exactly as written; this is display.

Cross-references need the archive to resolve; the caller passes `refs`, an
object with run(id, label) and user(name, label) that answer HTML, or None to
leave the reference as plain text."""
import html
import re

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.attrs import attrs_block_plugin, attrs_plugin
from mdit_py_plugins.container import container_plugin
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.footnote import footnote_plugin

SAFE_HREF = re.compile(r'^(https?://|mailto:|/|#|\./|\.\./|run:|user:)', re.I)
HTTP = re.compile(r'^https?://', re.I)
RUN_REF = re.compile(r'\[M([0-9]+)\]')
USER_REF = re.compile(r'\[user:([A-Za-z0-9. _-]{2,40})\]')
SHORTHAND = re.compile(RUN_REF.pattern + '|' + USER_REF.pattern)

SPAN_CLASSES = ('small', 'quote', 'sup', 'sub', 'frames')
DIV_CLASSES = ('small', 'center', 'pre')
YOUTUBE_ID = re.compile(r'[\w-]{6,}')
DIRECTIVES = ('quote', 'div', 'tabs', 'tab', 'toc', 'embed')

# what a token may carry out of the attribute lists: everything else is
# dropped, so no `onclick=` or `style=` can ride in on a {...}
KEEP_ATTRS = {
    'link_open': ('href', 'title'),
    'image': ('src', 'alt', 'title', 'width', 'height', 'class'),
    'heading_open': ('id',),
}


def esc(s):
    return html.escape(str(s), quote=True)


class PlainRefs:
    """References with nothing to resolve them against: a plain link for a
    run, a plain name for a member (the preview, and any caller without an
    archive)."""

    def run(self, rid, label):
        return f'<a class="runref" href="/runs/M{rid}/">{esc(label or "M" + rid)}</a>'

    def user(self, name, label):
        return f'<span class="au">{esc(label or name)}</span>'


# -------------------------------------------------------- attribute lists ----

_ATTR = re.compile(r'''\s*(?:\.([\w-]+)|\#([\w-]+)|([\w-]+)=("[^"]*"|'[^']*'|[^\s}]+)|([\w-]+))''')


def parse_attrs(text):
    """`{#id .a .b key="v" flag}` -> {'id', 'classes', 'kv'}, or None when it
    is not an attribute list at all."""
    text = text.strip()
    if not (text.startswith('{') and text.endswith('}')):
        return None
    body, pos, out = text[1:-1], 0, {'id': None, 'classes': [], 'kv': {}}
    while pos < len(body):
        if not body[pos:].strip():
            break
        m = _ATTR.match(body, pos)
        if not m:
            return None
        cls, ident, key, val, flag = m.groups()
        if cls:
            out['classes'].append(cls)
        elif ident:
            out['id'] = ident
        elif key:
            out['kv'][key.lower()] = val.strip('"\'')
        else:
            out['kv'][flag.lower()] = True
        pos = m.end()
    return out


# ------------------------------------------------------------ the parser ----

def _validate_link(url):
    return bool(SAFE_HREF.match(url))


def _directive_validator(name):
    pattern = re.compile(rf'^{name}(\{{.*\}})?\s*$')
    return lambda params, markup: bool(pattern.match(params.strip()))


def _directive_attrs(token):
    params = token.info.strip()
    brace = params.find('{')
    return parse_attrs(params[brace:]) if brace >= 0 else None


def _slug(text):
    slug = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
    return slug or 'section'


def _text_of(tokens):
    out = []
    for t in tokens:
        if t.type in ('text', 'code_inline'):
            out.append(t.content)
        elif t.children:
            out.append(_text_of(t.children))
    return ''.join(out)


_HEADING_ATTRS = re.compile(r'\s*(\{[^{}]*\})\s*$')


def _heading_attrs(inline):
    """`## Title {#id}`: the trailing attribute list leaves the title and
    names the heading."""
    kids = inline.children or []
    if not kids or kids[-1].type != 'text':
        return None
    m = _HEADING_ATTRS.search(kids[-1].content)
    attrs = parse_attrs(m.group(1)) if m else None
    if not attrs:
        return None
    kids[-1].content = kids[-1].content[:m.start()]
    return attrs['id']


def _core_headings(state):
    """Every heading gets an id (its own `{#id}`, else its text), unique on
    the page; the table of contents lists them."""
    seen, toc = set(), []
    tokens = state.tokens
    for i, t in enumerate(tokens):
        if t.type != 'heading_open':
            continue
        wanted = _heading_attrs(tokens[i + 1])
        text = _text_of(tokens[i + 1].children or [])
        ident = re.sub(r'[^\w-]', '', wanted or '') or _slug(text)
        base, n = ident, 1
        while ident in seen:
            n += 1
            ident = f'{base}-{n}'
        seen.add(ident)
        t.attrSet('id', ident)
        # `#` is the page's own title level: notes start at the second
        t.tag = 'h' + str(max(2, int(t.tag[1])))
        t.meta = {'toc': (ident, text, int(t.tag[1]))}
        toc.append((ident, text, int(t.tag[1])))
    state.env['toc'] = toc
    for i, t in enumerate(tokens):
        if t.type == 'heading_close':
            j = i - 2
            t.tag = tokens[j].tag


def _core_inline(state):
    """Walk every inline run: spans, references, images in links, and the
    final attribute whitelist."""
    refs = state.env.get('refs') or PlainRefs()
    for block in state.tokens:
        _sanitize(block)
        if block.type != 'inline' or not block.children:
            continue
        block.children = _frames(_refs(_clean(block.children), refs))
    for t in state.tokens:
        _sanitize(t)


def _sanitize(t):
    keep = KEEP_ATTRS.get(t.type, ())
    if t.attrs:
        t.attrs = {k: v for k, v in t.attrs.items() if k in keep}


def _clean(children):
    """Attribute whitelist, image link wrapping, shorthand references."""
    out, link = [], 0
    for t in children:
        if t.type == 'link_open':
            link += 1
        elif t.type == 'link_close':
            link -= 1
        elif t.type == 'image':
            t.meta = dict(t.meta or {}, inlink=bool(link))
            align = (t.attrGet('align') or '').lower()
            classes = ['noteimg'] + ([align] if align in ('left', 'right') else [])
            t.attrSet('class', ' '.join(classes))
            t.attrs = {k: v for k, v in t.attrs.items() if k in KEEP_ATTRS['image']}
        elif t.type == 'span_open':
            classes = [c for c in (t.attrGet('class') or '').split() if c in SPAN_CLASSES]
            t.meta = dict(t.meta or {}, classes=classes, fps=t.attrGet('fps'))
        _sanitize(t)
        out.append(t)
    return out


def _refs(children, refs):
    """`[M100001]` / `[user:Name]` shorthands and `run:` / `user:` links."""
    out, i = [], 0
    while i < len(children):
        t = children[i]
        if t.type == 'link_open':
            href = t.attrGet('href') or ''
            m = re.fullmatch(r'(run|user):(.+)', href, re.I)
            if m:
                j = i + 1
                while j < len(children) and children[j].type != 'link_close':
                    j += 1
                label = _text_of(children[i + 1:j])
                kind, key = m.group(1).lower(), m.group(2).strip()
                if kind == 'run':
                    rm = re.fullmatch(r'M?([0-9]+)', key)
                    h = refs.run(rm.group(1), label) if rm else None
                else:
                    h = refs.user(key, label) if USER_REF.fullmatch(f'[user:{key}]') else None
                out.append(_html(h if h is not None else esc(label or key)))
                i = j + 1
                continue
        if t.type == 'text' and '[' in t.content and SHORTHAND.search(t.content):
            pos = 0
            for m in SHORTHAND.finditer(t.content):
                if m.start() > pos:
                    out.append(_text(t.content[pos:m.start()]))
                if m.group(1):
                    h = refs.run(m.group(1), None)
                else:
                    h = refs.user(m.group(2).strip(), None)
                out.append(_html(h) if h is not None else _text(m.group(0)))
                pos = m.end()
            if pos < len(t.content):
                out.append(_text(t.content[pos:]))
            i += 1
            continue
        out.append(t)
        i += 1
    return out


def _frames(children):
    """`[9000]{.frames fps=60}` reads "9,000 frames", and with a frame rate
    says how long that is on hover."""
    out, i = [], 0
    while i < len(children):
        t = children[i]
        if t.type == 'span_open' and 'frames' in (t.meta or {}).get('classes', ()):
            j = i + 1
            while j < len(children) and children[j].type != 'span_close':
                j += 1
            raw = _text_of(children[i + 1:j]).strip().replace(',', '')
            if raw.isdigit():
                n, fps = int(raw), t.meta.get('fps')
                title = ''
                try:
                    if fps and float(fps) > 0:
                        secs = n / float(fps)
                        title = f' title="{int(secs // 60)}:{secs % 60:05.2f} at {esc(fps)} fps"'
                except ValueError:
                    pass
                out.append(_html(f'<span class="frames"{title}>{n:,} frames</span>'))
            else:
                out.extend(children[i + 1:j])
            i = j + 1
            continue
        out.append(t)
        i += 1
    return out


def _text(content):
    t = Token('text', '', 0)
    t.content = content
    return t


def _html(content):
    t = Token('html_inline', '', 0)
    t.content = content
    t.meta = {'trusted': True}
    return t


# ------------------------------------------------------------- rendering ----

def _render_html_inline(self, tokens, idx, options, env):
    t = tokens[idx]
    if (t.meta or {}).get('trusted'):
        return t.content
    # raw HTML is text; a comment is the author's note to themselves
    return '' if t.content.startswith('<!--') else esc(t.content)


def _render_html_block(self, tokens, idx, options, env):
    content = tokens[idx].content
    if content.lstrip().startswith('<!--'):
        return ''
    return '<p>' + esc(content.strip()) + '</p>\n'


def _render_image(self, tokens, idx, options, env):
    t = tokens[idx]
    src = t.attrGet('src') or ''
    alt = self.renderInlineAsText(t.children, options, env)
    if not HTTP.match(src):
        return esc(alt)
    attrs = ''.join(f' {k}="{esc(t.attrGet(k))}"' for k in ('title',) if t.attrGet(k))
    for k in ('width', 'height'):
        v = t.attrGet(k)
        if v and str(v).isdigit():
            attrs += f' {k}="{v}"'
    img = (f'<img class="{esc(t.attrGet("class") or "noteimg")}" src="{esc(src)}" '
           f'alt="{esc(alt)}"{attrs} loading="lazy">')
    return img if (t.meta or {}).get('inlink') else f'<a href="{esc(src)}">{img}</a>'


def _render_span_open(self, tokens, idx, options, env):
    t = tokens[idx]
    classes = (t.meta or {}).get('classes') or []
    if 'quote' in classes:
        return '<q>'
    for name in ('small', 'sup', 'sub'):
        if name in classes:
            return f'<{name}>'
    return ''


def _render_span_close(self, tokens, idx, options, env):
    j = idx
    depth = 0
    while j >= 0:
        if tokens[j].type == 'span_close':
            depth += 1
        elif tokens[j].type == 'span_open':
            depth -= 1
            if depth == 0:
                break
        j -= 1
    classes = (tokens[j].meta or {}).get('classes') or [] if j >= 0 else []
    if 'quote' in classes:
        return '</q>'
    for name in ('small', 'sup', 'sub'):
        if name in classes:
            return f'</{name}>'
    return ''


def _render_fence(self, tokens, idx, options, env):
    return '<pre class="codebox"><code>' + esc(tokens[idx].content) + '</code></pre>\n'


def _render_toc(self, tokens, idx, options, env):
    items = [(i, t, lvl) for i, t, lvl in env.get('toc', ())]
    if not items:
        return ''
    top = min(lvl for _, _, lvl in items)
    out = ['<nav class="toc"><ul>']
    for ident, text, lvl in items:
        out.append(f'<li class="toc{lvl - top + 1}"><a href="#{esc(ident)}">{esc(text)}</a></li>')
    out.append('</ul></nav>\n')
    return ''.join(out)


def _render_embed(self, tokens, idx, options, env):
    attrs = _directive_attrs(tokens[idx])
    kv = (attrs or {}).get('kv', {})
    vid = str(kv.get('v', ''))
    if kv.get('type', '').lower() != 'youtube' or not YOUTUBE_ID.fullmatch(vid):
        return ''
    return (f'<div class="notes-embed"><iframe src="https://www.youtube-nocookie.com/embed/{esc(vid)}" '
            f'allowfullscreen loading="lazy"></iframe></div>\n')


def _render_quote_open(self, tokens, idx, options, env):
    attrs = _directive_attrs(tokens[idx])
    who = (attrs or {}).get('kv', {}).get('author')
    return ('<blockquote class="wquote">'
            + (f'<p class="qwho">{esc(who)}:</p>' if isinstance(who, str) and who else ''))


def _render_div_open(self, tokens, idx, options, env):
    attrs = _directive_attrs(tokens[idx])
    classes = [c for c in (attrs or {}).get('classes', ()) if c in DIV_CLASSES]
    return f'<div class="ndiv {" ".join(classes)}">'.replace(' "', '"')


def _render_tab_open(self, tokens, idx, options, env):
    attrs = _directive_attrs(tokens[idx]) or {}
    title = attrs.get('kv', {}).get('title')
    title = title if isinstance(title, str) and title else 'Show'
    # the first tab of a set is open, as the page showed what came first;
    # `closed` says an author kept it folded
    first = True
    depth = 0
    for j in range(idx - 1, -1, -1):
        tj = tokens[j]
        if tj.type == 'container_tab_close':
            depth += 1
        elif tj.type == 'container_tab_open':
            if depth == 0:      # the tab this one sits inside, not a sibling
                break
            depth -= 1
            if depth == 0:      # a sibling before it
                first = False
                break
        elif tj.type == 'container_tabs_open' and depth == 0:
            break
    opened = ' open' if first and not attrs.get('kv', {}).get('closed') else ''
    return f'<details class="wtab"{opened}><summary>{esc(title)}</summary>'


_ENGINE = None


def engine():
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    md = MarkdownIt('commonmark', {'html': True, 'linkify': True, 'typographer': False})
    md.enable(['table', 'strikethrough', 'linkify'])
    md.validateLink = _validate_link
    footnote_plugin(md)
    deflist_plugin(md)
    attrs_block_plugin(md, allowed=['id', 'class'])
    attrs_plugin(md, spans=True, after=('image', 'link_close', 'code_inline', 'span_close'),
                 allowed=['id', 'class', 'width', 'height', 'align', 'fps', 'title'])
    for name in DIRECTIVES:
        container_plugin(md, name, marker=':', validate=_directive_validator(name))
    md.core.ruler.push('note_headings', _core_headings)
    md.core.ruler.push('note_inline', _core_inline)
    r = md.add_render_rule
    r('html_inline', _render_html_inline)
    r('html_block', _render_html_block)
    r('image', _render_image)
    r('span_open', _render_span_open)
    r('span_close', _render_span_close)
    r('fence', _render_fence)
    r('code_block', _render_fence)
    r('container_toc_open', _render_toc)
    r('container_toc_close', lambda *a: '')
    r('container_embed_open', _render_embed)
    r('container_embed_close', lambda *a: '')
    r('container_quote_open', _render_quote_open)
    r('container_quote_close', lambda *a: '</blockquote>\n')
    r('container_div_open', _render_div_open)
    r('container_div_close', lambda *a: '</div>\n')
    r('container_tabs_open', lambda *a: '<div class="wtabs">')
    r('container_tabs_close', lambda *a: '</div>\n')
    r('container_tab_open', _render_tab_open)
    r('container_tab_close', lambda *a: '</details>\n')
    r('blockquote_open', lambda *a: '<blockquote class="wquote">')
    r('table_open', lambda *a: '<div class="tblwrap"><table>')
    r('table_close', lambda *a: '</table></div>\n')
    _ENGINE = md
    return md


def render(text, refs=None):
    """The whole text as HTML."""
    text = str(text or '').replace('\r\n', '\n').replace('\r', '\n')
    return engine().render(text, {'refs': refs}).strip()


def render_inline(text, refs=None):
    """One run of text as inline HTML, no paragraph around it."""
    text = str(text or '').replace('\r\n', '\n').replace('\r', '\n')
    return engine().renderInline(text, {'refs': refs}).strip()
