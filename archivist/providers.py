"""Video platforms we accept an encode from.

One registry, read by everything: the archivist validates submissions and
fetches the run thumbnail through it, the generator renders the player and the
submit page's copy from it, and the client's live check gets its host list from
it. A platform added here appears everywhere at once, and nothing anywhere else
knows the name "YouTube".

Every entry answers four questions: which hosts are yours, how do I read the
video id out of a URL, how do I embed it, and where do I get a still frame.
The last one is the awkward one: some platforms publish an image URL you can
build, others make you ask an API first, so a provider may carry `thumbs`
(direct templates) or `thumb_api` (a request whose answer names the image), or
both.

Network access is funnelled through fetch_bytes/fetch_text so tests can point
every lookup at a local mock (PROVIDER_MOCK_BASE) instead of the real internet.
"""
import json
import os
import re
import urllib.parse
import urllib.request

UA = 'toolAssisted.run archivist (encode thumbnail)'
TIMEOUT = 15

# Tests set this to a local server; every outbound provider request is then
# rewritten to MOCK_BASE + the original URL, percent-encoded.
MOCK_BASE = os.environ.get('PROVIDER_MOCK_BASE', '')
# Kept separate and honoured for YouTube alone: it predates this module and
# several suites already point it at their own thumbnail server.
YT_THUMB_BASE = os.environ.get('THUMB_FETCH_BASE', 'https://img.youtube.com/vi/')

IMAGE_MAGIC = [(b'\xff\xd8\xff', '.jpg'), (b'\x89PNG\r\n\x1a\n', '.png'),
               (b'RIFF', '.webp')]

PROVIDERS = [
    {
        'kind': 'youtube',
        'name': 'YouTube',
        'hosts': ('youtube.com', 'm.youtube.com', 'youtu.be', 'youtube-nocookie.com'),
        'id': re.compile(r'(?:youtube\.com/watch\?(?:.*&)?v=|youtu\.be/|'
                         r'youtube\.com/shorts/|youtube\.com/embed/|'
                         r'youtube-nocookie\.com/embed/)([\w-]{6,20})'),
        'watch': 'https://youtu.be/{id}',
        'embed': 'https://www.youtube-nocookie.com/embed/{id}',
        'thumbs': ('{ytbase}{id}/maxresdefault.jpg', '{ytbase}{id}/hqdefault.jpg'),
    },
    {
        'kind': 'niconico',
        'name': 'Niconico',
        'hosts': ('nicovideo.jp', 'sp.nicovideo.jp', 'embed.nicovideo.jp', 'nico.ms'),
        'id': re.compile(r'(?:nicovideo\.jp/watch/|nico\.ms/)((?:sm|so|nm|za)?\d{1,12})'),
        'watch': 'https://www.nicovideo.jp/watch/{id}',
        'embed': 'https://embed.nicovideo.jp/watch/{id}',
        'thumb_api': ('https://ext.nicovideo.jp/api/getthumbinfo/{id}',
                      'xml', 'thumbnail_url'),
    },
    {
        'kind': 'bilibili',
        'name': 'Bilibili',
        'hosts': ('bilibili.com', 'player.bilibili.com', 'b23.tv'),
        'id': re.compile(r'(?:bilibili\.com/video/|b23\.tv/|bvid=)(BV[0-9A-Za-z]{10})'),
        'watch': 'https://www.bilibili.com/video/{id}',
        'embed': 'https://player.bilibili.com/player.html?bvid={id}&high_quality=1',
        'thumb_api': ('https://api.bilibili.com/x/web-interface/view?bvid={id}',
                      'json', 'data.pic'),
    },
    {
        'kind': 'vimeo',
        'name': 'Vimeo',
        'hosts': ('vimeo.com', 'player.vimeo.com'),
        'id': re.compile(r'vimeo\.com/(?:video/)?(\d{6,12})'),
        'watch': 'https://vimeo.com/{id}',
        'embed': 'https://player.vimeo.com/video/{id}',
        'thumb_api': ('https://vimeo.com/api/oembed.json?url=https%3A//vimeo.com/{id}',
                      'json', 'thumbnail_url'),
    },
    {
        'kind': 'dailymotion',
        'name': 'Dailymotion',
        'hosts': ('dailymotion.com', 'dai.ly'),
        'id': re.compile(r'(?:dailymotion\.com/video/|dai\.ly/)([a-zA-Z0-9]{5,12})'),
        'watch': 'https://www.dailymotion.com/video/{id}',
        'embed': 'https://www.dailymotion.com/embed/video/{id}',
        'thumbs': ('https://www.dailymotion.com/thumbnail/video/{id}',),
    },
    {
        'kind': 'archive',
        'name': 'the Internet Archive',
        'hosts': ('archive.org',),
        'id': re.compile(r'archive\.org/(?:details|embed|download)/([A-Za-z0-9._-]{3,80})'),
        'watch': 'https://archive.org/details/{id}',
        'embed': 'https://archive.org/embed/{id}',
        'thumbs': ('https://archive.org/services/img/{id}',),
    },
]

BY_KIND = {p['kind']: p for p in PROVIDERS}
ALL_HOSTS = sorted({h for p in PROVIDERS for h in p['hosts']})


def names():
    """Human list for page copy: 'YouTube, Niconico, Bilibili, …'."""
    return [p['name'] for p in PROVIDERS]


def host_of(url):
    host = urllib.parse.urlparse(url).netloc.lower().split(':')[0]
    return host[4:] if host.startswith('www.') else host


