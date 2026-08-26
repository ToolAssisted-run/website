"""toolAssisted.run archivist — the intake and moderation service.

The Flask app and every route live here (the Controller); the layers it
drives are their own modules (see each one's docstring):

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
    ATTACH_EXTS,
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
    MOVIE_EXTS,
    MOVIE_MAX,
    VISITS_FILE,
    NOTES_MAX,
    RECONCILE_SECONDS,
    SELF_URL,
    SESSION_TTL,
    SHOT_MAX_EACH,
    SHOT_MAX_TOTAL,
    SITE_ORIGIN,
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
    replay_spool,
)
from records import (
    already_covers,
    append_role_event,
    case_derived_status,
    covers_group,
    ensure_member,
    expert_covers,
    held_roles,
    is_committee,
    is_editor,
    is_founder,
    is_site_expert,
    is_uncl_run,
    load_claims,
    load_experts,
    load_groups,
    log_deletion,
    log_edit,
    may_decide_claims,
    next_report_id,
    note_new_member,
    save_claims,
    save_groups,
    scope_covers,
    scope_exists,
    scopes_over,
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
    read_committee_poll,
    send_pm,
    sync_expert_group,
    topics_for_imported,
    unlock_forum_username,
)

"""The archivist — toolAssisted.run's intake service.

Receives a submission (movie + metadata + optional text attachments), validates it
mechanically (no emulation), writes a per-run folder into the archive checkout,
commits and pushes. The archive repo's CI then validates independently and rebuilds
the site. The archivist never judges a run — it only records it, as pending.

v0: shared-key auth (Discourse SSO later), submissions to existing games/categories
only. Native run IDs start at M100001, above the tasvideos import range.

Community acts: POST /api/reproduce (mandatory ending screenshot) and
POST /api/verify (requires an encode on the run) append roster entries to
run.json and keep the status field exactly in sync with what the archive's
CI derives from the rosters. Self-acts, duplicate acts, and acts on Imported
runs are rejected. The archivist never judges — it records.
"""

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

app.config['MAX_CONTENT_LENGTH'] = 96 * 1024 * 1024

sso_nonces = {}   # nonce -> expiry

@app.after_request
def cors(resp):
    """Let the site's origin call this service with credentials: CORS
    headers on /api/*, /login and /logout.

    Who: every response (after_request hook), no auth of its own
    Reads: the request path
    Answers: the same response, with Access-Control-* and Vary headers added
    """
    if request.path.startswith('/api/') or request.path in ('/login', '/logout'):
        resp.headers['Access-Control-Allow-Origin'] = SITE_ORIGIN
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
    'act': (30, 3600),         # reproductions, verifications, console
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

def read_attachments(existing=None):
    """Validate the request's uploaded 'attachments': text configs (UTF-8,
    size-capped) or additional movie files. `existing` counts a run's
    current attachments against the caps. Returns ([(name, bytes)], error)."""
    existing = existing or []
    attachments = []
    total = 0
    movie_atts = sum(1 for a in existing
                     if pathlib.Path(a['file']).suffix.lower().lstrip('.') in MOVIE_EXTS)
    for upload in request.files.getlist('attachments'):
        if not upload.filename:
            continue
        name = re.sub(r'[^A-Za-z0-9._-]', '_', pathlib.Path(upload.filename).name)
        suffix = pathlib.Path(name).suffix.lower()
        data = upload.read()
        if suffix.lstrip('.') in MOVIE_EXTS:
            if len(data) > MOVIE_MAX:
                return None, fail(f'movie attachment {name!r} exceeds 16 MB')
            movie_atts += 1
        elif suffix in ATTACH_EXTS:
            total += len(data)
            if len(data) > ATTACH_MAX_EACH:
                return None, fail(f'attachment {name!r} exceeds 128 KB')
            try:
                data.decode('utf-8')
            except UnicodeDecodeError:
                return None, fail(f'attachment {name!r} is not valid UTF-8 text')
        else:
            return None, fail(f'attachment {name!r}: only text/config files or '
                              f'movie formats are allowed')
        attachments.append((name, data))
    if len(attachments) + len(existing) > ATTACH_MAX_COUNT:
        return None, fail('too many attachments (max 8)')
    if movie_atts > 4:
        return None, fail('too many movie attachments (max 4)')
    if total > ATTACH_MAX_TOTAL:
        return None, fail('text attachments exceed 512 KB total')
    return attachments, None

@app.post('/api/submit')
def submit():
    """Archive a new run, pending, as a per-run folder plus one commit.

    Who: a logged-in member, or the shared `key` plus `submitter`
    Reads: form fields consent, game (system/slug), goal, goal_description,
        metric_<key>, authors (comma-separated), video_only, time (whenever
        the category ranks by time; the record is what the author states),
        sub (the subcategory key, required iff the category defines
        subcategories), encode, emulator (the tool and its version),
        file_name / file_sha1 (repeatable rows; the old
        rom_name / rom_sha1 pair still counts as one), completed, notes,
        content_warnings (repeatable), dry_run; files movie, attachments
    Answers: {ok, id, archive, forum}; dry_run: {ok, dry_run, would_be, run,
        game_key}; 409 when the movie or encode is already archived
    """
    submission = request.form
    submitter, error = request_identity(submission, 'submitter')
    if error:
        return error
    paced = pace_gate(submission, submitter, 'submit')
    if paced:
        return paced
    if submission.get('consent') != 'yes':
        return fail('submission requires consent: licensing under CC BY 4.0, agreeing '
                    'with the Community Principles, Terms of Use, Code of Conduct and '
                    'Privacy Policy, and confirming the information, especially '
                    'authorship, is complete and truthful')

    # --- game and category exist beforehand (creation has its own flow) ---
    game_selection = (submission.get('game') or '').strip()
    game_match = re.fullmatch(r'([a-z0-9-]+)/([a-z0-9-]+)', game_selection)
    if not game_match:
        return fail('game must be system/slug; create the game first at '
                    '/create-game/ if it is not archived yet')
    system, slug = game_match.groups()
    game, categories = load_game(system, slug)
    if not game:
        return fail(f'unknown game {system}/{slug}; create it first at /create-game/')

    goal = (submission.get('goal') or '').strip()
    goal_description = ''
    dim_keys = {}
    if goal == 'unclassified':
        # special category on every game: no defined goal, the run describes
        # its own; never verifiable, ranked by likes alone
        goal_description = (submission.get('goal_description') or '').strip()[:200]
        if not goal_description:
            return fail('Unclassified runs must describe their goal '
                        '(goal_description); it is shown in the ranking')
        dim_keys = {'goal': 'unclassified'}
    for dimension in categories['dimensions']:
        if goal in {option['key'] for option in dimension['options']}:
            dim_keys[dimension['key']] = goal
    if not dim_keys:
        return fail(f'unknown category {goal!r} for {system}/{slug}; create it '
                    f'first from the game page')
    # a category with subcategories (Episode 1: any%, 100%) wants one named
    sub_error = place_subcategory(categories, dim_keys, submission.get('sub'))
    if sub_error:
        return fail(sub_error)

    # --- the category's metrics decide what the submitter must state ---
    goal_opt = next((option for dimension in categories['dimensions'] for option in dimension['options']
                     if option['key'] == goal), None)
    metric_defs = (goal_opt or {}).get('metrics')
    wants_time = metric_defs is None or any(metric_def['key'] == 'time'
                                            for metric_def in metric_defs)
    stated_metrics = {}
    for metric_def in (metric_defs or []):
        if metric_def['key'] == 'time':
            continue                    # the run's time is stated via `time`
        raw_value = (submission.get(f'metric_{metric_def["key"]}') or '').strip()
        if raw_value == '':
            return fail(f'this category ranks by {metric_def["label"]}: state its '
                        f'value (metric_{metric_def["key"]})')
        try:
            value = float(raw_value)
        except ValueError:
            return fail(f'{metric_def["label"]} must be a number (seconds for times)')
        if value < 0:
            return fail(f'{metric_def["label"]} cannot be negative')
        stated_metrics[metric_def['key']] = value

    authors = [a.strip() for a in (submission.get('authors') or '').split(',') if a.strip()]
    if not authors:
        return fail('at least one author required')

    # --- the movie, or the statement that there is none ---
    # A video-only run has no input movie: the encode IS the run. It can never
    # be reproduced, in emulator or on console, and it says so; verification
    # still gates its ranking like any other run's. The submitter states the
    # time, since there are no frames to derive it from.
    # a run without a movie file IS video-only: the encode is the run. The
    # explicit flag survives for API callers; sending both a flag and a file
    # is a contradiction to refuse, not to guess about
    movie_upload = request.files.get('movie')
    video_only_flag = (submission.get('video_only') or '').strip() in ('1', 'true', 'yes', 'on')
    if video_only_flag and movie_upload and movie_upload.filename:
        return fail('you attached a movie file and called the run video-only; '
                    'pick one')
    video_only = video_only_flag or not (movie_upload and movie_upload.filename)
    if video_only:
        duration = None
        if wants_time and goal != 'unclassified':
            # the category ranks by time and there are no frames to derive it
            # from, so the submitter states it
            duration, time_error = parse_stated_time(submission.get('time'))
            if time_error:
                return fail(f'a video-only run in a time-ranked category needs its time: {time_error}')
        ext = None
        movie_bytes = b''
        movie_sha1 = None
        parsed = {'frames': None, 'rerecords': None, 'start': None, 'fps': None}
    else:
        duration = None
        ext = movie_upload.filename.rsplit('.', 1)[-1].lower()
        movie_bytes = movie_upload.read()
        if len(movie_bytes) > MOVIE_MAX:
            return fail('movie exceeds 16 MB')
        if not movie_bytes:
            return fail('movie file is empty')
        # any extension is archived as it is: an author may work in a tool
        # the archive has no parser for. A parse failure is a warning, never
        # a refusal; the record's time is the one the author states either
        # way (the form's own Import from movie fills it when it can).
        parsed = movieparse.parse(movie_upload.filename, movie_bytes)
        if not parsed['ok']:
            parsed = {'ok': False, 'frames': 0, 'rerecords': None, 'start': 'power-on', 'fps': None}
        if wants_time and goal != 'unclassified':
            duration, time_error = parse_stated_time(submission.get('time'))
            if time_error:
                return fail(f'this category ranks by time, so the run states it '
                            f'(Import from movie fills it when the movie can be read): {time_error}')
        movie_sha1 = hashlib.sha1(movie_bytes).hexdigest()

    # --- encode (mandatory) + thumbnail derived from it ---
    # The encode is validated here and the run's thumbnail is a frame of it
    # (maxres, falling back to hq) — no author upload, nothing to moderate.
    encode = (submission.get('encode') or '').strip()
    encode_provider = providers.resolve(encode)
    if not encode_provider:
        return fail('an encode link is required, from one of: '
                    + ', '.join(providers.names())
                    + ' (the run thumbnail is derived from it)')
    thumb_bytes, thumb_ext = providers.thumbnail(encode_provider['kind'], encode_provider['id'], THUMB_MAX)
    if not thumb_bytes:
        return fail(f'the encode link does not resolve to a watchable '
                    f'{encode_provider["name"]} video; check the URL (the run thumbnail is '
                    f'derived from it)')

    # --- attachments: text configs, or additional movie files ---
    attachments, attachment_error = read_attachments()
    if attachment_error:
        return attachment_error

    completed = (submission.get('completed') or '').strip()
    if completed:
        if not re.fullmatch(r'(19[89]\d|20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])',
                            completed):
            return fail('completed must be a date like 2021-10-26')
        if completed > time.strftime('%Y-%m-%d', time.gmtime()):
            return fail('completed cannot be in the future')

    notes = (submission.get('notes') or '').strip()
    if len(notes.encode()) > NOTES_MAX:
        return fail('notes exceed 256 KB')

    # voluntary content disclosures — separate flags, shown on the run page
    content_warnings = [w for w in request.form.getlist('content_warnings')]
    if any(w not in CW_ALLOWED for w in content_warnings):
        return fail('unknown content warning flag')

    # the files the movie was made against (0 to n): ROMs, disc images,
    # executables, sources. Name and SHA1 only, hashed in the browser; the
    # old single rom_name/rom_sha1 pair is accepted as one row still
    files, files_error = parse_file_rows(submission)
    if files_error:
        return fail(files_error)
    if not files and (submission.get('rom_name') or submission.get('rom_sha1')):
        legacy = {'file_name': [submission.get('rom_name') or ''],
                  'file_sha1': [submission.get('rom_sha1') or '']}
        files, files_error = parse_file_rows(type('F', (), {'getlist': lambda self, k: legacy.get(k, [])})())
        if files_error:
            return fail(files_error)

    dry_run = submission.get('dry_run') in ('1', 'true', 'yes')

    with lock:
        checkout_branch()
        # refuse a movie the archive already holds: the same bytes (a double
        # click, or a run already imported from TASVideos) or the same work
        # saved again. Checked under the lock, against the fresh checkout.
        if video_only:
            # no bytes to compare, so the encode is the fingerprint: the same
            # video twice is the same run twice
            for other_run_json in ARCHIVE.glob('games/*/*/runs/*/run.json'):
                other_run = json.loads(other_run_json.read_text())
                if other_run.get('withdrawn'):
                    continue   # a withdrawn run never blocks a resubmission
                if any(e.get('url') == encode for e in other_run.get('encodes', [])):
                    return fail(f'this video is already archived as '
                                f'{other_run["id"]}: the encode is the run, and it is '
                                f'the same encode', 409)
        else:
            dup_id, why = duplicate_of(movie_sha1, f'{system}/{slug}',
                                       dim_keys.get('goal'), parsed['frames'], authors)
            if dup_id:
                return fail(f'this run is already archived as {dup_id}: it has {why}. '
                            f'If it is an improvement, submit the faster movie; if the '
                            f'archived one is wrong, its authors can edit it.', 409)
        run_number = next_id()
        run_id = f'M{run_number}'
        run_dir = ARCHIVE / 'games' / system / slug / 'runs' / run_id
        run = {
            'id': run_id, 'game': f'{system}/{slug}', 'category': dim_keys,
            'authors': [{'user': a} for a in authors],
            'tools': [],
            **({'metrics': stated_metrics} if stated_metrics else {}),
            **({'videoOnly': True,
                **({'duration': duration} if duration else {})} if video_only else
               {**({'duration': duration} if duration else {}),
                'movie': {'file': f'{run_id}.{ext}', 'format': ext, 'sha1': movie_sha1,
                          'frames': parsed['frames'],
                          'rerecords': parsed['rerecords'],
                          'start': parsed['start'],
                          **({'fps': parsed['fps']} if parsed.get('fps') else {})}}),
            'thumbnail': 'thumb' + thumb_ext,
            **({'goalDescription': goal_description} if goal_description else {}),
            **({'contentWarnings': content_warnings} if content_warnings else {}),
            'contract': {'emulator': (submission.get('emulator') or '').strip(), **({'files': files} if files else {})},
            'status': ({'reproduced': 'not-applicable', 'verified': 'none',
                        'console': 'not-applicable'} if video_only else
                       {'reproduced': 'none', 'verified': 'none', 'console': 'none'}),
            'encodes': [{'kind': encode_provider['kind'], 'url': encode}],
            'attachments': [{'file': f'attachments/{name}', 'role': 'submitted attachment'} for name, _ in attachments],
            **({'completed': completed} if completed else {}),
            'submitted': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'submittedBy': submitter,
        }
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_be': run_id, 'run': run,
                            'game_key': f'{system}/{slug}'})
        ensure_member(submitter)
        run_dir.mkdir(parents=True)
        if not video_only:
            (run_dir / f'{run_id}.{ext}').write_bytes(movie_bytes)
        (run_dir / ('thumb' + thumb_ext)).write_bytes(thumb_bytes)
        (run_dir / 'run.json').write_text(json.dumps(run, indent=1))
        if notes:
            (run_dir / 'notes.md').write_text(notes + '\n')
        if attachments:
            (run_dir / 'attachments').mkdir()
            for name, data in attachments:
                (run_dir / 'attachments' / name).write_bytes(data)

        title = f"New run archived: {game['title']} ({goal}) by {', '.join(authors)}"
        # topics BEFORE the push: written after it, the pointers sat only in
        # the working tree and the next request's hard reset erased them,
        # leaving orphan topics and run pages with no visible discussion
        ensure_game_topic(system, slug, game['title'])
        if ensure_topic(run, game['title'], system, slug, goal, authors):
            (run_dir / 'run.json').write_text(json.dumps(run, indent=1))
        commit_push(f'Archive {run_id}: {game["title"]} ({goal}) by {", ".join(authors)}\n\n'
                    f'Submitted-By: {submitter}\nVia: archivist')
        notify_discord(f'\U0001f3ac New run archived: '
                       + movie_md(run, game['title']) + f' ({goal})',
                       wait_for=f'{SITE_URL}/runs/{run_id}/',
                       image=f'{SITE_URL}/thumbs/{run_id}{thumb_ext}')

    return jsonify({'ok': True, 'id': run_id,
                    'archive': f'https://github.com/ToolAssisted-run/archive/tree/{BRANCH}/games/{system}/{slug}/runs/{run_id}',
                    'forum': (run.get('forum') or {}).get('url')})

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
        if len(_visit_seen) > 100_000:   # bounded memory under a wide flood
            cutoff = now - 3600
            for k in [k for k, t in _visit_seen.items() if t < cutoff]:
                del _visit_seen[k]
        _visits[run_id] = _visits.get(run_id, 0) + 1
        count = _visits[run_id]
        try:
            VISITS_FILE.write_text(json.dumps(_visits))
        except OSError as exc:
            LOG.warning('visits file not writable: %s', exc)
    return jsonify({'ok': True, 'run': run_id, 'visits': count})

@app.post('/api/reproduce')
def reproduce():
    """Record a community reproduction: mandatory ending screenshot as proof.

    Who: a member (session, or `key` plus `user`) who is not one of the
        run's authors; refused on Imported, withdrawn and video-only runs
    Reads: form fields run, emulator, notes, dry_run; file screenshot (png/jpg/webp)
    Answers: {ok, run, status, reproductions}; dry_run: {ok, dry_run,
        would_record, status}
    """
    reproduction_form = request.form
    dry_run = reproduction_form.get('dry_run') in ('1', 'true', 'yes')
    with lock:
        auth_error = auth_precheck(reproduction_form)
        if auth_error:
            return auth_error
        if not dry_run:
            checkout_branch()
        act_error, run_dir, run, user = act_common(reproduction_form)
        if act_error:
            return act_error
        if run.get('videoOnly'):
            return fail('this run is video-only: there is no input movie to '
                        'replay, so reproduction does not apply')
        if user.lower() in {a['user'].lower() for a in run.get('reproductions', [])}:
            return fail('you have already reproduced this run; one reproduction per member')

        screenshot_upload = request.files.get('screenshot')
        if not screenshot_upload or not screenshot_upload.filename:
            return fail('an ending screenshot is required as proof of sync')
        ext = pathlib.Path(screenshot_upload.filename).suffix.lower()
        if ext not in IMAGE_MAGIC:
            return fail('screenshot must be png, jpg or webp')
        screenshot_bytes = screenshot_upload.read()
        if len(screenshot_bytes) > SHOT_MAX_EACH:
            return fail('screenshot exceeds 512 KB')
        if not any(screenshot_bytes.startswith(magic) for magic in IMAGE_MAGIC[ext]):
            return fail(f'screenshot is not a real {ext} image')
        stored_bytes = sum(sp.stat().st_size for sp in (run_dir / 'reproductions').glob('*')
                       if sp.is_file()) if (run_dir / 'reproductions').exists() else 0
        if stored_bytes + len(screenshot_bytes) > SHOT_MAX_TOTAL:
            return fail('this run has reached its screenshot storage cap')

        ordinal = len(run.get('reproductions', [])) + 1
        shot_rel = f'reproductions/{ordinal}-{user}{ext}'
        entry = {'user': user, 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(),
                 'screenshot': shot_rel}
        if (reproduction_form.get('emulator') or '').strip():
            entry['emulator'] = reproduction_form.get('emulator').strip()[:120]
        if (reproduction_form.get('notes') or '').strip():
            entry['notes'] = reproduction_form.get('notes').strip()
        run.setdefault('reproductions', []).append(entry)
        sync_status(run)
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_record': entry,
                            'status': run['status']})

        (run_dir / 'reproductions').mkdir(exist_ok=True)
        (run_dir / shot_rel).write_bytes(screenshot_bytes)
        (run_dir / 'run.json').write_text(json.dumps(
            {k: v for k, v in run.items() if not k.startswith('_')}, indent=1))
        ensure_member(user)
        commit_push(f'Reproduce {run["id"]}: by {user}\n\nVia: archivist')
        notify_discord(f'\u21bb **{member_md(user)}** reproduced ' + movie_md(run),
                       wait_for=f'{SITE_URL}/runs/{run["id"]}/')
    return jsonify({'ok': True, 'run': run['id'], 'status': run['status'],
                    'reproductions': len([a for a in run['reproductions'] if not a.get('invalidated')])})

@app.post('/api/invalidate')
def invalidate():
    """An expert invalidates a faulty reproduction/verification — a logged,
    appealable moderation act, never automatic. The run recomputes and the act
    can be redone by anyone else.

    Who: an expert covering the run's game (`key` plus `expert`, or session)
    Reads: form fields run, kind (reproduction|verification|console), target
        (the username whose act it is), reason, dry_run
    Answers: {ok, run, status, note}; dry_run: {ok, dry_run, would_invalidate,
        status}
    """
    invalidation_form = request.form
    dry_run = invalidation_form.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        auth_error = auth_precheck(invalidation_form)
        if auth_error:
            return auth_error
        if not dry_run:
            checkout_branch()
        expert, error = request_identity(invalidation_form, 'expert')
        if error:
            return error
        run_id = (invalidation_form.get('run') or '').strip()
        run_dir = find_run(run_id) if re.fullmatch(r'M[0-9]+', run_id) else None
        if not run_dir:
            return fail(f'unknown run {run_id}', 404)
        run = json.loads((run_dir / 'run.json').read_text())
        if run.get('status', {}).get('reproduced') == 'imported':
            return fail('Imported runs are irrevocably verified; no further acts apply')
        game_key = run['game']
        if not expert_covers(expert, game_key):
            return fail(f'{expert!r} is not an expert covering {game_key}', 403)
        kind = (invalidation_form.get('kind') or '').strip()
        # console verification lives in its own roster, hence the mapping
        ROSTER = {'reproduction': 'reproductions', 'verification': 'verifications',
                  'console': 'consoleVerifications'}
        if kind not in ROSTER:
            return fail('kind must be reproduction, verification or console')
        target = (invalidation_form.get('target') or '').strip()
        reason = (invalidation_form.get('reason') or '').strip()
        if not reason:
            return fail('an invalidation must state its reason; it is logged in the open')
        if len(reason) > ACT_NOTES_MAX:
            return fail(f'reason exceeds {ACT_NOTES_MAX} characters')
        acts = run.get(ROSTER[kind], [])
        act = next((a for a in acts if a['user'].lower() == target.lower()
                    and not a.get('invalidated')), None)
        if not act:
            return fail(f'no live {kind} by {target!r} on {run_id}', 404)
        act['invalidated'] = {'by': expert, 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(),
                              'reason': reason}
        sync_status(run)
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_invalidate': act,
                            'status': run['status']})
        (run_dir / 'run.json').write_text(json.dumps(
            {k: v for k, v in run.items() if not k.startswith('_')}, indent=1))
        ensure_member(expert)
        commit_push(f'Invalidate {kind} on {run_id}: {target} by expert {expert}\n\n'
                    f'Reason: {reason}\nVia: archivist')
    return jsonify({'ok': True, 'run': run_id, 'status': run['status'],
                    'note': 'Logged in the open site log; appealable; '
                            'the act may be redone by any other member.'})

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
        if not isinstance(row, dict):
            return None, 'each metric is an object'
        if row.get('key') == 'time':
            metric = {'key': 'time', 'label': str(row.get('label') or 'Time')[:40],
                 'type': 'time', 'better': 'lower'}
        else:
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


@app.get('/api/categories')
def categories_of_game():
    """A game's category definitions, fresh from the checkout (refreshed at
    most 20 s old). The submit form asks here instead of the raw-file CDN,
    whose 5-minute cache showed a renamed category under its old label.

    Who: anybody
    Reads: query arg game (system/slug)
    Answers: the categories.json document with ok: true, Cache-Control: no-store
    """
    game_key = (request.args.get('game') or '').strip()
    if not re.fullmatch(r'[a-z0-9-]+/[a-z0-9-]+', game_key):
        return fail('game must be system/slug')
    refresh_archive()
    categories_file = ARCHIVE / 'games' / game_key / 'categories.json'
    if not categories_file.exists():
        return fail(f'unknown game {game_key}', 404)
    resp = jsonify({'ok': True, **json.loads(categories_file.read_text())})
    resp.headers['Cache-Control'] = 'no-store'
    return resp

_search_cache = {'at': 0.0, 'members': [], 'games': []}

def _search_index():
    """Members and games, as the pickers search them (issue #56): read from
    the checkout and kept for 20 s, so typing never hits the disk per key."""
    now = time.monotonic()
    if now - _search_cache['at'] > 20:
        refresh_archive()
        members = []
        for author_file in (ARCHIVE / 'authors').glob('*.json'):
            try:
                members.append(json.loads(author_file.read_text())['username'])
            except (OSError, ValueError, KeyError):
                continue
        groups = []
        groups_file = ARCHIVE / 'groups.json'
        if groups_file.exists():
            try:
                groups = json.loads(groups_file.read_text()).get('groups', [])
            except (ValueError, AttributeError):
                groups = []
        group_of = {k: gr['key'] for gr in groups for k in gr.get('games', [])}
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
        titles = {g['key']: g['title'] for g in games}
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
        _search_cache.update(at=now, members=sorted(members, key=str.lower),
                             games=sorted(games, key=lambda g: g['title'].lower()),
                             runs=sorted(run_rows, key=lambda x: x['key']))
    return _search_cache

@app.get('/api/search')
def search():
    """Type-to-find for the pickers on the panels (issue #56): the matching
    members or games, a page at a time, so no page carries the whole list.

    Who: anybody
    Reads: query args kind (members | games), q (at least one character),
        limit (at most 50, default 20)
    Answers: {ok, kind, items}; a member item is its username, a game item
        {key, title, system, group}
    """
    kind = (request.args.get('kind') or '').strip()
    query = (request.args.get('q') or '').strip().lower()[:80]
    try:
        limit = max(1, min(50, int(request.args.get('limit') or 20)))
    except ValueError:
        limit = 20
    if kind not in ('members', 'games', 'runs'):
        return fail('kind must be members, games or runs')
    if not query:
        return fail('q must say what to look for')
    index = _search_index()
    if kind == 'members':
        hits = [m for m in index['members'] if query in m.lower()]
        hits.sort(key=lambda m: (not m.lower().startswith(query), m.lower()))
    else:
        hits = [g for g in index[kind] if query in g['title'].lower() or query in g['key'].lower()]
        hits.sort(key=lambda g: (not g['title'].lower().startswith(query), g['title'].lower()))
    resp = jsonify({'ok': True, 'kind': kind, 'items': hits[:limit]})
    resp.headers['Cache-Control'] = 'no-store'
    return resp

@app.post('/api/category/add')
def category_add():
    """Any member adds a category (creation is everybody's; only experts
    edit what exists). The creator defines its metrics; the edit log carries
    the act.

    Who: any member (session, or `key` plus `user`)
    Reads: form fields parent (optional: the category this one becomes a
        subcategory of; then metrics are refused and the rule may be empty), game, label, rule, option_key, metrics (JSON array),
        reason, dry_run
    Answers: {ok, game, key, label}; dry_run: {ok, dry_run, key}; 409 when the
        key exists
    """
    category_form = request.form
    dry_run = category_form.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        auth_error = auth_precheck(category_form)
        if auth_error:
            return auth_error
        if not dry_run:
            checkout_branch()
        expert, game_key, categories_file, categories, error = _category_gate(category_form, need_expert=False)
        if error:
            return error
        paced = pace_gate(category_form, expert, 'create')
        if paced:
            return paced
        metric_defs, metric_error = parse_metric_defs(category_form.get('metrics'))
        if metric_error:
            return fail(metric_error)
        label = (category_form.get('label') or '').strip()
        rule = (category_form.get('rule') or '').strip()
        if not (1 <= len(label) <= 80):
            return fail('a label fits in 80 characters')
        if not (1 <= len(rule) <= 2000) and not (category_form.get('parent') and len(rule) <= 2000):
            return fail('a rule fits in 2000 characters of markdown; it is what a '
                        'verifier holds a run to')
        # 'key' is the submitter-key auth field; the option key travels as
        # option_key (the same collision removal/decide once had)
        option_key = slugify((category_form.get('option_key') or label).strip())
        if not option_key:
            return fail('the label yields an empty key')
        if option_key == 'unclassified':
            return fail('unclassified is reserved: every game already has it')
        parent_key = (category_form.get('parent') or '').strip()
        if parent_key:
            # a subcategory: a second level inside an existing category. It
            # has a label and a rule fragment; metrics stay the category's
            parent = option_in(categories, parent_key)
            if not parent:
                return fail(f'{game_key} defines no category {parent_key!r}', 404)
            if metric_defs:
                return fail('a subcategory ranks by its category\'s metrics; define those on the category')
            subs = parent.setdefault('subcategories', [])
            if any(s['key'] == option_key for s in subs):
                return fail(f'{option_key!r} already exists in {parent["label"]}', 409)
            # the first subcategory changes what the category's runs need: a
            # run already there would then name none, so the category must
            # be empty, or the new subcategory must take them all
            holders = [rp for rp in (ARCHIVE / 'games' / game_key / 'runs').glob('*/run.json')
                       if (json.loads(rp.read_text()).get('category') or {}).get('goal') == parent_key
                       and not (json.loads(rp.read_text()).get('category') or {}).get('sub')]
            if dry_run:
                return jsonify({'ok': True, 'dry_run': True, 'key': option_key, 'parent': parent_key,
                                'runs_moved': len(holders) if not subs else 0})
            moved = 0
            if not subs and holders:
                for rp in holders:
                    run_doc = json.loads(rp.read_text())
                    run_doc['category']['sub'] = option_key
                    rp.write_text(json.dumps(run_doc, indent=1) + '\n')
                    moved += 1
            subs.append({'key': option_key, 'label': label, **({'rule': rule} if rule else {})})
            categories_file.write_text(json.dumps(categories, indent=1) + '\n')
            log_edit('category', f'{game_key}:{parent_key}/{option_key}', 'added', '', label, expert,
                     (category_form.get('reason') or 'Created it.').strip()[:500])
            ensure_member(expert)
            commit_push(f'Subcategory add {game_key}:{parent_key}/{option_key}: by {expert}\n\n'
                        f'Label: {label}\nRuns moved into it: {moved}\nVia: archivist')
            return jsonify({'ok': True, 'game': game_key, 'key': option_key, 'parent': parent_key,
                            'label': label, 'runs_moved': moved})
        goal_dimension = next((d for d in categories['dimensions'] if d['key'] == 'goal'),
                   categories['dimensions'][0] if categories['dimensions'] else None)
        if goal_dimension is None:
            categories['dimensions'] = [{'key': 'goal', 'name': 'Category', 'options': []}]
            goal_dimension = categories['dimensions'][0]
        if any(o['key'] == option_key for d in categories['dimensions'] for o in d['options']):
            return fail(f'{option_key!r} already exists on this game', 409)
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'key': option_key})
        goal_dimension['options'].append({'key': option_key, 'label': label, 'rule': rule,
                               **({'metrics': metric_defs} if metric_defs else {})})
        categories_file.write_text(json.dumps(categories, indent=1) + '\n')
        log_edit('category', f'{game_key}:{option_key}', 'added', '', label, expert,
                 (category_form.get('reason') or 'Created it.').strip()[:500])
        ensure_member(expert)
        game_title = json.loads((ARCHIVE / 'games' / game_key / 'game.json')
                            .read_text()).get('title', game_key)
        commit_push(f'Category add {game_key}:{option_key}: by {expert}\n\n'
                    f'Label: {label}\nVia: archivist')
        notify_discord(f'\U0001f5c2\ufe0f **{member_md(expert)}** created the category '
                       f'[{label}](<{SITE_URL}/games/{game_key}/>) in '
                       f'[[{game_key.split("/")[0].upper()}] {game_title}]'
                       f'(<{SITE_URL}/games/{game_key}/>)',
                       wait_for=f'{SITE_URL}/games/{game_key}/')
    return jsonify({'ok': True, 'game': game_key, 'key': option_key, 'label': label})




