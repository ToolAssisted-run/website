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

from flask import Flask, jsonify, make_response, redirect, request

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
    _forum_get,
    avatar_for,
    committee_size,
    count_votes,
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
            j = json.loads(resp.get_data())
        except Exception:                                     # noqa: BLE001
            return resp
        if isinstance(j, dict) and j.get('ok') and not j.get('dry_run') \
                and 'serial' not in j:
            j['serial'] = current_serial()
            resp.set_data(json.dumps(j))
    return resp

@app.get('/login')
def login():
    """Redirect to the forum (DiscourseConnect provider) to authenticate."""
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
    re-authenticates against the still-live Discourse session."""
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
    u = session_user()
    return jsonify({'ok': True, 'user': u, 'loggedIn': bool(u),
                    'avatar': avatar_for(u) if u else None})

def auth_precheck(f):
    """Cheap auth gate to run BEFORE any git work: a valid session or the
    shared key. Full identity resolution happens later in request_identity."""
    if session_user():
        if not origin_ok():
            return fail('cross-origin request refused', 403)
        return None
    if f.get('key') == SUBMIT_KEY:
        return None
    return fail('log in via the forum, or provide the submitter key', 403)

def request_identity(f, field='user'):
    """Who is acting: a logged-in session's username wins; otherwise the shared
    key plus an explicit username (operator/v0 path). Returns (user, error)."""
    su = session_user()
    if su:
        if not origin_ok():
            return None, fail('cross-origin request refused', 403)
        if not re.fullmatch(r'[A-Za-z0-9._-]{3,30}', su):
            return None, fail('session username is not archive-safe', 400)
        return su, None
    if f.get('key') != SUBMIT_KEY:
        return None, fail('log in via the forum, or provide the submitter key', 403)
    user = (f.get(field) or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9._-]{3,30}', user):
        return None, fail(f'{field} must be a valid username')
    return user, None

def act_common(f):
    """Shared validation for /api/reproduce and /api/verify.
    Returns (error_response, run_dir, run, user) — error_response is None on success."""
    user, err_ = request_identity(f)
    if err_:
        return err_, None, None, None
    run_id = (f.get('run') or '').strip()
    if not re.fullmatch(r'M[0-9]+', run_id):
        return fail('run must be a run id like M100001'), None, None, None
    rdir = find_run(run_id)
    if not rdir:
        return fail(f'unknown run {run_id}', 404), None, None, None
    r = json.loads((rdir / 'run.json').read_text())
    if r.get('withdrawn'):
        return fail(f'{run_id} has been withdrawn; no further acts apply'), None, None, None
    if r.get('status', {}).get('reproduced') == 'imported':
        return fail('Imported runs are irrevocably verified; no further acts apply'), None, None, None
    if current_name(user).lower() in run_authors_now(r):
        return fail('authors cannot act on their own run'), None, None, None
    notes = (f.get('notes') or '').strip()
    if len(notes) > ACT_NOTES_MAX:
        return fail(f'notes exceed {ACT_NOTES_MAX} characters'), None, None, None
    return None, rdir, r, user

@app.get('/')
def form():
    games = sorted(f'{p.parent.parent.name}/{p.parent.name}'
                   for p in ARCHIVE.glob('games/*/*/game.json'))
    opts = ''.join(f'<option>{g}</option>' for g in games)
    return f'''<!DOCTYPE html><html><head><meta charset="utf-8"><title>archivist · submit</title>
<style>body{{font-family:system-ui;max-width:560px;margin:40px auto;padding:0 16px;line-height:1.5}}
label{{display:block;margin:12px 0 3px;font-size:.85rem;color:#555}}
input,select,textarea{{width:100%;padding:8px;border:1.5px solid #bbb;border-radius:7px;font:inherit}}
button{{margin-top:16px;padding:10px 18px;background:#22C55E;border:0;border-radius:8px;font-weight:700;cursor:pointer}}
h1{{font-size:1.3rem}} .note{{font-size:.8rem;color:#777}}</style></head><body>
<h1>toolAssisted.run · submit a run <small style="color:#D97706">(beta)</small></h1>
<p class="note">Movie file + metadata only, never ROMs. Text attachments (configs) allowed,
128&nbsp;KB each. Your run is archived immediately, as pending.</p>
<form method="post" action="api/submit" enctype="multipart/form-data">
<label>Submitter key</label><input name="key" type="password" required>
<label>Your username</label><input name="submitter" required>
<label>Game</label><select name="game">{opts}</select>
<label>Category (goal key)</label><input name="goal" placeholder="e.g. any" required>
<label>Authors (comma-separated)</label><input name="authors" required>
<label>Encode link (YouTube, required; verification and the run thumbnail derive from it)</label>
<input name="encode" type="url" required placeholder="https://youtu.be/…">
<label>Emulator / core</label><input name="emulator" placeholder="e.g. BizHawk 2.11">
<label>ROM name</label><input name="rom_name"><label>ROM sha1 (optional)</label><input name="rom_sha1">
<label>Movie file</label><input name="movie" type="file" required>
<label>Voluntary content disclosures</label>
<label><input type="checkbox" name="content_warnings" value="mature-violence" style="width:auto"> Mature / violent content</label>
<label><input type="checkbox" name="content_warnings" value="sexual" style="width:auto"> Sexual content</label>
<label><input type="checkbox" name="content_warnings" value="photosensitivity" style="width:auto"> Photosensitivity (flashing lights)</label>
<label><input type="checkbox" name="content_warnings" value="strong-language" style="width:auto"> Strong language</label>
<label>Attachments (optional: text configs, or additional movie files)</label><input name="attachments" type="file" multiple>
<label>Notes</label><textarea name="notes" rows="8"></textarea>
<label><input type="checkbox" name="consent" value="yes" required style="width:auto">
I license this submission under CC BY 4.0, I have read and agree with the
<a href="https://github.com/ToolAssisted-run#1-community-principles" target="_blank">Community
Principles, Terms of Use, Code of Conduct and Privacy Policy</a>, and I confirm everything
here is complete and truthful, especially the authorship.</label>
<button>Submit to the archive</button></form>
<hr style="margin:32px 0;border:none;border-top:1.5px solid #ddd">
<h1>Reproduce a run</h1>
<p class="note">You loaded the movie file on your own setup and it synced to the end.
Ending screenshot required as proof. You cannot reproduce your own run.</p>
<form method="post" action="api/reproduce" enctype="multipart/form-data">
<label>Submitter key</label><input name="key" type="password" required>
<label>Your username</label><input name="user" required>
<label>Run id</label><input name="run" placeholder="e.g. M100001" required>
<label>Emulator / core used</label><input name="emulator" placeholder="e.g. BizHawk 2.11">
<label>Ending screenshot (png/jpg/webp)</label><input name="screenshot" type="file" accept=".png,.jpg,.jpeg,.webp" required>
<label>Notes for the next reproducer (optional)</label><textarea name="notes" rows="3"></textarea>
<button>Record reproduction</button></form>
<hr style="margin:32px 0;border:none;border-top:1.5px solid #ddd">
<h1>Verify a run</h1>
<p class="note">You watched the encode and confirm the run achieves its stated category goal.
You cannot verify your own run.</p>
<form method="post" action="api/verify">
<label>Submitter key</label><input name="key" type="password" required>
<label>Your username</label><input name="user" required>
<label>Run id</label><input name="run" placeholder="e.g. M100001" required>
<label>Notes (optional)</label><textarea name="notes" rows="3"></textarea>
<button>Record verification</button></form></body></html>'''

def read_attachments(existing=None):
    """Validate the request's uploaded 'attachments': text configs (UTF-8,
    size-capped) or additional movie files. `existing` counts a run's
    current attachments against the caps. Returns ([(name, bytes)], error)."""
    existing = existing or []
    atts = []
    total = 0
    movie_atts = sum(1 for a in existing
                     if pathlib.Path(a['file']).suffix.lower().lstrip('.') in MOVIE_EXTS)
    for fs in request.files.getlist('attachments'):
        if not fs.filename:
            continue
        name = re.sub(r'[^A-Za-z0-9._-]', '_', pathlib.Path(fs.filename).name)
        suffix = pathlib.Path(name).suffix.lower()
        data = fs.read()
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
        atts.append((name, data))
    if len(atts) + len(existing) > ATTACH_MAX_COUNT:
        return None, fail('too many attachments (max 8)')
    if movie_atts > 4:
        return None, fail('too many movie attachments (max 4)')
    if total > ATTACH_MAX_TOTAL:
        return None, fail('text attachments exceed 512 KB total')
    return atts, None

@app.post('/api/submit')
def submit():
    f = request.form
    submitter, err_ = request_identity(f, 'submitter')
    if err_:
        return err_
    if f.get('consent') != 'yes':
        return fail('submission requires consent: licensing under CC BY 4.0, agreeing '
                    'with the Community Principles, Terms of Use, Code of Conduct and '
                    'Privacy Policy, and confirming the information, especially '
                    'authorship, is complete and truthful')

    # --- game and category exist beforehand (creation has its own flow) ---
    gsel = (f.get('game') or '').strip()
    m = re.fullmatch(r'([a-z0-9-]+)/([a-z0-9-]+)', gsel)
    if not m:
        return fail('game must be system/slug; create the game first at '
                    '/create-game/ if it is not archived yet')
    system, slug = m.groups()
    game, cats = load_game(system, slug)
    if not game:
        return fail(f'unknown game {system}/{slug}; create it first at /create-game/')

    goal = (f.get('goal') or '').strip()
    goal_description = ''
    dim_keys = {}
    if goal == 'unclassified':
        # special category on every game: no defined goal, the run describes
        # its own; never verifiable, ranked by likes alone
        goal_description = (f.get('goal_description') or '').strip()[:200]
        if not goal_description:
            return fail('Unclassified runs must describe their goal '
                        '(goal_description); it is shown in the ranking')
        dim_keys = {'goal': 'unclassified'}
    for d in cats['dimensions']:
        if goal in {o['key'] for o in d['options']}:
            dim_keys[d['key']] = goal
    if not dim_keys:
        return fail(f'unknown category {goal!r} for {system}/{slug}; create it '
                    f'first from the game page')

    # --- the category's metrics decide what the submitter must state ---
    goal_opt = next((o for d in cats['dimensions'] for o in d['options']
                     if o['key'] == goal), None)
    metric_defs = (goal_opt or {}).get('metrics')
    wants_time = metric_defs is None or any(mm['key'] == 'time'
                                            for mm in metric_defs)
    stated_metrics = {}
    for mm in (metric_defs or []):
        if mm['key'] == 'time':
            continue                    # derived for movies, stated via `time`
        raw = (f.get(f'metric_{mm["key"]}') or '').strip()
        if raw == '':
            return fail(f'this category ranks by {mm["label"]}: state its '
                        f'value (metric_{mm["key"]})')
        try:
            val = float(raw)
        except ValueError:
            return fail(f'{mm["label"]} must be a number (seconds for times)')
        if val < 0:
            return fail(f'{mm["label"]} cannot be negative')
        stated_metrics[mm['key']] = val

    authors = [a.strip() for a in (f.get('authors') or '').split(',') if a.strip()]
    if not authors:
        return fail('at least one author required')

    # --- the movie, or the statement that there is none ---
    # A video-only run has no input movie: the encode IS the run. It can never
    # be reproduced, in emulator or on console, and it says so; verification
    # still gates its ranking like any other run's. The submitter states the
    # time, since there are no frames to derive it from.
    video_only = (f.get('video_only') or '').strip() in ('1', 'true', 'yes', 'on')
    mov = request.files.get('movie')
    if video_only:
        if mov and mov.filename:
            return fail('you attached a movie file and called the run video-only; '
                        'pick one')
        duration = None
        if wants_time and goal != 'unclassified':
            # the category ranks by time and there are no frames to derive it
            # from, so the submitter states it
            stated = (f.get('time') or '').strip()
            m_t = re.fullmatch(r'(?:(\d{1,3}):)?(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?', stated)
            if not m_t:
                return fail('a video-only run in a time-ranked category needs its '
                            'time, stated as [h:]mm:ss or [h:]mm:ss.mmm')
            h, mnt, sec, frac = m_t.groups()
            duration = (int(h or 0) * 3600 + int(mnt) * 60 + int(sec)
                        + (int(frac.ljust(3, "0")) / 1000 if frac else 0.0))
            if duration <= 0:
                return fail('a run that takes no time at all is not a run')
        ext = None
        movie_bytes = b''
        movie_sha1 = None
        parsed = {'frames': None, 'rerecords': None, 'start': None, 'fps': None}
    else:
        if not mov or not mov.filename:
            return fail('movie file required, or mark the run video-only and state '
                        'its time')
        duration = None
        ext = mov.filename.rsplit('.', 1)[-1].lower()
        if ext not in MOVIE_EXTS:
            return fail(f'movie extension .{ext} not a known TAS format')
        movie_bytes = mov.read()
        if len(movie_bytes) > MOVIE_MAX:
            return fail('movie exceeds 16 MB')
        if not movie_bytes:
            return fail('movie file is empty')
        parsed = movieparse.parse(mov.filename, movie_bytes)
        if not parsed['ok']:
            return fail(f'movie did not parse as .{ext}: {parsed["error"]}')
        movie_sha1 = hashlib.sha1(movie_bytes).hexdigest()

    # --- encode (mandatory) + thumbnail derived from it ---
    # The encode is validated here and the run's thumbnail is a frame of it
    # (maxres, falling back to hq) — no author upload, nothing to moderate.
    encode = (f.get('encode') or '').strip()
    enc = providers.resolve(encode)
    if not enc:
        return fail('an encode link is required, from one of: '
                    + ', '.join(providers.names())
                    + ' (the run thumbnail is derived from it)')
    thumb_bytes, thumb_ext = providers.thumbnail(enc['kind'], enc['id'], THUMB_MAX)
    if not thumb_bytes:
        return fail(f'the encode link does not resolve to a watchable '
                    f'{enc["name"]} video; check the URL (the run thumbnail is '
                    f'derived from it)')

    # --- attachments: text configs, or additional movie files ---
    atts, att_err = read_attachments()
    if att_err:
        return att_err

    completed = (f.get('completed') or '').strip()
    if completed:
        if not re.fullmatch(r'(19[89]\d|20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])',
                            completed):
            return fail('completed must be a date like 2021-10-26')
        if completed > time.strftime('%Y-%m-%d', time.gmtime()):
            return fail('completed cannot be in the future')

    notes = (f.get('notes') or '').strip()
    if len(notes.encode()) > NOTES_MAX:
        return fail('notes exceed 256 KB')

    # voluntary content disclosures — separate flags, shown on the run page
    CW_ALLOWED = {'mature-violence', 'sexual', 'photosensitivity', 'strong-language'}
    content_warnings = [w for w in request.form.getlist('content_warnings')]
    if any(w not in CW_ALLOWED for w in content_warnings):
        return fail('unknown content warning flag')

    rom = {}
    if f.get('rom_name'): rom['name'] = f.get('rom_name').strip()
    if re.fullmatch(r'[0-9a-fA-F]{40}', (f.get('rom_sha1') or '').strip()):
        rom['sha1'] = f.get('rom_sha1').strip().lower()

    dry = f.get('dry_run') in ('1', 'true', 'yes')

    with lock:
        checkout_branch()
        # refuse a movie the archive already holds: the same bytes (a double
        # click, or a run already imported from TASVideos) or the same work
        # saved again. Checked under the lock, against the fresh checkout.
        if video_only:
            # no bytes to compare, so the encode is the fingerprint: the same
            # video twice is the same run twice
            for rj_ in ARCHIVE.glob('games/*/*/runs/*/run.json'):
                doc_ = json.loads(rj_.read_text())
                if any(e.get('url') == encode for e in doc_.get('encodes', [])):
                    return fail(f'this video is already archived as '
                                f'{doc_["id"]}: the encode is the run, and it is '
                                f'the same encode', 409)
        else:
            dup_id, why = duplicate_of(movie_sha1, f'{system}/{slug}',
                                       dim_keys.get('goal'), parsed['frames'], authors)
            if dup_id:
                return fail(f'this run is already archived as {dup_id}: it has {why}. '
                            f'If it is an improvement, submit the faster movie; if the '
                            f'archived one is wrong, its authors can edit it.', 409)
        rid = next_id()
        run_id = f'M{rid}'
        rdir = ARCHIVE / 'games' / system / slug / 'runs' / run_id
        run = {
            'id': run_id, 'game': f'{system}/{slug}', 'category': dim_keys,
            'authors': [{'user': a} for a in authors],
            'tools': [],
            **({'metrics': stated_metrics} if stated_metrics else {}),
            **({'videoOnly': True,
                **({'duration': duration} if duration else {})} if video_only else
               {'movie': {'file': f'{run_id}.{ext}', 'format': ext, 'sha1': movie_sha1,
                          'frames': parsed['frames'],
                          'rerecords': parsed['rerecords'],
                          'start': parsed['start'],
                          **({'fps': parsed['fps']} if parsed.get('fps') else {})}}),
            'thumbnail': 'thumb' + thumb_ext,
            **({'goalDescription': goal_description} if goal_description else {}),
            **({'contentWarnings': content_warnings} if content_warnings else {}),
            'contract': {'emulator': (f.get('emulator') or '').strip(), **({'rom': rom} if rom else {})},
            'status': ({'reproduced': 'not-applicable', 'verified': 'none',
                        'console': 'not-applicable'} if video_only else
                       {'reproduced': 'none', 'verified': 'none', 'console': 'none'}),
            'encodes': [{'kind': enc['kind'], 'url': encode}],
            'attachments': [{'file': f'attachments/{n}', 'role': 'submitted attachment'} for n, _ in atts],
            **({'completed': completed} if completed else {}),
            'submitted': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'submittedBy': submitter,
        }
        if dry:
            return jsonify({'ok': True, 'dry_run': True, 'would_be': run_id, 'run': run,
                            'game_key': f'{system}/{slug}'})
        ensure_member(submitter)
        rdir.mkdir(parents=True)
        if not video_only:
            (rdir / f'{run_id}.{ext}').write_bytes(movie_bytes)
        (rdir / ('thumb' + thumb_ext)).write_bytes(thumb_bytes)
        (rdir / 'run.json').write_text(json.dumps(run, indent=1))
        if notes:
            (rdir / 'notes.md').write_text(notes + '\n')
        if atts:
            (rdir / 'attachments').mkdir()
            for n, data in atts:
                (rdir / 'attachments' / n).write_bytes(data)

        title = f"New run archived: {game['title']} ({goal}) by {', '.join(authors)}"
        # topics BEFORE the push: written after it, the pointers sat only in
        # the working tree and the next request's hard reset erased them,
        # leaving orphan topics and run pages with no visible discussion
        ensure_game_topic(system, slug, game['title'])
        if ensure_topic(run, game['title'], system, slug, goal, authors):
            (rdir / 'run.json').write_text(json.dumps(run, indent=1))
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

@app.post('/api/visit')
def visit():
    rid = (request.form.get('run') or '').strip()
    if not re.fullmatch(r'M[0-9]+', rid):
        return fail('run must be an id like M100001')
    if not find_run(rid):
        return fail(f'unknown run {rid}', 404)
    with _visits_lock:
        _visits[rid] = _visits.get(rid, 0) + 1
        n = _visits[rid]
        try:
            VISITS_FILE.write_text(json.dumps(_visits))
        except OSError as exc:
            LOG.warning('visits file not writable: %s', exc)
    return jsonify({'ok': True, 'run': rid, 'visits': n})

@app.post('/api/reproduce')
def reproduce():
    """Record a community reproduction: mandatory ending screenshot as proof."""
    f = request.form
    dry = f.get('dry_run') in ('1', 'true', 'yes')
    with lock:
        err0 = auth_precheck(f)
        if err0:
            return err0
        if not dry:
            checkout_branch()
        err_resp, rdir, r, user = act_common(f)
        if err_resp:
            return err_resp
        if r.get('videoOnly'):
            return fail('this run is video-only: there is no input movie to '
                        'replay, so reproduction does not apply')
        if user.lower() in {a['user'].lower() for a in r.get('reproductions', [])}:
            return fail('you have already reproduced this run; one reproduction per member')

        shot = request.files.get('screenshot')
        if not shot or not shot.filename:
            return fail('an ending screenshot is required as proof of sync')
        ext = pathlib.Path(shot.filename).suffix.lower()
        if ext not in IMAGE_MAGIC:
            return fail('screenshot must be png, jpg or webp')
        data = shot.read()
        if len(data) > SHOT_MAX_EACH:
            return fail('screenshot exceeds 512 KB')
        if not any(data.startswith(m) for m in IMAGE_MAGIC[ext]):
            return fail(f'screenshot is not a real {ext} image')
        existing = sum(sp.stat().st_size for sp in (rdir / 'reproductions').glob('*')
                       if sp.is_file()) if (rdir / 'reproductions').exists() else 0
        if existing + len(data) > SHOT_MAX_TOTAL:
            return fail('this run has reached its screenshot storage cap')

        n = len(r.get('reproductions', [])) + 1
        shot_rel = f'reproductions/{n}-{user}{ext}'
        entry = {'user': user, 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(),
                 'screenshot': shot_rel}
        if (f.get('emulator') or '').strip():
            entry['emulator'] = f.get('emulator').strip()[:120]
        if (f.get('notes') or '').strip():
            entry['notes'] = f.get('notes').strip()
        r.setdefault('reproductions', []).append(entry)
        sync_status(r)
        if dry:
            return jsonify({'ok': True, 'dry_run': True, 'would_record': entry,
                            'status': r['status']})

        (rdir / 'reproductions').mkdir(exist_ok=True)
        (rdir / shot_rel).write_bytes(data)
        (rdir / 'run.json').write_text(json.dumps(
            {k: v for k, v in r.items() if not k.startswith('_')}, indent=1))
        ensure_member(user)
        commit_push(f'Reproduce {r["id"]}: by {user}\n\nVia: archivist')
        notify_discord(f'\u21bb **{member_md(user)}** reproduced ' + movie_md(r),
                       wait_for=f'{SITE_URL}/runs/{r["id"]}/')
    return jsonify({'ok': True, 'run': r['id'], 'status': r['status'],
                    'reproductions': len([a for a in r['reproductions'] if not a.get('invalidated')])})

@app.post('/api/invalidate')
def invalidate():
    """An expert invalidates a faulty reproduction/verification — a logged,
    appealable moderation act, never automatic. The run recomputes and the act
    can be redone by anyone else."""
    f = request.form
    dry = f.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        err0 = auth_precheck(f)
        if err0:
            return err0
        if not dry:
            checkout_branch()
        expert, err_ = request_identity(f, 'expert')
        if err_:
            return err_
        run_id = (f.get('run') or '').strip()
        rdir = find_run(run_id) if re.fullmatch(r'M[0-9]+', run_id) else None
        if not rdir:
            return fail(f'unknown run {run_id}', 404)
        r = json.loads((rdir / 'run.json').read_text())
        if r.get('status', {}).get('reproduced') == 'imported':
            return fail('Imported runs are irrevocably verified; no further acts apply')
        game_key = r['game']
        if not expert_covers(expert, game_key):
            return fail(f'{expert!r} is not an expert covering {game_key}', 403)
        kind = (f.get('kind') or '').strip()
        # console verification lives in its own roster, hence the mapping
        ROSTER = {'reproduction': 'reproductions', 'verification': 'verifications',
                  'console': 'consoleVerifications'}
        if kind not in ROSTER:
            return fail('kind must be reproduction, verification or console')
        target = (f.get('target') or '').strip()
        reason = (f.get('reason') or '').strip()
        if not reason:
            return fail('an invalidation must state its reason; it is logged in the open')
        if len(reason) > ACT_NOTES_MAX:
            return fail(f'reason exceeds {ACT_NOTES_MAX} characters')
        acts = r.get(ROSTER[kind], [])
        act = next((a for a in acts if a['user'].lower() == target.lower()
                    and not a.get('invalidated')), None)
        if not act:
            return fail(f'no live {kind} by {target!r} on {run_id}', 404)
        act['invalidated'] = {'by': expert, 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(),
                              'reason': reason}
        sync_status(r)
        if dry:
            return jsonify({'ok': True, 'dry_run': True, 'would_invalidate': act,
                            'status': r['status']})
        (rdir / 'run.json').write_text(json.dumps(
            {k: v for k, v in r.items() if not k.startswith('_')}, indent=1))
        ensure_member(expert)
        commit_push(f'Invalidate {kind} on {run_id}: {target} by expert {expert}\n\n'
                    f'Reason: {reason}\nVia: archivist')
    return jsonify({'ok': True, 'run': run_id, 'status': r['status'],
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
    out, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            return None, 'each metric is an object'
        if row.get('key') == 'time':
            m = {'key': 'time', 'label': str(row.get('label') or 'Time')[:40],
                 'type': 'time', 'better': 'lower'}
        else:
            label = str(row.get('label') or '').strip()[:40]
            if not label:
                return None, 'a metric needs a label'
            key = slugify(str(row.get('key') or label))
            if not key or key == 'unclassified':
                return None, f'bad metric key for {label!r}'
            mtype = row.get('type')
            better = row.get('better')
            if mtype not in ('time', 'number'):
                return None, f'{label}: type must be time or number'
            if better not in ('lower', 'higher'):
                return None, f'{label}: better must be lower or higher'
            m = {'key': key, 'label': label, 'type': mtype, 'better': better}
            unit = str(row.get('unit') or '').strip()[:12]
            if unit:
                m['unit'] = unit
        if m['key'] in seen:
            return None, f'duplicate metric key {m["key"]!r}'
        seen.add(m['key'])
        out.append(m)
    return (out or None), None


def _category_gate(f, need_expert=True):
    """Shared by the category endpoints: who is asking, over which game, and
    the game's categories document. Creation is everybody's; everything else
    needs a covering expert. Returns (actor, game_key, cfile, cats, error)."""
    actor, err_ = request_identity(f, 'expert' if need_expert else 'user')
    if err_:
        return None, None, None, None, err_
    game_key = (f.get('game') or '').strip()
    if not re.fullmatch(r'[a-z0-9-]+/[a-z0-9-]+', game_key):
        return None, None, None, None, fail('game must be system/slug')
    cfile = ARCHIVE / 'games' / game_key / 'categories.json'
    if not cfile.exists():
        return None, None, None, None, fail(f'unknown game {game_key}', 404)
    if need_expert and not expert_covers(actor, game_key) \
            and not is_editor(actor):
        return None, None, None, None, fail(
            f'{actor!r} is not an expert covering {game_key}, nor an editor', 403)
    return actor, game_key, cfile, json.loads(cfile.read_text()), None


@app.get('/api/categories')
def categories_of_game():
    """A game's category definitions, fresh from the checkout (refreshed at
    most 20 s old). The submit form asks here instead of the raw-file CDN,
    whose 5-minute cache showed a renamed category under its old label."""
    game_key = (request.args.get('game') or '').strip()
    if not re.fullmatch(r'[a-z0-9-]+/[a-z0-9-]+', game_key):
        return fail('game must be system/slug')
    refresh_archive()
    cfile = ARCHIVE / 'games' / game_key / 'categories.json'
    if not cfile.exists():
        return fail(f'unknown game {game_key}', 404)
    resp = jsonify({'ok': True, **json.loads(cfile.read_text())})
    resp.headers['Cache-Control'] = 'no-store'
    return resp

@app.post('/api/category/add')
def category_add():
    """Any member adds a category (creation is everybody's; only experts
    edit what exists). The creator defines its metrics; the edit log carries
    the act."""
    f = request.form
    dry = f.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        err0 = auth_precheck(f)
        if err0:
            return err0
        if not dry:
            checkout_branch()
        expert, game_key, cfile, cats, err_ = _category_gate(f, need_expert=False)
        if err_:
            return err_
        mdefs, merr = parse_metric_defs(f.get('metrics'))
        if merr:
            return fail(merr)
        label = (f.get('label') or '').strip()
        rule = (f.get('rule') or '').strip()
        if not (1 <= len(label) <= 80):
            return fail('a label fits in 80 characters')
        if not (1 <= len(rule) <= 500):
            return fail('a rule fits in 500 characters; it is what a verifier '
                        'holds a run to')
        # 'key' is the submitter-key auth field; the option key travels as
        # option_key (the same collision removal/decide once had)
        okey = slugify((f.get('option_key') or label).strip())
        if not okey:
            return fail('the label yields an empty key')
        if okey == 'unclassified':
            return fail('unclassified is reserved: every game already has it')
        dim = next((d for d in cats['dimensions'] if d['key'] == 'goal'),
                   cats['dimensions'][0] if cats['dimensions'] else None)
        if dim is None:
            cats['dimensions'] = [{'key': 'goal', 'name': 'Category', 'options': []}]
            dim = cats['dimensions'][0]
        if any(o['key'] == okey for d in cats['dimensions'] for o in d['options']):
            return fail(f'{okey!r} already exists on this game', 409)
        if dry:
            return jsonify({'ok': True, 'dry_run': True, 'key': okey})
        dim['options'].append({'key': okey, 'label': label, 'rule': rule,
                               **({'metrics': mdefs} if mdefs else {})})
        cfile.write_text(json.dumps(cats, indent=1) + '\n')
        log_edit('category', f'{game_key}:{okey}', 'added', '', label, expert,
                 (f.get('reason') or 'Created it.').strip()[:500])
        ensure_member(expert)
        gtitle = json.loads((ARCHIVE / 'games' / game_key / 'game.json')
                            .read_text()).get('title', game_key)
        commit_push(f'Category add {game_key}:{okey}: by {expert}\n\n'
                    f'Label: {label}\nVia: archivist')
        notify_discord(f'\U0001f5c2\ufe0f **{member_md(expert)}** created the category '
                       f'[{label}](<{SITE_URL}/games/{game_key}/>) in '
                       f'[[{game_key.split("/")[0].upper()}] {gtitle}]'
                       f'(<{SITE_URL}/games/{game_key}/>)',
                       wait_for=f'{SITE_URL}/games/{game_key}/')
    return jsonify({'ok': True, 'game': game_key, 'key': okey, 'label': label})




@app.post('/api/category/delete')
def category_delete():
    """Remove an option no run has ever used. Anything referenced stays: a
    category with runs in it is the runs' home, not clutter."""
    f = request.form
    dry = f.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        err0 = auth_precheck(f)
        if err0:
            return err0
        if not dry:
            checkout_branch()
        expert, game_key, cfile, cats, err_ = _category_gate(f)
        if err_:
            return err_
        okey = (f.get('option') or '').strip()
        opt = next((o for d in cats['dimensions'] for o in d['options']
                    if o['key'] == okey), None)
        if not opt:
            return fail(f'{game_key} defines no category {okey!r}', 404)
        users = [json.loads(rj.read_text())['id']
                 for rj in (ARCHIVE / 'games' / game_key / 'runs').glob('*/run.json')
                 if (json.loads(rj.read_text()).get('category') or {}).get('goal') == okey]
        if users:
            return fail(f'{okey!r} holds {len(users)} run(s) ({", ".join(users[:4])}'
                        f'{"…" if len(users) > 4 else ""}); a category with runs '
                        f'in it is their home, not clutter', 409)
        if dry:
            return jsonify({'ok': True, 'dry_run': True})
        for d in cats['dimensions']:
            d['options'] = [o for o in d['options'] if o['key'] != okey]
        cfile.write_text(json.dumps(cats, indent=1) + '\n')
        log_edit('category', f'{game_key}:{okey}', 'removed', opt.get('label', okey),
                 '', expert,
                 (f.get('reason') or 'Removed unused by a covering expert.').strip()[:500])
        ensure_member(expert)
        commit_push(f'Category remove {game_key}:{okey}: by expert {expert}\n\n'
                    f'Via: archivist')
    return jsonify({'ok': True, 'game': game_key, 'removed': okey})



