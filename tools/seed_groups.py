#!/usr/bin/env python3
"""Seed groups.json from the game groups TASVideos already maintains.

Grouping games into series is slow, thankless curation, and TASVideos has been
doing it for twenty years. Our imported games carry their `tasvideosGameId`, so
their groups come across for free: this reads each game's TASVideos page, keeps
the groups that hold at least two of our games, and writes groups.json.

Read-only against tasvideos.org, one request at a time with a pause between.
Writes exactly one file, and prints it first unless --write is given.

Usage: tools/seed_groups.py [archive] [--write] [--min N]
"""
import html
import json
import pathlib
import re
import sys
import time
import urllib.request

UA = 'toolassisted.run group seeding (one-off, eien86@toolassisted.run)'
PAUSE = 0.7


def get(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode('utf-8', 'replace')


def slug(title):
    s = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
    return s or 'group'


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    write = '--write' in sys.argv
    minimum = 2
    for a in sys.argv[1:]:
        if a.startswith('--min'):
            minimum = int(a.split('=')[1] if '=' in a else 2)
    archive = pathlib.Path(args[0] if args else pathlib.Path.home() / 'ToolAssisted-archive')

    games = {}          # tasvideos game id -> our key
    for gjson in sorted(archive.glob('games/*/*/game.json')):
        g = json.loads(gjson.read_text())
        tid = g.get('tasvideosGameId')
        if tid:
            games[int(tid)] = f'{gjson.parent.parent.name}/{gjson.parent.name}'
    print(f'{len(games)} of our games carry a TASVideos id', file=sys.stderr)

    titles = {}         # group id -> title
    for gid, title in re.findall(r'<a href="/GameGroups/(\d+)">([^<]*)</a>',
                                 get('https://tasvideos.org/GameGroups/List')):
        titles[int(gid)] = html.unescape(title).strip()   # &#x27; is an apostrophe
    print(f'{len(titles)} groups exist upstream', file=sys.stderr)

    members = {}        # group id -> [our keys]
    for i, (tid, key) in enumerate(sorted(games.items()), 1):
        page = get(f'https://tasvideos.org/{tid}G')
        block = re.search(r'Game Groups:(.*?)</ul>', page, re.S)
        found = re.findall(r'/GameGroups/(\d+)', block.group(1)) if block else []
        for gid in found:
            members.setdefault(int(gid), []).append(key)
        print(f'  [{i}/{len(games)}] {key}: '
              f'{", ".join(titles.get(int(g), g) for g in found) or "no group"}',
              file=sys.stderr)
        time.sleep(PAUSE)

    groups = []
    for gid, keys in members.items():
        if len(keys) < minimum:
            continue
        groups.append({'key': slug(titles.get(gid, str(gid))),
                       'title': titles.get(gid, str(gid)),
                       'games': sorted(set(keys))})
    groups.sort(key=lambda g: g['title'].lower())

    seen = set()
    for g in groups:                       # two upstream groups can slugify alike
        base, n = g['key'], 2
        while g['key'] in seen:
            g['key'] = f'{base}-{n}'
            n += 1
        seen.add(g['key'])

    doc = {'comment': 'Game groups (series). A group gathers a game family across '
                      'systems; experts may hold a group-wide scope over one '
                      "(experts.json scope 'group:<key>'). A game may belong to "
                      'more than one group. Seeded from the groups TASVideos '
                      'maintains, and edited here through git.',
           'groups': groups}
    text = json.dumps(doc, indent=1, ensure_ascii=False) + '\n'
    covered = len({k for g in groups for k in g['games']})
    print(f'\n{len(groups)} groups covering {covered} of our {len(games)} games',
          file=sys.stderr)
    if write:
        (archive / 'groups.json').write_text(text)
        print(f'wrote {archive / "groups.json"}', file=sys.stderr)
    else:
        print(text)


if __name__ == '__main__':
    main()
