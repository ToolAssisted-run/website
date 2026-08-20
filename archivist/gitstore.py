"""The archive checkout: run a git command, refresh before
reading, commit and push after writing. The lock serialises
every mutation of the working tree."""
import base64
import hashlib
import hmac
import json
import logging
import os
import pathlib
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from settings import (
    LOG,
    WEBSITE_DISPATCH_TOKEN,
    ARCHIVE,
    BRANCH,
    GIT_SSH,
    REFRESH_MAX_AGE,
)
from identity import (
    _renames,
)

lock = threading.RLock()   # re-entrant: a refresh may be taken inside a locked section

def sh(*args, **kw):
    return subprocess.run(args, cwd=ARCHIVE, check=True, capture_output=True,
                          text=True, env={**os.environ, 'GIT_SSH_COMMAND': GIT_SSH}, **kw)

def next_id():
    ids = [int(p.name[1:]) for p in ARCHIVE.glob('games/*/*/runs/M*') if p.name[1:].isdigit()]
    return max([100000] + [i for i in ids if i >= 100000]) + 1

def load_game(system, slug):
    gdir = ARCHIVE / 'games' / system / slug
    if not (gdir / 'game.json').exists():
        return None, None
    return (json.loads((gdir / 'game.json').read_text()),
            json.loads((gdir / 'categories.json').read_text()))

def duplicate_of(sha1, game_key=None, goal=None, frames=None, authors=None):
    """Is this movie already archived? Exact bytes first, then the same work
    submitted again after a re-save: same game, category, frame count and
    author set. Returns (existing run id, why) or (None, None)."""
    same_work = None
    aset = {a.lower() for a in (authors or [])}
    for rj in ARCHIVE.glob('games/*/*/runs/*/run.json'):
        try:
            d = json.loads(rj.read_text())
        except Exception:                                     # noqa: BLE001
            continue
        mv = d.get('movie') or {}
        if sha1 and mv.get('sha1') == sha1:
            return d.get('id'), 'the same movie file'
        if (game_key and d.get('game') == game_key
                and (d.get('category') or {}).get('goal') == goal
                and mv.get('frames') and mv.get('frames') == frames
                and aset and {a['user'].lower() for a in d.get('authors', [])} == aset):
            same_work = d.get('id')
    if same_work:
        return same_work, ('the same game, category, frame count and authors, so it '
                           'looks like the same run saved again')
    return None, None

def _abandon_unfinished_git_state():
    """Leave no half-finished rebase or merge behind. A conflicted rebase
    would otherwise wedge the checkout: every later request's `checkout -B`
    fails too, and intake stays broken until someone logs into the VPS."""
    for args in (('git', 'rebase', '--abort'), ('git', 'merge', '--abort'),
                 ('git', 'cherry-pick', '--abort')):
        try:
            sh(*args)
        except subprocess.CalledProcessError:
            pass   # nothing of that kind in progress, which is the normal case

_last_refresh = {'t': 0.0}

def checkout_branch():
    _abandon_unfinished_git_state()
    _last_refresh['t'] = time.time()
    sh('git', 'fetch', '-q', 'origin')
    try:
        sh('git', 'checkout', '-q', '-f', '-B', BRANCH, f'origin/{BRANCH}')
    except subprocess.CalledProcessError:
        sh('git', 'checkout', '-q', '-f', '-B', BRANCH, 'origin/main')
    # discard anything a failed request left in the worktree, so the next
    # commit carries only what this request writes
    sh('git', 'reset', '-q', '--hard', f'origin/{BRANCH}')
    sh('git', 'clean', '-qfd')

def refresh_archive(max_age=None):
    """Make sure the checkout is current before anything is decided from it.

    Permissions are read out of the working tree, and the tree was only ever
    refreshed on the way to a write. So an appointment made anywhere else, or a
    role event pushed by hand, was invisible until the next write happened to
    come along: /api/expert/sync answered "only site-wide experts may reconcile"
    to the very expert it had on record. Reads refresh themselves now, at most
    once every max_age seconds so a burst of requests costs one fetch.
    """
    if max_age is None:
        max_age = REFRESH_MAX_AGE
    with lock:
        if time.time() - _last_refresh['t'] >= max_age:
            try:
                checkout_branch()
                _renames['built'] = False   # a pull may carry new claims
            except Exception:                                  # noqa: BLE001
                # a fetch that fails leaves the previous tree in place, which is
                # stale but usable; refusing every request would be worse
                pass

def dispatch_site_rebuild():
    """Ask the website repo to rebuild, the moment content landed.

    Fire-and-forget in a background thread: the rebuild is a courtesy of
    speed, never a condition of the write. The archive repo's own CI fires
    the same dispatch a little later as the fallback (the deploy concurrency
    group coalesces the pair), and the six-hourly schedule backstops both."""
    if not WEBSITE_DISPATCH_TOKEN:
        return

    def work():
        try:
            req = urllib.request.Request(
                'https://api.github.com/repos/ToolAssisted-run/website/'
                'actions/workflows/deploy.yml/dispatches',
                method='POST',
                headers={'Authorization': f'Bearer {WEBSITE_DISPATCH_TOKEN}',
                         'Accept': 'application/vnd.github+json'},
                data=json.dumps({'ref': 'main',
                                 'inputs': {'reason': 'archive-content'}}).encode())
            urllib.request.urlopen(req, timeout=15).read()
        except Exception as e:                                 # noqa: BLE001
            LOG.warning('site rebuild dispatch failed (CI will catch up): %s', e)
    threading.Thread(target=work, daemon=True).start()


def commit_push(message):
    sh('git', 'add', '-A')
    sh('git', 'commit', '-q', '-m', message)
    for attempt in range(2):
        try:
            sh('git', 'push', '-q', 'origin', f'{BRANCH}:{BRANCH}')
            dispatch_site_rebuild()
            return
        except subprocess.CalledProcessError:
            if attempt:
                # leave the checkout usable for the next request even though
                # this one failed
                _abandon_unfinished_git_state()
                raise
            try:
                sh('git', 'pull', '-q', '--rebase', 'origin', BRANCH)
            except subprocess.CalledProcessError:
                _abandon_unfinished_git_state()
                raise

def find_run(run_id):
    for p in ARCHIVE.glob(f'games/*/*/runs/{run_id}/run.json'):
        return p.parent
    return None

