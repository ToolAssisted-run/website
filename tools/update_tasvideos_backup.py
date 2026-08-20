#!/usr/bin/env python3
"""Incremental refresh of the tasvideos-backup repo (new publications only).

Designed for a daily cron. Follows the original crawl discipline documented in
the backup's README: single-threaded, paced requests (default 1.5s), resumable,
skips whatever already exists. A quiet day costs ~15 light JSON requests; a new
publication adds one movie download, one wiki-source fetch and one YouTube
thumbnail.

What it does:
  1. Re-fetches the full publications list from the API (keeps obsoletion
     state current) and rewrites metadata/publications.json.
  2. For publications not previously present: downloads the movie zip
     ({id}M?handler=Download), the raw submission text (Wiki/ViewSource ->
     <pre> markup, stored with the same [#id: title]/[status] header as the
     original crawl), the submission's API record (merged into
     metadata/submissions.json), and a YouTube thumbnail (maxres -> hq).
  3. Commits and pushes if anything changed.

Usage: update_tasvideos_backup.py [--repo DIR] [--pace SECONDS] [--dry-run]
"""
import argparse
import datetime
import html
import json
import pathlib
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

API = 'https://tasvideos.org/api/v1'
SITE = 'https://tasvideos.org'
UA = 'toolAssisted.run backup refresh (single-threaded, paced; contact: eien86@toolassisted.run)'


def fetch(url, pace, binary=False):
    time.sleep(pace)
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
        return (data, dict(r.headers)) if binary else data.decode('utf-8', 'replace')


def fetch_publications(pace):
    # MaxPageSize is 100; ShowObsoleted keeps the full historical catalog;
    # a stable id sort keeps pagination consistent while the site changes.
    pubs, page = [], 1
    while True:
        chunk = json.loads(fetch(
            f'{API}/publications?PageSize=100&CurrentPage={page}'
            f'&ShowObsoleted=true&Sort=%2Bid', pace))
        pubs += chunk
        if len(chunk) < 100:
            return sorted(pubs, key=lambda p: p['id'])
        page += 1


def fetch_notes(sub_id, title, pace):
    page = fetch(f'{SITE}/Wiki/ViewSource?path='
                 + urllib.parse.quote(f'InternalSystem/SubmissionContent/S{sub_id}'), pace)
    m = re.search(r'<pre>(.*)</pre>', page, re.S)
    if not m:
        return None
    return f'[#{sub_id}: {title}]\n[status: Published]\n\n' + html.unescape(m.group(1)).strip() + '\n'


def fetch_movie(pub_id, pace):
    data, headers = fetch(f'{SITE}/{pub_id}M?handler=Download', pace, binary=True)
    cd = headers.get('Content-Disposition') or ''
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', cd)
    name = pathlib.Path(urllib.parse.unquote(m.group(1))).name if m else f'movie-{pub_id}.zip'
    name = re.sub(r'[^A-Za-z0-9._,()\[\]& +-]', '_', name)
    return name, data


def fetch_thumbnail(urls, pace):
    for u in urls or []:
        m = re.search(r'(?:v=|youtu\.be/)([\w-]+)', u)
        if not m:
            continue
        for variant in ('maxresdefault', 'hqdefault'):
            try:
                time.sleep(0.3)
                req = urllib.request.Request(
                    f'https://img.youtube.com/vi/{m.group(1)}/{variant}.jpg',
                    headers={'User-Agent': UA})
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = r.read()
            except Exception:
                continue
            if data.startswith(b'\xff\xd8\xff'):
                return data
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=str(pathlib.Path.home() / 'tasvideos-dumps'))
    ap.add_argument('--pace', type=float, default=1.5)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    repo = pathlib.Path(args.repo)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    print(f'== backup refresh {stamp} ({repo})', flush=True)

    def git(*a, check=True):
        return subprocess.run(['git', '-C', str(repo), *a], check=check,
                              capture_output=True, text=True)

    git('pull', '--ff-only', '-q')

    old = {p['id'] for p in json.loads((repo / 'metadata' / 'publications.json').read_text())}
    pubs = fetch_publications(args.pace)
    # work list: genuinely new publications, plus existing ones with missing
    # artifacts (self-heal: the original crawl can have boundary gaps)
    def incomplete(p):
        return (not list((repo / 'movies').glob(f'M{p["id"]}-*.zip'))
                or not (repo / 'submission-notes' / f'S{p["submissionId"]}.txt').exists())
    new = [p for p in pubs if p['id'] not in old or incomplete(p)]
    genuinely_new = sum(1 for p in new if p['id'] not in old)
    print(f'{len(pubs)} publications on tasvideos, {genuinely_new} new, '
          f'{len(new) - genuinely_new} healing missing artifacts', flush=True)

    if args.dry_run:
        for p in new:
            print(f'  would fetch M{p["id"]}: {p.get("title")}')
        return

    (repo / 'metadata' / 'publications.json').write_text(json.dumps(pubs, indent=0))

    subs = json.loads((repo / 'metadata' / 'submissions.json').read_text())
    have_subs = {s['id'] for s in subs}
    problems = []
    for p in new:
        pid, sid = p['id'], p['submissionId']
        print(f'  M{pid}: {p.get("title")}', flush=True)
        try:
            if not list((repo / 'movies').glob(f'M{pid}-*.zip')):
                name, data = fetch_movie(pid, args.pace)
                (repo / 'movies' / f'M{pid}-{name}').write_bytes(data)
        except Exception as e:
            problems.append(f'M{pid}: movie download failed ({e})')
        try:
            nf = repo / 'submission-notes' / f'S{sid}.txt'
            if not nf.exists():
                notes = fetch_notes(sid, p.get('title') or '', args.pace)
                if notes:
                    nf.write_text(notes)
                else:
                    problems.append(f'M{pid}: submission text S{sid} not extractable')
        except Exception as e:
            problems.append(f'M{pid}: notes fetch failed ({e})')
        try:
            if sid not in have_subs:
                subs.append(json.loads(fetch(f'{API}/submissions/{sid}', args.pace)))
                have_subs.add(sid)
        except Exception as e:
            problems.append(f'M{pid}: submission record fetch failed ({e})')
        tf = repo / 'thumbnails' / f'M{pid}.jpg'
        if not tf.exists():
            thumb = fetch_thumbnail(p.get('urls'), args.pace)
            if thumb:
                tf.write_bytes(thumb)

    subs.sort(key=lambda s: s['id'])
    (repo / 'metadata' / 'submissions.json').write_text(json.dumps(subs, indent=0))

    if not git('status', '--porcelain').stdout.strip():
        print('no changes; nothing to commit', flush=True)
        return
    git('add', 'metadata', 'movies', 'submission-notes', 'thumbnails')
    ids = ', '.join(f'M{p["id"]}' for p in new) or 'metadata only'
    git('commit', '-q', '-m',
        f'Daily refresh: {len(new)} publication(s) fetched ({ids})' +
        ('\n\nProblems:\n' + '\n'.join(problems) if problems else ''))
    git('push', '-q')
    print(f'pushed: {len(new)} new, {len(problems)} problems', flush=True)
    for pr in problems:
        print('  !', pr, flush=True)


if __name__ == '__main__':
    main()
