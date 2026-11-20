"""The archive checkout: run a git command, keep the tree clean before a
write, validate and commit after it, and keep it in step with GitHub from
the side. The lock serialises every mutation of the working tree.

A write never waits on GitHub. It commits to the local checkout, which is
durable on disk, and answers; the syncer (sync_once, run by a background
thread after every commit and on its own clock) fetches, puts whatever
arrived from elsewhere under the commits not yet pushed, and pushes them.
A like used to wait on a fetch, a whole-archive validation and a push,
4 to 6 seconds; it now waits on its own commit."""
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
    """Run a git command in the archive checkout."""
    return subprocess.run(args, cwd=ARCHIVE, check=True, capture_output=True,
                          text=True, env={**os.environ, 'GIT_SSH_COMMAND': GIT_SSH}, **kw)

def next_id():
    """Reserve the next unused run identifier."""
    ids = [int(p.name[1:]) for p in ARCHIVE.glob('games/*/*/runs/M*') if p.name[1:].isdigit()]
    return max([100000] + [i for i in ids if i >= 100000]) + 1

def load_game(system, slug):
    """Load a game record from the archive checkout."""
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
        if _same_run_metadata(d, game_key, goal, frames, aset):
            same_work = d.get('id')
    if same_work:
        return same_work, ('the same game, category, frame count and authors, so it '
                           'looks like the same run saved again')
    return None, None


def _same_run_metadata(run, game_key, goal, frames, authors):
    """Match an imported movie resaved with different bytes but identical work."""
    movie = run.get('movie') or {}
    return (game_key and run.get('game') == game_key
            and (run.get('category') or {}).get('goal') == goal
            and movie.get('frames') and movie.get('frames') == frames
            and authors and {a['user'].lower() for a in run.get('authors', [])} == authors)

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
    """Put the working tree back to the last commit, before a write.

    Local and immediate: nothing here waits on GitHub. Whatever a failed
    request left behind is discarded (an untracked half-written file, a
    corrupted tracked one, a rebase it broke off); commits not yet pushed are
    kept, and the syncer takes them out (sync_once)."""
    _abandon_unfinished_git_state()
    try:
        on = sh('git', 'symbolic-ref', '--short', 'HEAD').stdout.strip()
    except subprocess.CalledProcessError:
        on = None
    if on != BRANCH:
        try:
            sh('git', 'checkout', '-q', '-f', BRANCH)
        except subprocess.CalledProcessError:
            target, _ = _remote_branch_head()
            sh('git', 'checkout', '-q', '-f', '-B', BRANCH, target)
    if sh('git', 'status', '--porcelain').stdout.strip():
        sh('git', 'reset', '-q', '--hard', 'HEAD')
        sh('git', 'clean', '-qfd')


def _is_ancestor(a, b):
    """Is commit a contained in b?"""
    return subprocess.run(['git', 'merge-base', '--is-ancestor', a, b], cwd=ARCHIVE,
                          capture_output=True).returncode == 0


# What the syncer knows, for /api/health and for saying so when it is stuck.
sync_state = {'unpushed_since': None, 'last_push': None, 'last_fetch': None,
              'error': None, 'diverged': False, 'alerted': False}
_sync_wake = threading.Event()
_sync_mutex = threading.Lock()      # guards sync_state only; never held while waiting
STUCK_ALERT_SECONDS = int(os.environ.get('ARCHIVE_STUCK_ALERT_SECONDS', '600'))


def _say(text):
    """Tell Discord, best-effort (imported late: notify imports this module's
    neighbours, and the syncer must never fail on a notice)."""
    try:
        from notify import notify_discord
        notify_discord(text)
    except Exception as e:                                     # noqa: BLE001
        LOG.warning('could not say on Discord: %s', e)


