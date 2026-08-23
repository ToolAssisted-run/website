"""Environment, limits and shared constants. Imports nothing local."""
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

import movieparse

ARCHIVE = pathlib.Path(os.environ.get('ARCHIVE_DIR', '/opt/archivist/archive'))

SUBMIT_KEY = os.environ['SUBMIT_KEY']

BRANCH = os.environ.get('ARCHIVIST_BRANCH', 'main')

GIT_SSH = os.environ.get('GIT_SSH_COMMAND', 'ssh -i /opt/archivist/deploy_key -o StrictHostKeyChecking=accept-new')

DISCOURSE_URL = os.environ.get('DISCOURSE_URL', 'https://forum.toolassisted.run')

# Every outgoing request names the archivist. The forum sits behind Cloudflare
# since 2026-08-22, whose bot protection answers urllib's default agent
# ("Python-urllib/3.x") with 403; a named agent passes. Installed once here,
# so every urlopen in every module carries it without each call site knowing.
USER_AGENT = 'toolAssisted.run archivist (+https://toolassisted.run)'
_opener = urllib.request.build_opener()
_opener.addheaders = [('User-Agent', USER_AGENT)]
urllib.request.install_opener(_opener)

DISCOURSE_KEY = os.environ.get('DISCOURSE_KEY', '')

BOT_USER = 'archivist'          # our own account, never a role holder

GAMES_CATEGORY_ID = int(os.environ.get('GAMES_CATEGORY_ID', '12'))
# one topic per archived run lives in Movies; a game's anchor topic in Games
MOVIES_CATEGORY_ID = int(os.environ.get('MOVIES_CATEGORY_ID', '13'))

SITE_URL = 'https://toolassisted.run'

ARCHIVE_TREE_URL = ('https://github.com/ToolAssisted-run/archive/tree/'
                    + os.environ.get('ARCHIVIST_BRANCH', 'main'))

DUMPS_DIR = pathlib.Path(os.environ.get('DUMPS_DIR', '/opt/archivist/tasvideos-dumps'))

THUMB_FETCH_BASE = os.environ.get('THUMB_FETCH_BASE', 'https://img.youtube.com/vi/')

YT_ID_RE = re.compile(r'(?:youtube\.com/watch\?(?:.*&)?v=|youtu\.be/|youtube\.com/shorts/)([\w-]{6,20})')

CLAIM_FETCH_BASE = os.environ.get('CLAIM_FETCH_BASE', 'https://tasvideos.org/HomePages/')

SSO_SECRET = os.environ.get('DISCOURSE_CONNECT_SECRET', '')

SESSION_SECRET = os.environ.get('SESSION_SECRET', '')

SELF_URL = os.environ.get('SELF_URL', 'https://forum.toolassisted.run:8100')

SITE_ORIGIN = os.environ.get('SITE_ORIGIN', 'https://toolassisted.run')

SESSION_TTL = 14 * 24 * 3600

MOVIE_EXTS = set(movieparse.PARSERS) | set(movieparse.KNOWN_UNPARSED)

ATTACH_EXTS = {'.txt', '.md', '.ini', '.cfg', '.conf', '.toml', '.json', '.yaml',
               '.yml', '.xml', '.lua', '.sync', '.properties'}

MOVIE_MAX = 32 * 1024 * 1024   # intake cap. A console TAS can be genuinely

NOTES_MAX = 256 * 1024

ATTACH_MAX_EACH = 128 * 1024

ATTACH_MAX_TOTAL = 512 * 1024

ATTACH_MAX_COUNT = 8

SHOT_MAX_EACH = 512 * 1024

SHOT_MAX_TOTAL = 8 * 1024 * 1024

THUMB_MAX = 256 * 1024

IMAGE_MAGIC = {'.png': [b'\x89PNG\r\n\x1a\n'], '.jpg': [b'\xff\xd8\xff'],
               '.jpeg': [b'\xff\xd8\xff'], '.webp': [b'RIFF']}

ACT_NOTES_MAX = 2000

LOG = logging.getLogger('archivist')

def slugify(s):
    s = re.sub(r"['’]", '', s.lower())
    s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')
    return s[:60]

REFRESH_MAX_AGE = float(os.environ.get('ARCHIVE_REFRESH_SECONDS', '20'))

# run visit counters: operational state, not an archive fact, so the file
# sits beside the checkout, never inside it (refresh git-cleans the tree)
VISITS_FILE = pathlib.Path(os.environ.get('VISITS_FILE',
                                          str(ARCHIVE.parent / 'visits.json')))

EXPERT_GROUP = 'experts'

COMMITTEE_GROUP = 'committee'

ROLE_GROUP = {'expert': (EXPERT_GROUP, 'Experts'),
              'committee': (COMMITTEE_GROUP, 'Steering Committee')}

DISCORD_WEBHOOK = os.environ.get('DISCORD_WEBHOOK_URL', '')

DISCOURSE_HOOK_SECRET = os.environ.get('DISCOURSE_HOOK_SECRET', '')

NOTIFY_LINK_WAIT = float(os.environ.get('NOTIFY_LINK_WAIT_SECONDS', '900'))

RECONCILE_SECONDS = float(os.environ.get('ROLE_RECONCILE_SECONDS', '600'))
# a fine-grained token (website repo, Actions read-write) that lets the
# archivist ask the site to rebuild the moment a push lands; unset = the
# archive repo's CI dispatch alone carries it, ~15 s slower
WEBSITE_DISPATCH_TOKEN = os.environ.get('WEBSITE_DISPATCH_TOKEN', '')



def now_iso():
    """The arrival moment, seconds, UTC: every event record carries it
    beside its human-readable date, and the site orders by it."""
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
