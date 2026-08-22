#!/usr/bin/env python3
"""Compare two site builds page by page, ignoring whitespace that HTML does
not render. A refactor of the generator must leave the site unchanged; this
is how that is shown.

Usage: golden_diff.py <golden_dir> <new_dir> [--show]
Exit status 1 when any page differs. Build stamps and sitemap dates are
expected to differ and are skipped."""
import difflib
import pathlib
import re
import sys

SKIP = {'assets/buildstamp.json', 'sitemap.xml'}
_WS = re.compile(r'\s+')

def norm(text):
    # whitespace between tags and runs of whitespace inside text both collapse;
    # attribute order and everything else must match exactly
    text = re.sub(r'>\s+<', '><', text).replace('&#x27;', '&#39;').replace('&quot;', '&#34;')  # same quotes, two escapers
    return _WS.sub(' ', text).strip()

def main():
    golden, new = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    show = '--show' in sys.argv
    bad = 0
    gfiles = {p.relative_to(golden).as_posix() for p in golden.rglob('*') if p.is_file()}
    nfiles = {p.relative_to(new).as_posix() for p in new.rglob('*') if p.is_file()}
    for missing in sorted(gfiles - nfiles):
        print(f'MISSING {missing}'); bad += 1
    for extra in sorted(nfiles - gfiles):
        print(f'EXTRA   {extra}'); bad += 1
    for rel in sorted(gfiles & nfiles):
        if rel in SKIP or not rel.endswith(('.html', '.json', '.txt', '.xml', '.js', '.css')):
            continue
        a, b = (golden / rel).read_text(), (new / rel).read_text()
        if norm(a) != norm(b):
            bad += 1
            print(f'DIFF    {rel}')
            if show:
                sys.stdout.writelines(difflib.unified_diff(
                    norm(a).replace('><', '>\n<').splitlines(True),
                    norm(b).replace('><', '>\n<').splitlines(True),
                    'golden', 'new', n=1))
    print(f'{bad} differences' if bad else 'identical')
    sys.exit(1 if bad else 0)

if __name__ == '__main__':
    main()
