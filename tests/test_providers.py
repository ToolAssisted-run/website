#!/usr/bin/env python3
"""The video platforms an encode may come from.

Everything that touches an encode goes through archivist/providers.py: the
archivist validating a submission, the generator rendering the player, the
submit page deciding whether to bother asking. So this suite pins the parsing
table (every URL shape each platform actually hands people), the refusals
(above all a hostile URL that merely CONTAINS a platform's name), and the
thumbnail lookup against a local mock.

Hermetic: PROVIDER_MOCK_BASE points every outbound request at a local server;
nothing here reaches the internet.

Usage: tests/test_providers.py
"""
import http.server
import importlib
import json
import os
import pathlib
import sys
import threading
import urllib.parse

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'archivist'))

JPEG = b'\xff\xd8\xff' + b'\0' * 200
PNG = b'\x89PNG\r\n\x1a\n' + b'\0' * 200

# what the mock serves, keyed by the real URL the code asks for
ROUTES = {}

failures = []


def ck(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (f'  [{detail}]' if detail and not cond else ''))
    if not cond:
        failures.append(name)


class Mock(http.server.BaseHTTPRequestHandler):
    def do_GET(self):                                    # noqa: N802
        wanted = urllib.parse.unquote(self.path.lstrip('/'))
        body = ROUTES.get(wanted)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):                           # keep the run quiet
        pass


PARSES = [
    ('https://www.youtube.com/watch?v=JLVLBFjWiG8', 'youtube', 'JLVLBFjWiG8'),
    ('https://youtube.com/watch?list=PL1&v=JLVLBFjWiG8', 'youtube', 'JLVLBFjWiG8'),
    ('https://youtu.be/JLVLBFjWiG8?t=42', 'youtube', 'JLVLBFjWiG8'),
    ('https://m.youtube.com/watch?v=JLVLBFjWiG8', 'youtube', 'JLVLBFjWiG8'),
    ('https://www.youtube.com/shorts/JLVLBFjWiG8', 'youtube', 'JLVLBFjWiG8'),
    ('https://www.nicovideo.jp/watch/sm9', 'niconico', 'sm9'),
    ('https://www.nicovideo.jp/watch/sm40012345', 'niconico', 'sm40012345'),
    ('https://sp.nicovideo.jp/watch/so12345678', 'niconico', 'so12345678'),
    ('https://nico.ms/sm500873', 'niconico', 'sm500873'),
    ('https://www.bilibili.com/video/BV1xx411c7mD', 'bilibili', 'BV1xx411c7mD'),
    ('https://www.bilibili.com/video/BV1xx411c7mD/?spm_id_from=333', 'bilibili', 'BV1xx411c7mD'),
    ('https://player.bilibili.com/player.html?bvid=BV1xx411c7mD', 'bilibili', 'BV1xx411c7mD'),
    ('https://vimeo.com/123456789', 'vimeo', '123456789'),
    ('https://player.vimeo.com/video/123456789', 'vimeo', '123456789'),
    ('https://www.dailymotion.com/video/x8abcde', 'dailymotion', 'x8abcde'),
    ('https://dai.ly/x8abcde', 'dailymotion', 'x8abcde'),
    ('https://archive.org/details/tas-encode_2026', 'archive', 'tas-encode_2026'),
    ('https://archive.org/embed/tas-encode_2026', 'archive', 'tas-encode_2026'),
]

REFUSALS = [
    ('', 'empty'),
    ('not a url at all', 'not a url'),
    ('javascript:alert(1)//youtube.com/watch?v=abcdef', 'a javascript: url'),
    ('data:text/html,<script>alert(1)</script>', 'a data: url'),
    ('https://evil.example/?u=youtube.com/watch?v=JLVLBFjWiG8',
     'a hostile url that merely contains a platform url'),
    ('https://youtube.com.evil.example/watch?v=JLVLBFjWiG8',
     'a lookalike host with the platform as a prefix'),
    ('https://notyoutube.com/watch?v=JLVLBFjWiG8', 'a host that ends with the platform'),
    ('https://www.youtube.com/', 'a platform url with no video in it'),
    ('https://vimeo.com/12', 'an id too short to be real'),
    ('https://twitch.tv/videos/123456', 'a platform we do not accept'),
]


