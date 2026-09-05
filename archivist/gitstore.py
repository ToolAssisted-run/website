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
import sys
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
    author set. A withdrawn run holds nothing against a resubmission: taking
    a run back and later submitting it again is the author's right, so a
    tombstone never counts as a duplicate.
    Returns (existing run id, why) or (None, None)."""
    same_work = None
    aset = {a.lower() for a in (authors or [])}
    for rj in ARCHIVE.glob('games/*/*/runs/*/run.json'):
        try:
            d = json.loads(rj.read_text())
        except Exception:                                     # noqa: BLE001
            continue
        if d.get('withdrawn'):
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
    git_dir = ARCHIVE / '.git'
    if git_dir.is_dir():
        has_rebase = (git_dir / 'rebase-merge').exists() or (git_dir / 'rebase-apply').exists()
        has_merge = (git_dir / 'MERGE_HEAD').exists()
        has_cherry = (git_dir / 'CHERRY_PICK_HEAD').exists()
        if not (has_rebase or has_merge or has_cherry):
            return
        to_abort = []
        if has_rebase:
            to_abort.append(('git', 'rebase', '--abort'))
        if has_merge:
            to_abort.append(('git', 'merge', '--abort'))
        if has_cherry:
            to_abort.append(('git', 'cherry-pick', '--abort'))
    else:
        to_abort = (('git', 'rebase', '--abort'), ('git', 'merge', '--abort'),
                    ('git', 'cherry-pick', '--abort'))
    for args in to_abort:
        try:
            sh(*args)
        except subprocess.CalledProcessError:
            pass   # nothing of that kind in progress, which is the normal case

_last_refresh = {'t': 0.0}

_serial_cache = {'n': None}

def current_serial():
    """How many commits main carries: a monotonically increasing stamp of
    the archive's state. Every successful write's response carries it, the
    built site's buildstamp carries the one it was built from, and the
    client compares the two to know when a change is actually live."""
    with lock:
        if _serial_cache['n'] is None:
            try:
                _serial_cache['n'] = int(sh('git', 'rev-list', '--count',
                                            'HEAD').stdout)
            except (subprocess.CalledProcessError, ValueError):
                return 0
        return _serial_cache['n']

def checkout_branch():
    _abandon_unfinished_git_state()
    _last_refresh['t'] = time.time()
    try:
        before = sh('git', 'rev-parse', 'HEAD').stdout.strip()
    except subprocess.CalledProcessError:
        before = None
    sh('git', 'fetch', '-q', 'origin')
    try:
        sh('git', 'checkout', '-q', '-f', '-B', BRANCH, f'origin/{BRANCH}')
    except subprocess.CalledProcessError:
        sh('git', 'checkout', '-q', '-f', '-B', BRANCH, 'origin/main')
    # discard anything a failed request left in the worktree, so the next
    # commit carries only what this request writes
    sh('git', 'clean', '-qfd')
    # content that arrived from elsewhere (a manual push, another writer)
    # deserves a fresh site just as much as content committed here
    if before is not None and sh('git', 'rev-parse', 'HEAD').stdout.strip() != before:
        _serial_cache['n'] = None
        import sitebuild
        sitebuild.request_build()

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


class ArchiveInvalid(Exception):
    """A write that would leave the archive breaking its own rules."""


VALIDATE_TIMEOUT = 120

def _touched_keys():
    """Paths this request wrote, plus the run directory that owns each.

    What the validator names in a complaint is a file or a run folder, so
    these are the strings that say "this one is mine".
    """
    keys = set()
    for line in sh('git', 'status', '--porcelain').stdout.splitlines():
        path = line[3:].strip().strip('"').split(' -> ')[-1]
        if not path:
            continue
        keys.add(path)
        parts = path.split('/')
        for i, part in enumerate(parts[:-1]):
            if part == 'runs':
                keys.add('/'.join(parts[:i + 2]))
                break
    return keys


def validate_worktree():
    """The archive's own rules, applied to what this write touched, before
    the commit that would break them.

    validate.py lives in the archive because the archive is written from more
    places than intake: our own commits, the self-import, a maintainer's
    hand. This is the last point at which a bad write can still be refused,
    and 2.8.2 is why refusing beats reporting: nobody may rewrite history, so
    an invalid state that lands is in the archive for good.

    Only complaints about the paths this request wrote can stop it. The
    validator judges the whole archive, and a gate that took its verdict
    whole would turn one bad record anywhere into a total outage of writes:
    the morning two .wch attachments made the archive invalid, every
    submission on the site would have been refused until somebody noticed.
    Problems elsewhere are logged and left to the daily sweep.

    Returns the complaint to refuse on, or None. A validator that could not
    run at all (absent from the checkout, no jsonschema, crashed, hung) has
    said nothing about the content, so it never blocks a member's write.
    """
    script = ARCHIVE / 'validate.py'
    if not script.exists():
        return None            # a fixture archive carries no validator
    try:
        r = subprocess.run([sys.executable, str(script)], cwd=ARCHIVE,
                           capture_output=True, text=True,
                           timeout=VALIDATE_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        LOG.warning('archive validator could not run (%s): the write goes '
                    'through and the daily sweep has the last word', e)
        return None
    if r.returncode == 0:
        return None
    said = ((r.stdout or '') + (r.stderr or '')).strip()
    if 'INVALID' not in said:
        # its own setup complaints ("jsonschema is not installed", "no
        # schemas found") are not a verdict on this content
        LOG.warning('archive validator unavailable: %s', said[:300])
        return None
    problems = [l.strip() for l in said.splitlines() if l.strip().startswith('\u2717')]
    keys = _touched_keys()
    mine = [l for l in problems if any(k in l for k in keys)]
    theirs = [l for l in problems if l not in mine]
    if theirs:
        LOG.warning('the archive carries %d problem(s) this write did not '
                    'cause; the daily sweep reports them: %s',
                    len(theirs), ' | '.join(theirs)[:600])
    return '\n'.join(mine) if mine else None


def commit_push(message):
    sh('git', 'add', '-A')
    bad = validate_worktree()
    if bad:
        LOG.error('refusing a write the archive would reject:\n%s', bad[:4000])
        # put the checkout back the way origin has it, exactly as a failed
        # request's leftovers are discarded, so the next write starts clean
        checkout_branch()
        raise ArchiveInvalid(bad)
    sh('git', 'commit', '-q', '-m', message)
    for attempt in range(2):
        try:
            sh('git', 'push', '-q', 'origin', f'{BRANCH}:{BRANCH}')
            _serial_cache['n'] = None
            import sitebuild
            sitebuild.request_build()       # this host publishes in ~a second
            dispatch_site_rebuild()         # the Pages standby follows behind
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