@app.post('/api/category/reorder')
def category_reorder():
    """Put a game's categories, or one category's subcategories, in the order
    given: the popular ones first, at the left of every selector. Pure
    order; nothing else about them changes.

    Who: an expert covering the game, or an editor (`key` plus `expert`)
    Reads: form fields game, order (comma-separated keys, the whole set),
        option (optional: reorder that category's subcategories instead),
        reason (optional), dry_run
    Answers: {ok, game, order}; 400 when the keys are not exactly the set
    """
    form = request.form
    dry_run = form.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        auth_error = auth_precheck(form)
        if auth_error:
            return auth_error
        if not dry_run:
            checkout_branch()
        expert, game_key, categories_file, categories, error = _category_gate(form)
        if error:
            return error
        wanted = [k.strip() for k in (form.get('order') or '').split(',') if k.strip()]
        option_key = (form.get('option') or '').strip()
        if option_key:
            option = option_in(categories, option_key)
            if not option:
                return fail(f'{game_key} defines no category {option_key!r}', 404)
            items = option.get('subcategories') or []
            what = f'{option_key} subcategories'
        else:
            dimension = next((d for d in categories['dimensions'] if d['key'] == 'goal'),
                             categories['dimensions'][0] if categories['dimensions'] else None)
            if dimension is None:
                return fail('this game has no categories yet')
            items = dimension['options']
            what = 'categories'
        have = [x['key'] for x in items]
        if sorted(wanted) != sorted(have):
            return fail(f'order must list exactly the {what}: {", ".join(have)}')
        if wanted == have:
            return fail('that is already the order')
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'order': wanted})
        by_key = {x['key']: x for x in items}
        reordered = [by_key[k] for k in wanted]
        if option_key:
            option['subcategories'] = reordered
        else:
            dimension['options'] = reordered
        categories_file.write_text(json.dumps(categories, indent=1) + '\n')
        log_edit('category', f'{game_key}:{option_key or "*"}', 'order', ', '.join(have), ', '.join(wanted),
                 expert, (form.get('reason') or 'Reordered.').strip()[:500])
        ensure_member(expert)
        commit_push(f'Category order {game_key}{":" + option_key if option_key else ""}: by {expert}\n\n'
                    f'Order: {", ".join(wanted)}\nVia: archivist')
    return jsonify({'ok': True, 'game': game_key, 'order': wanted})


@app.post('/api/category/delete')
def category_delete():
    """Remove an option no run has ever used. Anything referenced stays: a
    category with runs in it is the runs' home, not clutter.

    Who: an expert covering the game, or an editor (`key` plus `expert`)
    Reads: form fields game, option, sub (optional: remove that subcategory
        alone; the last one may hold runs, which then stay in the category
        naming none), reason, dry_run
    Answers: {ok, game, removed}; 409 while any run sits in the category
    """
    category_form = request.form
    dry_run = category_form.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        auth_error = auth_precheck(category_form)
        if auth_error:
            return auth_error
        if not dry_run:
            checkout_branch()
        expert, game_key, categories_file, categories, error = _category_gate(category_form)
        if error:
            return error
        option_key = (category_form.get('option') or '').strip()
        option = next((option for dimension in categories['dimensions'] for option in dimension['options']
                    if option['key'] == option_key), None)
        if not option:
            return fail(f'{game_key} defines no category {option_key!r}', 404)
        sub_key = (category_form.get('sub') or '').strip()
        if sub_key:
            # one subcategory, when no run sits in it
            subs = option.get('subcategories') or []
            sub = next((s for s in subs if s['key'] == sub_key), None)
            if not sub:
                return fail(f'{option["label"]} has no subcategory {sub_key!r}', 404)
            holders = [rp for rp in (ARCHIVE / 'games' / game_key / 'runs').glob('*/run.json')
                       if (json.loads(rp.read_text()).get('category') or {}).get('goal') == option_key
                       and (json.loads(rp.read_text()).get('category') or {}).get('sub') == sub_key]
            last = len(subs) == 1
            # the last subcategory dissolves the level: its runs stay in the
            # category, naming none (the mirror of the first one taking them);
            # any other subcategory must be empty to go
            if holders and not last:
                return fail(f'{sub_key!r} holds {len(holders)} run(s); a subcategory with runs '
                            f'in it is their home, not clutter', 409)
            if dry_run:
                return jsonify({'ok': True, 'dry_run': True, 'runs_released': len(holders) if last else 0})
            released = 0
            if last:
                for rp in holders:
                    run_doc = json.loads(rp.read_text())
                    run_doc['category'].pop('sub', None)
                    rp.write_text(json.dumps(run_doc, indent=1) + '\n')
                    released += 1
            option['subcategories'] = [s for s in subs if s['key'] != sub_key]
            if not option['subcategories']:
                option.pop('subcategories')
            categories_file.write_text(json.dumps(categories, indent=1) + '\n')
            log_edit('category', f'{game_key}:{option_key}/{sub_key}', 'removed', sub.get('label', sub_key),
                     '', expert, (category_form.get('reason') or 'Removed unused by a covering expert.').strip()[:500])
            ensure_member(expert)
            commit_push(f'Subcategory remove {game_key}:{option_key}/{sub_key}: by expert {expert}\n\n'
                        f'Runs released into the category: {released}\nVia: archivist')
            return jsonify({'ok': True, 'game': game_key, 'removed': f'{option_key}/{sub_key}',
                            'runs_released': released})
        runs_in_category = [json.loads(run_json_path.read_text())['id']
                 for run_json_path in (ARCHIVE / 'games' / game_key / 'runs').glob('*/run.json')
                 if (json.loads(run_json_path.read_text()).get('category') or {}).get('goal') == option_key]
        if runs_in_category:
            return fail(f'{option_key!r} holds {len(runs_in_category)} run(s) ({", ".join(runs_in_category[:4])}'
                        f'{"…" if len(runs_in_category) > 4 else ""}); a category with runs '
                        f'in it is their home, not clutter', 409)
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True})
        for dimension in categories['dimensions']:
            dimension['options'] = [option for option in dimension['options'] if option['key'] != option_key]
        categories_file.write_text(json.dumps(categories, indent=1) + '\n')
        log_edit('category', f'{game_key}:{option_key}', 'removed', option.get('label', option_key),
                 '', expert,
                 (category_form.get('reason') or 'Removed unused by a covering expert.').strip()[:500])
        ensure_member(expert)
        commit_push(f'Category remove {game_key}:{option_key}: by expert {expert}\n\n'
                    f'Via: archivist')
    return jsonify({'ok': True, 'game': game_key, 'removed': option_key})



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

# The general voiding rule: a change to the run's SCORING (its time or any
# metric) invalidates the verifications, which attested those values from
# the encode; a change to its REPRODUCTION INFORMATION (the movie file, the
# tool it plays in, the files it was made against) invalidates the
# reproductions and the console verifications, which synced the old setup.
# Nothing else voids anything.
SCORING_FIELDS = {'duration'}                      # plus every metric:<key>
REPRO_FIELDS = {'movie', 'emulator', 'files'}

def void_acts_for(run, changed, by):
    """What an edit does to the acts already on the run, by the general
    rule: a scoring change (time or any metric) invalidates the live
    verifications, which attested those values, and the run leaves the
    ranking until somebody verifies it again; a reproduction-information
    change (the movie file, the tool, the files it was made against)
    invalidates the live reproductions and console verifications, which
    synced the old setup. Nothing else voids anything.
    Returns the kinds voided."""
    voided = []
    stamp = {'by': by, 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(), 'cause': 'edit'}
    if any(c in SCORING_FIELDS or c.startswith('metric:') for c in changed):
        for v in run.get('verifications', []):
            if not v.get('invalidated'):
                v['invalidated'] = dict(stamp, reason='the scoring changed after this verification')
                if 'verifications' not in voided: voided.append('verifications')
    if any(c in REPRO_FIELDS for c in changed):
        what = 'the movie file' if 'movie' in changed else 'the reproduction information'
        for r_ in run.get('reproductions', []):
            if not r_.get('invalidated'):
                r_['invalidated'] = dict(stamp, reason=f'{what} changed after this reproduction')
                if 'reproductions' not in voided: voided.append('reproductions')
        for c_ in run.get('consoleVerifications', []):
            if not c_.get('invalidated'):
                c_['invalidated'] = dict(stamp, reason=f'{what} changed after this console verification')
                if 'consoleVerifications' not in voided: voided.append('consoleVerifications')
    if voided:
        sync_status(run)
    return voided

def live_acts(run):
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

def parse_game_property(field, raw):
    """(value, error) for one game property from form text; None clears."""
    text = (raw or '').strip()
    if not text:
        return None, None
    if field == 'released':
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
        if len(text) > 300 or not re.fullmatch(r'https?://[^\s<>"\']+', text):
            return None, f'the {"RTA leaderboards" if field == "rta" else "community website"} link is an http(s) URL'
        return text, None
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