def resolve(url):
    """The provider and video id behind a URL, or None.

    The host must belong to the provider whose pattern matched: a pattern
    alone would accept https://evil.example/?x=youtube.com/watch?v=abcdef.
    """
    url = (url or '').strip()
    if not url.lower().startswith(('http://', 'https://')):
        return None
    host = host_of(url)
    for p in PROVIDERS:
        if host not in p['hosts']:
            continue
        m = p['id'].search(url)
        if not m:
            continue
        vid = m.group(1)
        return {'kind': p['kind'], 'name': p['name'], 'id': vid,
                'watch': p['watch'].format(id=vid),
                'embed': p['embed'].format(id=vid)}
    return None


def embed_url(kind, vid):
    p = BY_KIND.get(kind)
    return p['embed'].format(id=vid) if p else None


def _fetch(url, timeout=TIMEOUT):
    if MOCK_BASE:
        url = MOCK_BASE + urllib.parse.quote(url, safe='')
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_bytes(url, timeout=TIMEOUT):
    try:
        return _fetch(url, timeout)
    except Exception:                                      # noqa: BLE001
        return None


def fetch_text(url, timeout=TIMEOUT):
    data = fetch_bytes(url, timeout)
    return data.decode('utf-8', 'replace') if data else None


def _dig(obj, path):
    for part in path.split('.'):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj if isinstance(obj, str) else None


def duration_seconds(kind, vid):
    """How long the video runs, asked from the platform, for the submit
    form's Import from... time source. None when the platform will not say
    (Twitch and the Internet Archive have no anonymous answer)."""
    try:
        if kind == 'youtube':
            page = fetch_text(f'https://www.youtube.com/watch?v={vid}')
            m = re.search(r'"lengthSeconds"\s*:\s*"(\d+)"', page or '')
            return int(m.group(1)) if m else None
        if kind == 'niconico':
            xml_text = fetch_text(f'https://ext.nicovideo.jp/api/getthumbinfo/{vid}')
            m = re.search(r'<length>(?:(\d+):)?(\d+):(\d+)</length>', xml_text or '')
            if m:
                h, mnt, sec = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
                return h * 3600 + mnt * 60 + sec
            return None
        if kind == 'bilibili':
            body = fetch_text(f'https://api.bilibili.com/x/web-interface/view?bvid={vid}')
            doc = json.loads(body) if body else {}
            dur = (doc.get('data') or {}).get('duration')
            return int(dur) if isinstance(dur, (int, float)) and dur > 0 else None
        if kind == 'vimeo':
            body = fetch_text(f'https://vimeo.com/api/oembed.json?url=https%3A//vimeo.com/{vid}')
            doc = json.loads(body) if body else {}
            dur = doc.get('duration')
            return int(dur) if isinstance(dur, (int, float)) and dur > 0 else None
        if kind == 'dailymotion':
            body = fetch_text(f'https://api.dailymotion.com/video/{vid}?fields=duration')
            doc = json.loads(body) if body else {}
            dur = doc.get('duration')
            return int(dur) if isinstance(dur, (int, float)) and dur > 0 else None
    except Exception:                                      # noqa: BLE001
        return None
    return None


def thumbnail_url(kind, vid):
    """Where the still frame lives, asking the platform's API when needed."""
    p = BY_KIND.get(kind)
    if not p:
        return None
    api = p.get('thumb_api')
    if api:
        tmpl, fmt, path = api
        body = fetch_text(tmpl.format(id=vid))
        if not body:
            return None
        if fmt == 'json':
            try:
                found = _dig(json.loads(body), path)
            except ValueError:
                return None
        else:
            m = re.search(rf'<{path}>(.*?)</{path}>', body, re.S)
            found = m.group(1).strip() if m else None
        if not found:
            return None
        # Bilibili answers with a protocol-relative or plain http URL, and an
        # http image on an https page is blocked as mixed content before it
        # ever loads. Every one of these CDNs serves https.
        if found.startswith('//'):
            found = 'https:' + found
        elif found.startswith('http://'):
            found = 'https://' + found[7:]
        return found if found.startswith('https://') else None
    return None


def thumbnail(kind, vid, max_bytes=256 * 1024):
    """The still frame itself: (bytes, extension), or (None, None).

    Direct templates are tried in order first (they cost one request), then the
    API lookup. Whatever comes back must actually be an image of a size we are
    willing to keep in the archive.
    """
    p = BY_KIND.get(kind)
    if not p:
        return None, None
    candidates = [t.format(id=vid, ytbase=YT_THUMB_BASE) for t in p.get('thumbs', ())]
    if p.get('thumb_api'):
        found = thumbnail_url(kind, vid)
        if found:
            candidates.append(found)
    for url in candidates:
        data = fetch_bytes(url)
        if not data or len(data) > max_bytes:
            continue
        for magic, ext in IMAGE_MAGIC:
            if data.startswith(magic):
                LAST_THUMB_URL[(kind, vid)] = url
                return data, ext
    return None, None

# which candidate actually answered, per video: the submit preview shows the
# same image the archive will keep, not the first template (#29: YouTube has
# no maxresdefault for many videos, and the preview showed a broken frame)
LAST_THUMB_URL = {}

def thumbnail_source(kind, vid, max_bytes=256 * 1024):
    """The URL of the still frame that really exists, fetching to find out."""
    key = (kind, vid)
    if key not in LAST_THUMB_URL:
        thumbnail(kind, vid, max_bytes)
    return LAST_THUMB_URL.get(key)
