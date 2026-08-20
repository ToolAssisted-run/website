"""The archive's records: members, roles (an append-only log),
groups, claims, and the edit/deletion logs. Facts only; the
derived state lives in the site generator."""
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

import selfimport
from settings import (
    now_iso,
    ARCHIVE,
    LOG,
    SITE_URL,
)
from gitstore import (
    checkout_branch,
    commit_push,
    lock,
    refresh_archive,
)
from notify import (
    member_md,
    notify_discord,
    profile_slug,
)

def record_member_once(username):
    """Write the member record if this person has none yet.

    Registering on the forum is not arriving here, but logging in is: the
    members list is built from these records, and until one exists a new
    member cannot find themselves on a page that says it lists the people
    with an account. Records are still only ever about people who came here
    themselves; somebody merely credited in a run is still just text.
    """
    afile = ARCHIVE / 'authors' / f'{selfimport.slugify(username)}.json'
    refresh_archive()
    if afile.exists():
        return False
    with lock:
        checkout_branch()
        if afile.exists():
            return False
        ensure_member(username)
        commit_push(f'Member: {username} arrived\n\nVia: archivist (first login)')
        notify_discord(f'\U0001f195 {member_md(username)} joined toolAssisted.run',
                       wait_for=f'{SITE_URL}/authors/{profile_slug(username)}/')
    return True

def note_new_member(username):
    """Same, off the critical path. Nobody should wait on a git push to log
    in, and a failure here must never cost somebody their session: the next
    thing they do writes the record anyway."""
    def work():
        try:
            record_member_once(username)
        except Exception as e:                                 # noqa: BLE001
            LOG.warning('could not record %s as a member: %s', username, e)
    threading.Thread(target=work, daemon=True).start()

def ensure_member(name):
    """A record in authors/ means exactly one thing: this person is a member
    here, with an account on the forum.

    People credited as authors who are not members are never written down:
    they stay text in the run's author list until they claim their name. That
    keeps the archive a record of who is here, not a directory of everyone the
    community has ever credited, and it means nobody has a profile page they
    never asked for.
    """
    adir = ARCHIVE / 'authors'
    adir.mkdir(exist_ok=True)
    afile = adir / f"{selfimport.slugify(name)}.json"
    if afile.exists():
        rec = json.loads(afile.read_text())
        if not rec.get('claimed'):
            rec['claimed'] = True
            afile.write_text(json.dumps(rec, indent=1) + '\n')
        return
    afile.write_text(json.dumps({'username': name, 'claimed': True}, indent=1) + '\n')

def sync_status(r):
    """status is a checked cache of the rosters — keep it exactly as CI derives it.

    Verification is the ranking gate (rule change 2026-08-19): a community
    verification makes a run provisional, which ranks; a covering expert's
    verification makes it confirmed, which is permanent. Reproduction remains
    a recorded, paid act of assurance, and gates nothing.
    """
    live_r = [a for a in r.get('reproductions', []) if not a.get('invalidated')]
    live_v = [a for a in r.get('verifications', []) if not a.get('invalidated')]
    if not r.get('videoOnly'):
        r['status']['reproduced'] = 'community' if live_r else 'none'
    r['status']['verified'] = ('confirmed' if any(a.get('expert') for a in live_v) else
                               'provisional' if live_v else 'none')
    if r['status'].get('console') != 'imported' and not r.get('videoOnly'):
        live_c = [a for a in r.get('consoleVerifications', []) if not a.get('invalidated')]
        r['status']['console'] = 'community' if live_c else 'none'

def load_roles():
    p = ARCHIVE / 'roles.json'
    if not p.exists():
        return []
    return json.loads(p.read_text()).get('events', [])

def append_role_event(event):
    """Roles are a log, not a list: granting and removing both append."""
    p = ARCHIVE / 'roles.json'
    doc = (json.loads(p.read_text()) if p.exists()
           else {'comment': 'Every grant and every removal of a role, in order.',
                 'events': []})
    doc['events'].append(event)
    p.write_text(json.dumps(doc, indent=1) + '\n')

