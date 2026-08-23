"""Deployment configuration: where the archive is, where the site goes,
and the constants that name the deployment (URLs, refs, today).
Everything else imports from here; this imports from nothing local."""
import datetime
import html
import os
import json
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.parse

# the archivist's provider registry (accepted video platforms) is shared by
# the generator; the archivist directory joins the path once, here
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'archivist'))

def urlencode_q(s):
    return urllib.parse.quote(s, safe='')

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

ARCHIVE_REF = os.environ.get('ARCHIVE_REF', 'main')


ARCHIVE_RAW = f'https://raw.githubusercontent.com/ToolAssisted-run/archive/{ARCHIVE_REF}'

ARCHIVE_TREE = f'https://github.com/ToolAssisted-run/archive/blob/{ARCHIVE_REF}'

FORUM = 'https://forum.toolassisted.run'

ARCHIVIST = 'https://forum.toolassisted.run/archivist'

ARCHIVE = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else pathlib.Path.home() / 'ToolAssisted-archive')

OUT = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else 'stage-build')

TODAY = datetime.date.today()

def site_commit():
    """Short hash of the website repo commit this build came from — shown in
    the footer, linked to GitHub, so any page identifies its exact code."""
    try:
        return subprocess.run(['git', 'rev-parse', '--short', 'HEAD'],
                              cwd=pathlib.Path(__file__).resolve().parent,
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return None

SITE_COMMIT = site_commit()

def archive_serial():
    """How many commits the archive's history carries: a monotonically
    increasing stamp of the state this build was made from. The archivist
    answers every write with the serial it produced; the client compares
    the two to know when a change is actually being served."""
    try:
        return int(subprocess.run(['git', 'rev-list', '--count', 'HEAD'],
                                  cwd=ARCHIVE, capture_output=True, text=True,
                                  check=True).stdout)
    except Exception:
        return 0

ARCHIVE_SERIAL = archive_serial()

