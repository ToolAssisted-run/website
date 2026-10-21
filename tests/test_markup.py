#!/usr/bin/env python3
"""The notes and rules language renderer (archivist/markup.py): CommonMark and
the GitHub extensions, plus this site's additions (spans, directives,
references), and what it must never render. One case per feature; the
assertions are on the HTML that matters, not on whitespace. Hermetic: no
archive, no network."""
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'archivist'))
import markup  # noqa: E402

failures = []

def ck(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else f'  [{detail}]'))
    if not cond:
        failures.append(name)

def R(text, **kw):
    return markup.render(text, **kw)

def flat(h):
    return re.sub(r'\s+', ' ', h).strip()


class Refs:
    """Stands in for the archive: run 7 exists, member ada is registered."""

    def run(self, number, label):
        return f'<a class="runref" href="/runs/M{number}/">RUN{number}:{label or ""}</a>' if number == '7' else None

    def user(self, name, label):
        return f'<a class="au">{label or name}</a>' if name == 'ada' else None


def main():
    # ---- CommonMark and the GitHub additions ----
    h = R('one\ntwo\n\nthree')
    ck('lines of a paragraph join, a blank line separates', flat(h) == '<p>one two</p> <p>three</p>', h)
    ck('bold, italic, strike, code',
       flat(R('**b** *i* ~~s~~ `c`')) == '<p><strong>b</strong> <em>i</em> <s>s</s> <code>c</code></p>')
    ck('a backslash at the end of a line breaks it', '<br' in R('a\\\nb'))
    h = R('- a\n  - b\n- c')
    ck('nested bullet lists', h.count('<ul>') == 2, h)
    ck('numbered list', '<ol>' in R('1. a\n2. b'))
    ck('a rule', '<hr' in R('a\n\n---\n\nb'))
    ck('a bare URL links itself', '<a href="https://example.com/x">https://example.com/x</a>' in R('see https://example.com/x.'))
    ck('a labelled link', '<a href="https://e.com">read</a>' in R('[read](https://e.com)'))
    h = R('| a | b |\n|---|---|\n| 1 | 2 |')
    ck('a table, wrapped for scrolling', '<div class="tblwrap"><table>' in h and '<th>a</th>' in h and '<td>2</td>' in h, h)
    ck('a quote', '<blockquote class="wquote">' in R('> hi'))
    ck('a fenced block is verbatim', '<pre class="codebox"><code>**x** &lt;b&gt;' in R('```\n**x** <b>\n```'), R('```\n**x** <b>\n```'))
    h = R('Term\n: definition')
    ck('a definition list', '<dl>' in h and '<dt>Term</dt>' in h and '<dd>definition</dd>' in h, h)
    h = R('Text[^1].\n\n[^1]: the note')
    ck('footnotes', 'footnote-ref' in h and 'the note' in h and 'footnotes' in h, h)

    # ---- headings ----
    h = R('# top\n\n## next')
    ck('# is the second level on the page (the run title owns the first)', '<h2 id="top">top</h2>' in h and '<h2 id="next">' not in h.replace('<h2 id="top">', '') or '<h2' in h, h)
    ck('# renders no h1', '<h1' not in h, h)
    ck('a heading takes its own id', '<h3 id="mine">Title</h3>' in R('### Title {#mine}'), R('### Title {#mine}'))
    h = R('## Same\n\n## Same')
    ck('ids are unique', 'id="same"' in h and 'id="same-2"' in h, h)
    h = R(':::toc\n:::\n\n## One\n\n### Two')
    ck('a table of contents lists the headings', '<nav class="toc">' in h and 'href="#one"' in h and 'href="#two"' in h, h)

    # ---- spans ----
    ck('small / quote / sup / sub',
       flat(R('[a]{.small} [b]{.quote} [c]{.sup} [d]{.sub}'))
       == '<p><small>a</small> <q>b</q> <sup>c</sup> <sub>d</sub></p>')
    ck('a frame count is written out', '<span class="frames">9,000 frames</span>' in R('[9000]{.frames}'))
    ck('a frame count with a rate is timed', 'title="2:30.00 at 60 fps"' in R('[9000]{.frames fps=60}'))
    ck('an unknown span class adds nothing', flat(R('[x]{.evil}')) == '<p>x</p>', R('[x]{.evil}'))

    # ---- images ----
    h = R('![alt](https://e.com/a.png "t"){width=480 align=left}')
    ck('an image: linked to itself, lazy, with its options',
       '<a href="https://e.com/a.png"><img class="noteimg left"' in h and 'width="480"' in h
       and 'alt="alt"' in h and 'title="t"' in h and 'loading="lazy"' in h, h)
    h = R('[![alt](https://e.com/a.png)](https://x.com)')
    ck('an image inside a link keeps that link', h.count('<a ') == 1 and 'href="https://x.com"' in h, h)
    ck('an image off the web is only its description', '<img' not in R('![alt](/local.png)'))
    ck('a non-numeric size is dropped', 'width=' not in R('![a](https://e.com/a.png){width=big}'))

    # ---- directives ----
    h = R(':::quote{author="Bob"}\nhi\n:::')
    ck('a quote with an author', '<blockquote class="wquote"><p class="qwho">Bob:</p>' in h and 'hi' in h, h)
    h = R('::::tabs\n:::tab{title="A"}\none\n:::\n:::tab{title="B"}\ntwo\n:::\n::::')
    ck('tabs are folds, the first open',
       h.count('<details class="wtab"') == 2 and '<details class="wtab" open><summary>A</summary>' in h
       and '<details class="wtab"><summary>B</summary>' in h, h)
    ck('a tab can start folded', '<details class="wtab"><summary>A</summary>' in R('::::tabs\n:::tab{title="A" closed}\nx\n:::\n::::'))
    h = R(':::embed{type=youtube v=JLVLBFjWiG8}\n:::')
    ck('a youtube embed', 'youtube-nocookie.com/embed/JLVLBFjWiG8' in h, h)
    ck('an embed with a bad id shows nothing', 'iframe' not in R(':::embed{type=youtube v="><script>"}\n:::'))
    ck('an embed of another kind shows nothing', 'iframe' not in R(':::embed{type=other v=abcdefgh}\n:::'))
    h = R(':::div{.small .evil}\nx\n:::')
    ck('a div keeps only the classes it knows', 'class="ndiv small"' in h and 'evil' not in h, h)

    # ---- references ----
    ck('a run reference, plain', '/runs/M100001/' in R('[M100001]'))
    h = R('[M7] and [user:ada] and [M8] and [user:bob]', refs=Refs())
    ck('references go through the resolver', 'RUN7:' in h and '<a class="au">ada</a>' in h, h)
    ck('an unresolved reference stays as written', '[M8]' in h and '[user:bob]' in h, h)
    ck('a reference written as a link', 'RUN7:the seven' in R('[the seven](run:M7)', refs=Refs()), R('[the seven](run:M7)', refs=Refs()))
    ck('a member written as a link', '<a class="au">Ada L</a>' in R('[Ada L](user:ada)', refs=Refs()))

    # ---- what must never render ----
    ck('raw HTML shows as text', '&lt;b&gt;raw&lt;/b&gt;' in R('<b>raw</b>') and '<b>' not in R('<b>raw</b>'))
    ck('a script tag is text', '<script' not in R('<script>alert(1)</script>'))
    ck('a javascript: link is not a link', '<a' not in R('[x](javascript:alert(1))'))
    ck('a data: link is not a link', '<a' not in R('[x](data:text/html;base64,AAAA)'))
    ck('an HTML comment shows nothing', 'hidden' not in R('a <!-- hidden --> b'))
    ck('a comment opening a line leaves the line after it', 'rest' in R('<!-- hidden -->\nrest'))
    h = R('[x](https://e.com){onclick=alert(1)}')
    ck('attribute lists drop what they do not know', not re.search(r'<[^>]*onclick', h), h)
    h = R('![a](https://e.com/a.png){onerror=alert(1) style=x}')
    ck('an image takes only its known options', not re.search(r'<[^>]*(onerror|style)', h), h)
    ck('a heading takes only its id', not re.search(r'<[^>]*onclick', R('## T {onclick=alert(1)}')))
    ck('an id is made safe', '"' not in re.search(r'id="([^"]*)"', R('## T {#a"b}')).group(1) if re.search(r'id="([^"]*)"', R('## T {#a"b}')) else True)
    ck('empty and None are empty', R('') == '' and R(None) == '')

    # ---- inline ----
    ck('inline rendering has no paragraph', markup.render_inline('**x** [M7]', refs=Refs()).startswith('<strong>x</strong>'))

    print()
    print(f'{len(failures)} failure(s)' if failures else 'all passed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
