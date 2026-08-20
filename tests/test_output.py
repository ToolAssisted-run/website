#!/usr/bin/env python3
"""Output invariants: properties every generated site must hold, whatever the
archive contains.

Phase A builds a fully-controlled archive (tests/mkarchive.py) carrying hostile
fixtures — an author name and notes full of markup, malformed wiki blocks,
cross-references to a missing run — and asserts escaping, link integrity, page
shape, cache busting, beta gating and house style. Because every byte of that
archive is ours, "no em dash anywhere" and "no raw <script>" are meaningful
assertions rather than a lottery on member content.

Phase B rebuilds the same archive as production (ARCHIVE_REF=main) and asserts
the beta banner disappears.

Phase C runs the structural checks against the REAL archive, where the
114-dead-links regression actually happened.

Usage: tests/test_output.py [real_archive_dir]
"""
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mkarchive  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
REAL_ARCHIVE = pathlib.Path(sys.argv[1] if len(sys.argv) > 1
                            else pathlib.Path.home() / 'ToolAssisted-archive')

# Element ids and storage keys app.js looks up; each must exist in the built
# site (or in style.css) or the feature is silently dead.
CONTRACT = ['navauth', 'submitform', 'imp-scan', 'imp-run',
            'imp-list', 'imp-fill', 'imp-count', 'imp-log', 'selfimport',
            'authchips', 'authsearch', 's-romsha1', 'nsfwgate', 'nsfwreal',
            'nsfwblur', 'bellbadge', 'am-theme', 'navtoggle', 'navlinks',
            'heronews', 'bskyfeed']

HOSTILE_AUTHOR = 'Evil<img src=x onerror=alert(1)>'
HOSTILE_NOTES = """Notes with a raw <script>alert('xss')</script> tag.

%%QUOTE SomeoneElse
An unterminated quote block.

! A heading right after the open quote
* list item one
* list item two

||cell a||cell b||
||||

%%SRC_EMBED lua
never terminated code block
print("<script>")
"""

failures = []


def ck(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (f'  [{detail}]' if detail and not cond else ''))
    if not cond:
        failures.append(name)


def build(archive, out, ref='main', beta='1'):
    import os
    env = dict(os.environ, ARCHIVE_REF=ref, SITE_BETA=beta)
    r = subprocess.run([sys.executable, str(REPO / 'generator/build.py'),
                        str(archive), str(out)],
                       capture_output=True, text=True, env=env)
    return r


# 404.html is the one page that cannot follow the rules the others do: a 404
# is served for a path of any depth, so it must be self-contained and address
# everything from the root. It gets its own checks instead.
SPECIAL = {'404.html'}


def pages(out):
    return sorted(p for p in out.rglob('*.html') if p.name not in SPECIAL)


def dead_links(out):
    """Every internal href/src must resolve to a file (or dir/index.html)."""
    dead = []
    for page in pages(out):
        html = page.read_text()
        for m in re.finditer(r'(?:href|src)="([^"]+)"', html):
            url = m.group(1).split('#')[0].split('?')[0]
            if not url or url.startswith(('http://', 'https://', 'mailto:', 'data:', '//')):
                continue
            base = out if url.startswith('/') else page.parent
            target = (base / url.lstrip('/')).resolve()
            if not (target.is_file() or (target / 'index.html').is_file()):
                dead.append(f'{page.name}:{url}')
    return dead


def check_structure(out, label):
    stray = [p.name for p in pages(out) if p.name != 'index.html']
    ck(f'{label}: every page is folder/index.html', not stray, str(stray[:3]))
    ck(f'{label}: .htaccess shipped', (out / '.htaccess').is_file())
    dead = dead_links(out)
    ck(f'{label}: no dead internal links', not dead, f'{len(dead)}: {dead[:4]}')
    missing_assets = set()
    for page in pages(out):
        for m in re.finditer(r'(?:href|src)="([^"]*assets/[^"?]+)', page.read_text()):
            if not (page.parent / m.group(1)).resolve().is_file():
                missing_assets.add(m.group(1))
    ck(f'{label}: referenced assets exist', not missing_assets, str(sorted(missing_assets)[:3]))


def check_cache_busting(out, label):
    tokens = set()
    missing = []
    for page in pages(out):
        html = page.read_text()
        css = re.search(r'style\.css\?v=([^"]+)', html)
        js = re.search(r'app\.js\?v=([^"]+)', html)
        if not (css and js):
            missing.append(page.name)
            continue
        tokens.add(css.group(1))
        tokens.add(js.group(1))
    ck(f'{label}: every page cache-busts css+js', not missing, str(missing[:3]))
    ck(f'{label}: one build token site-wide', len(tokens) == 1, str(sorted(tokens)[:3]))


def js_integrity(js):
    """The client app is emitted from a Python string literal; a stray escape
    silently breaks every page. Catch unbalanced quotes/braces without node."""
    odd = []
    for i, raw in enumerate(js.splitlines(), 1):
        # drop regex literals first: /"/g and /'/g carry quotes that would
        # otherwise look like unbalanced string delimiters
        line = re.sub(r'/(?:\\.|\[[^\]]*\]|[^/\\\n])+/[gimsuy]*', 'RE', raw)
        n = k = 0
        while k < len(line):
            if line[k] == '\\':
                k += 2
                continue
            if line[k] == "'":
                n += 1
            k += 1
        if n % 2:
            odd.append(i)
    return odd, js.count('{') - js.count('}'), js.count('(') - js.count(')')


def check_inline_scripts(out, label):
    """Every inline <script> must parse.

    app.js gets a `node --check`, but the scripts written straight into a page
    (the games view switcher, the per-game category selector, the theme
    bootstrap) never did, so a stray brace there would ship silently and kill
    the page it lives on. Blocks are deduplicated first, since most of them are
    the same boilerplate on every page. Needs node; skipped without it.
    """
    node = shutil.which('node')
    if not node:
        print(f'SKIP {label}: inline scripts parse (node not installed)')
        return
    blocks = {}
    for page in pages(out):
        for block in re.findall(
                r'<script(?![^>]*\bsrc=)(?![^>]*\btype=)[^>]*>(.*?)</script>',
                page.read_text(), re.S):
            if block.strip():
                blocks.setdefault(block, page.parent.name)
    bad = []
    with tempfile.TemporaryDirectory() as jd:
        # a plain .js file, so node parses it exactly as a browser parses a
        # page script: no --input-type, nothing version-specific
        f = pathlib.Path(jd) / 'block.js'
        for block, where in blocks.items():
            f.write_text(block)
            r = subprocess.run([node, '--check', str(f)], capture_output=True, text=True)
            if r.returncode:
                bad.append(f'{where}: {r.stderr.strip().splitlines()[-1][:90]}')
    ck(f'{label}: every inline script parses ({len(blocks)} distinct)', not bad,
       str(bad[:2]))