@app.post('/api/expert/edit')
def expert_edit():
    """An expert corrects the record inside their jurisdiction, field by field,
    each change logged with who, from, to, and why.

    Who: an expert covering the target; an editor for library shape only
        (game title and thumbnail, category fields, group titles, and
        moving a run between goals)
    Reads: form fields kind (run|game|category|group), target (a category
        target may name a subcategory: system/slug:option/sub; a run's goal
        value may too: option/sub), field, value,
        reason (8 to 500 chars), dry_run; files movie (run.movie) and
        thumbnail (game.thumbnail)
    Answers: {ok, kind, key, field, from, to}, plus runs_seeded for category
        metrics; dry_run: {ok, dry_run, field, from, to}
    """
    edit_form = request.form
    dry_run = edit_form.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        auth_error = auth_precheck(edit_form)
        if auth_error:
            return auth_error
        actor, error = request_identity(edit_form, 'expert')
        if error:
            return error
        kind = (edit_form.get('kind') or '').strip()
        target = (edit_form.get('target') or '').strip()
        field = (edit_form.get('field') or '').strip()
        value = (edit_form.get('value') or '').strip()
        reason = (edit_form.get('reason') or '').strip()
        if kind not in EXPERT_EDITABLE:
            return fail('kind must be run, game, category or group')
        if field not in EXPERT_EDITABLE[kind] and not (
                kind == 'run' and field.startswith('metric:')):
            return fail(f'{field!r} is not expert-editable on a {kind}; the record '
                        f'allows: {", ".join(EXPERT_EDITABLE[kind])}. Member content '
                        f'is never edited by anybody but its author.')
        if not (8 <= len(reason) <= 500):
            return fail('say why, publicly: the edit log carries your reason')
        if not dry_run:
            checkout_branch()

        if kind == 'run':
            if not re.fullmatch(r'M[0-9]+', target):
                return fail('target must be a run id like M100001')
            run_dir = find_run(target)
            if not run_dir:
                return fail(f'unknown run {target}', 404)
            game_key = f'{run_dir.parent.parent.parent.name}/{run_dir.parent.parent.name}'
            if not expert_covers(actor, game_key):
                # an editor shapes the library, not the runs: the one run
                # field that is library shape is which category it sits in
                if not (is_editor(actor) and field == 'goal'):
                    return fail(f'{actor!r} is not an expert covering {game_key}'
                                f' (an editor may only move a run between '
                                f'categories)', 403)
            run = json.loads((run_dir / 'run.json').read_text())
            if field.startswith('metric:'):
                metric_key = field.split(':', 1)[1]
                if metric_key == 'time':
                    return fail("the run's time is the duration field, not a stored metric")
                try:
                    new_value = float(value)
                except ValueError:
                    return fail('value must be a number (seconds for times)')
                if new_value < 0:
                    return fail('a metric value cannot be negative')
                old_value = (run.get('metrics') or {}).get(metric_key, 0)
                run.setdefault('metrics', {})[metric_key] = new_value
                value = str(new_value)
            elif field == 'duration':
                sys_key, slug_key = run['game'].split('/')
                _, cats_doc = load_game(sys_key, slug_key)
                goal_key = (run.get('category') or {}).get('goal')
                opt_def = next((o for dim in (cats_doc or {}).get('dimensions', [])
                                for o in dim['options'] if o['key'] == goal_key), None)
                opt_metrics = (opt_def or {}).get('metrics')
                if not (opt_metrics is None or any(mm['key'] == 'time' for mm in opt_metrics)):
                    return fail('this category does not rank by time; there is no stated time to correct')
                time_match = re.fullmatch(r'(?:(\d{1,3}):)?(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?',
                                   value)
                if not time_match:
                    return fail('value must be a time, [h:]mm:ss or [h:]mm:ss.mmm')
                hours, minutes, seconds, fraction = time_match.groups()
                new_value = (int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)
                         + (int(fraction.ljust(3, "0")) / 1000 if fraction else 0.0))
                if new_value <= 0:
                    return fail('a run that takes no time at all is not a run')
                old_value = run.get('duration')
                run['duration'] = new_value
            elif field == 'goal':
                # the value names the category, and the subcategory after a
                # slash when the category has them: "episode-1/any"
                categories = json.loads((run_dir.parent.parent / 'categories.json').read_text())
                goal_value, _, sub_value = value.partition('/')
                valid_goals = {o['key'] for d in categories['dimensions'] for o in d['options']}
                valid_goals.add('unclassified')
                if goal_value not in valid_goals:
                    return fail(f'{goal_value!r} is not a goal this game defines')
                if goal_value == 'unclassified' and any(
                        not v.get('invalidated') for v in run.get('verifications', [])):
                    return fail('this run holds live verifications, which are bound '
                                'to its goal; unclassifying it would void them, and '
                                'that is not an edit')
                old_category = dict(run.get('category') or {})
                old_value = old_category.get('goal', '') + ('/' + old_category['sub'] if old_category.get('sub') else '')
                new_category = {'goal': goal_value}
                if goal_value != 'unclassified':
                    sub_error = place_subcategory(categories, new_category, sub_value)
                    if sub_error:
                        return fail(sub_error)
                if old_value == value:
                    return fail('that is already its goal')
                run['category'] = new_category
            elif field == 'encode':
                encode_provider = providers.resolve(value)
                if not encode_provider:
                    return fail('value must be a watchable encode URL on a platform '
                                'we accept')
                old_value = (run.get('encodes') or [{}])[0].get('url', '')
                run['encodes'] = [{'kind': encode_provider['kind'], 'url': value}]
            elif field == 'goalDescription':
                if len(value) > 500:
                    return fail('a goal description fits in 500 characters')
                old_value = run.get('goalDescription', '')
                if value:
                    run['goalDescription'] = value
                else:
                    run.pop('goalDescription', None)
                if is_uncl_run(run) and not value:
                    return fail('an Unclassified run states its own goal; it cannot '
                                'lose its description')
            elif field == 'notes':
                if len(value.encode()) > 64 * 1024:
                    return fail('notes fit in 64 KB')
                notes_file = run_dir / 'notes.md'
                old_value = (notes_file.read_text()[:300] if notes_file.exists() else '')
                if dry_run:
                    return jsonify({'ok': True, 'dry_run': True, 'field': field,
                                    'from': old_value, 'to': value[:300]})
                if value:
                    notes_file.write_text(value + ('\n' if not value.endswith('\n') else ''))
                elif notes_file.exists():
                    notes_file.unlink()
            elif field == 'movie':
                if run.get('videoOnly'):
                    return fail('a video-only run has no movie file to replace')
                new_movie_upload = request.files.get('movie')
                if not new_movie_upload or not new_movie_upload.filename:
                    return fail('attach the replacement movie file')
                movie_ext = new_movie_upload.filename.rsplit('.', 1)[-1].lower()
                movie_bytes = new_movie_upload.read()
                if not movie_bytes or len(movie_bytes) > MOVIE_MAX:
                    return fail('movie must be non-empty and under 16 MB')
                # the same door as submission: any extension, and a parse
                # failure keeps the file with frames unknown
                parsed_movie = movieparse.parse(new_movie_upload.filename, movie_bytes)
                if not parsed_movie['ok']:
                    parsed_movie = {'ok': False, 'frames': 0, 'rerecords': None, 'start': 'power-on', 'fps': None}
                old_value = f"{run['movie']['file']} (sha1 {run['movie'].get('sha1', '?')[:12]})"
                value = f"{run['id']}.{movie_ext} (sha1 {hashlib.sha1(movie_bytes).hexdigest()[:12]})"
                if dry_run:
                    return jsonify({'ok': True, 'dry_run': True, 'field': field,
                                    'from': old_value, 'to': value})
                (run_dir / run['movie']['file']).unlink(missing_ok=True)
                (run_dir / f"{run['id']}.{movie_ext}").write_bytes(movie_bytes)
                run['movie'] = {'file': f"{run['id']}.{movie_ext}", 'format': movie_ext,
                              'sha1': hashlib.sha1(movie_bytes).hexdigest(),
                              'frames': parsed_movie['frames'],
                              'rerecords': parsed_movie['rerecords'],
                              'start': parsed_movie['start'],
                              **({'fps': parsed_movie['fps']} if parsed_movie.get('fps') else {})}
            would_void = []
            if (field in SCORING_FIELDS or field.startswith('metric:')) and live_acts(run)['verifications']: would_void.append('verifications')
            if field in REPRO_FIELDS:
                if live_acts(run)['reproductions']: would_void.append('reproductions')
                if any(not c_.get('invalidated') for c_ in run.get('consoleVerifications', [])): would_void.append('consoleVerifications')
            if dry_run:
                return jsonify({'ok': True, 'dry_run': True, 'field': field,
                                'from': old_value, 'to': value, 'would_void': would_void})
            voided = void_acts_for(run, [field], actor)
            (run_dir / 'run.json').write_text(json.dumps(
                {k: v for k, v in run.items() if not k.startswith('_')}, indent=1))
            log_edit('run', target, field, old_value, value, actor, reason)
            if voided:
                log_edit('run', target, 'acts voided', ', '.join(voided), 'by the change of ' + field, actor, reason)

        elif kind == 'game':
            target_match = re.fullmatch(r'([a-z0-9-]+)/([a-z0-9-]+)', target)
            if not target_match:
                return fail('target must be system/slug')
            game_file = ARCHIVE / 'games' / target / 'game.json'
            if not game_file.exists():
                return fail(f'unknown game {target}', 404)
            if not expert_covers(actor, target) and not is_editor(actor):
                return fail(f'{actor!r} is not an expert covering {target}, '
                            f'nor an editor', 403)
            game = json.loads(game_file.read_text())
            if field == 'title':
                if not (1 <= len(value) <= 120):
                    return fail('a title fits in 120 characters')
                old_value = game.get('title')
                if old_value == value:
                    return fail('that is already its title')
                game['title'] = value
            elif field in GAME_PROPERTY_FIELDS:
                # the game properties (#44): an empty value clears the field
                old_value = game.get(field, '')
                value, property_error = parse_game_property(field, value)
                if property_error:
                    return fail(property_error)
                if value is None:
                    value = ''
                if old_value == value:
                    return fail(f'that is already its {field}')
                if value == '':
                    game.pop(field, None)
                else:
                    game[field] = value
                # the record keeps the typed value (a real boolean); the log
                # and the answer carry it as text like every other edit
                old_value, value = str(old_value), str(value)
            else:
                screenshot_upload = request.files.get('thumbnail')
                if not screenshot_upload or not screenshot_upload.filename:
                    return fail('attach the thumbnail image')
                upload_ext = pathlib.Path(screenshot_upload.filename).suffix.lower()
                stored_ext = '.jpg' if upload_ext == '.jpeg' else upload_ext
                if stored_ext not in IMAGE_MAGIC:
                    return fail('thumbnail must be png, jpg or webp')
                image_bytes = screenshot_upload.read()
                if not image_bytes or len(image_bytes) > THUMB_MAX:
                    return fail(f'thumbnail must be non-empty and under '
                                f'{THUMB_MAX >> 10} KB')
                if not any(image_bytes.startswith(magic) for magic in IMAGE_MAGIC[stored_ext]):
                    return fail('that file is not the image its name claims')
                old_value = game.get('thumbnail', '')
                value = f'thumb{stored_ext}'
                if dry_run:
                    return jsonify({'ok': True, 'dry_run': True, 'field': field,
                                    'from': old_value, 'to': value})
                if old_value:
                    (ARCHIVE / 'games' / target / old_value).unlink(missing_ok=True)
                (ARCHIVE / 'games' / target / value).write_bytes(image_bytes)
                game['thumbnail'] = value
            if dry_run:
                return jsonify({'ok': True, 'dry_run': True, 'field': field,
                                'from': old_value, 'to': value})
            game_file.write_text(json.dumps(game, indent=1) + '\n')
            log_edit('game', target, field, old_value, value, actor, reason)

        elif kind == 'category':
            target_match = re.fullmatch(r'([a-z0-9-]+/[a-z0-9-]+):([a-z0-9-]+|\*)(?:/([a-z0-9-]+))?', target)
            if not target_match:
                return fail('target must be system/slug:option, or system/slug:option/subcategory')
            game_key, option_key, sub_key = target_match.group(1), target_match.group(2), target_match.group(3)
            if option_key == '*' and field == 'selector':
                # the dimension itself: how the game page offers its categories
                categories_file = ARCHIVE / 'games' / game_key / 'categories.json'
                if not categories_file.exists():
                    return fail(f'unknown game {game_key}', 404)
                if not expert_covers(actor, game_key) and not is_editor(actor):
                    return fail(f'{actor!r} is not an expert covering {game_key}, nor an editor', 403)
                if value not in ('buttons', 'dropdown'):
                    return fail('selector is buttons or dropdown')
                categories = json.loads(categories_file.read_text())
                dimension = next((d for d in categories['dimensions'] if d['key'] == 'goal'),
                                 categories['dimensions'][0] if categories['dimensions'] else None)
                if dimension is None:
                    return fail('this game has no categories yet')
                old_value = dimension.get('selector', 'buttons')
                if old_value == value:
                    return fail('that is already how they are shown')
                if value == 'buttons':
                    dimension.pop('selector', None)
                else:
                    dimension['selector'] = value
                if dry_run:
                    return jsonify({'ok': True, 'dry_run': True, 'field': field, 'from': old_value, 'to': value})
                categories_file.write_text(json.dumps(categories, indent=1) + '\n')
                log_edit('category', target, field, old_value, value, actor, reason)
                ensure_member(actor)
                commit_push(f'Expert edit category {target}: selector\n\nFrom: {old_value}\nTo: {value}\n'
                            f'By: {actor}\nReason: {reason}\nVia: archivist')
                return jsonify({'ok': True, 'kind': kind, 'key': target, 'field': field,
                                'from': old_value, 'to': value})
            categories_file = ARCHIVE / 'games' / game_key / 'categories.json'
            if not categories_file.exists():
                return fail(f'unknown game {game_key}', 404)
            if not expert_covers(actor, game_key) and not is_editor(actor):
                return fail(f'{actor!r} is not an expert covering {game_key}, '
                            f'nor an editor', 403)
            categories = json.loads(categories_file.read_text())
            option = next((o for d in categories['dimensions'] for o in d['options']
                        if o['key'] == option_key), None)
            if not option:
                return fail(f'{game_key} defines no category {option_key!r}', 404)
            if field == 'key':
                # rename the category's (or subcategory's) key (issue #69):
                # the label is what readers see, the key is the address the
                # rankings and runs point at, so every run in it follows in
                # the same commit. Nothing judged changes: nothing is voided.
                new_key = slugify(value)
                if not new_key:
                    return fail('a key is lowercase-with-hyphens')
                if new_key == 'unclassified':
                    return fail('unclassified is reserved')
                runs_dir = ARCHIVE / 'games' / game_key / 'runs'
                if sub_key:
                    subs = option.get('subcategories', [])
                    sub = next((s for s in subs if s['key'] == sub_key), None)
                    if not sub:
                        return fail(f'{option_key} has no subcategory {sub_key!r}', 404)
                    if new_key == sub_key:
                        return fail('that is already its key')
                    if any(s['key'] == new_key for s in subs):
                        return fail(f'{new_key!r} already exists in {option["label"]}', 409)
                    old_value = sub_key
                else:
                    if new_key == option_key:
                        return fail('that is already its key')
                    if any(o['key'] == new_key for d in categories['dimensions']
                           for o in d['options']):
                        return fail(f'{new_key!r} already exists on this game', 409)
                    old_value = option_key
                moved = 0
                for run_json_path in runs_dir.glob('*/run.json'):
                    run_doc = json.loads(run_json_path.read_text())
                    run_cat = run_doc.get('category') or {}
                    if run_cat.get('goal') != option_key:
                        continue
                    if sub_key:
                        if run_cat.get('sub') != sub_key:
                            continue
                    if dry_run:
                        moved += 1
                        continue
                    if sub_key:
                        run_cat['sub'] = new_key
                    else:
                        run_cat['goal'] = new_key
                    run_json_path.write_text(json.dumps(run_doc, indent=1) + '\n')
                    moved += 1
                if dry_run:
                    return jsonify({'ok': True, 'dry_run': True, 'field': field,
                                    'from': old_value, 'to': new_key, 'runs_moved': moved})
                if sub_key:
                    sub['key'] = new_key
                else:
                    option['key'] = new_key
                categories_file.write_text(json.dumps(categories, indent=1) + '\n')
                log_edit('category', target, field, old_value, new_key, actor, reason)
                ensure_member(actor)
                commit_push(f'Expert edit category {target}: key\n\n'
                            f'From: {old_value}\nTo: {new_key}\n'
                            f'Runs following the rename: {moved}\n'
                            f'By: {actor}\nReason: {reason}\nVia: archivist')
                return jsonify({'ok': True, 'kind': kind, 'key': target, 'field': field,
                                'from': old_value, 'to': new_key, 'runs_moved': moved})
            if field == 'subSelector' and not sub_key:
                # how this category's subcategories are offered
                if value not in ('buttons', 'dropdown'):
                    return fail('subSelector is buttons or dropdown')
                old_value = option.get('subSelector', 'buttons')
                if old_value == value:
                    return fail('that is already how they are shown')
                if value == 'buttons':
                    option.pop('subSelector', None)
                else:
                    option['subSelector'] = value
                if dry_run:
                    return jsonify({'ok': True, 'dry_run': True, 'field': field, 'from': old_value, 'to': value})
                categories_file.write_text(json.dumps(categories, indent=1) + '\n')
                log_edit('category', target, field, old_value, value, actor, reason)
                ensure_member(actor)
                commit_push(f'Expert edit category {target}: subSelector\n\nFrom: {old_value}\nTo: {value}\n'
                            f'By: {actor}\nReason: {reason}\nVia: archivist')
                return jsonify({'ok': True, 'kind': kind, 'key': target, 'field': field,
                                'from': old_value, 'to': value})
            if sub_key:
                # a subcategory's label or rule: the same edit, on the inner record
                sub = next((s for s in option.get('subcategories', []) if s['key'] == sub_key), None)
                if not sub:
                    return fail(f'{option["label"]} has no subcategory {sub_key!r}', 404)
                if field not in ('label', 'rule'):
                    return fail('a subcategory has a label and a rule; metrics are the category\'s')
                option = sub
            if field == 'metrics':
                metric_defs, metric_error = parse_metric_defs(value)
                if metric_error:
                    return fail(metric_error)
                old_defs = option.get('metrics')
                old_value = json.dumps(old_defs) if old_defs else '(classic: time)'
                new_value = json.dumps(metric_defs) if metric_defs else '(classic: time)'
                if old_value == new_value:
                    return fail('that is already its metric definition')
                if dry_run:
                    return jsonify({'ok': True, 'dry_run': True, 'field': field,
                                    'from': old_value, 'to': new_value})
                if metric_defs:
                    option['metrics'] = metric_defs
                else:
                    option.pop('metrics', None)
                categories_file.write_text(json.dumps(categories, indent=1) + '\n')
                # a freshly added metric writes the explicit empty value onto
                # every run already in the category: nothing gets unranked,
                # zeros rank last, and the experts fill them in from here
                old_keys = {metric_def['key'] for metric_def in (old_defs or [])}
                fresh = [metric_def['key'] for metric_def in (metric_defs or [])
                         if metric_def['key'] != 'time' and metric_def['key'] not in old_keys]
                touched = 0
                if fresh:
                    for run_json_path in (ARCHIVE / 'games' / game_key / 'runs').glob('*/run.json'):
                        category_run = json.loads(run_json_path.read_text())
                        if (category_run.get('category') or {}).get('goal') != option_key:
                            continue
                        for fresh_key in fresh:
                            category_run.setdefault('metrics', {}).setdefault(fresh_key, 0)
                        run_json_path.write_text(json.dumps(category_run, indent=1) + '\n')
                        touched += 1
                log_edit('category', target, field, old_value[:300], new_value[:300],
                         actor, reason)
                ensure_member(actor)
                commit_push(f'Expert edit category {target}: metrics\n\n'
                            f'By: {actor}\nReason: {reason}\n'
                            f'Runs seeded with empty values: {touched}\n'
                            f'Via: archivist')
                return jsonify({'ok': True, 'kind': kind, 'key': target,
                                'field': field, 'from': old_value, 'to': new_value,
                                'runs_seeded': touched})
            limit = 80 if field == 'label' else 2000   # rules are markdown
            if not (1 <= len(value) <= limit):
                return fail(f'a {field} fits in {limit} characters')
            old_value = option.get(field, '')
            if old_value == value:
                return fail(f'that is already its {field}')
            option[field] = value
            if dry_run:
                return jsonify({'ok': True, 'dry_run': True, 'field': field,
                                'from': old_value, 'to': value})
            categories_file.write_text(json.dumps(categories, indent=1) + '\n')
            log_edit('category', target, field, old_value, value, actor, reason)

        else:
            groups_doc = load_groups()
            group = next((g for g in groups_doc['groups'] if g['key'] == target.lower()), None)
            if not group:
                return fail(f'no group with the key {target!r}', 404)
            if not covers_group(actor, group) and not is_editor(actor):
                return fail(f'{actor} holds no scope covering the {group["title"]} group',
                            403)
            if not (1 <= len(value) <= 80):
                return fail('a title fits in 80 characters')
            old_value = group.get('title')
            if old_value == value:
                return fail('that is already its title')
            group['title'] = value
            if dry_run:
                return jsonify({'ok': True, 'dry_run': True, 'field': field,
                                'from': old_value, 'to': value})
            save_groups(groups_doc)
            log_edit('group', target.lower(), field, old_value, value, actor, reason)

        ensure_member(actor)
        commit_push(f'Expert edit {kind} {target}: {field}\n\n'
                    f'From: {str(old_value)[:120]}\nTo: {value[:120]}\n'
                    f'By: {actor}\nReason: {reason}\nVia: archivist')
    return jsonify({'ok': True, 'kind': kind, 'key': target, 'field': field,
                    'from': old_value, 'to': value})

