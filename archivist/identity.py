"""Who is acting: session cookies, the rename map (a claim
supersedes the name a person registered under), and the email
masking used when the Committee assesses a claim."""
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

from flask import g, request

from settings import (
    ARCHIVE,
    DISCOURSE_KEY,
    DISCOURSE_URL,
    ROLE_2FA_FROM,
    ROLE_2FA_RECHECK_SECONDS,
    SESSION_SECRET,
    SESSION_TTL,
    SITE_ORIGIN,
    SITE_ORIGINS,
    SSO_SECRET,
)

def sso_sign(payload_b64):
    """Sign a Discourse single-sign-on payload."""
    return hmac.new(SSO_SECRET.encode(), payload_b64, hashlib.sha256).hexdigest()

def session_token(username, external_id, second_factor=None):
    """Sign a member session token.

    The fourth field is what the forum said, at login, about two-factor
    authentication on the account: 1 on, 0 off, ? when it could not be asked.
    """
    exp = int(time.time()) + SESSION_TTL
    mark = {True: '1', False: '0'}.get(second_factor, '?')
    body = f'{username}|{external_id}|{exp}|{mark}'
    sig = hmac.new(SESSION_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f'{body}|{sig}'

def forum_name(identity):
    """The forum's form of a claimed identity: its username rules are
    narrower than a name another site allowed."""
    return re.sub(r'[^\w.\-]', '_', identity)[:20]

_renames = {'built': False, 'map': {}}

def current_name(username):
    """Follow name claims: a session cookie outlives an approved claim, so
    it still carries the name the person logged in with before the Committee
    handed them their claimed one. The author records already say who goes by
    what (claimedBy), so a stale cookie is translated rather than the person
    being forced to log out: the request acts as the name a fresh login would
    carry."""
    if not _renames['built']:
        m = {}
        for p_ in (ARCHIVE / 'authors').glob('*.json'):
            try:
                rec = json.loads(p_.read_text())
            except ValueError:
                continue
            by, ident = rec.get('claimedBy'), rec.get('username')
            if by and ident:
                m[by.lower()] = forum_name(ident)
        _renames.update(built=True, map=m)
    seen = set()
    while username.lower() in _renames['map'] and username.lower() not in seen:
        seen.add(username.lower())
        username = _renames['map'][username.lower()]
    return username

def run_authors_now(r):
    """The run's authors, each resolved through any later rename: a credit
    written under a name the person no longer uses still belongs to them."""
    return {current_name(a['user']).lower() for a in r.get('authors', [])}

def session_fields():
    """The signed fields of a valid session cookie, else None.

    [name at login, forum user id, expiry, second-factor mark]. A session
    minted before the mark existed has three fields and reads as '?'.
    """
    if not SESSION_SECRET:
        return None
    tok = request.cookies.get('tar_session', '')
    parts = tok.rsplit('|', 1)
    if len(parts) != 2:
        return None
    body, sig = parts
    want = hmac.new(SESSION_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, want):
        return None
    fields = body.split('|')
    if len(fields) not in (3, 4) or not fields[2].isdigit() or time.time() > int(fields[2]):
        return None
    if len(fields) == 3:
        fields.append('?')
    return fields

def session_user():
    """Username from a valid session cookie, else None."""
    fields = session_fields()
    return current_name(fields[0]) if fields else None

# ---- two-factor authentication, for those who hold a role ----
# A role is in force for a session only when the forum account behind it has
# a second factor (ROLE_2FA_FROM says since when). The forum is the one that
# knows: it is asked at login, and the answer rides in the session. A session
# that logged in WITH a second factor keeps its roles for its life, so the
# forum being down takes nobody's powers away mid-session. One that logged in
# without, or when the forum could not be asked, is asked about again when a
# role is needed, so turning it on (or the forum coming back) takes effect in
# a minute and without logging in again. Not being able to ask counts as no:
# the roles stay off until the forum answers.
_second_factor_seen = {}        # forum user id or name -> (answer, when asked)
_second_factor_lock = threading.Lock()
SECOND_FACTOR_ASK_AGAIN = {True: 12 * 3600, False: ROLE_2FA_RECHECK_SECONDS,
                           None: ROLE_2FA_RECHECK_SECONDS / 2}   # seconds

def second_factor_lookup(username, external_id):
    """Ask the forum whether an account has two-factor authentication on.

    True, False, or None when it could not be asked or did not say. The
    admin view of the account is read by its id, which a rename does not
    change, and the member view by name is the fallback; either says
    `second_factor_enabled`.
    """
    if not DISCOURSE_KEY:
        return None
    paths = []
    if str(external_id).isdigit():
        paths.append(f'/admin/users/{external_id}.json')
    if username:
        paths.append(f'/u/{urllib.parse.quote(username)}.json')
    for path in paths:
        try:
            req = urllib.request.Request(
                DISCOURSE_URL + path,
                headers={'Api-Key': DISCOURSE_KEY, 'Api-Username': 'eien86'})
            with urllib.request.urlopen(req, timeout=6) as resp:
                doc = json.loads(resp.read() or b'{}')
        except Exception:                                      # noqa: BLE001
            continue
        for holder in (doc, doc.get('user') if isinstance(doc, dict) else None):
            if isinstance(holder, dict) and isinstance(holder.get('second_factor_enabled'), bool):
                return holder['second_factor_enabled']
    return None

def second_factor_asked(username, external_id, fresh=False):
    """The forum's answer about an account, asked again only once it is stale."""
    key = str(external_id) if str(external_id).isdigit() else (username or '').lower()
    now = time.time()
    with _second_factor_lock:
        seen = _second_factor_seen.get(key)
    if seen and not fresh and now - seen[1] < SECOND_FACTOR_ASK_AGAIN[seen[0]]:
        return seen[0]
    answer = second_factor_lookup(username, external_id)
    with _second_factor_lock:
        _second_factor_seen[key] = (answer, now)
    return answer

def role_2fa_enforced(today=None, since=None):
    """Is the rule in force on this day? ('never', or no date, means never.)"""
    since = ROLE_2FA_FROM if since is None else since
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', since or ''):
        return False
    return (today or time.strftime('%Y-%m-%d', time.gmtime())) >= since

def session_second_factor():
    """'on', 'off' or 'unknown' for the session making this request; None without one."""
    if 'second_factor' in g:
        return g.second_factor
    fields = session_fields()
    if not fields:
        state = None
    elif fields[3] == '1':
        state = 'on'
    else:
        state = {True: 'on', False: 'off'}.get(
            second_factor_asked(current_name(fields[0]), fields[1]), 'unknown')
    g.second_factor = state
    return state

def roles_suspended(user=None):
    """Are the roles of the member making this request out of force?

    True when the rule is in force and the session's account is not known to
    have a second factor. With `user`, only when that is the member asked
    about: a role check about somebody else is not this session's business.
    A request with no session (the operator's key) is never suspended.
    """
    if not role_2fa_enforced():
        return False
    acting = session_user()
    if not acting or (user is not None and str(user).lower() != acting.lower()):
        return False
    return session_second_factor() != 'on'

def origin_ok():
    """CSRF guard for cookie-authenticated writes: the tar_session cookie is
    SameSite=None, so any site could POST it — accept browser writes only from
    our own origins. Requests without an Origin header (curl, scripts) pass;
    they carry no ambient cookie authority worth forging."""
    origin = request.headers.get('Origin')
    return origin is None or origin in SITE_ORIGINS or origin == DISCOURSE_URL

def mask_email(addr):
    """jo***oe@e****.com

    Enough of an address to recognise one you were expecting, and not enough to
    write to somebody or to identify them from scratch. Confirming a claim asks
    "is this the address this author would have"; the whole address answers a
    question nobody put.
    """
    if not addr or '@' not in addr:
        return ''
    local, _, domain = addr.partition('@')
    dom, _, tld = domain.rpartition('.')

    def keep(s, head, tail):
        """Preserve safe character ranges while masking an email."""
        if len(s) <= head + tail:
            return (s[:1] or '*') + '*' * max(1, len(s) - 1)
        stars = '*' * max(1, min(8, len(s) - head - tail))
        return s[:head] + stars + (s[-tail:] if tail else '')

    if not tld:
        return keep(local, 2, 2) + '@***'
    return keep(local, 2, 2) + '@' + keep(dom, 1, 0) + '.' + tld

