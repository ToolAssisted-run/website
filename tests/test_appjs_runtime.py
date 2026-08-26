#!/usr/bin/env python3
"""Run the emitted client scripts the way a browser would.

Syntax checks and per-function tests both passed while the news feed was dead
on the live site: `escapeHtml` (then `escH`) was declared inside the submit page's block, so on the
landing page the feed threw a ReferenceError, its error handler threw the same
way, and the panel sat on "Loading the latest posts…" for ever. Nothing static
catches that; only executing the script in a page context does.

So this loads the real, emitted ES modules under a small DOM stub, once per
page context — assets/app.js alone for a page with no module of its own,
and the real assets/page-*.js the generator wired to that page otherwise, the
same way a browser resolves its `import './app.js'` — and asserts each runs
clean and does what that page needs.

Needs node; skips without it.

Usage: tests/test_appjs_runtime.py
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

# a DOM small enough to write down, big enough to run the script: elements
# exist only when the page under test declares them, which is the whole point
STUB = r"""
const ids = JSON.parse(process.argv[2]);
const calls = { fetched: [], errors: [], html: {} };

function makeEl(id) {
  const el = {
    id, hidden: true, textContent: '', value: '', checked: false,
    dataset: id === 'bskyfeed' ? { handle: 'toolassisted.run' } : {},
    style: {}, children: [], files: [],
    classList: { add(){}, remove(){}, toggle(){ return true; }, contains(){ return false; } },
    setAttribute(k, v){ this.dataset[k] = v; },
    getAttribute(k){ return this.dataset[k]; },
    addEventListener(){}, removeEventListener(){},
    appendChild(c){ this.children.push(c); return c; },
    prepend(){}, remove(){}, closest(){ return makeEl('closest'); },
    parentNode: { replaceChild(){}, insertBefore(){} },
    querySelector(){ return null; }, querySelectorAll(){ return []; },
    getBoundingClientRect(){ return { height: 500, width: 800, top: 0 }; },
    insertAdjacentHTML(){}, focus(){}, submit(){},
    get innerHTML(){ return calls.html[this.id] || ''; },
    set innerHTML(v){ calls.html[this.id] = v; },
  };
  return el;
}
const store = {};
for (const id of ids) store[id] = makeEl(id);

global.document = {
  documentElement: { dataset: {}, classList: { add(){}, remove(){} } },
  head: { appendChild(){} },
  body: { appendChild(){}, classList: { add(){}, remove(){} } },
  getElementById: (id) => store[id] || null,
  querySelector: (sel) => null,
  querySelectorAll: () => [],
  createElement: (tag) => makeEl('created:' + tag),
  createTextNode: () => makeEl('created:#text'),
  addEventListener(){},
  cookie: '',
};
global.window = {
  TAR: { api: 'https://forum.example/archivist', rel: '', v: 'test' },
  location: { pathname: '/', href: 'https://toolassisted.run/', search: '' },
  matchMedia: () => ({ matches: false, addEventListener(){} }),
  innerWidth: 1280,
  addEventListener(){},
  localStorage: { getItem: () => null, setItem(){}, removeItem(){} },
  sessionStorage: { getItem: () => null, setItem(){}, removeItem(){} },
};
global.localStorage = window.localStorage;
global.sessionStorage = window.sessionStorage;
// node 22 makes navigator a getter-only global
Object.defineProperty(global, 'navigator', { value: { userAgent: 'node' }, configurable: true });
Object.defineProperty(global, 'crypto', {   // node 20 exposes a getter-only crypto
  value: { subtle: { digest: async () => new ArrayBuffer(20) } }, configurable: true });
global.setTimeout = (fn) => 0;          // never fire deferred fallbacks
global.URLSearchParams = URLSearchParams;

global.fetch = (url, opts) => {
  calls.fetched.push(String(url));
  if (String(url).includes('bsky.app/xrpc')) {
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(FEED) });
  }
  return Promise.resolve({ ok: true, status: 200,
    json: () => Promise.resolve({ ok: true, loggedIn: false, user: null }) });
};

const FEED = { feed: [ { post: {
  uri: 'at://did/app.bsky.feed.post/abc123',
  likeCount: 3, repostCount: 1,
  record: { text: 'Hello from the archive', createdAt: new Date().toISOString() },
} } ] };