def _deletion_gate(f, need='expert'):
    """Common to every delete: who is asking, and why, said properly."""
    actor, err_ = request_identity(f, 'expert')
    if err_:
        return None, None, err_
    reason = (f.get('reason') or '').strip()
    if not (8 <= len(reason) <= 500):
        return None, None, fail('say why, publicly: a deletion is permanent and the '
                                'log entry is all that remains of it')
    return actor, reason, None

EXPERT_EDITABLE = {'run': ('duration', 'goal', 'encode', 'goalDescription',
                           'notes', 'movie'),
                   'game': ('title', 'thumbnail'),
                   'category': ('label', 'rule', 'metrics'),
                   'group': ('title',)}

@app.post('/api/expert/edit')
def expert_edit():
    """An expert corrects the record inside their jurisdiction, field by field,
    each change logged with who, from, to, and why."""
    f = request.form
    dry = f.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        err0 = auth_precheck(f)
        if err0:
            return err0
        actor, err_ = request_identity(f, 'expert')
        if err_:
            return err_
        kind = (f.get('kind') or '').strip()
        key = (f.get('target') or '').strip()
        field = (f.get('field') or '').strip()
        value = (f.get('value') or '').strip()
        reason = (f.get('reason') or '').strip()
        if kind not in EXPERT_EDITABLE:
            return fail('kind must be run, game, category or group')
        if field not in EXPERT_EDITABLE[kind] and not (
                kind == 'run' and field.startswith('metric:')):
            return fail(f'{field!r} is not expert-editable on a {kind}; the record '
                        f'allows: {", ".join(EXPERT_EDITABLE[kind])}. Member content '
                        f'is never edited by anybody but its author.')
        if not (8 <= len(reason) <= 500):
            return fail('say why, publicly: the edit log carries your reason')
        if not dry:
            checkout_branch()

        if kind == 'run':
            if not re.fullmatch(r'M[0-9]+', key):
                return fail('target must be a run id like M100001')
            rdir = find_run(key)
            if not rdir:
                return fail(f'unknown run {key}', 404)
            game_key = f'{rdir.parent.parent.parent.name}/{rdir.parent.parent.name}'
            if not expert_covers(actor, game_key):
                # an editor shapes the library, not the runs: the one run
                # field that is library shape is which category it sits in
                if not (is_editor(actor) and field == 'goal'):
                    return fail(f'{actor!r} is not an expert covering {game_key}'
                                f' (an editor may only move a run between '
                                f'categories)', 403)
            r = json.loads((rdir / 'run.json').read_text())
            if field.startswith('metric:'):
                mkey = field.split(':', 1)[1]
                try:
                    new_v = float(value)
                except ValueError:
                    return fail('value must be a number (seconds for times)')
                if new_v < 0:
                    return fail('a metric value cannot be negative')
                old_v = (r.get('metrics') or {}).get(mkey, 0)
                r.setdefault('metrics', {})[mkey] = new_v
                value = str(new_v)
            elif field == 'duration':
                if not r.get('videoOnly'):
                    return fail('only a video-only run has a stated time to correct; '
                                'a movie derives its time from its frames')
                m_t = re.fullmatch(r'(?:(\d{1,3}):)?(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?',
                                   value)
                if not m_t:
                    return fail('value must be a time, [h:]mm:ss or [h:]mm:ss.mmm')
                h, mnt, sec, frac = m_t.groups()
                new_v = (int(h or 0) * 3600 + int(mnt) * 60 + int(sec)
                         + (int(frac.ljust(3, "0")) / 1000 if frac else 0.0))
                if new_v <= 0:
                    return fail('a run that takes no time at all is not a run')
                old_v = r.get('duration')
                r['duration'] = new_v
            elif field == 'goal':
                cats = json.loads((rdir.parent.parent / 'categories.json').read_text())
                valid = {o['key'] for d in cats['dimensions'] for o in d['options']}
                valid.add('unclassified')
                if value not in valid:
                    return fail(f'{value!r} is not a goal this game defines')
                if value == 'unclassified' and any(
                        not v.get('invalidated') for v in r.get('verifications', [])):
                    return fail('this run holds live verifications, which are bound '
                                'to its goal; unclassifying it would void them, and '
                                'that is not an edit')
                old_v = (r.get('category') or {}).get('goal')
                if old_v == value:
                    return fail('that is already its goal')
                r.setdefault('category', {})['goal'] = value
            elif field == 'encode':
                enc = providers.resolve(value)
                if not enc:
                    return fail('value must be a watchable encode URL on a platform '
                                'we accept')
                old_v = (r.get('encodes') or [{}])[0].get('url', '')
                r['encodes'] = [{'kind': enc['kind'], 'url': value}]
            elif field == 'goalDescription':
                if len(value) > 500:
                    return fail('a goal description fits in 500 characters')
                old_v = r.get('goalDescription', '')
                if value:
                    r['goalDescription'] = value
                else:
                    r.pop('goalDescription', None)
                if is_uncl_run(r) and not value:
                    return fail('an Unclassified run states its own goal; it cannot '
                                'lose its description')
            elif field == 'notes':
                if len(value.encode()) > 64 * 1024:
                    return fail('notes fit in 64 KB')
                nfile = rdir / 'notes.md'
                old_v = (nfile.read_text()[:300] if nfile.exists() else '')
                if dry:
                    return jsonify({'ok': True, 'dry_run': True, 'field': field,
                                    'from': old_v, 'to': value[:300]})
                if value:
                    nfile.write_text(value + ('\n' if not value.endswith('\n') else ''))
                elif nfile.exists():
                    nfile.unlink()
            elif field == 'movie':
                if r.get('videoOnly'):
                    return fail('a video-only run has no movie file to replace')
                newmov = request.files.get('movie')
                if not newmov or not newmov.filename:
                    return fail('attach the replacement movie file')
                mext = newmov.filename.rsplit('.', 1)[-1].lower()
                if mext not in MOVIE_EXTS:
                    return fail(f'movie extension .{mext} not a known TAS format')
                mbytes = newmov.read()
                if not mbytes or len(mbytes) > MOVIE_MAX:
                    return fail('movie must be non-empty and under 16 MB')
                mparsed = movieparse.parse(newmov.filename, mbytes)
                if not mparsed['ok']:
                    return fail(f'movie did not parse as .{mext}: {mparsed["error"]}')
                old_v = f"{r['movie']['file']} (sha1 {r['movie'].get('sha1', '?')[:12]})"
                value = f"{r['id']}.{mext} (sha1 {hashlib.sha1(mbytes).hexdigest()[:12]})"
                if dry:
                    return jsonify({'ok': True, 'dry_run': True, 'field': field,
                                    'from': old_v, 'to': value})
                (rdir / r['movie']['file']).unlink(missing_ok=True)
                (rdir / f"{r['id']}.{mext}").write_bytes(mbytes)
                r['movie'] = {'file': f"{r['id']}.{mext}", 'format': mext,
                              'sha1': hashlib.sha1(mbytes).hexdigest(),
                              'frames': mparsed['frames'],
                              'rerecords': mparsed['rerecords'],
                              'start': mparsed['start'],
                              **({'fps': mparsed['fps']} if mparsed.get('fps') else {})}
            if dry:
                return jsonify({'ok': True, 'dry_run': True, 'field': field,
                                'from': old_v, 'to': value})
            (rdir / 'run.json').write_text(json.dumps(
                {k: v for k, v in r.items() if not k.startswith('_')}, indent=1))
            log_edit('run', key, field, old_v, value, actor, reason)

        elif kind == 'game':
            m = re.fullmatch(r'([a-z0-9-]+)/([a-z0-9-]+)', key)
            if not m:
                return fail('target must be system/slug')
            gfile = ARCHIVE / 'games' / key / 'game.json'
            if not gfile.exists():
                return fail(f'unknown game {key}', 404)
            if not expert_covers(actor, key) and not is_editor(actor):
                return fail(f'{actor!r} is not an expert covering {key}, '
                            f'nor an editor', 403)
            game = json.loads(gfile.read_text())
            if field == 'title':
                if not (1 <= len(value) <= 120):
                    return fail('a title fits in 120 characters')
                old_v = game.get('title')
                if old_v == value:
                    return fail('that is already its title')
                game['title'] = value
            else:
                shot = request.files.get('thumbnail')
                if not shot or not shot.filename:
                    return fail('attach the thumbnail image')
                fext = pathlib.Path(shot.filename).suffix.lower()
                sext = '.jpg' if fext == '.jpeg' else fext
                if sext not in IMAGE_MAGIC:
                    return fail('thumbnail must be png, jpg or webp')
                data = shot.read()
                if not data or len(data) > THUMB_MAX:
                    return fail(f'thumbnail must be non-empty and under '
                                f'{THUMB_MAX >> 10} KB')
                if not any(data.startswith(m_) for m_ in IMAGE_MAGIC[sext]):
                    return fail('that file is not the image its name claims')
                old_v = game.get('thumbnail', '')
                value = f'thumb{sext}'
                if dry:
                    return jsonify({'ok': True, 'dry_run': True, 'field': field,
                                    'from': old_v, 'to': value})
                if old_v:
                    (ARCHIVE / 'games' / key / old_v).unlink(missing_ok=True)
                (ARCHIVE / 'games' / key / value).write_bytes(data)
                game['thumbnail'] = value
            if dry:
                return jsonify({'ok': True, 'dry_run': True, 'field': field,
                                'from': old_v, 'to': value})
            gfile.write_text(json.dumps(game, indent=1) + '\n')
            log_edit('game', key, field, old_v, value, actor, reason)

        elif kind == 'category':
            m = re.fullmatch(r'([a-z0-9-]+/[a-z0-9-]+):([a-z0-9-]+)', key)
            if not m:
                return fail('target must be system/slug:option')
            game_key, okey = m.group(1), m.group(2)
            cfile = ARCHIVE / 'games' / game_key / 'categories.json'
            if not cfile.exists():
                return fail(f'unknown game {game_key}', 404)
            if not expert_covers(actor, game_key) and not is_editor(actor):
                return fail(f'{actor!r} is not an expert covering {game_key}, '
                            f'nor an editor', 403)
            cats = json.loads(cfile.read_text())
            opt = next((o for d in cats['dimensions'] for o in d['options']
                        if o['key'] == okey), None)
            if not opt:
                return fail(f'{game_key} defines no category {okey!r}', 404)
            if field == 'metrics':
                mdefs, merr = parse_metric_defs(value)
                if merr:
                    return fail(merr)
                old_defs = opt.get('metrics')
                old_v = json.dumps(old_defs) if old_defs else '(classic: time)'
                new_v = json.dumps(mdefs) if mdefs else '(classic: time)'
                if old_v == new_v:
                    return fail('that is already its metric definition')
                if dry:
                    return jsonify({'ok': True, 'dry_run': True, 'field': field,
                                    'from': old_v, 'to': new_v})
                if mdefs:
                    opt['metrics'] = mdefs
                else:
                    opt.pop('metrics', None)
                cfile.write_text(json.dumps(cats, indent=1) + '\n')
                # a freshly added metric writes the explicit empty value onto
                # every run already in the category: nothing gets unranked,
                # zeros rank last, and the experts fill them in from here
                old_keys = {m['key'] for m in (old_defs or [])}
                fresh = [m['key'] for m in (mdefs or [])
                         if m['key'] != 'time' and m['key'] not in old_keys]
                touched = 0
                if fresh:
                    for rj in (ARCHIVE / 'games' / game_key / 'runs').glob('*/run.json'):
                        rr = json.loads(rj.read_text())
                        if (rr.get('category') or {}).get('goal') != okey:
                            continue
                        for kf in fresh:
                            rr.setdefault('metrics', {}).setdefault(kf, 0)
                        rj.write_text(json.dumps(rr, indent=1) + '\n')
                        touched += 1
                log_edit('category', key, field, old_v[:300], new_v[:300],
                         actor, reason)
                ensure_member(actor)
                commit_push(f'Expert edit category {key}: metrics\n\n'
                            f'By: {actor}\nReason: {reason}\n'
                            f'Runs seeded with empty values: {touched}\n'
                            f'Via: archivist')
                return jsonify({'ok': True, 'kind': kind, 'key': key,
                                'field': field, 'from': old_v, 'to': new_v,
                                'runs_seeded': touched})
            limit = 80 if field == 'label' else 500
            if not (1 <= len(value) <= limit):
                return fail(f'a {field} fits in {limit} characters')
            old_v = opt.get(field, '')
            if old_v == value:
                return fail(f'that is already its {field}')
            opt[field] = value
            if dry:
                return jsonify({'ok': True, 'dry_run': True, 'field': field,
                                'from': old_v, 'to': value})
            cfile.write_text(json.dumps(cats, indent=1) + '\n')
            log_edit('category', key, field, old_v, value, actor, reason)

        else:
            doc = load_groups()
            gr = next((g for g in doc['groups'] if g['key'] == key.lower()), None)
            if not gr:
                return fail(f'no group with the key {key!r}', 404)
            if not covers_group(actor, gr) and not is_editor(actor):
                return fail(f'{actor} holds no scope covering the {gr["title"]} group',
                            403)
            if not (1 <= len(value) <= 80):
                return fail('a title fits in 80 characters')
            old_v = gr.get('title')
            if old_v == value:
                return fail('that is already its title')
            gr['title'] = value
            if dry:
                return jsonify({'ok': True, 'dry_run': True, 'field': field,
                                'from': old_v, 'to': value})
            save_groups(doc)
            log_edit('group', key.lower(), field, old_v, value, actor, reason)

        ensure_member(actor)
        commit_push(f'Expert edit {kind} {key}: {field}\n\n'
                    f'From: {str(old_v)[:120]}\nTo: {value[:120]}\n'
                    f'By: {actor}\nReason: {reason}\nVia: archivist')
    return jsonify({'ok': True, 'kind': kind, 'key': key, 'field': field,
                    'from': old_v, 'to': value})

