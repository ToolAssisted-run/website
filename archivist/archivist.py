"""toolAssisted.run archivist — the executable Flask service.

Response hooks, startup workers and shared request helpers live here;
feature-grouped HTTP endpoints register from routes/. The layers they
drive are their own modules (see each one's docstring):

  settings.py  environment and limits
  webutil.py   the JSON error shape
  identity.py  sessions, renames, request identity, email masking
  gitstore.py  the archive checkout (git), locking, refresh, commit+push
  notify.py    Discord notifications
  forumapi.py  Discourse (topics, PMs, role-group projections)
  records.py   members, roles, groups, claims, and the public logs

Every route answers JSON; the site (a static build) is the only frontend,
and it talks to this service through these endpoints alone.
"""
import base64
import datetime
import hashlib
import hmac
import io
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

from flask import Flask, jsonify, make_response, redirect, render_template, request

import movieparse
import providers
import selfimport

from settings import (
    now_iso,
    ACT_NOTES_MAX,
    ARCHIVE,
    allowed_attach_exts,
    allowed_movie_exts,
    ATTACH_MAX_COUNT,
    ATTACH_MAX_EACH,
    ATTACH_MAX_TOTAL,
    BOT_USER,
    BRANCH,
    DISCOURSE_HOOK_SECRET,
    GITHUB_HOOK_SECRET,
    SITE_SYNC_CMD,
    DISCOURSE_KEY,
    DISCOURSE_URL,
    DUMPS_DIR,
    IMAGE_MAGIC,
    LOG,
    MOVIE_MAX,
    VISITS_FILE,
    NOTES_MAX,
    RECONCILE_SECONDS,
    SELF_URL,
    SESSION_TTL,
    SHOT_MAX_EACH,
    SHOT_MAX_TOTAL,
    SITE_ORIGIN,
    SITE_ORIGINS,
    SITE_URL,
    SSO_SECRET,
    SUBMIT_KEY,
    THUMB_FETCH_BASE,
    THUMB_MAX,
    slugify,
)
from webutil import (
    fail,
)
from identity import (
    current_name,
    origin_ok,
    run_authors_now,
    session_token,
    session_user,
    sso_sign,
)
from gitstore import (
    ArchiveInvalid,
    checkout_branch,
    commit_push,
    current_serial,
    duplicate_of,
    find_run,
    load_game,
    lock,
    next_id,
    refresh_archive,
)
from notify import (
    movie_md,
    member_md,
    notify_discord,
    category_label,
    replay_spool,
)
from records import (
    already_covers,
    append_role_event,
    case_derived_status,
    covers_group,
    ensure_member,
    expert_covers,
    expert_covers_system,
    held_roles,
    is_committee,
    is_editor,
    is_founder,
    is_site_expert,
    is_uncl_run,
    load_claims,
    load_emulators,
    load_experts,
    load_groups,
    log_deletion,
    log_edit,
    may_decide_claims,
    next_report_id,
    note_new_member,
    save_claims,
    save_emulators,
    save_groups,
    scope_covers,
    scope_exists,
    sync_status,
)
from forumapi import (
    close_announce_topic,
    _forum_get,
    avatar_for,
    committee_size,
    count_votes,
    votes_cast,
    ensure_game_topic,
    ensure_topic,
    forum_account_exists,
    member_email_masked,
    publish_group,
    publish_roles,
    reserved_usernames,
    read_committee_poll,
    send_pm,
    sync_expert_group,
    topics_for_imported,
    unlock_forum_username,
)

MOVIE_TOO_LARGE = f'movie exceeds {MOVIE_MAX >> 20} MB'
MOVIE_MUST_BE_NON_EMPTY_AND_UNDER_MAX = (
    f'movie must be non-empty and under {MOVIE_MAX >> 20} MB'
)
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

app.config['MAX_CONTENT_LENGTH'] = 96 * 1024 * 1024

sso_nonces = {}   # nonce -> expiry

@app.errorhandler(ArchiveInvalid)
def archive_invalid(e):
    """A write the archive's own rules reject never became a commit.

    Who: any endpoint that writes, through commit_push
    Reads: the validator's complaint
    Answers: 422 {ok: false, error, detail} and an unchanged archive

    The member sees that nothing was written and why, rather than a 500
    that leaves them guessing whether it landed. Nothing was: the checkout
    is back at origin's state before this is raised.
    """
    LOG.error('write refused by the archive\'s own rules: %s', str(e)[:1000])
    said = str(e).strip()
    # the validator names a path on this server; a member reads a run id
    first = (said.splitlines() or [''])[0].replace(f'{ARCHIVE}/', '').strip()
    return jsonify({'ok': False,
                    'error': f'the archive will not hold this as written: '
                             f'{first}. Nothing was written.',
                    'detail': said[:2000]}), 422

@app.after_request
def cors(resp):
    """Let the site's origin call this service with credentials: CORS
    headers on /api/*, /login and /logout.

    Who: every response (after_request hook), no auth of its own
    Reads: the request path
    Answers: the same response, with Access-Control-* and Vary headers added
    """
    if request.path.startswith('/api/') or request.path in ('/login', '/logout'):
        # the caller's own origin when we know it, since a credentialed
        # request cannot be answered with a list or a wildcard
        asked = request.headers.get('Origin')
        resp.headers['Access-Control-Allow-Origin'] = (
            asked if asked in SITE_ORIGINS else SITE_ORIGIN)
        resp.headers['Access-Control-Allow-Credentials'] = 'true'
        resp.headers['Vary'] = 'Origin'
    return resp

@app.after_request
def stamp_serial(resp):
    """A successful write answers with the archive revision it left behind.

    The built site's assets/buildstamp.json carries the revision it was
    built from; the client holds its confirmation message until the served
    stamp reaches the response's serial, so "done" only ever means "and you
    can see it". Dry runs change nothing and carry nothing."""
    if (request.method == 'POST' and request.path.startswith('/api/')
            and resp.status_code == 200
            and resp.mimetype == 'application/json'):
        try:
            body = json.loads(resp.get_data())
        except Exception:                                     # noqa: BLE001
            return resp
        if isinstance(body, dict) and body.get('ok') and not body.get('dry_run') \
                and 'serial' not in body:
            body['serial'] = current_serial()
            resp.set_data(json.dumps(body))
    # the hardening headers (OWASP ZAP pass, 2026-08-22): the API answers
    # JSON and is never framed; the fallback form page is the one HTML
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('X-Frame-Options', 'DENY')
    resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    resp.headers.setdefault('Content-Security-Policy',
                            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
                            if request.path.startswith('/api/') else
                            "default-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'")
    return resp

