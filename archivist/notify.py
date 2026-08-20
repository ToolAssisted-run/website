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

def movie_md(r, title=None):
    """The run as people say it: [SNES] Prince of Persia by a, b — the
    name carrying the link (brackets escaped so Discord keeps them as text)."""
    system, slug = r['game'].split('/')
    if title is None:
        gfile = ARCHIVE / 'games' / system / slug / 'game.json'
        try:
            title = json.loads(gfile.read_text()).get('title', slug)
        except OSError:
            title = slug
    who = ', '.join(a['user'] for a in r.get('authors', []))
    return (f'[\\[{system.upper()}\\] {title}](<{SITE_URL}/runs/{r["id"]}/>)'
            + (f' by {who}' if who else ''))


def notify_discord(text, wait_for=None, image=None):
    """Tell the Discord server something happened, best effort.

    Fire-and-forget in a background thread: a notification is a courtesy, so
    it must never slow an action down or fail one, and with no webhook
    configured it is silently nothing. Only ever called AFTER the archive
    write succeeded, so Discord never hears of things that did not happen.

    wait_for is the page the message links to: the post is held until that
    page actually answers, so a link in Discord is never a 404. If the deploy
    pipeline is stuck the message still goes out at the deadline, with a
    warning in our log: the event is real either way.
    """
    if not DISCORD_WEBHOOK:
        return
    def work():
        if wait_for and NOTIFY_LINK_WAIT > 0:
            if not wait_until_live(wait_for, time.time() + NOTIFY_LINK_WAIT):
                LOG.warning('posting to discord with %s still unreachable '
                                   'after %ss', wait_for, NOTIFY_LINK_WAIT)
        try:
            req = urllib.request.Request(
                DISCORD_WEBHOOK, method='POST',
                headers={'Content-Type': 'application/json',
                         'User-Agent': 'toolAssisted.run archivist'},
                data=json.dumps({'content': text[:1900],
                                 # nothing we post should ever ping anybody
                                 'allowed_mentions': {'parse': []},
                                 # a picture where there is one: the run's
                                 # thumbnail, served by the site itself
                                 **({'embeds': [{'image': {'url': image}}]}
                                    if image else {})}).encode())
            urllib.request.urlopen(req, timeout=10).read()
        except Exception as e:                                 # noqa: BLE001
            LOG.warning('discord notification failed: %s', e)
    threading.Thread(target=work, daemon=True).start()