@app.post('/api/run/delete')
def run_delete():
    """An expert deletes a movie outright: tests, spam, non-TAS, mistakes.

    This is the fast lane beside withdrawal (which keeps a tombstone) and
    all-author erasure (Terms 3.1). It exists for things that were never
    really works; the reason is public and permanent even though the run is
    neither.
    """
    f = request.form
    dry = f.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        err0 = auth_precheck(f)
        if err0:
            return err0
        actor, reason, err_ = _deletion_gate(f)
        if err_:
            return err_
        run_id = (f.get('run') or '').strip()
        rdir = find_run(run_id) if re.fullmatch(r'M[0-9]+', run_id) else None
        if not rdir:
            return fail(f'unknown run {run_id}', 404)
        game_key = f'{rdir.parent.parent.parent.name}/{rdir.parent.parent.name}'
        if not expert_covers(actor, game_key):
            return fail(f'{actor!r} is not an expert covering {game_key}', 403)
        r = json.loads((rdir / 'run.json').read_text())
        title = f'{game_key} ({(r.get("category") or {}).get("goal", "?")})'
        if dry:
            return jsonify({'ok': True, 'dry_run': True, 'would_delete': run_id,
                            'game': game_key})
        checkout_branch()
        rdir = find_run(run_id)
        if not rdir:
            return fail(f'unknown run {run_id}', 404)
        shutil.rmtree(rdir)
        log_deletion('run', run_id, title, actor, reason)
        ensure_member(actor)
        commit_push(f'Delete {run_id}: by expert {actor}\n\nReason: {reason}\nVia: archivist')
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
    """
    f = request.form
    dry = f.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        err0 = auth_precheck(f)
        if err0:
            return err0
        actor, reason, err_ = _deletion_gate(f)
        if err_:
            return err_
        m = re.fullmatch(r'([a-z0-9-]+)/([a-z0-9-]+)', (f.get('game') or '').strip())
        if not m:
            return fail('game must be system/slug')
        game_key = m.group(0)
        system, slug = m.groups()
        gdir = ARCHIVE / 'games' / system / slug
        if not (gdir / 'game.json').exists():
            return fail(f'unknown game {game_key}', 404)
        if not expert_covers(actor, game_key):
            return fail(f'{actor!r} is not an expert covering {game_key}', 403)
        game = json.loads((gdir / 'game.json').read_text())
        run_dirs = sorted(d for d in (gdir / 'runs').glob('M*') if d.is_dir())
        if dry:
            return jsonify({'ok': True, 'dry_run': True, 'would_delete': game_key,
                            'runs_deleted': [d.name for d in run_dirs]})
        checkout_branch()
        gdir = ARCHIVE / 'games' / system / slug
        if not (gdir / 'game.json').exists():
            return fail(f'unknown game {game_key}', 404)
        run_dirs = sorted(d for d in (gdir / 'runs').glob('M*') if d.is_dir())
        deleted_runs = []
        for d in run_dirs:
            try:
                rdoc = json.loads((d / 'run.json').read_text())
                rtitle = f'{game.get("title", game_key)} ' \
                         f'({(rdoc.get("category") or {}).get("goal", "?")})'
            except Exception:                                 # noqa: BLE001
                rtitle = game.get('title', game_key)
            log_deletion('run', d.name, rtitle, actor,
                         f'Its game {game_key} was deleted. {reason}')
            deleted_runs.append(d.name)
        shutil.rmtree(gdir)
        # the game leaves any group it sat in; a group cannot hold a ghost
        doc = load_groups()
        changed = False
        for gr in doc['groups']:
            if game_key in gr.get('games', []):
                gr['games'] = [g for g in gr['games'] if g != game_key]
                changed = True
        if changed:
            save_groups(doc)
        today_ = time.strftime('%Y-%m-%d', time.gmtime())
        for (u, role, scope), ev in list(held_roles().items()):
            if role == 'expert' and scope == game_key:
                append_role_event({'user': ev['user'], 'role': 'expert',
                                   'scope': scope, 'action': 'revoked', 'by': actor,
                                   'date': today_, 'at': now_iso(),
                                   'reason': f'The game was deleted. {reason}'})
        log_deletion('game', game_key, game.get('title', game_key), actor, reason)
        ensure_member(actor)
        commit_push(f'Delete game {game_key}: by expert {actor}\n\n'
                    f'Reason: {reason}\n'
                    f'Runs deleted with it: {", ".join(deleted_runs) or "none"}\n'
                    f'Via: archivist')
    return jsonify({'ok': True, 'deleted': game_key, 'runs_deleted': deleted_runs})

@app.post('/api/group/delete')
def group_delete():
    """An expert deletes a group outright; its games become ungrouped and the
    derived Unclassified group picks them up at the next build."""
    f = request.form
    dry = f.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        err0 = auth_precheck(f)
        if err0:
            return err0
        actor, reason, err_ = _deletion_gate(f)
        if err_:
            return err_
        key = (f.get('group') or '').strip().lower()
        doc = load_groups()
        gr = next((g for g in doc['groups'] if g['key'] == key), None)
        if not gr:
            return fail(f'no group with the key {key!r}', 404)
        if not covers_group(actor, gr) and not is_editor(actor):
            return fail(f'{actor} holds no scope covering the {gr["title"]} group', 403)
        if dry:
            return jsonify({'ok': True, 'dry_run': True, 'would_delete': key,
                            'released': gr.get('games', [])})
        checkout_branch()
        doc = load_groups()
        gr = next((g for g in doc['groups'] if g['key'] == key), None)
        if not gr:
            return fail(f'no group with the key {key!r}', 404)
        released = gr.get('games', [])
        doc['groups'] = [g for g in doc['groups'] if g['key'] != key]
        save_groups(doc)
        today_ = time.strftime('%Y-%m-%d', time.gmtime())
        for (u, role, scope), ev in list(held_roles().items()):
            if role == 'expert' and scope == f'group:{key}':
                append_role_event({'user': ev['user'], 'role': 'expert',
                                   'scope': scope, 'action': 'revoked', 'by': actor,
                                   'date': today_, 'at': now_iso(),
                                   'reason': f'The group was deleted. {reason}'})
        log_deletion('group', key, gr.get('title', key), actor, reason)
        ensure_member(actor)
        commit_push(f'Delete group {key}: by expert {actor}\n\n'
                    f'Reason: {reason}\nReleased: {", ".join(released) or "no games"}\n'
                    f'Via: archivist')
    return jsonify({'ok': True, 'deleted': key, 'released': released})

@app.post('/api/member/delete')
def member_delete():
    """The Steering Committee deletes a member record: spam accounts, tests.

    Refused while the member holds any role or authored any run: those are
    real entanglements with the community and each has its own procedure.
    Their name in other runs' credits is text and stays.
    """
    f = request.form
    dry = f.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        err0 = auth_precheck(f)
        if err0:
            return err0
        actor, reason, err_ = _deletion_gate(f)
        if err_:
            return err_
        if not is_committee(actor):
            return fail('only the Steering Committee deletes a member', 403)
        target = (f.get('target') or '').strip()
        if not re.fullmatch(r'[A-Za-z0-9. _-]{2,40}', target):
            return fail('target must be the member being deleted')
        afile = ARCHIVE / 'authors' / f'{selfimport.slugify(target)}.json'
        if not afile.exists():
            return fail(f'no member record for {target}', 404)
        if target.lower() == actor.lower():
            return fail('deleting yourself is not a decision to make alone; ask '
                        'another Committee member')
        target_roles = [(role, scope) for (u, role, scope) in held_roles()
                        if u == target.lower()]
        # The Committee does not eat itself: a sitting Committee member is the
        # Founder's alone to delete, and the Founder is nobody's (2.2.2).
        if any(role == 'founder' for role, s in target_roles):
            return fail('the Founder cannot be deleted (Principles 2.2.2)', 403)
        if any(role == 'committee' for role, s in target_roles) and not is_founder(actor):
            return fail('a sitting Committee member is deleted by the Founder alone, '
                        'never by fellow Committee members', 403)
        tl = target.lower()
        authored = [rj for rj in ARCHIVE.glob('games/*/*/runs/*/run.json')
                    if any(a.get('user', '').lower() == tl
                           for a in json.loads(rj.read_text()).get('authors', []))]
        if authored:
            return fail(f'{target} authored {len(authored)} archived run(s); a member '
                        f'with works here is removed through withdrawal or erasure, '
                        f'never a record deletion', 409)
        if dry:
            return jsonify({'ok': True, 'dry_run': True, 'would_delete': target})
        checkout_branch()
        if not afile.exists():
            return fail(f'no member record for {target}', 404)
        # the deletion revokes whatever they held, in the same commit: a
        # deleted member on the roster would be a ghost with authority
        today_ = time.strftime('%Y-%m-%d', time.gmtime())
        for role, scope in target_roles:
            ev = {'user': target, 'role': role, 'action': 'revoked', 'by': actor,
                  'date': today_, 'at': now_iso(), 'reason': f'Member deleted. {reason}'}
            if scope:
                ev['scope'] = scope
            append_role_event(ev)
        afile.unlink()
        log_deletion('member', target, target, actor, reason)
        commit_push(f'Delete member {target}: by {actor}\n\n'
                    f'Reason: {reason}\nVia: archivist')
    for role, scope in target_roles:
        publish_group(role, target, add=False)
    return jsonify({'ok': True, 'deleted': target,
                    'roles_revoked': [r for r, s in target_roles]})

@app.post('/api/game/create')
def game_create():
    """Create a game with no run in it yet, inside a group you speak for.

    Submitting a movie has always been able to create a game; this is the other
    way round, for an expert filling out a group before anybody has archived a
    run of it. Real on arrival, like every creation here; a mistaken one is
    deleted on the record.
    """
    f = request.form
    dry = f.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        err0 = auth_precheck(f)
        if err0:
            return err0
        expert, err_ = request_identity(f, 'user')
        if err_:
            return err_
        system = (f.get('system') or '').strip()
        title = (f.get('title') or '').strip()[:120]
        gkey = (f.get('group') or '').strip().lower()
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
        doc = load_groups()
        gr = next((g for g in doc['groups'] if g['key'] == gkey), None) if gkey else None
        if gkey and not gr:
            return fail(f'no group with the key {gkey!r}', 404)
        # Authority: over the group you are filling out, or over the system the
        # game lands in. A group expert creating into their own group is the
        # case this exists for, and the game is not in the group yet, so the
        # group is what has to be checked rather than the game.
        # creation is everybody's (good faith; experts moderate). Placing
        # the game into a group is curation and still needs scope over it.
        if gr and not covers_group(expert, gr) and not is_editor(expert):
            return fail(f'{expert} holds no scope covering the '
                        f'{gr["title"]} group', 403)
        today_ = time.strftime('%Y-%m-%d', time.gmtime())
        game = {'title': title, 'system': system, 'createdBy': expert,
                'createdAt': today_}
        cat_label = (f.get('cat_label') or 'fastest completion').strip()[:80]
        cat_rule = (f.get('cat_rule')
                    or 'Complete the game as fast as possible.').strip()[:500]
        cat_key = slugify(f.get('cat_key') or cat_label)
        mdefs, merr = parse_metric_defs(f.get('metrics'))
        if merr:
            return fail(merr)
        if not cat_key or cat_key == 'unclassified':
            return fail('bad first-category key')
        first_cat = {'key': cat_key, 'label': cat_label, 'rule': cat_rule,
                     **({'metrics': mdefs} if mdefs else {})}
        if dry:
            return jsonify({'ok': True, 'dry_run': True, 'would_create': game_key,
                            'game': game, 'category': first_cat,
                            'group': gkey or None})
        checkout_branch()
        gdir = ARCHIVE / 'games' / system / slug
        if (gdir / 'game.json').exists():
            return fail(f'{game_key} already exists', 409)
        gdir.mkdir(parents=True, exist_ok=True)
        (gdir / 'game.json').write_text(json.dumps(game, indent=1) + '\n')
        (gdir / 'categories.json').write_text(json.dumps(
            {'dimensions': [{'key': 'goal', 'name': 'Category',
                             'options': [first_cat]}]}, indent=1) + '\n')
        (gdir / 'runs').mkdir(exist_ok=True)
        if gkey:
            doc = load_groups()
            gr = next((g for g in doc['groups'] if g['key'] == gkey), None)
            gr['games'] = sorted(set(gr['games']) | {game_key})
            save_groups(doc)
        ensure_member(expert)
        ensure_game_topic(*game_key.split('/'), title)
        commit_push(f'Create {game_key}: by {expert}\n\n'
                    f'Title: {title}\nFirst category: {cat_key}\n'
                    f'Group: {gkey or "none"}\nVia: archivist')
        notify_discord(f'\U0001f5c2\ufe0f **{member_md(expert)}** created the '
                       f'[game](<{SITE_URL}/games/{game_key}/>) {title}'
                       + (f' in the {gkey} group' if gkey else ''),
                       wait_for=f'{SITE_URL}/games/{game_key}/')
    return jsonify({'ok': True, 'game': game_key, 'category': cat_key,
                    'group': gkey or None,
                    'note': 'It has no runs yet, so it shows as an empty game until '
                            'somebody archives one.'})
@app.post('/api/group/create')
def group_create():
    """Create a group, real on arrival, exactly like a
    game: naming a family of games is a curatorial claim, not a fact.

    You may only gather games you already have authority over, which is the same
    rule appointment follows. An empty group is site scope only, since there is
    nothing yet to derive authority from.
    """
    f = request.form
    dry = f.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        err0 = auth_precheck(f)
        if err0:
            return err0
        expert, err_ = request_identity(f, 'expert')
        if err_:
            return err_
        key = (f.get('group') or '').strip().lower()
        title = (f.get('title') or '').strip()
        games = [g.strip() for g in (f.get('games') or '').replace(',', ' ').split() if g.strip()]
        if not re.fullmatch(r'[a-z0-9]+(-[a-z0-9]+)*', key or ''):
            return fail('the group key must be lowercase words joined by hyphens')
        if key in ('uncategorized', 'unclassified'):
            return fail(f'{key} is reserved for the derived group that gathers '
                        f'every game no group has claimed')
        if not (1 <= len(title) <= 80):
            return fail('a group needs a title')
        doc = load_groups()
        if any(g['key'] == key for g in doc['groups']):
            return fail(f'a group with the key {key!r} already exists', 409)
        for g in games:
            if not re.fullmatch(r'[a-z0-9-]+/[a-z0-9-]+', g) or \
                    not (ARCHIVE / 'games' / g / 'game.json').is_file():
                return fail(f'no such game: {g!r}', 404)
            other = next((x for x in doc['groups'] if g in x.get('games', [])), None)
            if other:
                return fail(f'{g} already belongs to the {other["title"]} group; a game '
                            f'belongs to one', 409)
        holder = {'key': key, 'games': games}
        if not covers_group(expert, holder) and not is_editor(expert):
            return fail(f'{expert} holds no scope covering '
                        f'{"every game listed" if games else "an empty group"}; '
                        f'a group gathers games you already speak for', 403)
        today_ = time.strftime('%Y-%m-%d', time.gmtime())
        # real on arrival: ratification is gone as a mechanism
        entry = {'key': key, 'title': title, 'games': games,
                 'createdBy': expert, 'createdAt': today_}
        if dry:
            return jsonify({'ok': True, 'dry_run': True, 'would_create': entry})
        checkout_branch()
        doc = load_groups()
        if any(g['key'] == key for g in doc['groups']):
            return fail(f'a group with the key {key!r} already exists', 409)
        doc['groups'].append(entry)
        save_groups(doc)
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
    whatever group holds it, because a game belongs to one group."""
    f = request.form
    dry = f.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        err0 = auth_precheck(f)
        if err0:
            return err0
        expert, err_ = request_identity(f, 'expert')
        if err_:
            return err_
        key = (f.get('group') or '').strip().lower()
        add = [g.strip() for g in (f.get('add') or '').replace(',', ' ').split() if g.strip()]
        move = [g.strip() for g in (f.get('move') or '').replace(',', ' ').split() if g.strip()]
        drop = [g.strip() for g in (f.get('remove') or '').replace(',', ' ').split() if g.strip()]
        title = (f.get('title') or '').strip()
        if not (add or move or drop or title):
            return fail('nothing to change')
        doc = load_groups()
        gr = next((g for g in doc['groups'] if g['key'] == key), None)
        if not gr:
            return fail(f'no group with the key {key!r}', 404)
        if not covers_group(expert, gr) and not is_editor(expert):
            return fail(f'{expert} holds no scope covering the {gr["title"]} group', 403)
        for g in add:
            if not (ARCHIVE / 'games' / g / 'game.json').is_file():
                return fail(f'no such game: {g!r}', 404)
            if not expert_covers(expert, g) and not is_editor(expert):
                return fail(f'{expert} holds no scope covering {g}; a group cannot '
                            f'reach a game its curator may not speak for', 403)
            if g in gr['games']:
                return fail(f'{g} is already in this group', 409)
            other = next((x for x in doc['groups'] if x['key'] != key and g in x.get('games', [])),
                         None)
            if other:
                return fail(f'{g} already belongs to the {other["title"]} group; a game '
                            f'belongs to one (move it instead)', 409)
        for g in move:
            if not (ARCHIVE / 'games' / g / 'game.json').is_file():
                return fail(f'no such game: {g!r}', 404)
            if not expert_covers(expert, g) and not is_editor(expert):
                return fail(f'{expert} holds no scope covering {g}; a group cannot '
                            f'reach a game its curator may not speak for', 403)
            if g in gr['games']:
                return fail(f'{g} is already in this group', 409)
        for g in drop:
            if g not in gr['games']:
                return fail(f'{g} is not in this group', 404)
        if title and not (1 <= len(title) <= 80):
            return fail('a title must be under 80 characters')
        after = sorted((set(gr['games']) | set(add) | set(move)) - set(drop))
        if dry:
            return jsonify({'ok': True, 'dry_run': True, 'would_hold': after,
                            'title': title or gr['title']})
        checkout_branch()
        doc = load_groups()
        gr = next((g for g in doc['groups'] if g['key'] == key), None)
        if not gr:
            return fail(f'no group with the key {key!r}', 404)
        before_games = list(gr['games'])
        before_title = gr['title']
        # a move pulls the game out of whatever group held it, first
        moved_from = {}
        for other in doc['groups']:
            if other['key'] == key:
                continue
            hits = [g for g in move if g in other.get('games', [])]
            if hits:
                other['games'] = [g for g in other['games'] if g not in hits]
                for g in hits:
                    moved_from[g] = other['key']
        gr['games'] = sorted((set(gr['games']) | set(add) | set(move)) - set(drop))
        if title:
            gr['title'] = title
        save_groups(doc)
        what = ', '.join(filter(None, [
            f'+{" ".join(add)}' if add else '',
            ' '.join(f'{g} moved in from {moved_from[g]}' if g in moved_from
                     else f'{g} moved in' for g in move) if move else '',
            f'-{" ".join(drop)}' if drop else '',
            f'retitled {title!r}' if title else '']))
        log_edit('group', key, 'games' if (add or move or drop) else 'title',
                 ', '.join(before_games) if (add or move or drop) else before_title,
                 ', '.join(gr['games']) if (add or move or drop) else gr['title'],
                 expert, f'Changed from the group form: {what}')
        ensure_member(expert)
        commit_push(f'Group {key}: {what}\n\nBy: {expert}\nVia: archivist')
        notify_discord(f'\U0001f5c2\ufe0f **{member_md(expert)}** changed the '
                       f'[group](<{SITE_URL}/groups/{key}/>) {gr["title"]}: {what}',
                       wait_for=f'{SITE_URL}/groups/{key}/')
    return jsonify({'ok': True, 'group': key, 'games': gr['games'], 'title': gr['title']})


