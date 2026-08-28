#!/usr/bin/env python3
"""Layout: what the pages actually measure once a browser lays them out.

Every other suite reads markup or runs the script; none of them has a box
model, so a rule that quietly collapses an element is invisible to all of
them. That is not hypothetical: the game-group mosaic shipped as an empty grey
frame because `.thumb` sets `align-items:center` for its flex layout and that
survived the switch to grid, leaving every tile 130x0. The markup was perfect
and the images loaded.

So this drives a real Chrome over the built site and asserts the things a
reader would notice: nothing visible has collapsed to nothing, the mosaic
fills its frame whatever the tile count, and no page is wider than the phone
it is being read on.

Hermetic: serves the built site from a local directory; remote images (the
archive's raw URLs) simply fail to load, which changes no measurement here.
Needs Chrome and puppeteer-core; skips without them.

Usage: tests/test_layout.py
"""
import functools
import http.server
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mkarchive  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent

CHROME_NAMES = ('google-chrome', 'google-chrome-stable', 'chromium',
                'chromium-browser', 'chrome')

# Exit code the probe uses for "the browser would not start": an unstartable
# Chrome is the runner having a bad day, not the site being wrong, and it is
# reported the way a missing browser already is rather than failing the build
# (a red suite now also means no deploy, so a flake must not gate the site).
NO_BROWSER = 97

PROBE = r"""
import puppeteer from 'puppeteer-core';

// Chrome is talked to over a pipe rather than a WebSocket: the ws:// form
// makes the launch wait for a URL to appear in Chrome's stdout, and on a
// loaded runner that line arrives late (or not at all) and the launch dies
// with "Timed out ... waiting for the WS endpoint URL". A pipe has nothing
// to parse. The launch is also given room and two more tries.
async function launchBrowser() {
  let last;
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      return await puppeteer.launch({
        executablePath: process.env.CHROME_PATH,
        args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'],
        pipe: true,
        timeout: 120000,
        protocolTimeout: 180000,
      });
    } catch (e) {
      last = e;
      console.error(`chrome launch attempt ${attempt} failed: ${e.message}`);
      await new Promise((r) => setTimeout(r, 2000 * attempt));
    }
  }
  console.error('chrome would not start: ' + (last && last.message));
  process.exit(97);
}
const browser = await launchBrowser();
const out = {};
const page = await browser.newPage();

async function look(url, width, view) {
  await page.setViewport({ width, height: 900 });
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  if (view) await page.evaluate((v) => {
    const b = document.querySelector('[data-view="' + v + '"]');
    if (b) b.click();
  }, view);
  await new Promise((r) => setTimeout(r, 400));
  return page.evaluate(() => {
    const box = (el) => { const r = el.getBoundingClientRect();
      return { w: Math.round(r.width), h: Math.round(r.height) }; };
    const tiles = [...document.querySelectorAll('.tile')].map(box);
    const collages = [...document.querySelectorAll('.collage')]
      .filter((c) => c.getClientRects().length > 0)
      .map((c) => ({
      n: Number(c.dataset.n), frame: box(c),
      tiles: [...c.querySelectorAll('.tile')].map(box),
    }));
    // anything a reader is meant to see, measured: a zero-height box that
    // holds an image is the shape the mosaic bug took. A hidden view has no
    // client rects at all, and is not what this is looking for.
    const shown = (el) => el.getClientRects().length > 0;
    const collapsed = [...document.querySelectorAll('.card, .thumb, .tile, .grid, .policy, .implist')]
      .filter((el) => shown(el) && el.getBoundingClientRect().height === 0)
      .map((el) => el.className + '#' + (el.id || ''));
    // the top bar: one row at every width, or it folded into the menu
    // button. Wrapping into two is the failure this measures.
    const navEl = document.querySelector('.nav');
    const toggleEl = document.getElementById('navtoggle');
    const nav = navEl ? {
      h: Math.round(navEl.getBoundingClientRect().height),
      folded: toggleEl ? getComputedStyle(toggleEl).display !== 'none' : false,
      linksShown: shown(document.getElementById('navlinks')),
    } : null;
    return {
      docWidth: document.documentElement.scrollWidth,
      viewport: window.innerWidth,
      tiles, collages, collapsed, nav,
      cards: [...document.querySelectorAll('.card')].filter(shown).length,
    };
  });
}

for (const job of JSON.parse(process.argv[2])) {
  out[job.name] = await look(job.url, job.width, job.view);
}
console.log(JSON.stringify(out));
await browser.close();
"""

