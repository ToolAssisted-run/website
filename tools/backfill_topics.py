#!/usr/bin/env python3
"""Give every archived run a discussion topic.

The site shows each run's forum thread on its page, so a run without a topic
has no discussion at all. Intake and the self-service import now create one;
this fills in runs that predate that. Paced, resumable (it skips runs that
already have a topic) and it writes the topic back into run.json.

Usage: backfill_topics.py <archive-dir> [--limit N] [--dry-run]
"""
import argparse
import json
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

FORUM = 'https://forum.toolassisted.run'
SITE = 'https://toolassisted.run'
GAMES_CATEGORY = 12


def api(path, key, payload=None, method='GET', tries=6):
    for attempt in range(tries):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(FORUM + path, data=data, method=method,
                                     headers={'Api-Key': key, 'Api-Username': 'archivist',
                                              'Content-Type': 'application/json',
                                              'Accept': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read() or b'{}')
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(15 * (attempt + 1))
                continue
            raise RuntimeError(f'{e.code}: {e.read()[:200].decode(errors="replace")}')
    raise RuntimeError('rate limited: ' + path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('archive')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--pace', type=float, default=2.0)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    key = (pathlib.Path.home() / '.toolassisted-discourse-api').read_text().strip()
    archive = pathlib.Path(args.archive)

    todo = []
    for rj in sorted(archive.glob('games/*/*/runs/*/run.json')):
        run = json.loads(rj.read_text())
        if run.get('forum') or run.get('withdrawn'):
            continue
        todo.append((rj, run))
    print(f'{len(todo)} runs without a topic')
    if args.limit:
        todo = todo[:args.limit]

    made = 0
    for rj, run in todo:
        system, slug = run['game'].split('/')
        gfile = archive / 'games' / system / slug / 'game.json'
        gtitle = json.loads(gfile.read_text()).get('title', slug)
        goal = (run.get('category') or {}).get('goal', '')
        who = ', '.join(a['user'] for a in run.get('authors', []))
        imported = (run.get('imported') or {}).get('source')
        title = f'{gtitle} ({goal}) by {who} [{run["id"]}]'
        body = (f'**{gtitle}** ({goal}) by {who}.\n\n'
                f'[Run page]({SITE}/runs/{run["id"]}/) · '
                f'[files in the archive](https://github.com/ToolAssisted-run/archive/'
                f'tree/main/games/{system}/{slug}/runs/{run["id"]})'
                + (f' · [original publication]({imported})' if imported else '')
                + ('\n\nImported from TASVideos, where it was verified and reproduced '
                   'before joining this archive.' if imported else
                   '\n\nArchived here; discussion welcome.'))
        if args.dry_run:
            print('  would create:', title[:90])
            continue
        topic = api('/posts.json', key, {'title': title[:255], 'raw': body,
                                         'category': GAMES_CATEGORY,
                                         'tags': [f'{system}-{slug}'[:60]]}, 'POST')
        tid = topic.get('topic_id')
        if not tid:
            print('  ! no topic id for', run['id'], topic)
            continue
        run['forum'] = {'topicId': int(tid), 'url': f'{FORUM}/t/{tid}'}
        rj.write_text(json.dumps(run, indent=1) + '\n')
        made += 1
        if made % 20 == 0:
            print(f'  {made}/{len(todo)}', flush=True)
        time.sleep(args.pace)
    print('topics created:', made)


if __name__ == '__main__':
    main()
