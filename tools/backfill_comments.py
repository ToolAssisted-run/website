#!/usr/bin/env python3
"""Record who has already spoken in each run's forum topic, once.

The archivist writes a run's `comments[]` pointer from the forum's post
events as they happen. The replies posted before that existed are only on
the forum, so this walks every run that has a topic, reads the topic through
the forum's public JSON, and writes the same pointer the hook would have:
each member's first regular reply there, never the run's own authors, never
the archivist's own account, never a forum account that has no member
record here. The text stays on the forum; the archive gets a username, a
day and a post number.

It touches nothing else and commits nothing: run it over a checkout, read
the summary, run `validate.py`, then commit the result as one commit.

Usage: tools/backfill_comments.py <archive checkout> [--forum URL] [--apply]

Without --apply it only says what it would write.
"""
import argparse
import datetime
import json
import pathlib
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request

BOT_USER = 'archivist'
USERNAME_RE = re.compile(r'[A-Za-z0-9._-]{1,60}')


def fetch_json(url, pause):
    """One GET, answered as JSON; a 429 waits out the forum's minute once."""
    for attempt in range(2):
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    url, headers={'Accept': 'application/json',
                                  'User-Agent': 'toolassisted.run backfill'}), timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and not attempt:
                time.sleep(max(pause, 61))
                continue
            if exc.code in (403, 404, 410):
                return None
            raise
    return None


def topic_posts(forum, topic_id, pause):
    """Every post of a topic, oldest first, following the stream past the
    first page the forum hands out."""
    doc = fetch_json(f'{forum}/t/{topic_id}.json', pause)
    if not doc:
        return None
    stream = doc.get('post_stream') or {}
    posts = list(stream.get('posts') or [])
    have = {p.get('id') for p in posts}
    rest = [pid for pid in (stream.get('stream') or []) if pid not in have]
    for i in range(0, len(rest), 20):
        time.sleep(pause)
        chunk = rest[i:i + 20]
        query = '&'.join(f'post_ids[]={pid}' for pid in chunk)
        more = fetch_json(f'{forum}/t/{topic_id}/posts.json?{query}', pause)
        posts.extend(((more or {}).get('post_stream') or {}).get('posts') or [])
    posts.sort(key=lambda p: p.get('post_number') or 0)
    return posts


# Accents come off as the archivist's settings.fold_accents takes them off
# (website issue 114): Pokémon is pokemon, not pok-mon.
_PLAIN_LETTERS = str.maketrans({'ß': 'ss', 'æ': 'ae', 'œ': 'oe', 'ø': 'o', 'ł': 'l',
                                'đ': 'd', 'ð': 'd', 'þ': 'th', 'ı': 'i'})


def fold_accents(s):
    """Lower-case text with its accents taken off: 'pokémon' -> 'pokemon'."""
    s = unicodedata.normalize('NFKD', s).translate(_PLAIN_LETTERS)
    return ''.join(c for c in s if not unicodedata.combining(c))


def slugify(name):
    """The member record's file name, as the archivist writes it."""
    return re.sub(r'[^a-z0-9._-]+', '-', fold_accents(name.lower())).strip('-.') or 'author'


def load_members(archive):
    """username(lower) -> current username, renames folded."""
    members = {}
    claimed_by = {}
    for f in (archive / 'authors').glob('*.json'):
        a = json.loads(f.read_text(encoding='utf-8'))
        members[a['username'].lower()] = a['username']
        by = (a.get('claimedBy') or '').lower()
        if by and by != a['username'].lower():
            claimed_by[by] = a['username']
    return members, claimed_by


def pointer_for(post):
    stamp = (post.get('created_at') or '')[:19]
    try:
        when = datetime.datetime.strptime(stamp, '%Y-%m-%dT%H:%M:%S')
    except ValueError:
        when = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    return {'user': None, 'date': when.strftime('%Y-%m-%d'),
            'at': when.strftime('%Y-%m-%dT%H:%M:%SZ'), 'post': post['post_number']}


def comments_of(posts, authors, members, claimed_by):
    """The pointers a topic's posts yield, in post order: each member's first
    regular reply, by their current name."""
    out, seen = [], set()
    for post in posts:
        number = post.get('post_number') or 0
        if number < 2 or post.get('post_type') not in (None, 1):
            continue
        poster = post.get('username') or ''
        if poster == BOT_USER or not USERNAME_RE.fullmatch(poster):
            continue
        low = poster.lower()
        current = claimed_by.get(low) or members.get(low)
        if not current:
            continue                      # no record here: nothing to earn under
        cur_low = current.lower()
        if cur_low in authors or cur_low in seen:
            continue
        seen.add(cur_low)
        entry = pointer_for(post)
        entry['user'] = current
        out.append(entry)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('archive', type=pathlib.Path)
    ap.add_argument('--forum', default='https://forum.toolassisted.run')
    ap.add_argument('--pause', type=float, default=1.0, help='seconds between forum calls')
    ap.add_argument('--apply', action='store_true', help='write the pointers into run.json files')
    args = ap.parse_args()
    archive = args.archive
    members, claimed_by = load_members(archive)
    canon = lambda n: (claimed_by.get(n.lower()) or n).lower()   # noqa: E731
    touched = written = skipped = 0
    for rjson in sorted(archive.glob('games/*/*/runs/*/run.json')):
        run = json.loads(rjson.read_text(encoding='utf-8'))
        topic_id = (run.get('forum') or {}).get('topicId')
        if not topic_id or run.get('withdrawn'):
            continue
        posts = topic_posts(args.forum, int(topic_id), args.pause)
        time.sleep(args.pause)
        if posts is None:
            skipped += 1
            print(f'{run["id"]}: topic {topic_id} unreadable, skipped', file=sys.stderr)
            continue
        authors = {canon(a['user']) for a in run.get('authors', [])}
        have = {c['user'].lower() for c in run.get('comments', [])}
        new = [c for c in comments_of(posts, authors, members, claimed_by)
               if c['user'].lower() not in have]
        if not new:
            continue
        touched += 1
        written += len(new)
        print(f'{run["id"]}: {", ".join(c["user"] for c in new)}')
        if args.apply:
            run['comments'] = sorted(run.get('comments', []) + new, key=lambda c: c['post'])
            rjson.write_text(json.dumps(run, indent=1), encoding='utf-8')
    verb = 'written' if args.apply else 'would be written'
    print(f'{written} comment pointer(s) on {touched} run(s) {verb}; {skipped} topic(s) unreadable')


if __name__ == '__main__':
    main()