def _sync_failed(stage, err):
    """Note a failed fetch or push; after a while, say so once."""
    said = (getattr(err, 'stderr', None) or str(err) or '').strip()[:300]
    LOG.warning('archive sync: %s failed: %s', stage, said)
    with _sync_mutex:
        sync_state['error'] = f'{stage}: {said}'
        since = sync_state['unpushed_since']
        stuck = (since is not None and time.time() - since > STUCK_ALERT_SECONDS
                 and not sync_state['alerted'])
        if stuck:
            sync_state['alerted'] = True
    if stuck:
        _say(f'\u26a0\ufe0f The archive has had commits waiting to reach GitHub for '
             f'{int((time.time() - since) // 60)} minutes ({stage} fails: {said[:160]}). '
             f'Every write is kept on the server and keeps being accepted; they go out '
             f'as soon as GitHub takes them.')
    return False


def _integrate():
    """Put what arrived on origin under the commits not yet pushed. Runs under
    the lock (nothing is mid-write). True when the tree moved, False when
    nothing arrived, None when the two cannot be put together."""
    _abandon_unfinished_git_state()
    target, remote_head = _remote_branch_head()
    if remote_head is None:
        return False
    head = sh('git', 'rev-parse', 'HEAD').stdout.strip()
    if head == remote_head or _is_ancestor(remote_head, head):
        return False
    if sh('git', 'status', '--porcelain').stdout.strip():
        # leftovers of a request that failed: nothing is mid-write while we
        # hold the lock, so they are nobody's
        sh('git', 'reset', '-q', '--hard', 'HEAD')
        sh('git', 'clean', '-qfd')
    if _is_ancestor(head, remote_head):
        sh('git', 'merge', '-q', '--ff-only', target)
    else:
        try:
            sh('git', 'rebase', '-q', target)
        except subprocess.CalledProcessError as e:
            _abandon_unfinished_git_state()
            said = (e.stderr or e.stdout or '').strip()[:300]
            LOG.error('archive sync: our commits do not go on top of origin: %s', said)
            with _sync_mutex:
                first = not sync_state['diverged']
                sync_state['diverged'] = True
                sync_state['error'] = f'rebase: {said}'
            if first:
                _say('\u26a0\ufe0f The archive on the server and the archive on GitHub both '
                     'changed the same file, and the server\'s new commits do not go on '
                     'top of GitHub\'s by themselves. Writes keep being accepted and kept '
                     'on the server; a person has to put the two together '
                     '(git -C /opt/archivist/archive rebase origin/main, as its owner).')
            return None
    _serial_cache['n'] = None
    _renames['built'] = False       # a pull may carry new claims
    return True


def _fetch_and_integrate():
    """Fetch, then put what arrived under our unpushed commits. True when the
    tree moved, False when nothing arrived, None when it failed."""
    _last_refresh['t'] = time.time()
    try:
        sh('git', 'fetch', '-q', 'origin')
    except subprocess.CalledProcessError as e:
        _sync_failed('fetch', e)
        return None
    with _sync_mutex:
        sync_state['last_fetch'] = time.time()
    with lock:
        moved = _integrate()
    if moved:
        # content that arrived from elsewhere deserves a fresh site as much
        # as content committed here
        import sitebuild
        sitebuild.request_build()
    return moved


def sync_once(fetch=True):
    """Bring origin in (fetch=True) and take our commits out. True when the
    archive and GitHub agree afterwards.

    After a commit the syncer only pushes: that is the common case and needs
    no fetch, which costs half a second of git checking what it received. It
    fetches when the push is refused because GitHub moved, and on its own
    clock. The network is touched with no lock held; the lock is taken only
    to change the tree, which is local and fast, so a write never waits on
    GitHub and nothing here waits on a lock while holding another. Safe to
    run from any thread, at the same time as the syncer."""
    if fetch and _fetch_and_integrate() is None:
        return False
    for attempt in range(2):
        with lock:
            target, _ = _remote_branch_head()
            try:
                ahead = int(sh('git', 'rev-list', '--count', f'{target}..HEAD').stdout)
            except (subprocess.CalledProcessError, ValueError):
                ahead = 0
        if not ahead:
            break
        try:
            sh('git', 'push', '-q', 'origin', f'{BRANCH}:{BRANCH}')
        except subprocess.CalledProcessError as e:
            said = (e.stderr or '') + (e.stdout or '')
            moved_on = any(w in said for w in ('fetch first', 'non-fast-forward', 'rejected'))
            if attempt == 0 and moved_on:
                # GitHub has something we lack: bring it in under ours, again
                if _fetch_and_integrate() is None:
                    return False
                continue
            return _sync_failed('push', e)
        with _sync_mutex:
            sync_state['last_push'] = time.time()
        dispatch_site_rebuild()         # the Pages standby follows behind
        break
    with lock:
        target, _ = _remote_branch_head()
        try:
            waiting = int(sh('git', 'rev-list', '--count', f'{target}..HEAD').stdout)
        except (subprocess.CalledProcessError, ValueError):
            waiting = 0
    if waiting:
        return False                    # a write landed meanwhile: the next pass
    with _sync_mutex:
        recovered = sync_state['alerted'] or sync_state['diverged']
        sync_state.update(unpushed_since=None, error=None, diverged=False, alerted=False)
    if recovered:
        _say('\u2705 The archive is in step with GitHub again: every commit kept on the '
             'server has gone out.')
    return True


