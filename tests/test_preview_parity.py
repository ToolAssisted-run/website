#!/usr/bin/env python3
"""Preview parity: the submit page's live preview must render notes the way
the published run page will.

One implementation of the markup dialect (archivist/wikitext.py) serves both
the generator (render.wiki_html wraps it with the archive's cross-references)
and the archivist's /api/preview, which the submit page calls. This feeds the
corpus through both entry points and compares, and checks the client carries
no renderer of its own, so the two can never drift again (issue #30).

Usage: tests/test_preview_parity.py
"""
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mkarchive  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent

# block constructs both renderers claim to support (inline cross-references
# are deliberately excluded: only the server can resolve them)
CORPUS = [
    ('paragraphs', 'First paragraph.\n\nSecond paragraph.'),
    ('heading', '! A heading\nSome text.'),
    ('subheading', '!! A subheading\nSome text.'),
    ('bullet list', '* one\n* two\n* three'),
    ('numbered list', '# one\n# two'),
    ('list then paragraph', '* one\n* two\n\nAfter the list.'),
    ('rule', 'Above.\n----\nBelow.'),
    ('code block', '%%SRC_EMBED lua\nprint("hi")\nfor i=1,10 do end\n%%END_EMBED'),
    ('quote', '%%QUOTE Someone\nQuoted text.\n%%QUOTE_END\nAfter the quote.'),
    ('quote then heading', '%%QUOTE Someone\nQuoted text.\n! Heading after an open quote'),
    ('quote then list', '%%QUOTE\nQuoted.\n* item after an open quote'),
    ('table', '||a||b||\n||c||d||'),
    ('table then text', '||a||b||\nAfter the table.'),
    ('mixed', '! Head\n* a\n* b\n\n||x||y||\n\n%%QUOTE Bo\nq\n%%QUOTE_END\nEnd.'),
]

failures = []


def ck(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (f'  [{detail}]' if detail and not cond else ''))
    if not cond:
        failures.append(name)


def normalize(html):
    """Compare structure, not incidental whitespace."""
    html = re.sub(r'\s+', ' ', html)
    html = re.sub(r'>\s+<', '><', html)
    return html.strip()


def server_render(td, snippets):
    """Render each snippet by building a run whose notes are that snippet."""
    runs = [mkarchive.run_spec(f'M9006{i:02d}', frames=1000 + i, authors=[f'A{i}'],
                               notes=text)
            for i, (_, text) in enumerate(snippets)]
    arch = mkarchive.make_archive(td / 'parch', runs)
    out = td / 'pout'
    r = subprocess.run([sys.executable, str(REPO / 'generator/build.py'),
                        str(arch), str(out)], capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[-1500:])
        sys.exit('build failed')
    rendered = []
    for i, _ in enumerate(snippets):
        page = (out / 'runs' / f'M9006{i:02d}' / 'index.html').read_text()
        m = re.search(r'<div class="notes">(.*?)</div>\s*<h2>', page, re.S)
        rendered.append(m.group(1) if m else '')
    return rendered, out


def take_function(js, name):
    """Slice one function out of the emitted app.js so the harness runs
    exactly the code the browser would. Brace counting is unreliable here
    (regex literals such as /"/g contain quotes and braces), but the emitted
    file is consistently indented, so the function ends at the first line
    that is just its opening indentation plus a closing brace."""
    lines = js.splitlines()
    for i, line in enumerate(lines):
        if f'function {name}(' in line:
            indent = line[:len(line) - len(line.lstrip())]
            for j in range(i + 1, len(lines)):
                if lines[j] == indent + '}':
                    return '\n'.join(lines[i:j + 1])
            break
    raise ValueError(f'could not extract {name}')


def client_render(node, app_js, td, snippets):
    """Run the emitted renderNotes over the same snippets under node."""
    parts = [take_function(app_js, n) for n in ('escH', 'inlineMd', 'renderNotes')]
    harness = '\n'.join(parts) + """
const snippets = JSON.parse(process.argv[2]);
console.log(JSON.stringify(snippets.map(renderNotes)));
"""
    script = td / 'harness.mjs'
    script.write_text(harness)
    import json as _json
    r = subprocess.run([node, str(script), _json.dumps([t for _, t in snippets])],
                       capture_output=True, text=True)
    if r.returncode:
        return None, r.stderr[-800:]
    return _json.loads(r.stdout), ''


def main():
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        server, out = server_render(td, CORPUS)
        ck('server rendered every snippet', all(s.strip() for s in server),
           str([n for (n, _), s in zip(CORPUS, server) if not s.strip()]))

        # One renderer, not two (issue #30): the archivist previews with the
        # shared wikitext module, the client only asks for it.
        sys.path.insert(0, str(REPO / 'archivist'))
        import wikitext
        for (name, text), s in zip(CORPUS, server):
            ns, nw = normalize(s), normalize(wikitext.wiki_html(text))
            ck(f'parity: {name}', ns == nw, f'site={ns[:110]!r} shared={nw[:110]!r}')
        app_js = (out / 'assets' / 'app.js').read_text()
        ck('the client carries no renderer of its own',
           'function renderNotes' not in app_js and 'function inlineMd' not in app_js)
        ck('the preview asks the archivist', "'/api/preview'" in app_js)

    print('---', len(failures), 'failures')
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