REPORT_KINDS = {'missing-content-warnings', 'spam-malicious', 'miscredited',
                'licensing', 'other'}

@app.post('/api/report')
def report():
    """Report a run — public, uniquely identified, addressed by the covering
    expert, permanently listed in the site log."""
    f = request.form
    dry = f.get('dry_run') in ('1', 'true', 'yes')
    with lock:
        err0 = auth_precheck(f)
        if err0:
            return err0
        if not dry:
            checkout_branch()
        user, err_ = request_identity(f)
        if err_:
            return err_
        run_id = (f.get('run') or '').strip()
        rdir = find_run(run_id) if re.fullmatch(r'M[0-9]+', run_id) else None
        if not rdir:
            return fail(f'unknown run {run_id}', 404)
        kind = (f.get('kind') or '').strip()
        if kind not in REPORT_KINDS:
            return fail(f'kind must be one of: {", ".join(sorted(REPORT_KINDS))}')
        details = (f.get('details') or '').strip()
        if len(details) > ACT_NOTES_MAX:
            return fail(f'details exceed {ACT_NOTES_MAX} characters')
        if kind == 'other' and not details:
            return fail("an 'other' report needs details")
        r = json.loads((rdir / 'run.json').read_text())
        rep = {'id': next_report_id(), 'by': user,
               'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(),
               'kind': kind, 'status': 'open'}
        if details:
            rep['details'] = details
        r.setdefault('reports', []).append(rep)
        if dry:
            return jsonify({'ok': True, 'dry_run': True, 'would_file': rep})
        (rdir / 'run.json').write_text(json.dumps(
            {k: v for k, v in r.items() if not k.startswith('_')}, indent=1))
        ensure_member(user)
        commit_push(f'Report R{rep["id"]} on {run_id}: {kind} by {user}\n\nVia: archivist')
    return jsonify({'ok': True, 'run': run_id, 'report': f'R{rep["id"]}',
                    'note': 'Filed in the open. The covering expert will address it; '
                            'it is permanently listed in the site log.'})