@app.post('/api/run/delete')
def run_delete():
    """An expert deletes a movie outright: tests, spam, non-TAS, mistakes.

    This is the fast lane beside withdrawal (which keeps a tombstone) and
    all-author erasure (Terms 4.1). It exists for things that were never
    really works; the reason is public and permanent even though the run is
    neither.

    Who: an expert covering the run's game (`key` plus `expert`)
    Reads: form fields run, reason (8 to 500 chars), dry_run
    Answers: {ok, deleted, note}; dry_run: {ok, dry_run, would_delete, game}
    """
    deletion_form = request.form
    dry_run = deletion_form.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        auth_error = auth_precheck(deletion_form)
        if auth_error:
            return auth_error
        actor, reason, error = _deletion_gate(deletion_form)
        if error:
            return error
        run_id = (deletion_form.get('run') or '').strip()
        run_dir = find_run(run_id) if re.fullmatch(r'M[0-9]+', run_id) else None
        if not run_dir:
            return fail(f'unknown run {run_id}', 404)
        game_key = f'{run_dir.parent.parent.parent.name}/{run_dir.parent.parent.name}'
        if not expert_covers(actor, game_key):
            return fail(f'{actor!r} is not an expert covering {game_key}', 403)
        run = json.loads((run_dir / 'run.json').read_text())
        title = f'{game_key} ({(run.get("category") or {}).get("goal", "?")})'
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_delete': run_id,
                            'game': game_key})
        checkout_branch()
        run_dir = find_run(run_id)
        if not run_dir:
            return fail(f'unknown run {run_id}', 404)
        shutil.rmtree(run_dir)
        log_deletion('run', run_id, title, actor, reason)
        ensure_member(actor)
        commit_push(f'Delete {run_id}: by expert {actor}\n\nReason: {reason}\nVia: archivist')
    # the run's own announce topic closes with the reason (member replies
    # stay readable), and Discord hears; both best-effort, after the write
    close_announce_topic((run.get('forum') or {}).get('topicId'),
                         f'This run was deleted from the archive by expert {actor}. '
                         f'Reason, from the public log: {reason}')
    notify_discord(f'\U0001f5d1\ufe0f Run {run_id} ({title}) was deleted by expert '
                   f'**{member_md(actor)}**: {reason}')
    return jsonify({'ok': True, 'deleted': run_id,
                    'note': 'Gone, with your reason in the site log. Withdrawal and '
                            'all-author erasure remain the routes for genuine works.'})

@app.post('/api/game/delete')
def game_delete():
    """An expert deletes a game outright, and its runs go with it.

    The use case is a game that should never have been archived: rule
    violations, spam, fabrications. Every deleted run gets its own line in
    deletions.json beside the game's, so the log carries the whole act, and
    git history keeps the bytes. Works that were genuine but mis-homed are
    moved by an expert run edit, never by deletion.

    Who: an expert covering the game
    Reads: form fields game (system/slug), reason, dry_run
    Answers: {ok, deleted, runs_deleted}
    """
    deletion_form = request.form
    dry_run = deletion_form.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        auth_error = auth_precheck(deletion_form)
        if auth_error:
            return auth_error
        actor, reason, error = _deletion_gate(deletion_form)
        if error:
            return error
        game_match = re.fullmatch(r'([a-z0-9-]+)/([a-z0-9-]+)', (deletion_form.get('game') or '').strip())
        if not game_match:
            return fail('game must be system/slug')
        game_key = game_match.group(0)
        system, slug = game_match.groups()
        game_dir = ARCHIVE / 'games' / system / slug
        if not (game_dir / 'game.json').exists():
            return fail(f'unknown game {game_key}', 404)
        if not expert_covers(actor, game_key):
            return fail(f'{actor!r} is not an expert covering {game_key}', 403)
        game = json.loads((game_dir / 'game.json').read_text())
        run_dirs = sorted(run_dir for run_dir in (game_dir / 'runs').glob('M*') if run_dir.is_dir())
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_delete': game_key,
                            'runs_deleted': [run_dir.name for run_dir in run_dirs]})
        checkout_branch()
        game_dir = ARCHIVE / 'games' / system / slug
        if not (game_dir / 'game.json').exists():
            return fail(f'unknown game {game_key}', 404)
        run_dirs = sorted(run_dir for run_dir in (game_dir / 'runs').glob('M*') if run_dir.is_dir())
        deleted_runs = []
        orphan_topics = []
        for run_dir in run_dirs:
            try:
                run_doc = json.loads((run_dir / 'run.json').read_text())
                run_title = f'{game.get("title", game_key)} ' \
                         f'({(run_doc.get("category") or {}).get("goal", "?")})'
                if (run_doc.get('forum') or {}).get('topicId'):
                    orphan_topics.append((run_dir.name, run_doc['forum']['topicId']))
            except Exception:                                 # noqa: BLE001
                run_title = game.get('title', game_key)
            log_deletion('run', run_dir.name, run_title, actor,
                         f'Its game {game_key} was deleted. {reason}')
            deleted_runs.append(run_dir.name)
        shutil.rmtree(game_dir)
        # the game leaves any group it sat in; a group cannot hold a ghost
        groups_doc = load_groups()
        changed = False
        for group in groups_doc['groups']:
            if game_key in group.get('games', []):
                group['games'] = [g for g in group['games'] if g != game_key]
                changed = True
        if changed:
            save_groups(groups_doc)
        today = time.strftime('%Y-%m-%d', time.gmtime())
        for (holder, role, scope), event in list(held_roles().items()):
            if role == 'expert' and scope == game_key:
                append_role_event({'user': event['user'], 'role': 'expert',
                                   'scope': scope, 'action': 'revoked', 'by': actor,
                                   'date': today, 'at': now_iso(),
                                   'reason': f'The game was deleted. {reason}'})
        log_deletion('game', game_key, game.get('title', game_key), actor, reason)
        ensure_member(actor)
        commit_push(f'Delete game {game_key}: by expert {actor}\n\n'
                    f'Reason: {reason}\n'
                    f'Runs deleted with it: {", ".join(deleted_runs) or "none"}\n'
                    f'Via: archivist')
    # each deleted run's announce topic closes with the reason; Discord
    # hears once for the whole act; all best-effort, after the write
    for orphan_run_id, orphan_topic in orphan_topics:
        close_announce_topic(orphan_topic,
                             f'This run was deleted from the archive with its game '
                             f'{game_key}, by expert {actor}. Reason, from the public '
                             f'log: {reason}')
    notify_discord(f'\U0001f5d1\ufe0f Game {game.get("title", game_key)} ({game_key}) was '
                   f'deleted by expert **{member_md(actor)}** with '
                   f'{len(deleted_runs)} run(s): {reason}')
    return jsonify({'ok': True, 'deleted': game_key, 'runs_deleted': deleted_runs})

@app.post('/api/group/delete')
def group_delete():
    """An expert deletes a group outright; its games become ungrouped and the
    derived Unclassified group picks them up at the next build.

    Who: an expert whose scope covers the group, or an editor
    Reads: form fields group (key), reason, dry_run
    Answers: {ok, deleted, released}
    """
    deletion_form = request.form
    dry_run = deletion_form.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        auth_error = auth_precheck(deletion_form)
        if auth_error:
            return auth_error
        actor, reason, error = _deletion_gate(deletion_form)
        if error:
            return error
        group_key = (deletion_form.get('group') or '').strip().lower()
        groups_doc = load_groups()
        group = next((g for g in groups_doc['groups'] if g['key'] == group_key), None)
        if not group:
            return fail(f'no group with the key {group_key!r}', 404)
        if not covers_group(actor, group) and not is_editor(actor):
            return fail(f'{actor} holds no scope covering the {group["title"]} group', 403)
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_delete': group_key,
                            'released': group.get('games', [])})
        checkout_branch()
        groups_doc = load_groups()
        group = next((g for g in groups_doc['groups'] if g['key'] == group_key), None)
        if not group:
            return fail(f'no group with the key {group_key!r}', 404)
        released = group.get('games', [])
        groups_doc['groups'] = [g for g in groups_doc['groups'] if g['key'] != group_key]
        save_groups(groups_doc)
        today = time.strftime('%Y-%m-%d', time.gmtime())
        for (holder, role, scope), event in list(held_roles().items()):
            if role == 'expert' and scope == f'group:{group_key}':
                append_role_event({'user': event['user'], 'role': 'expert',
                                   'scope': scope, 'action': 'revoked', 'by': actor,
                                   'date': today, 'at': now_iso(),
                                   'reason': f'The group was deleted. {reason}'})
        log_deletion('group', group_key, group.get('title', group_key), actor, reason)
        ensure_member(actor)
        commit_push(f'Delete group {group_key}: by expert {actor}\n\n'
                    f'Reason: {reason}\nReleased: {", ".join(released) or "no games"}\n'
                    f'Via: archivist')
    return jsonify({'ok': True, 'deleted': group_key, 'released': released})

@app.post('/api/member/delete')
def member_delete():
    """The Steering Committee deletes a member record: spam accounts, tests.

    Refused while the member holds any role or authored any run: those are
    real entanglements with the community and each has its own procedure.
    Their name in other runs' credits is text and stays.

    Who: a Steering Committee member; a sitting Committee member is the
        Founder's alone to delete, and the Founder is nobody's
    Reads: form fields target (username), reason, dry_run
    Answers: {ok, deleted, roles_revoked}; 409 while the member authored runs
    """
    deletion_form = request.form
    dry_run = deletion_form.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        auth_error = auth_precheck(deletion_form)
        if auth_error:
            return auth_error
        actor, reason, error = _deletion_gate(deletion_form)
        if error:
            return error
        if not is_committee(actor):
            return fail('only the Steering Committee deletes a member', 403)
        target = (deletion_form.get('target') or '').strip()
        if not re.fullmatch(r'[A-Za-z0-9. _-]{2,40}', target):
            return fail('target must be the member being deleted')
        author_file = ARCHIVE / 'authors' / f'{selfimport.slugify(target)}.json'
        if not author_file.exists():
            return fail(f'no member record for {target}', 404)
        if target.lower() == actor.lower():
            return fail('deleting yourself is not a decision to make alone; ask '
                        'another Committee member')
        target_roles = [(role, scope) for (holder, role, scope) in held_roles()
                        if holder == target.lower()]
        # The Committee does not eat itself: a sitting Committee member is the
        # Founder's alone to delete, and the Founder is nobody's (2.2.2).
        if any(role == 'founder' for role, s in target_roles):
            return fail('the Founder cannot be deleted (Governance 2.2.2)', 403)
        if any(role == 'committee' for role, s in target_roles) and not is_founder(actor):
            return fail('a sitting Committee member is deleted by the Founder alone, '
                        'never by fellow Committee members', 403)
        target_lower = target.lower()
        authored_runs = [run_json_path for run_json_path in ARCHIVE.glob('games/*/*/runs/*/run.json')
                    if any(a.get('user', '').lower() == target_lower
                           for a in json.loads(run_json_path.read_text()).get('authors', []))]
        if authored_runs:
            return fail(f'{target} authored {len(authored_runs)} archived run(s); a member '
                        f'with works here is removed through withdrawal or erasure, '
                        f'never a record deletion', 409)
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_delete': target})
        checkout_branch()
        if not author_file.exists():
            return fail(f'no member record for {target}', 404)
        # the deletion revokes whatever they held, in the same commit: a
        # deleted member on the roster would be a ghost with authority
        today = time.strftime('%Y-%m-%d', time.gmtime())
        for role, scope in target_roles:
            event = {'user': target, 'role': role, 'action': 'revoked', 'by': actor,
                  'date': today, 'at': now_iso(), 'reason': f'Member deleted. {reason}'}
            if scope:
                event['scope'] = scope
            append_role_event(event)
        author_file.unlink()
        log_deletion('member', target, target, actor, reason)
        commit_push(f'Delete member {target}: by {actor}\n\n'
                    f'Reason: {reason}\nVia: archivist')
    for role, scope in target_roles:
        publish_group(role, target, add=False)
    return jsonify({'ok': True, 'deleted': target,
                    'roles_revoked': [role for role, s in target_roles]})

@app.post('/api/game/create')
def game_create():
    """Create a game with no run in it yet, inside a group you speak for.

    Submitting a movie has always been able to create a game; this is the other
    way round, for an expert filling out a group before anybody has archived a
    run of it. Real on arrival, like every creation here; a mistaken one is
    deleted on the record.

    Who: any member; placing the game into a group needs scope over the
        group, or the editor role
    Reads: form fields system, title, group (optional key), released,
        unofficial, discord, website, rta (optional properties, #44), cat_label,
        cat_rule, cat_key, metrics (JSON array), dry_run
    Answers: {ok, game, category, group, note}; 409 when the game exists
    """
    game_form = request.form
    dry_run = game_form.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        auth_error = auth_precheck(game_form)
        if auth_error:
            return auth_error
        expert, error = request_identity(game_form, 'user')
        if error:
            return error
        paced = pace_gate(game_form, expert, 'create')
        if paced:
            return paced
        system = (game_form.get('system') or '').strip()
        title = (game_form.get('title') or '').strip()[:120]
        group_key = (game_form.get('group') or '').strip().lower()
        if system not in json.loads((ARCHIVE / 'systems.json').read_text()):
            return fail(f'unknown system {system!r}: systems are curated')
        if not title:
            return fail('a game needs a title')
        slug = slugify(title)
        if not slug:
            return fail('that title yields an empty slug')
        game_key = f'{system}/{slug}'
        if (ARCHIVE / 'games' / system / slug / 'game.json').exists():
            return fail(f'{game_key} already exists', 409)
        groups_doc = load_groups()
        group = next((g for g in groups_doc['groups'] if g['key'] == group_key), None) if group_key else None
        if group_key and not group:
            return fail(f'no group with the key {group_key!r}', 404)
        # Authority: over the group you are filling out, or over the system the
        # game lands in. A group expert creating into their own group is the
        # case this exists for, and the game is not in the group yet, so the
        # group is what has to be checked rather than the game.
        # creation is everybody's (good faith; experts moderate). Placing
        # the game into a group is curation and still needs scope over it.
        if group and not covers_group(expert, group) and not is_editor(expert):
            return fail(f'{expert} holds no scope covering the '
                        f'{group["title"]} group', 403)
        today = time.strftime('%Y-%m-%d', time.gmtime())
        properties = {}
        for property_field in GAME_PROPERTY_FIELDS:
            property_value, property_error = parse_game_property(
                property_field, game_form.get(property_field))
            if property_error:
                return fail(property_error)
            if property_value is not None:
                properties[property_field] = property_value
        game = {'title': title, 'system': system, 'createdBy': expert, **properties,
                'createdAt': today}
        cat_label = (game_form.get('cat_label') or 'fastest completion').strip()[:80]
        cat_rule = (game_form.get('cat_rule')
                    or 'Complete the game as fast as possible.').strip()[:500]
        cat_key = slugify(game_form.get('cat_key') or cat_label)
        metric_defs, metric_error = parse_metric_defs(game_form.get('metrics'))
        if metric_error:
            return fail(metric_error)
        if not cat_key or cat_key == 'unclassified':
            return fail('bad first-category key')
        first_category = {'key': cat_key, 'label': cat_label, 'rule': cat_rule,
                     **({'metrics': metric_defs} if metric_defs else {})}
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_create': game_key,
                            'game': game, 'category': first_category,
                            'group': group_key or None})
        checkout_branch()
        game_dir = ARCHIVE / 'games' / system / slug
        if (game_dir / 'game.json').exists():
            return fail(f'{game_key} already exists', 409)
        game_dir.mkdir(parents=True, exist_ok=True)
        (game_dir / 'game.json').write_text(json.dumps(game, indent=1) + '\n')
        (game_dir / 'categories.json').write_text(json.dumps(
            {'dimensions': [{'key': 'goal', 'name': 'Category',
                             'options': [first_category]}]}, indent=1) + '\n')
        (game_dir / 'runs').mkdir(exist_ok=True)
        if group_key:
            groups_doc = load_groups()
            group = next((g for g in groups_doc['groups'] if g['key'] == group_key), None)
            group['games'] = sorted(set(group['games']) | {game_key})
            save_groups(groups_doc)
        ensure_member(expert)
        ensure_game_topic(*game_key.split('/'), title)
        commit_push(f'Create {game_key}: by {expert}\n\n'
                    f'Title: {title}\nFirst category: {cat_key}\n'
                    f'Group: {group_key or "none"}\nVia: archivist')
        notify_discord(f'\U0001f5c2\ufe0f **{member_md(expert)}** created the '
                       f'[game](<{SITE_URL}/games/{game_key}/>) {title}'
                       + (f' in the {group_key} group' if group_key else ''),
                       wait_for=f'{SITE_URL}/games/{game_key}/')
    return jsonify({'ok': True, 'game': game_key, 'category': cat_key,
                    'group': group_key or None,
                    'note': 'It has no runs yet, so it shows as an empty game until '
                            'somebody archives one.'})
@app.post('/api/group/create')
def group_create():
    """Create a group, real on arrival, exactly like a
    game: naming a family of games is a curatorial claim, not a fact.

    You may only gather games you already have authority over, which is the same
    rule appointment follows. An empty group is site scope only, since there is
    nothing yet to derive authority from.

    Who: an expert whose scope covers every listed game (site scope for an
        empty group), or an editor
    Reads: form fields group (key), title, games (space or comma separated
        system/slug keys), dry_run
    Answers: {ok, group, games, note}; 409 when the key or a game is taken
    """
    group_form = request.form
    dry_run = group_form.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        auth_error = auth_precheck(group_form)
        if auth_error:
            return auth_error
        expert, error = request_identity(group_form, 'expert')
        if error:
            return error
        paced = pace_gate(group_form, expert, 'create')
        if paced:
            return paced
        key = (group_form.get('group') or '').strip().lower()
        title = (group_form.get('title') or '').strip()
        games = [g.strip() for g in (group_form.get('games') or '').replace(',', ' ').split() if g.strip()]
        if not re.fullmatch(r'[a-z0-9]+(-[a-z0-9]+)*', key or ''):
            return fail('the group key must be lowercase words joined by hyphens')
        if key in ('uncategorized', 'unclassified'):
            return fail(f'{key} is reserved for the derived group that gathers '
                        f'every game no group has claimed')
        if not (1 <= len(title) <= 80):
            return fail('a group needs a title')
        groups_doc = load_groups()
        if any(g['key'] == key for g in groups_doc['groups']):
            return fail(f'a group with the key {key!r} already exists', 409)
        for game_key in games:
            if not re.fullmatch(r'[a-z0-9-]+/[a-z0-9-]+', game_key) or \
                    not (ARCHIVE / 'games' / game_key / 'game.json').is_file():
                return fail(f'no such game: {game_key!r}', 404)
            other = next((x for x in groups_doc['groups'] if game_key in x.get('games', [])), None)
            if other:
                return fail(f'{game_key} already belongs to the {other["title"]} group; a game '
                            f'belongs to one', 409)
        prospective_group = {'key': key, 'games': games}
        if not covers_group(expert, prospective_group) and not is_editor(expert):
            return fail(f'{expert} holds no scope covering '
                        f'{"every game listed" if games else "an empty group"}; '
                        f'a group gathers games you already speak for', 403)
        today = time.strftime('%Y-%m-%d', time.gmtime())
        # real on arrival: ratification is gone as a mechanism
        entry = {'key': key, 'title': title, 'games': games,
                 'createdBy': expert, 'createdAt': today}
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_create': entry})
        checkout_branch()
        groups_doc = load_groups()
        if any(g['key'] == key for g in groups_doc['groups']):
            return fail(f'a group with the key {key!r} already exists', 409)
        groups_doc['groups'].append(entry)
        save_groups(groups_doc)
        ensure_member(expert)
        commit_push(f'Group {key}: created by {expert}\n\n'
                    f'Title: {title}\nGames: {", ".join(games) or "none yet"}\n'
                    f'Via: archivist')
        notify_discord(f'\U0001f5c2\ufe0f **{member_md(expert)}** created the '
                       f'[group](<{SITE_URL}/groups/{key}/>) {title}, '
                       f'{len(games) or "no"} game{"s" if len(games) != 1 else ""} in it',
                       wait_for=f'{SITE_URL}/groups/{key}/')
    return jsonify({'ok': True, 'group': key, 'games': games,
                    'note': 'The group exists. A mistaken one is deleted by an '
                            'expert, on the record.'})