def main():
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Mock)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.environ['PROVIDER_MOCK_BASE'] = f'http://127.0.0.1:{port}/'
    os.environ.pop('THUMB_FETCH_BASE', None)
    import providers
    importlib.reload(providers)                          # pick up the mock base

    for url, kind, vid in PARSES:
        got = providers.resolve(url)
        ck(f'{kind}: {url[:52]}',
           got is not None and got['kind'] == kind and got['id'] == vid,
           str(got))
    for url, why in REFUSALS:
        ck(f'refuses {why}', providers.resolve(url) is None, url)

    # embeds must be same-origin-safe URLs on the platform's own player
    for url, kind, vid in PARSES:
        emb = providers.resolve(url)['embed']
        ck(f'{kind} embed is an https player url', emb.startswith('https://'), emb)
    seen = {providers.resolve(u)['embed'] for u, k, v in PARSES if k == 'youtube'}
    ck('every youtube url shape yields one embed', len(seen) == 1, str(seen))

    # ---- thumbnails, direct and via an api ----
    ROUTES['https://img.youtube.com/vi/JLVLBFjWiG8/maxresdefault.jpg'] = JPEG
    data, ext = providers.thumbnail('youtube', 'JLVLBFjWiG8')
    ck('a direct thumbnail template is fetched', data == JPEG and ext == '.jpg', str(ext))

    ROUTES['https://img.youtube.com/vi/NOMAXRES123/hqdefault.jpg'] = JPEG
    data, ext = providers.thumbnail('youtube', 'NOMAXRES123')
    ck('it falls back to the next template', data == JPEG, str(data)[:40])

    ROUTES['https://ext.nicovideo.jp/api/getthumbinfo/sm9'] = (
        b'<nicovideo_thumb_response><thumb>'
        b'<thumbnail_url>https://nicovideo.cdn.example/sm9.jpg</thumbnail_url>'
        b'</thumb></nicovideo_thumb_response>')
    ROUTES['https://nicovideo.cdn.example/sm9.jpg'] = JPEG
    data, ext = providers.thumbnail('niconico', 'sm9')
    ck('an xml api answer names the thumbnail', data == JPEG and ext == '.jpg', str(ext))

    ROUTES['https://api.bilibili.com/x/web-interface/view?bvid=BV1xx411c7mD'] = json.dumps(
        {'code': 0, 'data': {'pic': '//i0.hdslb.example/frame.png'}}).encode()
    ROUTES['https://i0.hdslb.example/frame.png'] = PNG
    data, ext = providers.thumbnail('bilibili', 'BV1xx411c7mD')
    ck('a json api answer names the thumbnail, protocol-relative and all',
       data == PNG and ext == '.png', str(ext))

    ck('an unknown video has no thumbnail',
       providers.thumbnail('youtube', 'NOSUCHVIDEO')[0] is None)
    ROUTES['https://img.youtube.com/vi/NOTANIMAGE1/maxresdefault.jpg'] = b'<html>nope</html>'
    ROUTES['https://img.youtube.com/vi/NOTANIMAGE1/hqdefault.jpg'] = b'<html>nope</html>'
    ck('an answer that is not an image is refused',
       providers.thumbnail('youtube', 'NOTANIMAGE1')[0] is None)
    ROUTES['https://img.youtube.com/vi/TOOBIG12345/maxresdefault.jpg'] = JPEG + b'\0' * 300000
    ROUTES['https://img.youtube.com/vi/TOOBIG12345/hqdefault.jpg'] = JPEG + b'\0' * 300000
    ck('an oversized image is refused',
       providers.thumbnail('youtube', 'TOOBIG12345', 256 * 1024)[0] is None)

    ROUTES['https://api.bilibili.com/x/web-interface/view?bvid=BVjsjsjsjsjs'] = json.dumps(
        {'code': 0, 'data': {'pic': 'javascript:alert(1)'}}).encode()
    ck('an api that answers with a javascript: url is refused',
       providers.thumbnail_url('bilibili', 'BVjsjsjsjsjs') is None)

    # Bilibili really does answer with http:// or //, and an http image on an
    # https page never loads at all
    ROUTES['https://api.bilibili.com/x/web-interface/view?bvid=BVplainhttp'] = json.dumps(
        {'code': 0, 'data': {'pic': 'http://i0.hdslb.example/plain.jpg'}}).encode()
    ck('a plain http thumbnail url is upgraded to https',
       providers.thumbnail_url('bilibili', 'BVplainhttp')
       == 'https://i0.hdslb.example/plain.jpg',
       str(providers.thumbnail_url('bilibili', 'BVplainhttp')))
    ROUTES['https://api.bilibili.com/x/web-interface/view?bvid=BVprotorel'] = json.dumps(
        {'code': 0, 'data': {'pic': '//i0.hdslb.example/rel.jpg'}}).encode()
    ck('a protocol-relative thumbnail url becomes https',
       providers.thumbnail_url('bilibili', 'BVprotorel')
       == 'https://i0.hdslb.example/rel.jpg')

    # the site's own copy comes from the same list, so it cannot drift
    ck('every provider is named for the page copy',
       len(providers.names()) == len(providers.PROVIDERS))
    ck('the host list covers every provider',
       all(h in providers.ALL_HOSTS for p in providers.PROVIDERS for h in p['hosts']))

    srv.shutdown()
    print('---', len(failures), 'failures')
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
