"""Reading HTTP endpoints; registered by the executable archivist entrypoint."""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from flask import jsonify, request
import movieparse
import providers
from settings import ARCHIVE, allowed_movie_exts, DISCOURSE_KEY, DISCOURSE_URL, MOVIE_MAX, SITE_ORIGIN, THUMB_MAX
from webutil import fail
from identity import origin_ok, session_user
from forumapi import _forum_get, forum_account_exists, reserved_usernames


def register(app, *, DISCUSSION_CACHE, ENCODE_CACHE, MOVIE_TOO_LARGE, _helper_gate, _name_seen, _search_index):
    """Attach the reading endpoints with explicit request dependencies."""

    @app.get('/api/name/status')
    def name_status():
        """What stands between a visitor and the username they typed.

        Discourse refuses a held name with the same words it uses for a name
        somebody already registered, so the person whose name it is gets told to
        try "Nymx1" instead of being told that the name is theirs to claim. This
        says which of the two it is; the forum's signup form asks, and explains.

        Who: anybody (the signup form has no session yet)
        Reads: query arg name
        Answers: {ok, name, state} where state is free, taken, held, or unknown
            (the forum could not be asked, which is never read as free), plus
            {claim} naming the page that starts a claim when the name is held
        """
        name = (request.args.get('name') or '').strip()
        if not 1 <= len(name) <= 60:
            return fail('a username is between 1 and 60 characters')
        now = time.monotonic()
        cached = _name_seen.get(name.lower())
        if cached and now - cached[0] < 60:
            state = cached[1]
        else:
            exists = forum_account_exists(name)
            reserved = reserved_usernames()
            if exists:
                state = 'taken'
            elif exists is None or reserved is None:
                state = 'unknown'
            else:
                state = 'held' if name.lower() in reserved else 'free'
            if len(_name_seen) > 4000:
                _name_seen.clear()
            _name_seen[name.lower()] = (now, state)
        out = {'ok': True, 'name': name, 'state': state}
        if state == 'held':
            out['claim'] = SITE_ORIGIN + '/claim/'
        resp = jsonify(out)
        resp.headers['Cache-Control'] = 'public, max-age=60'
        return resp


    @app.get('/api/search')
    def search():
        """Type-to-find for the pickers on the panels (issue #56): the matching
        members or games, a page at a time, so no page carries the whole list.

        Who: anybody
        Reads: query args kind (members | games), q (at least one character),
            limit (at most 50, default 20)
        Answers: {ok, kind, items}; a member item is its username, a game item
            {key, title, system, group}
        """
        kind = (request.args.get('kind') or '').strip()
        query = (request.args.get('q') or '').strip().lower()[:80]
        try:
            limit = max(1, min(50, int(request.args.get('limit') or 20)))
        except ValueError:
            limit = 20
        if kind not in ('members', 'games', 'runs'):
            return fail('kind must be members, games or runs')
        if not query:
            return fail('q must say what to look for')
        index = _search_index()
        if kind == 'members':
            hits = [m for m in index['members'] if query in m.lower()]
            hits.sort(key=lambda m: (not m.lower().startswith(query), m.lower()))
        else:
            hits = [g for g in index[kind] if query in g['title'].lower() or query in g['key'].lower()]
            hits.sort(key=lambda g: (not g['title'].lower().startswith(query), g['title'].lower()))
        resp = jsonify({'ok': True, 'kind': kind, 'items': hits[:limit]})
        resp.headers['Cache-Control'] = 'no-store'
        return resp


    @app.post('/api/movie/inspect')
    def movie_inspect():
        """Read a movie file the way a submission would, and say what it holds,
        before anything is submitted: the submit form's Import from... offers
        what was read; the author states the values either way. Nothing is
        stored.

        Who: anybody (the file is the caller's own)
        Reads: file movie; form field game (system/slug, for the frame rate when
            the movie names none)
        Answers: {ok, format, known, parsed, frames, fps, seconds, rerecords,
            igt (the game's own timer in seconds, when the movie carries one)};
            400 for a missing, empty, oversized or unknown-format file
        """
        gate = _helper_gate()
        if gate:
            return gate
        movie_upload = request.files.get('movie')
        if not movie_upload or not movie_upload.filename:
            return fail('attach the movie file')
        ext = movie_upload.filename.rsplit('.', 1)[-1].lower()
        movie_bytes = movie_upload.read()
        if not movie_bytes:
            return fail('movie file is empty')
        if len(movie_bytes) > MOVIE_MAX:
            return fail(MOVIE_TOO_LARGE)
        known = ext in allowed_movie_exts()
        parsed = movieparse.parse(movie_upload.filename, movie_bytes) if known else {'ok': False, 'error': f'.{ext} is not a format the archive can read'}
        fps = parsed.get('fps') if parsed.get('ok') else None
        frames = parsed.get('frames') if parsed.get('ok') else None
        # a movie that names no frame rate runs at its system's (form field game)
        game_key = (request.form.get('game') or '').strip()
        if frames and not fps and re.fullmatch(r'[a-z0-9-]+/[a-z0-9-]+', game_key):
            fps = json.loads((ARCHIVE / 'systems.json').read_text()).get(game_key.split('/')[0], {}).get('fps')
        resp = jsonify({'ok': True, 'format': ext, 'known': known, 'parsed': bool(parsed.get('ok')),
                        'frames': frames, 'fps': fps, 'rerecords': parsed.get('rerecords') if parsed.get('ok') else None,
                        'seconds': (frames / fps) if (frames and fps) else None,
                        # a game core may carry the game's own timer; the form
                        # offers it for the metric that ranks by it, never for
                        # the run's time
                        'igt': parsed.get('igt') if parsed.get('ok') else None,
                        'error': None if parsed.get('ok') else parsed.get('error')})
        resp.headers['Cache-Control'] = 'no-store'
        return resp


    @app.post('/api/preview')
    def preview_notes():
        """The submit preview, rendered by the very code that renders the
        published page (issue #30). Cross-references get a plain link here;
        the published page dresses them with the run's title and thumbnail.
        When kind=rules or kind=markdown, renders rules using md_html.

        Who: anybody
        Reads: form field notes (or rules), kind
        Answers: {ok, html}, Cache-Control: no-store
        """
        gate = _helper_gate()
        if gate:
            return gate
        text = (request.form.get('notes') or request.form.get('rules') or '').replace('\r\n', '\n')
        if len(text.encode()) > 1024 * 1024:
            return fail('notes exceed 1 MB')
        kind = (request.form.get('kind') or request.form.get('dialect') or '').strip()
        if kind in ('rules', 'markdown', 'md'):
            from wikitext import md_html
            resp = jsonify({'ok': True, 'html': md_html(text)})
        else:
            import wikitext
            def refs(markup):
                """Link safe run and member references in discussion markup."""
                markup = re.sub(r'\[M([0-9]+)\]', r'<a class="runref" href="/runs/M\1/">M\1</a>', markup)
                markup = re.sub(r'\[user:([A-Za-z0-9. _-]{2,40})\]', r'<span class="au">\1</span>', markup)
                return markup
            resp = jsonify({'ok': True, 'html': wikitext.wiki_html(text, refs=refs)})
        resp.headers['Cache-Control'] = 'no-store'
        return resp


    @app.get('/api/encode/check')
    def encode_check():
        """Is this a usable encode link, and what does its still frame look like?

        The submit page used to answer this itself by loading a YouTube thumbnail
        URL directly. Most platforms do not publish one you can build: Niconico and
        Bilibili have to be asked, and a browser cannot ask them (no CORS). So the
        check moved here, which also means the page and the server agree on what
        counts as a valid encode, by construction.

        Who: anybody
        Reads: query arg url
        Answers: {ok, kind, name, id, thumb}, or {ok: false, kind, name, error}
        """
        gate = _helper_gate()
        if gate:
            return gate
        url = (request.args.get('url') or '').strip()
        encode_provider = providers.resolve(url)
        if not encode_provider:
            return jsonify({'ok': False,
                            'error': 'not a link from ' + ', '.join(providers.names())})
        cached = ENCODE_CACHE.get(url)
        if cached and time.time() - cached[0] < 300:
            return jsonify(cached[1])
        thumb = providers.thumbnail_url(encode_provider['kind'], encode_provider['id'])
        if not thumb and providers.BY_KIND[encode_provider['kind']].get('thumbs'):
            # a direct template needs fetching to know whether the video is real;
            # the page then loads the candidate that actually answered (#29)
            thumb = providers.thumbnail_source(encode_provider['kind'], encode_provider['id'], THUMB_MAX)
        payload = ({'ok': True, 'kind': encode_provider['kind'], 'name': encode_provider['name'],
                    'id': encode_provider['id'], 'thumb': thumb,
                    'seconds': providers.duration_seconds(encode_provider['kind'], encode_provider['id'])} if thumb else
                   {'ok': False, 'kind': encode_provider['kind'], 'name': encode_provider['name'],
                    'error': f'that {encode_provider["name"]} video does not exist, or is private'})
        ENCODE_CACHE[url] = (time.time(), payload)
        return jsonify(payload)


    def _discussion_rate_retry(topic_id, cached, exc):
        """Retry a rate-limited forum request or serve the cached topic."""
        if cached:
            return None, jsonify(cached[1])
        try:
            time.sleep(min(3.0, float(exc.headers.get('Retry-After') or 1)))
            return _forum_get(f'/t/{topic_id}.json'), None
        except Exception as again:                          # noqa: BLE001
            return None, fail(f'could not reach the forum: {again}', 502)

    def _discussion_topic(topic_id, cached):
        """Fetch a forum topic, using the existing cache on rate limits or outages."""
        try:
            topic_json = _forum_get(f'/t/{topic_id}.json')
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                # the forum meters API calls by the minute; a busy minute is no
                # reason to show an empty box. Serve what was shown before, or
                # wait out the short window once, then give up for this call
                return _discussion_rate_retry(topic_id, cached, exc)
            else:
                return None, fail(f'could not reach the forum: {exc}', 502)
        except Exception as exc:                                    # noqa: BLE001
            if cached:
                return None, jsonify(cached[1])
            return None, fail(f'could not reach the forum: {exc}', 502)
        return topic_json, None

    @app.get('/api/discussion')
    def discussion():
        """The forum topic for a run, as the site renders it in place.

        Who: anybody
        Reads: query arg topic (forum topic id)
        Answers: {ok, topic, title, url, posts, replyCount}
        """
        try:
            topic_id = int(request.args.get('topic') or 0)
        except ValueError:
            return fail('topic must be a number')
        if topic_id <= 0:
            return fail('topic is required')
        if not DISCOURSE_KEY:
            return fail('the forum is not configured on this server', 503)
        cached = DISCUSSION_CACHE.get(topic_id)
        if cached and time.time() - cached[0] < 60:
            return jsonify(cached[1])
        topic_json, error = _discussion_topic(topic_id, cached)
        if error is not None:
            return error
        posts = []
        for post in topic_json.get('post_stream', {}).get('posts', []):
            posts.append({
                'id': post.get('id'), 'number': post.get('post_number'),
                'user': post.get('username'), 'name': post.get('display_username'),
                'avatar': (DISCOURSE_URL + post['avatar_template'].replace('{size}', '48')
                           if (post.get('avatar_template') or '').startswith('/')
                           else (post.get('avatar_template') or '').replace('{size}', '48')),
                'html': post.get('cooked') or '',
                'date': (post.get('created_at') or '')[:19],
                'staff': bool(post.get('staff')),
            })
        payload = {'ok': True, 'topic': topic_id, 'title': topic_json.get('title'),
                   'url': f'{DISCOURSE_URL}/t/{topic_id}',
                   'posts': posts, 'replyCount': max(0, len(posts) - 1)}
        DISCUSSION_CACHE[topic_id] = (time.time(), payload)
        return jsonify(payload)


    @app.post('/api/discussion/reply')
    def discussion_reply():
        """Post a reply to a run's topic as the logged-in member.

        Session only: the shared key must never be able to speak as somebody
        else, and Discourse applies that member's own trust level and rate
        limits because the post is made under their name.

        Who: a logged-in member (session only)
        Reads: form fields topic, body
        Answers: {ok, topic, post, user}
        """
        reply_form = request.form
        user = session_user()
        if not user:
            return fail('log in via the forum to reply', 403)
        if not origin_ok():
            return fail('cross-origin request refused', 403)
        if not re.fullmatch(r'[A-Za-z0-9._-]{3,30}', user):
            return fail('session username is not valid', 400)
        if not DISCOURSE_KEY:
            return fail('the forum is not configured on this server', 503)
        try:
            topic_id = int(reply_form.get('topic') or 0)
        except ValueError:
            return fail('topic must be a number')
        body = (reply_form.get('body') or '').strip()
        if topic_id <= 0:
            return fail('topic is required')
        if len(body) < 5:
            return fail('a reply needs at least a few words')
        if len(body.encode()) > 32 * 1024:
            return fail('reply exceeds 32 KB')
        post_body = urllib.parse.urlencode({'topic_id': topic_id, 'raw': body}).encode()
        forum_request = urllib.request.Request(f'{DISCOURSE_URL}/posts.json', data=post_body, method='POST',
                                     headers={'Api-Key': DISCOURSE_KEY, 'Api-Username': user})
        try:
            with urllib.request.urlopen(forum_request, timeout=20) as forum_response:
                posted = json.loads(forum_response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode(errors='replace')
            return fail(f'the forum refused the reply: {detail}', 502)
        except Exception as exc:                                    # noqa: BLE001
            return fail(f'could not reach the forum: {exc}', 502)
        DISCUSSION_CACHE.pop(topic_id, None)
        return jsonify({'ok': True, 'topic': topic_id, 'post': posted.get('post_number'),
                        'user': user})