@app.get('/login')
def login():
    """Redirect to the forum (DiscourseConnect provider) to authenticate.

    Who: anybody
    Reads: nothing; mints a nonce that the callback must return
    Answers: a 302 to the forum SSO provider; 501 when SSO is not configured
    """
    if not SSO_SECRET:
        return fail('SSO is not configured', 501)
    nonce = secrets.token_urlsafe(16)
    now = time.time()
    for n in [n for n, exp in sso_nonces.items() if exp < now]:
        del sso_nonces[n]
    sso_nonces[nonce] = now + 600
    payload = urllib.parse.urlencode({'nonce': nonce,
                                      'return_sso_url': f'{SELF_URL}/login/callback'})
    b64 = base64.b64encode(payload.encode())
    return redirect(f'{DISCOURSE_URL}/session/sso_provider?'
                    + urllib.parse.urlencode({'sso': b64.decode(), 'sig': sso_sign(b64)}))

@app.get('/login/callback')
def login_callback():
    """The forum's SSO return leg: check the signature and the nonce, note the
    member, and set the session cookie.

    Who: anybody carrying a payload the forum signed
    Reads: query args `sso` (base64 payload with nonce, username, external_id) and `sig`
    Answers: a 302 to the site root with the tar_session cookie; 403 on a bad
        signature or nonce; 502 when the payload names no user
    """
    sso = request.args.get('sso', '')
    sig = request.args.get('sig', '')
    if not sso or not hmac.compare_digest(sig, sso_sign(sso.encode())):
        return fail('bad SSO signature', 403)
    fields = urllib.parse.parse_qs(base64.b64decode(sso).decode())
    nonce = (fields.get('nonce') or [''])[0]
    if nonce not in sso_nonces or sso_nonces.pop(nonce) < time.time():
        return fail('unknown or expired SSO nonce', 403)
    username = (fields.get('username') or [''])[0]
    external_id = (fields.get('external_id') or [''])[0]
    if not username:
        return fail('SSO response has no username', 502)
    note_new_member(username)
    resp = make_response(redirect(SITE_ORIGIN + '/'))
    resp.set_cookie('tar_session', session_token(username, external_id),
                    max_age=SESSION_TTL, secure=True, httponly=True, samesite='None')
    return resp

@app.get('/logout')
def logout():
    """End the session HERE and on the forum — otherwise 'Log in' silently
    re-authenticates against the still-live Discourse session.

    Who: anybody; needs no session to succeed
    Reads: nothing
    Answers: a 302 to the forum logout (or the site root when SSO is off) with the
        tar_session cookie cleared
    """
    back = SITE_ORIGIN + '/'
    if SSO_SECRET:
        payload = urllib.parse.urlencode({'return_sso_url': back, 'logout': 'true'})
        b64 = base64.b64encode(payload.encode())
        target = (f'{DISCOURSE_URL}/session/sso_provider?'
                  + urllib.parse.urlencode({'sso': b64.decode(), 'sig': sso_sign(b64)}))
    else:
        target = back
    resp = make_response(redirect(target))
    resp.set_cookie('tar_session', '', max_age=0, secure=True, httponly=True,
                    samesite='None')
    return resp

@app.get('/api/me')
def me():
    """Who the session cookie says is logged in.

    Who: anybody
    Reads: the tar_session cookie only
    Answers: {ok, user, loggedIn, avatar}
    """
    username = session_user()
    return jsonify({'ok': True, 'user': username, 'loggedIn': bool(username),
                    'avatar': avatar_for(username) if username else None})

def auth_precheck(form):
    """Cheap auth gate to run BEFORE any git work: a valid session or the
    shared key. Full identity resolution happens later in request_identity."""
    if session_user():
        if not origin_ok():
            return fail('cross-origin request refused', 403)
        return None
    if form.get('key') == SUBMIT_KEY:
        return None
    return fail('log in via the forum, or provide the submitter key', 403)

