"""Publish the site the moment content lands.

The generator rebuilds the whole site in under a second, so the archivist
builds it right here, from its own checkout, straight after every commit:
into a fresh directory, verified complete, then swapped in with one atomic
symlink rename. The web server on this host serves `current`; a reader who
loads a page one moment after a write sees the write.

GitHub Pages keeps deploying in parallel (the CI pipeline is untouched) and
stays a hot standby: if this host dies, the site is one DNS change away.

Disabled automatically when there is no website checkout to build from
(WEBSITE_DIR), which is every test run and local session that does not
opt in.
"""
import os
import pathlib
import shutil
import subprocess
import sys
import threading
import time

from settings import ARCHIVE, LOG

WEBSITE_DIR = pathlib.Path(os.environ.get('WEBSITE_DIR',
                                          str(ARCHIVE.parent / 'website')))

# built sites live beside the checkout, never inside it (refresh git-cleans
# the tree): site/build-<n> directories under a `current` symlink
SITE_DIR = pathlib.Path(os.environ.get('SITE_DIR', str(ARCHIVE.parent / 'site')))

BUILD_TIMEOUT = float(os.environ.get('SITE_BUILD_TIMEOUT', '300'))
SITE_BUILD_LIMIT = int(os.environ.get('SITE_BUILD_LIMIT', '0'))

_wake = threading.Event()
_serial = {'n': 0}          # monotonic build names within one process life
_last = {'ok': None, 'when': 0.0, 'error': None}   # observable via /api/health


def enabled():
    return (WEBSITE_DIR / 'generator' / 'build.py').exists()


def request_build():
    """Called after every successful push and every refresh that moved HEAD.
    Cheap and idempotent: the worker coalesces a burst into one build, and a
    commit landing mid-build simply schedules the next one."""
    if (SITE_DIR / '.pause').exists() or (SITE_BUILD_LIMIT and _serial['n'] >= SITE_BUILD_LIMIT):
        return
    _wake.set()


def _incomplete(out):
    """The same gate the Pages deploy applies: a partial build must never
    replace a whole site. Returns the reason, or None when complete."""
    for f in ('index.html', 'assets/app.js', 'assets/style.css', '404.html'):
        if not (out / f).exists():
            return f'missing {f}'
    runs = len(list(ARCHIVE.glob('games/*/*/runs/*/run.json')))
    rdir = out / 'runs'
    built = sum(1 for p in rdir.iterdir() if p.is_dir()) if rdir.is_dir() else 0
    if runs == 0 or built != runs:
        return f'archive has {runs} runs but the build has {built} run pages'
    if sum(1 for _ in out.rglob('index.html')) < 20:
        return 'suspiciously few pages'
    return None


def _swap(out):
    """Atomically point `current` at the new build by creating a symlink
    beside it and renaming that symlink over `current`. A reader mid-request
    keeps the directory it already opened; the next request gets the new site.
    If an OSError prevents creating or renaming a symlink (for example when
    symlinks aren't supported or across filesystems), fall back to copying
    the build directory into `current` so the site remains available."""
    tmp = SITE_DIR / f'.current-{out.name}'
    if tmp.is_symlink() or tmp.exists():
        if tmp.is_dir() and not tmp.is_symlink():
            shutil.rmtree(tmp)
        else:
            tmp.unlink()
    try:
        os.symlink(out.name, tmp, target_is_directory=True)
        if os.name == 'nt' and (SITE_DIR / 'current').is_symlink():
            (SITE_DIR / 'current').unlink()
        os.replace(tmp, SITE_DIR / 'current')
    except OSError:
        if tmp.is_symlink() or tmp.exists():
            if tmp.is_dir() and not tmp.is_symlink():
                shutil.rmtree(tmp, ignore_errors=True)
            else:
                tmp.unlink(missing_ok=True)
        cur = SITE_DIR / 'current'
        if cur.is_symlink():
            cur.unlink()
        elif cur.exists():
            shutil.rmtree(cur)
        shutil.copytree(out, cur)


def _prune():
    """Keep the serving build and one predecessor; delete the rest."""
    try:
        cur = os.readlink(SITE_DIR / 'current')
    except OSError:
        cur = None
    builds = sorted((p for p in SITE_DIR.glob('build-*') if p.is_dir()),
                    key=lambda p: p.stat().st_mtime, reverse=True)
    keep, kept = set(), 0
    for p in builds:
        if p.name == cur or kept < 2:
            keep.add(p.name)
            kept += p.name != cur
    for p in builds:
        if p.name not in keep:
            shutil.rmtree(p, ignore_errors=True)


def _build_once():
    import gitstore
    _serial['n'] += 1
    out = SITE_DIR / f'build-{int(time.time())}-{_serial["n"]}'
    t0 = time.time()
    # the lock keeps the tree still while the generator reads it, so a build
    # never captures half of a concurrent write; builds take about a second,
    # which a queued request can afford to wait out
    with gitstore.lock:
        r = subprocess.run([sys.executable, '-X', 'utf8', str(WEBSITE_DIR / 'generator' / 'build.py'),
                            str(ARCHIVE), str(out)],
                           capture_output=True, text=True, timeout=BUILD_TIMEOUT)
    if r.returncode != 0:
        shutil.rmtree(out, ignore_errors=True)
        raise RuntimeError(f'generator exited {r.returncode}: '
                           f'{(r.stderr or r.stdout)[-800:]}')
    problem = _incomplete(out)
    if problem:
        shutil.rmtree(out, ignore_errors=True)
        raise RuntimeError(f'build refused: {problem}')
    _swap(out)
    _prune()
    LOG.info('site published: %s in %.2fs', out.name, time.time() - t0)


def _worker():
    while True:
        _wake.wait()
        _wake.clear()
        if (SITE_DIR / '.pause').exists() or (SITE_BUILD_LIMIT and _serial['n'] >= SITE_BUILD_LIMIT):
            continue
        try:
            _build_once()
            _last.update(ok=True, when=time.time(), error=None)
        except Exception as e:                                 # noqa: BLE001
            # the old build keeps serving; CI's Pages deploy is unaffected
            _last.update(ok=False, when=time.time(), error=str(e)[:500])
            LOG.warning('site build failed, previous build keeps serving: %s', e)


def start():
    """Start the builder thread and schedule the first build. A no-op
    without a website checkout, so tests and local runs stay hermetic."""
    if not enabled():
        LOG.info('site builder off: no website checkout at %s', WEBSITE_DIR)
        return
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=_worker, daemon=True).start()
    _wake.set()   # the site this process starts serving should be current