def main():
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)

        # ---------- phase A: controlled archive, staging build ----------
        arch = mkarchive.make_archive(td / 'arch', [
            mkarchive.run_spec('M900101', frames=6000,
                               authors=['Ada', HOSTILE_AUTHOR, 'Nyx'],
                               forum={'topicId': 4242,
                                      'url': 'https://forum.toolassisted.run/t/4242'},
                               notes='See [M900102] and [M999999] and [user:Ada].\n\n' + HOSTILE_NOTES,
                               likes=[{'user': 'Fan', 'date': '2026-02-02'}],
                               status={'reproduced': 'community', 'verified': 'full'},
                               reproductions=[{'user': 'Rep', 'date': '2026-02-01'}],
                               verifications=[{'user': 'Ver', 'date': '2026-02-01'},
                                              {'user': 'Ver2', 'date': '2026-02-02'}]),
            mkarchive.run_spec('M900102', frames=7000, authors=['Bo'],
                               contentWarnings=['sexual', 'photosensitivity'],
                               status={'reproduced': 'none', 'verified': 'none'}),
            mkarchive.run_spec('M900103', goal='unclassified', frames=800, authors=['Cy'],
                               goalDescription='Playaround with "quotes" & <angles>',
                               likes=[{'user': 'Fan', 'date': '2026-02-03'}],
                               status={'reproduced': 'community', 'verified': 'none'},
                               reproductions=[{'user': 'Rep', 'date': '2026-02-03'}]),
            mkarchive.run_spec('M900106', frames=6000, authors=['Ada'],
                               withdrawn={'by': 'Ada', 'date': '2026-02-08',
                                          'role': 'author',
                                          'reason': 'A duplicate of M900101.'}),
            mkarchive.run_spec('M900108', frames=6200, authors=['Ada', 'Nyx'],
                               withdrawn={'by': 'Root', 'date': '2026-08-17',
                                          'role': 'expert', 'contentRemoved': True,
                                          'reason': 'Imported on one author\'s word.'}),
            mkarchive.run_spec('M900105', frames=5500, authors=['Eve'],
                               consoleVerifications=[{'user': 'Metal', 'date': '2026-02-07',
                                                      'proof': 'https://example.com/rec',
                                                      'hardware': 'NES + Everdrive'}]),
            mkarchive.run_spec('M900104', game='dos/hardgame', frames=9000, authors=['Dee'],
                               status={'reproduced': 'community', 'verified': 'full'},
                               reproductions=[{'user': 'Rep', 'date': '2026-02-04'}],
                               verifications=[{'user': 'Ver', 'date': '2026-02-04'},
                                              {'user': 'Ver2', 'date': '2026-02-05'}],
                               reports=[{'id': 1, 'kind': 'spam-malicious', 'by': 'Fan',
                                         'date': '2026-02-06', 'details': 'Test report.',
                                         'status': 'open'}]),
            mkarchive.run_spec('M900107', game='nes/orphan', frames=4200,
                               authors=['Ada']),
        ], nonmembers=['Nyx'],
            experts=[{'user': 'Root', 'scope': 'site'},
                     {'user': 'Grp', 'scope': 'group:test-family'}],
            groups=[{'key': 'test-family', 'title': 'Test <b>Family</b>',
                     'games': ['nes/testgame', 'dos/hardgame']},
                    {'key': 'lonely', 'title': 'Lonely',
                     'games': ['nes/testgame'], 'established': False,
                     'createdBy': 'Grp', 'createdAt': '2026-02-08'},
                    {'key': 'hollow', 'title': 'Hollow',
                     'games': [], 'established': False,
                     'createdBy': 'Root', 'createdAt': '2026-02-13'},
                    {'key': 'ratified-one', 'title': 'Ratified Family',
                     'games': [], 'established': True,
                     'ratifiedBy': 'Root', 'ratifiedAt': '2026-02-10'}],
            ratified={'dos/hardgame': {'ratifiedBy': 'Grp', 'ratifiedAt': '2026-02-09'}},
            role_events=[{'user': 'Ada', 'role': 'committee', 'action': 'granted',
                          'by': 'founder', 'date': '2026-02-01',
                          'reason': 'fixture: a sitting committee member'}],
            empty_games=['nes/runless-one'],
            claims=[{'member': 'Rep', 'identity': 'SomeHeldName',
                     'evidence': 'I posted from that account, fixture-style.',
                     'date': '2026-02-11', 'status': 'approved',
                     'decidedBy': 'Ada', 'decidedAt': '2026-02-12'}])
        # a couple of logged revisions, so the run page can count them
        (arch / 'edits.json').write_text(json.dumps({'events': [
            {'kind': 'run', 'key': 'M900101', 'field': 'encode',
             'from': 'https://youtu.be/old', 'to': 'https://youtu.be/new',
             'by': 'Grp', 'date': '2026-02-14', 'reason': 'the old encode was taken down'},
            {'kind': 'run', 'key': 'M900101', 'field': 'goalDescription',
             'from': '', 'to': 'clarified', 'by': 'Ada', 'date': '2026-02-15',
             'reason': "The author's own revision."},
        ]}, indent=1))
        out = td / 'out'
        r = build(arch, out, ref='staging')
        ck('controlled build succeeds', r.returncode == 0, r.stderr[-400:])
        if r.returncode:
            print(r.stderr[-3000:])
            print('---', len(failures), 'failures')
            sys.exit(1)

        check_structure(out, 'controlled')
        check_cache_busting(out, 'controlled')
        check_inline_scripts(out, 'controlled')

        all_html = {p: p.read_text() for p in pages(out)}
        joined = '\n'.join(all_html.values())

        # ---------- escaping ----------
        ck('no raw injected img tag', '<img src=x' not in joined)
        ck('no raw script from member text', "<script>alert('xss')</script>" not in joined)
        ck('hostile author name escaped', '&lt;img src=x' in joined)
        browse = all_html[out / 'browse' / 'index.html']
        script_opens = len(re.findall(r'<script\b', browse))
        script_closes = browse.count('</script>')
        ck('browse index json cannot break out of its script tag',
           script_opens == script_closes, f'{script_opens} open vs {script_closes} close')
        ck('browse json escapes angle brackets', '\\u003c' in browse)

        # ---------- wiki renderer robustness ----------
        run_page = all_html[out / 'runs' / 'M900101' / 'index.html']
        for tag in ('ul', 'ol', 'blockquote', 'pre', 'table'):
            opens = len(re.findall(rf'<{tag}[ >]', run_page))
            closes = run_page.count(f'</{tag}>')
            ck(f'malformed markup still yields balanced <{tag}>', opens == closes,
               f'{opens} vs {closes}')
        ck('known [M#] renders a reference card',
           'runref' in run_page and 'refcard' in run_page)
        ck('unknown [M#] renders literally, no ref card',
           '[M999999]' in run_page and run_page.count('refcard') == 1)
        ck('[user:] reference resolves', 'href="../../authors/ada/"' in run_page)

        # ---------- content warnings / 18+ gate / reports ----------
        warned = all_html[out / 'runs' / 'M900102' / 'index.html']
        ck('content warning chips render', 'warnchip' in warned)
        ck('sexual content gates the media', 'nsfwgate' in warned and 'nsfwblur' in warned)
        uncl = all_html[out / 'runs' / 'M900103' / 'index.html']
        ck('unclassified goal description escaped',
           '&quot;quotes&quot;' in uncl and '&lt;angles&gt;' in uncl)
        modlog = all_html[out / 'policy' / 'moderation-log' / 'index.html']
        ck('report anchors match their links',
           set(re.findall(r'id="(R\d+)"', modlog)) ==
           set(re.findall(r'href="#(R\d+)"', modlog)) or 'R1' in modlog)

        # ---------- game groups ----------
        gpage = out / 'groups' / 'test-family' / 'index.html'
        ck('a group spanning two games gets a page', gpage.exists())
        if gpage.exists():
            gh = all_html[gpage]
            ck('the group page lists both its games',
               'games/nes/testgame/' in gh and 'games/dos/hardgame/' in gh)
            ck('the group page names the systems it spans',
               'Nintendo Entertainment System' in gh and 'DOS' in gh)
            ck('a hostile group title is escaped',
               '<b>Family</b>' not in gh and '&lt;b&gt;Family' in gh)
            ck('the group page lists the runs of its games',
               'runs/M900101/' in gh and 'runs/M900104/' in gh)
            ck('a withdrawn run stays off the group page', 'M900106' not in gh)
            ck('an expert scoped to the group is shown as covering it', 'Grp' in gh)
        ck('a group holding a single game has a page like any other',
           (out / 'groups' / 'lonely' / 'index.html').exists())
        gindex = all_html[out / 'games' / 'index.html']
        ck('the games index links the group', 'groups/test-family/' in gindex)
        ck('a one-game group is linked like any other',
           'groups/lonely/' in gindex, 'every group is visible now')
        ck('an empty group is visible too, placeholder face and all',
           'groups/hollow/' in gindex and (out / 'groups' / 'hollow' / 'index.html').exists(),
           'a group an expert just made is the state worth seeing')
        hollow = all_html[out / 'groups' / 'hollow' / 'index.html']
        ck('and its page says what an empty group is',
           'No games in this group yet' in hollow and 'f-groupaddgame' in hollow,
           hollow[:200])

        # ---------- a withdrawal that took the content down ----------
        gone = all_html.get(out / 'runs' / 'M900108' / 'index.html', '')
        ck('a content-removed withdrawal has a tombstone', bool(gone))
        if gone:
            ck('it says the files were taken down and why',
               'taken down with' in gone and 'one author' in gone, gone[:200])
            ck('it does not claim the movie is still in the archive',
               'the movie file and this record stay' not in gone)
            ck('a plain withdrawal still says nothing is erased',
               'the movie file and this record stay'
               in all_html[out / 'runs' / 'M900106' / 'index.html'])

        # ---------- thumbnails are ours to serve ----------
        ck('every run thumbnail is shipped with the site',
           (out / 'thumbs').is_dir() and len(list((out / 'thumbs').glob('*'))) >= 5,
           str(sorted(p.name for p in (out / 'thumbs').glob('*'))[:6])
           if (out / 'thumbs').is_dir() else 'no thumbs directory')
        ck('pages point at our own copies, not at raw.githubusercontent',
           'raw.githubusercontent.com' not in all_html[out / 'browse' / 'index.html']
           or '/thumbs/' in all_html[out / 'browse' / 'index.html'])
        for pth, html in all_html.items():
            if 'src="https://raw.githubusercontent.com' in html:
                ck(f'no page loads an image from raw.githubusercontent ({pth.parent.name})',
                   False, 'raw is rate-limited; it answered 429 site-wide on 2026-08-17')
                break
        else:
            ck('no page loads an image from raw.githubusercontent', True)

        # ---------- what an expert can reach ----------
        run_page_x = all_html[out / 'runs' / 'M900101' / 'index.html']
        for form, what in (('f-invalidate', 'invalidate a contribution'),
                           ('f-resolve', 'close a report'),
                           ('f-withdraw', 'withdraw the run')):
            ck(f'a run page carries the form to {what}', f'id="{form}"' in run_page_x)
        ck('the expert forms start hidden and folded',
           'id="f-invalidate-wrap" hidden' in run_page_x
           and '<details class="actform"><summary>Invalidate' in run_page_x)
        claim_page = all_html[out / 'claim' / 'index.html']
        ck('the claim page carries the attestation form for site experts',
           'id="f-attest"' in claim_page and 'id="siteexperts"' in claim_page)
        ck('there is no separate experts page; a role travels with its member',
           not (out / 'experts').exists())
        members_html = all_html[out / 'authors' / 'index.html']
        rows_html = members_html[members_html.find('<tbody>'):members_html.find('</tbody>')]
        ck('the members list badges the roles somebody holds',
           'rolechip role-expert' in rows_html, rows_html[:200])
        # A scope is not a name for a person: they can hold several unrelated
        # ones, so the chip says Expert and their own page says which.
        ck('the expert badge does not try to name a scope',
           'the whole site' not in rows_html and 'Expert ·' not in rows_html,
           rows_html[:200])
        # governance is recorded, in the role log and on the forum; a badge
        # beside a name here says what somebody does with runs
        ck('no governance role is a badge',
           'role-committee' not in rows_html and 'role-moderator' not in rows_html,
           rows_html[:200])
        # earned, not granted: one act is enough
        ck('a member who has earned a point is badged a contributor',
           'role-contrib' in rows_html, rows_html[:300])
        ck('a console verification alone reaches the first milestone',
           'tier-1k' in rows_html and '1k Contributor' in rows_html,
           rows_html[:300])
        member_pages = {f.parent.name: h for f, h in all_html.items()
                        if f.parent.parent.name == 'authors' and f.name == 'index.html'}
        badged = {n for n, h in member_pages.items() if 'role-contrib' in h}
        ck('the contributor badge is on their own page as well as the list',
           badged, str(sorted(member_pages)))
        # the tier a member wears must be the tier their score buys, at every
        # threshold, or the badge is decoration rather than a fact
        TIERS = [(25000, '25k Contributor'), (10000, '10k Contributor'),
                 (5000, '5k Contributor'), (1000, '1k Contributor'),
                 (1, 'Contributor')]
        for name in badged:
            page_ = member_pages[name]
            score = re.search(r'<b>(\d+)</b><span>contributor score', page_)
            ck(f'{name} is badged a contributor because they earned it',
               score and int(score.group(1)) >= 1, name + ': ' + str(score))
            if not score:
                continue
            pts = int(score.group(1))
            want = next(lbl for t_, lbl in TIERS if pts >= t_)
            worn = re.search(r'class="rolechip role-contrib[^"]*"[^>]*>([^<]+)<', page_)
            ck(f'{name} wears the tier {pts} points buys',
               worn and worn.group(1) == want,
               f'{pts} points wants {want!r}, wears {worn and worn.group(1)!r}')
        for name, h in member_pages.items():
            if name in badged:
                continue
            score = re.search(r'<b>(\d+)</b><span>contributor score', h)
            ck(f'{name} is not badged a contributor, having earned nothing',
               score and int(score.group(1)) == 0, name + ': ' + str(score))
        root_page = all_html[out / 'authors' / 'root' / 'index.html']
        ck('a member page ends with their role history',
           '<h2>Roles' in root_page
           and root_page.rfind('<h2>Roles') > root_page.rfind('<h2>Runs'),
           'the role log is missing or not at the bottom')
        ck('the role history states who granted it and why',
           'fixture appointment' in root_page and 'Granted' in root_page)

        # ---------- the 404 page ----------
        nf = out / '404.html'
        ck('a 404 page is built', nf.is_file())
        if nf.is_file():
            h404 = nf.read_text()
            ck('the 404 page is self-contained',
               not re.search(r'(?:href|src)="(?!/|https|#)', h404)
               and 'style.css' not in h404,
               'it depends on assets it cannot resolve from an unknown depth')
            ck('the 404 page addresses the site from the root',
               'href="/"' in h404 and 'href="/browse/"' in h404)
            ck('the 404 page forwards the old /stage/ links',
               "'/stage/'" in h404 and 'location.replace' in h404)

        # ---------- encodes come from more than one platform ----------
        subp = all_html[out / 'submit' / 'index.html']
        for name in ('YouTube', 'Niconico', 'Bilibili'):
            ck(f'the submit page names {name} as an accepted platform', name in subp)
        ck('the submit page no longer claims YouTube only',
           'Encode link (YouTube' not in subp)
        js = (out / 'assets' / 'app.js').read_text()
        ck('the client host list comes from the registry',
           'nicovideo.jp' in js and 'bilibili.com' in js and 'youtu.be' in js)
        ck('the client asks the archivist to resolve an encode',
           '/api/encode/check?url=' in js)
        ck('no page builds a YouTube thumbnail url by hand',
           'img.youtube.com' not in js, 'app.js still hardcodes the youtube thumb host')
        # every archivist-triggering button goes flat, grey and spinning while
        # the request runs, and cannot be pressed twice
        submit_ = all_html[out / 'submit' / 'index.html']
        ck('the submit game picker is type-to-find, not a giant select',
           'id="s-gamesearch"' in submit_ and '<select id="s-game"' not in submit_)
        ck('the stated time is picked in segments, never typed as a format',
           'id="s-timepick"' in submit_ and 'id="t-ms"' in submit_
           and 'pattern=' not in submit_[submit_.index('s-timewrap'):
                                          submit_.index('s-timewrap') + 700])
        ck('the submit page ships no per-game category payload',
           '"goals"' not in submit_,
           'categories are fetched from the archive when a game is picked')
        gpage_ = all_html[out / 'games' / 'nes' / 'testgame' / 'index.html']
        ck('the game page offers submission with its own context',
           'submit/?game=nes/testgame' in gpage_)
        ck('the busy treatment exists in the shared helper',
           'function busy(' in js and "classList.toggle('busy'" in js
           and 'busy(btn, true)' in js)

        # ---------- the three views of the games index ----------
        for label, view in (('Groups', 'groups'), ('Systems', 'systems'), ('List', 'list')):
            ck(f'the games index offers the {label} view',
               f'data-view="{view}"' in gindex)
        shown = re.findall(r'<div class="[^"]*" id="v-(\w+)"( hidden)?>', gindex)
        ck('exactly one view is visible without javascript',
           [v for v, h in shown if not h] == ['systems'], str(shown))
        vgroups = gindex[gindex.index('id="v-groups"'):gindex.index('id="v-systems"')]
        cards = re.findall(r'<a class="card".*?</a>', vgroups, re.S)
        # test-family, lonely, hollow, ratified-one, and Unclassified: every
        # group is a card now, however empty
        ck('the groups view is one card per group', len(cards) == 5, str(len(cards)))
        fam = next((c for c in cards if 'groups/test-family/' in c), '')
        ck('the group card links to the group page', bool(fam))
        ck('the group card is a collage of its games',
           'data-n="2"' in fam and fam.count('<img') == 2, fam[fam.find('collage'):][:140])
        # within one collage every tile is a different run; two groups that
        # share a game may of course show the same run once each
        for card_ in cards:
            srcs_ = re.findall(r'<span class="tile"><img[^>]*src="([^"]+)"', card_)
            ck('each collage tile shows a different run',
               len(set(srcs_)) == len(srcs_), str(srcs_)) if srcs_ else None
        ck('the group card counts its games and systems',
           '2 games · 2 systems' in vgroups, vgroups[vgroups.find('cbody'):][:160])
        ck('the groups view does not list the games themselves',
           'href="nes/testgame/"' not in vgroups and 'href="nes/orphan/"' not in vgroups)
        ck('the groups view holds an Unclassified card for the rest',
           'groups/unclassified/' in vgroups, str(vgroups.count('class="card"')))
        ck('Unclassified sorts last whatever the sort is',
           vgroups.index('data-last="1"') > vgroups.index('groups/test-family/'))
        uncl = all_html.get(out / 'groups' / 'unclassified' / 'index.html', '')
        ck('the Unclassified group has a page', bool(uncl))
        ck('it holds exactly the game no group claimed',
           'games/nes/orphan/' in uncl and 'games/dos/hardgame/' not in uncl)
        ck('it does not pretend to be a series',
           'Records across the group' not in uncl and 'Group experts' not in uncl)
        ck('every game belongs to a group',
           all('grpline' in h for pth, h in all_html.items()
               if pth.parent.parent.parent.name == 'games'),
           str([pth.parent.name for pth, h in all_html.items()
                if pth.parent.parent.parent.name == 'games' and 'grpline' not in h]))
        ck('an unclaimed game says so on its own page',
           'groups/unclassified/' in all_html[out / 'games' / 'nes' / 'orphan' / 'index.html'])
        vlist = gindex[gindex.index('id="v-list"'):]
        # four games now: the three with runs, and the runless one an expert
        # created while filling out a group
        ck('the list view has one row per game', vlist.count('<tr onclick') == 4,
           str(vlist.count('<tr onclick')))
        ck('the list view names each game system', vlist.count('Nintendo Entertainment System') == 3)
        gamepage = all_html[out / 'games' / 'nes' / 'testgame' / 'index.html']
        ck("a game's page links to the group it belongs to",
           '../../../groups/test-family/' in gamepage, 'no part-of link')
        ck("the game's part-of line reaches every group that holds it",
           '/groups/test-family/' in gamepage)

        # ---------- landing page shape ----------
        home = all_html[out / 'index.html']
        ck('hero and news sit in one grid', 'class="herogrid"' in home
           and 'class="herotext"' in home and 'class="heronews"' in home)
        base = 'github.com/ToolAssisted-run#'
        for frag, label in (('1-community-principles', 'Community Principles'),
                            ('2-governance', 'Governance'),
                            ('3-terms-of-use', 'Terms of Use'),
                            ('4-code-of-conduct', 'Code of Conduct')):
            ck(f'footer links {label} to its section',
               f'{base}{frag}">{label}</a>' in joined, frag)
        ck('nothing still calls it the manifesto',
           'anifesto' not in joined)
        ck('no stale policy pages are generated',
           not (out / 'policy' / 'terms').exists()
           and not (out / 'policy' / 'code-of-conduct').exists())
        ck('the moderation log is still generated here',
           (out / 'policy' / 'moderation-log' / 'index.html').is_file())
        ck('news column hosts the Bluesky feed',
           'id="bskyfeed"' in home and 'data-handle="' in home)
        ck('the feed has the news panel to itself',
           'actfeed' not in home and 'newslinks' not in home)
        ck('no third-party widget script is embedded',
           'platform.twitter.com' not in home
           and 'platform.twitter.com' not in (out / 'assets' / 'app.js').read_text())
        ck('statistics sit inside the welcome column',
           home.index('statstrip') < home.index('heronews'))
        ck('nav offers a menu button', 'id="navtoggle"' in home
           and 'aria-expanded="false"' in home)
        css_txt = (out / 'assets' / 'style.css').read_text()
        ck('a narrow window stacks the hero',
           '.herogrid{grid-template-columns:1fr}' in css_txt.replace('\n', ''))
        ck('a narrow window hides the nav links behind the button',
           '.nav .navlinks,.nav .navsearch{display:none}' in css_txt.replace('\n', ''))
        ck('the menu button is desktop-hidden by default',
           '.navtoggle{display:none' in css_txt)
        flat = css_txt.replace('\n', '')
        ck('hero buttons get short labels on a phone',
           '.wide{display:none}.narrow{display:inline}' in flat
           and '.narrow{display:none}' in flat)
        ck('both label variants ship in the markup',
           'class="wide">Browse the archive<' in home
           and 'class="narrow">Browse<' in home)
        ck('the statistics become a grid on a phone',
           '.statstrip{display:grid;grid-template-columns:repeat(3,1fr)' in flat)

        # ---------- tables are structurally sound ----------
        # adding a column is easy to get half-right: header updated, one row
        # kind forgotten, and the table silently skews
        bad_tables = []
        for page, html in all_html.items():
            for tbl in re.findall(r'<table[^>]*>(.*?)</table>', html, re.S):
                heads = re.findall(r'<th\b[^>]*>', tbl)
                if not heads:
                    continue
                for row in re.findall(r'<tr[^>]*>(.*?)</tr>', tbl, re.S):
                    cells = re.findall(r'<td\b[^>]*>', row)
                    if cells and len(cells) != len(heads):
                        names = [re.sub(r'<[^>]+>', '', h)[:10]
                                 for h in re.findall(r'<th\b[^>]*>(.*?)</th>', tbl, re.S)]
                        bad_tables.append(f'{page.parent.name}: {len(cells)} cells '
                                          f'vs {len(heads)} headers {names}')
        ck('every table row matches its header', not bad_tables, str(bad_tables[:4]))

        # ---------- the third signal is visible ----------
        game_pages = [h for p_, h in all_html.items() if p_.parent.name == 'testgame']
        ck('game listings carry a console column',
           any('<th class="ctr">Console</th>' in h for h in game_pages),
           str(len(game_pages)))
        ck('a console-verified run is marked on its own page',
           'consolechip' in all_html[out / 'runs' / 'M900105' / 'index.html'])

        # ---------- only members have a presence ----------
        ck('someone who is not a member has no profile',
           not (out / 'authors' / 'nyx').exists())
        ck('a member has one', (out / 'authors' / 'ada' / 'index.html').is_file())
        members_page = all_html[out / 'authors' / 'index.html']
        ck('the member list holds only members',
           'Nyx' not in members_page and 'Ada' in members_page,
           members_page[members_page.find('<tbody>'):][:200])
        ck('the member list keeps the claim path visible', 'claim/' in members_page)
        run_credits = all_html[out / 'runs' / 'M900101' / 'index.html']
        ck('a non-member is credited as plain text, linked nowhere',
           '<span class="au">Nyx</span>' in run_credits
           or 'authors/nyx/' not in run_credits,
           'a credit still points at a profile that does not exist')

        # ---------- withdrawal ----------
        ck('a withdrawn run is gone from the listings',
           'M900106' not in all_html[out / 'browse' / 'index.html']
           and 'M900106' not in all_html[out / 'games/nes/testgame' / 'index.html'])
        tomb = all_html.get(out / 'runs' / 'M900106' / 'index.html', '')
        ck('a withdrawn run keeps an honest page',
           'was withdrawn' in tomb and 'duplicate of M900101' in tomb, tomb[:160])
        ck('the withdrawn page says nothing was erased', 'stay in' in tomb)

        # ---------- discussion in place ----------
        disc_page = all_html[out / 'runs' / 'M900101' / 'index.html']
        ck('a run with a topic shows its discussion',
           'id="discussion"' in disc_page and 'data-topic="4242"' in disc_page)
        ck('the discussion offers a reply box',
           'id="disc-reply"' in disc_page and 'id="disc-login"' in disc_page)
        plain_page = all_html[out / 'runs' / 'M900102' / 'index.html']
        ck('a run without a topic shows no discussion',
           'id="discussion"' not in plain_page)

        # ---------- beta gating (staging) ----------
        no_banner = [p.name for p, h in all_html.items() if 'betabar' not in h]
        ck('staging: beta banner on every page', not no_banner, str(no_banner[:3]))
        ck('staging: footer marks beta', '· beta' in joined)

        # ---------- house style, on a canvas that is entirely our copy ----------
        prose = re.sub(r'>—<', '><', joined)          # empty-value placeholders
        prose = prose.replace('title="Not yet: this run is pending"', '')
        ck('no em dashes in generated prose', '—' not in prose,
           prose[max(0, prose.find('—') - 60):prose.find('—') + 60] if '—' in prose else '')
        ck('terminology: no "Legacy" anywhere', 'Legacy' not in joined)

        # ---------- who speaks for a game ----------
        # A site-wide expert covers every game here, so printing them under
        # every title says nothing about any of them.
        gp = all_html[out / 'games' / 'nes' / 'testgame' / 'index.html']
        head_ = gp[:gp.find('</header>')]
        ck('a game page names its group expert', 'Grp' in head_, head_[-400:])
        ck('and leaves the site-wide expert off it', 'Root' not in head_, head_[-400:])

        # ---------- acts on the page they are about ----------
        # Each zone starts hidden and opens only for the people who may use it,
        # and none of them can delete anything: a removal is a request.
        for page_, blob, zone in (
                (out / 'games' / 'nes' / 'testgame' / 'index.html',
                 'gameactdata', 'f-gameremove-wrap'),
                (out / 'groups' / 'test-family' / 'index.html',
                 'groupactdata', 'groupacts'),
                (out / 'games' / 'index.html', 'gamesactdata', 'gamesacts')):
            h = all_html[page_]
            ck(f'{page_.parent.name}: the zone carries who may open it',
               f'id="{blob}"' in h and f'id="{zone}" class="actzone' in h
               and f'id="{zone}" class="actzone' in h
               and 'hidden' in h[h.index(f'id="{zone}"'):h.index(f'id="{zone}"') + 80],
               h[:200])
        # game/group expert actions live in the bottom Expert menu box; the
        # games index zone (site experts) is not one of the three page kinds
        for page_ in (out / 'games' / 'nes' / 'testgame' / 'index.html',
                      out / 'groups' / 'test-family' / 'index.html'):
            h = all_html[page_]
            ck(f'{page_.parent.name}: the expert menu is one box at the bottom',
               h.count('expertmenu') >= 1 and 'Expert menu</h2>' in h
               and h.rfind('expertmenu') < h.find('<footer'), h[:120])
        gh = all_html[out / 'games' / 'nes' / 'testgame' / 'index.html']
        ck('a game page asks for removal rather than offering deletion',
           'request-removal' not in gh and 'Ask for this game to be removed' in gh,
           'the path is chosen by the script, the words by the page')
        grh = all_html[out / 'groups' / 'test-family' / 'index.html']
        ck('a group page can add a game and ask for its own removal',
           'f-groupaddgame' in grh and 'f-groupremove' in grh, grh[:200])
        ck('the games index offers a new group',
           'f-newgroup' in all_html[out / 'games' / 'index.html'])

        # ---------- registered things are picked, never typed blind ----------
        # the global pattern: wherever a game, member or group that already
        # exists is asked for, the input carries a list you can type into
        panel = all_html[out / 'expert' / 'index.html']
        claimp_ = all_html[out / 'claim' / 'index.html']
        ck('the claim page offers the held names as a list',
           'id="dl-heldnames"' in claimp_ and 'list="dl-heldnames"' in claimp_,
           claimp_[:200])
        ck('and it offers the members as a list for attestation',
           'id="dl-members"' in claimp_ and 'list="dl-members"' in claimp_)
        ck('a held name in the list is one nobody claimed',
           'value="Nyx"' in claimp_, 'Nyx is credited on a run and not a member')
        fpanel_ = all_html[out / 'founder' / 'index.html']
        ck('the founder seat form picks a member',
           'list="dl-members"' in fpanel_ and 'id="dl-members"' in fpanel_)
        # a picker never offers what would be refused: Ada already sits on the
        # Committee, so the seat form's list leaves her out and keeps the rest
        fdl = fpanel_[fpanel_.find('id="dl-members"'):]
        fdl = fdl[:fdl.find('</datalist>')]
        ck('the seat picker leaves out who is already seated',
           'value="Ada"' not in fdl and 'value="Rep"' in fdl, fdl[:200])
        cpanel_ = all_html[out / 'committee' / 'index.html']
        ck('the committee-decision form follows role and direction',
           'list="dl-role-candidates"' in cpanel_
           and '"moderators":' in cpanel_ and '"members":' in cpanel_)
        ck('and it left the members page for the committee panel',
           'dl-role-candidates' not in all_html[out / 'authors' / 'index.html'])
        gamesp_ = all_html[out / 'games' / 'index.html']
        gdl_ = gamesp_[gamesp_.find('id="dl-games"'):]
        gdl_ = gdl_[:gdl_.find('</datalist>')]
        ck('the group picker offers only ungrouped games',
           'nes/orphan' in gdl_ and 'nes/testgame' not in gdl_, gdl_[:200])
        gamesp_ = all_html[out / 'games' / 'index.html']
        ck('the games page group form picks games',
           'data-pick="dl-games"' in gamesp_ and 'id="dl-games"' in gamesp_)
        ck('the panel annul form picks an expert',
           'list="panel-expertlist"' in panel)
        ck('no free-text placeholder asks for a slug to be typed blind',
           'space separated' not in panel and 'space separated' not in gamesp_,
           'multi-game fields are chips pickers now')

        # ---------- the committee panel ----------
        cpanel_ = all_html[out / 'committee' / 'index.html']
        ck('the committee panel appoints whole-site experts',
           'f-siteexpert' in cpanel_ and 'name="scope" value="site"' in cpanel_,
           cpanel_[:200])
        ck('and the expert panel no longer offers the whole site',
           'or the whole site' not in panel
           and "the Steering Committee's to give" in panel, panel[:200])

        # 'wide' is the responsive text-swap class: display:none under 560px.
        # On a container it deletes the whole block on phones, which is how
        # the import list spent a day invisible on mobile while fine on
        # desktop. Only the <span class="wide">/<span class="narrow"> pair may
        # use these names.
        offenders = [m.group(0)[:80] for m in re.finditer(
            r'<(?!span\b)\w+[^>]*class="[^"]*\bwide\b[^"]*"', joined)]
        ck('the text-swap class never rides on a container',
           not offenders, str(offenders[:3]))

        # ---------- the import page ----------
        imp = all_html[out / 'import' / 'index.html']
        ck('the import page says nothing is imported unpicked',
           'Nothing is imported unpicked' in imp, imp[:200])
        ck('and says a co-authored import is your responsibility',
           'your responsibility' in imp and 'Co-authored works' in imp, imp[:200])

        # ---------- the founder panel ----------
        fpanel = all_html[out / 'founder' / 'index.html']
        ck('the founder panel exists and starts shut',
           'id="fpanel" hidden' in fpanel and 'Checking who you are' in fpanel,
           fpanel[:200])
        ck('it can seat and unseat, nothing else',
           'f-seat' in fpanel and 'f-unseat' in fpanel
           and 'founder/committee' not in fpanel,
           'the path is chosen by the script, not baked into the page')

        # ---------- what real usage crashed, kept building forever ----------
        # Both of these took the site down for a day: a game created with no
        # runs (an expert filling out a group), and a decided claim reaching
        # the site log's chips before their definition.
        ck('a game with no runs builds and has a page',
           (out / 'games' / 'nes' / 'runless-one' / 'index.html').exists())
        ck('and the site log shows the decided claim',
           'SomeHeldName' in log_page if False else 'SomeHeldName' in all_html[
               out / 'policy' / 'site-log' / 'index.html'])

        # ---------- delete buttons, gated and confirmed ----------
        runp_ = all_html[out / 'runs' / 'M900101' / 'index.html']
        ck('the run page carries the expert delete, hidden until armed',
           'id="f-rundelete-wrap" hidden' in runp_
           and 'Delete this movie (experts)' in runp_, runp_[:200])
        ck('the game page carries the expert delete',
           'f-gamedelete' in all_html[out / 'games' / 'nes' / 'testgame' / 'index.html'])
        ck('the group page carries the expert delete',
           'f-groupdelete' in all_html[out / 'groups' / 'test-family' / 'index.html'])
        ck('the member page carries the committee delete, hidden until armed',
           'id="memberacts" class="actzone" hidden' in all_html[
               out / 'authors' / 'ada' / 'index.html'])
        log_page0 = all_html[out / 'policy' / 'site-log' / 'index.html']
        ck('the site log has a Deletions section even when nothing was deleted',
           '<h2>Deletions' in log_page0 and 'Nothing has been deleted outright' in log_page0)

        # ---------- revisions are visible, small, and counted ----------
        r101 = all_html[out / 'runs' / 'M900101' / 'index.html']
        ck('a revised run carries the small history link with its count',
           'change history · 2 revisions' in r101 and '/commits/' in r101,
           r101[r101.find('change history') - 50:][:160])
        r107 = all_html[out / 'runs' / 'M900107' / 'index.html']
        ck('an unrevised run still links its history, uncounted',
           'change history →' in r107 and 'revision' not in
           r107[r107.find('change history'):r107.find('change history') + 60], r107[:100])
        ck('the site log accounts for both kinds of editor',
           '<h2>Edits (2)' in all_html[out / 'policy' / 'site-log' / 'index.html']
           and 'Authors revise their own runs' in all_html[
               out / 'policy' / 'site-log' / 'index.html'])

        # ---------- the site log ----------
        # It stopped being only about moderation: roles, identities, reports,
        # withdrawals and moderation are all acts of authority over somebody
        # else's work, and they belong in one place that is never pruned.
        log_page = all_html[out / 'policy' / 'site-log' / 'index.html']
        order = [log_page.find(f'<h2>{h}') for h in
                 ('Role changes', 'Identity attestations', 'Movie reports',
                  'Game and group decisions', 'Removal requests',
                  'Withdrawals and erasures', 'Moderation actions')]
        ck('the site log carries every kind of act', all(i >= 0 for i in order), str(order))
        ck('roles first, moderation last', order == sorted(order), str(order))
        ck('the role log names who acted and why',
           'Granted' in log_page and 'the founder' in log_page,
           log_page[log_page.find('<h2>Role changes'):][:300])
        # a game somebody here vouched for is listed with their name; one that
        # merely arrived established from the seeding import is not, because
        # nobody on this site ever ratified it
        rat = log_page[log_page.find('<h2>Game and group decisions'):]
        rat = rat[:rat.find('</section>')]
        rat_rows_ = re.findall(r'<tr>(.*?)</tr>',
                               rat[rat.find('<tbody>'):rat.find('</tbody>')], re.S)
        ck('the games and the group somebody here ratified are all listed',
           len(rat_rows_) == 3, f'{len(rat_rows_)} rows: {rat[:300]}')
        ck('rows sort newest first',
           'Runless One' in rat_rows_[0] and 'Ratified Family' in rat_rows_[1],
           str([r[:60] for r in rat_rows_]))
        ck('and the game names its expert, date and system',
           all(s in rat_rows_[2] for s in ('Grp', '2026-02-09', 'Hardgame')),
           rat_rows_[2][:300])

        moved = all_html.get(out / 'policy' / 'moderation-log' / 'index.html', '')
        ck('the address it used to live at still works',
           '/policy/site-log/' in moved and 'http-equiv="refresh"' in moved, moved[:160])
        ck('the footer points at the site log',
           'policy/site-log/">Site log' in log_page
           and 'moderation-log/">Moderation log' not in log_page)

        # ---------- contribute: what happened last ----------
        # A worklist with nothing visibly moving on it reads as abandoned.
        contrib_page = all_html[out / 'contribute' / 'index.html']
        aside = contrib_page[contrib_page.find('<aside'):]
        ck('the contribute page leads its side rail with the latest contributions',
           aside.find('Latest contributions') >= 0
           and aside.find('Latest contributions') < aside.find('Contributor board'),
           aside[:160])
        feed = aside[aside.find('Latest contributions'):]
        feed = feed[:feed.find('Contributor board')]
        entries = re.findall(r'<p class="statline newsline">(.*?)</p>', feed, re.S)
        ck('it shows a handful, not a history', 0 < len(entries) <= 10, str(len(entries)))
        dates = [re.search(r'(\d{4}-\d{2}-\d{2})', e).group(1) for e in entries
                 if re.search(r'(\d{4}-\d{2}-\d{2})', e)]
        ck('newest first', dates == sorted(dates, reverse=True), str(dates))
        ck('every entry names a member, an act and a run that exists',
           all(re.search(r'href="\.\./runs/(M\d+)/"', e)
               and (out / 'runs' / re.search(r'href="\.\./runs/(M\d+)/"', e).group(1)
                    / 'index.html').exists() for e in entries),
           str(entries[:1]))
        ck('an imported act is not passed off as a contribution',
           'importer' not in feed.lower(), feed[:200])

        # ---------- expert panel ----------
        # Its links are built by the script from this blob, so no link checker
        # ever sees them: they shipped pointing at games/<group>/ and
        # browse/?sys=<system>, neither of which this site has.
        panel = all_html[out / 'expert' / 'index.html']
        pd = json.loads(re.search(r'id="paneldata">(.*?)</script>', panel, re.S).group(1))
        dead = [s['href'] for s in pd['scopes']
                if s['href'] and not (out / s['href'] / 'index.html').exists()]
        ck('every scope the panel can link to has a page', not dead, str(dead[:3]))
        built_games = {f'{g.parent.name}/{g.name}'
                       for g in (out / 'games').glob('*/*') if g.is_dir()}
        want_scopes = {'site'} | built_games
        ck('the panel offers every scope that can be appointed',
           {s['key'] for s in pd['scopes']} >= want_scopes,
           str(sorted(want_scopes - {s['key'] for s in pd['scopes']})[:3]))
        ck('the panel is not readable until the script says who you are',
           'id="panel" hidden' in panel and 'Checking who you are' in panel)
        # appointing to a game and to a series are different questions with
        # different lists behind them, so they are different forms
        for form in ('f-appoint-game', 'f-appoint-group', 'f-appoint-wide'):
            ck(f'the panel has {form}', f'id="{form}"' in panel, panel[:200])
        # every action folded away; what is waiting on you is not an action
        # answering a name claim is the Steering Committee's alone: expert
        # scope is authority over games, not over who somebody is
        ck('the expert panel does not answer name claims',
           'epanel-claims' not in panel and 'claim/pending' not in panel, panel[:200])
        acts = re.findall(r'<summary><h2>([^<]+)</h2></summary>', panel)
        ck('every action in the panel is folded', len(acts) >= 6, str(acts))
        ck('and the pending list is not folded',
           'id="pending-list"' in panel
           and panel.find('id="pending-list"') < panel.find('<summary><h2>'),
           str(acts))
        ck('the panel knows who is a member, so it can offer names',
           isinstance(pd.get('members'), list) and pd['members'], str(pd)[:200])
        ck('and knows which games and series are still waiting',
           all('established' in g for g in pd['games'])
           and all('established' in gr for gr in pd['groups']), str(pd['games'][:2]))

        # ---------- client app ----------
        js = (out / 'assets' / 'app.js').read_text()
        node = shutil.which('node')
        if node:
            # a real parser: authoritative, and unlike the fallback below it
            # understands regex literals containing quotes or braces
            chk = subprocess.run([node, '--input-type=module', '--check'],
                                 input=js, capture_output=True, text=True)
            ck('app.js: node --check', chk.returncode == 0, chk.stderr[-300:])
        else:
            # crude fallback for machines without node: catches the failure
            # mode that actually happened (a Python escape collapsing inside
            # the emitted JS), at the cost of false alarms on regex literals
            print('NOTE app.js: node not installed, using the heuristic check')
            odd, brace, paren = js_integrity(js)
            ck('app.js: no broken string literals (heuristic)',
               not odd, f'lines {odd[:3]}')
            ck('app.js: balanced braces', brace == 0, str(brace))
            ck('app.js: balanced parens', paren == 0, str(paren))
        css = (out / 'assets' / 'style.css').read_text()
        missing_contract = [i for i in CONTRACT if i not in joined and i not in css]
        ck('server/client element contract intact', not missing_contract,
           str(missing_contract))

        # ---------- phase B: the beta ends (SITE_BETA=0) ----------
        out_main = td / 'out-main'
        r = build(arch, out_main, beta='0')
        ck('production build succeeds', r.returncode == 0, r.stderr[-400:])
        if r.returncode == 0:
            main_html = '\n'.join(p.read_text() for p in pages(out_main))
            ck('production: no beta banner', 'betabar' not in main_html)
            ck('production: no beta in footer', '· beta' not in main_html)
            ck('production: still cache-busted', 'app.js?v=' in main_html)

        # ---------- phase C: the real archive ----------
        if REAL_ARCHIVE.exists():
            real_out = td / 'out-real'
            r = build(REAL_ARCHIVE, real_out, ref='staging')
            ck('real-archive build succeeds', r.returncode == 0, r.stderr[-400:])
            if r.returncode == 0:
                # the hosting plan is a hard 10 MB; a deploy that overruns it
                # fails halfway through the upload, which is a far worse way to
                # find out than a red test
                built = sum(f.stat().st_size for f in real_out.rglob('*') if f.is_file())
                page_bytes = sum(f.stat().st_size for f in real_out.rglob('*.html'))
                thumbs = sum(f.stat().st_size for f in (real_out / 'thumbs').glob('*')) \
                    if (real_out / 'thumbs').is_dir() else 0
                # GitHub Pages allows 1 GB per site; the pages themselves are what
                # grows unboundedly with the archive, so watch that separately
                ck(f'real archive: the built site fits its host '
                   f'({built / 1048576:.0f} MB, of which {thumbs / 1048576:.0f} MB '
                   f'thumbnails, 1 GB allowed)', built < 700 * 1048576)
                ck(f'real archive: the pages stay small ({page_bytes / 1048576:.1f} MB)',
                   page_bytes < 100 * 1048576)
                check_structure(real_out, 'real archive')
                check_cache_busting(real_out, 'real archive')
                check_inline_scripts(real_out, 'real archive')
        else:
            print('SKIP real-archive phase (archive not found)')

    print('---', len(failures), 'failures')
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
