#!/usr/bin/env python3
"""The upgrade of legacy notes to the current language
(archivist/notes_upgrade.py): what each legacy construct becomes, that text
which only looked like markup stays text, and that the upgraded notes read
the same as the old ones did. Hermetic: no archive, no network."""
import html
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'archivist'))
import markup  # noqa: E402
import notes_upgrade  # noqa: E402
import wikitext  # noqa: E402

BASE = 'https://legacy.example/'
failures = []

def ck(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else f'  [{detail}]'))
    if not cond:
        failures.append(name)

def up(text, base=BASE):
    return notes_upgrade.upgrade(text, base, lambda n: 'M6012' if n == '6012' else None)

def U(text):
    return up(text)[0].rstrip('\n')

def words(h):
    """What a reader sees, reduced to its letters and digits."""
    h = re.sub(r'<[^>]+>', ' ', h)
    return re.sub(r'\W+', '', html.unescape(h).lower())


class Plain:
    def run(self, number, label):
        return f'M{number}'

    def user(self, name, label):
        return name


def main():
    # ---- what each construct becomes ----
    ck('bold, italic, code, strike', U("__bold__ and ''italic'' and {{code}} and ---gone---")
       == '**bold** and *italic* and `code` and ~~gone~~')
    ck('lists nest', U('* a\n** b\n# one\n## two') == '- a\n  - b\n1. one\n   1. two')
    ck('headings and rule', U('!!! Big\n!! Mid\n! Small\n----\ntext') == '## Big\n\n### Mid\n\n#### Small\n\n---\n\ntext')
    ck('links', U('[https://e.com|label] [https://e.com]') == '[label](https://e.com) <https://e.com>')
    ck('an image with options', U('[https://e.com/a.png|w=480|right]') == '![](https://e.com/a.png){width=480 align=right}')
    ck('an image that links elsewhere', U('[https://x.com|https://e.com/a.png]') == '[![](https://e.com/a.png)](https://x.com)')
    ck('a movie that is here becomes a run reference', U('[6012M]') == '[M6012]')
    ck('a labelled movie that is here becomes a run link', U('[6012M|that one]') == '[that one](run:M6012)')
    ck('other references become ordinary links',
       U('[1032S|label] [Forum/Topics/629|forum] [=Some/Page|pg]')
       == '[label](https://legacy.example/1032S) [forum](https://legacy.example/Forum/Topics/629) [pg](https://legacy.example/Some/Page)')
    md, warn = up('[1032S]', base='')
    ck('without a configured site, a reference is its label and says so', md.strip() == '1032S' and warn, (md, warn))
    ck('site references stay as they are', U('[user:someone] [M100001]') == '[user:someone] [M100001]')
    ck('a youtube line becomes an embed', U('[module:youtube|v=JLVLBFjWiG8]') == ':::embed{type=youtube v=JLVLBFjWiG8}\n:::')
    ck('a frame count', U('x [module:frames|amount=9000]') == 'x [9000]{.frames}')
    ck('a frame count with a stray space', U('x [module:frames|amount= 9000]') == 'x [9000]{.frames}')
    ck('a quote with an author', U('%%QUOTE bob\nhi\n%%QUOTE_END') == ':::quote{author="bob"}\nhi\n:::')
    ck('a source box keeps its language', U('%%SRC_EMBED lua\nx = __1__\n%%END_EMBED') == '```lua\nx = __1__\n```')
    ck('preformatted text becomes a fence', U(' one\n two') == '```\none\ntwo\n```')
    ck('a table with a header row', U('|| h1 || h2 ||\n| a | b |') == '| h1 | h2 |\n| --- | --- |\n| a | b |')
    user_tbl = ('||Character||Max speed||Acceleration||Deceleration\n'
                '|Junior|2:0|0:40|0:40\n'
                '|Pig|3:0|1:0|0:64\n'
                '|Ostrich|4:0|0:128|0:32\n'
                '|Goat|3:0|0:128|0:32')
    want_user_tbl = ('| Character | Max speed | Acceleration | Deceleration |\n'
                     '| --- | --- | --- | --- |\n'
                     '| Junior | 2:0 | 0:40 | 0:40 |\n'
                     '| Pig | 3:0 | 1:0 | 0:64 |\n'
                     '| Ostrich | 4:0 | 0:128 | 0:32 |\n'
                     '| Goat | 3:0 | 0:128 | 0:32 |')
    ck('a table without trailing pipes', U(user_tbl) == want_user_tbl)
    user_tbl_linesep = (
        f'||Character||Max speed||Acceleration||Deceleration{notes_upgrade.LINESEP}'
        f'|Junior|2:0|0:40|0:40{notes_upgrade.LINESEP}'
        f'|Pig|3:0|1:0|0:64{notes_upgrade.LINESEP}'
        f'|Ostrich|4:0|0:128|0:32{notes_upgrade.LINESEP}'
        f'|Goat|3:0|0:128|0:32'
    )
    ck('a table with platform line-breaker', U(user_tbl_linesep) == want_user_tbl)
    ck('a table with CRLF explicitly', U(user_tbl.replace('\n', '\r\n')) == want_user_tbl)
    md, warn = up('| a | b |\n| c | d |')
    ck('a table without one gets an empty header, and says so', md.startswith('|  |  |\n| --- | --- |') and warn, (md, warn))
    md, warn = up('| a | b |\n| c |')
    ck('a ragged table is padded, and says so', warn and md.count('|') >= 9, (md, warn))
    ck('a definition', U(';Term: the def') == 'Term\n: the def')
    ck('a footnote with its reference', U('text[#1] more\n\n[1] the note') == 'text[^1] more\n\n[^1]: the note')
    ck('a footnote takes its following lines', U('a[#1]\n[1] one\ntwo') == 'a[^1]\n\n[^1]: one two')
    ck('a footnote nobody refers to is text', U('[1] note') == '\\[1\\] note')
    ck('a forced break', U('a%%%b') == 'a\\\nb')
    ck('small, quote, sup, sub', U('((s)) ««q»» ⸢⸢p⸣⸣ ⸤⸤b⸥⸥') == '[s]{.small} [q]{.quote} [p]{.sup} [b]{.sub}')
    ck('tabs, with the table of contents',
       U('%%TOC%%\n%%TAB_START%%\n%%TAB One%%\nfirst\n%%TAB Two%%\nsecond\n%%TAB_END%%')
       == ':::toc\n:::\n\n::::tabs\n:::tab{title="One"}\nfirst\n:::\n\n:::tab{title="Two"}\nsecond\n:::\n::::')
    ck('a tab that holds an embed nests by more colons',
       U('%%TAB_START%%\n%%TAB A%%\n[module:youtube|v=JLVLBFjWiG8]\n%%TAB_END%%').startswith(':::::tabs\n::::tab{title="A"}\n:::embed'))
    ck('an empty tab is the hide half of a pair: the next one starts folded',
       ':::tab{title="B" closed}' in U('%%TAB_START%%\n%%TAB Hide A%%\n%%TAB B%%\nbody\n%%TAB_END%%'))
    ck('hidden text becomes a comment', U('[if:0]hidden[endif] shown') == '<!-- hidden -->\n\nshown')
    ck('a literal bracket pair', U('[[literal]]') == '\\[literal\\]')
    ck('a link kept as text stays text', 'https:\\/\\/e.com' in U('see !https://e.com/x'))
    ck('an empty note is empty', up('')[0] == '')

    # ---- text that only looked like markup stays text ----
    for text in ['1. not a list', '- not a list', '+ not either', '# not a heading', '> not a quote',
                 '*star* and _under_', 'a `tick`', '[a](b) literal', '```fence', '~~x~~', ':::note',
                 '<b>tag</b>', '&amp; entity', 'x {.cls} y', '[^2] no def', '= = =', '***']:
        md, _ = up(text)
        h = markup.render(md, refs=Plain())
        ck(f'literal text survives: {text!r}', words(h) == words(html.escape(text)) or words(h) == words(text),
           (md, h))

    # ---- the upgraded notes read the same as the old ones ----
    samples = [
        "Intro with __bold__ and ''italic'' and a [http://e.com/x|link].\n\n!! Section\n\n* one\n** two\n* three\n\n----\nEnd.",
        "|| Name || Time ||\n| a | 1:00 |\n| b | 2:00 |",
        ";A: first\n;B: second",
        "%%QUOTE who\nthey said __this__\n%%QUOTE_END\nafter",
        "%%TAB_START%%\n%%TAB Inputs%%\nlong __text__\n%%TAB More%%\nmore\n%%TAB_END%%",
        "See [M100001] and [user:someone] and ((fine print)) and ««quoted»».",
        "Line one%%%line two\n\n %%% preformatted * stuff",
        "[https://e.com/a.png|alt=A shot|w=300|left] caption",
        "* item with [https://e.com|a link] and {{code}}\n# numbered ''em''",
    ]
    for s in samples:
        old = wikitext.wiki_html(s, refs=lambda t: re.sub(r'\[user:([^\]]+)\]', r'\1', t))
        old = re.sub(r'\[M([0-9]+)\]', r'M\1', old)
        md, _ = up(s)
        new = markup.render(md, refs=Plain())
        ck(f'reads the same: {s[:40]!r}', words(old) == words(new), (words(old), words(new)))

    # ---- the whole thing is safe ----
    md, _ = up('<script>alert(1)</script> [x](javascript:alert(1)) <img src=x onerror=alert(1)>')
    h = markup.render(md, refs=Plain())
    ck('upgraded hostile text renders inert', '<script' not in h and '<img' not in h and 'href="javascript' not in h, h)

    print()
    print(f'{len(failures)} failure(s)' if failures else 'all passed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