_sync_fetch_wanted = threading.Event()   # set: the next pass fetches too
SYNC_SETTLE_SECONDS = float(os.environ.get('ARCHIVE_SYNC_SETTLE_SECONDS', '1.5'))


def start_syncer():
    """The background half of every write: push what is waiting once writes
    pause, and fetch on its own clock so content pushed from elsewhere
    arrives without anybody asking.

    A commit wakes it, and it waits until SYNC_SETTLE_SECONDS pass with no
    further commit (ten seconds at most) before pushing: somebody liking
    several runs in a row commits each like at once and is never slowed by a
    sync running beside it, and the likes go out together."""
    def loop():
        # git's own caches for a large tree: status remembers which folders
        # hold nothing untracked, and the commit-graph each fetch writes
        # makes counting commits (the serial every answer carries) cheap
        for key, value in (('core.untrackedCache', 'true'), ('fetch.writeCommitGraph', 'true')):
            try:
                sh('git', 'config', key, value)
            except subprocess.CalledProcessError:
                pass
        # one validation now, while nobody waits on it: the validator's cache
        # starts the process empty, and a cold run checks the whole archive
        # (seconds), which would otherwise fall on the first member to write
        try:
            with lock:
                validate_worktree()
        except Exception as e:                                 # noqa: BLE001
            LOG.warning('could not warm the validator: %s', e)
        backoff = 0
        while True:
            woken = _sync_wake.wait(timeout=backoff or max(REFRESH_MAX_AGE, 1))
            _sync_wake.clear()
            if woken:
                settle_until = time.time() + 10
                while time.time() < settle_until and _sync_wake.wait(SYNC_SETTLE_SECONDS):
                    _sync_wake.clear()
            fetch = (not woken) or backoff > 0 or _sync_fetch_wanted.is_set()
            _sync_fetch_wanted.clear()
            try:
                ok = sync_once(fetch=fetch)
            except Exception:                                  # noqa: BLE001
                LOG.exception('archive sync failed')
                ok = False
            backoff = 0 if ok else min(300, max(5, backoff * 2))
    threading.Thread(target=loop, daemon=True, name='archive-sync').start()
    _sync_fetch_wanted.set()
    _sync_wake.set()        # whatever a previous process left waiting goes out now


def _remote_branch_head():
    """Choose the configured remote branch or its main fallback."""
    try:
        remote_target = f'origin/{BRANCH}'
        remote_head = sh('git', 'rev-parse', remote_target).stdout.strip()
    except subprocess.CalledProcessError:
        remote_target = 'origin/main'
        try:
            remote_head = sh('git', 'rev-parse', remote_target).stdout.strip()
        except subprocess.CalledProcessError:
            remote_head = None
    return remote_target, remote_head

_distrusted = {'v': False}


def distrust_checkout():
    """Have the next request start from a clean tree, whoever makes it.

    A write that fails somewhere nobody foresaw has already changed files on
    disk and committed none of them. Reads decide from the working tree, so
    the very next request puts it back to the last commit first (locally,
    at once) instead of reading the leftovers as the record.
    """
    _distrusted['v'] = True


