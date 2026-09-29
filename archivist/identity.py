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

from flask import request

from settings import (
    ARCHIVE,
    DISCOURSE_URL,
    SESSION_SECRET,
    SESSION_TTL,
    SITE_ORIGIN,
    SITE_ORIGINS,
    SSO_SECRET,
)

def sso_sign(payload_b64):
    return hmac.new(SSO_SECRET.encode(), payload_b64, hashlib.sha256).hexdigest()

def session_token(username, external_id):
    exp = int(time.time()) + SESSION_TTL
    body = f'{username}|{external_id}|{exp}'
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

def session_user():
    """Username from a valid session cookie, else None."""
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
    if len(fields) != 3 or not fields[2].isdigit() or time.time() > int(fields[2]):
        return None
    return current_name(fields[0])

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
        if len(s) <= head + tail:
            return (s[:1] or '*') + '*' * max(1, len(s) - 1)
        stars = '*' * max(1, min(8, len(s) - head - tail))
        return s[:head] + stars + (s[-tail:] if tail else '')

    if not tld:
        return keep(local, 2, 2) + '@***'
    return keep(local, 2, 2) + '@' + keep(dom, 1, 0) + '.' + tld

