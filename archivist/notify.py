"""Discord notifications: fire-and-forget, one line each, held
until the page they link answers so a posted link is never 404."""
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
    SITE_URL,
    ARCHIVE,
    DISCORD_WEBHOOK,
    LOG,
    NOTIFY_LINK_WAIT,
)

def wait_until_live(url, deadline, poll=15):
    """Block until the URL answers 200, or the deadline passes. Returns
    whether it ever did; the caller posts either way, because the event is
    real whatever the deploy pipeline is doing."""
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url, method='HEAD',
                                         headers={'User-Agent': 'toolAssisted.run archivist'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    return True
        except Exception:                                      # noqa: BLE001
            pass
        time.sleep(min(poll, max(1.0, deadline - time.time())))
    return False

def profile_slug(name):
    """URL segment of a member profile: mirror of the generator's
    model.profile_slug, so the links land on the pages it builds."""
    return re.sub(r'[^a-z0-9._-]+', '-', name.lower()).strip('-.') or 'author'


def member_md(name):
    """A member's name carrying the link to their profile.

    Only registered members get the link: a credited name that never claimed
    an account here has no profile page, and a link to a page that does not
    exist helps nobody. Mirrors the generator's is_member (record exists and
    is claimed), reading the record fresh so a name registered a moment ago
    links from its very next mention."""
    import selfimport
    try:
        rec = json.loads((ARCHIVE / 'authors'
                          / f'{selfimport.slugify(name)}.json').read_text())
    except (OSError, ValueError):
        return name
    if not rec.get('claimed'):
        return name
    return f'[{name}](<{SITE_URL}/authors/{profile_slug(rec.get("username") or name)}/>)'


def category_label(r):
    """What the ranking calls the run's category, as the site says it: the
    option's label, each dimension joined by a cross, and the subcategory
    after a middle dot. Falls back to the stored key, because a notice with
    a rough name beats a notice with none."""
    category = r.get('category') or {}
    if category.get('goal') == 'unclassified':
        return 'Unclassified'
    system, slug = r['game'].split('/')
    try:
        dimensions = json.loads(
            (ARCHIVE / 'games' / system / slug / 'categories.json').read_text())['dimensions']
    except (OSError, ValueError, KeyError):
        return category.get('goal', '')
    labels, option = [], None
    for dimension in dimensions:
        chosen = category.get(dimension['key'])
        found = next((o for o in dimension['options'] if o['key'] == chosen), None)
        if found:
            labels.append(found['label'])
            if dimension['key'] == 'goal':
                option = found
    label = ' \u00d7 '.join(labels) or category.get('goal', '')
    sub = next((s for s in (option or {}).get('subcategories', [])
                if s['key'] == category.get('sub')), None)
    return f'{label} \u00b7 {sub["label"]}' if sub else label


def movie_md(r, title=None):
    """The run as people say it: [SNES] Prince of Persia by a, b — the
    name carrying the link. Discord keeps nested brackets in a link label
    as text on its own; escaping them shows literal backslashes."""
    system, slug = r['game'].split('/')
    if title is None:
        gfile = ARCHIVE / 'games' / system / slug / 'game.json'
        try:
            title = json.loads(gfile.read_text()).get('title', slug)
        except OSError:
            title = slug
    who = ', '.join(member_md(a['user']) for a in r.get('authors', []))
    return (f'[[{system.upper()}] {title}](<{SITE_URL}/runs/{r["id"]}/>)'
            + (f' by {who}' if who else ''))


# A notification that waits for a deploy is a daemon thread, and a service
# restart (every code deploy) used to take the waiting message with it: the
# run stayed archived, Discord never heard. The spool file beside the
# checkout carries every pending message across restarts; startup replays
# whatever a previous process left behind.
SPOOL = pathlib.Path(os.environ.get('NOTIFY_SPOOL',
                                    str(ARCHIVE.parent / 'notify-spool.json')))
_spool_lock = threading.Lock()

def _spool_read():
    try:
        return json.loads(SPOOL.read_text())
    except (OSError, ValueError):
        return []

def _spool_write(items):
    try:
        SPOOL.write_text(json.dumps(items))
    except OSError as exc:
        LOG.warning('notify spool not writable: %s', exc)

def _spool_add(entry):
    with _spool_lock:
        _spool_write(_spool_read() + [entry])

def _spool_drop(eid):
    with _spool_lock:
        _spool_write([x for x in _spool_read() if x.get('id') != eid])

def _deliver(entry):
    """One spooled message: hold for its page, post, unspool. The attempt
    is made exactly once past the deadline; a failure is logged and the entry
    still leaves the spool, so a broken webhook cannot pile up retries."""
    wait_for = entry.get('wait_for')
    if wait_for and entry.get('deadline'):
        if not wait_until_live(wait_for, entry['deadline']):
            LOG.warning('posting to discord with %s still unreachable',
                        wait_for)
    try:
        req = urllib.request.Request(
            DISCORD_WEBHOOK, method='POST',
            headers={'Content-Type': 'application/json',
                     'User-Agent': 'toolAssisted.run archivist'},
            data=json.dumps({'content': entry['text'][:1900],
                             # nothing we post should ever ping anybody
                             'allowed_mentions': {'parse': []},
                             # a picture where there is one: the run's
                             # thumbnail, served by the site itself
                             **({'embeds': [{'image': {'url': entry['image']}}]}
                                if entry.get('image') else {})}).encode())
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:                                 # noqa: BLE001
        LOG.warning('discord notification failed: %s', e)
    _spool_drop(entry['id'])

def notify_discord(text, wait_for=None, image=None):
    """Tell the Discord server something happened, best effort.

    Fire-and-forget in a background thread: a notification is a courtesy, so
    it must never slow an action down or fail one, and with no webhook
    configured it is silently nothing. Only ever called AFTER the archive
    write succeeded, so Discord never hears of things that did not happen.

    wait_for is the page the message links to: the post is held until that
    page actually answers, so a link in Discord is never a 404. If the deploy
    pipeline is stuck the message still goes out at the deadline, with a
    warning in our log: the event is real either way. The message is spooled
    first, so a restart mid-wait delays it instead of losing it.
    """
    if not DISCORD_WEBHOOK:
        return
    entry = {'id': secrets.token_hex(8), 'text': text, 'wait_for': wait_for,
             'image': image,
             'deadline': (time.time() + NOTIFY_LINK_WAIT
                          if wait_for and NOTIFY_LINK_WAIT > 0 else None)}
    _spool_add(entry)
    threading.Thread(target=_deliver, args=(entry,), daemon=True).start()

def replay_spool():
    """Deliver whatever a previous process left waiting; called at startup.
    Stored deadlines are absolute, so a message whose page deployed during
    the restart posts immediately."""
    if not DISCORD_WEBHOOK:
        return
    for entry in _spool_read():
        LOG.info('replaying spooled discord notification %s', entry.get('id'))
        threading.Thread(target=_deliver, args=(entry,), daemon=True).start()