def request_identity(form, field='user'):
    """Who is acting: a logged-in session's username wins; otherwise the shared
    key plus an explicit username (operator/v0 path). Returns (user, error)."""
    session_name = session_user()
    if session_name:
        if not origin_ok():
            return None, fail('cross-origin request refused', 403)
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{2,29}', session_name):
            return None, fail('session username is not archive-safe', 400)
        return session_name, None
    if form.get('key') != SUBMIT_KEY:
        return None, fail('log in via the forum, or provide the submitter key', 403)
    user = (form.get(field) or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{2,29}', user):
        return None, fail(f'{field} must be a valid username')
    return user, None

# ---- write pacing (log-flooding defence) ----
# Every write is a git commit, a rebuild, and often a log entry, so a
# scripted flood of likes or edits would swell the history and the site
# log without limit. Writes are paced per member, in memory: honest use
# never notices, a script hits the wall. The operator key (the archivist
# bot's own imports and the test harness) is never paced, and nginx holds
# a per-IP backstop in front of all of this.
WRITE_PACE = {          # kind -> (calls allowed, per seconds)
    'like': (12, 600),         # a dozen votes in ten minutes
    'edit': (40, 3600),        # revisions of one's own work (a save is a dry run + a write)
    'act': (30, 3600),         # reproductions, verifications
    'submit': (12, 3600),      # new runs
    'report': (6, 3600),       # reports and cases
    'create': (20, 3600),      # games, categories, groups
}
_pace = {}
_pace_lock = threading.Lock()

def pace_gate(form, user, kind):
    """fail(429) when this member is writing faster than people do."""
    if form.get('key') == SUBMIT_KEY:
        return None                       # the operator path is never paced
    cap, window = WRITE_PACE[kind]
    now = time.monotonic()
    with _pace_lock:
        stamps = _pace.setdefault((user.lower(), kind), [])
        stamps[:] = [t for t in stamps if now - t < window]
        if len(stamps) >= cap:
            return fail(f'easy there: at most {cap} of these each '
                        f'{window // 60} minutes. The archive is permanent; '
                        f'it can wait a moment.', 429)
        stamps.append(now)
    return None


def act_common(form):
    """Shared validation for /api/reproduce and /api/verify.
    Returns (error_response, run_dir, run, user) — error_response is None on success."""
    user, error = request_identity(form)
    if error:
        return error, None, None, None
    paced = pace_gate(form, user, 'act')
    if paced:
        return paced, None, None, None
    run_id = (form.get('run') or '').strip()
    if not re.fullmatch(r'M[0-9]+', run_id):
        return fail('run must be a run id like M100001'), None, None, None
    run_dir = find_run(run_id)
    if not run_dir:
        return fail(f'unknown run {run_id}', 404), None, None, None
    run = json.loads((run_dir / 'run.json').read_text())
    if run.get('withdrawn'):
        return fail(f'{run_id} has been withdrawn; no further acts apply'), None, None, None
    if run.get('status', {}).get('reproduced') == 'imported':
        return fail('Imported runs are irrevocably verified; no further acts apply'), None, None, None
    if current_name(user).lower() in run_authors_now(run):
        return fail('authors cannot act on their own run'), None, None, None
    notes = (form.get('notes') or '').strip()
    if len(notes) > ACT_NOTES_MAX:
        return fail(f'notes exceed {ACT_NOTES_MAX} characters'), None, None, None
    return None, run_dir, run, user

@app.get('/api/health')
def health():
    """Observable health status of the live origin and its background site builder."""
    import sitebuild
    return jsonify({
        'ok': True,
        'build': sitebuild._last,
        'serial': current_serial(),
        'archive': str(ARCHIVE),
    })

@app.post('/api/build/rebuild')
def trigger_rebuild():
    """Operator endpoint to refresh archive and trigger an immediate site rebuild."""
    key = request.headers.get('X-Submit-Key') or request.form.get('key') or (request.get_json(silent=True) or {}).get('key')
    if not hmac.compare_digest(str(key or ''), str(SUBMIT_KEY)):
        return fail('forbidden', 403)
    import sitebuild
    try:
        refresh_archive(0)
    except Exception as exc:                                   # noqa: BLE001
        LOG.warning('rebuild archive refresh failed: %s', exc)
    sitebuild.request_build()
    return jsonify({'ok': True, 'syncing': True})

@app.get('/')
def form():
    """The archivist's own minimal HTML page: submit, reproduce and verify
    forms for the shared-key operator path (the site is the real frontend).

    Who: anybody may load it; the forms it posts need the submitter key
    Reads: nothing
    Answers: an HTML page
    """
    games = sorted(f'{game_file.parent.parent.name}/{game_file.parent.name}'
                  for game_file in ARCHIVE.glob('games/*/*/game.json'))
    return render_template('submit.html', games=games)

def _attachment_content(name, data, suffix, movie_exts, attach_exts):
    """Check one upload's extension, size and text encoding before archiving."""
    if suffix.lstrip('.') in movie_exts:
        if len(data) > MOVIE_MAX:
            return None, fail(f'movie attachment {name!r} exceeds {MOVIE_MAX >> 20} MB')
        return True, None
    if suffix in attach_exts:
        if len(data) > ATTACH_MAX_EACH:
            return None, fail(f'attachment {name!r} exceeds 128 KB')
        try:
            data.decode('utf-8')
        except UnicodeDecodeError:
            return None, fail(f'attachment {name!r} is not valid UTF-8 text')
        return False, None
    return None, fail(f'attachment {name!r}: only text/config files or '
                      f'movie formats are allowed')

def read_attachments(existing=None):
    """Validate the request's uploaded 'attachments': text configs (UTF-8,
    size-capped) or additional movie files. `existing` counts a run's
    current attachments against the caps. Returns ([(name, bytes)], error)."""
    existing = existing or []
    attachments = []
    total = 0
    # what this service can read, narrowed to what the archive will hold:
    # taking a file the archive refuses archives it and then breaks the
    # archive, so the door is the place to say no
    movie_exts, attach_exts = allowed_movie_exts(), allowed_attach_exts()
    movie_atts = sum(1 for a in existing
                     if pathlib.Path(a['file']).suffix.lower().lstrip('.') in movie_exts)
    for upload in request.files.getlist('attachments'):
        if not upload.filename:
            continue
        name = re.sub(r'[^A-Za-z0-9._-]', '_', pathlib.Path(upload.filename).name)
        suffix = pathlib.Path(name).suffix.lower()
        data = upload.read()
        is_movie, error = _attachment_content(name, data, suffix, movie_exts, attach_exts)
        if error:
            return None, error
        if is_movie:
            movie_atts += 1
        else:
            total += len(data)
        attachments.append((name, data))
    if len(attachments) + len(existing) > ATTACH_MAX_COUNT:
        return None, fail('too many attachments (max 8)')
    if movie_atts > 4:
        return None, fail('too many movie attachments (max 4)')
    if total > ATTACH_MAX_TOTAL:
        return None, fail('text attachments exceed 512 KB total')
    return attachments, None


# ---- visit counter: a public tally, not an archive fact ----
# Counted when the run page's script pings in, so plain crawlers do not
# inflate it. No auth: a visit is anonymous by nature and nothing but a
# number is stored.
_visits_lock = threading.Lock()
try:
    _visits = json.loads(VISITS_FILE.read_text())
except (OSError, ValueError):
    _visits = {}
_visit_seen = {}   # (ip, run) -> monotonic time of the counted visit

def _expire_visit_addresses(now):
    """Bound the in-memory per-address visit deduplication window."""
    if len(_visit_seen) > 100_000:
        cutoff = now - 3600
        for k in [k for k, t in _visit_seen.items() if t < cutoff]:
            del _visit_seen[k]

@app.post('/api/visit')
def visit():
    """Count one visit to a run page.

    Who: anybody; no auth, nothing but a number is stored
    Reads: form field run
    Answers: {ok, run, visits}
    """
    run_id = (request.form.get('run') or '').strip()
    if not re.fullmatch(r'M[0-9]+', run_id):
        return fail('run must be an id like M100001')
    if not find_run(run_id):
        return fail(f'unknown run {run_id}', 404)
    # one count per address per run per hour: a reload is not a new reader,
    # and a scripted loop must not inflate the number (the address is never
    # stored beyond this in-memory hour)
    ip = request.headers.get('X-Real-IP') or request.remote_addr or '?'
    now = time.monotonic()
    with _visits_lock:
        seen_at = _visit_seen.get((ip, run_id))
        if seen_at is not None and now - seen_at < 3600:
            return jsonify({'ok': True, 'run': run_id, 'visits': _visits.get(run_id, 0)})
        _visit_seen[(ip, run_id)] = now
        _expire_visit_addresses(now)
        _visits[run_id] = _visits.get(run_id, 0) + 1
        count = _visits[run_id]
        try:
            VISITS_FILE.write_text(json.dumps(_visits))
        except OSError as exc:
            LOG.warning('visits file not writable: %s', exc)
    return jsonify({'ok': True, 'run': run_id, 'visits': count})

def notify_edit(who, what, fields, voided=(), reason='', link=None):
    """Say on Discord that the record changed: who, what, which fields, and
    what it cost. Every edit is already in edits.json and in git; this is
    the same fact where people are, and it is one line like every other
    notice. A reason is quoted when there is one (an author revising their
    own work owes none)."""
    line = (f'\u270e **{member_md(who)}** edited {what}: '
            + ', '.join(fields))
    if voided:
        line += ' (' + ' and '.join(voided) + ' invalidated)'
    if reason:
        line += f' \u00b7 {" ".join(reason.split())[:160]}'
    notify_discord(line, wait_for=link)


def in_category(run):
    """", <the category>" for a Discord line, or nothing when the archive
    cannot name it. Every act notice ends this way: what somebody did, to
    which run, in which category."""
    label = category_label(run)
    return f', {label}' if label else ''




def _metric_definition(row):
    """Validate a single explicit scoring metric or the reserved time row."""
    if not isinstance(row, dict):
        return None, 'each metric is an object'
    if row.get('key') == 'time':
        return {'key': 'time', 'label': str(row.get('label') or 'Time')[:40],
                'type': 'time', 'better': 'lower'}, None
    label = str(row.get('label') or '').strip()[:40]
    if not label:
        return None, 'a metric needs a label'
    key = slugify(str(row.get('key') or label))
    if not key or key == 'unclassified':
        return None, f'bad metric key for {label!r}'
    metric_type = row.get('type')
    better = row.get('better')
    if metric_type not in ('time', 'number'):
        return None, f'{label}: type must be time or number'
    if better not in ('lower', 'higher'):
        return None, f'{label}: better must be lower or higher'
    metric = {'key': key, 'label': label, 'type': metric_type, 'better': better}
    unit = str(row.get('unit') or '').strip()[:12]
    if unit:
        metric['unit'] = unit
    return metric, None

def parse_metric_defs(raw):
    """The metric rows a creation form sends, validated: (defs, error).

    JSON array, at most 4 entries of {key?, label, type, better, unit?};
    keys derive from labels; 'time' is the reserved derived metric and may
    appear as a bare {"key": "time"} row placed anywhere in the hierarchy.
    An empty/absent value means the classic category (no metrics field)."""
    raw = (raw or '').strip()
    if not raw:
        return None, None
    try:
        rows = json.loads(raw)
    except ValueError:
        return None, 'metrics must be a JSON array'
    if not isinstance(rows, list) or len(rows) > 4:
        return None, 'metrics: at most four, as a JSON array'
    defs, seen = [], set()
    for row in rows:
        metric, error = _metric_definition(row)
        if error:
            return None, error
        if metric['key'] in seen:
            return None, f'duplicate metric key {metric["key"]!r}'
        seen.add(metric['key'])
        defs.append(metric)
    return (defs or None), None


def _category_gate(form, need_expert=True):
    """Shared by the category endpoints: who is asking, over which game, and
    the game's categories document. Creation is everybody's; everything else
    needs a covering expert. Returns (actor, game_key, categories_file, categories, error)."""
    actor, error = request_identity(form, 'expert' if need_expert else 'user')
    if error:
        return None, None, None, None, error
    game_key = (form.get('game') or '').strip()
    if not re.fullmatch(r'[a-z0-9-]+/[a-z0-9-]+', game_key):
        return None, None, None, None, fail('game must be system/slug')
    categories_file = ARCHIVE / 'games' / game_key / 'categories.json'
    if not categories_file.exists():
        return None, None, None, None, fail(f'unknown game {game_key}', 404)
    if need_expert and not expert_covers(actor, game_key) \
            and not is_editor(actor):
        return None, None, None, None, fail(
            f'{actor!r} is not an expert covering {game_key}, nor an editor', 403)
    return actor, game_key, categories_file, json.loads(categories_file.read_text()), None



_search_cache = {'at': 0.0, 'members': [], 'games': []}

def _search_members():
    """Read member usernames for the search picker, skipping unreadable records."""
    members = []
    for author_file in (ARCHIVE / 'authors').glob('*.json'):
        try:
            members.append(json.loads(author_file.read_text())['username'])
        except (OSError, ValueError, KeyError):
            continue
    return members

def _search_groups():
    """Read group membership for game search without requiring groups to exist."""
    groups_file = ARCHIVE / 'groups.json'
    if groups_file.exists():
        try:
            return json.loads(groups_file.read_text()).get('groups', [])
        except (ValueError, AttributeError):
            pass
    return []

def _search_games(group_of):
    """Index visible game titles and their group for the pickers."""
    games = []
    for game_file in ARCHIVE.glob('games/*/*/game.json'):
        try:
            game = json.loads(game_file.read_text())
        except (OSError, ValueError):
            continue
        if game.get('rejected') or game.get('removed'):
            continue
        key = f'{game_file.parent.parent.name}/{game_file.parent.name}'
        games.append({'key': key, 'title': game.get('title', key),
                      'system': game_file.parent.parent.name, 'group': group_of.get(key, '')})
    return games

def _search_runs(titles):
    """Index runs using the game titles shown by the search picker."""
    run_rows = []
    for run_file in ARCHIVE.glob('games/*/*/runs/*/run.json'):
        try:
            run_doc = json.loads(run_file.read_text())
        except (OSError, ValueError):
            continue
        goal_txt = (run_doc.get('category') or {}).get('goal', '')
        run_rows.append({'key': run_doc.get('id', run_file.parent.name),
                         'title': f"{titles.get(run_doc.get('game'), run_doc.get('game', ''))} \u00b7 {goal_txt}",
                         'system': (run_doc.get('game') or '/').split('/')[0], 'group': ''})
    return run_rows

def _search_index():
    """Members and games, as the pickers search them (issue #56): read from
    the checkout and kept for 20 s, so typing never hits the disk per key."""
    now = time.monotonic()
    if now - _search_cache['at'] > 20:
        refresh_archive()
        members = _search_members()
        groups = _search_groups()
        group_of = {k: gr['key'] for gr in groups for k in gr.get('games', [])}
        games = _search_games(group_of)
        titles = {g['key']: g['title'] for g in games}
        run_rows = _search_runs(titles)
        _search_cache.update(at=now, members=sorted(members, key=str.lower),
                             games=sorted(games, key=lambda g: g['title'].lower()),
                             runs=sorted(run_rows, key=lambda x: x['key']))
    return _search_cache

# ---- is a name free, taken, or held for somebody? ----
_name_seen = {}          # name -> (answered at, state), a minute's memory












def _deletion_gate(form, need='expert'):
    """Common to every delete: who is asking, and why, said properly."""
    actor, error = request_identity(form, 'expert')
    if error:
        return None, None, error
    reason = (form.get('reason') or '').strip()
    if not (8 <= len(reason) <= 500):
        return None, None, fail('say why, publicly: a deletion is permanent and the '
                                'log entry is all that remains of it')
    return actor, reason, None

# the plain game properties (#44): release date, unofficial flag, community
# links. One parser for the editor and for creation; an empty value means
# "not stated" and the field is absent from the record
CW_ALLOWED = {'mature-violence', 'sexual', 'photosensitivity', 'strong-language'}

def option_in(categories, option_key):
    """The category option dict for a key, or None."""
    return next((o for d in categories['dimensions'] for o in d['options']
                 if o['key'] == option_key), None)

def place_subcategory(categories, category, raw_sub):
    """Settle `category['sub']` for the option in `category['goal']`: required
    and checked when the option defines subcategories, refused when it does
    not. Returns an error string or None."""
    option = option_in(categories, category.get('goal'))
    subs = (option or {}).get('subcategories') or []
    sub = (raw_sub or '').strip()
    if subs:
        if sub not in {s['key'] for s in subs}:
            return (f'{option["label"]} has subcategories ({", ".join(s["label"] for s in subs)}); '
                    f'pick one')
        category['sub'] = sub
    elif sub:
        return f'{(option or {}).get("label", category.get("goal"))} has no subcategories'
    else:
        category.pop('sub', None)
    return None

# The general voiding rule: a change to the run's GOAL OR SCORING (its goal,
# time or any metric) invalidates the verifications, which attested those
# facts from the encode; a change to its REPRODUCTION INFORMATION (the movie file, the
# tool it plays in, the files it was made against) invalidates the
# reproductions, which synced the old setup.
# Nothing else voids anything.
SCORING_FIELDS = {'duration', 'goal'}              # plus every metric:<key>
REPRO_FIELDS = {'movie', 'emulator', 'files'}

def _void_roster(entries, stamp, reason):
    """Mark only currently live acts obsolete for the stated edit reason."""
    voided = False
    for entry in entries:
        if not entry.get('invalidated'):
            entry['invalidated'] = dict(stamp, reason=reason)
            voided = True
    return voided

def void_acts_for(run, changed, by):
    """What an edit does to the acts already on the run, by the general
    rule: a goal/scoring change (goal, time or any metric) invalidates the live
    verifications, which attested those values, and the run leaves the
    ranking until somebody verifies it again; a reproduction-information
    change (the movie file, the tool, the files it was made against)
    invalidates the live reproductions, which synced the old setup.
    Nothing else voids anything.
    Returns the kinds voided."""
    voided = []
    stamp = {'by': by, 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(), 'cause': 'edit'}
    if any(c in SCORING_FIELDS or c.startswith('metric:') for c in changed):
        reason = ('the goal changed after this verification' if 'goal' in changed
                  else 'the scoring changed after this verification')
        if _void_roster(run.get('verifications', []), stamp, reason):
            voided.append('verifications')
    if any(c in REPRO_FIELDS for c in changed):
        what = 'the movie file' if 'movie' in changed else 'the reproduction information'
        if _void_roster(run.get('reproductions', []), stamp,
                        f'{what} changed after this reproduction'):
            voided.append('reproductions')
    if voided:
        sync_status(run)
    return voided

def live_acts(run):
    """Count currently valid verifications and reproductions on a run."""
    return {'verifications': sum(1 for v in run.get('verifications', []) if not v.get('invalidated')),
            'reproductions': sum(1 for v in run.get('reproductions', []) if not v.get('invalidated'))}

def parse_stated_time(raw):
    """A time typed by a person, [h:]mm:ss[.mmm], as seconds; (value, error)."""
    stated = (raw or '').strip()
    time_match = re.fullmatch(r'(?:(\d{1,3}):)?(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?', stated)
    if not time_match:
        return None, 'state it as [h:]mm:ss or [h:]mm:ss.mmm'
    hours, minutes, seconds, fraction = time_match.groups()
    value = (int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)
             + (int(fraction.ljust(3, '0')) / 1000 if fraction else 0.0))
    if value <= 0:
        return None, 'a run that takes no time at all is not a run'
    return value, None