@app.post('/api/report/resolve')
def report_resolve():
    """The covering expert resolves or dismisses a report — logged in the open."""
    f = request.form
    dry = f.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        err0 = auth_precheck(f)
        if err0:
            return err0
        if not dry:
            checkout_branch()
        expert, err_ = request_identity(f, 'expert')
        if err_:
            return err_
        run_id = (f.get('run') or '').strip()
        rdir = find_run(run_id) if re.fullmatch(r'M[0-9]+', run_id) else None
        if not rdir:
            return fail(f'unknown run {run_id}', 404)
        r = json.loads((rdir / 'run.json').read_text())
        if not expert_covers(expert, r['game']):
            return fail(f'{expert!r} is not an expert covering {r["game"]}', 403)
        try:
            rep_id = int(f.get('report') or '')
        except ValueError:
            return fail('report must be a report id number')
        rep = next((x for x in r.get('reports', []) if x['id'] == rep_id), None)
        if not rep:
            return fail(f'no report R{rep_id} on this run', 404)
        if rep['status'] != 'open':
            return fail(f'report R{rep_id} is already {rep["status"]}')
        outcome = (f.get('outcome') or '').strip()
        if outcome not in ('resolved', 'dismissed'):
            return fail('outcome must be resolved or dismissed')
        resolution = (f.get('resolution') or '').strip()
        if not resolution:
            return fail('a public resolution text is required; it is logged in the open')
        if len(resolution) > ACT_NOTES_MAX:
            return fail(f'resolution exceeds {ACT_NOTES_MAX} characters')
        rep['status'] = outcome
        rep['resolvedBy'] = expert
        rep['resolvedAt'] = time.strftime('%Y-%m-%d', time.gmtime())
        rep['resolution'] = resolution
        if dry:
            return jsonify({'ok': True, 'dry_run': True, 'would_resolve': rep})
        (rdir / 'run.json').write_text(json.dumps(
            {k: v for k, v in r.items() if not k.startswith('_')}, indent=1))
        ensure_member(expert)
        commit_push(f'Report R{rep_id} {outcome} on {run_id}: by expert {expert}\n\n'
                    f'Resolution: {resolution}\nVia: archivist')
    return jsonify({'ok': True, 'report': f'R{rep_id}', 'status': outcome})

@app.post('/api/edit')
def edit_run():
    """The run's authors revise their own work freely; a covering expert may
    correct the same details, one run at a time, always with a public reason
    (the same logged, git-reversible trail as /api/expert/edit; the author
    list and supplementary uploads stay the authors' alone). Git history is
    the audit trail — nothing is erased."""
    f = request.form
    dry = f.get('dry_run') in ('1', 'true', 'yes')
    with lock:
        err0 = auth_precheck(f)
        if err0:
            return err0
        if not dry:
            checkout_branch()
        user, err_ = request_identity(f)
        if err_:
            return err_
        run_id = (f.get('run') or '').strip()
        rdir = find_run(run_id) if re.fullmatch(r'M[0-9]+', run_id) else None
        if not rdir:
            return fail(f'unknown run {run_id}', 404)
        r = json.loads((rdir / 'run.json').read_text())
        is_author = current_name(user).lower() in run_authors_now(r)
        if not is_author and not expert_covers(user, r['game']):
            return fail("only the run's authors or a covering expert may edit it", 403)
        reason = (f.get('reason') or '').strip()
        if not is_author and not (8 <= len(reason) <= 500):
            return fail('an expert edit states its public reason (8 to 500 '
                        'characters), published in the edit log')
        changed = []
        befores = {'emulator': r.get('contract', {}).get('emulator', '')}
        if 'authors' in f:
            if not is_author:
                return fail("an author list is never an expert's edit: who made "
                            "a thing is moderation's question", 403)
            new_authors = [a.strip() for a in (f.get('authors') or '').split(',') if a.strip()]
            if not new_authors:
                return fail('a run needs at least one author')
            acted = ({current_name(x['user']).lower() for x in r.get('reproductions', [])}
                     | {current_name(x['user']).lower() for x in r.get('verifications', [])}
                     | {current_name(l['user']).lower() for l in r.get('likes', [])})
            clash = [a for a in new_authors if current_name(a).lower() in acted]
            if clash:
                return fail(f'cannot credit {", ".join(clash)} as author: they already '
                            f'reproduced, verified, or liked this run (authors may not '
                            f'act on their own runs)')
            if [a['user'] for a in r['authors']] != new_authors:
                r['authors'] = [{'user': a} for a in new_authors]
                changed.append('authors')
                if not dry:
                    ensure_member(user)
        # Only what actually differs is a change (issue #38): the form sends
        # every field every time, and a browser textarea submits CRLF, which
        # used to rewrite an untouched 96-line notes file on every edit.
        if 'notes' in f:
            notes = (f.get('notes') or '').replace('\r\n', '\n').replace('\r', '\n')
            if len(notes.encode()) > 1024 * 1024:
                return fail('notes exceed 1 MB')
            notes = notes.rstrip() + '\n'
            try:
                old_notes = (rdir / 'notes.md').read_text()
            except OSError:
                old_notes = ''
            if old_notes.rstrip() + '\n' != notes:
                changed.append('notes')
        if 'emulator' in f:
            new_emu = (f.get('emulator') or '').strip()[:120]
            if new_emu != r.get('contract', {}).get('emulator', ''):
                r.setdefault('contract', {})['emulator'] = new_emu
                changed.append('emulator')
        # stated metric values: only the keys this run's category defines;
        # an empty field leaves the value untouched, an explicit 0 returns
        # it to "not yet stated" (which ranks last)
        _sys, _slug = r['game'].split('/')
        _g, _cats = load_game(_sys, _slug)
        _goal = (r.get('category') or {}).get('goal')
        _opt = next((o for dd in (_cats or {}).get('dimensions', [])
                     for o in dd['options'] if o['key'] == _goal), None)
        _mkeys = {mm['key'] for mm in (_opt or {}).get('metrics', [])
                  if mm['key'] != 'time'}
        for fk in list(f.keys()):
            if not fk.startswith('metric_'):
                continue
            mkey = fk[len('metric_'):]
            raw = (f.get(fk) or '').strip()
            if raw == '':
                continue
            if mkey not in _mkeys:
                return fail(f'this category states no metric {mkey!r}')
            try:
                mval = float(raw)
            except ValueError:
                return fail(f'{mkey} must be a number (seconds for times)')
            if mval < 0:
                return fail(f'{mkey} cannot be negative')
            if mval == (r.get('metrics') or {}).get(mkey, 0):
                continue
            befores[f'metric:{mkey}'] = str((r.get('metrics') or {}).get(mkey, 0))
            r.setdefault('metrics', {})[mkey] = mval
            changed.append(f'metric:{mkey}')
        if 'completed' in f:
            cv = (f.get('completed') or '').strip()
            if cv:
                if not re.fullmatch(r'(19[89]\d|20\d{2})-(0[1-9]|1[0-2])'
                                    r'-(0[1-9]|[12]\d|3[01])', cv):
                    return fail('completed must be a date like 2021-10-26')
                if cv > time.strftime('%Y-%m-%d', time.gmtime()):
                    return fail('completed cannot be in the future')
            if cv != r.get('completed', ''):
                befores['completed'] = r.get('completed', '')
                if cv:
                    r['completed'] = cv
                else:
                    r.pop('completed', None)
                changed.append('completed')
        if 'goalDescription' in f:
            gd = (f.get('goalDescription') or '').strip()
            if len(gd) > 500:
                return fail('a goal description fits in 500 characters')
            if is_uncl_run(r) and not gd:
                return fail('an Unclassified run states its own goal; it cannot lose '
                            'its description')
            if gd != r.get('goalDescription', ''):
                befores['goalDescription'] = r.get('goalDescription', '')
                if gd:
                    r['goalDescription'] = gd
                else:
                    r.pop('goalDescription', None)
                changed.append('goalDescription')
        if 'encode' in f:
            enc_v = (f.get('encode') or '').strip()
            if enc_v:
                enc_r = providers.resolve(enc_v)
                if not enc_r:
                    return fail('encode must be a watchable URL on a platform we accept')
                if enc_v != (r.get('encodes') or [{}])[0].get('url', ''):
                    befores['encode'] = (r.get('encodes') or [{}])[0].get('url', '')
                    r['encodes'] = [{'kind': enc_r['kind'], 'url': enc_v}]
                    changed.append('encode')
        if 'time' in f and r.get('videoOnly'):
            m_t = re.fullmatch(r'(?:(\d{1,3}):)?(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?',
                               (f.get('time') or '').strip())
            if not m_t:
                return fail('time must be [h:]mm:ss or [h:]mm:ss.mmm')
            h, mnt, sec, frac = m_t.groups()
            dur = (int(h or 0) * 3600 + int(mnt) * 60 + int(sec)
                   + (int(frac.ljust(3, "0")) / 1000 if frac else 0.0))
            if dur <= 0:
                return fail('a run that takes no time at all is not a run')
            befores['duration'] = str(r.get('duration'))
            r['duration'] = dur
            changed.append('duration')
        new_atts, att_err = read_attachments(r.get('attachments') or [])
        if att_err:
            return att_err
        if new_atts:
            if not is_author:
                return fail("supplementary files are the authors' own uploads", 403)
            have = {a['file'] for a in r.get('attachments') or []}
            clash = [n for n, _ in new_atts if f'attachments/{n}' in have]
            if clash:
                return fail(f'attachment {clash[0]!r} already exists on this run')
            r.setdefault('attachments', []).extend(
                {'file': f'attachments/{n}', 'role': 'supplementary'}
                for n, _ in new_atts)
            changed.append('attachments')
        if not changed:
            return fail('nothing to change: every value sent already matches the '
                        'record (send notes, emulator, completed, goalDescription, '
                        'encode, attachments, or, video-only, time)')
        if dry:
            return jsonify({'ok': True, 'dry_run': True, 'would_change': changed})
        if 'notes' in changed:
            (rdir / 'notes.md').write_text(notes)
        if new_atts:
            (rdir / 'attachments').mkdir(exist_ok=True)
            for n, data in new_atts:
                (rdir / 'attachments' / n).write_bytes(data)
        (rdir / 'run.json').write_text(json.dumps(
            {k: v for k, v in r.items() if not k.startswith('_')}, indent=1))
        # every revision joins the same history the expert edits live in: the
        # author owes nobody a justification for editing their own work, but
        # the history prevails either way
        for field in changed:
            log_edit('run', run_id, field,
                     befores.get(field, '(previous value in git history)'),
                     ('(see the run)' if field in ('notes', 'authors') else
                      str((r.get('metrics') or {}).get(field.split(':', 1)[1], '')
                          if field.startswith('metric:') else
                          {'emulator': r.get('contract', {}).get('emulator', ''),
                           'completed': r.get('completed', ''),
                           'goalDescription': r.get('goalDescription', ''),
                           'encode': (r.get('encodes') or [{}])[0].get('url', ''),
                           'duration': r.get('duration', ''),
                           'attachments': ', '.join(n for n, _ in new_atts),
                           }.get(field, ''))[:300]),
                     user, "The author's own revision." if is_author else reason)
        commit_push(f'Edit {run_id}: {", ".join(changed)} by '
                    f'{"author" if is_author else "expert"} {user}\n\nVia: archivist')
    return jsonify({'ok': True, 'run': run_id, 'changed': changed})