def held_roles(events=None):
    """Fold the log into what is held right now."""
    held = {}
    for ev in (events if events is not None else load_roles()):
        key = (ev['user'].lower(), ev['role'], ev.get('scope', ''))
        if ev['action'] == 'granted':
            held[key] = ev
        else:
            held.pop(key, None)
    return held

def load_experts():
    """The expert roster of the moment, in the shape the callers expect."""
    return [{'user': ev['user'], 'scope': ev.get('scope', '')}
            for (u, role, scope), ev in held_roles().items() if role == 'expert']

def scopes_over(game_key):
    """Every expert scope that reaches this game. They nest: 'site' covers
    everything, 'group:<key>' covers a game group (group), a system covers its
    games, 'sys/slug' covers one game."""
    reach = {'site', game_key.split('/')[0], game_key}
    p = ARCHIVE / 'groups.json'
    if p.exists():
        for gr in json.loads(p.read_text()).get('groups', []):
            if game_key in gr.get('games', []):
                reach.add('group:' + gr['key'])
    return reach

def is_site_expert(user):
    """Site-wide scope, and nothing narrower: an identity is not a game."""
    return any(e['user'].lower() == user.lower() and e['scope'] == 'site'
               for e in load_experts())

def already_covers(user, scope):
    """Does this member already speak for that scope, by any route?

    Appointing somebody to a game they already cover through the system or the
    group adds no authority and only clutters the roster, so it is refused
    rather than recorded. This is also what lets the panel leave them out of
    the list instead of offering a name that would be rejected.
    """
    for s in {e['scope'] for e in load_experts() if e['user'].lower() == user.lower()}:
        if s == scope or scope_covers(s, scope):
            return s
    return None

def expert_covers(user, game_key):
    reach = scopes_over(game_key)
    return any(e['user'].lower() == user.lower() and e['scope'] in reach
               for e in load_experts())

def load_groups():
    p = ARCHIVE / 'groups.json'
    if not p.exists():
        return {'comment': 'Game groups: one family of games, across every system it '
                           'appeared on.', 'groups': []}
    return json.loads(p.read_text())

def save_groups(doc):
    doc['groups'].sort(key=lambda g: g['title'].lower())
    (ARCHIVE / 'groups.json').write_text(json.dumps(doc, indent=1) + '\n')

def covers_group(user, gr):
    """May this expert speak for this group?

    Holding the group's own scope, or a scope over every game in it. An empty
    group reaches nobody, so only site scope covers one: otherwise the first
    game added would decide, after the fact, who had authority all along.
    """
    scopes = {e['scope'] for e in load_experts() if e['user'].lower() == user.lower()}
    if 'site' in scopes or ('group:' + gr['key']) in scopes:
        return True
    games = gr.get('games', [])
    if not games:
        # An empty group reaches nobody, so nothing can be derived from it.
        # Whoever made it keeps it, or emptying a group would lock its own
        # author out of the thing they just made.
        return (gr.get('createdBy') or '').lower() == user.lower()
    return all(expert_covers(user, g) for g in games)