failures = []


def ck(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (f'  [{detail}]' if detail and not cond else ''))
    if not cond:
        failures.append(name)


def find_chrome():
    if os.environ.get('CHROME_PATH'):
        return os.environ['CHROME_PATH']
    for name in CHROME_NAMES:
        found = shutil.which(name)
        if found:
            return found
    cache = pathlib.Path.home() / '.cache' / 'puppeteer'
    for candidate in sorted(cache.rglob('chrome-headless-shell')) + sorted(cache.rglob('chrome')):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def find_puppeteer():
    """puppeteer-core, from wherever npm put it."""
    here = pathlib.Path(__file__).resolve()
    roots = [REPO, pathlib.Path.cwd(), here.parent]
    scratch = os.environ.get('SCRATCH_NODE_MODULES')
    if scratch:
        roots.insert(0, pathlib.Path(scratch))
    for root in roots:
        if (root / 'node_modules' / 'puppeteer-core' / 'package.json').is_file():
            return root
    return None


def main():
    chrome = find_chrome()
    node = shutil.which('node')
    pupp_root = find_puppeteer()
    if not (chrome and node and pupp_root):
        why = (f'chrome={bool(chrome)} node={bool(node)} '
               f'puppeteer-core={bool(pupp_root)}')
        if os.environ.get('CI'):
            # skipping quietly on the machine that gates deploys would make a
            # missing browser look exactly like a passing suite
            sys.exit(f'layout tests cannot run in CI ({why})')
        print(f'SKIP layout checks ({why})')
        sys.exit(0)

    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        arch = mkarchive.make_archive(td / 'a', [
            mkarchive.run_spec('M900901', frames=6000, authors=['Ada']),
            mkarchive.run_spec('M900902', game='nes/second', frames=6100, authors=['Bo']),
            mkarchive.run_spec('M900903', game='nes/third', frames=6200, authors=['Cy']),
            mkarchive.run_spec('M900904', game='dos/fourth', frames=6300, authors=['Dee']),
            mkarchive.run_spec('M900905', game='dos/fifth', frames=6400, authors=['Eve']),
        ], groups=[
            # one group per tile count the mosaic has to lay out
            {'key': 'four', 'title': 'Four Games', 'games': [
                'nes/testgame', 'nes/second', 'nes/third', 'dos/fourth']},
            {'key': 'two', 'title': 'Two Games', 'games': ['dos/fifth', 'nes/testgame']},
        ])
        out = td / 'o'
        r = subprocess.run([sys.executable, str(REPO / 'generator/build.py'),
                            str(arch), str(out)], capture_output=True, text=True)
        ck('build succeeds', r.returncode == 0, r.stderr[-300:])
        if r.returncode:
            sys.exit(1)

        handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                    directory=str(out))
        srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{port}'

        # the script has to live beside node_modules: an ES module resolves
        # its imports from its own directory, not from the working directory
        script = pupp_root / '.tar-layout-probe.mjs'
        script.write_text(PROBE)
        jobs = [
            {'name': 'groups', 'url': f'{base}/games/', 'width': 1280, 'view': 'groups'},
            {'name': 'systems', 'url': f'{base}/games/', 'width': 1280, 'view': 'systems'},
            {'name': 'phone', 'url': f'{base}/games/', 'width': 360, 'view': 'groups'},
            {'name': 'home', 'url': f'{base}/', 'width': 360, 'view': None},
            {'name': 'run', 'url': f'{base}/runs/M900901/', 'width': 360, 'view': None},
            {'name': 'group', 'url': f'{base}/groups/four/', 'width': 360, 'view': None},
            {'name': 'game', 'url': f'{base}/games/nes/testgame/', 'width': 360, 'view': None},
            {'name': 'games-list', 'url': f'{base}/games/', 'width': 360, 'view': 'list'},
            {'name': 'authors', 'url': f'{base}/authors/', 'width': 360, 'view': None},
            {'name': 'author', 'url': f'{base}/authors/ada/', 'width': 360, 'view': None},
            {'name': 'contribute', 'url': f'{base}/contribute/', 'width': 360, 'view': None},
            {'name': 'tools', 'url': f'{base}/tools/', 'width': 360, 'view': None},
            {'name': 'browse', 'url': f'{base}/browse/', 'width': 360, 'view': None},
            {'name': 'sitelog', 'url': f'{base}/policy/site-log/', 'width': 360, 'view': None},
            # the import page's disclaimer block wore the responsive text-swap
            # class as a layout class and vanished whole under 560px; a phone
            # never saw the list. This is the page measured at the width that
            # broke.
            {'name': 'import', 'url': f'{base}/import/', 'width': 360, 'view': None},
            # the top bar wrapped into two rows between 900px and about
            # 1120px: the search, the bell and the avatar dangled under the
            # links while the menu button waited for a breakpoint 200px away
            {'name': 'nav-wide', 'url': f'{base}/', 'width': 1400, 'view': None},
            {'name': 'nav-mid', 'url': f'{base}/', 'width': 1000, 'view': None},
            {'name': 'nav-narrow', 'url': f'{base}/', 'width': 360, 'view': None},
        ]
        proc = subprocess.run([node, str(script), json.dumps(jobs)],
                              capture_output=True, text=True, cwd=str(pupp_root),
                              env=dict(os.environ, CHROME_PATH=chrome), timeout=300)
        srv.shutdown()
        script.unlink(missing_ok=True)
        if proc.returncode == NO_BROWSER:
            # the runner could not start Chrome; say so loudly and skip the
            # measurements rather than call the site broken
            print('NOTE layout checks skipped: ' + proc.stderr.strip().splitlines()[-1]
                  if proc.stderr.strip() else 'NOTE layout checks skipped: chrome would not start')
            print('---', len(failures), 'failures')
            sys.exit(1 if failures else 0)
        if proc.returncode != 0:
            ck('the browser drives the built site', False, proc.stderr[-400:])
            print('---', len(failures), 'failures')
            sys.exit(1)
        data = json.loads(proc.stdout.strip().splitlines()[-1])
        ck('the browser drives the built site', True)

        # the top bar: one row at every width, folding into the menu button
        # exactly when the full row stops fitting
        for name, folded_here in (('nav-wide', False), ('nav-mid', True),
                                  ('nav-narrow', True)):
            nav = data[name]['nav']
            ck(f'{name}: the top bar is one row', nav and nav['h'] <= 80, str(nav))
            ck(f'{name}: ' + ('folded into the menu button' if folded_here
                              else 'shows the full row of links'),
               nav and nav['folded'] == folded_here
               and nav['linksShown'] == (not folded_here), str(nav))
            ck(f'{name}: the page does not scroll sideways',
               data[name]['docWidth'] <= data[name]['viewport'],
               f"{data[name]['docWidth']} > {data[name]['viewport']}")

        groups = data['groups']
        ck('the groups view draws a card per group', groups['cards'] == 2, str(groups['cards']))
        ck('nothing on the groups view collapsed to zero height',
           not groups['collapsed'], str(groups['collapsed'][:3]))
        for c in groups['collages']:
            ck(f'a {c["n"]}-tile mosaic fills its frame',
               all(t['w'] > 0 and t['h'] > 0 for t in c['tiles']),
               f'frame {c["frame"]} tiles {c["tiles"]}')
            covered = sum(t['w'] * t['h'] for t in c['tiles'])
            area = c['frame']['w'] * c['frame']['h']
            ck(f'a {c["n"]}-tile mosaic covers its frame',
               area and covered / area > 0.9, f'{covered}/{area}')
        ck('the mosaic tile counts are the ones the data implies',
           sorted(c['n'] for c in groups['collages']) == [2, 4],
           str([c['n'] for c in groups['collages']]))

        ck('nothing on the systems view collapsed to zero height',
           not data['systems']['collapsed'], str(data['systems']['collapsed'][:3]))

        for name in ('phone', 'home', 'run', 'group', 'game', 'games-list',
                     'authors', 'author', 'contribute', 'tools', 'browse',
                     'sitelog', 'import'):
            page = data[name]
            ck(f'{name} at 360px does not scroll sideways',
               page['docWidth'] <= page['viewport'] + 1,
               f'{page["docWidth"]} > {page["viewport"]}')
            ck(f'{name} at 360px has nothing collapsed',
               not page['collapsed'], str(page['collapsed'][:3]))

    print('---', len(failures), 'failures')
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
