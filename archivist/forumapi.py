"""Discourse: admin API calls, private messages, the discussion
topics every run and game gets, and the forum-group
projections of the archive's roles (the archive decides;
the forum only displays)."""
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
    ARCHIVE,
    ARCHIVE_TREE_URL,
    BOT_USER,
    COMMITTEE_GROUP,
    DISCOURSE_KEY,
    DISCOURSE_URL,
    GAMES_CATEGORY_ID,
    ROLE_GROUP,
    SITE_URL,
)
from identity import (
    _renames,
    forum_name,
    mask_email,
)
from records import (
    held_roles,
)

avatar_cache = {}   # user_lower -> (url_or_None, expires)

def avatar_for(user):
    """Small avatar URL from the forum's public profile, cached an hour."""
    now = time.time()
    hit = avatar_cache.get(user.lower())
    if hit and hit[1] > now:
        return hit[0]
    url = None
    try:
        req = urllib.request.Request(
            f'{DISCOURSE_URL}/u/{urllib.parse.quote(user)}.json',
            headers={'User-Agent': 'toolAssisted.run archivist (avatar)'})
        with urllib.request.urlopen(req, timeout=8) as resp:
            tmpl = json.loads(resp.read())['user']['avatar_template']
        url = (DISCOURSE_URL + tmpl if tmpl.startswith('/') else tmpl).replace('{size}', '48')
    except Exception:
        pass
    avatar_cache[user.lower()] = (url, now + 3600)
    return url

def ensure_topic(run, game_title, system, slug, goal_label, authors, kind='submitted'):
    """Give a run its discussion topic and remember it on the run.

    Every run needs one, because the site shows the thread on the run page:
    a run without a topic simply has no discussion. Best effort: a forum
    hiccup must never cost the archival."""
    if not DISCOURSE_KEY or run.get('forum'):
        return None
    who = ', '.join(authors)
    if kind == 'imported':
        title = f'{game_title} ({goal_label}) by {who} [{run["id"]}]'
        body = (f'**{game_title}** ({goal_label}) by {who}.\n\n'
                f'[Run page]({SITE_URL}/runs/{run["id"]}/) · '
                f'[files in the archive]({ARCHIVE_TREE_URL}/games/{system}/{slug}/runs/{run["id"]})'
                + (f' · [original publication]({run["imported"]["source"]})'
                   if (run.get("imported") or {}).get("source") else '')
                + '\n\nImported from a trusted site, where it was verified and '
                  'reproduced before joining this archive.')
    else:
        title = f'{game_title} ({goal_label}) by {who} [{run["id"]}]'
        body = (f'A new run was archived in **{game_title}** ({goal_label}).\n\n'
                f'Authors: {who}\n\n'
                f'[Run page]({SITE_URL}/runs/{run["id"]}/) · '
                f'[files in the archive]({ARCHIVE_TREE_URL}/games/{system}/{slug}/runs/{run["id"]})\n\n'
                f'Status: **pending**, awaiting verification.')
    try:
        req = urllib.request.Request(f'{DISCOURSE_URL}/posts.json', method='POST',
            headers={'Api-Key': DISCOURSE_KEY, 'Api-Username': 'archivist',
                     'Content-Type': 'application/json'},
            data=json.dumps({'title': title[:255], 'raw': body,
                             'category': GAMES_CATEGORY_ID,
                             'tags': [f'{system}-{slug}'[:60]]}).encode())
        with urllib.request.urlopen(req, timeout=20) as r:
            topic = json.loads(r.read())
        if topic.get('topic_id'):
            run['forum'] = {'topicId': int(topic['topic_id']),
                            'url': f'{DISCOURSE_URL}/t/{topic["topic_id"]}'}
            return run['forum']['url']
    except Exception:                                         # noqa: BLE001
        return None
    return None

def ensure_game_topic(system, slug, title):
    """Give a game its anchor topic under its tag, remembered in game.json.

    The tag page is the game's forum home, and a tag only exists once a topic
    carries it: without an anchor a game had no forum page until its first
    run, and imported games whose tags Discourse truncated had none at all.
    Idempotent and best-effort; the caller commits game.json."""
    if not DISCOURSE_KEY:
        return None
    gfile = ARCHIVE / 'games' / system / slug / 'game.json'
    if not gfile.exists():
        return None
    game = json.loads(gfile.read_text())
    if game.get('forum'):
        return game['forum'].get('url')
    body = (f'Discussion for **{game.get("title") or title}**.\n\n'
            f'[Game page]({SITE_URL}/games/{system}/{slug}/) · every run archived '
            f'in it gets its own topic under this tag.')
    try:
        req = urllib.request.Request(f'{DISCOURSE_URL}/posts.json', method='POST',
            headers={'Api-Key': DISCOURSE_KEY, 'Api-Username': 'archivist',
                     'Content-Type': 'application/json'},
            data=json.dumps({'title': f'{game.get("title") or title} [{system}/{slug}]'[:255],
                             'raw': body, 'category': GAMES_CATEGORY_ID,
                             'tags': [f'{system}-{slug}'[:60]]}).encode())
        with urllib.request.urlopen(req, timeout=20) as r:
            topic = json.loads(r.read())
        if topic.get('topic_id'):
            game['forum'] = {'topicId': int(topic['topic_id']),
                             'url': f'{DISCOURSE_URL}/t/{topic["topic_id"]}'}
            gfile.write_text(json.dumps(game, indent=1) + '\n')
            return game['forum']['url']
    except Exception:                                         # noqa: BLE001
        return None
    return None

