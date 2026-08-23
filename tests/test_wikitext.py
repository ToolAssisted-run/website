#!/usr/bin/env python3
"""The notes dialect renderer (archivist/wikitext.py): the subset of
TASVideos' TextFormattingRules that the imported corpus uses, plus this
site's cross-references. One case per feature; the assertions are on the
HTML that matters, not on whitespace. Hermetic: no archive, no network."""
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'archivist'))
import wikitext  # noqa: E402

failures = []

def ck(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else f'  [{detail}]'))
    if not cond:
        failures.append(name)

def H(text):
    return wikitext.wiki_html(text)

def flat(h):
    return re.sub(r'\s+', ' ', h).strip()


def main():
    # ---- paragraphs: consecutive lines join; a blank line separates ----
    h = H('one\ntwo\n\nthree')
    ck('lines of one paragraph join', flat(h) == '<p>one two</p> <p>three</p>', h)
    ck('%%% forces a line break', '<br>' in H('a%%%b'))

    # ---- lists, nested ----
    h = H('* a\n** b\n*** c\n* d')
    ck('nested bullet lists', h.count('<ul>') == 3 and h.count('</ul>') == 3
       and '<li>a<ul>' in flat(h).replace(' ', ''), h)
    h = H('# one\n## two\n# three')
    ck('nested numbered lists', h.count('<ol>') == 2 and '<li>one<ol>' in flat(h).replace(' ', ''), h)
    h = H('* a\n# b')
    ck('bullets and numbers mix', '<ul>' in h and '<ol>' in h, h)
    h = H(';TAS: A tool-assisted speedrun.\n;Term: def')
    ck('definition lists', '<dl>' in h and '<dt>TAS</dt>' in h
       and '<dd>A tool-assisted speedrun.</dd>' in h and h.count('<dt>') == 2, h)

    # ---- headings and rules ----
    ck('! is a small heading', '<h4>small</h4>' in H('! small'))
    ck('!! is a medium heading', '<h3>medium</h3>' in H('!! medium'))
    ck('!!! is a large heading', '<h2>large</h2>' in H('!!! large'))
    ck('!!!! is a main heading', '<h2>main</h2>' in H('!!!! main'))
    ck('four dashes rule', '<hr>' in H('----'))
    ck('%%TOC%% vanishes', H('%%TOC%%\n! h').count('TOC') == 0)

    # ---- emphasis ----
    ck("''italic''", '<em>it</em>' in H("''it''"))
    ck('__bold__', '<b>bo</b>' in H('__bo__'))
    ck('((small))', '<small>sm</small>' in H('((sm))'))
    ck('{{teletype}}', '<code>tt</code>' in H('{{tt}}'))
    ck('---strike---', '<s>gone</s>' in H('---gone---'))
    ck('««inline quote»»', '<q>quoted</q>' in H('««quoted»»'))
    ck('superscript', '<sup>2</sup>' in H('x⸢⸢2⸣⸣'))
    ck('subscript', '<sub>2</sub>' in H('log⸤⸤2⸥⸥'))
    ck("'''' breaks an underscore run without bold",
       '<b>' not in H("+____''''____+") and '________' in H("+____''''____+"), H("+____''''____+"))

    # ---- preformatted ----
    h = H('text\n code line\n   more\ntext')
    ck('leading space is preformatted', '<pre' in h and 'code line\n   more' in h, h)
    h = H('%%SRC_EMBED lua\nlocal x = __1__\n%%END_EMBED')
    ck('SRC_EMBED is verbatim', '__1__' in h and '<b>' not in h, h)

    # ---- quotes ----
    h = H('%%QUOTE feos\nsaid so\n%%QUOTE_END')
    ck('named quote block', '<blockquote' in h and 'feos' in h and 'said so' in h, h)
    h = H('%%QUOTE\nnever closed')
    ck('an unterminated quote still closes', h.count('<blockquote') == h.count('</blockquote>') == 1, h)
    ck('a stray QUOTE_END closes nothing', '</blockquote>' not in H('%%QUOTE_END\ntext'))
    h = H('> indented note')
    ck('> line is an indented quote', '<blockquote' in h and 'indented note' in h, h)

    # ---- tabs become collapsible sections ----
    h = H('%%TAB Hide Inputs%%\nthe inputs\n%%TAB Show Inputs%%\nmore\n%%TAB_END%%')
    ck('tabs render as details/summary', h.count('<details') == 2
       and '<summary>Hide Inputs</summary>' in h and 'the inputs' in h
       and 'Inputs%%' not in h, h)
    h = H('%%TAB Hide Inputs%%\n%%TAB Show Inputs%%\nthe inputs\n%%TAB_END%%')
    ck('an empty first tab is the show/hide idiom: one closed section',
       h.count('<details') == 1 and '<details class="wtab"><summary>Show Inputs</summary>' in h
       and 'Hide Inputs' not in h, h)
    ck('TAB_START and TAB_END leave nothing behind',
       'TAB' not in H('%%TAB_START%%\n%%TAB a%%\nx\n%%TAB_END%%'))
    ck('DIV directives vanish', 'DIV' not in H('%%DIV card\nx\n%%DIV_END'))

    # ---- links ----
    h = H('see https://www.speedrun.com/pop_ww/guide/eeumn now')
    ck('bare URLs auto-link',
       '<a href="https://www.speedrun.com/pop_ww/guide/eeumn">https://www.speedrun.com/pop_ww/guide/eeumn</a>' in h, h)
    h = H('(https://example.com/x). Next')
    ck('trailing punctuation stays outside the link',
       'href="https://example.com/x"' in h and 'x).' not in h.split('href=')[1].split('"')[1], h)
    h = H('see !https://not.linked.example/ there')
    ck('! suppresses auto-linking', '<a' not in h and 'https://not.linked.example/' in h and '!' not in h, h)
    ck('[url|label]', '<a href="https://a.b/">lbl</a>' in H('[https://a.b/|lbl]'))
    ck('[url]', '<a href="https://a.b/">https://a.b/</a>' in H('[https://a.b/]'))
    ck('[[ ]] are literal brackets', '[literal]' in H('[[literal]]') and '<a' not in H('[[literal]]'))
    ck('[515M] links the TASVideos movie',
       '<a href="https://tasvideos.org/515M">' in H('[515M]'))
    ck('[1032S|label] links the submission',
       '<a href="https://tasvideos.org/1032S">rockman</a>' in H('[1032S|rockman]'))
    ck('[Forum/Topics/629|t]', 'href="https://tasvideos.org/Forum/Topics/629">t</a>' in H('[Forum/Topics/629|t]'))
    ck('[=Wiki/Page] relative link', 'href="https://tasvideos.org/Wiki/Page"' in H('[=Wiki/Page]'))
    ck('[GameResources/NES/X] bare wiki path', 'href="https://tasvideos.org/GameResources/NES/X"' in H('[GameResources/NES/X]'))
    ck('[user:feos] goes to the refs callback',
       'USER:feos' in wikitext.wiki_html('[user:feos]', refs=lambda s: s.replace('[user:feos]', 'USER:feos')))
    h = H('[#1] text\n\n[1] the footnote')
    ck('footnotes link and anchor', 'href="#fn-1"' in h and 'id="fn-1"' in h, h)
    h = H('[#2] text\n\n[2]: the colon form')
    ck('the colon form of a footnote anchors too', 'id="fn-2"' in h and 'the colon form' in h, h)

    # ---- images ----
    ck('[image.png] embeds', '<img' in H('[https://x.y/a.png]'))
    ck('bare image URL embeds', '<img' in H('https://x.y/a.png'))
    h = H('[https://x.y/a.png|right]')
    ck('image alignment', '<img' in h and 'class="noteimg right"' in h, h)
    h = H('[https://x.y/a.png|w=480|h=360|alt=cows]')
    ck('image size and alt', 'width="480"' in h and 'height="360"' in h and 'alt="cows"' in h, h)
    h = H('[https://site.example|https://x.y/a.png|alt=logo]')
    ck('image link', '<a href="https://site.example"><img' in h and 'alt="logo"' in h, h)

    # ---- modules ----
    for src in ('[module:youtube|v=jnXzwzhY1jo]',
                '[module:Youtube|hidelink|h=200|v=jnXzwzhY1jo]',
                '[module:youtube|v=jnXzwzhY1jo?si=abc|w=640]'):
        h = H(src)
        ck(f'youtube embed: {src}',
           'youtube-nocookie.com/embed/jnXzwzhY1jo"' in h and 'module' not in h
           and 'si=' not in h, h)
    h = H('watch [module:youtube|v=abcDEF12345] here')
    ck('inline youtube is a link', 'href="https://youtu.be/abcDEF12345"' in h, h)
    ck('frames module states the count', '41,658 frames' in H('[module:frames|amount=41658]'))
    ck('unknown module leaves a quiet mark', '<a' not in H('[module:foo|x=1]') and 'module:foo' not in H('[module:foo|x=1]'))

    # ---- tables ----
    h = H('||h1||h2||\n|a|b|\n|c with [|] bar| |')
    ck('tables with header', '<th>h1</th>' in h and '<td>a</td>' in h, h)
    ck('[|] is a literal bar in a cell', '<td>c with | bar</td>' in h, h)
    ck('empty cell stays a cell', h.count('<td>') == 4, h)

    # ---- safety ----
    h = H('<script>x</script> & <b>')
    ck('HTML is text', '&lt;script&gt;' in h and '<script>' not in h and '&amp;' in h, h)
    ck('javascript: URLs are not linked', 'href="javascript' not in H('[javascript:alert(1)|x]'))
    ck('comment macros hide their content', 'secret' not in H('a [if:0]secret[endif] b'))

    print('---', len(failures), 'failures')
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