@app.post('/api/group/edit')
def group_edit():
    """Add, move in or remove games, or retitle. Adding needs authority over
    the game as well as the group: a group is not a way to reach games you do
    not cover. `move` differs from `add` in one way: it pulls the game out of
    whatever group holds it, because a game belongs to one group.

    Who: an expert whose scope covers the group, or an editor; adding or
        moving a game in needs scope over that game too
    Reads: form fields group, add, move, remove (game key lists), title, dry_run
    Answers: {ok, group, games, title}; dry_run: {ok, dry_run, would_hold, title}
    """
    group_form = request.form
    dry_run = group_form.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        auth_error = auth_precheck(group_form)
        if auth_error:
            return auth_error
        expert, error = request_identity(group_form, 'expert')
        if error:
            return error
        key = (group_form.get('group') or '').strip().lower()
        add = [g.strip() for g in (group_form.get('add') or '').replace(',', ' ').split() if g.strip()]
        move = [g.strip() for g in (group_form.get('move') or '').replace(',', ' ').split() if g.strip()]
        drop = [g.strip() for g in (group_form.get('remove') or '').replace(',', ' ').split() if g.strip()]
        title = (group_form.get('title') or '').strip()
        if not (add or move or drop or title):
            return fail('nothing to change')
        groups_doc = load_groups()
        group = next((g for g in groups_doc['groups'] if g['key'] == key), None)
        if not group:
            return fail(f'no group with the key {key!r}', 404)
        if not covers_group(expert, group) and not is_editor(expert):
            return fail(f'{expert} holds no scope covering the {group["title"]} group', 403)
        for game_key in add:
            if not (ARCHIVE / 'games' / game_key / 'game.json').is_file():
                return fail(f'no such game: {game_key!r}', 404)
            if not expert_covers(expert, game_key) and not is_editor(expert):
                return fail(f'{expert} holds no scope covering {game_key}; a group cannot '
                            f'reach a game its curator may not speak for', 403)
            if game_key in group['games']:
                return fail(f'{game_key} is already in this group', 409)
            other = next((x for x in groups_doc['groups'] if x['key'] != key and game_key in x.get('games', [])),
                         None)
            if other:
                return fail(f'{game_key} already belongs to the {other["title"]} group; a game '
                            f'belongs to one (move it instead)', 409)
        for game_key in move:
            if not (ARCHIVE / 'games' / game_key / 'game.json').is_file():
                return fail(f'no such game: {game_key!r}', 404)
            if not expert_covers(expert, game_key) and not is_editor(expert):
                return fail(f'{expert} holds no scope covering {game_key}; a group cannot '
                            f'reach a game its curator may not speak for', 403)
            if game_key in group['games']:
                return fail(f'{game_key} is already in this group', 409)
        for game_key in drop:
            if game_key not in group['games']:
                return fail(f'{game_key} is not in this group', 404)
        if title and not (1 <= len(title) <= 80):
            return fail('a title must be under 80 characters')
        after = sorted((set(group['games']) | set(add) | set(move)) - set(drop))
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_hold': after,
                            'title': title or group['title']})
        checkout_branch()
        groups_doc = load_groups()
        group = next((g for g in groups_doc['groups'] if g['key'] == key), None)
        if not group:
            return fail(f'no group with the key {key!r}', 404)
        before_games = list(group['games'])
        before_title = group['title']
        # a move pulls the game out of whatever group held it, first
        moved_from = {}
        for other in groups_doc['groups']:
            if other['key'] == key:
                continue
            hits = [game_key for game_key in move if game_key in other.get('games', [])]
            if hits:
                other['games'] = [game_key for game_key in other['games'] if game_key not in hits]
                for game_key in hits:
                    moved_from[game_key] = other['key']
        group['games'] = sorted((set(group['games']) | set(add) | set(move)) - set(drop))
        if title:
            group['title'] = title
        save_groups(groups_doc)
        change_summary = ', '.join(filter(None, [
            f'+{" ".join(add)}' if add else '',
            ' '.join(f'{game_key} moved in from {moved_from[game_key]}' if game_key in moved_from
                     else f'{game_key} moved in' for game_key in move) if move else '',
            f'-{" ".join(drop)}' if drop else '',
            f'retitled {title!r}' if title else '']))
        log_edit('group', key, 'games' if (add or move or drop) else 'title',
                 ', '.join(before_games) if (add or move or drop) else before_title,
                 ', '.join(group['games']) if (add or move or drop) else group['title'],
                 expert, f'Changed from the group form: {change_summary}')
        ensure_member(expert)
        commit_push(f'Group {key}: {change_summary}\n\nBy: {expert}\nVia: archivist')
        notify_discord(f'\U0001f5c2\ufe0f **{member_md(expert)}** changed the '
                       f'[group](<{SITE_URL}/groups/{key}/>) {group["title"]}: {change_summary}',
                       wait_for=f'{SITE_URL}/groups/{key}/')
    return jsonify({'ok': True, 'group': key, 'games': group['games'], 'title': group['title']})


REPORT_KINDS = {'missing-content-warnings', 'spam-malicious', 'miscredited',
                'licensing', 'other'}

@app.post('/api/report')
def report():
    """Report a run — public, uniquely identified, addressed by the covering
    expert, permanently listed in the site log.

    Who: any member
    Reads: form fields run, kind (one of REPORT_KINDS), details, dry_run
    Answers: {ok, run, report: 'R<id>', note}; dry_run: {ok, dry_run, would_file}
    """
    report_form = request.form
    dry_run = report_form.get('dry_run') in ('1', 'true', 'yes')
    with lock:
        auth_error = auth_precheck(report_form)
        if auth_error:
            return auth_error
        if not dry_run:
            checkout_branch()
        user, error = request_identity(report_form)
        if error:
            return error
        paced = pace_gate(report_form, user, 'report')
        if paced:
            return paced
        run_id = (report_form.get('run') or '').strip()
        run_dir = find_run(run_id) if re.fullmatch(r'M[0-9]+', run_id) else None
        if not run_dir:
            return fail(f'unknown run {run_id}', 404)
        kind = (report_form.get('kind') or '').strip()
        if kind not in REPORT_KINDS:
            return fail(f'kind must be one of: {", ".join(sorted(REPORT_KINDS))}')
        details = (report_form.get('details') or '').strip()
        if len(details) > ACT_NOTES_MAX:
            return fail(f'details exceed {ACT_NOTES_MAX} characters')
        if kind == 'other' and not details:
            return fail("an 'other' report needs details")
        run = json.loads((run_dir / 'run.json').read_text())
        report_entry = {'id': next_report_id(), 'by': user,
               'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(),
               'kind': kind, 'status': 'open'}
        if details:
            report_entry['details'] = details
        run.setdefault('reports', []).append(report_entry)
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_file': report_entry})
        (run_dir / 'run.json').write_text(json.dumps(
            {k: v for k, v in run.items() if not k.startswith('_')}, indent=1))
        ensure_member(user)
        commit_push(f'Report R{report_entry["id"]} on {run_id}: {kind} by {user}\n\nVia: archivist')
    return jsonify({'ok': True, 'run': run_id, 'report': f'R{report_entry["id"]}',
                    'note': 'Filed in the open. The covering expert will address it; '
                            'it is permanently listed in the site log.'})

@app.post('/api/report/resolve')
def report_resolve():
    """The covering expert resolves or dismisses a report — logged in the open.

    Who: an expert covering the run's game
    Reads: form fields run, report (id number), outcome (resolved|dismissed),
        resolution, dry_run
    Answers: {ok, report, status}
    """
    resolution_form = request.form
    dry_run = resolution_form.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        auth_error = auth_precheck(resolution_form)
        if auth_error:
            return auth_error
        if not dry_run:
            checkout_branch()
        expert, error = request_identity(resolution_form, 'expert')
        if error:
            return error
        run_id = (resolution_form.get('run') or '').strip()
        run_dir = find_run(run_id) if re.fullmatch(r'M[0-9]+', run_id) else None
        if not run_dir:
            return fail(f'unknown run {run_id}', 404)
        run = json.loads((run_dir / 'run.json').read_text())
        if not expert_covers(expert, run['game']):
            return fail(f'{expert!r} is not an expert covering {run["game"]}', 403)
        try:
            report_id = int(resolution_form.get('report') or '')
        except ValueError:
            return fail('report must be a report id number')
        report_entry = next((x for x in run.get('reports', []) if x['id'] == report_id), None)
        if not report_entry:
            return fail(f'no report R{report_id} on this run', 404)
        if report_entry['status'] != 'open':
            return fail(f'report R{report_id} is already {report_entry["status"]}')
        outcome = (resolution_form.get('outcome') or '').strip()
        if outcome not in ('resolved', 'dismissed'):
            return fail('outcome must be resolved or dismissed')
        resolution = (resolution_form.get('resolution') or '').strip()
        if not resolution:
            return fail('a public resolution text is required; it is logged in the open')
        if len(resolution) > ACT_NOTES_MAX:
            return fail(f'resolution exceeds {ACT_NOTES_MAX} characters')
        report_entry['status'] = outcome
        report_entry['resolvedBy'] = expert
        report_entry['resolvedAt'] = time.strftime('%Y-%m-%d', time.gmtime())
        report_entry['resolution'] = resolution
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_resolve': report_entry})
        (run_dir / 'run.json').write_text(json.dumps(
            {k: v for k, v in run.items() if not k.startswith('_')}, indent=1))
        ensure_member(expert)
        commit_push(f'Report R{report_id} {outcome} on {run_id}: by expert {expert}\n\n'
                    f'Resolution: {resolution}\nVia: archivist')
    return jsonify({'ok': True, 'report': f'R{report_id}', 'status': outcome})

@app.post('/api/edit')
def edit_run():
    """The run's authors revise their own work freely; a covering expert may
    correct the same details, one run at a time, always with a public reason
    (the same logged, git-reversible trail as /api/expert/edit; the author
    list and supplementary uploads stay the authors' alone). Git history is
    the audit trail — nothing is erased.

    Who: one of the run's authors, or an expert covering its game (who must
        give a reason)
    Reads: form fields run, reason, authors (authors only), notes, emulator,
        metric_<key>, completed, goalDescription, encode, time (video-only
        runs), content_warnings (repeatable, with content_warnings_set as
        the marker that the field was sent), file_name / file_sha1 rows
        (with files_set as the marker), time (whenever the category ranks by
        time; only a real change is recorded), dry_run; files attachments
        (authors only)
    Answers: {ok, run, changed}; dry_run: {ok, dry_run, would_change}
    """
    edit_form = request.form
    dry_run = edit_form.get('dry_run') in ('1', 'true', 'yes')
    with lock:
        auth_error = auth_precheck(edit_form)
        if auth_error:
            return auth_error
        if not dry_run:
            checkout_branch()
        user, error = request_identity(edit_form)
        if error:
            return error
        paced = pace_gate(edit_form, user, 'edit')
        if paced:
            return paced
        run_id = (edit_form.get('run') or '').strip()
        run_dir = find_run(run_id) if re.fullmatch(r'M[0-9]+', run_id) else None
        if not run_dir:
            return fail(f'unknown run {run_id}', 404)
        run = json.loads((run_dir / 'run.json').read_text())
        is_author = current_name(user).lower() in run_authors_now(run)
        if not is_author and not expert_covers(user, run['game']):
            return fail("only the run's authors or a covering expert may edit it", 403)
        reason = (edit_form.get('reason') or '').strip()
        if not is_author and not (8 <= len(reason) <= 500):
            return fail('an expert edit states its public reason (8 to 500 '
                        'characters), published in the edit log')
        changed = []
        befores = {'emulator': run.get('contract', {}).get('emulator', '')}
        if 'authors' in edit_form:
            if not is_author:
                return fail("an author list is never an expert's edit: who made "
                            "a thing is moderation's question", 403)
            new_authors = [a.strip() for a in (edit_form.get('authors') or '').split(',') if a.strip()]
            if not new_authors:
                return fail('a run needs at least one author')
            acted = ({current_name(x['user']).lower() for x in run.get('reproductions', [])}
                     | {current_name(x['user']).lower() for x in run.get('verifications', [])}
                     | {current_name(l['user']).lower() for l in run.get('likes', [])})
            clash = [a for a in new_authors if current_name(a).lower() in acted]
            if clash:
                return fail(f'cannot credit {", ".join(clash)} as author: they already '
                            f'reproduced, verified, or liked this run (authors may not '
                            f'act on their own runs)')
            if [a['user'] for a in run['authors']] != new_authors:
                run['authors'] = [{'user': a} for a in new_authors]
                changed.append('authors')
                if not dry_run:
                    ensure_member(user)
        # Only what actually differs is a change (issue #38): the form sends
        # every field every time, and a browser textarea submits CRLF, which
        # used to rewrite an untouched 96-line notes file on every edit.
        if 'notes' in edit_form:
            notes = (edit_form.get('notes') or '').replace('\r\n', '\n').replace('\r', '\n')
            if len(notes.encode()) > 1024 * 1024:
                return fail('notes exceed 1 MB')
            try:
                old_notes = (run_dir / 'notes.md').read_text()
            except OSError:
                old_notes = ''
            # the form edits the author's part; the archive's own header (an
            # import's disclaimer) stays on top, untouched, whatever is sent
            notes_header, old_body = split_notes_header(old_notes)
            sent = notes.strip() + '\n' if notes.strip() else ''
            if notes_header and sent.startswith(notes_header):
                sent = sent[len(notes_header):]
            notes = notes_header + sent
            if old_body != sent:
                changed.append('notes')
        if 'emulator' in edit_form:
            new_emulator = (edit_form.get('emulator') or '').strip()[:120]
            if new_emulator != run.get('contract', {}).get('emulator', ''):
                run.setdefault('contract', {})['emulator'] = new_emulator
                changed.append('emulator')
        if 'files_set' in edit_form:
            # the files list, whole: the form always sends every row, so an
            # emptied list clears it. A legacy single `rom` is replaced by
            # the list the moment the author or an expert revises it
            new_files, files_error = parse_file_rows(edit_form)
            if files_error:
                return fail(files_error)
            old_files = run.get('contract', {}).get('files')
            if old_files is None and run.get('contract', {}).get('rom'):
                old_files = [run['contract']['rom']]
            if (old_files or []) != new_files:
                befores['files'] = '; '.join(f"{f.get('name', '')} {f.get('sha1', '')}".strip()
                                             for f in (old_files or []))
                contract = run.setdefault('contract', {})
                contract.pop('rom', None)
                if new_files:
                    contract['files'] = new_files
                else:
                    contract.pop('files', None)
                changed.append('files')
        # stated metric values: only the keys this run's category defines;
        # an empty field leaves the value untouched, an explicit 0 returns
        # it to "not yet stated" (which ranks last)
        system, slug = run['game'].split('/')
        game, categories = load_game(system, slug)
        goal = (run.get('category') or {}).get('goal')
        option = next((o for dimension in (categories or {}).get('dimensions', [])
                     for o in dimension['options'] if o['key'] == goal), None)
        metric_keys = {mm['key'] for mm in (option or {}).get('metrics', [])
                  if mm['key'] != 'time'}
        for form_key in list(edit_form.keys()):
            if not form_key.startswith('metric_'):
                continue
            metric_key = form_key[len('metric_'):]
            raw = (edit_form.get(form_key) or '').strip()
            if raw == '':
                continue
            if metric_key not in metric_keys:
                return fail(f'this category states no metric {metric_key!r}')
            try:
                metric_value = float(raw)
            except ValueError:
                return fail(f'{metric_key} must be a number (seconds for times)')
            if metric_value < 0:
                return fail(f'{metric_key} cannot be negative')
            if metric_value == (run.get('metrics') or {}).get(metric_key, 0):
                continue
            befores[f'metric:{metric_key}'] = str((run.get('metrics') or {}).get(metric_key, 0))
            run.setdefault('metrics', {})[metric_key] = metric_value
            changed.append(f'metric:{metric_key}')
        if 'completed' in edit_form:
            completed_value = (edit_form.get('completed') or '').strip()
            if completed_value:
                if not re.fullmatch(r'(19[89]\d|20\d{2})-(0[1-9]|1[0-2])'
                                    r'-(0[1-9]|[12]\d|3[01])', completed_value):
                    return fail('completed must be a date like 2021-10-26')
                if completed_value > time.strftime('%Y-%m-%d', time.gmtime()):
                    return fail('completed cannot be in the future')
            if completed_value != run.get('completed', ''):
                befores['completed'] = run.get('completed', '')
                if completed_value:
                    run['completed'] = completed_value
                else:
                    run.pop('completed', None)
                changed.append('completed')
        if 'content_warnings_set' in edit_form:
            # the content disclosures (#49): the form always sends the marker,
            # so no box ticked means "none" and clears them
            new_warnings = sorted(set(edit_form.getlist('content_warnings')))
            if any(w not in CW_ALLOWED for w in new_warnings):
                return fail('unknown content warning')
            old_warnings = sorted(run.get('contentWarnings', []))
            if new_warnings != old_warnings:
                befores['contentWarnings'] = ', '.join(old_warnings)
                if new_warnings:
                    run['contentWarnings'] = new_warnings
                else:
                    run.pop('contentWarnings', None)
                changed.append('contentWarnings')
        if 'related' in edit_form:
            # the run page's "You may also like" picks (up to 8 run ids,
            # shown before every computed suggestion); presentation only,
            # so nothing is voided
            raw_ids = [t for t in re.split(r'[,\s]+', edit_form.get('related') or '') if t]
            related_ids = []
            for rid_ in raw_ids:
                if not re.fullmatch(r'M[0-9]+', rid_):
                    return fail(f'{rid_!r} is not a run id like M100001')
                if rid_ == run_id:
                    return fail('a run cannot recommend itself')
                if not find_run(rid_):
                    return fail(f'unknown run {rid_}', 404)
                if rid_ not in related_ids:
                    related_ids.append(rid_)
            if len(related_ids) > 8:
                return fail('at most 8 designated runs')
            if related_ids != run.get('related', []):
                befores['related'] = ', '.join(run.get('related', []))
                if related_ids:
                    run['related'] = related_ids
                else:
                    run.pop('related', None)
                changed.append('related')
        if 'goalDescription' in edit_form:
            goal_description = (edit_form.get('goalDescription') or '').strip()
            if len(goal_description) > 500:
                return fail('a goal description fits in 500 characters')
            if is_uncl_run(run) and not goal_description:
                return fail('an Unclassified run states its own goal; it cannot lose '
                            'its description')
            if goal_description != run.get('goalDescription', ''):
                befores['goalDescription'] = run.get('goalDescription', '')
                if goal_description:
                    run['goalDescription'] = goal_description
                else:
                    run.pop('goalDescription', None)
                changed.append('goalDescription')
        if 'encode' in edit_form:
            encode_url = (edit_form.get('encode') or '').strip()
            if encode_url:
                encode_provider = providers.resolve(encode_url)
                if not encode_provider:
                    return fail('encode must be a watchable URL on a platform we accept')
                if encode_url != (run.get('encodes') or [{}])[0].get('url', ''):
                    befores['encode'] = (run.get('encodes') or [{}])[0].get('url', '')
                    run['encodes'] = [{'kind': encode_provider['kind'], 'url': encode_url}]
                    changed.append('encode')
        # the run's time is the one its authors state, whatever the movie
        # holds; a score category has no time to state, so one left empty
        # there is no error (issue #62)
        option_metrics = (option or {}).get('metrics')
        option_wants_time = option_metrics is None or any(mm['key'] == 'time' for mm in option_metrics)
        stated_time = (edit_form.get('time') or '').strip()
        # a legacy run that never stated a duration still ranks by its
        # frames; an empty time on one means "keep deriving", never an error
        legacy_frames = run.get('duration') is None and (run.get('movie') or {}).get('frames')
        if 'time' in edit_form and option_wants_time and not (stated_time == '' and legacy_frames):
            time_match = re.fullmatch(r'(?:(\d{1,3}):)?(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?', stated_time)
            if not time_match:
                return fail('this category ranks by time, so the run states it as [h:]mm:ss or [h:]mm:ss.mmm')
            hours, minutes, seconds, fraction = time_match.groups()
            duration = (int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)
                   + (int(fraction.ljust(3, "0")) / 1000 if fraction else 0.0))
            if duration <= 0:
                return fail('a run that takes no time at all is not a run')
            # only a real change is a change: the form sends the record's own
            # value back on every save, and that must never void anything.
            # A legacy run's record is its frames-derived time: the form
            # prefills that, so getting it back (to the millisecond the form
            # rounds to) means "keep deriving", not a newly stated duration
            derived = None
            if legacy_frames:
                movie_fps = (run.get('movie') or {}).get('fps')
                if not movie_fps:
                    try:
                        movie_fps = json.loads((ARCHIVE / 'systems.json').read_text()).get(
                            run['game'].split('/')[0], {}).get('fps')
                    except (OSError, ValueError):
                        movie_fps = None
                if movie_fps:
                    derived = run['movie']['frames'] / movie_fps
            same_as_record = (abs(duration - run['duration']) < 0.0005 if run.get('duration') is not None
                              else derived is not None and abs(duration - derived) < 0.002)
            if not same_as_record:
                befores['duration'] = str(run.get('duration'))
                run['duration'] = duration
                changed.append('duration')
        new_attachments, attachment_error = read_attachments(run.get('attachments') or [])
        if attachment_error:
            return attachment_error
        if new_attachments:
            if not is_author:
                return fail("supplementary files are the authors' own uploads", 403)
            existing_files = {a['file'] for a in run.get('attachments') or []}
            clash = [name for name, _ in new_attachments if f'attachments/{name}' in existing_files]
            if clash:
                return fail(f'attachment {clash[0]!r} already exists on this run')
            run.setdefault('attachments', []).extend(
                {'file': f'attachments/{name}', 'role': 'supplementary'}
                for name, _ in new_attachments)
            changed.append('attachments')
        if not changed:
            return fail('nothing to change: every value sent already matches the '
                        'record (send notes, emulator, completed, goalDescription, '
                        'encode, attachments, or time)')
        # what this revision would void (the form asks before sending)
        would_void = []
        if any(c in SCORING_FIELDS or c.startswith('metric:') for c in changed):
            if live_acts(run)['verifications']: would_void.append('verifications')
        if any(c in REPRO_FIELDS for c in changed):
            if live_acts(run)['reproductions']: would_void.append('reproductions')
            if any(not c_.get('invalidated') for c_ in run.get('consoleVerifications', [])):
                would_void.append('consoleVerifications')
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_change': changed, 'would_void': would_void})
        voided = void_acts_for(run, changed, user)
        if 'notes' in changed:
            (run_dir / 'notes.md').write_text(notes)
        if new_attachments:
            (run_dir / 'attachments').mkdir(exist_ok=True)
            for name, data in new_attachments:
                (run_dir / 'attachments' / name).write_bytes(data)
        (run_dir / 'run.json').write_text(json.dumps(
            {k: v for k, v in run.items() if not k.startswith('_')}, indent=1))
        # every revision joins the same history the expert edits live in: the
        # author owes nobody a justification for editing their own work, but
        # the history prevails either way
        for field in changed:
            log_edit('run', run_id, field,
                     befores.get(field, '(previous value in git history)'),
                     ('(see the run)' if field in ('notes', 'authors') else
                      str((run.get('metrics') or {}).get(field.split(':', 1)[1], '')
                          if field.startswith('metric:') else
                          {'emulator': run.get('contract', {}).get('emulator', ''),
                           'completed': run.get('completed', ''),
                           'goalDescription': run.get('goalDescription', ''),
                           'encode': (run.get('encodes') or [{}])[0].get('url', ''),
                           'duration': run.get('duration', ''),
                           'contentWarnings': ', '.join(run.get('contentWarnings', [])),
                           'related': ', '.join(run.get('related', [])),
                           'files': '; '.join(f"{f.get('name', '')} {f.get('sha1', '')}".strip()
                                              for f in run.get('contract', {}).get('files', [])),
                           'attachments': ', '.join(name for name, _ in new_attachments),
                           }.get(field, ''))[:300]),
                     user, "The author's own revision." if is_author else reason)
        commit_push(f'Edit {run_id}: {", ".join(changed)} by '
                    f'{"author" if is_author else "expert"} {user}\n\nVia: archivist')
    return jsonify({'ok': True, 'run': run_id, 'changed': changed, 'voided': voided})

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