def discourse_api(path, method='GET', payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(DISCOURSE_URL + path, data=body, method=method,
                                 headers={'Api-Key': DISCOURSE_KEY, 'Api-Username': 'eien86',
                                          'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read() or b'{}')

def unlock_forum_username(claimant, tv_user):
    """After an attested claim: free the held name and rename the
    claimant's forum account to it. Best-effort — returns a status string."""
    _renames['built'] = False   # the author record naming the claimant is written
    if not DISCOURSE_KEY:
        return 'forum key missing; rename skipped'
    new_name = forum_name(tv_user)
    if claimant.lower() == new_name.lower():
        return 'already using the name'
    try:
        # drop the name from the reserved list so the rename can land
        settings = discourse_api('/admin/site_settings.json')
        current = next((s.get('value') or '' for s in settings['site_settings']
                        if s['setting'] == 'reserved_usernames'), '')
        entries = [e for e in current.split('|') if e and e.lower() != tv_user.lower()
                   and e.lower() != new_name.lower()]
        req = urllib.request.Request(
            DISCOURSE_URL + '/admin/site_settings/reserved_usernames',
            data=urllib.parse.urlencode({'reserved_usernames': '|'.join(entries)}).encode(),
            method='PUT', headers={'Api-Key': DISCOURSE_KEY, 'Api-Username': 'eien86'})
        urllib.request.urlopen(req, timeout=20)
        discourse_api(f'/u/{urllib.parse.quote(claimant)}/preferences/username.json',
                      'PUT', {'new_username': new_name})
        return f'forum account renamed to {new_name}'
    except Exception as e:
        return f'rename failed ({e}); an admin can rename manually'

def forum_account_exists(username):
    """True, False, or None when the forum could not be asked.

    Refusing an appointment because Discourse happened to be unreachable would
    make governance depend on the forum's uptime, so only a definite 404 counts
    as "no such account".
    """
    try:
        discourse_api(f'/u/{urllib.parse.quote(username)}.json')
        return True
    except urllib.error.HTTPError as e:
        return False if e.code == 404 else None
    except Exception:                                          # noqa: BLE001
        return None

def publish_group(role, username, add=True):
    """Print one membership change into the forum, best effort.

    The group is a projection of roles.json and nothing else. Nothing is read
    back out of it to decide anything, which is the whole point: a fact with two
    homes has no owner, and this one lives in the archive. So a failure here is
    reported and never fatal. The event is already recorded, the site already
    shows it, and the next publish repairs the projection.
    """
    target = ROLE_GROUP.get(role)
    if not target:
        return f'the {role} role has no forum group; nothing to publish'
    group, full_name = target
    if not DISCOURSE_KEY:
        return 'forum not configured; group membership unchanged'
    try:
        groups = discourse_api('/groups.json').get('groups', [])
        grp = next((g for g in groups if g['name'] == group), None)
        if not grp and add:
            grp = discourse_api('/admin/groups.json', 'POST', {'group': {
                'name': group, 'visibility_level': 0,
                'members_visibility_level': 0, 'mentionable_level': 3,
                'full_name': full_name}}).get('basic_group')
        if not grp:
            return f'no {group} group on the forum'
        verb, method = ('added to', 'PUT') if add else ('removed from', 'DELETE')
        discourse_api(f'/groups/{grp["id"]}/members.json', method,
                      {'usernames': username})
        note = f'{verb} the forum group {group}'
    except Exception as e:                                     # noqa: BLE001
        return f'forum group not updated ({e})'
    if role == 'committee':
        note += '; ' + sync_forum_admin(username, add)
    return note

def sync_forum_admin(username, add):
    """A Committee seat is a forum administrator by virtue of the seat.
    Discourse will not grant admin over the API without the acting admin
    confirming by email, so a grant is a request that lands in the
    Founder's inbox; a revocation takes effect at once. Best effort."""
    try:
        uid = discourse_api(f'/u/{urllib.parse.quote(username)}.json')['user']['id']
        if add:
            r = discourse_api(f'/admin/users/{uid}/grant_admin', 'PUT', {})
            return ('forum admin requested (confirmation email sent to the founder)'
                    if r.get('email_confirmation_required') else 'made a forum admin')
        discourse_api(f'/admin/users/{uid}/revoke_admin', 'PUT', {})
        return 'forum admin revoked'
    except Exception as e:                                     # noqa: BLE001
        return f'forum admin not synced ({e})'

def sync_expert_group(username, add=True):
    """Kept for the expert paths, which only ever touch the one group."""
    return publish_group('expert', username, add)

def publish_roles(dry=False):
    """Make every forum group match the archive. One direction, always.

    Reading membership is only ever a diff against the record, never a source
    of it, so this can run after every role event without any risk of a forum
    edit leaking back in as a fact.
    """
    report = {}
    for role, (group, _full) in ROLE_GROUP.items():
        want = sorted({ev['user'] for (u, r, s), ev in held_roles().items() if r == role})
        try:
            members = discourse_api(f'/groups/{group}/members.json').get('members', [])
            have = [m['username'] for m in members]
        except Exception as e:                                 # noqa: BLE001
            report[role] = {'error': f'could not read the forum group {group} ({e})'}
            continue
        held_lower = {w.lower() for w in want}
        add = [u for u in want if u.lower() not in {h.lower() for h in have}]
        # never evict our own bot: it is in groups to be able to post, not
        # because anybody granted it a role
        drop = [u for u in have if u.lower() not in held_lower and u != BOT_USER]
        entry = {'group': group, 'roster': want, 'forum': have,
                 'add': add, 'remove': drop}
        if not dry:
            entry['notes'] = ([publish_group(role, u, True) for u in add]
                              + [publish_group(role, u, False) for u in drop])
        report[role] = entry
    return report

def committee_size():
    """How many people the Committee has, which is what a majority is measured
    against. A poll where two of nine voted 'annul' is not a majority of the
    Committee, however lopsided the turnout.

    Counted from the archive. It used to be read off the forum group, which put
    the denominator of every governance vote in a place nobody owns: Discourse's
    own moderators group holds the archivist bot, any staff account added to a
    group inflates the count, and a real majority then fails for a reason nobody
    can see. It also made a vote uncountable whenever the forum was down.
    """
    return len({u for (u, role, scope) in held_roles() if role == 'committee'})

def count_votes(poll, words):
    """Votes for any option whose text carries one of these words."""
    return sum(o.get('votes', 0) for o in poll.get('options', [])
               if any(w in (o.get('html') or '').lower() for w in words))

def read_committee_poll(post_id):
    """The Committee decides in the forum, with Discourse's own poll. We only
    read it, and we refuse anything that is not a real committee decision:
    restricted to the group, public so the votes can be checked, and closed so
    the count is final.

    Returns (poll_dict, error_message).
    """
    try:
        post = discourse_api(f'/posts/{int(post_id)}.json')
    except Exception as e:                                     # noqa: BLE001
        return None, f'could not read that forum post ({e})'
    polls = post.get('polls') or []
    if not polls:
        return None, 'that post carries no poll'
    poll = polls[0]
    groups = [g.strip().lower() for g in (poll.get('groups') or '').split(',') if g.strip()]
    if COMMITTEE_GROUP not in groups:
        return None, (f'that poll is open to {groups or "everybody"}; an annulment is '
                      f'decided by the {COMMITTEE_GROUP}, so the poll must be restricted '
                      f'to it')
    if not poll.get('public'):
        return None, ('that poll is anonymous; a governance vote is public or it is '
                      'not checkable')
    if poll.get('status') != 'closed':
        return None, 'that poll is still open; close it before the result is applied'
    return poll, None

def member_email_masked(username):
    """The address the forum holds, masked, read at the moment it is needed.

    The whole address is never returned by this service, never written to the
    archive and never rendered into the site: the archive is a public git
    repository, and a claim is not a reason to publish where somebody can be
    reached.
    """
    if not DISCOURSE_KEY:
        return ''
    try:
        got = discourse_api(f'/u/{urllib.parse.quote(username)}/emails.json')
        return mask_email(got.get('email') or '')
    except Exception:                                          # noqa: BLE001
        return ''

def send_pm(username, title, body):
    """Tell somebody what was decided. Discourse mails a private message to the
    address it holds, which is how a member hears from us without us keeping
    their address ourselves."""
    if not DISCOURSE_KEY:
        return 'forum not configured; nobody was told'
    try:
        discourse_api('/posts.json', 'POST', {
            'title': title[:255], 'raw': body, 'archetype': 'private_message',
            'target_recipients': username})
        return f'{username} was told by private message, which the forum emails on'
    except Exception as e:                                     # noqa: BLE001
        return f'could not tell {username} ({e})'

def topics_for_imported(archive, run_ids):
    """Imported runs are real published works, so they get a discussion topic
    even while native test submissions deliberately do not."""
    made = 0
    for rid in run_ids:
        for rj in archive.glob(f'games/*/*/runs/{rid}/run.json'):
            run = json.loads(rj.read_text())
            if run.get('forum'):
                continue
            system, slug = run['game'].split('/')
            gfile = archive / 'games' / system / slug / 'game.json'
            title = json.loads(gfile.read_text()).get('title', slug) if gfile.exists() else slug
            goal = (run.get('category') or {}).get('goal', '')
            authors = [a['user'] for a in run.get('authors', [])]
            ensure_game_topic(system, slug, title)
            if ensure_topic(run, title, system, slug, goal, authors, kind='imported'):
                rj.write_text(json.dumps(run, indent=1) + '\n')
                made += 1
    return made

def _forum_get(path):
    req = urllib.request.Request(
        f'{DISCOURSE_URL}{path}',
        headers={'Api-Key': DISCOURSE_KEY, 'Api-Username': 'system',
                 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())

