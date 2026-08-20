#!/usr/bin/env python3
"""News & Events feed: the Bluesky renderer.

The panel renders posts fetched straight from the AT Protocol into our own
markup, which means remote text reaches the page through us: escaping is a
security property here, not a nicety. This runs the emitted renderer under
node against a canned payload shaped like the real API response (including a
hostile post) and checks what it produces.

Hermetic: the payload is canned; nothing is fetched. Needs node, skips without.

Usage: tests/test_bskyfeed.py
"""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mkarchive  # noqa: E402
from test_preview_parity import take_function  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent

PAYLOAD = {'feed': [
    {'post': {
        'uri': 'at://did:plc:abc/app.bsky.feed.post/3mt6sf5ya4s24',
        'likeCount': 4, 'repostCount': 2,
        'record': {'text': 'Beta launch today at https://toolassisted.run !',
                   'createdAt': '2026-08-16T08:46:17.125Z'},
        'embed': {'$type': 'app.bsky.embed.external#view',
                  'external': {'uri': 'https://toolassisted.run/',
                               'title': 'toolAssisted.run',
                               'description': 'An open community archive.',
                               'thumb': 'https://cdn.bsky.app/thumb.jpg'}}}},
    {'post': {
        'uri': 'at://did:plc:abc/app.bsky.feed.post/hostile1',
        'likeCount': 0, 'repostCount': 0,
        'record': {'text': '<img src=x onerror=alert(1)> "quoted" & <script>bad()</script>',
                   'createdAt': '2026-08-15T10:00:00.000Z'},
        'embed': {'$type': 'app.bsky.embed.external#view',
                  'external': {'uri': 'https://evil.example/"onmouseover="alert(1)',
                               'title': '<b>not bold</b>',
                               'description': 'x' * 300}}}},
    {'post': {
        'uri': 'at://did:plc:abc/app.bsky.feed.post/plainpost',
        'likeCount': 1, 'repostCount': 0,
        'record': {'text': 'A plain post with no embed.',
                   'createdAt': '2026-08-14T10:00:00.000Z'}}},
]}

failures = []


def ck(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (f'  [{detail}]' if detail and not cond else ''))
    if not cond:
        failures.append(name)


def main():
    node = shutil.which('node')
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        arch = mkarchive.make_archive(td / 'a', [
            mkarchive.run_spec('M900701', frames=1000, authors=['Ada'])])
        out = td / 'o'
        r = subprocess.run([sys.executable, str(REPO / 'generator/build.py'),
                            str(arch), str(out)], capture_output=True, text=True)
        ck('build succeeds', r.returncode == 0, r.stderr[-300:])
        if r.returncode:
            sys.exit(1)

        home = (out / 'index.html').read_text()
        ck('panel declares the account it reads', 'data-handle="toolassisted.run"' in home)
        ck('panel starts with a loading note', 'Loading the latest posts' in home)
        js = (out / 'assets' / 'app.js').read_text()
        ck('no third-party script is loaded for the feed',
           'platform.twitter.com' not in js and 'widgets.js' not in js)
        ck('the feed is read from the public AT Protocol endpoint',
           'public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed' in js)

        if not node:
            print('SKIP renderer checks (node not installed)')
            print('---', len(failures), 'failures')
            sys.exit(1 if failures else 0)

        harness = '\n'.join(take_function(js, n) for n in ('escH', 'bskyPostHtml')) + """
const linkify = (t) => escH(t).replace(/(https?:\\/\\/[^\\s<]+)/g, (u) => '<a href="' + u + '">' + u + '</a>');
const since = () => '1d ago';
const payload = JSON.parse(process.argv[2]);
console.log(JSON.stringify(payload.feed.map(
  (it) => bskyPostHtml(it, 'https://bsky.app/profile/toolassisted.run', linkify, since))));
"""
        script = td / 'h.mjs'
        script.write_text(harness)
        res = subprocess.run([node, str(script), json.dumps(PAYLOAD)],
                             capture_output=True, text=True)
        ck('renderer runs under node', res.returncode == 0, res.stderr[-400:])
        if res.returncode:
            print('---', len(failures), 'failures')
            sys.exit(1)
        posts = json.loads(res.stdout)
        ck('every post renders', len(posts) == 3, str(len(posts)))
        first, hostile, plain = posts

        ck('post text is shown', 'Beta launch today at' in first)
        ck('links in a post become clickable',
           '<a href="https://toolassisted.run">' in first
           or '<a href="https://toolassisted.run/">' in first, first[:160])
        ck('the post links back to Bluesky',
           'bsky.app/profile/toolassisted.run/post/3mt6sf5ya4s24' in first)
        ck('like and repost counts are shown', '4' in first and '2' in first)
        ck('an external embed renders as a card',
           'class="bcard"' in first and 'toolAssisted.run' in first)

        ck('hostile post text cannot inject markup',
           '<img src=x' not in hostile and '<script>' not in hostile, hostile[:200])
        ck('hostile text is escaped instead', '&lt;img src=x' in hostile)
        ck('a hostile embed url cannot break out of its attribute',
           'onmouseover=' not in hostile.replace('&quot;onmouseover=', ''), hostile[:200])
        ck('embed titles are escaped', '&lt;b&gt;not bold' in hostile)
        ck('long descriptions are trimmed', len(hostile) < 1200, str(len(hostile)))

        ck('a post without an embed still renders',
           'A plain post with no embed.' in plain and 'bcard' not in plain)

    print('---', len(failures), 'failures')
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
