import html
import re
from .markdown import esc

_IMG = r'https?://[^\s|\]\[<>"]+?\.(?:png|jpe?g|gif|webp|svg)(?:\?[^\s|\]\[<>"]*)?'
_URL = r'https?://[^\s<>\[\]"]+'
_TV_ROOTS = ('Forum/', 'UserFiles/', 'GameResources/', 'HomePages/', 'Wiki/',
             'Games/', 'Movies/', 'Submissions/', 'EmulatorResources/', 'Publications/')
_SAFE_HREF = re.compile(r'^(https?://|#)', re.I)

def _youtube_id(params):
    """Extract a valid YouTube id from module parameters."""
    for p in params:
        if p.lower().startswith('v='):
            vid = p[2:].split('?')[0].split('&')[0]
            if re.fullmatch(r'[\w-]{6,}', vid):
                return vid
    return None

def _youtube_embed(vid):
    """Render a privacy-friendly YouTube iframe."""
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
    """Render an image with safe markup options."""
    cls, attrs = 'noteimg', ''
    for o in opts:
        cls_part, attr = _image_option(o)
        cls += cls_part
        attrs += attr
    if ' alt=' not in attrs:
        attrs += ' alt=""'
    return (f'<a href="{link or src}"><img class="{cls}" src="{src}"{attrs} loading="lazy"></a>')


def _image_option(option):
    """Render one image alignment, text, or numeric size option."""
    low = option.lower()
    if low in ('left', 'right'):
        return ' ' + low, ''
    if low.startswith(('alt=', 'title=')):
        key, _, value = option.partition('=')
        return '', f' {key.lower()}="{value}"'
    if low.startswith(('w=', 'h=')) and option[2:].isdigit():
        key = 'width' if low.startswith('w=') else 'height'
        return '', f' {key}="{option[2:]}"'
    return '', ''

def _url_bracket(head, parts):
    """Render a bracketed URL, including linked images and bare labels."""
    url = head
    if re.fullmatch(_IMG, url, re.I):
        return _image(url, parts[1:])
    if len(parts) > 1 and re.fullmatch(_IMG, parts[1], re.I):
        return _image(parts[1], parts[2:], link=url)
    if len(parts) > 1 and parts[1].startswith('='):
        return _image('https://tasvideos.org/' + parts[1][1:].lstrip('/'), parts[2:], link=url)
    label = '|'.join(parts[1:]).strip() or url
    return f'<a href="{url}">{label}</a>'


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
        return _url_bracket(head, parts)
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
    """Render a bare URL without absorbing trailing punctuation."""
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

_RE_BOLD = re.compile(r'__(.+?)__')
_RE_EMPH = re.compile(r"''(.+?)''")
_RE_CODE = re.compile(r'\{\{(.+?)\}\}')
_RE_STRIKE = re.compile(r'---(.+?)---')
_RE_QUOTE = re.compile(r'««(.+?)»»')
_RE_SUP = re.compile(r'⸢⸢(.+?)⸣⸣')
_RE_SUB = re.compile(r'⸤⸤(.+?)⸥⸥')
_RE_SMALL = re.compile(r'\(\(([^()]+?)\)\)')

_RE_IF_ZERO = re.compile(r'\[if:0\].*?\[endif\]', flags=re.S | re.I)
_RE_BRACKET = re.compile(r'\[([^\[\]\x00]+)\]')
_RE_SUPPRESSED_URL = re.compile(r'!(' + _URL + ')')
_RE_BARE_URL = re.compile(r'(?<![\w/=\x00])(' + _URL + ')')
_RE_PLACEHOLDER = re.compile(r'\x00(\d+)\x00')

def inline(s, refs=lambda s: s):
    """Inline markup over one run of text. Links, images and modules are cut
    out first, into placeholders, so emphasis never reaches inside a URL and
    a link is never linked twice; the emphasis pass runs on what is left."""
    s = esc(s)
    tokens = []

    def hold(h):
        """Hold rendered markup outside subsequent emphasis passes."""
        tokens.append(h)
        return f'\x00{len(tokens) - 1}\x00'

    if '[if:0]' in s or '[IF:0]' in s:
        s = _RE_IF_ZERO.sub('', s)
    if '[[' in s:
        s = s.replace('[[', hold('['))
    if ']]' in s:
        s = s.replace(']]', hold(']'))

    def bracket(m):
        """Replace recognized wiki links with held markup."""
        h = _bracket(m.group(1))
        return m.group(0) if h is None else hold(h)

    if '[' in s:
        s = _RE_BRACKET.sub(bracket, s)
    # bare URLs: not inside a placeholder (already cut out), not preceded by
    # '!' (the suppression mark, which drops out), not glued to a word
    if '!' in s and ('http' in s or 'www' in s):
        s = _RE_SUPPRESSED_URL.sub(lambda m: hold(m.group(1)), s)
    if 'http' in s or 'www' in s:
        s = _RE_BARE_URL.sub(lambda m: hold(_autolink(m)), s)
    s = refs(s)
    if '%%%' in s:
        s = s.replace('%%%', '<br>')
    # '''' is the breaker: emphasis never spans it, so a run of underscores
    # in ASCII art can be cut in two and stay underscores
    if "''''" in s:
        s = ''.join(_emphasis(seg) for seg in s.split("''''"))
    else:
        s = _emphasis(s)
    if tokens:
        s = _RE_PLACEHOLDER.sub(lambda m: tokens[int(m.group(1))], s)
    return s

def _emphasis(s):
    """Render supported inline emphasis and code syntax."""
    if '__' in s:
        s = _RE_BOLD.sub(r'<b>\1</b>', s)
    if "''" in s:
        s = _RE_EMPH.sub(r'<em>\1</em>', s)
    if '{{' in s:
        s = _RE_CODE.sub(r'<code>\1</code>', s)
    if '---' in s:
        s = _RE_STRIKE.sub(r'<s>\1</s>', s)
    if '««' in s:
        s = _RE_QUOTE.sub(r'<q>\1</q>', s)
    if '⸢⸢' in s:
        s = _RE_SUP.sub(r'<sup>\1</sup>', s)
    if '⸤⸤' in s:
        s = _RE_SUB.sub(r'<sub>\1</sub>', s)
    if '((' in s:
        for _ in range(3):   # ((small)) nests
            s, n = _RE_SMALL.subn(r'<small>\1</small>', s)
            if not n:
                break
    return s

# ----------------------------------------------------------------- blocks ----