def refresh_archive(max_age=None):
    """Make sure the checkout is current before anything is decided from it.

    Permissions are read out of the working tree, so content pushed from
    elsewhere (an appointment, a role event pushed by hand) has to arrive
    without waiting for a write. The syncer fetches on its own clock; a read
    that finds the last fetch older than max_age wakes it and goes on, and
    max_age=0 (publishing, the operator's rebuild, the role reconcile) syncs
    before answering.
    """
    if max_age is None:
        max_age = REFRESH_MAX_AGE
    if _distrusted['v']:
        with lock:
            try:
                checkout_branch()
                _distrusted['v'] = False
            except Exception as e:                             # noqa: BLE001
                LOG.warning('could not clean the checkout: %s', e)
    if max_age <= 0:
        try:
            sync_once()
        except Exception as e:                                 # noqa: BLE001
            # a fetch that fails leaves the previous tree in place, which is
            # stale but usable; refusing every request would be worse
            LOG.warning('archive refresh failed: %s', e)
    elif time.time() - _last_refresh['t'] >= max_age:
        _sync_fetch_wanted.set()
        _sync_wake.set()

def dispatch_site_rebuild():
    """Ask the website repo to rebuild, the moment content landed.

    Fire-and-forget in a background thread: the rebuild is a courtesy of
    speed, never a condition of the write. The archive repo's own CI fires
    the same dispatch a little later as the fallback (the deploy concurrency
    group coalesces the pair), and the six-hourly schedule backstops both."""
    if not WEBSITE_DISPATCH_TOKEN:
        return

    def work():
        """Perform a background persistence operation."""
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


class ArchiveUnreachable(Exception):
    """GitHub would not answer, so the write never began.

    Every write starts by fetching origin, and when that fails there is no
    safe way to continue: the checkout may be behind, and committing on top
    of a stale tree is how two members overwrite each other. Raised instead
    of the bare CalledProcessError, which reached Flask as an HTML 500 that
    the client could not parse, so the page reported the service unreachable
    when it was the credential that had gone.
    """


VALIDATE_TIMEOUT = 120

def _touched_keys():
    """Paths this request wrote, plus the run directory that owns each.

    What the validator names in a complaint is a file or a run folder, so
    these are the strings that say "this one is mine".
    """
    keys = set()
    for line in sh('git', 'status', '--porcelain').stdout.splitlines():
        path = line[3:].strip().strip('"').split(' -> ')[-1].replace('\\', '/')
        if not path:
            continue
        keys.add(path)
        parts = path.split('/')
        for i, part in enumerate(parts[:-1]):
            if part == 'runs':
                keys.add('/'.join(parts[:i + 2]))
                break
    return keys


# A Python that has already imported what the validator needs and forks a
# child for each run: a fresh interpreter importing jsonschema cost 0.14 s of
# every write, about as much as the checks themselves. The child runs the
# archive's own validate.py as its __main__, exactly as a fresh process would.
_VALIDATOR_HOST = r"""
import contextlib, json, os, runpy, sys, traceback
try:
    import jsonschema  # noqa: F401  (what every child would import again)
except ImportError:
    pass
for line in sys.stdin:
    req = json.loads(line)
    pid = os.fork()
    if pid == 0:
        code = 1
        try:
            with open(req['out'], 'w', encoding='utf-8') as out, \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                os.chdir(req['cwd'])
                os.environ.update(req['env'])
                sys.argv = [req['script']]
                try:
                    runpy.run_path(req['script'], run_name='__main__')
                    code = 0
                except SystemExit as e:
                    if e.code is None or isinstance(e.code, int):
                        code = e.code or 0
                    else:
                        print(e.code)
                        code = 1
                except BaseException:
                    traceback.print_exc()
                    code = 1
                out.flush()
        finally:
            os._exit(code)
    _, status = os.waitpid(pid, 0)
    sys.stdout.write(json.dumps({'code': os.waitstatus_to_exitcode(status)}) + '\n')
    sys.stdout.flush()
"""
_vhost = {'proc': None}