@app.get('/api/run/record')
def run_record():
    """The run's record as the archivist holds it right now, for the edit
    form: run.json, the notes text, the game's categories, and what the
    caller may do with it (author, covering expert, editor).

    Who: anybody may read; the permissions reflect the session
    Reads: query run (M-id)
    Answers: {ok, run, notes, game: {key, title, system}, categories,
        may: {author, expert, editor}}, Cache-Control: no-store; 404 unknown
    """
    run_id = (request.args.get('run') or '').strip()
    if not re.fullmatch(r'M[0-9]+', run_id):
        return fail('run must be a run id like M100001')
    refresh_archive()
    run_dir = find_run(run_id)
    if not run_dir:
        return fail(f'unknown run {run_id}', 404)
    run = json.loads((run_dir / 'run.json').read_text())
    notes_path = run_dir / 'notes.md'
    notes = notes_path.read_text() if notes_path.exists() else ''
    # the archive's own header (the import disclaimer, a leading quote block)
    # is not the author's notes; their own quotes further down are
    notes = split_notes_header(notes)[1]
    game_key = f'{run_dir.parent.parent.parent.name}/{run_dir.parent.parent.name}'
    game = json.loads((run_dir.parent.parent / 'game.json').read_text())
    categories = json.loads((run_dir.parent.parent / 'categories.json').read_text())
    who = session_user()
    low = (who or '').lower()
    may = {'author': bool(who) and current_name(who).lower() in run_authors_now(run),
           'expert': bool(who) and expert_covers(who, game_key),
           'editor': bool(who) and is_editor(who)}
    resp = jsonify({'ok': True, 'run': {k: v for k, v in run.items() if not k.startswith('_')},
                    'notes': notes, 'game': {'key': game_key, 'title': game.get('title'), 'system': game_key.split('/')[0]},
                    'categories': categories, 'may': may, 'user': who})
    resp.headers['Cache-Control'] = 'no-store'
    return resp


def _helper_gate():
    """The submit helpers (inspect, preview, encode check) parse files and
    fetch third-party pages: real work. The form that uses them only shows
    to a logged-in member, so anonymous calls are a script, not a person."""
    if session_user() or request.form.get('key') == SUBMIT_KEY or request.args.get('key') == SUBMIT_KEY:
        return None
    return fail('log in via the forum to use this', 403)

@app.post('/api/movie/inspect')
def movie_inspect():
    """Read a movie file the way a submission would, and say what it holds,
    before anything is submitted: the submit form's Import from... offers
    what was read; the author states the values either way. Nothing is
    stored.

    Who: anybody (the file is the caller's own)
    Reads: file movie; form field game (system/slug, for the frame rate when
        the movie names none)
    Answers: {ok, format, known, parsed, frames, fps, seconds, rerecords};
        400 for a missing, empty, oversized or unknown-format file
    """
    gate = _helper_gate()
    if gate:
        return gate
    movie_upload = request.files.get('movie')
    if not movie_upload or not movie_upload.filename:
        return fail('attach the movie file')
    ext = movie_upload.filename.rsplit('.', 1)[-1].lower()
    movie_bytes = movie_upload.read()
    if not movie_bytes:
        return fail('movie file is empty')
    if len(movie_bytes) > MOVIE_MAX:
        return fail('movie exceeds 16 MB')
    known = ext in MOVIE_EXTS
    parsed = movieparse.parse(movie_upload.filename, movie_bytes) if known else {'ok': False, 'error': f'.{ext} is not a format the archive can read'}
    fps = parsed.get('fps') if parsed.get('ok') else None
    frames = parsed.get('frames') if parsed.get('ok') else None
    # a movie that names no frame rate runs at its system's (form field game)
    game_key = (request.form.get('game') or '').strip()
    if frames and not fps and re.fullmatch(r'[a-z0-9-]+/[a-z0-9-]+', game_key):
        fps = json.loads((ARCHIVE / 'systems.json').read_text()).get(game_key.split('/')[0], {}).get('fps')
    resp = jsonify({'ok': True, 'format': ext, 'known': known, 'parsed': bool(parsed.get('ok')),
                    'frames': frames, 'fps': fps, 'rerecords': parsed.get('rerecords') if parsed.get('ok') else None,
                    'seconds': (frames / fps) if (frames and fps) else None,
                    'error': None if parsed.get('ok') else parsed.get('error')})
    resp.headers['Cache-Control'] = 'no-store'
    return resp

@app.post('/api/preview')
def preview_notes():
    """The submit preview, rendered by the very code that renders the
    published page (issue #30). Cross-references get a plain link here;
    the published page dresses them with the run's title and thumbnail.

    Who: anybody
    Reads: form field notes
    Answers: {ok, html}, Cache-Control: no-store
    """
    gate = _helper_gate()
    if gate:
        return gate
    import wikitext
    text = (request.form.get('notes') or '').replace('\r\n', '\n')
    if len(text.encode()) > 1024 * 1024:
        return fail('notes exceed 1 MB')
    def refs(markup):
        markup = re.sub(r'\[M([0-9]+)\]', r'<a class="runref" href="/runs/M\1/">M\1</a>', markup)
        markup = re.sub(r'\[user:([A-Za-z0-9. _-]{2,40})\]', r'<span class="au">\1</span>', markup)
        return markup
    resp = jsonify({'ok': True, 'html': wikitext.wiki_html(text, refs=refs)})
    resp.headers['Cache-Control'] = 'no-store'
    return resp

@app.post('/api/like')
def like():
    """Thumbs-up: everybody except the run's own authors, one per run.
    Works on any run, Imported included — it feeds player points and orders
    the Unclassified rankings.

    Who: any member except the run's authors
    Reads: form fields run, dry_run
    Answers: {ok, run, liked, likes}
    """
    like_form = request.form
    dry_run = like_form.get('dry_run') in ('1', 'true', 'yes')
    with lock:
        auth_error = auth_precheck(like_form)
        if auth_error:
            return auth_error
        if not dry_run:
            checkout_branch()
        user, error = request_identity(like_form)
        if error:
            return error
        paced = pace_gate(like_form, user, 'like')
        if paced:
            return paced
        run_id = (like_form.get('run') or '').strip()
        run_dir = find_run(run_id) if re.fullmatch(r'M[0-9]+', run_id) else None
        if not run_dir:
            return fail(f'unknown run {run_id}', 404)
        run = json.loads((run_dir / 'run.json').read_text())
        if run.get('withdrawn'):
            return fail(f'{run_id} has been withdrawn; no further acts apply')
        if current_name(user).lower() in run_authors_now(run):
            return fail('authors cannot like their own run')
        # The same star both ways: a second press takes the like back. Taking
        # it back deletes the entry outright, no tombstone and no log line, as
        # if it never happened: a like is a mood, not an act of authority, and
        # nobody owes the record an explanation for a change of heart. (The
        # git commit remains, as every commit does.)
        existing_like = [l for l in run.get('likes', []) if l['user'].lower() == user.lower()]
        if existing_like:
            run['likes'] = [l for l in run['likes'] if l['user'].lower() != user.lower()]
            liked = False
        else:
            run.setdefault('likes', []).append(
                {'user': user, 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso()})
            liked = True
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'liked': liked,
                            'likes': len(run['likes'])})
        (run_dir / 'run.json').write_text(json.dumps(
            {k: v for k, v in run.items() if not k.startswith('_')}, indent=1))
        ensure_member(user)
        commit_push(f'{"Like" if liked else "Unlike"} {run_id}: by {user}\n\nVia: archivist')
    return jsonify({'ok': True, 'run': run_id, 'liked': liked, 'likes': len(run['likes'])})

@app.post('/api/case/open')
def case_open():
    """A dispute opens a case — never auto-disqualifies. The run's verifiers
    (snapshotted now) are asked to reaffirm.

    Who: a member who is not one of the run's authors
    Reads: form fields run, reason, notes, dry_run
    Answers: {ok, run, case, verifiersAsked}; dry_run: {ok, dry_run, would_open}
    """
    case_form = request.form
    dry_run = case_form.get('dry_run') in ('1', 'true', 'yes')
    with lock:
        auth_error = auth_precheck(case_form)
        if auth_error:
            return auth_error
        if not dry_run:
            checkout_branch()
        act_error, run_dir, run, user = act_common(case_form)
        if act_error:
            return act_error
        reason = (case_form.get('reason') or '').strip()
        if not reason:
            return fail('a dispute needs a reason')
        if len(reason) > ACT_NOTES_MAX:
            return fail(f'reason exceeds {ACT_NOTES_MAX} characters')
        live_verifications = [a for a in run.get('verifications', []) if not a.get('invalidated')]
        if not live_verifications:
            return fail('this run has no live verifications to dispute')
        if any(c.get('status') == 'open' for c in run.get('cases', [])):
            return fail('this run already has an open case')
        case = {'id': max([c['id'] for c in run.get('cases', [])] + [0]) + 1,
                'openedBy': user,
                'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(),
                'reason': reason,
                'verifiers': [a['user'] for a in live_verifications],
                'reaffirmations': [],
                'status': 'open'}
        run.setdefault('cases', []).append(case)
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_open': case})
        (run_dir / 'run.json').write_text(json.dumps(
            {k: v for k, v in run.items() if not k.startswith('_')}, indent=1))
        ensure_member(user)
        commit_push(f'Case {case["id"]} opened on {run["id"]}: by {user}\n\nVia: archivist')
    return jsonify({'ok': True, 'run': run['id'], 'case': case['id'],
                    'verifiersAsked': case['verifiers']})

@app.post('/api/case/vote')
def case_vote():
    """A snapshotted verifier reaffirms (or withdraws) their verification.

    Who: a verifier snapshotted on the case when it opened
    Reads: form fields run, case (id number), reaffirm, notes, dry_run
    Answers: {ok, run, case, case_status, status}
    """
    vote_form = request.form
    dry_run = vote_form.get('dry_run') in ('1', 'true', 'yes')
    with lock:
        auth_error = auth_precheck(vote_form)
        if auth_error:
            return auth_error
        if not dry_run:
            checkout_branch()
        act_error, run_dir, run, user = act_common(vote_form)
        if act_error:
            return act_error
        try:
            case_id = int(vote_form.get('case') or '')
        except ValueError:
            return fail('case must be a case id number')
        case = next((c for c in run.get('cases', []) if c['id'] == case_id), None)
        if not case:
            return fail(f'no case {case_id} on this run', 404)
        if case['status'] != 'open':
            return fail(f'case {case_id} is already {case["status"]}')
        if user.lower() not in {u.lower() for u in case['verifiers']}:
            return fail('only the verifiers asked at case-open time may vote')
        if user.lower() in {v['user'].lower() for v in case.get('reaffirmations', [])}:
            return fail('you have already voted on this case')
        reaffirm = vote_form.get('reaffirm') in ('1', 'true', 'yes')
        today = time.strftime('%Y-%m-%d', time.gmtime())
        vote = {'user': user, 'date': today, 'at': now_iso(), 'reaffirm': reaffirm}
        if (vote_form.get('notes') or '').strip():
            vote['notes'] = vote_form.get('notes').strip()
        case.setdefault('reaffirmations', []).append(vote)
        if not reaffirm:
            for verification in run.get('verifications', []):
                if verification['user'].lower() == user.lower() and not verification.get('invalidated'):
                    verification['invalidated'] = {'by': user, 'date': today, 'at': now_iso(),
                                        'reason': f'withdrew during case {case_id}'}
        case['status'] = case_derived_status(case)
        if case['status'] != 'open':
            case['resolvedAt'] = today
        if case['status'] == 'upheld':
            # shortfall upholds the dispute: the run returns to pending —
            # every snapshot verification is invalidated; the run returns to
            # pending and OTHER members may verify it (each member has one
            # verification per run, spent whether or not it survived)
            snapshot = {u.lower() for u in case['verifiers']}
            for verification in run.get('verifications', []):
                if verification['user'].lower() in snapshot and not verification.get('invalidated'):
                    verification['invalidated'] = {'by': 'case', 'date': today, 'at': now_iso(),
                                        'reason': f'case {case_id} upheld'}
        sync_status(run)
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_vote': vote,
                            'case_status': case['status'], 'status': run['status']})
        (run_dir / 'run.json').write_text(json.dumps(
            {k: v for k, v in run.items() if not k.startswith('_')}, indent=1))
        ensure_member(user)
        commit_push(f'Case {case_id} vote on {run["id"]}: '
                    f'{"reaffirmed" if reaffirm else "withdrawn"} by {user}\n\nVia: archivist')
    return jsonify({'ok': True, 'run': run['id'], 'case': case_id,
                    'case_status': case['status'], 'status': run['status']})

@app.post('/api/expert/appoint')
def expert_appoint():
    """An expert appoints another, downward and in the open.

    Who: a Committee member, or an expert whose own scope covers the target
        scope (`key` plus `expert`)
    Reads: form fields user, scope, reason (8 to 500 chars), dry_run
    Answers: {ok, user, scope, by, forum, note}
    """
    appointment_form = request.form
    appointer, error = request_identity(appointment_form, 'expert')
    if error:
        return error
    user = (appointment_form.get('user') or '').strip()
    scope = (appointment_form.get('scope') or '').strip()
    reason = (appointment_form.get('reason') or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9. _-]{2,40}', user):
        return fail('user must be the forum account being appointed')
    if not scope_exists(scope):
        return fail(f'no such scope: {scope!r} names no game, system or group here')
    if len(reason) < 8:
        return fail('say why, publicly: an appointment is authority over other '
                    "people's work")
    if len(reason) > 500:
        return fail('reason must be under 500 characters')
    dry_run = appointment_form.get('dry_run') in ('1', 'true', 'yes')

    refresh_archive()
    with lock:
        if not dry_run:
            checkout_branch()
        roster = load_experts()
        appointer_scopes = [e for e in roster if e['user'].lower() == appointer.lower()]
        # Two doors in (Governance 2.5.3 and 2.5.6): any single Committee
        # member may appoint an expert at any scope, the whole site included,
        # and an expert appoints downward into scopes their own scope covers.
        # Equal scope still does not qualify on the expert door, or an expert
        # could clone themselves without anybody wider agreeing; a Committee
        # seat is the wider agreement.
        if not (is_committee(appointer)
                or any(scope_covers(e['scope'], scope) for e in appointer_scopes)):
            return fail(f'{appointer} holds no scope that covers {scope} and no '
                        f'Committee seat; appointment runs downward, or from the '
                        f'Committee (Governance 2.5.3)', 403)
        if any(e['user'].lower() == user.lower() and e['scope'] == scope
               for e in roster):
            return fail(f'{user} already holds {scope}', 409)
        held_already = already_covers(user, scope)
        if held_already:
            return fail(f'{user} already speaks for {scope} through their '
                        f'{held_already} scope; a narrower appointment would add '
                        f'nothing', 409)
        if forum_account_exists(user) is False:
            return fail(f'no forum account named {user}; they need one before they '
                        f'can act as an expert', 404)
        entry = {'user': user, 'role': 'expert', 'scope': scope, 'action': 'granted',
                 'by': appointer, 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(),
                 'reason': reason}
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_append': entry})
        append_role_event(entry)
        ensure_member(user)
        commit_push(f'Appoint {user} as expert for {scope}\n\n'
                    f'By: {appointer}\nReason: {reason}\nVia: archivist')
    note = sync_expert_group(user, add=True)
    return jsonify({'ok': True, 'user': user, 'scope': scope, 'by': appointer,
                    'forum': note,
                    'note': 'The appointment is public: it shows as a badge on the '
                            'members list and in the role log on their own page, '
                            'with your name and your reason.'})

@app.post('/api/editor/appoint')
def editor_appoint():
    """A single Committee seat grants the editor role, in the open.

    The library's shape, nothing else (see is_editor). Unscoped, so there is
    no downward door: only the Committee gives it. Taking it away is a role
    removal like any other: a Committee poll through /api/role/decide.

    Who: a Committee member (`key` plus `expert`)
    Reads: form fields user, reason, dry_run
    Answers: {ok, user, by, note}
    """
    appointment_form = request.form
    appointer, error = request_identity(appointment_form, 'expert')
    if error:
        return error
    user = (appointment_form.get('user') or '').strip()
    reason = (appointment_form.get('reason') or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9. _-]{2,40}', user):
        return fail('user must be the forum account being appointed')
    if len(reason) < 8:
        return fail('say why, publicly: the appointment is published with your name')
    if len(reason) > 500:
        return fail('reason must be under 500 characters')
    dry_run = appointment_form.get('dry_run') in ('1', 'true', 'yes')

    refresh_archive()
    with lock:
        if not dry_run:
            checkout_branch()
        if not is_committee(appointer):
            return fail(f'{appointer} holds no Committee seat; the editor role '
                        f'is the Committee\'s to give', 403)
        if is_editor(user):
            return fail(f'{user} is already an editor', 409)
        if forum_account_exists(user) is False:
            return fail(f'no forum account named {user}; they need one before a '
                        f'role means anything', 404)
        entry = {'user': user, 'role': 'editor', 'action': 'granted',
                 'by': appointer, 'date': time.strftime('%Y-%m-%d', time.gmtime()),
                 'at': now_iso(), 'reason': reason}
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_append': entry})
        append_role_event(entry)
        ensure_member(user)
        commit_push(f'Appoint {user} as editor\n\n'
                    f'By: {appointer}\nReason: {reason}\nVia: archivist')
    return jsonify({'ok': True, 'user': user, 'by': appointer,
                    'note': 'The appointment is public: an Editor badge on the '
                            'members list and a line in the role log on their '
                            'page, with your name and your reason.'})

ANNUL_WORDS = ('annul', 'remove', 'revoke', 'yes')

_sync_lock = threading.Lock()
_sync_last = [0.0]

def spawn_site_sync():
    """Run the code deploy detached from this process.

    The script ends in `systemctl restart archivist`, and a child living in
    our own cgroup would be killed along with us, so as root the work goes
    into a transient unit of its own. Returns how it was started."""
    with _sync_lock:
        now = time.monotonic()
        if now - _sync_last[0] < 20:
            return 'already syncing'
        _sync_last[0] = now
    try:
        if os.geteuid() == 0:
            subprocess.Popen(['systemd-run', '--collect', '--quiet',
                              '--unit=tar-site-sync-' + secrets.token_hex(4),
                              SITE_SYNC_CMD], start_new_session=True)
            return 'systemd-run'
        subprocess.Popen([SITE_SYNC_CMD], start_new_session=True)
        return 'detached'
    except (OSError, subprocess.SubprocessError) as exc:      # noqa: BLE001
        LOG.warning('site sync could not start: %s', exc)
        return 'failed'

