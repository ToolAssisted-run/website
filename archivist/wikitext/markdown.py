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


def _markdown_list(line, mode, out, close, inline_markdown):
    """Append a Markdown bullet or numbered item when the line is a list."""
    match = re.match(r'[-*]\s+(.*)', line)
    kind = 'ul'
    if not match:
        match = re.match(r'\d+[.)]\s+(.*)', line)
        kind = 'ol'
    if not match:
        return False
    if mode[0] != kind:
        close()
        out.append(f'<{kind}>')
        mode[0] = kind
    out.append(f'<li>{inline_markdown(match.group(1))}</li>')
    return True


def esc(s):
    """Text-safe: &, <, > and the double quote. The single quote stays, the
    dialect spells italics with it and it is harmless in a text node."""
    return html.escape(str(s), quote=False).replace('"', '&quot;')


def md_html(text):
    """The safe Markdown subset used by game and category rules."""
    def link(match):
        """Render a safe Markdown link."""
        label, url = match.group(1), match.group(2)
        if url.startswith(('http://', 'https://', '/', '#', './', '../')):
            return f'<a href="{url}">{label}</a>'
        return f'[{label}]({url})'

    def inline_markdown(value):
        """Render safe inline Markdown syntax."""
        value = esc(value)
        value = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', link, value)
        value = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', value)
        value = re.sub(r'__(.+?)__', r'<b>\1</b>', value)
        value = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'<em>\1</em>', value)
        value = re.sub(r'(?<!_)_([^_\n]+)_(?!_)', r'<em>\1</em>', value)
        value = re.sub(r'~~(.+?)~~', r'<s>\1</s>', value)
        return re.sub(r'`([^`]+)`', r'<code>\1</code>', value)

    out = []
    mode = [None]

    def close():
        """Close the current Markdown block."""
        if mode[0] == 'ul':
            out.append('</ul>')
        elif mode[0] == 'ol':
            out.append('</ol>')
        elif mode[0] == 'p':
            out.append('</p>')
        elif mode[0] == 'blockquote':
            out.append('</blockquote>')
        mode[0] = None

    for raw in str(text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        line = raw.strip()
        if not line:
            close()
            continue
        match = re.match(r'^(#{1,6})\s+(.*)', line)
        if match:
            close()
            level = len(match.group(1))
            out.append(f'<h{level}>{inline_markdown(match.group(2))}</h{level}>')
            continue
        if re.match(r'^(-{3,}|\*{3,}|_{3,})$', line):
            close()
            out.append('<hr>')
            continue
        match = re.match(r'^>\s*(.*)', line)
        if match:
            if mode[0] != 'blockquote':
                close()
                out.append('<blockquote>')
                mode[0] = 'blockquote'
            else:
                out.append(' ')
            out.append(inline_markdown(match.group(1)))
            continue
        if _markdown_list(line, mode, out, close, inline_markdown):
            continue
        if mode[0] != 'p':
            close()
            out.append('<p>')
            mode[0] = 'p'
        else:
            out.append(' ')
        out.append(inline_markdown(line))
    close()
    return ''.join(out)


# ---------------------------------------------------------------- inline ----