def parse_file_rows(form):
    """The files a movie was made against, from the repeated form fields
    file_name / file_sha1 (one row each, paired by position). A row with
    nothing in it is skipped; a name is required; a sha1, when given, is
    exactly 40 hex digits. Returns (files, error)."""
    names = form.getlist('file_name')
    shas = form.getlist('file_sha1')
    files = []
    for i in range(max(len(names), len(shas))):
        name = (names[i] if i < len(names) else '').strip()[:200]
        sha = (shas[i] if i < len(shas) else '').strip().lower()
        if not name and not sha:
            continue
        if not name:
            return None, f'file {i + 1}: a name is required (the sha1 alone identifies nothing)'
        if sha and not re.fullmatch(r'[0-9a-f]{40}', sha):
            return None, f'file {i + 1} ({name}): a sha1 is exactly 40 hexadecimal characters'
        entry = {'name': name}
        if sha:
            entry['sha1'] = sha
        files.append(entry)
    if len(files) > 50:
        return None, 'at most 50 files'
    return files, None

GAME_PROPERTY_FIELDS = ('released', 'unofficial', 'discord', 'website', 'rta', 'rules')

def _game_release_date(text):
    """Validate an optional year, month or full release date."""
    if not re.fullmatch(r'\d{4}(-\d{2}(-\d{2})?)?', text):
        return None, 'release date is YYYY, YYYY-MM or YYYY-MM-DD'
    parts = [int(x) for x in text.split('-')]
    if not (1950 <= parts[0] <= 2100):
        return None, 'release year out of range'
    if len(parts) > 1 and not (1 <= parts[1] <= 12):
        return None, 'release month out of range'
    if len(parts) > 2:
        try:
            datetime.date(*parts)
        except ValueError:
            return None, 'that release date does not exist'
    return text, None