@app.post('/api/preview')
def preview_notes():
    """The submit preview, rendered by the very code that renders the
    published page (issue #30). Cross-references get a plain link here;
    the published page dresses them with the run's title and thumbnail."""
    import wikitext
    text = (request.form.get('notes') or '').replace('\r\n', '\n')
    if len(text.encode()) > 1024 * 1024:
        return fail('notes exceed 1 MB')
    def refs(t):
        t = re.sub(r'\[M([0-9]+)\]', r'<a class="runref" href="/runs/M\1/">M\1</a>', t)
        t = re.sub(r'\[user:([A-Za-z0-9. _-]{2,40})\]', r'<span class="au">\1</span>', t)
        return t
    resp = jsonify({'ok': True, 'html': wikitext.wiki_html(text, refs=refs)})
    resp.headers['Cache-Control'] = 'no-store'
    return resp

@app.post('/api/like')
def like():
    """Thumbs-up: everybody except the run's own authors, one per run.
    Works on any run, Imported included — it feeds player points and orders
    the Unclassified rankings."""
    f = request.form
    dry = f.get('dry_run') in ('1', 'true', 'yes')
    with lock:
        err0 = auth_precheck(f)
        if err0:
            return err0
        if not dry:
            checkout_branch()
        user, err_ = request_identity(f)
        if err_:
            return err_
        run_id = (f.get('run') or '').strip()
        rdir = find_run(run_id) if re.fullmatch(r'M[0-9]+', run_id) else None
        if not rdir:
            return fail(f'unknown run {run_id}', 404)
        r = json.loads((rdir / 'run.json').read_text())
        if r.get('withdrawn'):
            return fail(f'{run_id} has been withdrawn; no further acts apply')
        if current_name(user).lower() in run_authors_now(r):
            return fail('authors cannot like their own run')
        # The same star both ways: a second press takes the like back. Taking
        # it back deletes the entry outright, no tombstone and no log line, as
        # if it never happened: a like is a mood, not an act of authority, and
        # nobody owes the record an explanation for a change of heart. (The
        # git commit remains, as every commit does.)
        had = [l for l in r.get('likes', []) if l['user'].lower() == user.lower()]
        if had:
            r['likes'] = [l for l in r['likes'] if l['user'].lower() != user.lower()]
            liked = False
        else:
            r.setdefault('likes', []).append(
                {'user': user, 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso()})
            liked = True
        if dry:
            return jsonify({'ok': True, 'dry_run': True, 'liked': liked,
                            'likes': len(r['likes'])})
        (rdir / 'run.json').write_text(json.dumps(
            {k: v for k, v in r.items() if not k.startswith('_')}, indent=1))
        ensure_member(user)
        commit_push(f'{"Like" if liked else "Unlike"} {run_id}: by {user}\n\nVia: archivist')
    return jsonify({'ok': True, 'run': run_id, 'liked': liked, 'likes': len(r['likes'])})

@app.post('/api/case/open')
def case_open():
    """A dispute opens a case — never auto-disqualifies. The run's verifiers
    (snapshotted now) are asked to reaffirm."""
    f = request.form
    dry = f.get('dry_run') in ('1', 'true', 'yes')
    with lock:
        err0 = auth_precheck(f)
        if err0:
            return err0
        if not dry:
            checkout_branch()
        err_resp, rdir, r, user = act_common(f)
        if err_resp:
            return err_resp
        reason = (f.get('reason') or '').strip()
        if not reason:
            return fail('a dispute needs a reason')
        if len(reason) > ACT_NOTES_MAX:
            return fail(f'reason exceeds {ACT_NOTES_MAX} characters')
        live_v = [a for a in r.get('verifications', []) if not a.get('invalidated')]
        if not live_v:
            return fail('this run has no live verifications to dispute')
        if any(c.get('status') == 'open' for c in r.get('cases', [])):
            return fail('this run already has an open case')
        case = {'id': max([c['id'] for c in r.get('cases', [])] + [0]) + 1,
                'openedBy': user,
                'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(),
                'reason': reason,
                'verifiers': [a['user'] for a in live_v],
                'reaffirmations': [],
                'status': 'open'}
        r.setdefault('cases', []).append(case)
        if dry:
            return jsonify({'ok': True, 'dry_run': True, 'would_open': case})
        (rdir / 'run.json').write_text(json.dumps(
            {k: v for k, v in r.items() if not k.startswith('_')}, indent=1))
        ensure_member(user)
        commit_push(f'Case {case["id"]} opened on {r["id"]}: by {user}\n\nVia: archivist')
    return jsonify({'ok': True, 'run': r['id'], 'case': case['id'],
                    'verifiersAsked': case['verifiers']})

