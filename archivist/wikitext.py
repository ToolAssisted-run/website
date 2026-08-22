"""The notes dialect, rendered: one implementation, shared by the site
generator (published run pages) and the archivist (the submit preview), so
a preview can never drift from the page it previews (issue #30).

Cross-references ([M100001], [user:Name]) need the archive to resolve; the
caller passes `refs`, a function over the already-escaped inline text.
Everything else is pure text processing."""
import html
import re

def esc(s):
    return html.escape(str(s), quote=True)

def wiki_html(text, refs=lambda s: s):
    """Renderer for the wiki dialect used in author notes — the subset of
    tasvideos' TextFormattingRules that actually occurs in the corpus, plus
    this site's cross-references (see /formatting/)."""
    out = []
    in_ul = in_ol = in_table = in_code = in_quote = False
    code_buf = []

    def close_blocks(quote_too=True):
        nonlocal in_ul, in_ol, in_table, in_quote
        if in_ul: out.append('</ul>'); in_ul = False
        if in_ol: out.append('</ol>'); in_ol = False
        if in_table: out.append('</tbody></table></div>'); in_table = False
        if quote_too and in_quote: out.append('</blockquote>'); in_quote = False

    for line in text.splitlines():
        l = line.rstrip()
        s = l.strip()
        if in_code:
            if s.upper().startswith('%%END_EMBED'):
                out.append(f'<pre class="codebox"><code>{esc(chr(10).join(code_buf))}</code></pre>')
                code_buf = []
                in_code = False
            else:
                code_buf.append(l)
            continue
        if s.upper().startswith('%%SRC_EMBED'):
            close_blocks()
            in_code = True
            continue
        if s.upper().startswith(('%%QUOTE_END', '%%END_QUOTE')):
            close_blocks(quote_too=False)
            if in_quote:
                out.append('</blockquote>')
                in_quote = False
            continue
        if s.upper().startswith('%%QUOTE'):
            close_blocks()
            who = s[7:].strip()
            out.append('<blockquote class="wquote">'
                       + (f'<p class="qwho">{inline(who, refs)}:</p>' if who else ''))
            in_quote = True
            continue
        if s.upper().startswith('%%TAB_END') or s.upper() == '%%TAB':
            continue
        if s.upper().startswith('%%TAB '):
            close_blocks()
            out.append(f'<h4>{inline(s[6:], refs)}</h4>')
            continue
        if s == '%%TOC%%' or s.upper().startswith('%%DIV'):
            continue
        m = re.fullmatch(r'\[module:youtube\|v=([\w-]+)\]', s)
        if m:
            close_blocks()
            out.append(f'<div class="notes-embed"><iframe src="https://www.youtube-nocookie.com/embed/{m.group(1)}" allowfullscreen loading="lazy"></iframe></div>')
            continue
        if re.match(r'^-{4,}$', s):
            close_blocks()
            out.append('<hr>')
            continue
        if l.startswith('>'):  # disclaimer blockquote is rendered separately
            close_blocks()
            continue
        m = re.match(r'^(!{1,3})\s*(.*)', l)
        if m:
            close_blocks()
            lvl = {3: 'h3', 2: 'h3', 1: 'h4'}[len(m.group(1))]
            out.append(f'<{lvl}>{inline(m.group(2), refs)}</{lvl}>')
            continue
        if s.startswith('||') or (s.startswith('|') and s.endswith('|') and len(s) > 1):
            if not in_table:
                close_blocks(quote_too=False)
                out.append('<div class="tblwrap"><table><tbody>')
                in_table = True
            if s.startswith('||'):
                cells = s.strip('|').split('||')
                out.append('<tr>' + ''.join(f'<th>{inline(c.strip(), refs)}</th>' for c in cells) + '</tr>')
            else:
                cells = s.strip('|').split('|')
                out.append('<tr>' + ''.join(f'<td>{inline(c.strip(), refs)}</td>' for c in cells) + '</tr>')
            continue
        elif in_table:
            out.append('</tbody></table></div>')
            in_table = False
        m = re.match(r'^\*+\s*(.*)', l)
        if m:
            if in_ol: out.append('</ol>'); in_ol = False
            if not in_ul: out.append('<ul>'); in_ul = True
            out.append(f'<li>{inline(m.group(1), refs)}</li>')
            continue
        m = re.match(r'^#+\s+(.*)', l)
        if m:
            if in_ul: out.append('</ul>'); in_ul = False
            if not in_ol: out.append('<ol>'); in_ol = True
            out.append(f'<li>{inline(m.group(1), refs)}</li>')
            continue
        if not s:
            close_blocks(quote_too=False)
            continue
        if in_ul: out.append('</ul>'); in_ul = False
        if in_ol: out.append('</ol>'); in_ol = False
        out.append(f'<p>{inline(l, refs)}</p>')
    if in_code and code_buf:  # unterminated embed: still show it
        out.append(f'<pre class="codebox"><code>{esc(chr(10).join(code_buf))}</code></pre>')
    close_blocks()
    return '\n'.join(out)

def inline(s, refs=lambda s: s):
    s = esc(s)
    s = s.replace('%%%', '<br>')
    s = re.sub(r'__(.+?)__', r'<b>\1</b>', s)
    s = re.sub(r"''(.+?)''", r'<em>\1</em>', s)
    # a link to an image renders as the image itself, linked to the original;
    # display only, the stored notes stay exactly as written. Runs first,
    # while the text carries no generated markup to trip over; the lookbehind
    # leaves [url|label] links (a labelled link was asked for) alone.
    img = r'https?://[^\s\|\]\[]+\.(?:png|jpe?g|gif|webp)(?:\?[^\s\|\]\[]*)?'
    s = re.sub(rf'\[({img})\]',
               r'<a href="\1"><img class="noteimg" src="\1" alt="" loading="lazy"></a>',
               s, flags=re.I)
    s = re.sub(rf'(?<!["\[|=])\b({img})',
               r'<a href="\1"><img class="noteimg" src="\1" alt="" loading="lazy"></a>',
               s, flags=re.I)
    s = re.sub(r'\[module:youtube\|v=([\w-]+)\]',
               r'<a href="https://youtu.be/\1">▶ video</a>', s)
    s = refs(s)
    # TASVideos wiki-relative links ([=Path|label], [=Path]) point at the site
    # the notes were written on; early imports carry plenty and they rendered
    # as broken literal text here
    s = re.sub(r'\[=/?([^\]|]*)\|([^\]]+)\]',
               r'<a href="https://tasvideos.org/\1">\2</a>', s)
    s = re.sub(r'\[=/?([^\]|\s]+)\]',
               r'<a href="https://tasvideos.org/\1">\1</a>', s)
    # the same links written bare, without the '=': only known TASVideos
    # path roots, so bracketed prose is never touched
    s = re.sub(r'\[((?:UserFiles|GameResources|Forum|HomePages)/[^\]|\s]+)\|([^\]]+)\]',
               r'<a href="https://tasvideos.org/\1">\2</a>', s)
    s = re.sub(r'\[((?:UserFiles|GameResources|Forum|HomePages)/[^\]|\s]+)\]',
               r'<a href="https://tasvideos.org/\1">\1</a>', s)
    s = re.sub(r'\[(https?://[^\s\|\]]+)\|([^\]]+)\]', r'<a href="\1">\2</a>', s)
    s = re.sub(r'\[(https?://[^\s\]]+)\]', r'<a href="\1">\1</a>', s)
    return s