def _game_link(field, text):
    """Validate an HTTP(S) community or RTA leaderboard address."""
    if len(text) > 300 or not re.fullmatch(r'https?://[^\s<>"\']+', text):
        return None, f'the {"RTA leaderboards" if field == "rta" else "community website"} link is an http(s) URL'
    return text, None

def parse_game_property(field, raw):
    """(value, error) for one game property from form text; None clears."""
    text = (raw or '').strip()
    if not text:
        return None, None
    if field == 'released':
        return _game_release_date(text)
    if field == 'unofficial':
        if text.lower() in ('1', 'true', 'yes', 'on'):
            return True, None
        if text.lower() in ('0', 'false', 'no', 'off'):
            return None, None
        return None, 'unofficial is yes or no'
    if field == 'discord':
        if not re.fullmatch(r'https://(discord\.gg|discord\.com/invite)/[A-Za-z0-9-]+', text):
            return None, 'a Discord invite looks like https://discord.gg/xxxx'
        return text, None
    if field in ('website', 'rta'):
        return _game_link(field, text)
    if field == 'rules':
        # game-wide rules (issue #64): markdown, shown above every
        # category's own rule in the View rules dialog
        if len(text) > 2000:
            return None, 'game rules fit in 2000 characters of markdown'
        return text, None
    return None, f'unknown property {field}'