process.on('unhandledRejection', (e) => calls.errors.push('unhandledRejection: ' + e));
try {
  // the real, emitted module (app.js, or a page's own module importing it),
  // resolved and executed exactly as a browser's <script type="module"> would
  await import(MODULE_URL_HERE);
} catch (e) {
  calls.errors.push('threw: ' + (e && e.stack ? e.stack.split('\n')[0] : e));
}
setImmediate(() => {
  setImmediate(() => console.log(JSON.stringify(calls)));
});
"""

failures = []


def ck(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (f'  [{detail}]' if detail and not cond else ''))
    if not cond:
        failures.append(name)


def module_url(assets_dir, module):
    """A file:// URL for a real, emitted asset module (posix-safe on Windows)."""
    return pathlib.Path(assets_dir, module).resolve().as_uri()


def run_page(node, assets_dir, td, label, ids, module='app.js'):
    script = td / f'run-{label}.mjs'
    script.write_text(STUB.replace('MODULE_URL_HERE', json.dumps(module_url(assets_dir, module))))
    r = subprocess.run([node, str(script), json.dumps(ids)],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        return None, r.stderr[-500:]
    try:
        return json.loads(r.stdout.strip().splitlines()[-1]), ''
    except Exception as e:                                     # noqa: BLE001
        return None, f'{e}: {r.stdout[-300:]}'



# A second, richer harness: instead of a hand-listed set of ids, it mirrors a
# page the generator actually produced (every element carrying an id, a name or
# a button class, in document order) and resolves the simple selectors app.js
# uses. That is what it takes to catch a selector picking the WRONG element:
# `submitForm.querySelector('button.btn')` matched Preview, so page init disabled
# Preview and left Submit ungated. A stub that returns a fresh fake for every
# query cannot see that.
PAGE_STUB = r"""
import { readFileSync } from 'node:fs';
const spec = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const session = JSON.parse(process.argv[3]);
const calls = { errors: [], html: {}, fetched: [] };
const els = [];

function makeEl(s) {
  const e = {
    id: s.id || '', tag: s.tag, className: s.className || '', name: s.name || '',
    disabled: false, hidden: true, value: '', checked: false, files: [],
    textContent: s.text || (s.tag === 'script' ? '{}' : ''), style: {}, dataset: {}, options: [],
    selectedIndex: -1, handlers: {},
    classList: { add(){}, remove(){}, toggle(){ return true; }, contains(){ return false; } },
    addEventListener(type, fn){ (this.handlers[type] = this.handlers[type] || []).push(fn); },
    removeEventListener(){}, setAttribute(k, v){ this.dataset[k] = v; },
    getAttribute(k){ return this.dataset[k]; }, removeAttribute(){},
    appendChild(c){ return c; }, prepend(){}, remove(){}, insertAdjacentHTML(){},
    parentNode: { replaceChild(){}, insertBefore(){} },
    closest(){ return null; }, focus(){}, submit(){}, scrollIntoView(){},
    getBoundingClientRect(){ return { height: 400, width: 800, top: 0 }; },
    querySelector(sel){ return find(sel); }, querySelectorAll(sel){ return findAll(sel); },
    get innerHTML(){ return calls.html[this.id || this.name] || ''; },
    set innerHTML(v){ calls.html[this.id || this.name] = v; },
  };
  return e;
}
function matches(e, sel) {
  sel = sel.trim();
  let m;
  if ((m = /^#([\w-]+)$/.exec(sel))) return e.id === m[1];
  if ((m = /^\[name=["']?([\w-]+)["']?\]$/.exec(sel))) return e.name === m[1];
  if ((m = /^(\w*)\.([\w-]+)$/.exec(sel)))
    return (!m[1] || e.tag === m[1]) && e.className.split(/\s+/).includes(m[2]);
  if ((m = /^(\w+)$/.exec(sel))) return e.tag === m[1];
  return false;
}
const find = (sel) => els.find((e) => matches(e, sel)) || null;
const findAll = (sel) => els.filter((e) => matches(e, sel));
for (const s of spec) els.push(makeEl(s));
const byId = {};
for (const e of els) if (e.id) byId[e.id] = e;

global.document = {
  documentElement: { dataset: {}, classList: { add(){}, remove(){} } },
  head: { appendChild(){} },
  body: { appendChild(){}, classList: { add(){}, remove(){} } },
  getElementById: (id) => byId[id] || null,
  querySelector: (sel) => find(sel),
  querySelectorAll: (sel) => findAll(sel),
  createElement: (tag) => makeEl({ tag }),
  createTextNode: (text) => makeEl({ tag: '#text', text: String(text) }),
  addEventListener(){}, cookie: '',
};
global.window = {
  TAR: Object.assign({ api: 'https://forum.example/archivist', rel: '../', v: 'test' },
                     (session && session.__tar) || {}),
  location: { pathname: '/submit/', href: 'https://toolassisted.run/submit/', search: '' },
  matchMedia: () => ({ matches: false, addEventListener(){} }),
  innerWidth: 1280, addEventListener(){},
  localStorage: { getItem: () => null, setItem(){}, removeItem(){} },
  sessionStorage: { getItem: (k) => (k === 'tar-viewas' && session && session.__viewas) || null,
                    setItem(){}, removeItem(){} },
};
global.localStorage = window.localStorage;
global.sessionStorage = window.sessionStorage;
global.location = window.location;      // a browser has it bare, not only on window
global.history = { replaceState(){}, pushState(){} };
global.alert = () => {};
// node 22 exposes navigator and crypto as getter-only globals
Object.defineProperty(global, 'navigator', { value: { userAgent: 'node' }, configurable: true });
Object.defineProperty(global, 'crypto', {
  value: { subtle: { digest: async () => new ArrayBuffer(20) } }, configurable: true });
global.fetch = (url) => {
  calls.fetched.push(String(url));
  if (session && session.__blocked) return Promise.reject(new Error('blocked'));
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(session) });
};
const realTimeout = setTimeout;
global.setTimeout = (fn) => 0;          // never fire debounced work on its own
global.URLSearchParams = URLSearchParams;

process.on('unhandledRejection', (e) => calls.errors.push('unhandledRejection: ' + e));
try {
  // the real, emitted module the generator wired to this page (or app.js
  // alone, for a page with none of its own), resolved and executed exactly
  // as a browser's <script type="module"> would
  await import(MODULE_URL_HERE);
} catch (e) {
  calls.errors.push('threw: ' + (e && e.stack ? e.stack.split('\n')[0] : e));
}
realTimeout(() => {                     // the page wires itself after the session probe
  const fire = (id, type) => {
    const el = byId[id];
    if (!el || !el.handlers[type]) { calls.errors.push('no ' + type + ' handler on ' + id); return; }
    try { el.handlers[type].forEach((fn) => fn({ preventDefault(){}, target: el })); }
    catch (e) { calls.errors.push('handler ' + id + ': ' + e.message); }
  };
  JSON.parse(process.argv[4] || '[]').forEach(([id, type]) => fire(id, type));
  calls.state = {};
  for (const e of els) if (e.id) calls.state[e.id] = { disabled: e.disabled, hidden: e.hidden };
  console.log(JSON.stringify(calls));
}, 200);
"""


def dom_of(html):
    """Every element the stub needs to know about, in document order.

    The embedded JSON blobs (#actdata, #gamedata, …) carry their real content:
    the page drives itself from them, and feeding it a placeholder would test
    a page no member ever sees.
    """
    blobs = dict(re.findall(
        r'<script type="application/json" id="([^"]+)">(.*?)</script>', html, re.S))
    out = []
    for m in re.finditer(r'<(\w+)([^>]*)>', html):
        tag, attrs = m.group(1), m.group(2)
        gid = re.search(r'\bid="([^"]*)"', attrs)
        cls = re.search(r'\bclass="([^"]*)"', attrs)
        nam = re.search(r'\bname="([^"]*)"', attrs)
        if gid or nam or (cls and tag == 'button'):
            key = gid.group(1) if gid else ''
            out.append({'id': key, 'tag': tag,
                        'className': cls.group(1) if cls else '',
                        'name': nam.group(1) if nam else '',
                        'text': blobs.get(key, '')})
    return out


def run_real_page(node, assets_dir, td, label, html, session, events=(), module='app.js'):
    script = td / f'page-{label}.mjs'
    script.write_text(PAGE_STUB.replace('MODULE_URL_HERE', json.dumps(module_url(assets_dir, module))))
    dom = td / f'dom-{label}.json'
    dom.write_text(json.dumps(dom_of(html)))
    r = subprocess.run([node, str(script), str(dom), json.dumps(session),
                        json.dumps([list(e) for e in events])],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        return None, r.stderr[-500:]
    try:
        return json.loads(r.stdout.strip().splitlines()[-1]), ''
    except Exception as e:                                     # noqa: BLE001
        return None, f'{e}: {r.stdout[-300:]}'


def main():
    node = shutil.which('node')
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        arch = mkarchive.make_archive(td / 'a', [
            mkarchive.run_spec('M900801', frames=1000, authors=['Ada'],
                               status={'reproduced': 'community', 'verified': 'none'},
                               reproductions=[{'user': 'Helper', 'date': '2026-08-01'}],
                               reports=[{'id': 1, 'kind': 'spam-malicious', 'by': 'Fan',
                                         'date': '2026-08-02', 'details': 'Test report.',
                                         'status': 'open'}])],
            experts=[{'user': 'Root', 'scope': 'site'},
                     {'user': 'GameExpert', 'scope': 'nes/testgame'}],
            role_events=[{'user': 'Ada', 'role': 'committee', 'action': 'granted',
                          'by': 'founder', 'date': '2026-01-01',
                          'reason': 'fixture: a sitting committee member'}])
        out = td / 'o'
        r = subprocess.run([sys.executable, str(REPO / 'generator/build.py'),
                            str(arch), str(out)], capture_output=True, text=True)
        ck('build succeeds', r.returncode == 0, r.stderr[-300:])
        if r.returncode:
            sys.exit(1)
        assets_dir = out / 'assets'
        # Node picks ES-module vs CommonJS from the extension and the
        # nearest package.json; the emitted assets are .js files a browser
        # only ever loads through <script type="module">, so say as much
        # here or every `import` in them reads as a syntax error.
        (assets_dir / 'package.json').write_text('{"type": "module"}\n')

        if not node:
            print('SKIP runtime checks (node not installed)')
            print('---', len(failures), 'failures')
            sys.exit(1 if failures else 0)

        # the landing page: nav, account probe, news feed. No submit form, no
        # run page, no import page: exactly the context that broke. Its own
        # module (page-home.js) is what the generator wires to home.py.
        home, err = run_page(node, assets_dir, td, 'home',
                              ['navauth', 'navtoggle', 'navlinks', 'bskyfeed'],
                              module='page-home.js')
        ck('the script runs on the landing page', home is not None, err)
        if home:
            ck('no exception on the landing page', not home['errors'], str(home['errors'][:2]))
            ck('the news feed is requested',
               any('app.bsky.feed.getAuthorFeed' in u for u in home['fetched']),
               str(home['fetched']))
            rendered = home['html'].get('bskyfeed', '')
            ck('the feed replaces the loading note', 'Hello from the archive' in rendered,
               rendered[:160])
            ck('the rendered post links back to Bluesky', 'bsky.app/profile/' in rendered)

        # a bare page with nothing but the nav: app.js alone (shared-only;
        # no page module of its own), nothing may throw
        bare, err = run_page(node, assets_dir, td, 'bare', ['navauth'])
        ck('the script runs on a page with only the nav', bare is not None, err)
        if bare:
            ck('no exception on a bare page', not bare['errors'], str(bare['errors'][:2]))

        # the submit page, wired as a logged-in member sees it (page-submit.js,
        # the module submit.py wires). Preview must be usable and Submit must
        # be the button the encode check gates.
        sub_html = (out / 'submit' / 'index.html').read_text()
        session = {'ok': True, 'loggedIn': True, 'user': 'Ada', 'claimed': True,
                   'notifications': 0}
        sub, err = run_real_page(node, assets_dir, td, 'submit', sub_html, session,
                                  module='page-submit.js')
        ck('the script runs on the submit page', sub is not None, err)
        if sub:
            ck('no exception on the submit page', not sub['errors'], str(sub['errors'][:2]))
            st = sub['state']
            ck('Preview is usable as soon as the page loads',
               st.get('s-preview-btn', {}).get('disabled') is False,
               str(st.get('s-preview-btn')))
            ck('Submit is the button the encode check gates',
               st.get('s-submit', {}).get('disabled') is True, str(st.get('s-submit')))
            ck('the form is shown to a logged-in member',
               st.get('submitform', {}).get('hidden') is False, str(st.get('submitform')))

        # and Preview actually renders something when pressed
        pressed, err = run_real_page(node, assets_dir, td, 'preview', sub_html, session,
                                     events=[('s-preview-btn', 'click')], module='page-submit.js')
        ck('pressing Preview runs its handler', pressed is not None, err)
        if pressed:
            ck('pressing Preview raises no error', not pressed['errors'],
               str(pressed['errors'][:2]))
            ck('pressing Preview reveals the preview panel',
               pressed['state'].get('s-preview', {}).get('hidden') is False,
               str(pressed['state'].get('s-preview')))

        # a run page seen by a member who may contribute (page-run.js, the
        # module run_pages.py wires): the acts are folded away so the
        # discussion is reachable, but arming them must still reveal their
        # wrapper, or the act is simply unavailable
        run_html = (out / 'runs' / 'M900801' / 'index.html').read_text()
        member = {'ok': True, 'loggedIn': True, 'user': 'Zed', 'claimed': True,
                  'notifications': 0}
        act, err = run_real_page(node, assets_dir, td, 'acts', run_html, member,
                                  module='page-run.js')
        ck('the script runs on a run page for a member', act is not None, err)
        if act:
            ck('no exception arming the acts', not act['errors'], str(act['errors'][:2]))
            st = act['state']
            for wrap in ('f-repro-wrap', 'f-verify-wrap', 'f-console-wrap'):
                ck(f'{wrap} is revealed for a member who has not acted',
                   st.get(wrap, {}).get('hidden') is False, str(st.get(wrap)))
            ck('the act zone itself is shown',
               st.get('actzone', {}).get('hidden') is False, str(st.get('actzone')))

        # an expert on a run page: the powers that only existed server-side
        expert_session = {'ok': True, 'loggedIn': True, 'user': 'Root',
                          'claimed': True, 'notifications': 0}
        exp, err = run_real_page(node, assets_dir, td, 'expert', run_html, expert_session,
                                  module='page-run.js')
        ck('the script runs for an expert', exp is not None, err)
        if exp:
            ck('no exception arming the expert powers', not exp['errors'],
               str(exp['errors'][:2]))
            st = exp['state']
            ck('a site expert can invalidate a contribution',
               st.get('f-invalidate-wrap', {}).get('hidden') is False,
               str(st.get('f-invalidate-wrap')))
            ck('a site expert can close an open report',
               st.get('f-resolve-wrap', {}).get('hidden') is False,
               str(st.get('f-resolve-wrap')))
            ck('withdrawal stays with the authors: even a site expert never '
               'sees it', st.get('f-withdraw-wrap', {}).get('hidden') is True,
               str(st.get('f-withdraw-wrap')))

        # view-as: a Committee seat borrowing lesser eyes (presentation only)
        demoted = dict(expert_session, __tar={'committee': ['root']},
                       __viewas='member')
        dem, err = run_real_page(node, assets_dir, td, 'viewas-member', run_html, demoted,
                                  module='page-run.js')
        ck('the script runs viewing as a plain member', dem is not None, err)
        if dem:
            ck('no exception under borrowed eyes', not dem['errors'],
               str(dem['errors'][:2]))
            ck('viewing as a member, the expert powers stay hidden',
               dem['state'].get('f-invalidate-wrap', {}).get('hidden') is True,
               str(dem['state'].get('f-invalidate-wrap')))
        lifted = {'ok': True, 'loggedIn': True, 'user': 'Ada', 'claimed': True,
                  'notifications': 0, '__tar': {'committee': ['ada']},
                  '__viewas': 'expert'}
        lif, err = run_real_page(node, assets_dir, td, 'viewas-expert', run_html, lifted,
                                  module='page-run.js')
        ck('the script runs viewing as a site-wide expert', lif is not None, err)
        if lif:
            ck('viewing as a site-wide expert opens the expert powers',
               lif['state'].get('f-invalidate-wrap', {}).get('hidden') is False,
               str(lif['state'].get('f-invalidate-wrap')))
        stale = dict(expert_session, __viewas='member')   # no Committee seat
        stl, err = run_real_page(node, assets_dir, td, 'viewas-stale', run_html, stale,
                                  module='page-run.js')
        ck('the script runs with a stale view-as key', stl is not None, err)
        if stl:
            ck('a view-as key on a non-Committee account changes nothing',
               stl['state'].get('f-invalidate-wrap', {}).get('hidden') is False,
               str(stl['state'].get('f-invalidate-wrap')))

        # a blocked archivist: an empty nav is indistinguishable from a broken
        # page, and that is exactly how this was reported
        blocked, err = run_real_page(node, assets_dir, td, 'blocked', run_html,
                                      {'__blocked': True}, module='page-run.js')
        ck('the script survives an unreachable archivist', blocked is not None, err)
        if blocked:
            ck('no exception when the archivist cannot be reached',
               not blocked['errors'], str(blocked['errors'][:2]))
            ck('the nav says the archivist is unreachable',
               blocked['state'].get('navoffline', {}).get('hidden') is False,
               str(blocked['state'].get('navoffline')))
        if act:
            ck('and says nothing of the sort when it is reachable',
               act['state'].get('navoffline', {}).get('hidden') is not False,
               str(act['state'].get('navoffline')))

        # the expert panel: the powers are real, so the gate is the whole point
        panel_html = (out / 'expert' / 'index.html').read_text()
        root_session = {'ok': True, 'loggedIn': True, 'user': 'Root', 'claimed': True,
                        'notifications': 0}
        pan, err = run_real_page(node, assets_dir, td, 'panel', panel_html, root_session,
                                  module='page-panels.js')
        ck('the script runs on the expert panel', pan is not None, err)
        if pan:
            ck('no exception on the panel', not pan['errors'], str(pan['errors'][:2]))
            st = pan['state']
            ck('an expert is let into the panel',
               st.get('panel', {}).get('hidden') is False, str(st.get('panel')))
            ck('and the annul form waits for the Committee',
               st.get('panel-annul-wrap', {}).get('hidden') is not False,
               'Root is an expert but not on the Committee')
        pan2, err = run_real_page(node, assets_dir, td, 'panel-plain', panel_html, member,
                                   module='page-panels.js')
        ck('the script runs on the panel for a member', pan2 is not None, err)
        if pan2:
            ck('a member who holds no scope is kept out',
               pan2['state'].get('panel', {}).get('hidden') is not False,
               str(pan2['state'].get('panel')))
        pan3, err = run_real_page(node, assets_dir, td, 'panel-committee', panel_html,
                                  {'ok': True, 'loggedIn': True, 'user': 'Ada',
                                   'claimed': True, 'notifications': 0},
                                  module='page-panels.js')
        if pan3:
            # any single Committee member may appoint an expert (2.5.3), so a
            # committee seat opens the panel even with no expert scope
            ck('the panel opens for a Committee member with no scope',
               pan3['state'].get('panel', {}).get('hidden') is False,
               str(pan3['state'].get('panel')))
            ck('no exception for the committee member', not pan3['errors'],
               str(pan3['errors'][:2]))

        # recording a Committee decision lives in the Committee panel now:
        # governance tools live in panels, the members page is about the movies
        members_html = (out / 'authors' / 'index.html').read_text()
        cpanel_html = (out / 'committee' / 'index.html').read_text()
        ck('the decision form left the members page',
           'rolezone' not in members_html
           and 'Record a Committee decision' not in members_html)
        ck('and lives in the committee panel', 'f-role' in cpanel_html
           and 'Record a Committee decision' in cpanel_html)
        comm_session = {'ok': True, 'loggedIn': True, 'user': 'Ada', 'claimed': True,
                        'notifications': 0}
        mem, err = run_real_page(node, assets_dir, td, 'roles', cpanel_html, comm_session,
                                  module='page-panels.js')
        ck('the script runs on the committee panel', mem is not None, err)
        if mem:
            ck('a committee member is offered the panel, decision form included',
               mem['state'].get('cpanel', {}).get('hidden') is False,
               str(mem['state'].get('cpanel')))
        out_, err = run_real_page(node, assets_dir, td, 'roles-plain', cpanel_html, member,
                                   module='page-panels.js')
        ck('the script runs on the committee panel for a member', out_ is not None, err)
        if out_:
            ck('everybody else sees no panel',
               out_['state'].get('cpanel', {}).get('hidden') is not False,
               str(out_['state'].get('cpanel')))

        # a member who is not an expert sees none of it
        plain, err = run_real_page(node, assets_dir, td, 'plain', run_html, member,
                                    module='page-run.js')
        ck('the script runs for a plain member', plain is not None, err)
        if plain:
            for wrap in ('f-invalidate-wrap', 'f-resolve-wrap'):
                ck(f'{wrap} stays hidden from a member',
                   plain['state'].get(wrap, {}).get('hidden') is True,
                   str(plain['state'].get(wrap)))

        # a run page: app.js alone (shared-only) never fetches the feed that
        # is now page-home.js's alone to fetch
        runpage, err = run_page(node, assets_dir, td, 'run',
                                ['navauth', 'act-login', 'likebtn', 'likecount'])
        ck('the script runs on a run page', runpage is not None, err)
        if runpage:
            ck('no exception on a run page', not runpage['errors'], str(runpage['errors'][:2]))
            ck('a run page does not fetch the news feed',
               not any('bsky' in u for u in runpage['fetched']), str(runpage['fetched']))

    print('---', len(failures), 'failures')
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