@app.post('/api/case/vote')
def case_vote():
    """A snapshotted verifier reaffirms (or withdraws) their verification."""
    f = request.form
    dry = f.get('dry_run') in ('1', 'true', 'yes')
    with lock:
        err0 = auth_precheck(f)
        if err0:
            return err0
        if not dry:
            checkout_branch()
        err_resp, rdir, r, user = act_common(f)
        if err_resp:
            return err_resp
        try:
            case_id = int(f.get('case') or '')
        except ValueError:
            return fail('case must be a case id number')
        case = next((c for c in r.get('cases', []) if c['id'] == case_id), None)
        if not case:
            return fail(f'no case {case_id} on this run', 404)
        if case['status'] != 'open':
            return fail(f'case {case_id} is already {case["status"]}')
        if user.lower() not in {u.lower() for u in case['verifiers']}:
            return fail('only the verifiers asked at case-open time may vote')
        if user.lower() in {v['user'].lower() for v in case.get('reaffirmations', [])}:
            return fail('you have already voted on this case')
        reaffirm = f.get('reaffirm') in ('1', 'true', 'yes')
        today = time.strftime('%Y-%m-%d', time.gmtime())
        vote = {'user': user, 'date': today, 'at': now_iso(), 'reaffirm': reaffirm}
        if (f.get('notes') or '').strip():
            vote['notes'] = f.get('notes').strip()
        case.setdefault('reaffirmations', []).append(vote)
        if not reaffirm:
            for a in r.get('verifications', []):
                if a['user'].lower() == user.lower() and not a.get('invalidated'):
                    a['invalidated'] = {'by': user, 'date': today, 'at': now_iso(),
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
            for a in r.get('verifications', []):
                if a['user'].lower() in snapshot and not a.get('invalidated'):
                    a['invalidated'] = {'by': 'case', 'date': today, 'at': now_iso(),
                                        'reason': f'case {case_id} upheld'}
        sync_status(r)
        if dry:
            return jsonify({'ok': True, 'dry_run': True, 'would_vote': vote,
                            'case_status': case['status'], 'status': r['status']})
        (rdir / 'run.json').write_text(json.dumps(
            {k: v for k, v in r.items() if not k.startswith('_')}, indent=1))
        ensure_member(user)
        commit_push(f'Case {case_id} vote on {r["id"]}: '
                    f'{"reaffirmed" if reaffirm else "withdrawn"} by {user}\n\nVia: archivist')
    return jsonify({'ok': True, 'run': r['id'], 'case': case_id,
                    'case_status': case['status'], 'status': r['status']})

@app.post('/api/expert/appoint')
def expert_appoint():
    """An expert appoints another, downward and in the open."""
    f = request.form
    appointer, err_ = request_identity(f, 'expert')
    if err_:
        return err_
    user = (f.get('user') or '').strip()
    scope = (f.get('scope') or '').strip()
    reason = (f.get('reason') or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9. _-]{2,40}', user):
        return fail('user must be the forum account being appointed')
    if not scope_exists(scope):
        return fail(f'no such scope: {scope!r} names no game, system or group here')
    if len(reason) < 8:
        return fail('say why, publicly: an appointment is authority over other '
                    "people's work")
    if len(reason) > 500:
        return fail('reason must be under 500 characters')
    dry = f.get('dry_run') in ('1', 'true', 'yes')

    refresh_archive()
    with lock:
        if not dry:
            checkout_branch()
        roster = load_experts()
        held = [e for e in roster if e['user'].lower() == appointer.lower()]
        # Two doors in (Principles 2.5.3 and 2.5.6): any single Committee
        # member may appoint an expert at any scope, the whole site included,
        # and an expert appoints downward into scopes their own scope covers.
        # Equal scope still does not qualify on the expert door, or an expert
        # could clone themselves without anybody wider agreeing; a Committee
        # seat is the wider agreement.
        if not (is_committee(appointer)
                or any(scope_covers(e['scope'], scope) for e in held)):
            return fail(f'{appointer} holds no scope that covers {scope} and no '
                        f'Committee seat; appointment runs downward, or from the '
                        f'Committee (Principles 2.5.3)', 403)
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
        if dry:
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
    removal like any other: a Committee poll through /api/role/decide."""
    f = request.form
    appointer, err_ = request_identity(f, 'expert')
    if err_:
        return err_
    user = (f.get('user') or '').strip()
    reason = (f.get('reason') or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9. _-]{2,40}', user):
        return fail('user must be the forum account being appointed')
    if len(reason) < 8:
        return fail('say why, publicly: the appointment is published with your name')
    if len(reason) > 500:
        return fail('reason must be under 500 characters')
    dry = f.get('dry_run') in ('1', 'true', 'yes')

    refresh_archive()
    with lock:
        if not dry:
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
        if dry:
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

@app.post('/api/hooks/discourse')
def discourse_hook():
    """Discourse tells us a post happened; we relay it to Discord.

    Signature first: the body is only trusted if Discourse's HMAC matches our
    shared secret. Private messages are never relayed, whatever they are, and
    neither are the archivist bot's own posts, which announce things the other
    notifications already said.
    """
    if not DISCOURSE_HOOK_SECRET:
        return fail('forum hooks are not configured on this server', 503)
    raw = request.get_data()
    sig = request.headers.get('X-Discourse-Event-Signature', '')
    want = 'sha256=' + hmac.new(DISCOURSE_HOOK_SECRET.encode(), raw,
                                hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, want):
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
    who = post.get('username') or 'somebody'
    if who == BOT_USER:
        return jsonify({'ok': True, 'ignored': 'our own bot'})
    title = post.get('topic_title') or 'a topic'
    excerpt = re.sub(r'<[^>]+>', '', post.get('cooked') or '').strip()
    excerpt = (excerpt[:140] + '\u2026') if len(excerpt) > 140 else excerpt
    link = (f'{DISCOURSE_URL}/t/{post.get("topic_id")}/{post.get("post_number")}'
            if post.get('topic_id') else DISCOURSE_URL)
    notify_discord(f'\U0001f4ac **{member_md(who)}** posted in [{title}](<{link}>): {excerpt}')
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
    """
    f = request.form
    caller, err_ = request_identity(f, 'expert')
    if err_:
        return err_
    refresh_archive(0)          # publishing must print the truth, not a cache
    if not is_site_expert(caller):
        return fail('only site-wide experts may publish the roster', 403)
    if not DISCOURSE_KEY:
        return fail('the forum is not configured on this server', 503)
    dry = f.get('dry_run') in ('1', 'true', 'yes')
    report = publish_roles(dry=dry)
    return jsonify({'ok': True, 'dry_run': dry, 'groups': report,
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
    """
    f = request.form
    caller, err_ = request_identity(f, 'user')
    if err_:
        return err_
    refresh_archive()
    if not is_founder(caller):
        return fail('only the Founder does this; the Committee route is '
                    '/api/role/decide with a poll', 403)
    target = (f.get('target') or '').strip()
    action = (f.get('action') or '').strip()
    reason = (f.get('reason') or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9. _-]{2,40}', target):
        return fail('target must be the forum account the decision is about')
    if action not in ('granted', 'revoked'):
        return fail('action must be granted or revoked')
    if not (8 <= len(reason) <= 500):
        return fail('say why, publicly: a seat on the Committee is authority over '
                    'the whole place')
    dry = f.get('dry_run') in ('1', 'true', 'yes')
    with lock:
        if not dry:
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
        if dry:
            return jsonify({'ok': True, 'dry_run': True, 'would_append': entry})
        append_role_event(entry)
        if action == 'granted':
            ensure_member(target)
        commit_push(f'Committee: {target} {action} by the Founder\n\n'
                    f'Reason: {reason}\nVia: archivist')
    note_ = publish_group('committee', target, add=(action == 'granted'))
    told = send_pm(
        target,
        f'You were {"seated on" if action == "granted" else "unseated from"} '
        f'the Steering Committee',
        (f'The Founder ({caller}) {"seated you on" if action == "granted" else "unseated you from"} '
         f'the Steering Committee.\n\nReason given: {reason}\n\n'
         f'The decision is public in the site log and on your member page.'))
    return jsonify({'ok': True, 'target': target, 'action': action, 'by': caller,
                    'forum': note_, 'told': told})

GRANT_WORDS = ('grant', 'appoint', 'yes', 'approve', 'in favour', 'in favor', 'for')

@app.post('/api/role/decide')
def role_decide():
    """Record a Committee decision about the committee or moderator role.

    The forum decides and the archive records. We do not implement voting: this
    reads the poll the Committee voted in, refuses anything that is not a
    genuine, checkable, finished Committee decision, and appends the event with
    the post as proof so anybody can go and check the call themselves. Joining a
    Discourse group is not how a role is granted, and never was a decision.
    """
    f = request.form
    caller, err_ = request_identity(f, 'user')
    if err_:
        return err_
    target = (f.get('target') or '').strip()
    role = (f.get('role') or '').strip()
    action = (f.get('action') or '').strip()
    post_id = (f.get('post') or '').strip()
    reason = (f.get('reason') or '').strip()
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
    poll, perr = read_committee_poll(post_id)
    if perr:
        return fail(perr, 409)
    size = committee_size()
    if size <= 0:
        return fail('the Committee is empty in the archive; there is nothing to '
                    'count a majority against', 409)
    words = GRANT_WORDS if action == 'granted' else ANNUL_WORDS
    votes = count_votes(poll, words)
    # Granting is an ordinary decision (Principles 2.3.3, 2.4.1): a simple
    # majority. Taking a role away is not (2.3.5): it needs a hard majority,
    # two thirds of every sitting member, counted whether they voted or not.
    # Expert annulment is the documented exception and keeps its own rule
    # (2.5.4), which is why it lives in its own endpoint.
    # An editor is the documented exception the other way (2.6.3): removal
    # by simple majority, like an expert's annulment.
    if action == 'granted' or role == 'editor':
        enough, needed = votes * 2 > size, 'a simple majority of the Committee'
    else:
        enough, needed = votes * 3 >= size * 2, ('a hard majority of the Committee, '
                                                 'two thirds of all sitting members')
    if not enough:
        return fail(f'{votes} of {size} committee members voted to '
                    f'{"grant" if action == "granted" else "remove"} this role; '
                    f'{needed} is required (Principles '
                    f'{"2.3.3" if action == "granted" else "2.3.5"})', 409)
    dry = f.get('dry_run') in ('1', 'true', 'yes')
    proof = f'{DISCOURSE_URL}/p/{post_id}'
    label = {'committee': 'the Steering Committee', 'moderator': 'moderator',
             'editor': 'editor'}[role]

    refresh_archive()
    with lock:
        if not dry:
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
        said = f' {reason}' if reason else ''
        entry = {'user': target, 'role': role, 'action': action, 'by': 'committee',
                 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(), 'proof': proof,
                 'reason': (f'{"Joined" if action == "granted" else "Left"} {label} '
                            f'by a Committee vote, {votes} of {size}.{said}')}
        if dry:
            return jsonify({'ok': True, 'dry_run': True, 'would_append': entry,
                            'votes': votes, 'committee': size, 'proof': proof})
        append_role_event(entry)
        if action == 'granted':
            ensure_member(target)
        commit_push(f'Roles: {target} {action} {role} by Committee vote\n\n'
                    f'Vote: {votes} of {size}\nProof: {proof}\n'
                    f'Recorded by: {caller}\nVia: archivist')
    note = publish_group(role, target, add=(action == 'granted'))
    return jsonify({'ok': True, 'user': target, 'role': role, 'action': action,
                    'votes': votes, 'committee': size, 'proof': proof, 'forum': note})

@app.post('/api/expert/annul')
def expert_annul():
    """Apply a Committee decision to annul an appointment (Principles 2.5.4).

    We do not implement voting: the forum already has it. This reads the poll,
    checks it was a genuine Committee decision with a majority of the Committee
    behind it, and then edits the roster. Everything a member needs to check the
    call themselves is in the post it names.
    """
    f = request.form
    caller, err_ = request_identity(f, 'user')
    if err_:
        return err_
    target = (f.get('target') or '').strip()
    scope = (f.get('scope') or '').strip()
    post_id = (f.get('post') or '').strip()
    if not target:
        return fail('target must be the expert whose appointment is annulled')
    if not post_id.isdigit():
        return fail('post must be the id of the forum post carrying the Committee poll')
    poll, perr = read_committee_poll(post_id)
    if perr:
        return fail(perr, 409)
    size = committee_size()
    if size <= 0:
        return fail('the committee group is empty or unreadable; nothing to count '
                    'a majority against', 409)
    for_annul = count_votes(poll, ANNUL_WORDS)
    if for_annul * 2 <= size:
        return fail(f'{for_annul} of {size} committee members voted to annul; a simple '
                    f'majority of the Committee is required', 409)
    dry = f.get('dry_run') in ('1', 'true', 'yes')
    proof = f'{DISCOURSE_URL}/p/{post_id}'
    refresh_archive()
    with lock:
        if not dry:
            checkout_branch()
        mine = [e for e in load_experts()
                if e['user'].lower() == target.lower()
                and (not scope or e['scope'] == scope)]
        dropped = len(mine)
        if not dropped:
            return fail(f'{target} holds no such scope', 404)
        if dry:
            return jsonify({'ok': True, 'dry_run': True, 'would_drop': dropped,
                            'votes': for_annul, 'committee': size, 'proof': proof})
        today = time.strftime('%Y-%m-%d', time.gmtime())
        for e in mine:
            append_role_event({
                'user': target, 'role': 'expert', 'scope': e['scope'],
                'action': 'revoked', 'by': 'committee', 'date': today, 'at': now_iso(), 'proof': proof,
                'reason': f'Annulled by a Committee vote, {for_annul} of {size}.'})
        commit_push(f'Annul: {target} loses {scope or "every scope"}\n\n'
                    f'Committee vote: {for_annul} of {size}\nProof: {proof}\n'
                    f'Applied by: {caller}\nVia: archivist')
    still = any(e['user'].lower() == target.lower() for e in load_experts())
    note = sync_expert_group(target, add=False) if not still else 'still an expert elsewhere'
    return jsonify({'ok': True, 'target': target, 'dropped': dropped,
                    'votes': for_annul, 'committee': size, 'proof': proof, 'forum': note})

@app.post('/api/expert/resign')
def expert_resign():
    """Step down from a scope. Always available, needs nobody's agreement."""
    f = request.form
    user, err_ = request_identity(f, 'user')
    if err_:
        return err_
    scope = (f.get('scope') or '').strip()
    dry = f.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        if not dry:
            checkout_branch()
        mine = [e for e in load_experts()
                if e['user'].lower() == user.lower() and (not scope or e['scope'] == scope)]
        if not mine:
            return fail(f'{user} holds no such scope', 404)
        if dry:
            return jsonify({'ok': True, 'dry_run': True, 'would_drop': len(mine)})
        dropped = len(mine)
        today = time.strftime('%Y-%m-%d', time.gmtime())
        for e in mine:
            append_role_event({'user': user, 'role': 'expert', 'scope': e['scope'],
                               'action': 'revoked', 'by': user, 'date': today, 'at': now_iso(),
                               'reason': 'Stepped down of their own accord.'})
        commit_push(f'Resign: {user} steps down from '
                    f'{scope or "every scope"}\n\nVia: archivist')
    keep = load_experts()
    still = any(e['user'].lower() == user.lower() for e in keep)
    note = sync_expert_group(user, add=False) if not still else 'still an expert elsewhere'
    return jsonify({'ok': True, 'user': user, 'dropped': dropped, 'forum': note})

@app.post('/api/claim/request')
def claim_request():
    """Ask to be handed a name held for an author elsewhere."""
    f = request.form
    member, err_ = request_identity(f, 'member')
    if err_:
        return err_
    identity = (f.get('identity') or '').strip()
    evidence = (f.get('evidence') or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9. _-]{2,40}', identity):
        return fail('identity must be the name you are claiming')
    if not (8 <= len(evidence) <= 1000):
        return fail('say what shows the name is yours: a post from that account, a '
                    'channel hosting your encodes, anything somebody can check')
    dry = f.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        afile = ARCHIVE / 'authors' / f'{selfimport.slugify(identity)}.json'
        if afile.exists():
            rec = json.loads(afile.read_text())
            if rec.get('claimed'):
                return fail(f'{identity} is already claimed by '
                            f'{rec.get("claimedBy") or "somebody"}', 409)
        doc = load_claims()
        if any(r['status'] == 'open' and r['identity'].lower() == identity.lower()
               for r in doc['requests']):
            return fail(f'a claim for {identity} is already open', 409)
        if any(r['status'] == 'open' and r['member'].lower() == member.lower()
               for r in doc['requests']):
            return fail('you already have a claim open; it has to be answered first', 409)
        entry = {'member': member, 'identity': identity, 'evidence': evidence,
                 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(), 'status': 'open'}
        if dry:
            return jsonify({'ok': True, 'dry_run': True, 'would_file': entry})
        checkout_branch()
        doc = load_claims()
        doc['requests'].append(entry)
        save_claims(doc)
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
    """
    f = request.form
    caller, err_ = request_identity(f, 'user')
    if err_:
        return err_
    refresh_archive(0)
    if not may_decide_claims(caller):
        return fail('the Steering Committee answers name claims', 403)
    out = []
    for r in load_claims()['requests']:
        if r['status'] != 'open':
            continue
        out.append(dict(r, email=member_email_masked(r['member'])))
    return jsonify({'ok': True, 'pending': out,
                    'note': 'The addresses here are masked and read from the forum as '
                            'you ask for them. The whole address is never sent by this '
                            'service, is not in the archive, and never appears on the '
                            'site.'})

@app.post('/api/claim/decide')
def claim_decide():
    """The Committee answers a claim, and the person is told either way."""
    f = request.form
    caller, err_ = request_identity(f, 'user')
    if err_:
        return err_
    refresh_archive()
    if not may_decide_claims(caller):
        return fail('the Steering Committee answers name claims', 403)
    identity = (f.get('identity') or '').strip()
    action = (f.get('action') or '').strip()
    note_ = (f.get('note') or '').strip()
    if action not in ('approved', 'denied'):
        return fail('action must be approved or denied')
    if action == 'denied' and not (8 <= len(note_) <= 500):
        return fail('say why it was denied: the person is told, and they can answer it')
    if len(note_) > 500:
        return fail('note must be under 500 characters')
    dry = f.get('dry_run') in ('1', 'true', 'yes')
    with lock:
        doc = load_claims()
        req = next((r for r in doc['requests']
                    if r['status'] == 'open' and r['identity'].lower() == identity.lower()),
                   None)
        if not req:
            return fail(f'no claim for {identity} is open', 404)
        if req['member'].lower() == caller.lower():
            return fail('you cannot answer your own claim', 403)
        today_ = time.strftime('%Y-%m-%d', time.gmtime())
        if dry:
            return jsonify({'ok': True, 'dry_run': True, 'request': req, 'would': action})
        checkout_branch()
        doc = load_claims()
        req = next((r for r in doc['requests']
                    if r['status'] == 'open' and r['identity'].lower() == identity.lower()),
                   None)
        if not req:
            return fail(f'no claim for {identity} is open', 404)
        req.update(status=action, decidedBy=caller, decidedAt=today_, note=note_)
        member = req['member']
        if action == 'approved':
            adir = ARCHIVE / 'authors'
            afile = adir / f'{selfimport.slugify(req["identity"])}.json'
            rec = json.loads(afile.read_text()) if afile.exists() else {
                'username': req['identity']}
            if rec.get('claimed') and (rec.get('claimedBy') or '').lower() != member.lower():
                return fail(f'{req["identity"]} is already claimed by '
                            f'{rec.get("claimedBy")}', 409)
            rec.update({'username': rec.get('username') or req['identity'],
                        'claimed': True, 'claimedBy': member, 'claimedAt': today_, 'claimedAtTime': now_iso(),
                        'claimMethod': 'committee', 'attestedBy': caller,
                        'attestation': (f'Claim approved by the Steering Committee. '
                                        f'{req["evidence"]}')[:1000]})
            adir.mkdir(exist_ok=True)
            afile.write_text(json.dumps(rec, indent=1) + '\n')
            # the claimed record IS this person's member record now; the one
            # their registration name wrote at first login is superseded, and
            # keeping it would list a member who no longer exists
            oldf = adir / f'{selfimport.slugify(member)}.json'
            if oldf != afile and oldf.exists():
                oldf.unlink()
        save_claims(doc)
        commit_push(f'Claim {action}: {member} for {req["identity"]}\n\n'
                    f'By: {caller}\n' + (f'Note: {note_}\n' if note_ else '')
                    + 'Via: archivist')
    rename_note = (unlock_forum_username(member, req['identity'])
                   if action == 'approved' else 'no rename')
    told = send_pm(
        member,
        f'Your claim to the name {req["identity"]} was {action}',
        (f'The Steering Committee {action} your claim to **{req["identity"]}**.\n\n'
         + (f'Your forum account has been renamed and the name is yours. Your profile '
            f'now carries an **Import my movies** button for your publications '
            f'at the site the name comes from, co-authored ones included; '
            f'importing a co-authored work is your responsibility.\n\n'
            if action == 'approved' else '')
         + (f'Reason given: {note_}\n\n' if note_ else '')
         + f'Answered by {caller}. You can reply to this message if you think this '
           f'is wrong; the decision is recorded in the site log either way.'))
    return jsonify({'ok': True, 'identity': req['identity'], 'member': member,
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
    """
    f = request.form
    expert, err_ = request_identity(f, 'expert')
    if err_:
        return err_
    refresh_archive()
    if not may_decide_claims(expert):
        return fail('only the Steering Committee assesses identity', 403)
    member = (f.get('member') or '').strip()
    identity = (f.get('identity') or '').strip()
    method = (f.get('method') or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9. _-]{2,40}', member):
        return fail('member must be the forum account being attested')
    if not re.fullmatch(r'[A-Za-z0-9. _-]{2,40}', identity):
        return fail('identity must be the name being claimed')
    if len(method) < 12:
        return fail('say how you verified it: the reason is public and it is the '
                    'whole point of an attestation')
    if len(method) > 1000:
        return fail('method must be under 1000 characters')
    dry = f.get('dry_run') in ('1', 'true', 'yes')

    with lock:
        if not dry:
            checkout_branch()
        adir = ARCHIVE / 'authors'
        afile = adir / f'{selfimport.slugify(identity)}.json'
        rec = json.loads(afile.read_text()) if afile.exists() else {'username': identity}
        if rec.get('claimed') and (rec.get('claimedBy') or '').lower() != member.lower():
            return fail(f'{identity} is already claimed by {rec.get("claimedBy")}', 409)
        rec.update({'username': rec.get('username') or identity,
                    'claimed': True,
                    'claimedBy': member,
                    'claimedAt': time.strftime('%Y-%m-%d', time.gmtime()),
                    'claimedAtTime': now_iso(),
                    'claimMethod': 'attested',
                    'attestedBy': expert,
                    'attestation': method})
        if dry:
            return jsonify({'ok': True, 'dry_run': True, 'would_write': rec})
        adir.mkdir(exist_ok=True)
        afile.write_text(json.dumps(rec, indent=1) + '\n')
        oldf = adir / f'{selfimport.slugify(member)}.json'
        if oldf != afile and oldf.exists():
            oldf.unlink()
        commit_push(f'Attest {identity}: verified by expert {expert}\n\n'
                    f'Member: {member}\nMethod: {method}\nVia: archivist')
    rename_note = unlock_forum_username(member, identity)
    return jsonify({'ok': True, 'identity': rec['username'], 'member': member,
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
    su = session_user()
    if not su:
        return None, fail('log in via the forum to import your movies', 403)
    if not origin_ok():
        return None, fail('cross-origin request refused', 403)
    if not re.fullmatch(r'[A-Za-z0-9._-]{3,30}', su):
        return None, fail('session username is not archive-safe', 400)
    afile = ARCHIVE / 'authors' / f'{selfimport.slugify(su)}.json'
    if not afile.exists():
        return None, fail('imports are available once you have claimed the identity', 403)
    a = json.loads(afile.read_text())
    if not a.get('claimed') or a.get('username', '').lower() != su.lower():
        return None, fail('imports are available once you have claimed the identity', 403)
    return a['username'], None

@app.post('/api/import/scan')
def import_scan():
    """What of my TASVideos catalog is not in the archive yet?"""
    user, err = _import_identity()
    if err:
        return err
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
    repeatedly until remaining is 0; each batch is one archive commit."""
    user, err = _import_identity()
    if err:
        return err
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
        res = selfimport.import_batch(DUMPS_DIR, ARCHIVE, user,
                                      time.strftime('%Y-%m-%d', time.gmtime()),
                                      THUMB_FETCH_BASE, limit=6, select=select)
        if res['imported']:
            res['topics'] = topics_for_imported(ARCHIVE, res['imported'])
            commit_push(f"Self-import for {user}: {', '.join(res['imported'])}")
    if res['imported']:
        # one line per batch, not per movie: a large import in six-movie
        # batches would otherwise flood the channel. The first few ids carry
        # the links; the source stays implicit, each run page naming its own.
        ids = res['imported']
        shown = ', '.join(f'[{i}](<{SITE_URL}/runs/{i}/>)' for i in ids[:3])
        more = f' +{len(ids) - 3} more' if len(ids) > 3 else ''
        word = 'movie' if len(ids) == 1 else f'{len(ids)} movies'
        notify_discord(f'\U0001f4e5 **{member_md(user)}** imported {word}: {shown}{more}',
                       wait_for=f'{SITE_URL}/runs/{ids[0]}/')
    return jsonify({'ok': True, 'user': user, **res})

@app.post('/api/verify')
def verify():
    """Record a verification: the goal, judged from the encode.

    Verification is the ranking gate. From a member it makes the run
    verified, which ranks; from an expert covering the game it makes it
    verified (expert), which is permanent. (The archive's enum names stay
    provisional/confirmed; only the words people see changed.) Who the verifier was at the moment of
    the act is stamped on the act, because scopes change and facts do not.
    """
    f = request.form
    dry = f.get('dry_run') in ('1', 'true', 'yes')
    with lock:
        err0 = auth_precheck(f)
        if err0:
            return err0
        if not dry:
            checkout_branch()
        err_resp, rdir, r, user = act_common(f)
        if err_resp:
            return err_resp
        if user.lower() in {a['user'].lower() for a in r.get('verifications', [])}:
            return fail('you have already verified this run; one verification per member')
        if (r.get('category') or {}).get('goal') == 'unclassified':
            return fail('Unclassified runs cannot be verified because no goal is defined; '
                        'they can be reproduced and liked')
        if not r.get('encodes'):
            return fail('this run has no encode linked; verification needs one to judge from')

        entry = {'user': user, 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso()}
        game_key_v = f'{rdir.parent.parent.parent.name}/{rdir.parent.parent.name}'
        if expert_covers(user, game_key_v):
            entry['expert'] = True
        if (f.get('notes') or '').strip():
            entry['notes'] = f.get('notes').strip()
        r.setdefault('verifications', []).append(entry)
        sync_status(r)
        if dry:
            return jsonify({'ok': True, 'dry_run': True, 'would_record': entry,
                            'status': r['status']})

        (rdir / 'run.json').write_text(json.dumps(
            {k: v for k, v in r.items() if not k.startswith('_')}, indent=1))
        ensure_member(user)
        commit_push(f'Verify {r["id"]}: by {user}\n\nVia: archivist')
        notify_discord(f'\u2713 **{member_md(user)}** verified'
                       + ' '
                       + movie_md(r),
                       wait_for=f'{SITE_URL}/runs/{r["id"]}/')
    return jsonify({'ok': True, 'run': r['id'], 'status': r['status'],
                    'verifications': len([a for a in r['verifications'] if not a.get('invalidated')])})

@app.post('/api/withdraw')
def withdraw():
    """Take a run out of the listings.

    Withdrawing is a voluntary act: only the run's own authors may do it (an
    expert who must remove a run deletes it, on the record). Nothing is
    erased: the record, the movie file and the reason stay in the archive,
    because the principles forbid erasing a contribution (1.2, 2.8.2). The
    site stops listing it and says why."""
    f = request.form
    dry = f.get('dry_run') in ('1', 'true', 'yes')
    refresh_archive()
    with lock:
        err0 = auth_precheck(f)
        if err0:
            return err0
        if not dry:
            checkout_branch()
        user, err_ = request_identity(f)
        if err_:
            return err_
        run_id = (f.get('run') or '').strip()
        rdir = find_run(run_id) if re.fullmatch(r'M[0-9]+', run_id) else None
        if not rdir:
            return fail(f'unknown run {run_id}', 404)
        r = json.loads((rdir / 'run.json').read_text())
        is_author = current_name(user).lower() in run_authors_now(r)
        if not is_author:
            return fail("withdrawing is the author's own voluntary act; an expert "
                        "who must remove a run deletes it instead", 403)
        if r.get('withdrawn'):
            return fail(f'{run_id} is already withdrawn')
        reason = (f.get('reason') or '').strip()
        if not reason:
            return fail('a withdrawal must state its reason; it is shown in the open')
        if len(reason) > ACT_NOTES_MAX:
            return fail(f'reason exceeds {ACT_NOTES_MAX} characters')

        r['withdrawn'] = {'by': user, 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(),
                          'reason': reason, 'role': 'author'}
        if dry:
            return jsonify({'ok': True, 'dry_run': True, 'would_withdraw': r['withdrawn']})
        (rdir / 'run.json').write_text(json.dumps(
            {k: v for k, v in r.items() if not k.startswith('_')}, indent=1))
        ensure_member(user)
        commit_push(f'Withdraw {run_id}: by {user}\n\nReason: {reason}\nVia: archivist')
    return jsonify({'ok': True, 'run': run_id, 'withdrawn': r['withdrawn']})

@app.post('/api/console-verify')
def console_verify():
    """Record a console verification: the run replayed on original hardware.

    An optional signal beside verification (the one gate). It is the most
    expensive act anyone can perform here, so it carries a public recording
    and pays accordingly."""
    f = request.form
    dry = f.get('dry_run') in ('1', 'true', 'yes')
    with lock:
        err0 = auth_precheck(f)
        if err0:
            return err0
        if not dry:
            checkout_branch()
        err_resp, rdir, r, user = act_common(f)
        if err_resp:
            return err_resp
        if r.get('videoOnly'):
            return fail('this run is video-only: there is no input movie to play '
                        'back on hardware, so console verification does not apply')
        if user.lower() in {a['user'].lower() for a in r.get('consoleVerifications', [])}:
            return fail('you have already console-verified this run; '
                        'one console verification per member')
        proof = (f.get('proof') or '').strip()
        if not re.match(r'https?://\S+$', proof) or len(proof) > 500:
            return fail('a link to the recording of the console playing this run '
                        'is required as proof')

        entry = {'user': user, 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(),
                 'proof': proof}
        if (f.get('hardware') or '').strip():
            entry['hardware'] = f.get('hardware').strip()[:120]
        if (f.get('notes') or '').strip():
            entry['notes'] = f.get('notes').strip()[:2000]

        shot = request.files.get('screenshot')
        data = None
        if shot and shot.filename:
            ext = pathlib.Path(shot.filename).suffix.lower()
            if ext not in IMAGE_MAGIC:
                return fail('screenshot must be png, jpg or webp')
            data = shot.read()
            if len(data) > SHOT_MAX_EACH:
                return fail('screenshot exceeds 512 KB')
            if not any(data.startswith(m) for m in IMAGE_MAGIC[ext]):
                return fail(f'screenshot is not a real {ext} image')
            existing = sum(sp.stat().st_size for sp in (rdir / 'console').glob('*')
                           if sp.is_file()) if (rdir / 'console').exists() else 0
            if existing + len(data) > SHOT_MAX_TOTAL:
                return fail('this run has reached its screenshot storage cap')
            n = len(r.get('consoleVerifications', [])) + 1
            entry['screenshot'] = f'console/{n}-{user}{ext}'

        r.setdefault('consoleVerifications', []).append(entry)
        sync_status(r)
        if dry:
            return jsonify({'ok': True, 'dry_run': True, 'would_record': entry})

        if data is not None:
            (rdir / 'console').mkdir(exist_ok=True)
            (rdir / entry['screenshot']).write_bytes(data)
        (rdir / 'run.json').write_text(json.dumps(
            {k: v for k, v in r.items() if not k.startswith('_')}, indent=1))
        ensure_member(user)
        commit_push(f'Console-verify {r["id"]}: by {user}\n\nProof: {proof}\nVia: archivist')
        notify_discord(f'\U0001f579\ufe0f **{member_md(user)}** played ' + movie_md(r)
                       + ' back on original hardware',
                       wait_for=f'{SITE_URL}/runs/{r["id"]}/')
    return jsonify({'ok': True, 'run': r['id'], 'proof': proof,
                    'consoleVerifications': len([a for a in r['consoleVerifications']
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
    """
    url = (request.args.get('url') or '').strip()
    enc = providers.resolve(url)
    if not enc:
        return jsonify({'ok': False,
                        'error': 'not a link from ' + ', '.join(providers.names())})
    hit = ENCODE_CACHE.get(url)
    if hit and time.time() - hit[0] < 300:
        return jsonify(hit[1])
    thumb = providers.thumbnail_url(enc['kind'], enc['id'])
    if not thumb and providers.BY_KIND[enc['kind']].get('thumbs'):
        # a direct template needs fetching to know whether the video is real;
        # the page then loads the candidate that actually answered (#29)
        thumb = providers.thumbnail_source(enc['kind'], enc['id'], THUMB_MAX)
    payload = ({'ok': True, 'kind': enc['kind'], 'name': enc['name'],
                'id': enc['id'], 'thumb': thumb} if thumb else
               {'ok': False, 'kind': enc['kind'], 'name': enc['name'],
                'error': f'that {enc["name"]} video does not exist, or is private'})
    ENCODE_CACHE[url] = (time.time(), payload)
    return jsonify(payload)

@app.get('/api/discussion')
def discussion():
    """The forum topic for a run, as the site renders it in place."""
    try:
        topic_id = int(request.args.get('topic') or 0)
    except ValueError:
        return fail('topic must be a number')
    if topic_id <= 0:
        return fail('topic is required')
    if not DISCOURSE_KEY:
        return fail('the forum is not configured on this server', 503)
    hit = DISCUSSION_CACHE.get(topic_id)
    if hit and time.time() - hit[0] < 60:
        return jsonify(hit[1])
    try:
        tj = _forum_get(f'/t/{topic_id}.json')
    except Exception as e:                                    # noqa: BLE001
        return fail(f'could not reach the forum: {e}', 502)
    posts = []
    for p_ in tj.get('post_stream', {}).get('posts', []):
        posts.append({
            'id': p_.get('id'), 'number': p_.get('post_number'),
            'user': p_.get('username'), 'name': p_.get('display_username'),
            'avatar': (DISCOURSE_URL + p_['avatar_template'].replace('{size}', '48')
                       if (p_.get('avatar_template') or '').startswith('/')
                       else (p_.get('avatar_template') or '').replace('{size}', '48')),
            'html': p_.get('cooked') or '',
            'date': (p_.get('created_at') or '')[:19],
            'staff': bool(p_.get('staff')),
        })
    payload = {'ok': True, 'topic': topic_id, 'title': tj.get('title'),
               'url': f'{DISCOURSE_URL}/t/{topic_id}',
               'posts': posts, 'replyCount': max(0, len(posts) - 1)}
    DISCUSSION_CACHE[topic_id] = (time.time(), payload)
    return jsonify(payload)

@app.post('/api/discussion/reply')
def discussion_reply():
    """Post a reply to a run's topic as the logged-in member.

    Session only: the shared key must never be able to speak as somebody
    else, and Discourse applies that member's own trust level and rate
    limits because the post is made under their name."""
    f = request.form
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
        topic_id = int(f.get('topic') or 0)
    except ValueError:
        return fail('topic must be a number')
    body = (f.get('body') or '').strip()
    if topic_id <= 0:
        return fail('topic is required')
    if len(body) < 5:
        return fail('a reply needs at least a few words')
    if len(body.encode()) > 32 * 1024:
        return fail('reply exceeds 32 KB')
    data = urllib.parse.urlencode({'topic_id': topic_id, 'raw': body}).encode()
    req = urllib.request.Request(f'{DISCOURSE_URL}/posts.json', data=data, method='POST',
                                 headers={'Api-Key': DISCOURSE_KEY, 'Api-Username': user})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            posted = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode(errors='replace')
        return fail(f'the forum refused the reply: {detail}', 502)
    except Exception as e:                                    # noqa: BLE001
        return fail(f'could not reach the forum: {e}', 502)
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
        except Exception as e:                                 # noqa: BLE001
            LOG.warning('reconcile failed: %s', e)

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