EXPERT_EDITABLE = {'run': ('duration', 'goal', 'encode', 'goalDescription',
                           'notes', 'movie'),
                   'game': ('title', 'thumbnail') + GAME_PROPERTY_FIELDS,
                   'category': ('label', 'rule', 'metrics', 'selector', 'subSelector', 'key'),
                   'group': ('title',)}







# ---- systems: the machines a run can be on ----
# A system is the one piece of library structure with no page of its own to
# be corrected on: every game key starts with it, every ranking rate comes
# from it, and a run cannot move between systems. So creating one is a
# Committee or whole-site matter, and deleting one is the Committee's alone.
SYSTEM_KEY = re.compile(r'[a-z0-9]+(-[a-z0-9]+)*')
SYSTEM_KEY_MAX = 24
SYSTEM_FPS_MIN, SYSTEM_FPS_MAX = 1.0, 1000.0
SYSTEM_FPS_DEFAULT = 60.0     # what a submitter's system starts at, until an
                              # expert sets the rate the machine really runs at


def system_key_for(name):
    """The key a system name becomes: lowercase, spaces to hyphens, the rest
    dropped. "Bandai Terebikko" is terebikko's neighbour bandai-terebikko, and
    a submitter never has to think about it."""
    key = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    return key[:SYSTEM_KEY_MAX].rstrip('-')


def load_systems():
    """Read the current system metadata from the archive checkout."""
    return json.loads((ARCHIVE / 'systems.json').read_text())













REPORT_KINDS = {'missing-content-warnings', 'spam-malicious', 'miscredited',
                'licensing', 'other'}



def attach_movie(run, run_dir, upload):
    """Put a movie file on a run that has none, and stop it being video-only.

    A run is video-only because no movie file came with it, which is a
    statement about the submission and not a permanent property of the work:
    a file that failed to reach us (a form that would not send it, an author
    who had not exported it yet) can still arrive. Reproduction stops being
    not-applicable the moment one does.

    Returns (description, error): the error is a Flask response.
    """
    ext = upload.filename.rsplit('.', 1)[-1].lower()
    movie_bytes = upload.read()
    if not movie_bytes or len(movie_bytes) > MOVIE_MAX:
        return None, fail(MOVIE_MUST_BE_NON_EMPTY_AND_UNDER_MAX)
    parsed = movieparse.parse(upload.filename, movie_bytes)
    if not parsed['ok']:                 # any extension is archived as it is
        parsed = {'frames': 0, 'rerecords': None, 'start': 'power-on', 'fps': None}
    (run_dir / f"{run['id']}.{ext}").write_bytes(movie_bytes)
    run['movie'] = {'file': f"{run['id']}.{ext}", 'format': ext,
                    'sha1': hashlib.sha1(movie_bytes).hexdigest(),
                    'frames': parsed['frames'], 'rerecords': parsed['rerecords'],
                    'start': parsed['start'],
                    **({'fps': parsed['fps']} if parsed.get('fps') else {})}
    run.pop('videoOnly', None)
    if run.get('status', {}).get('reproduced') == 'not-applicable':
        run['status']['reproduced'] = 'none'
    return (f"{run['id']}.{ext} (sha1 {run['movie']['sha1'][:12]}, "
            f"{parsed['frames'] or 'unknown'} frames)"), None



def split_notes_header(text):
    """(header, body): the leading block of `>` lines an import carries as
    its disclaimer, and the author's notes after it. Only the leading block
    is the archive's; a quote anywhere else is the author's own."""
    lines = text.splitlines()
    n = 0
    while n < len(lines) and lines[n].startswith('>'):
        n += 1
    header = '\n'.join(lines[:n]) + '\n' if n else ''
    body = '\n'.join(lines[n:]).strip()
    return header, (body + '\n' if body else '')



def _helper_gate():
    """The submit helpers (inspect, preview, encode check) parse files and
    fetch third-party pages: real work. The form that uses them only shows
    to a logged-in member, so anonymous calls are a script, not a person."""
    if session_user() or request.form.get('key') == SUBMIT_KEY or request.args.get('key') == SUBMIT_KEY:
        return None
    return fail('log in via the forum to use this', 403)


ANNUL_WORDS = ('annul', 'remove', 'revoke', 'yes')

_sync_lock = threading.Lock()
_sync_last = [0.0]