def _run_validator(script, extra_env):
    """Run the archive's validate.py; answers a CompletedProcess. Through the
    validator host where there is fork, else (or if the host fails) as a
    fresh process; either way a run that hangs is cut off at
    VALIDATE_TIMEOUT."""
    if os.name != 'nt':
        try:
            return _run_in_host(script, extra_env)
        except subprocess.TimeoutExpired:
            raise
        except Exception as e:                                 # noqa: BLE001
            LOG.warning('validator host unavailable (%s); running it fresh', e)
    return subprocess.run([sys.executable, str(script)], cwd=ARCHIVE,
                          capture_output=True, text=True,
                          encoding='utf-8', errors='replace',
                          timeout=VALIDATE_TIMEOUT, env={**os.environ, **extra_env})


def _run_in_host(script, extra_env):
    import select
    import tempfile
    proc = _vhost['proc']
    if proc is None or proc.poll() is not None:
        proc = subprocess.Popen([sys.executable, '-X', 'utf8', '-c', _VALIDATOR_HOST],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True,
                                start_new_session=True)
        _vhost['proc'] = proc
    fd, out_path = tempfile.mkstemp(prefix='validate-', suffix='.txt')
    os.close(fd)
    try:
        proc.stdin.write(json.dumps({'script': str(script), 'cwd': str(ARCHIVE),
                                     'env': extra_env, 'out': out_path}) + '\n')
        proc.stdin.flush()
        ready, _, _ = select.select([proc.stdout], [], [], VALIDATE_TIMEOUT)
        if not ready:
            # a validator that hangs takes the host with it; the next run
            # starts a new one
            try:
                os.killpg(proc.pid, 9)
            except OSError:
                pass
            _vhost['proc'] = None
            raise subprocess.TimeoutExpired('validate.py', VALIDATE_TIMEOUT)
        line = proc.stdout.readline()
        if not line:
            _vhost['proc'] = None
            raise OSError('the validator host ended')
        code = json.loads(line)['code']
        with open(out_path, encoding='utf-8', errors='replace') as f:
            said = f.read()
        return subprocess.CompletedProcess(['validate.py'], code, said, '')
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def validate_worktree():
    """Reject pre-commit archive errors on touched paths; ignore unrelated
    errors or validator outages. Return the complaint, or None."""
    script = ARCHIVE / 'validate.py'
    if not script.exists():
        return None            # a fixture archive carries no validator
    try:
        # the validator's own result cache, outside the worktree (git never
        # sees it): unchanged games are replayed, so a write is validated in
        # a fraction of a second instead of re-checking the whole archive
        r = _run_validator(script, {'VALIDATE_CACHE':
                                    str(ARCHIVE / '.git' / 'validate-cache.json')})
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
    mine = [l for l in problems if any(k in l.replace('\\', '/') for k in keys)]
    theirs = [l for l in problems if l not in mine]
    if theirs:
        LOG.warning('the archive carries %d problem(s) this write did not '
                    'cause; the daily sweep reports them: %s',
                    len(theirs), ' | '.join(theirs)[:600])
    return '\n'.join(mine) if mine else None


def commit_push(message):
    """Validate and commit the archive worktree; the syncer pushes it.

    The answer a member gets means the write is committed and validated on
    the server, which is durable: it goes out to GitHub in the background,
    and a GitHub outage delays that without refusing anything."""
    sh('git', 'add', '-A')
    bad = validate_worktree()
    if bad:
        LOG.error('refusing a write the archive would reject:\n%s', bad[:4000])
        # put the checkout back to the last commit, exactly as a failed
        # request's leftovers are discarded, so the next write starts clean
        checkout_branch()
        raise ArchiveInvalid(bad)
    sh('git', 'commit', '-q', '-m', message)
    _serial_cache['n'] = None
    with _sync_mutex:
        if sync_state['unpushed_since'] is None:
            sync_state['unpushed_since'] = time.time()
    import sitebuild
    sitebuild.request_build()       # this host publishes from the local checkout
    _sync_wake.set()


def find_run(run_id):
    """Locate a run record by its identifier."""
    for p in ARCHIVE.glob(f'games/*/*/runs/{run_id}/run.json'):
        return p.parent
    return None