@app.post('/api/hooks/github')
def github_hook():
    """GitHub says main moved; the live origin pulls the code and restarts.

    Site code reaches this machine through CI (deploy.yml, job sync-vps).
    When Actions is backed up, or a push produces no run at all, that path
    goes quiet and the VPS keeps serving yesterday's code. This is the
    second, independent door: GitHub's own webhook, signature-checked,
    `main` only, running the very script the CI key runs. Repeat calls
    inside 20 seconds fold into the sync already under way.

    Who: GitHub, proven by the HMAC in X-Hub-Signature-256
    Reads: the raw JSON body and the X-GitHub-Event header
    Answers: {ok, syncing, how} 202; {ok, ignored} for anything else
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
    if event != 'push':
        return jsonify({'ok': True, 'ignored': f'not a push event ({event})'})
    try:
        payload = json.loads(raw)
    except ValueError:
        return fail('unreadable payload')
    if payload.get('ref') != 'refs/heads/main':
        return jsonify({'ok': True, 'ignored': f'not main ({payload.get("ref")})'})
    head = (payload.get('after') or '')[:12]
    how = spawn_site_sync()
    LOG.info('github hook: main at %s, site sync %s', head, how)
    return jsonify({'ok': True, 'syncing': head, 'how': how}), 202

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

@app.post('/api/roles/publish')
@app.post('/api/expert/sync')                    # the name it shipped under
def roles_publish():
    """Print the forum groups from the archive.

    This used to run in both directions at once: experts were pushed out, while
    committee and moderator were pulled in from Discourse and written into the
    archive as facts. That made group membership a second way to grant a role,
    so the same field had two homes and neither owned it. Now every role is
    granted through the archivist and recorded in roles.json, and this only ever
    prints that record into the forum. It cannot write to the archive, which is
    what makes it safe to run after every role event instead of by hand.

    Who: a site-wide expert (`key` plus `expert`)
    Reads: form field dry_run
    Answers: {ok, dry_run, groups, note}
    """
    publish_form = request.form
    caller, error = request_identity(publish_form, 'expert')
    if error:
        return error
    refresh_archive(0)          # publishing must print the truth, not a cache
    if not is_site_expert(caller):
        return fail('only site-wide experts may publish the roster', 403)
    if not DISCOURSE_KEY:
        return fail('the forum is not configured on this server', 503)
    dry_run = publish_form.get('dry_run') in ('1', 'true', 'yes')
    report = publish_roles(dry=dry_run)
    return jsonify({'ok': True, 'dry_run': dry_run, 'groups': report,
                    'note': 'Membership of these groups is derived from roles.json. '
                            'Editing a group on the forum grants nothing and is '
                            'undone by the next publish.'})

@app.post('/api/founder/committee')
def founder_committee():
    """The Founder seats and unseats Steering Committee members, directly.

    The Committee's own poll route (/api/role/decide) exists alongside this and
    keeps its thresholds; this is the Founder acting as Founder. Every use is a
    role event with 'founder' on it, public in the site log and on the member's
    page, and the person is told. It is not quiet power, it is fast power.

    Who: the Founder (`key` plus `user`)
    Reads: form fields target, action (granted|revoked), reason, dry_run
    Answers: {ok, target, action, by, forum, told}
    """
    decision_form = request.form
    caller, error = request_identity(decision_form, 'user')
    if error:
        return error
    refresh_archive()
    if not is_founder(caller):
        return fail('only the Founder does this; the Committee route is '
                    '/api/role/decide with a poll', 403)
    target = (decision_form.get('target') or '').strip()
    action = (decision_form.get('action') or '').strip()
    reason = (decision_form.get('reason') or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9. _-]{2,40}', target):
        return fail('target must be the forum account the decision is about')
    if action not in ('granted', 'revoked'):
        return fail('action must be granted or revoked')
    if not (8 <= len(reason) <= 500):
        return fail('say why, publicly: a seat on the Committee is authority over '
                    'the whole place')
    dry_run = decision_form.get('dry_run') in ('1', 'true', 'yes')
    with lock:
        if not dry_run:
            checkout_branch()
        holds = any(u == target.lower() and r == 'committee'
                    for (u, r, s) in held_roles())
        if action == 'granted' and holds:
            return fail(f'{target} already sits on the Committee', 409)
        if action == 'revoked' and not holds:
            return fail(f'{target} does not sit on the Committee', 404)
        if action == 'granted' and forum_account_exists(target) is False:
            return fail(f'no forum account named {target}; they need one before a '
                        f'seat means anything', 404)
        entry = {'user': target, 'role': 'committee', 'action': action,
                 'by': caller, 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(),
                 'reason': f'{"Seated" if action == "granted" else "Unseated"} by the '
                           f'Founder. {reason}'}
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_append': entry})
        append_role_event(entry)
        if action == 'granted':
            ensure_member(target)
        commit_push(f'Committee: {target} {action} by the Founder\n\n'
                    f'Reason: {reason}\nVia: archivist')
    forum_note = publish_group('committee', target, add=(action == 'granted'))
    told = send_pm(
        target,
        f'You were {"seated on" if action == "granted" else "unseated from"} '
        f'the Steering Committee',
        (f'The Founder ({caller}) {"seated you on" if action == "granted" else "unseated you from"} '
         f'the Steering Committee.\n\nReason given: {reason}\n\n'
         f'The decision is public in the site log and on your member page.'))
    return jsonify({'ok': True, 'target': target, 'action': action, 'by': caller,
                    'forum': forum_note, 'told': told})

GRANT_WORDS = ('grant', 'appoint', 'yes', 'approve', 'in favour', 'in favor', 'for')

@app.post('/api/role/decide')
def role_decide():
    """Record a Committee decision about the committee or moderator role.

    The forum decides and the archive records. We do not implement voting: this
    reads the poll the Committee voted in, refuses anything that is not a
    genuine, checkable, finished Committee decision, and appends the event with
    the post as proof so anybody can go and check the call themselves. Joining a
    Discourse group is not how a role is granted, and never was a decision.

    Who: any member records it; the Committee poll named by `post` decides
    Reads: form fields target, role (committee|moderator|editor), action
        (granted|revoked), post (forum post id), reason, dry_run
    Answers: {ok, user, role, action, votes, committee, proof, forum}; 409 when
        the poll falls short
    """
    decision_form = request.form
    caller, error = request_identity(decision_form, 'user')
    if error:
        return error
    target = (decision_form.get('target') or '').strip()
    role = (decision_form.get('role') or '').strip()
    action = (decision_form.get('action') or '').strip()
    post_id = (decision_form.get('post') or '').strip()
    reason = (decision_form.get('reason') or '').strip()
    if role not in ('committee', 'moderator', 'editor'):
        return fail('role must be committee, moderator or editor; an expert scope '
                    'is appointed downward instead, and annulled by /api/expert/annul')
    if action not in ('granted', 'revoked'):
        return fail('action must be granted or revoked')
    if not re.fullmatch(r'[A-Za-z0-9. _-]{2,40}', target):
        return fail('target must be the forum account the decision is about')
    if not post_id.isdigit():
        return fail('post must be the id of the forum post carrying the Committee poll')
    if reason and len(reason) > 400:
        return fail('reason must be under 400 characters')
    poll, poll_error = read_committee_poll(post_id)
    if poll_error:
        return fail(poll_error, 409)
    size = committee_size()
    if size <= 0:
        return fail('the Committee is empty in the archive; there is nothing to '
                    'count a majority against', 409)
    words = GRANT_WORDS if action == 'granted' else ANNUL_WORDS
    votes = count_votes(poll, words)
    # Granting is an ordinary decision (Governance 2.3.3, 2.4.1): a simple
    # majority of the votes cast. So is every removal except a Committee
    # seat's (2.3.5): unseating the Committee alone needs a hard majority,
    # two thirds of every sitting member, counted whether they voted or not.
    # Expert annulment keeps its own rule (2.5.4) in its own endpoint.
    # A simple majority is more than half of the votes cast (2.1.1): absence
    # is abstention and there is no quorum. A hard majority is two thirds of
    # every sitting member (2.1.2), counted whether they voted or not.
    cast = votes_cast(poll)
    if action == 'granted' or role != 'committee':
        enough, needed = cast > 0 and votes * 2 > cast, 'a simple majority of the votes cast'
        against = f'{votes} of the {cast} votes cast'
    else:
        enough, needed = votes * 3 >= size * 2, ('a hard majority of the Committee, '
                                                 'two thirds of all sitting members')
        against = f'{votes} of {size} committee members'
    if not enough:
        return fail(f'{against} went to '
                    f'{"grant" if action == "granted" else "remove"} this role; '
                    f'{needed} is required (Governance '
                    f'{"2.3.3" if action == "granted" else "2.3.5"})', 409)
    dry_run = decision_form.get('dry_run') in ('1', 'true', 'yes')
    proof = f'{DISCOURSE_URL}/p/{post_id}'
    label = {'committee': 'the Steering Committee', 'moderator': 'moderator',
             'editor': 'editor'}[role]

    refresh_archive()
    with lock:
        if not dry_run:
            checkout_branch()
        holds = any(u == target.lower() and r == role
                    for (u, r, s) in held_roles())
        if action == 'granted' and holds:
            return fail(f'{target} already holds {role}', 409)
        if action == 'revoked' and not holds:
            return fail(f'{target} does not hold {role}', 404)
        if action == 'granted' and forum_account_exists(target) is False:
            return fail(f'no forum account named {target}; they need one before a '
                        f'role means anything', 404)
        reason_suffix = f' {reason}' if reason else ''
        entry = {'user': target, 'role': role, 'action': action, 'by': 'committee',
                 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(), 'proof': proof,
                 'reason': (f'{"Joined" if action == "granted" else "Left"} {label} '
                            f'by a Committee vote, {votes} of {size}.{reason_suffix}')}
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_append': entry,
                            'votes': votes, 'cast': cast, 'committee': size, 'proof': proof})
        append_role_event(entry)
        if action == 'granted':
            ensure_member(target)
        commit_push(f'Roles: {target} {action} {role} by Committee vote\n\n'
                    f'Vote: {votes} of {size}\nProof: {proof}\n'
                    f'Recorded by: {caller}\nVia: archivist')
    forum_note = publish_group(role, target, add=(action == 'granted'))
    return jsonify({'ok': True, 'user': target, 'role': role, 'action': action,
                    'votes': votes, 'cast': cast, 'committee': size, 'proof': proof, 'forum': forum_note})

@app.post('/api/expert/annul')
def expert_annul():
    """Apply a Committee decision to annul an appointment (Governance 2.5.4).

    We do not implement voting: the forum already has it. This reads the poll,
    checks it was a genuine Committee decision with a majority of the Committee
    behind it, and then edits the roster. Everything a member needs to check the
    call themselves is in the post it names.

    Who: any member applies it; the Committee poll named by `post` decides
    Reads: form fields target, scope (optional: every scope), post, dry_run
    Answers: {ok, target, dropped, votes, committee, proof, forum}
    """
    annulment_form = request.form
    caller, error = request_identity(annulment_form, 'user')
    if error:
        return error
    target = (annulment_form.get('target') or '').strip()
    scope = (annulment_form.get('scope') or '').strip()
    post_id = (annulment_form.get('post') or '').strip()
    if not target:
        return fail('target must be the expert whose appointment is annulled')
    if not post_id.isdigit():
        return fail('post must be the id of the forum post carrying the Committee poll')
    poll, poll_error = read_committee_poll(post_id)
    if poll_error:
        return fail(poll_error, 409)
    size = committee_size()
    if size <= 0:
        return fail('the committee group is empty or unreadable; nothing to count '
                    'a majority against', 409)
    for_annul = count_votes(poll, ANNUL_WORDS)
    cast = votes_cast(poll)
    # a simple majority: more than half of the votes cast (2.1.1, 2.5.4)
    if cast <= 0 or for_annul * 2 <= cast:
        return fail(f'{for_annul} of the {cast} votes cast went to annul; a simple '
                    f'majority of the votes cast is required', 409)
    dry_run = annulment_form.get('dry_run') in ('1', 'true', 'yes')
    proof = f'{DISCOURSE_URL}/p/{post_id}'
    refresh_archive()
    with lock:
        if not dry_run:
            checkout_branch()
        matching_scopes = [appointment for appointment in load_experts()
                if appointment['user'].lower() == target.lower()
                and (not scope or appointment['scope'] == scope)]
        dropped = len(matching_scopes)
        if not dropped:
            return fail(f'{target} holds no such scope', 404)
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_drop': dropped,
                            'votes': for_annul, 'cast': cast, 'committee': size, 'proof': proof})
        today = time.strftime('%Y-%m-%d', time.gmtime())
        for appointment in matching_scopes:
            append_role_event({
                'user': target, 'role': 'expert', 'scope': appointment['scope'],
                'action': 'revoked', 'by': 'committee', 'date': today, 'at': now_iso(), 'proof': proof,
                'reason': f'Annulled by a Committee vote, {for_annul} of {size}.'})
        commit_push(f'Annul: {target} loses {scope or "every scope"}\n\n'
                    f'Committee vote: {for_annul} of {size}\nProof: {proof}\n'
                    f'Applied by: {caller}\nVia: archivist')
    still = any(appointment['user'].lower() == target.lower() for appointment in load_experts())
    forum_note = sync_expert_group(target, add=False) if not still else 'still an expert elsewhere'
    return jsonify({'ok': True, 'target': target, 'dropped': dropped,
                    'votes': for_annul, 'cast': cast, 'committee': size, 'proof': proof, 'forum': forum_note})

@app.post('/api/expert/resign')
def expert_resign():
    """Step down from a scope. Always available, needs nobody's agreement.

    Who: the expert themselves (`key` plus `user`)
    Reads: form fields scope (optional: every scope), dry_run
    Answers: {ok, user, dropped, forum}
    """
    resignation_form = request.form
    user, error = request_identity(resignation_form, 'user')
    if error:
        return error
    scope = (resignation_form.get('scope') or '').strip()
    dry_run = resignation_form.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        if not dry_run:
            checkout_branch()
        matching_scopes = [appointment for appointment in load_experts()
                if appointment['user'].lower() == user.lower() and (not scope or appointment['scope'] == scope)]
        if not matching_scopes:
            return fail(f'{user} holds no such scope', 404)
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_drop': len(matching_scopes)})
        dropped = len(matching_scopes)
        today = time.strftime('%Y-%m-%d', time.gmtime())
        for appointment in matching_scopes:
            append_role_event({'user': user, 'role': 'expert', 'scope': appointment['scope'],
                               'action': 'revoked', 'by': user, 'date': today, 'at': now_iso(),
                               'reason': 'Stepped down of their own accord.'})
        commit_push(f'Resign: {user} steps down from '
                    f'{scope or "every scope"}\n\nVia: archivist')
    remaining_roster = load_experts()
    still = any(appointment['user'].lower() == user.lower() for appointment in remaining_roster)
    forum_note = sync_expert_group(user, add=False) if not still else 'still an expert elsewhere'
    return jsonify({'ok': True, 'user': user, 'dropped': dropped, 'forum': forum_note})

@app.post('/api/claim/request')
def claim_request():
    """Ask to be handed a name held for an author elsewhere.

    Who: any member (`key` plus `member`)
    Reads: form fields identity, evidence (8 to 1000 chars), dry_run
    Answers: {ok, request, note}; 409 when the name is claimed or a claim is open
    """
    claim_form = request.form
    member, error = request_identity(claim_form, 'member')
    if error:
        return error
    identity = (claim_form.get('identity') or '').strip()
    evidence = (claim_form.get('evidence') or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9. _-]{2,40}', identity):
        return fail('identity must be the name you are claiming')
    if not (8 <= len(evidence) <= 1000):
        return fail('say what shows the name is yours: a post from that account, a '
                    'channel hosting your encodes, anything somebody can check')
    dry_run = claim_form.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        author_file = ARCHIVE / 'authors' / f'{selfimport.slugify(identity)}.json'
        if author_file.exists():
            author_record = json.loads(author_file.read_text())
            if author_record.get('claimed'):
                return fail(f'{identity} is already claimed by '
                            f'{author_record.get("claimedBy") or "somebody"}', 409)
        claims_doc = load_claims()
        if any(claim['status'] == 'open' and claim['identity'].lower() == identity.lower()
               for claim in claims_doc['requests']):
            return fail(f'a claim for {identity} is already open', 409)
        if any(claim['status'] == 'open' and claim['member'].lower() == member.lower()
               for claim in claims_doc['requests']):
            return fail('you already have a claim open; it has to be answered first', 409)
        entry = {'member': member, 'identity': identity, 'evidence': evidence,
                 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(), 'status': 'open'}
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_file': entry})
        checkout_branch()
        claims_doc = load_claims()
        claims_doc['requests'].append(entry)
        save_claims(claims_doc)
        ensure_member(member)
        commit_push(f'Claim requested: {member} asks for {identity}\n\n'
                    f'Evidence: {evidence}\nVia: archivist')
    return jsonify({'ok': True, 'request': entry,
                    'note': 'Filed. The Steering Committee answers it, and you will '
                            'hear either way. While it is open they can see a masked '
                            'form of the address on your forum account, enough to '
                            'recognise it and not enough to write to you.'})

@app.post('/api/claim/pending')
def claim_pending():
    """Open claims, for the people who answer them.

    Carries the requester's forum email, read live and never stored, so the
    Committee can reach somebody about their own claim.

    Who: those who decide claims (the Steering Committee)
    Reads: nothing beyond identity
    Answers: {ok, pending, note}
    """
    request_form = request.form
    caller, error = request_identity(request_form, 'user')
    if error:
        return error
    refresh_archive(0)
    if not may_decide_claims(caller):
        return fail('the Steering Committee answers name claims', 403)
    pending_claims = []
    for claim in load_claims()['requests']:
        if claim['status'] != 'open':
            continue
        pending_claims.append(dict(claim, email=member_email_masked(claim['member'])))
    return jsonify({'ok': True, 'pending': pending_claims,
                    'note': 'The addresses here are masked and read from the forum as '
                            'you ask for them. The whole address is never sent by this '
                            'service, is not in the archive, and never appears on the '
                            'site.'})

@app.post('/api/claim/decide')
def claim_decide():
    """The Committee answers a claim, and the person is told either way.

    Who: those who decide claims, never on their own claim
    Reads: form fields identity, action (approved|denied), note, dry_run
    Answers: {ok, identity, member, action, by, rename, told}
    """
    decision_form = request.form
    caller, error = request_identity(decision_form, 'user')
    if error:
        return error
    refresh_archive()
    if not may_decide_claims(caller):
        return fail('the Steering Committee answers name claims', 403)
    identity = (decision_form.get('identity') or '').strip()
    action = (decision_form.get('action') or '').strip()
    decision_note = (decision_form.get('note') or '').strip()
    if action not in ('approved', 'denied'):
        return fail('action must be approved or denied')
    if action == 'denied' and not (8 <= len(decision_note) <= 500):
        return fail('say why it was denied: the person is told, and they can answer it')
    if len(decision_note) > 500:
        return fail('note must be under 500 characters')
    dry_run = decision_form.get('dry_run') in ('1', 'true', 'yes')
    with lock:
        claims_doc = load_claims()
        open_claim = next((claim for claim in claims_doc['requests']
                    if claim['status'] == 'open' and claim['identity'].lower() == identity.lower()),
                   None)
        if not open_claim:
            return fail(f'no claim for {identity} is open', 404)
        if open_claim['member'].lower() == caller.lower():
            return fail('you cannot answer your own claim', 403)
        today = time.strftime('%Y-%m-%d', time.gmtime())
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'request': open_claim, 'would': action})
        checkout_branch()
        claims_doc = load_claims()
        open_claim = next((claim for claim in claims_doc['requests']
                    if claim['status'] == 'open' and claim['identity'].lower() == identity.lower()),
                   None)
        if not open_claim:
            return fail(f'no claim for {identity} is open', 404)
        open_claim.update(status=action, decidedBy=caller, decidedAt=today, note=decision_note)
        member = open_claim['member']
        if action == 'approved':
            authors_dir = ARCHIVE / 'authors'
            author_file = authors_dir / f'{selfimport.slugify(open_claim["identity"])}.json'
            author_record = json.loads(author_file.read_text()) if author_file.exists() else {
                'username': open_claim['identity']}
            if author_record.get('claimed') and (author_record.get('claimedBy') or '').lower() != member.lower():
                return fail(f'{open_claim["identity"]} is already claimed by '
                            f'{author_record.get("claimedBy")}', 409)
            author_record.update({'username': author_record.get('username') or open_claim['identity'],
                        'claimed': True, 'claimedBy': member, 'claimedAt': today, 'claimedAtTime': now_iso(),
                        'claimMethod': 'committee', 'attestedBy': caller,
                        'attestation': (f'Claim approved by the Steering Committee. '
                                        f'{open_claim["evidence"]}')[:1000]})
            authors_dir.mkdir(exist_ok=True)
            author_file.write_text(json.dumps(author_record, indent=1) + '\n')
            # the claimed record IS this person's member record now; the one
            # their registration name wrote at first login is superseded, and
            # keeping it would list a member who no longer exists
            old_record_file = authors_dir / f'{selfimport.slugify(member)}.json'
            if old_record_file != author_file and old_record_file.exists():
                old_record_file.unlink()
        save_claims(claims_doc)
        commit_push(f'Claim {action}: {member} for {open_claim["identity"]}\n\n'
                    f'By: {caller}\n' + (f'Note: {decision_note}\n' if decision_note else '')
                    + 'Via: archivist')
    rename_note = (unlock_forum_username(member, open_claim['identity'])
                   if action == 'approved' else 'no rename')
    told = send_pm(
        member,
        f'Your claim to the name {open_claim["identity"]} was {action}',
        (f'The Steering Committee {action} your claim to **{open_claim["identity"]}**.\n\n'
         + (f'Your forum account has been renamed and the name is yours. Your profile '
            f'now carries an **Import my movies** button for your publications '
            f'at the site the name comes from, co-authored ones included; '
            f'importing a co-authored work is your responsibility.\n\n'
            if action == 'approved' else '')
         + (f'Reason given: {decision_note}\n\n' if decision_note else '')
         + f'Answered by {caller}. You can reply to this message if you think this '
           f'is wrong; the decision is recorded in the site log either way.'))
    return jsonify({'ok': True, 'identity': open_claim['identity'], 'member': member,
                    'action': action, 'by': caller, 'rename': rename_note,
                    'told': told})

@app.post('/api/claim/attest')
def claim_attest():
    """A Steering Committee member attests that a member is an author from
    another site, directly, without a claim having been filed.

    To protect authors who have not arrived yet, their names are held here and
    nobody else may take one. Proving you are one of them used to mean posting a
    token on a TASVideos wiki homepage, which silently required a permission that
    site grants to some accounts and not others: the very people whose names we
    hold were the ones who could not prove anything. A proof route that depends
    on somebody's standing at another site is not a route we control.

    So identity is a judgement, made by a named Committee member, in public: they say who
    they verified and how, it is written into the author record, and the
    site log shows it. A ban elsewhere is not a status here (Principles
    1.4, 1.5) and never blocks an attestation. Wrong calls are visible and
    revocable, which is the honest trade for accepting human judgement.

    Who: those who decide claims (`key` plus `expert`)
    Reads: form fields member, identity, method (12 to 1000 chars), dry_run
    Answers: {ok, identity, member, attestedBy, rename, note}
    """
    attestation_form = request.form
    expert, error = request_identity(attestation_form, 'expert')
    if error:
        return error
    refresh_archive()
    if not may_decide_claims(expert):
        return fail('only the Steering Committee assesses identity', 403)
    member = (attestation_form.get('member') or '').strip()
    identity = (attestation_form.get('identity') or '').strip()
    method = (attestation_form.get('method') or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9. _-]{2,40}', member):
        return fail('member must be the forum account being attested')
    if not re.fullmatch(r'[A-Za-z0-9. _-]{2,40}', identity):
        return fail('identity must be the name being claimed')
    if len(method) < 12:
        return fail('say how you verified it: the reason is public and it is the '
                    'whole point of an attestation')
    if len(method) > 1000:
        return fail('method must be under 1000 characters')
    dry_run = attestation_form.get('dry_run') in ('1', 'true', 'yes')

    with lock:
        if not dry_run:
            checkout_branch()
        authors_dir = ARCHIVE / 'authors'
        author_file = authors_dir / f'{selfimport.slugify(identity)}.json'
        author_record = json.loads(author_file.read_text()) if author_file.exists() else {'username': identity}
        if author_record.get('claimed') and (author_record.get('claimedBy') or '').lower() != member.lower():
            return fail(f'{identity} is already claimed by {author_record.get("claimedBy")}', 409)
        author_record.update({'username': author_record.get('username') or identity,
                    'claimed': True,
                    'claimedBy': member,
                    'claimedAt': time.strftime('%Y-%m-%d', time.gmtime()),
                    'claimedAtTime': now_iso(),
                    'claimMethod': 'attested',
                    'attestedBy': expert,
                    'attestation': method})
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_write': author_record})
        authors_dir.mkdir(exist_ok=True)
        author_file.write_text(json.dumps(author_record, indent=1) + '\n')
        old_record_file = authors_dir / f'{selfimport.slugify(member)}.json'
        if old_record_file != author_file and old_record_file.exists():
            old_record_file.unlink()
        commit_push(f'Attest {identity}: verified by expert {expert}\n\n'
                    f'Member: {member}\nMethod: {method}\nVia: archivist')
    rename_note = unlock_forum_username(member, identity)
    return jsonify({'ok': True, 'identity': author_record['username'], 'member': member,
                    'attestedBy': expert, 'rename': rename_note,
                    'note': 'The attestation is public: it names you as the expert who '
                            'made the call, and the site log carries it. The '
                            'member can now import their movies from their '
                            'own profile.'})

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

@app.post('/api/import/scan')
def import_scan():
    """What of my TASVideos catalog is not in the archive yet?

    Who: a logged-in member who has claimed the identity (session only)
    Reads: nothing
    Answers: {ok, user} plus selfimport.scan's result
    """
    user, error = _import_identity()
    if error:
        return error
    if not (DUMPS_DIR / 'metadata' / 'publications.json').exists():
        return fail('the tasvideos backup is not available on this server; try later', 503)
    _refresh_dumps()
    with lock:
        checkout_branch()
        return jsonify({'ok': True, 'user': user,
                        **selfimport.scan(DUMPS_DIR, ARCHIVE, user)})

@app.post('/api/import/run')
def import_run():
    """Import the next batch of my pending TASVideos publications. Call
    repeatedly until remaining is 0; each batch is one archive commit.

    Who: a logged-in member who has claimed the identity (session only)
    Reads: form field select (publication ids)
    Answers: {ok, user} plus selfimport.import_batch's result
    """
    user, error = _import_identity()
    if error:
        return error
    if not (DUMPS_DIR / 'metadata' / 'publications.json').exists():
        return fail('the tasvideos backup is not available on this server; try later', 503)
    _refresh_dumps()
    # nothing is imported that was not picked by id: the member chooses which
    # of their movies come over, co-authored ones included, and picking a
    # co-authored one is the act that carries the responsibility for it
    raw = (request.form.get('select') or '').replace(',', ' ').replace('M', ' ')
    try:
        select = sorted({int(s) for s in raw.split() if s.strip()})
    except ValueError:
        return fail('select must be publication ids, like "910001 910002"')
    if not select:
        return fail('select which of your movies to import; nothing is imported '
                    'unpicked')
    if len(select) > 500:
        return fail('that is more than one member has ever published; check the list')
    with lock:
        checkout_branch()
        result = selfimport.import_batch(DUMPS_DIR, ARCHIVE, user,
                                      time.strftime('%Y-%m-%d', time.gmtime()),
                                      THUMB_FETCH_BASE, limit=6, select=select)
        if result['imported']:
            result['topics'] = topics_for_imported(ARCHIVE, result['imported'])
            commit_push(f"Self-import for {user}: {', '.join(result['imported'])}")
    if result['imported']:
        # one line per batch, not per movie: a large import in six-movie
        # batches would otherwise flood the channel. The first few ids carry
        # the links; the source stays implicit, each run page naming its own.
        imported_ids = result['imported']
        shown = ', '.join(f'[{i}](<{SITE_URL}/runs/{i}/>)' for i in imported_ids[:3])
        more = f' +{len(imported_ids) - 3} more' if len(imported_ids) > 3 else ''
        word = 'movie' if len(imported_ids) == 1 else f'{len(imported_ids)} movies'
        notify_discord(f'\U0001f4e5 **{member_md(user)}** imported {word}: {shown}{more}',
                       wait_for=f'{SITE_URL}/runs/{imported_ids[0]}/')
    return jsonify({'ok': True, 'user': user, **result})

@app.post('/api/verify')
def verify():
    """Record a verification: the goal, judged from the encode.

    Verification is the ranking gate. From a member it makes the run
    verified, which ranks; from an expert covering the game it makes it
    verified (expert), which is permanent. (The archive's enum names stay
    provisional/confirmed; only the words people see changed.) Who the verifier was at the moment of
    the act is stamped on the act, because scopes change and facts do not.

    Who: a member who is not one of the run's authors; the run needs an
        encode and a defined goal
    Reads: form fields run, notes, dry_run
    Answers: {ok, run, status, verifications}; dry_run: {ok, dry_run,
        would_record, status}
    """
    verification_form = request.form
    dry_run = verification_form.get('dry_run') in ('1', 'true', 'yes')
    with lock:
        auth_error = auth_precheck(verification_form)
        if auth_error:
            return auth_error
        if not dry_run:
            checkout_branch()
        act_error, run_dir, run, user = act_common(verification_form)
        if act_error:
            return act_error
        if user.lower() in {a['user'].lower() for a in run.get('verifications', [])}:
            return fail('you have already verified this run; one verification per member')
        if (run.get('category') or {}).get('goal') == 'unclassified':
            return fail('Unclassified runs cannot be verified because no goal is defined; '
                        'they can be reproduced and liked')
        if not run.get('encodes'):
            return fail('this run has no encode linked; verification needs one to judge from')

        entry = {'user': user, 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso()}
        game_key = f'{run_dir.parent.parent.parent.name}/{run_dir.parent.parent.name}'
        if expert_covers(user, game_key):
            entry['expert'] = True
        if (verification_form.get('notes') or '').strip():
            entry['notes'] = verification_form.get('notes').strip()
        run.setdefault('verifications', []).append(entry)
        sync_status(run)
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_record': entry,
                            'status': run['status']})

        (run_dir / 'run.json').write_text(json.dumps(
            {k: v for k, v in run.items() if not k.startswith('_')}, indent=1))
        ensure_member(user)
        commit_push(f'Verify {run["id"]}: by {user}\n\nVia: archivist')
        notify_discord(f'\u2713 **{member_md(user)}** verified'
                       + ' '
                       + movie_md(run),
                       wait_for=f'{SITE_URL}/runs/{run["id"]}/')
    return jsonify({'ok': True, 'run': run['id'], 'status': run['status'],
                    'verifications': len([a for a in run['verifications'] if not a.get('invalidated')])})

@app.post('/api/withdraw')
def withdraw():
    """Take a run out of the listings.

    Withdrawing is a voluntary act: only the run's own authors may do it (an
    expert who must remove a run deletes it, on the record). Nothing is
    erased: the record, the movie file and the reason stay in the archive,
    because the principles forbid erasing a contribution (1.2, 2.8.2). The
    site stops listing it and says why.

    Who: one of the run's authors
    Reads: form fields run, reason, dry_run
    Answers: {ok, run, withdrawn}
    """
    withdrawal_form = request.form
    dry_run = withdrawal_form.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        auth_error = auth_precheck(withdrawal_form)
        if auth_error:
            return auth_error
        if not dry_run:
            checkout_branch()
        user, error = request_identity(withdrawal_form)
        if error:
            return error
        run_id = (withdrawal_form.get('run') or '').strip()
        run_dir = find_run(run_id) if re.fullmatch(r'M[0-9]+', run_id) else None
        if not run_dir:
            return fail(f'unknown run {run_id}', 404)
        run = json.loads((run_dir / 'run.json').read_text())
        is_author = current_name(user).lower() in run_authors_now(run)
        if not is_author:
            return fail("withdrawing is the author's own voluntary act; an expert "
                        "who must remove a run deletes it instead", 403)
        if run.get('withdrawn'):
            return fail(f'{run_id} is already withdrawn')
        reason = (withdrawal_form.get('reason') or '').strip()
        if not reason:
            return fail('a withdrawal must state its reason; it is shown in the open')
        if len(reason) > ACT_NOTES_MAX:
            return fail(f'reason exceeds {ACT_NOTES_MAX} characters')

        run['withdrawn'] = {'by': user, 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(),
                          'reason': reason, 'role': 'author'}
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_withdraw': run['withdrawn']})
        (run_dir / 'run.json').write_text(json.dumps(
            {k: v for k, v in run.items() if not k.startswith('_')}, indent=1))
        ensure_member(user)
        commit_push(f'Withdraw {run_id}: by {user}\n\nReason: {reason}\nVia: archivist')
    return jsonify({'ok': True, 'run': run_id, 'withdrawn': run['withdrawn']})

@app.post('/api/console-verify')
def console_verify():
    """Record a console verification: the run replayed on original hardware.

    An optional signal beside verification (the one gate). It is the most
    expensive act anyone can perform here, so it carries a public recording
    and pays accordingly.

    Who: a member who is not one of the run's authors; not on video-only runs
    Reads: form fields run, proof (URL of the recording), hardware, notes,
        dry_run; optional file screenshot
    Answers: {ok, run, proof, consoleVerifications}
    """
    verification_form = request.form
    dry_run = verification_form.get('dry_run') in ('1', 'true', 'yes')
    with lock:
        auth_error = auth_precheck(verification_form)
        if auth_error:
            return auth_error
        if not dry_run:
            checkout_branch()
        act_error, run_dir, run, user = act_common(verification_form)
        if act_error:
            return act_error
        if run.get('videoOnly'):
            return fail('this run is video-only: there is no input movie to play '
                        'back on hardware, so console verification does not apply')
        systems_doc = json.loads((ARCHIVE / 'systems.json').read_text())
        if not systems_doc.get(run_dir.parent.parent.parent.name, {}).get('hardwareVerifiable'):
            return fail('this system is not one that is played back on original hardware '
                        '(systems.json: hardwareVerifiable), so console verification does not apply')
        if user.lower() in {a['user'].lower() for a in run.get('consoleVerifications', [])}:
            return fail('you have already console-verified this run; '
                        'one console verification per member')
        proof = (verification_form.get('proof') or '').strip()
        if not re.match(r'https?://\S+$', proof) or len(proof) > 500:
            return fail('a link to the recording of the console playing this run '
                        'is required as proof')

        entry = {'user': user, 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(),
                 'proof': proof}
        if (verification_form.get('hardware') or '').strip():
            entry['hardware'] = verification_form.get('hardware').strip()[:120]
        if (verification_form.get('notes') or '').strip():
            entry['notes'] = verification_form.get('notes').strip()[:2000]

        screenshot_upload = request.files.get('screenshot')
        screenshot_bytes = None
        if screenshot_upload and screenshot_upload.filename:
            ext = pathlib.Path(screenshot_upload.filename).suffix.lower()
            if ext not in IMAGE_MAGIC:
                return fail('screenshot must be png, jpg or webp')
            screenshot_bytes = screenshot_upload.read()
            if len(screenshot_bytes) > SHOT_MAX_EACH:
                return fail('screenshot exceeds 512 KB')
            if not any(screenshot_bytes.startswith(magic) for magic in IMAGE_MAGIC[ext]):
                return fail(f'screenshot is not a real {ext} image')
            stored_bytes = sum(sp.stat().st_size for sp in (run_dir / 'console').glob('*')
                           if sp.is_file()) if (run_dir / 'console').exists() else 0
            if stored_bytes + len(screenshot_bytes) > SHOT_MAX_TOTAL:
                return fail('this run has reached its screenshot storage cap')
            ordinal = len(run.get('consoleVerifications', [])) + 1
            entry['screenshot'] = f'console/{ordinal}-{user}{ext}'

        run.setdefault('consoleVerifications', []).append(entry)
        sync_status(run)
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_record': entry})

        if screenshot_bytes is not None:
            (run_dir / 'console').mkdir(exist_ok=True)
            (run_dir / entry['screenshot']).write_bytes(screenshot_bytes)
        (run_dir / 'run.json').write_text(json.dumps(
            {k: v for k, v in run.items() if not k.startswith('_')}, indent=1))
        ensure_member(user)
        commit_push(f'Console-verify {run["id"]}: by {user}\n\nProof: {proof}\nVia: archivist')
        notify_discord(f'\U0001f579\ufe0f **{member_md(user)}** played ' + movie_md(run)
                       + ' back on original hardware',
                       wait_for=f'{SITE_URL}/runs/{run["id"]}/')
    return jsonify({'ok': True, 'run': run['id'], 'proof': proof,
                    'consoleVerifications': len([a for a in run['consoleVerifications']
                                                 if not a.get('invalidated')])})

DISCUSSION_CACHE = {}      # topic id -> (fetched_at, payload)

ENCODE_CACHE = {}       # url -> (when, payload); the submit page asks on every keystroke

@app.get('/api/encode/check')
def encode_check():
    """Is this a usable encode link, and what does its still frame look like?

    The submit page used to answer this itself by loading a YouTube thumbnail
    URL directly. Most platforms do not publish one you can build: Niconico and
    Bilibili have to be asked, and a browser cannot ask them (no CORS). So the
    check moved here, which also means the page and the server agree on what
    counts as a valid encode, by construction.

    Who: anybody
    Reads: query arg url
    Answers: {ok, kind, name, id, thumb}, or {ok: false, kind, name, error}
    """
    gate = _helper_gate()
    if gate:
        return gate
    url = (request.args.get('url') or '').strip()
    encode_provider = providers.resolve(url)
    if not encode_provider:
        return jsonify({'ok': False,
                        'error': 'not a link from ' + ', '.join(providers.names())})
    cached = ENCODE_CACHE.get(url)
    if cached and time.time() - cached[0] < 300:
        return jsonify(cached[1])
    thumb = providers.thumbnail_url(encode_provider['kind'], encode_provider['id'])
    if not thumb and providers.BY_KIND[encode_provider['kind']].get('thumbs'):
        # a direct template needs fetching to know whether the video is real;
        # the page then loads the candidate that actually answered (#29)
        thumb = providers.thumbnail_source(encode_provider['kind'], encode_provider['id'], THUMB_MAX)
    payload = ({'ok': True, 'kind': encode_provider['kind'], 'name': encode_provider['name'],
                'id': encode_provider['id'], 'thumb': thumb,
                'seconds': providers.duration_seconds(encode_provider['kind'], encode_provider['id'])} if thumb else
               {'ok': False, 'kind': encode_provider['kind'], 'name': encode_provider['name'],
                'error': f'that {encode_provider["name"]} video does not exist, or is private'})
    ENCODE_CACHE[url] = (time.time(), payload)
    return jsonify(payload)

@app.get('/api/discussion')
def discussion():
    """The forum topic for a run, as the site renders it in place.

    Who: anybody
    Reads: query arg topic (forum topic id)
    Answers: {ok, topic, title, url, posts, replyCount}
    """
    try:
        topic_id = int(request.args.get('topic') or 0)
    except ValueError:
        return fail('topic must be a number')
    if topic_id <= 0:
        return fail('topic is required')
    if not DISCOURSE_KEY:
        return fail('the forum is not configured on this server', 503)
    cached = DISCUSSION_CACHE.get(topic_id)
    if cached and time.time() - cached[0] < 60:
        return jsonify(cached[1])
    try:
        topic_json = _forum_get(f'/t/{topic_id}.json')
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            # the forum meters API calls by the minute; a busy minute is no
            # reason to show an empty box. Serve what was shown before, or
            # wait out the short window once, then give up for this call
            if cached:
                return jsonify(cached[1])
            try:
                time.sleep(min(3.0, float(exc.headers.get('Retry-After') or 1)))
                topic_json = _forum_get(f'/t/{topic_id}.json')
            except Exception as again:                          # noqa: BLE001
                return fail(f'could not reach the forum: {again}', 502)
        else:
            return fail(f'could not reach the forum: {exc}', 502)
    except Exception as exc:                                    # noqa: BLE001
        if cached:
            return jsonify(cached[1])
        return fail(f'could not reach the forum: {exc}', 502)
    posts = []
    for post in topic_json.get('post_stream', {}).get('posts', []):
        posts.append({
            'id': post.get('id'), 'number': post.get('post_number'),
            'user': post.get('username'), 'name': post.get('display_username'),
            'avatar': (DISCOURSE_URL + post['avatar_template'].replace('{size}', '48')
                       if (post.get('avatar_template') or '').startswith('/')
                       else (post.get('avatar_template') or '').replace('{size}', '48')),
            'html': post.get('cooked') or '',
            'date': (post.get('created_at') or '')[:19],
            'staff': bool(post.get('staff')),
        })
    payload = {'ok': True, 'topic': topic_id, 'title': topic_json.get('title'),
               'url': f'{DISCOURSE_URL}/t/{topic_id}',
               'posts': posts, 'replyCount': max(0, len(posts) - 1)}
    DISCUSSION_CACHE[topic_id] = (time.time(), payload)
    return jsonify(payload)

@app.post('/api/discussion/reply')
def discussion_reply():
    """Post a reply to a run's topic as the logged-in member.

    Session only: the shared key must never be able to speak as somebody
    else, and Discourse applies that member's own trust level and rate
    limits because the post is made under their name.

    Who: a logged-in member (session only)
    Reads: form fields topic, body
    Answers: {ok, topic, post, user}
    """
    reply_form = request.form
    user = session_user()
    if not user:
        return fail('log in via the forum to reply', 403)
    if not origin_ok():
        return fail('cross-origin request refused', 403)
    if not re.fullmatch(r'[A-Za-z0-9._-]{3,30}', user):
        return fail('session username is not valid', 400)
    if not DISCOURSE_KEY:
        return fail('the forum is not configured on this server', 503)
    try:
        topic_id = int(reply_form.get('topic') or 0)
    except ValueError:
        return fail('topic must be a number')
    body = (reply_form.get('body') or '').strip()
    if topic_id <= 0:
        return fail('topic is required')
    if len(body) < 5:
        return fail('a reply needs at least a few words')
    if len(body.encode()) > 32 * 1024:
        return fail('reply exceeds 32 KB')
    post_body = urllib.parse.urlencode({'topic_id': topic_id, 'raw': body}).encode()
    forum_request = urllib.request.Request(f'{DISCOURSE_URL}/posts.json', data=post_body, method='POST',
                                 headers={'Api-Key': DISCOURSE_KEY, 'Api-Username': user})
    try:
        with urllib.request.urlopen(forum_request, timeout=20) as forum_response:
            posted = json.loads(forum_response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode(errors='replace')
        return fail(f'the forum refused the reply: {detail}', 502)
    except Exception as exc:                                    # noqa: BLE001
        return fail(f'could not reach the forum: {exc}', 502)
    DISCUSSION_CACHE.pop(topic_id, None)
    return jsonify({'ok': True, 'topic': topic_id, 'post': posted.get('post_number'),
                    'user': user})

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