def spawn_site_sync(ref=None):
    """Run the code deploy detached from this process.

    The script ends in `systemctl restart archivist`, and a child living in
    our own cgroup would be killed along with us, so as root the work goes
    into a transient unit of its own. `ref` is the exact commit the tests
    passed on, so main racing ahead to a red commit cannot ride along.
    Returns how it was started."""
    with _sync_lock:
        now = time.monotonic()
        if now - _sync_last[0] < 20:
            return 'already syncing'
        _sync_last[0] = now
    argv = [SITE_SYNC_CMD] + ([ref] if ref else [])
    if os.name == 'nt' and SITE_SYNC_CMD.lower().endswith(('.cmd', '.bat')):
        argv = ['cmd.exe', '/c'] + argv
    try:
        if getattr(os, 'geteuid', lambda: -1)() == 0:
            subprocess.Popen(['systemd-run', '--collect', '--quiet',
                              '--unit=tar-site-sync-' + secrets.token_hex(4)] + argv,
                             start_new_session=True)
            return 'systemd-run'
        subprocess.Popen(argv, start_new_session=True)
        return 'detached'
    except (OSError, subprocess.SubprocessError) as exc:      # noqa: BLE001
        LOG.warning('site sync could not start: %s', exc)
        return 'failed'

DEPLOY_WORKFLOW = 'Build and deploy'   # the workflow whose green light deploys

def _workflow_deploy(payload, deploy):
    """Deploy only successful push-triggered main workflow runs."""
    run = payload.get('workflow_run') or {}
    if payload.get('action') != 'completed':
        return jsonify({'ok': True, 'ignored': 'run not finished'})
    if run.get('name') != DEPLOY_WORKFLOW:
        return jsonify({'ok': True, 'ignored': f'not the {DEPLOY_WORKFLOW} workflow'})
    if run.get('head_branch') != 'main':
        return jsonify({'ok': True, 'ignored': f'not main ({run.get("head_branch")})'})
    if run.get('conclusion') != 'success':
        LOG.info('github hook: %s on main concluded %s; nothing deployed',
                 DEPLOY_WORKFLOW, run.get('conclusion'))
        return jsonify({'ok': True, 'ignored': f'run {run.get("conclusion")}'})
    if run.get('event') != 'push':
        # schedule and archive-content dispatches skip the suite entirely
        return jsonify({'ok': True, 'ignored': f'run triggered by {run.get("event")}'})
    return deploy(run.get('head_sha'), 'suite passed')

