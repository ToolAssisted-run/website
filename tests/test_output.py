#!/usr/bin/env python3
"""Output invariants: properties every generated site must hold, whatever the
archive contains.

Phase A builds a fully-controlled archive (tests/mkarchive.py) carrying hostile
fixtures — an author name and notes full of markup, malformed wiki blocks,
cross-references to a missing run — and asserts escaping, link integrity, page
shape, cache busting and house style. Because every byte of that
archive is ours, "no em dash anywhere" and "no raw <script>" are meaningful
assertions rather than a lottery on member content.

Phase B rebuilds the same archive as production (ARCHIVE_REF=main) and asserts

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
            'authchips', 'authsearch', 'filerows', 'nsfwgate', 'nsfwreal',
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


def build(archive, out, ref='main'):
    import os
    env = dict(os.environ, ARCHIVE_REF=ref)
    r = subprocess.run([sys.executable, str(REPO / 'generator/build.py'),
                        str(archive), str(out)],
                       capture_output=True, text=True, env=env)
    return r


# 404.html is the one page that cannot follow the rules the others do: a 404
# is served for a path of any depth, so it must be self-contained and address
# everything from the root. It gets its own checks instead.
SPECIAL = {'404.html'}


def pages(out):
    # /mock/ is a self-contained design preview for console-bound review; it
    # carries no site chrome, so the chrome invariants do not apply to it
    return sorted(p for p in out.rglob('*.html')
                  if p.name not in SPECIAL and p.parent.name != 'mock')


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


def check_stylesheet_rules():
    """Layout rules that a screenshot proved necessary (#45): a fact value
    never widens its box."""
    css = (REPO / 'assets' / 'style.css').read_text()
    dd = re.search(r'\.factbox dd\{([^}]*)\}', css)
    ck('fact values shrink and wrap instead of overflowing (#45)',
       dd and 'min-width:0' in dd.group(1) and 'overflow-wrap:anywhere' in dd.group(1))


def check_markup_lives_in_templates():
    """The view/template split (issue #23): a view module prepares data and
    calls tpl(); every tag lives under generator/templates/. A tag in a view
    is a regression, as is a template reaching for |safe to smuggle markup
    through (HTML helpers are registered safe in render.py already)."""
    tag = re.compile(r'<[a-zA-Z][a-zA-Z0-9-]*[\s>/]')
    for view in sorted((REPO / 'generator' / 'views').glob('*.py')):
        ck(f'no markup in views/{view.name}', not tag.search(view.read_text()))
    templates = sorted((REPO / 'generator' / 'templates').glob('*.html'))
    ck('every view has templates to render from', len(templates) >= 13)
    for t in templates:
        ck(f'{t.name} does not |safe a string',
           not re.search(r"'[^']*'\s*\|\s*safe", t.read_text()))


def main():
    check_stylesheet_rules()
    check_markup_lives_in_templates()
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)

        # ---------- phase A: controlled archive, staging build ----------
        arch = mkarchive.make_archive(td / 'arch', [
            mkarchive.run_spec('M900101', frames=6000,
                               authors=['Ada', HOSTILE_AUTHOR, 'Nyx'],
                               contract={'emulator': 'BizHawk 2.11',
                                         'files': [{'name': 'Disc 1.iso', 'sha1': 'a' * 40},
                                                   {'name': 'game.exe'}]},
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
            mkarchive.run_spec('M900109', frames=0, authors=['Vid'],
                               videoOnly=True, duration=1317.419,
                               submitted='2026-02-05T00:00:00Z'),
            mkarchive.run_spec('M900105', frames=5500, authors=['Eve'], goal='100-percent',
                               contract={'emulator': 'BizHawk 2.11',
                                         'rom': {'name': 'Old Game (USA).nes', 'sha1': 'c' * 40}},
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
            # subcategories (#43): Episode 1 has any% and 100%; the boards,
            # labels and selector split accordingly, and only here
            mkarchive.run_spec('M900110', game='dos/subgame', goal='episode-1', sub='any',
                               frames=3000, authors=['Ada'],
                               status={'reproduced': 'none', 'verified': 'provisional'},
                               verifications=[{'user': 'Rep', 'date': '2026-02-09'}]),
            mkarchive.run_spec('M900111', game='dos/subgame', goal='episode-1', sub='100',
                               selector='dropdown', sub_selector='dropdown',
                               frames=9000, authors=['Bo'],
                               status={'reproduced': 'none', 'verified': 'provisional'},
                               verifications=[{'user': 'Rep', 'date': '2026-02-09'}]),
        ], nonmembers=['Nyx'],
            game_props={'nes/testgame': {'released': '1989-03', 'unofficial': True,
                                         'discord': 'https://discord.gg/tg1',
                                         'website': 'https://example.org/tg',
                                         'rta': 'https://www.speedrun.com/tg',
                                         'rules': 'No **game-breaking** glitches, game-wide.'}},
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

        # ---------- game thumbnail editor ----------
        game_edit = all_html[out / 'games' / 'nes' / 'testgame' / 'edit' / 'index.html']
        ck('game editor explains the 16:9 thumbnail standard',
           '16:9 is recommended' in game_edit
           and 'recognisable to its players' in game_edit
           and 'centered 16:9 crop' in game_edit)
        ck('game editor carries the thumbnail preview and crop controls',
           'id="ge-thumb-preview"' in game_edit
           and 'id="ge-thumb-crop"' in game_edit
           and 'id="ge-crop-apply"' in game_edit
           and 'id="ge-crop-size"' in game_edit)

        # ---------- content warnings / 18+ gate / reports ----------
        warned = all_html[out / 'runs' / 'M900102' / 'index.html']
        ck('content warning chips render', 'warnchip' in warned)
        ck('sexual content gates the media', 'nsfwgate' in warned and 'nsfwblur' in warned)
        uncl = all_html[out / 'runs' / 'M900103' / 'index.html']
        ck('unclassified goal description escaped',
           ('&quot;quotes&quot;' in uncl or '&#34;quotes&#34;' in uncl) and '&lt;angles&gt;' in uncl)
        policy = 'https://github.com/ToolAssisted-run#32-multiple-author-submission-policy'
        ck('the multiple-authors policy lives in the constitution, not on a site page',
           not (out / 'policy' / 'co-authors').exists())
        ck('the submit form links the policy, hidden until a second author',
           'class="rules coauthnote" hidden' in all_html[out / 'submit' / 'index.html']
           and policy in all_html[out / 'submit' / 'index.html'])
        ck('the import page links the policy',
           policy in all_html[out / 'import' / 'index.html'])
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
           'No games in this group yet' in hollow and 'f-groupmove' in hollow,
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
        # the Committee wears its chip; Founder and Moderator stay in the role log
        ck('the Steering Committee is a badge',
           'rolechip role-committee">Steering Committee' in rows_html, rows_html[:200])
        ck('founder and moderator are not badges',
           'role-founder' not in rows_html and 'role-moderator' not in rows_html,
           rows_html[:200])
        # earned, not granted: one act is enough
        ck('a member who has earned a point is badged a contributor',
           'role-contrib' in rows_html, rows_html[:300])
        ck('the milestone tiers are retired: one plain Contributor badge',
           'tier-1k' not in rows_html and '1k Contributor' not in rows_html,
           rows_html[:300])
        member_pages = {f.parent.name: h for f, h in all_html.items()
                        if f.parent.parent.name == 'authors' and f.name == 'index.html'}
        badged = {n for n, h in member_pages.items() if 'role-contrib' in h}
        ck('the contributor badge is on their own page as well as the list',
           badged, str(sorted(member_pages)))
        # everybody who earned a point wears the same plain badge: the
        # milestone tiers are retired, the medals carry the honors
        for name in badged:
            page_ = member_pages[name]
            score = re.search(r'<b>(\d+)</b><span>contributor score', page_)
            ck(f'{name} is badged a contributor because they earned it',
               score and int(score.group(1)) >= 1, name + ': ' + str(score))
            worn = re.search(r'class="rolechip role-contrib[^"]*"[^>]*>([^<]+)<', page_)
            ck(f'{name} wears the plain Contributor badge',
               worn and worn.group(1) == 'Contributor', str(worn and worn.group(1)))
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
        # the game properties (#44): header chips and external community links
        ck('release date shown at its precision', 'Released March 1989' in gpage_)
        ck('unofficial mark shown', '>Unofficial</span>' in gpage_)
        ck('Discord link is external and referrer-free',
           'href="https://discord.gg/tg1"' in gpage_
           and 'rel="noopener noreferrer">Community Discord' in gpage_)
        ck('community website link is external and referrer-free',
           'href="https://example.org/tg"' in gpage_
           and 'rel="noopener noreferrer">Community website' in gpage_)
        ck('RTA leaderboards link is external and referrer-free',
           'href="https://www.speedrun.com/tg"' in gpage_
           and 'rel="noopener noreferrer">RTA leaderboards' in gpage_)
        ck('the URLs land in attributes, escaped', 'javascript:' not in gpage_)
        # hardware verification exists only on systems played back on real
        # hardware (#53): nes is one, dos is not
        dosgame_ = all_html[out / 'games' / 'dos' / 'hardgame' / 'index.html']
        ck('a game on a non-verifiable system shows no Console column',
           '<th class="ctr">Console</th>' not in dosgame_ and 'Console</th>' in all_html[out / 'games' / 'nes' / 'testgame' / 'index.html'])
        dosrun_ = all_html[out / 'runs' / 'M900104' / 'index.html']
        ck('its run page has no console roster, form or status line',
           'Console verifications' not in dosrun_ and 'id="f-console"' not in dosrun_
           and 'Console verification: none yet' not in dosrun_)
        nesrun_ = all_html[out / 'runs' / 'M900101' / 'index.html']
        ck('an nes run page keeps them',
           'Console verifications' in nesrun_ and 'id="f-console"' in nesrun_)
        contrib_hw = all_html[out / 'contribute' / 'index.html']
        hw_list2 = contrib_hw.split('id="hw-scroll"')[1].split('</table>')[0] if 'id="hw-scroll"' in contrib_hw else ''
        ck('the hardware worklist lists verifiable systems only',
           'M900104' not in hw_list2 and 'M900101' in hw_list2)
        ck('the hardware filter offers verifiable systems only',
           'data-sys="dos"' not in contrib_hw.split('id="hwfilter"')[1].split('</div>')[0])
        # subcategories (#43)
        subpage_ = all_html[out / 'games' / 'dos' / 'subgame' / 'index.html']
        ck('a category with subcategories has one board per subcategory',
           'data-combo="episode-1/any"' in subpage_ and 'data-combo="episode-1/100"' in subpage_
           and 'data-combo="episode-1"' not in subpage_)
        ck('the board heading names category and subcategory',
           '<h2>episode 1 · any</h2>' in subpage_ and '<h2>episode 1 · 100</h2>' in subpage_, subpage_[:100])
        ck('the selector shows a Subcategory row for that category only',
           'class="dimrow subrow" data-dim="goal" data-for="episode-1"' in subpage_
           and subpage_.count('class="dimrow subrow"') == 1)
        ck('a dropdown choice renders both levels as selects',
           '<select class="dimdd" data-dim="goal">' in subpage_ and '<select class="subdd"' in subpage_
           and 'class="dimopt"' not in subpage_)
        ck('the default stays one button each',
           '<select class="dimdd"' not in all_html[out / 'games' / 'nes' / 'testgame' / 'index.html']
           and 'data-opt="100-percent"' in all_html[out / 'games' / 'nes' / 'testgame' / 'index.html'])
        ck('the editor offers the choice for categories', 'name="ge-selector"' in all_html[out / 'games' / 'dos' / 'subgame' / 'edit' / 'index.html']
           and '"selector": "dropdown"' in all_html[out / 'games' / 'dos' / 'subgame' / 'edit' / 'index.html'])
        ck('the composed rules carry the subcategory fragment',
           'Sub rule any.' in subpage_ and 'Sub rule 100.' in subpage_)
        ck('rules live in a dialog behind a View rules button (#60)',
           'class="btn shade rulesbtn"' in subpage_ and '<dialog class="rulesdlg"' in subpage_
           and 'rulesmd' in subpage_)
        ck('the run page carries the move control with the categories and the current spot',
           'id="f-move"' in all_html[out / 'runs' / 'M900110' / 'index.html']
           and '"goal": "episode-1"' in all_html[out / 'runs' / 'M900110' / 'index.html']
           and '"sub": "any"' in all_html[out / 'runs' / 'M900110' / 'index.html'])
        ck('a run page names the subcategory in its category',
           'episode 1 · any' in all_html[out / 'runs' / 'M900110' / 'index.html'])
        ck('games without subcategories show no subcategory row',
           'class="dimrow subrow"' not in all_html[out / 'games' / 'nes' / 'testgame' / 'index.html'])
        ck('the submit form carries the subcategory select, hidden until needed',
           'id="s-subwrap" hidden' in all_html[out / 'submit' / 'index.html'])
        ck('the create-category page offers "subcategory of"',
           'id="cc-parent"' in all_html[out / 'create-category' / 'index.html'])
        # the files a movie was made against: the list on new records, the
        # legacy single rom shown the same way on old ones
        rfiles_ = all_html[out / 'runs' / 'M900101' / 'index.html']
        ck('the run page lists every file with its sha1',
           'Disc 1.iso' in rfiles_ and 'game.exe' in rfiles_
           and 'SHA1</span> ' + 'a' * 40 in rfiles_ and rfiles_.count('class="filefact"') == 2, rfiles_[:100])
        rlegacy_ = all_html[out / 'runs' / 'M900105' / 'index.html']
        ck('a legacy single rom still shows as one file row',
           'Old Game (USA).nes' in rlegacy_ and rlegacy_.count('class="filefact"') == 1)
        ck('the run page links the edit mode of the submit form',
           'href="../../submit/?edit=M900101"' in rfiles_ and 'id="f-edit"' not in rfiles_)
        ck('the submit form carries the file rows widget', 'class="filerows"' in all_html[out / 'submit' / 'index.html'])
        # the page-level 18+ gate on a sexual-content flag: declaration, yes,
        # and a no that leads home; absent elsewhere
        sexual_ = all_html[out / 'runs' / 'M900102' / 'index.html']
        ck('a sexual-content run carries the page gate',
           'id="agegate"' in sexual_ and 'I declare I am 18 years old, or older' in sexual_
           and 'id="agegate-yes"' in sexual_ and 'id="agegate-no" class="btn leave" href="../../"' in sexual_)
        ck('other runs carry no page gate',
           'id="agegate"' not in all_html[out / 'runs' / 'M900101' / 'index.html'])
        # the run editor offers the content disclosures, current ones ticked (#49)
        submit_ = all_html[out / 'submit' / 'index.html']
        ck('the submit page carries edit mode (title, back link, why field)',
           'id="s-title"' in submit_ and 'id="s-editback"' in submit_ and 'id="s-why"' in submit_)
        gedit_ = all_html[out / 'games' / 'nes' / 'testgame' / 'edit' / 'index.html']
        ck('the editor offers every property',
           all(f'id="ge-{f}"' in gedit_ for f in ('title', 'thumb', 'released', 'unofficial', 'discord', 'website', 'rta', 'rules')))
        gpage_ = all_html[out / 'games' / 'nes' / 'testgame' / 'index.html']
        ck('game-wide rules render above the category rule in the dialog (#64)',
           '<b>game-breaking</b> glitches, game-wide' in gpage_
           and gpage_.find('game-breaking') < gpage_.find('Test rule.'), gpage_[:200])
        ck('the unofficial flag is a checkbox, ticked from the record',
           'id="ge-unofficial" checked' in gedit_)
        ck('the editor has exactly one Save, at the bottom, and one reason',
           gedit_.count('id="ge-save"') == 1 and 'id="ge-why"' in gedit_
           and gedit_.index('id="ge-cats"') < gedit_.index('id="ge-save"')
           and '<button class="btn">Rename</button>' not in gedit_)
        ck('the editor shows the current values', 'value="1989-03"' in gedit_
           and 'value="https://discord.gg/tg1"' in gedit_)
        ck('a game without properties shows none',
           'Released ' not in all_html[out / 'games' / 'nes' / 'orphan' / 'index.html'])
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
        ck('the groups view holds an Uncategorized card for the rest',
           'groups/uncategorized/' in vgroups, str(vgroups.count('class="card"')))
        ck('Uncategorized sorts last whatever the sort is',
           vgroups.index('data-last="1"') > vgroups.index('groups/test-family/'))
        uncl = all_html.get(out / 'groups' / 'uncategorized' / 'index.html', '')
        ck('the Uncategorized group has a page', bool(uncl))
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
           'groups/uncategorized/' in all_html[out / 'games' / 'nes' / 'orphan' / 'index.html'])
        syspage_ = all_html[out / 'systems' / 'nes' / 'index.html']
        ck('library pages sort by release date and can hide unofficial games',
           'data-mode="released"' in syspage_ and 'id="hide-unofficial"' in syspage_
           and 'data-released="1989-03"' in syspage_ and 'data-unofficial="1"' in syspage_)
        ck('library controls wait for the grid before wiring (#44 follow-up)',
           "DOMContentLoaded" in syspage_.split('id="hide-unofficial"')[1][:1500])
        vlist = gindex[gindex.index('id="v-list"'):]
        # five games now: the four with runs (the subcategory game included),
        # and the runless one an expert created while filling out a group
        ck('the list view has one row per game', vlist.count('<tr onclick') == 5,
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
                            ('4-terms-of-use', 'Terms of Use'),
                            ('5-code-of-conduct', 'Code of Conduct')):
            ck(f'footer links {label} to its section',
               f'{base}{frag}">{label}</a>' in joined, frag)
        ck('the footer no longer links Governance', 'Governance</a>' not in all_html[out / 'index.html'])
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

        ck('no beta notice anywhere', 'betabar' not in joined and '· beta' not in joined and 'open beta' not in joined)

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
        ck('a game page names its group expert', 'Grp' in head_
           and '(group scope)' in head_, head_[-400:])
        ck('the site-wide expert is a quiet count, never a chip (#65)',
           'authors/root' not in head_ and 'wider-scope' in head_
           and 'title="Root"' in head_, head_[-400:])
        # ---------- you may also like ----------
        reel_page = all_html[out / 'runs' / 'M900101' / 'index.html']
        ck('a run page carries the also-like reel', 'alsolike' in reel_page
           and 'You may also like' in reel_page, reel_page[:200])
        ck('the reel cards link a sibling run relative to the page',
           re.search(r'class="card" href="\.\./M\d+/"', reel_page) is not None)
        grp_page = all_html[out / 'groups' / 'test-family' / 'index.html']
        ck('a group page names only its own group experts (#65)',
           'Group experts:' in grp_page and 'authors/grp' in grp_page
           and 'Group experts and above' not in grp_page, grp_page[:300])
        ck('site-wide experts roll up on the group page too',
           'site-wide' in grp_page and 'title="Root"' in grp_page, grp_page[:300])

        # ---------- acts on the page they are about ----------
        # Each zone starts hidden and opens only for the people who may use it.
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
        ck('a game page offers deletion, not the retired removal request',
           'request-removal' not in gh and 'Ask for this game' not in gh
           and 'f-gamedelete' in gh,
           'the path is chosen by the script, the words by the page')
        grh = all_html[out / 'groups' / 'test-family' / 'index.html']
        ck('a group page moves games in and deletes itself outright; no request form',
           'f-groupmove' in grh and 'f-groupaddgame' not in grh
           and 'f-groupremove' not in grh and 'f-groupdelete' in grh, grh[:200])
        ck('the games index offers a new group',
           'f-newgroup' in all_html[out / 'games' / 'index.html'])

        contrib = all_html[out / 'contribute' / 'index.html']
        # the third worklist: hardware verification, with its own remembered
        # filter; a video-only run (no input movie) never appears on it
        ck('contribute lists what needs hardware verification',
           'Needs hardware verification' in contrib and 'id="hw-scroll"' in contrib
           and 'id="hwfilter"' in contrib and 'Hardware I own' in contrib
           and "'tar-my-hardware'" in contrib)
        hw_list = contrib.split('id="hw-scroll"')[1].split('</table>')[0]
        ck('the hardware list skips video-only runs and console-verified ones',
           'M900109' not in hw_list and 'M900105' not in hw_list and 'M900101' in hw_list, hw_list[:300])
        ck('the systems filter applies after the rows it filters exist (#40)',
           "addEventListener('DOMContentLoaded', apply)" in contrib
           and contrib.index('#sysfilter') < contrib.index('id="nr-scroll"'))

        # ---------- search engines: every public page is describable ----------
        pub = [p for p, h in all_html.items() if 'content="noindex"' not in h]
        ck('every public page carries a canonical and a description',
           all('rel="canonical"' in all_html[p] and 'name="description"' in all_html[p]
               for p in pub), str([p.parent.name for p in pub
                                   if 'rel="canonical"' not in all_html[p]][:3]))
        ck('a run page carries VideoObject and breadcrumb structured data',
           '"@type": "VideoObject"' in all_html[out / 'runs' / 'M900101' / 'index.html']
           and '"@type": "BreadcrumbList"' in all_html[out / 'runs' / 'M900101' / 'index.html'])
        ck('run titles say TAS, the system, the time and the authors',
           re.search(r'<title>[^<]*\(NES\) TAS in [^<]* by ', all_html[out / 'runs' / 'M900101' / 'index.html']) is not None)
        smap = (out / 'sitemap.xml').read_text()
        ck('the sitemap lists exactly the indexable pages',
           smap.count('<url>') == len(pub) and 'submit/' not in smap and '/edit/' not in smap,
           f'{smap.count("<url>")} vs {len(pub)}')
        ck('robots.txt points at the sitemap and fences the tooling',
           'Sitemap: https://toolassisted.run/sitemap.xml' in (out / 'robots.txt').read_text()
           and 'Disallow: /submit/' in (out / 'robots.txt').read_text())
        gidx = all_html[out / 'games' / 'index.html']
        ck('the list view sorts by any column',
           gidx.count('data-key="') in (5, 6) and 'data-runs=' in gidx
           and 'data-stars=' in gidx, str(gidx.count('data-key="')))

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
        # the pickers search the archivist as you type (#56): no page carries
        # the member list, and the page knows who is seated so the seat
        # picker can leave them out
        ck('the founder seat form picks a member, with no list embedded',
           'id="dl-members"' not in fpanel_ and 'name="target" required' in fpanel_
           and '"committee": ["Ada"' in fpanel_, fpanel_[-400:])
        cpanel_ = all_html[out / 'committee' / 'index.html']
        ck('the committee-decision form follows role and direction, members searched live',
           'dl-role-candidates' not in cpanel_ and '"members":' not in cpanel_
           and '"moderators":' in cpanel_ and '"committeeNames":' in cpanel_)
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
           re.search(r'your\s+responsibility', imp) is not None and 'Co-authored works' in imp, imp[:200])

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
           and 'Delete this run' in runp_, runp_[:200])
        editp_ = all_html.get(out / 'games' / 'nes' / 'testgame' / 'edit' / 'index.html') \
            or (out / 'games' / 'nes' / 'testgame' / 'edit' / 'index.html').read_text()
        ck('every game has its editor page, gated and data-carrying',
           'id="gameeditdata"' in editp_ and 'id="ge-gate"' in editp_
           and 'id="ge-addcat"' in editp_)
        ck('the game page expert menu links the editor',
           'href="edit/"' in all_html[out / 'games' / 'nes' / 'testgame' / 'index.html'])
        ck('the game page carries the expert delete',
           'f-gamedelete' in all_html[out / 'games' / 'nes' / 'testgame' / 'index.html'])
        ck('the group page carries the expert delete',
           'f-groupdelete' in all_html[out / 'groups' / 'test-family' / 'index.html'])
        ck('the member page carries no delete box (#61)',
           'f-memberdelete' not in all_html[out / 'authors' / 'ada' / 'index.html'])
        ck('the Committee panel carries the member delete',
           'id="f-memberdelete"' in all_html[out / 'committee' / 'index.html']
           and '"founders"' in all_html[out / 'committee' / 'index.html'])
        log_page0 = all_html[out / 'policy' / 'site-log' / 'index.html']
        ck('the site log page is a window, the archive the log',
           'shows the last 7 days' in log_page0 and 'archive repository' in log_page0)
        ck('the site log has a Deletions section even when nothing was deleted',
           '<h2>Deletions' in log_page0 and 'Nothing has been deleted outright' in log_page0)

        # ---------- revisions are visible, small, and counted ----------
        r101 = all_html[out / 'runs' / 'M900101' / 'index.html']
        ck('a revised run carries the small history link with its count',
           'view history · 2 revisions' in r101 and '/commits/' in r101,
           r101[r101.find('view history') - 50:][:160])
        r107 = all_html[out / 'runs' / 'M900107' / 'index.html']
        ck('an unrevised run still links its history, uncounted',
           'view history →' in r107 and 'revision' not in
           r107[r107.find('view history'):r107.find('view history') + 60], r107[:100])
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
                 ('Role changes', 'Identity attestations', 'Run reports',
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
        board = aside[aside.find('Contributor board'):]
        ck('the board carries no points-tier chip beside the points (#59)',
           'role-contrib' not in board, board[:300])
        ck('an achievement wears a tooltipped medal on the board',
           re.search(r'<span class="medal medal-(gold|silver|bronze)" title="[^"]+: [^"]+"', board) is not None,
           board[:400])
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
        ck('nothing waits on anybody: the removal-request flow is retired',
           'pending-list' not in panel and 'f-decide' not in panel, panel[:200])
        ck('the panel embeds neither the members nor the games (#56): they are searched',
           'members' not in pd and 'games' not in pd, str(list(pd))[:200])
        ck('ratification is gone from the panel data',
           all('established' not in gr for gr in pd['groups']), str(pd['groups'][:2]))

        # ---------- the browse script actually renders ----------
        # It once threw on a video-only run's null frames and the movies page
        # went empty for everybody; the fixture carries one, so the script is
        # RUN here, not just syntax-checked.
        node0 = shutil.which('node')
        if node0:
            bh = (out / 'browse' / 'index.html').read_text()
            m0 = re.search(r'<script>\n?(var RUNS = .*?)</script>', bh, re.S)
            stub = (
                'const els = {};\n'
                'function mk(){ return {value: "", textContent: "", innerHTML: "",'
                ' addEventListener(){}, }; }\n'
                'global.document = { getElementById: (i) => (els[i] = els[i] || mk()) };\n'
                'global.location = { search: "" };\n')
            r0 = subprocess.run([node0, '-e',
                                 stub + m0.group(1)
                                 + '\nconsole.log(JSON.stringify({rows: els.brows.innerHTML.length,'
                                   ' broken: /undefined|null|NaN/.test(els.brows.innerHTML),'
                                   ' count: els.bcount.textContent}))'],
                                capture_output=True, text=True, timeout=60)
            ck('the browse script renders without throwing', r0.returncode == 0,
               r0.stderr[-300:])
            if r0.returncode == 0:
                out0 = json.loads(r0.stdout.strip().splitlines()[-1])
                ck('and every run lands, none of the cells broken',
                   out0['rows'] > 0 and not out0['broken'], str(out0))

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