def log_deletion(kind, key, title, by, reason, moved_to=None):
    """The deleted thing is gone, so the log is the only readable remnant of
    the act: appended before the deletion commits, in the same commit."""
    p_ = ARCHIVE / 'deletions.json'
    doc = (json.loads(p_.read_text()) if p_.exists()
           else {'comment': 'What was deleted outright, by whom, and why. The thing '
                            'itself is gone; this is where the act stays readable.',
                 'events': []})
    entry = {'kind': kind, 'key': key, 'title': title, 'by': by,
             'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(), 'reason': reason}
    if moved_to:
        entry['movedTo'] = moved_to
    doc['events'].append(entry)
    p_.write_text(json.dumps(doc, indent=1) + '\n')

def log_edit(kind, key, field, old_v, new_v, by, reason):
    """Git history carries the diff; this log carries the account: what field
    of what, by whom, from what to what, and why."""
    p_ = ARCHIVE / 'edits.json'
    doc = (json.loads(p_.read_text()) if p_.exists()
           else {'comment': 'Every expert modification of the record, field by '
                            'field. Git history carries the diffs; this carries '
                            'the account.', 'events': []})
    doc['events'].append({'kind': kind, 'key': key, 'field': field,
                          'from': str(old_v)[:300], 'to': str(new_v)[:300],
                          'by': by, 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(),
                          'reason': reason})
    p_.write_text(json.dumps(doc, indent=1) + '\n')

def is_uncl_run(r):
    return (r.get('category') or {}).get('goal') == 'unclassified'

def case_derived_status(case):
    """Deterministic case resolution — must match the archive CI's derivation."""
    n = len(case.get('verifiers', []))
    votes = {v['user'].lower(): v['reaffirm'] for v in case.get('reaffirmations', [])}
    yes = sum(1 for x in votes.values() if x)
    no = len(votes) - yes
    if yes * 2 > n:
        return 'closed'
    if len(votes) == n or no * 2 >= n:
        return 'upheld'
    return 'open'

def next_report_id():
    ids = [0]
    for rj in ARCHIVE.glob('games/*/*/runs/*/run.json'):
        for rep in json.loads(rj.read_text()).get('reports', []):
            ids.append(rep['id'])
    return max(ids) + 1

def scope_exists(scope):
    """A scope has to point at something real, or it grants authority over nothing."""
    if scope == 'site':
        return True
    if scope.startswith('group:'):
        p = ARCHIVE / 'groups.json'
        if not p.exists():
            return False
        return any(g['key'] == scope[6:]
                   for g in json.loads(p.read_text()).get('groups', []))
    if '/' in scope:
        return (ARCHIVE / 'games' / scope / 'game.json').is_file()
    return scope in json.loads((ARCHIVE / 'systems.json').read_text())

def scope_covers(wider, narrower):
    """Can an expert holding `wider` appoint into `narrower`?

    Appointment runs downward and never sideways: site appoints anything, a
    system or a group appoints the games inside it, a game expert appoints
    nobody. Equal scopes do not qualify, or an expert could clone themselves
    indefinitely without anybody wider agreeing.
    """
    if wider == narrower:
        return False
    if wider == 'site':
        return True
    if wider.startswith('group:'):
        p = ARCHIVE / 'groups.json'
        if not p.exists():
            return False
        gr = next((g for g in json.loads(p.read_text()).get('groups', [])
                   if g['key'] == wider[6:]), None)
        return bool(gr) and narrower in gr.get('games', [])
    if '/' not in wider:                      # a system covers its own games
        return narrower.startswith(wider + '/')
    return False

def is_founder(user):
    return any(u == user.lower() and role == 'founder'
               for (u, role, scope) in held_roles())

def is_committee(user):
    return any(u == user.lower() and role == 'committee'
               for (u, role, scope) in held_roles())

def is_editor(user):
    """The editor role: full control over the library's shape (groups,
    categories, game identity, which category a run sits in), no power over
    people and none over the runs themselves."""
    return any(u == user.lower() and role == 'editor'
               for (u, role, scope) in held_roles())

def load_claims():
    p_ = ARCHIVE / 'claims.json'
    if not p_.exists():
        return {'comment': 'Requests to be handed a held name, and how each was '
                           'answered. No email address is ever written here: the '
                           'archive is public and a request is not a reason to '
                           'publish somebody\'s address.', 'requests': []}
    return json.loads(p_.read_text())

def save_claims(doc):
    (ARCHIVE / 'claims.json').write_text(json.dumps(doc, indent=1) + '\n')

def may_decide_claims(user):
    """The Steering Committee assesses identity. Nobody else, on any route.

    Expert scope is authority over games: what a category means, whether a run
    meets it, which games are real. Deciding that a person is who they say they
    are is a different kind of judgement, and it belongs to the body that is
    accountable for the community. This gates both the filed-claim route and
    the direct attestation.
    """
    return is_committee(user)