@app.post('/api/hooks/github')
def github_hook():
    """Deploy only the tested main commit from a successful push workflow.

    GitHub authenticates the raw JSON and event header via X-Hub-Signature-256;
    a signed deploy-now dispatch body is the operator's independent override.
    Skipped, red, scheduled, archive-content and other-branch runs do not deploy.
    Answers {ok, syncing, how} (202) or {ok, ignored: why}.
    """
    if not GITHUB_HOOK_SECRET:
        return fail('github hooks are not configured on this server', 503)
    raw = request.get_data()
    expected_sig = 'sha256=' + hmac.new(GITHUB_HOOK_SECRET.encode(), raw,
                                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(request.headers.get('X-Hub-Signature-256', ''),
                               expected_sig):
        return fail('bad hook signature', 403)
    event = request.headers.get('X-GitHub-Event', '')
    if event == 'ping':
        return jsonify({'ok': True, 'pong': True})
    try:
        payload = json.loads(raw) if raw else {}
    except ValueError:
        return fail('unreadable payload')

    def deploy(sha, why):
        """Start a site sync for a validated GitHub push SHA."""
        if not re.fullmatch(r'[0-9a-f]{40}', sha or ''):
            return jsonify({'ok': True, 'ignored': 'no commit to deploy'})
        how = spawn_site_sync(sha)
        LOG.info('github hook: deploying %s (%s), site sync %s', sha[:12], why, how)
        return jsonify({'ok': True, 'syncing': sha[:12], 'why': why, 'how': how}), 202

    if event == 'push':
        # the tests have not spoken yet; CI's green light is what deploys
        if payload.get('ref') == 'refs/heads/main':
            LOG.info('github hook: main at %s, waiting for the suite',
                     (payload.get('after') or '')[:12])
            return jsonify({'ok': True, 'ignored': 'waiting for the test suite'})
        return jsonify({'ok': True, 'ignored': f'not main ({payload.get("ref")})'})

    if event == 'workflow_run':
        return _workflow_deploy(payload, deploy)

    if event == 'repository_dispatch' and payload.get('action') == 'deploy-now':
        # the way out when Actions cannot report at all: a named commit, or
        # whatever main holds right now
        sha = ((payload.get('client_payload') or {}).get('sha') or '').lower()
        if re.fullmatch(r'[0-9a-f]{40}', sha):
            return deploy(sha, 'operator override')
        how = spawn_site_sync()
        LOG.info('github hook: operator override on main, site sync %s', how)
        return jsonify({'ok': True, 'syncing': 'main',
                        'why': 'operator override', 'how': how}), 202

    return jsonify({'ok': True, 'ignored': f'not an event we act on ({event})'})

@app.post('/api/hooks/discourse')
def discourse_hook():
    """Discourse tells us a post happened; we relay it to Discord.

    Signature first: the body is only trusted if Discourse's HMAC matches our
    shared secret. Private messages are never relayed, whatever they are, and
    neither are the archivist bot's own posts, which announce things the other
    notifications already said.

    Who: Discourse, proven by the HMAC in X-Discourse-Event-Signature
    Reads: the raw JSON body (post) and the X-Discourse-Event header
    Answers: {ok} or {ok, ignored: why}
    """
    if not DISCOURSE_HOOK_SECRET:
        return fail('forum hooks are not configured on this server', 503)
    raw = request.get_data()
    sig = request.headers.get('X-Discourse-Event-Signature', '')
    expected_sig = 'sha256=' + hmac.new(DISCOURSE_HOOK_SECRET.encode(), raw,
                                hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return fail('bad hook signature', 403)
    if request.headers.get('X-Discourse-Event') != 'post_created':
        return jsonify({'ok': True, 'ignored': 'not a post_created event'})
    try:
        post = json.loads(raw).get('post') or {}
    except ValueError:
        return fail('unreadable payload')
    if post.get('topic_archetype') != 'regular':
        # a private message is private; it does not go to Discord, ever
        return jsonify({'ok': True, 'ignored': 'not a public topic'})
    poster = post.get('username') or 'somebody'
    if poster == BOT_USER:
        return jsonify({'ok': True, 'ignored': 'our own bot'})
    title = post.get('topic_title') or 'a topic'
    excerpt = re.sub(r'<[^>]+>', '', post.get('cooked') or '').strip()
    excerpt = (excerpt[:140] + '\u2026') if len(excerpt) > 140 else excerpt
    link = (f'{DISCOURSE_URL}/t/{post.get("topic_id")}/{post.get("post_number")}'
            if post.get('topic_id') else DISCOURSE_URL)
    notify_discord(f'\U0001f4ac **{member_md(poster)}** posted in [{title}](<{link}>): {excerpt}')
    return jsonify({'ok': True})



GRANT_WORDS = ('grant', 'appoint', 'yes', 'approve', 'in favour', 'in favor', 'for')








_dumps_pulled = {'t': 0.0}

def _refresh_dumps():
    """Pull the tasvideos-backup checkout so freshly published movies become
    importable; best-effort, at most once per 10 minutes."""
    if time.time() - _dumps_pulled['t'] < 600:
        return
    _dumps_pulled['t'] = time.time()
    if not (DUMPS_DIR / '.git').exists():
        return
    try:
        subprocess.run(['git', '-C', str(DUMPS_DIR), 'pull', '--ff-only', '-q'],
                       timeout=120, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def _import_identity():
    """Self-service import is session-only: the logged-in member must BE the
    claimed author. Returns (canonical_username, error)."""
    session_name = session_user()
    if not session_name:
        return None, fail('log in via the forum to import your movies', 403)
    if not origin_ok():
        return None, fail('cross-origin request refused', 403)
    if not re.fullmatch(r'[A-Za-z0-9._-]{3,30}', session_name):
        return None, fail('session username is not archive-safe', 400)
    author_file = ARCHIVE / 'authors' / f'{selfimport.slugify(session_name)}.json'
    if not author_file.exists():
        return None, fail('imports are available once you have claimed the identity', 403)
    author_record = json.loads(author_file.read_text())
    if not author_record.get('claimed') or author_record.get('username', '').lower() != session_name.lower():
        return None, fail('imports are available once you have claimed the identity', 403)
    return author_record['username'], None





DISCUSSION_CACHE = {}      # topic id -> (fetched_at, payload)

ENCODE_CACHE = {}       # url -> (when, payload); the submit page asks on every keystroke




def reconcile_loop():
    """Keep the forum groups matching the record, without anybody asking.

    A grant publishes itself, so this is only here for drift from the other
    side: somebody editing a group in the Discourse admin, or a group that was
    changed while this service was down. It is deliberately not a person's
    action and carries no identity, because it is not a decision: publishing
    cannot write to the archive, so the worst an automatic run can do is
    correct the projection. Anything it changed is worth saying out loud,
    since a group that keeps drifting means somebody is trying to grant a role
    the wrong way.
    """
    while True:
        time.sleep(RECONCILE_SECONDS)
        if not DISCOURSE_KEY:
            continue
        try:
            refresh_archive(0)
            for role, entry in publish_roles().items():
                moved = entry.get('add', []) + entry.get('remove', [])
                if moved or entry.get('error'):
                    LOG.warning('reconcile %s: %s', role, entry)
        except Exception as exc:                                 # noqa: BLE001
            LOG.warning('reconcile failed: %s', exc)


# Register feature routes after the request helpers and service hooks exist.
from routes import runs as runs_routes
runs_routes.register(app,
    CW_ALLOWED=CW_ALLOWED,
    EXPERT_EDITABLE=EXPERT_EDITABLE,
    GAME_PROPERTY_FIELDS=GAME_PROPERTY_FIELDS,
    MOVIE_MUST_BE_NON_EMPTY_AND_UNDER_MAX=MOVIE_MUST_BE_NON_EMPTY_AND_UNDER_MAX,
    MOVIE_TOO_LARGE=MOVIE_TOO_LARGE,
    REPRO_FIELDS=REPRO_FIELDS,
    SCORING_FIELDS=SCORING_FIELDS,
    act_common=act_common,
    attach_movie=attach_movie,
    auth_precheck=auth_precheck,
    in_category=in_category,
    live_acts=live_acts,
    notify_edit=notify_edit,
    option_in=option_in,
    pace_gate=pace_gate,
    parse_file_rows=parse_file_rows,
    parse_game_property=parse_game_property,
    parse_metric_defs=parse_metric_defs,
    parse_stated_time=parse_stated_time,
    place_subcategory=place_subcategory,
    read_attachments=read_attachments,
    request_identity=request_identity,
    split_notes_header=split_notes_header,
    void_acts_for=void_acts_for,
)
from routes import library as library_routes
library_routes.register(app,
    GAME_PROPERTY_FIELDS=GAME_PROPERTY_FIELDS,
    SYSTEM_FPS_DEFAULT=SYSTEM_FPS_DEFAULT,
    SYSTEM_FPS_MAX=SYSTEM_FPS_MAX,
    SYSTEM_FPS_MIN=SYSTEM_FPS_MIN,
    SYSTEM_KEY=SYSTEM_KEY,
    SYSTEM_KEY_MAX=SYSTEM_KEY_MAX,
    _category_gate=_category_gate,
    _deletion_gate=_deletion_gate,
    auth_precheck=auth_precheck,
    load_systems=load_systems,
    notify_edit=notify_edit,
    option_in=option_in,
    pace_gate=pace_gate,
    parse_game_property=parse_game_property,
    parse_metric_defs=parse_metric_defs,
    request_identity=request_identity,
    system_key_for=system_key_for,
)
from routes import community as community_routes
community_routes.register(app,
    ANNUL_WORDS=ANNUL_WORDS,
    GRANT_WORDS=GRANT_WORDS,
    REPORT_KINDS=REPORT_KINDS,
    _deletion_gate=_deletion_gate,
    _import_identity=_import_identity,
    _refresh_dumps=_refresh_dumps,
    act_common=act_common,
    auth_precheck=auth_precheck,
    pace_gate=pace_gate,
    request_identity=request_identity,
)
from routes import reading as reading_routes
reading_routes.register(app,
    DISCUSSION_CACHE=DISCUSSION_CACHE,
    ENCODE_CACHE=ENCODE_CACHE,
    MOVIE_TOO_LARGE=MOVIE_TOO_LARGE,
    _helper_gate=_helper_gate,
    _name_seen=_name_seen,
    _search_index=_search_index,
)

if __name__ == '__main__':
    if RECONCILE_SECONDS > 0:
        threading.Thread(target=reconcile_loop, daemon=True).start()
    replay_spool()   # notifications a restart interrupted mid-wait
    import sitebuild
    sitebuild.start()   # publish the site from here, fresh on every commit
    cert = os.environ.get('TLS_CERT')
    key = os.environ.get('TLS_KEY')
    ctx = (cert, key) if cert and key else None
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '8100')),
            ssl_context=ctx, threaded=True)
