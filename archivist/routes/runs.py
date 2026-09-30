"""Runs HTTP endpoints; registered by the executable archivist entrypoint."""
import hashlib
import json
import pathlib
import re
import shutil
import time
from flask import jsonify, request
import movieparse
import providers
from settings import now_iso, ACT_NOTES_MAX, ARCHIVE, BRANCH, IMAGE_MAGIC, MOVIE_MAX, NOTES_MAX, SHOT_MAX_EACH, SHOT_MAX_TOTAL, SITE_URL, THUMB_MAX, slugify
from webutil import fail
from identity import current_name, run_authors_now, session_user
from gitstore import checkout_branch, commit_push, duplicate_of, find_run, load_game, lock, next_id, refresh_archive
from notify import movie_md, member_md, notify_discord
from records import covers_group, ensure_member, expert_covers, is_editor, is_uncl_run, load_groups, log_edit, save_groups, sync_status
from forumapi import ensure_game_topic, ensure_topic


def register(app, *, CW_ALLOWED, EXPERT_EDITABLE, GAME_PROPERTY_FIELDS, MOVIE_MUST_BE_NON_EMPTY_AND_UNDER_MAX, MOVIE_TOO_LARGE, REPRO_FIELDS, SCORING_FIELDS, act_common, attach_movie, auth_precheck, in_category, live_acts, notify_edit, option_in, pace_gate, parse_file_rows, parse_game_property, parse_metric_defs, parse_stated_time, place_subcategory, read_attachments, request_identity, split_notes_header, void_acts_for):
    """Attach the runs endpoints with explicit request dependencies."""

    def _submission_uploaded_movie(movie_upload, submission, wants_time, goal):
        """Read and describe an uploaded movie without rejecting unknown formats."""
        duration = None
        ext = movie_upload.filename.rsplit('.', 1)[-1].lower()
        movie_bytes = movie_upload.read()
        if len(movie_bytes) > MOVIE_MAX:
            return None, fail(MOVIE_TOO_LARGE)
        if not movie_bytes:
            return None, fail('movie file is empty')
        # any extension is archived as it is: an author may work in a tool
        # the archive has no parser for. A parse failure is a warning, never
        # a refusal; the record's time is the one the author states either
        # way (the form's own Import from movie fills it when it can).
        parsed = movieparse.parse(movie_upload.filename, movie_bytes)
        if not parsed['ok']:
            parsed = {'ok': False, 'frames': 0, 'rerecords': None, 'start': 'power-on', 'fps': None}
        if wants_time and goal != 'unclassified':
            duration, time_error = parse_stated_time(submission.get('time'))
            if time_error:
                return None, fail(f'this category ranks by time, so the run states it '
                            f'(Import from movie fills it when the movie can be read): {time_error}')
        movie_sha1 = hashlib.sha1(movie_bytes).hexdigest()
        return (duration, ext, movie_bytes, movie_sha1, parsed), None

    def _submission_movie(submission, wants_time, goal):
        """Validate an optional movie and derive its stated time and identity."""
        movie_upload = request.files.get('movie')
        video_only_flag = (submission.get('video_only') or '').strip() in ('1', 'true', 'yes', 'on')
        if video_only_flag and movie_upload and movie_upload.filename:
            return None, fail('you attached a movie file and called the run video-only; '
                        'pick one')
        video_only = video_only_flag or not (movie_upload and movie_upload.filename)
        if video_only:
            duration = None
            if wants_time and goal != 'unclassified':
                # the category ranks by time and there are no frames to derive it
                # from, so the submitter states it
                duration, time_error = parse_stated_time(submission.get('time'))
                if time_error:
                    return None, fail(f'a video-only run in a time-ranked category needs its time: {time_error}')
            ext = None
            movie_bytes = b''
            movie_sha1 = None
            parsed = {'frames': None, 'rerecords': None, 'start': None, 'fps': None}
        else:
            movie_details, error = _submission_uploaded_movie(movie_upload, submission, wants_time, goal)
            if error:
                return None, error
            duration, ext, movie_bytes, movie_sha1, parsed = movie_details
        return (video_only, duration, ext, movie_bytes, movie_sha1, parsed), None

    def _submission_category(submission):
        """Validate game, goal and optional subcategory."""
        game_selection = (submission.get('game') or '').strip()
        game_match = re.fullmatch(r'([a-z0-9-]+)/([a-z0-9-]+)', game_selection)
        if not game_match:
            return None, fail('game must be system/slug; create the game first at '
                        '/create-game/ if it is not archived yet')
        system, slug = game_match.groups()
        game, categories = load_game(system, slug)
        if not game:
            return None, fail(f'unknown game {system}/{slug}; create it first at /create-game/')

        goal = (submission.get('goal') or '').strip()
        goal_description = ''
        dim_keys = {}
        if goal == 'unclassified':
            # special category on every game: no defined goal, the run describes
            # its own; never verifiable, ranked by likes alone
            goal_description = (submission.get('goal_description') or '').strip()[:200]
            if not goal_description:
                return None, fail('Unclassified runs must describe their goal '
                            '(goal_description); it is shown in the ranking')
            dim_keys = {'goal': 'unclassified'}
        for dimension in categories['dimensions']:
            if goal in {option['key'] for option in dimension['options']}:
                dim_keys[dimension['key']] = goal
        if not dim_keys:
            return None, fail(f'unknown category {goal!r} for {system}/{slug}; create it '
                        f'first from the game page')
        # a category with subcategories (Episode 1: any%, 100%) wants one named
        sub_error = place_subcategory(categories, dim_keys, submission.get('sub'))
        if sub_error:
            return None, fail(sub_error)
        return (system, slug, game, categories, goal, goal_description, dim_keys), None

    def _submission_metric_value(submission, metric_def):
        """Read a required scoring value for a submitted run."""
        raw_value = (submission.get(f'metric_{metric_def["key"]}') or '').strip()
        if raw_value == '':
            return None, fail(f'this category ranks by {metric_def["label"]}: state its '
                              f'value (metric_{metric_def["key"]})')
        try:
            value = float(raw_value)
        except ValueError:
            return None, fail(f'{metric_def["label"]} must be a number (seconds for times)')
        if value < 0:
            return None, fail(f'{metric_def["label"]} cannot be negative')
        return value, None

    def _submission_metrics(submission, categories, goal):
        """Validate required scoring metrics and author credits."""
        goal_opt = next((option for dimension in categories['dimensions'] for option in dimension['options']
                         if option['key'] == goal), None)
        metric_defs = (goal_opt or {}).get('metrics')
        wants_time = metric_defs is None or any(metric_def['key'] == 'time'
                                                for metric_def in metric_defs)
        stated_metrics = {}
        for metric_def in (metric_defs or []):
            if metric_def['key'] == 'time':
                continue                    # the run's time is stated via `time`
            value, value_error = _submission_metric_value(submission, metric_def)
            if value_error:
                return None, value_error
            stated_metrics[metric_def['key']] = value

        authors = [a.strip() for a in (submission.get('authors') or '').split(',') if a.strip()]
        if not authors:
            return None, fail('at least one author required')
        return (wants_time, stated_metrics, authors), None

    def _submission_files(submission):
        """Validate repeated file rows, including the legacy ROM pair."""
        files, files_error = parse_file_rows(submission)
        if files_error:
            return None, fail(files_error)
        if not files and (submission.get('rom_name') or submission.get('rom_sha1')):
            legacy = {'file_name': [submission.get('rom_name') or ''],
                      'file_sha1': [submission.get('rom_sha1') or '']}
            files, files_error = parse_file_rows(type('F', (), {'getlist': lambda self, k: legacy.get(k, [])})())
            if files_error:
                return None, fail(files_error)
        return files, None

    def _submission_metadata(submission):
        """Validate the encode, disclosures, notes and supplementary files."""
        encode = (submission.get('encode') or '').strip()
        encode_provider = providers.resolve(encode)
        if not encode_provider:
            return None, fail('an encode link is required, from one of: '
                        + ', '.join(providers.names())
                        + ' (the run thumbnail is derived from it)')
        thumb_bytes, thumb_ext = providers.thumbnail(encode_provider['kind'], encode_provider['id'], THUMB_MAX)
        if not thumb_bytes:
            return None, fail(f'the encode link does not resolve to a watchable '
                        f'{encode_provider["name"]} video; check the URL (the run thumbnail is '
                        f'derived from it)')

        # --- attachments: text configs, or additional movie files ---
        attachments, attachment_error = read_attachments()
        if attachment_error:
            return None, attachment_error

        completed = (submission.get('completed') or '').strip()
        if completed:
            if not re.fullmatch(r'(19[89]\d|20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])',
                                completed):
                return None, fail('completed must be a date like 2021-10-26')
            if completed > time.strftime('%Y-%m-%d', time.gmtime()):
                return None, fail('completed cannot be in the future')

        notes = (submission.get('notes') or '').strip()
        if len(notes.encode()) > NOTES_MAX:
            return None, fail('notes exceed 256 KB')

        # voluntary content disclosures — separate flags, shown on the run page
        content_warnings = [w for w in request.form.getlist('content_warnings')]
        if any(w not in CW_ALLOWED for w in content_warnings):
            return None, fail('unknown content warning flag')

        # the files the movie was made against (0 to n): ROMs, disc images,
        # executables, sources. Name and SHA1 only, hashed in the browser; the
        # old single rom_name/rom_sha1 pair is accepted as one row still
        files, files_error = _submission_files(submission)
        if files_error:
            return None, files_error
        return (encode, encode_provider, thumb_bytes, thumb_ext, attachments, completed, notes, content_warnings, files), None

    def _submission_duplicate(video_only, encode, movie_sha1, system, slug, dim_keys, parsed, authors):
        """Refuse an active run with the same encode or movie under the archive lock."""
        if video_only:
            # no bytes to compare, so the encode is the fingerprint: the same
            # video twice is the same run twice
            for other_run_json in ARCHIVE.glob('games/*/*/runs/*/run.json'):
                other_run = json.loads(other_run_json.read_text())
                if other_run.get('withdrawn'):
                    continue   # a withdrawn run never blocks a resubmission
                if any(e.get('url') == encode for e in other_run.get('encodes', [])):
                    return fail(f'this video is already archived as '
                                f'{other_run["id"]}: the encode is the run, and it is '
                                f'the same encode', 409)
        else:
            dup_id, why = duplicate_of(movie_sha1, f'{system}/{slug}',
                                       dim_keys.get('goal'), parsed['frames'], authors)
            if dup_id:
                return fail(f'this run is already archived as {dup_id}: it has {why}. '
                            f'If it is an improvement, submit the faster movie; if the '
                            f'archived one is wrong, its authors can edit it.', 409)
        return None

    def _save_submission(submitter, run_dir, run, run_id, video_only, ext, movie_bytes,
                         thumb_ext, thumb_bytes, notes, attachments, game, goal,
                         authors, system, slug):
        """Persist one run, create discussion pointers, commit, then notify."""
        ensure_member(submitter)
        run_dir.mkdir(parents=True)
        if not video_only:
            (run_dir / f'{run_id}.{ext}').write_bytes(movie_bytes)
        (run_dir / ('thumb' + thumb_ext)).write_bytes(thumb_bytes)
        (run_dir / 'run.json').write_text(json.dumps(run, indent=1))
        if notes:
            (run_dir / 'notes.md').write_text(notes + '\n')
        if attachments:
            (run_dir / 'attachments').mkdir()
            for name, data in attachments:
                (run_dir / 'attachments' / name).write_bytes(data)

        title = f"New run archived: {game['title']} ({goal}) by {', '.join(authors)}"
        # topics BEFORE the push: written after it, the pointers sat only in
        # the working tree and the next request's hard reset erased them,
        # leaving orphan topics and run pages with no visible discussion
        ensure_game_topic(system, slug, game['title'])
        if ensure_topic(run, game['title'], system, slug, goal, authors):
            (run_dir / 'run.json').write_text(json.dumps(run, indent=1))
        commit_push(f'Archive {run_id}: {game["title"]} ({goal}) by {", ".join(authors)}\n\n'
                    f'Submitted-By: {submitter}\nVia: archivist')
        notify_discord(f'\U0001f3ac New run archived: '
                       + movie_md(run, game['title']) + f' ({goal})',
                       wait_for=f'{SITE_URL}/runs/{run_id}/',
                       image=f'{SITE_URL}/thumbs/{run_id}{thumb_ext}')

    def _submission_record(run_id, system, slug, dim_keys, authors, stated_metrics,
                           video_only, duration, ext, movie_sha1, parsed, thumb_ext,
                           goal_description, content_warnings, files, submission,
                           encode_provider, encode, attachments, completed, submitter):
        """Assemble the immutable initial facts for a newly archived run."""
        run = {
            'id': run_id, 'game': f'{system}/{slug}', 'category': dim_keys,
            'authors': [{'user': a} for a in authors],
            'tools': [],
            **({'metrics': stated_metrics} if stated_metrics else {}),
            **({'videoOnly': True,
                **({'duration': duration} if duration else {})} if video_only else
               {**({'duration': duration} if duration else {}),
                'movie': {'file': f'{run_id}.{ext}', 'format': ext, 'sha1': movie_sha1,
                          'frames': parsed['frames'],
                          'rerecords': parsed['rerecords'],
                          'start': parsed['start'],
                          **({'fps': parsed['fps']} if parsed.get('fps') else {})}}),
            'thumbnail': 'thumb' + thumb_ext,
            **({'goalDescription': goal_description} if goal_description else {}),
            **({'contentWarnings': content_warnings} if content_warnings else {}),
            'contract': {'emulator': (submission.get('emulator') or '').strip(), **({'files': files} if files else {})},
            'status': ({'reproduced': 'not-applicable', 'verified': 'none'} if video_only else
                       {'reproduced': 'none', 'verified': 'none'}),
            'encodes': [{'kind': encode_provider['kind'], 'url': encode}],
            'attachments': [{'file': f'attachments/{name}', 'role': 'submitted attachment'} for name, _ in attachments],
            **({'completed': completed} if completed else {}),
            'submitted': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'submittedBy': submitter,
        }
        return run

    @app.post('/api/submit')
    def submit():
        """Archive a pending run as one folder and commit.

        Requires member session or `key` plus `submitter`, consent, game, goal,
        authors, encode and category-dependent time, sub and metrics. Accepts
        movie or video_only, metadata, supplementary files and dry_run.
        Answers {ok, id, archive, forum} or preview; 409 for duplicate media.
        """
        submission = request.form
        submitter, error = request_identity(submission, 'submitter')
        if error:
            return error
        paced = pace_gate(submission, submitter, 'submit')
        if paced:
            return paced
        if submission.get('consent') != 'yes':
            return fail('submission requires consent: licensing under CC BY 4.0, agreeing '
                        'with the Community Principles, Terms of Use, Code of Conduct and '
                        'Privacy Policy, and confirming the information, especially '
                        'authorship, is complete and truthful')

        # --- game and category exist beforehand (creation has its own flow) ---
        category_data, error = _submission_category(submission)
        if error:
            return error
        system, slug, game, categories, goal, goal_description, dim_keys = category_data

        # --- the category's metrics decide what the submitter must state ---
        metrics_data, error = _submission_metrics(submission, categories, goal)
        if error:
            return error
        wants_time, stated_metrics, authors = metrics_data

        # --- the movie, or the statement that there is none ---
        # A video-only run has no input movie: the encode IS the run. It can never
        # be reproduced, and it says so; verification
        # still gates its ranking like any other run's. The submitter states the
        # time, since there are no frames to derive it from.
        # a run without a movie file IS video-only: the encode is the run. The
        # explicit flag survives for API callers; sending both a flag and a file
        # is a contradiction to refuse, not to guess about
        movie_data, error = _submission_movie(submission, wants_time, goal)
        if error:
            return error
        video_only, duration, ext, movie_bytes, movie_sha1, parsed = movie_data

        # --- encode (mandatory) + thumbnail derived from it ---
        # The encode is validated here and the run's thumbnail is a frame of it
        # (maxres, falling back to hq) — no author upload, nothing to moderate.
        metadata, error = _submission_metadata(submission)
        if error:
            return error
        (encode, encode_provider, thumb_bytes, thumb_ext, attachments,
         completed, notes, content_warnings, files) = metadata

        dry_run = submission.get('dry_run') in ('1', 'true', 'yes')

        with lock:
            checkout_branch()
            # refuse a movie the archive already holds: the same bytes (a double
            # click, or a run already imported from TASVideos) or the same work
            # saved again. Checked under the lock, against the fresh checkout.
            duplicate_error = _submission_duplicate(
                video_only, encode, movie_sha1, system, slug, dim_keys, parsed, authors)
            if duplicate_error is not None:
                return duplicate_error
            run_number = next_id()
            run_id = f'M{run_number}'
            run_dir = ARCHIVE / 'games' / system / slug / 'runs' / run_id
            run = _submission_record(run_id, system, slug, dim_keys, authors,
                                     stated_metrics, video_only, duration, ext, movie_sha1,
                                     parsed, thumb_ext, goal_description, content_warnings,
                                     files, submission, encode_provider, encode, attachments,
                                     completed, submitter)
            if dry_run:
                return jsonify({'ok': True, 'dry_run': True, 'would_be': run_id, 'run': run,
                                'game_key': f'{system}/{slug}'})
            _save_submission(submitter, run_dir, run, run_id, video_only, ext,
                             movie_bytes, thumb_ext, thumb_bytes, notes,
                             attachments, game, goal, authors, system, slug)

        return jsonify({'ok': True, 'id': run_id,
                        'archive': f'https://github.com/ToolAssisted-run/archive/tree/{BRANCH}/games/{system}/{slug}/runs/{run_id}',
                        'forum': (run.get('forum') or {}).get('url')})


    def _reproduction_screenshot(run_dir):
        """Validate screenshot type, size and remaining storage capacity."""
        screenshot_upload = request.files.get('screenshot')
        if not screenshot_upload or not screenshot_upload.filename:
            return None, fail('an ending screenshot is required as proof of sync')
        ext = pathlib.Path(screenshot_upload.filename).suffix.lower()
        if ext not in IMAGE_MAGIC:
            return None, fail('screenshot must be png, jpg or webp')
        screenshot_bytes = screenshot_upload.read()
        if len(screenshot_bytes) > SHOT_MAX_EACH:
            return None, fail('screenshot exceeds 512 KB')
        if not any(screenshot_bytes.startswith(magic) for magic in IMAGE_MAGIC[ext]):
            return None, fail(f'screenshot is not a real {ext} image')
        stored_bytes = sum(sp.stat().st_size for sp in (run_dir / 'reproductions').glob('*')
                           if sp.is_file()) if (run_dir / 'reproductions').exists() else 0
        if stored_bytes + len(screenshot_bytes) > SHOT_MAX_TOTAL:
            return None, fail('this run has reached its screenshot storage cap')
        return (ext, screenshot_bytes), None

    def _locked_reproduce(dry_run, reproduction_form):
        """Process the reproduce request under the archive write lock."""
        auth_error = auth_precheck(reproduction_form)
        if auth_error:
            return auth_error
        if not dry_run:
            checkout_branch()
        act_error, run_dir, run, user = act_common(reproduction_form)
        if act_error:
            return act_error
        if run.get('videoOnly'):
            return fail('this run is video-only: there is no input movie to '
                        'replay, so reproduction does not apply')
        if any(a['user'].lower() == user.lower() and (not a.get('invalidated') or a['invalidated'].get('cause') != 'edit')
               for a in run.get('reproductions', [])):
            return fail('you have already reproduced this run; one reproduction per member')

        screenshot, screenshot_error = _reproduction_screenshot(run_dir)
        if screenshot_error:
            return screenshot_error
        ext, screenshot_bytes = screenshot

        ordinal = len(run.get('reproductions', [])) + 1
        shot_rel = f'reproductions/{ordinal}-{user}{ext}'
        entry = {'user': user, 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(),
                 'screenshot': shot_rel}
        if (reproduction_form.get('emulator') or '').strip():
            entry['emulator'] = reproduction_form.get('emulator').strip()[:120]
        if (reproduction_form.get('notes') or '').strip():
            entry['notes'] = reproduction_form.get('notes').strip()
        run.setdefault('reproductions', []).append(entry)
        sync_status(run)
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_record': entry,
                            'status': run['status']})

        (run_dir / 'reproductions').mkdir(exist_ok=True)
        (run_dir / shot_rel).write_bytes(screenshot_bytes)
        (run_dir / 'run.json').write_text(json.dumps(
            {k: v for k, v in run.items() if not k.startswith('_')}, indent=1))
        ensure_member(user)
        commit_push(f'Reproduce {run["id"]}: by {user}\n\nVia: archivist')
        notify_discord(f'\u21bb **{member_md(user)}** reproduced '
                       + movie_md(run) + in_category(run),
                       wait_for=f'{SITE_URL}/runs/{run["id"]}/')
        return True, (run,)

    @app.post('/api/reproduce')
    def reproduce():
        """Record a community reproduction: mandatory ending screenshot as proof.

        Who: a member (session, or `key` plus `user`) who is not one of the
            run's authors; refused on Imported, withdrawn and video-only runs
        Reads: form fields run, emulator, notes, dry_run; file screenshot (png/jpg/webp)
        Answers: {ok, run, status, reproductions}; dry_run: {ok, dry_run,
            would_record, status}
        """
        reproduction_form = request.form
        dry_run = reproduction_form.get('dry_run') in ('1', 'true', 'yes')
        with lock:
            locked_result = _locked_reproduce(dry_run, reproduction_form)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        run, = locked_result[1]
        return jsonify({'ok': True, 'run': run['id'], 'status': run['status'],
                        'reproductions': len([a for a in run['reproductions'] if not a.get('invalidated')])})


    def _locked_invalidate(dry_run, invalidation_form):
        """Process the invalidate request under the archive write lock."""
        auth_error = auth_precheck(invalidation_form)
        if auth_error:
            return auth_error
        if not dry_run:
            checkout_branch()
        expert, error = request_identity(invalidation_form, 'expert')
        if error:
            return error
        run_id = (invalidation_form.get('run') or '').strip()
        run_dir = find_run(run_id) if re.fullmatch(r'M[0-9]+', run_id) else None
        if not run_dir:
            return fail(f'unknown run {run_id}', 404)
        run = json.loads((run_dir / 'run.json').read_text())
        if run.get('status', {}).get('reproduced') == 'imported':
            return fail('Imported runs are irrevocably verified; no further acts apply')
        game_key = run['game']
        if not expert_covers(expert, game_key):
            return fail(f'{expert!r} is not an expert covering {game_key}', 403)
        kind = (invalidation_form.get('kind') or '').strip()
        ROSTER = {'reproduction': 'reproductions', 'verification': 'verifications'}
        if kind not in ROSTER:
            return fail('kind must be reproduction or verification')
        target = (invalidation_form.get('target') or '').strip()
        reason = (invalidation_form.get('reason') or '').strip()
        if not reason:
            return fail('an invalidation must state its reason; it is logged in the open')
        if len(reason) > ACT_NOTES_MAX:
            return fail(f'reason exceeds {ACT_NOTES_MAX} characters')
        acts = run.get(ROSTER[kind], [])
        act = next((a for a in acts if a['user'].lower() == target.lower()
                    and not a.get('invalidated')), None)
        if not act:
            return fail(f'no live {kind} by {target!r} on {run_id}', 404)
        act['invalidated'] = {'by': expert, 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(),
                              'reason': reason}
        sync_status(run)
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_invalidate': act,
                            'status': run['status']})
        (run_dir / 'run.json').write_text(json.dumps(
            {k: v for k, v in run.items() if not k.startswith('_')}, indent=1))
        ensure_member(expert)
        commit_push(f'Invalidate {kind} on {run_id}: {target} by expert {expert}\n\n'
                    f'Reason: {reason}\nVia: archivist')
        return True, (run, run_id)

    @app.post('/api/invalidate')
    def invalidate():
        """An expert invalidates a faulty reproduction/verification — a logged,
        appealable moderation act, never automatic. The run recomputes and the act
        can be redone by anyone else.

        Who: an expert covering the run's game (`key` plus `expert`, or session)
        Reads: form fields run, kind (reproduction|verification), target
            (the username whose act it is), reason, dry_run
        Answers: {ok, run, status, note}; dry_run: {ok, dry_run, would_invalidate,
            status}
        """
        invalidation_form = request.form
        dry_run = invalidation_form.get('dry_run') in ('1', 'true', 'yes')
        refresh_archive()
        with lock:
            locked_result = _locked_invalidate(dry_run, invalidation_form)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        run, run_id = locked_result[1]
        return jsonify({'ok': True, 'run': run_id, 'status': run['status'],
                        'note': 'Logged in the open site log; appealable; '
                                'the act may be redone by any other member.'})


    def _expert_run_metric(run, run_dir, value, field, dry_run):
        """Validate and apply the expert correction to the run's metric."""
        metric_key = field.split(':', 1)[1]
        if metric_key == 'time':
            return fail("the run's time is the duration field, not a stored metric")
        try:
            new_value = float(value)
        except ValueError:
            return fail('value must be a number (seconds for times)')
        if new_value < 0:
            return fail('a metric value cannot be negative')
        old_value = (run.get('metrics') or {}).get(metric_key, 0)
        run.setdefault('metrics', {})[metric_key] = new_value
        value = str(new_value)
        return old_value, value, None

    def _expert_run_duration(run, run_dir, value, field, dry_run):
        """Validate and apply the expert correction to the run's duration."""
        sys_key, slug_key = run['game'].split('/')
        _, cats_doc = load_game(sys_key, slug_key)
        goal_key = (run.get('category') or {}).get('goal')
        opt_def = next((o for dim in (cats_doc or {}).get('dimensions', [])
                        for o in dim['options'] if o['key'] == goal_key), None)
        opt_metrics = (opt_def or {}).get('metrics')
        if not (opt_metrics is None or any(mm['key'] == 'time' for mm in opt_metrics)):
            return fail('this category does not rank by time; there is no stated time to correct')
        time_match = re.fullmatch(r'(?:(\d{1,3}):)?(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?',
                           value)
        if not time_match:
            return fail('value must be a time, [h:]mm:ss or [h:]mm:ss.mmm')
        hours, minutes, seconds, fraction = time_match.groups()
        new_value = (int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)
                 + (int(fraction.ljust(3, "0")) / 1000 if fraction else 0.0))
        if new_value <= 0:
            return fail('a run that takes no time at all is not a run')
        old_value = run.get('duration')
        run['duration'] = new_value
        return old_value, value, None

    def _expert_run_goal(run, run_dir, value, field, dry_run):
        """Validate and apply the expert correction to the run's goal."""
        categories = json.loads((run_dir.parent.parent / 'categories.json').read_text())
        goal_value, _, sub_value = value.partition('/')
        valid_goals = {o['key'] for d in categories['dimensions'] for o in d['options']}
        valid_goals.add('unclassified')
        if goal_value not in valid_goals:
            return fail(f'{goal_value!r} is not a goal this game defines')
        if goal_value == 'unclassified' and any(
                not v.get('invalidated') for v in run.get('verifications', [])):
            return fail('this run holds live verifications, which are bound '
                        'to its goal; unclassifying it would void them, and '
                        'that is not an edit')
        old_category = dict(run.get('category') or {})
        old_value = old_category.get('goal', '') + ('/' + old_category['sub'] if old_category.get('sub') else '')
        new_category = {'goal': goal_value}
        if goal_value != 'unclassified':
            sub_error = place_subcategory(categories, new_category, sub_value)
            if sub_error:
                return fail(sub_error)
        if old_value == value:
            return fail('that is already its goal')
        run['category'] = new_category
        return old_value, value, None

    def _expert_run_encode(run, run_dir, value, field, dry_run):
        """Validate and apply the expert correction to the run's encode."""
        encode_provider = providers.resolve(value)
        if not encode_provider:
            return fail('value must be a watchable encode URL on a platform '
                        'we accept')
        old_value = (run.get('encodes') or [{}])[0].get('url', '')
        run['encodes'] = [{'kind': encode_provider['kind'], 'url': value}]
        return old_value, value, None

    def _expert_run_goal_description(run, run_dir, value, field, dry_run):
        """Validate and apply the expert correction to the run's goal description."""
        if len(value) > 500:
            return fail('a goal description fits in 500 characters')
        old_value = run.get('goalDescription', '')
        if value:
            run['goalDescription'] = value
        else:
            run.pop('goalDescription', None)
        if is_uncl_run(run) and not value:
            return fail('an Unclassified run states its own goal; it cannot '
                        'lose its description')
        return old_value, value, None

    def _expert_run_notes(run, run_dir, value, field, dry_run):
        """Validate and apply the expert correction to the run's notes."""
        if len(value.encode()) > 64 * 1024:
            return fail('notes fit in 64 KB')
        notes_file = run_dir / 'notes.md'
        old_value = (notes_file.read_text()[:300] if notes_file.exists() else '')
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'field': field,
                            'from': old_value, 'to': value[:300]})
        if value:
            notes_file.write_text(value + ('\n' if not value.endswith('\n') else ''))
        elif notes_file.exists():
            notes_file.unlink()
        return old_value, value, None

    def _expert_run_movie(run, run_dir, value, field, dry_run):
        """Validate and apply the expert correction to the run's movie."""
        new_movie_upload = request.files.get('movie')
        if not new_movie_upload or not new_movie_upload.filename:
            return fail('attach the replacement movie file')
        movie_ext = new_movie_upload.filename.rsplit('.', 1)[-1].lower()
        movie_bytes = new_movie_upload.read()
        if not movie_bytes or len(movie_bytes) > MOVIE_MAX:
            return fail(MOVIE_MUST_BE_NON_EMPTY_AND_UNDER_MAX)
        # the same door as submission: any extension, and a parse
        # failure keeps the file with frames unknown
        parsed_movie = movieparse.parse(new_movie_upload.filename, movie_bytes)
        if not parsed_movie['ok']:
            parsed_movie = {'ok': False, 'frames': 0, 'rerecords': None, 'start': 'power-on', 'fps': None}
        old_value = (f"{run['movie']['file']} (sha1 {run['movie'].get('sha1', '?')[:12]})"
                     if run.get('movie') else 'none: the run was video-only')
        value = f"{run['id']}.{movie_ext} (sha1 {hashlib.sha1(movie_bytes).hexdigest()[:12]})"
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'field': field,
                            'from': old_value, 'to': value})
        if run.get('movie'):
            (run_dir / run['movie']['file']).unlink(missing_ok=True)
        (run_dir / f"{run['id']}.{movie_ext}").write_bytes(movie_bytes)
        run['movie'] = {'file': f"{run['id']}.{movie_ext}", 'format': movie_ext,
                      'sha1': hashlib.sha1(movie_bytes).hexdigest(),
                      'frames': parsed_movie['frames'],
                      'rerecords': parsed_movie['rerecords'],
                      'start': parsed_movie['start'],
                      **({'fps': parsed_movie['fps']} if parsed_movie.get('fps') else {})}
        # a run that gains its first movie stops being video-only, and
        # what was not applicable to it becomes merely undone
        run.pop('videoOnly', None)
        if run.get('status', {}).get('reproduced') == 'not-applicable':
            run['status']['reproduced'] = 'none'
        return old_value, value, None

    def _expert_run(actor, target, field, value, reason, dry_run, kind):
        """Apply one logged expert run correction within its jurisdiction."""
        if not re.fullmatch(r'M[0-9]+', target):
            return fail('target must be a run id like M100001')
        run_dir = find_run(target)
        if not run_dir:
            return fail(f'unknown run {target}', 404)
        game_key = f'{run_dir.parent.parent.parent.name}/{run_dir.parent.parent.name}'
        if not expert_covers(actor, game_key):
            # an editor shapes the library, not the runs: the one run
            # field that is library shape is which category it sits in
            if not (is_editor(actor) and field == 'goal'):
                return fail(f'{actor!r} is not an expert covering {game_key}'
                            f' (an editor may only move a run between '
                            f'categories)', 403)
        run = json.loads((run_dir / 'run.json').read_text())
        handler = (_expert_run_metric if field.startswith('metric:') else {
            'duration': _expert_run_duration, 'goal': _expert_run_goal,
            'encode': _expert_run_encode, 'goalDescription': _expert_run_goal_description,
            'notes': _expert_run_notes, 'movie': _expert_run_movie,
        }[field])
        outcome = handler(run, run_dir, value, field, dry_run)
        if not isinstance(outcome, tuple) or len(outcome) != 3:
            return outcome
        old_value, value, _ = outcome
        would_void = []
        if (field in SCORING_FIELDS or field.startswith('metric:')) and live_acts(run)['verifications']: would_void.append('verifications')
        if field in REPRO_FIELDS:
            if live_acts(run)['reproductions']: would_void.append('reproductions')
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'field': field,
                            'from': old_value, 'to': value, 'would_void': would_void})
        voided = void_acts_for(run, [field], actor)
        (run_dir / 'run.json').write_text(json.dumps(
            {k: v for k, v in run.items() if not k.startswith('_')}, indent=1))
        log_edit('run', target, field, old_value, value, actor, reason)
        if voided:
            log_edit('run', target, 'acts voided', ', '.join(voided), 'by the change of ' + field, actor, reason)
        return old_value, value, voided, run_dir

    def _expert_game_title(game, target, field, value, dry_run):
        """Validate the game title and prepare its recorded value."""
        if not (1 <= len(value) <= 120):
            return fail('a title fits in 120 characters')
        old_value = game.get('title')
        if old_value == value:
            return fail('that is already its title')
        game['title'] = value
        return old_value, value, None

    def _expert_game_property(game, target, field, value, dry_run):
        """Validate the game property and prepare its recorded value."""
        old_value = game.get(field, '')
        value, property_error = parse_game_property(field, value)
        if property_error:
            return fail(property_error)
        if value is None:
            value = ''
        if old_value == value:
            return fail(f'that is already its {field}')
        if value == '':
            game.pop(field, None)
        else:
            game[field] = value
        # the record keeps the typed value (a real boolean); the log
        # and the answer carry it as text like every other edit
        old_value, value = str(old_value), str(value)
        return old_value, value, None

    def _expert_game_thumbnail(game, target, field, value, dry_run):
        """Validate the game thumbnail and prepare its recorded value."""
        screenshot_upload = request.files.get('thumbnail')
        if not screenshot_upload or not screenshot_upload.filename:
            return fail('attach the thumbnail image')
        upload_ext = pathlib.Path(screenshot_upload.filename).suffix.lower()
        stored_ext = '.jpg' if upload_ext == '.jpeg' else upload_ext
        if stored_ext not in IMAGE_MAGIC:
            return fail('thumbnail must be png, jpg or webp')
        image_bytes = screenshot_upload.read()
        if not image_bytes or len(image_bytes) > THUMB_MAX:
            return fail(f'thumbnail must be non-empty and under '
                        f'{THUMB_MAX >> 10} KB')
        if not any(image_bytes.startswith(magic) for magic in IMAGE_MAGIC[stored_ext]):
            return fail('that file is not the image its name claims')
        old_value = game.get('thumbnail', '')
        value = f'thumb{stored_ext}'
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'field': field,
                            'from': old_value, 'to': value})
        if old_value:
            (ARCHIVE / 'games' / target / old_value).unlink(missing_ok=True)
        (ARCHIVE / 'games' / target / value).write_bytes(image_bytes)
        game['thumbnail'] = value
        return old_value, value, None

    def _expert_game(actor, target, field, value, reason, dry_run, kind):
        """Apply one logged expert game correction within its jurisdiction."""
        target_match = re.fullmatch(r'([a-z0-9-]+)/([a-z0-9-]+)', target)
        if not target_match:
            return fail('target must be system/slug')
        game_file = ARCHIVE / 'games' / target / 'game.json'
        if not game_file.exists():
            return fail(f'unknown game {target}', 404)
        if not expert_covers(actor, target) and not is_editor(actor):
            return fail(f'{actor!r} is not an expert covering {target}, '
                        f'nor an editor', 403)
        game = json.loads(game_file.read_text())
        handler = (_expert_game_title if field == 'title' else
                   _expert_game_property if field in GAME_PROPERTY_FIELDS else
                   _expert_game_thumbnail)
        outcome = handler(game, target, field, value, dry_run)
        if not isinstance(outcome, tuple) or len(outcome) != 3:
            return outcome
        old_value, value, _ = outcome
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'field': field,
                            'from': old_value, 'to': value})
        game_file.write_text(json.dumps(game, indent=1) + '\n')
        log_edit('game', target, field, old_value, value, actor, reason)
        return old_value, value, [], None

    def _expert_category_selector(game_key, option_key, sub_key, value, option, categories, categories_file, target, actor, reason, dry_run, kind, field):
        """Apply the category selector correction and its audit entry."""
        categories_file = ARCHIVE / 'games' / game_key / 'categories.json'
        if not categories_file.exists():
            return fail(f'unknown game {game_key}', 404)
        if not expert_covers(actor, game_key) and not is_editor(actor):
            return fail(f'{actor!r} is not an expert covering {game_key}, nor an editor', 403)
        if value not in ('buttons', 'dropdown'):
            return fail('selector is buttons or dropdown')
        categories = json.loads(categories_file.read_text())
        dimension = next((d for d in categories['dimensions'] if d['key'] == 'goal'),
                         categories['dimensions'][0] if categories['dimensions'] else None)
        if dimension is None:
            return fail('this game has no categories yet')
        old_value = dimension.get('selector', 'buttons')
        if old_value == value:
            return fail('that is already how they are shown')
        if value == 'buttons':
            dimension.pop('selector', None)
        else:
            dimension['selector'] = value
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'field': field, 'from': old_value, 'to': value})
        categories_file.write_text(json.dumps(categories, indent=1) + '\n')
        log_edit('category', target, field, old_value, value, actor, reason)
        ensure_member(actor)
        commit_push(f'Expert edit category {target}: selector\n\nFrom: {old_value}\nTo: {value}\n'
                    f'By: {actor}\nReason: {reason}\nVia: archivist')
        return jsonify({'ok': True, 'kind': kind, 'key': target, 'field': field,
                        'from': old_value, 'to': value})

    def _rename_category_runs(runs_dir, option_key, sub_key, new_key, dry_run):
        """Move every referenced run goal key with its renamed category."""
        moved = 0
        for run_json_path in runs_dir.glob('*/run.json'):
            run_doc = json.loads(run_json_path.read_text())
            run_cat = run_doc.get('category') or {}
            if run_cat.get('goal') != option_key:
                continue
            if sub_key:
                if run_cat.get('sub') != sub_key:
                    continue
            if dry_run:
                moved += 1
                continue
            if sub_key:
                run_cat['sub'] = new_key
            else:
                run_cat['goal'] = new_key
            run_json_path.write_text(json.dumps(run_doc, indent=1) + '\n')
            moved += 1
        return moved

    def _category_rename_target(option_key, sub_key, new_key, option, categories):
        """Find the renamed goal or subcategory and reject key collisions."""
        if sub_key:
            subs = option.get('subcategories', [])
            sub = next((s for s in subs if s['key'] == sub_key), None)
            if not sub:
                return None, fail(f'{option_key} has no subcategory {sub_key!r}', 404)
            if new_key == sub_key:
                return None, fail('that is already its key')
            if any(s['key'] == new_key for s in subs):
                return None, fail(f'{new_key!r} already exists in {option["label"]}', 409)
            return (sub_key, sub), None
        if new_key == option_key:
            return None, fail('that is already its key')
        if any(o['key'] == new_key for d in categories['dimensions']
               for o in d['options']):
            return None, fail(f'{new_key!r} already exists on this game', 409)
        return (option_key, option), None

    def _expert_category_key(game_key, option_key, sub_key, value, option, categories, categories_file, target, actor, reason, dry_run, kind, field):
        """Apply the category key correction and its audit entry."""
        new_key = slugify(value)
        if not new_key:
            return fail('a key is lowercase-with-hyphens')
        if new_key == 'unclassified':
            return fail('unclassified is reserved')
        target_entry, target_error = _category_rename_target(
            option_key, sub_key, new_key, option, categories)
        if target_error:
            return target_error
        old_value, renamed_entry = target_entry
        runs_dir = ARCHIVE / 'games' / game_key / 'runs'
        moved = _rename_category_runs(runs_dir, option_key, sub_key, new_key, dry_run)
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'field': field,
                            'from': old_value, 'to': new_key, 'runs_moved': moved})
        renamed_entry['key'] = new_key
        categories_file.write_text(json.dumps(categories, indent=1) + '\n')
        log_edit('category', target, field, old_value, new_key, actor, reason)
        ensure_member(actor)
        commit_push(f'Expert edit category {target}: key\n\n'
                    f'From: {old_value}\nTo: {new_key}\n'
                    f'Runs following the rename: {moved}\n'
                    f'By: {actor}\nReason: {reason}\nVia: archivist')
        return jsonify({'ok': True, 'kind': kind, 'key': target, 'field': field,
                        'from': old_value, 'to': new_key, 'runs_moved': moved})

    def _expert_category_sub_selector(game_key, option_key, sub_key, value, option, categories, categories_file, target, actor, reason, dry_run, kind, field):
        """Apply the category sub selector correction and its audit entry."""
        if value not in ('buttons', 'dropdown'):
            return fail('subSelector is buttons or dropdown')
        old_value = option.get('subSelector', 'buttons')
        if old_value == value:
            return fail('that is already how they are shown')
        if value == 'buttons':
            option.pop('subSelector', None)
        else:
            option['subSelector'] = value
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'field': field, 'from': old_value, 'to': value})
        categories_file.write_text(json.dumps(categories, indent=1) + '\n')
        log_edit('category', target, field, old_value, value, actor, reason)
        ensure_member(actor)
        commit_push(f'Expert edit category {target}: subSelector\n\nFrom: {old_value}\nTo: {value}\n'
                    f'By: {actor}\nReason: {reason}\nVia: archivist')
        return jsonify({'ok': True, 'kind': kind, 'key': target, 'field': field,
                        'from': old_value, 'to': value})

    def _seed_category_metrics(game_key, option_key, fresh):
        """Initialize newly added scoring metrics on matching archived runs."""
        touched = 0
        if fresh:
            for run_json_path in (ARCHIVE / 'games' / game_key / 'runs').glob('*/run.json'):
                category_run = json.loads(run_json_path.read_text())
                if (category_run.get('category') or {}).get('goal') != option_key:
                    continue
                for fresh_key in fresh:
                    category_run.setdefault('metrics', {}).setdefault(fresh_key, 0)
                run_json_path.write_text(json.dumps(category_run, indent=1) + '\n')
                touched += 1
        return touched

    def _expert_category_metrics(game_key, option_key, sub_key, value, option, categories, categories_file, target, actor, reason, dry_run, kind, field):
        """Apply the category metrics correction and its audit entry."""
        metric_defs, metric_error = parse_metric_defs(value)
        if metric_error:
            return fail(metric_error)
        old_defs = option.get('metrics')
        old_value = json.dumps(old_defs) if old_defs else '(classic: time)'
        new_value = json.dumps(metric_defs) if metric_defs else '(classic: time)'
        if old_value == new_value:
            return fail('that is already its metric definition')
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'field': field,
                            'from': old_value, 'to': new_value})
        if metric_defs:
            option['metrics'] = metric_defs
        else:
            option.pop('metrics', None)
        categories_file.write_text(json.dumps(categories, indent=1) + '\n')
        # a freshly added metric writes the explicit empty value onto
        # every run already in the category: nothing gets unranked,
        # zeros rank last, and the experts fill them in from here
        old_keys = {metric_def['key'] for metric_def in (old_defs or [])}
        fresh = [metric_def['key'] for metric_def in (metric_defs or [])
                 if metric_def['key'] != 'time' and metric_def['key'] not in old_keys]
        touched = _seed_category_metrics(game_key, option_key, fresh)
        log_edit('category', target, field, old_value[:300], new_value[:300],
                 actor, reason)
        ensure_member(actor)
        commit_push(f'Expert edit category {target}: metrics\n\n'
                    f'By: {actor}\nReason: {reason}\n'
                    f'Runs seeded with empty values: {touched}\n'
                    f'Via: archivist')
        return jsonify({'ok': True, 'kind': kind, 'key': target,
                        'field': field, 'from': old_value, 'to': new_value,
                        'runs_seeded': touched})

    def _expert_category_text(option, field, value, dry_run, categories_file, categories, target, actor, reason):
        """Change one category or subcategory label or markdown rule."""
        limit = 80 if field == 'label' else 2000   # rules are markdown
        if not (1 <= len(value) <= limit):
            return fail(f'a {field} fits in {limit} characters')
        old_value = option.get(field, '')
        if old_value == value:
            return fail(f'that is already its {field}')
        option[field] = value
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'field': field,
                            'from': old_value, 'to': value})
        categories_file.write_text(json.dumps(categories, indent=1) + '\n')
        log_edit('category', target, field, old_value, value, actor, reason)
        return old_value, value, [], None

    def _expert_category(actor, target, field, value, reason, dry_run, kind):
        """Apply one logged expert category correction within its jurisdiction."""
        target_match = re.fullmatch(r'([a-z0-9-]+/[a-z0-9-]+):([a-z0-9-]+|\*)(?:/([a-z0-9-]+))?', target)
        if not target_match:
            return fail('target must be system/slug:option, or system/slug:option/subcategory')
        game_key, option_key, sub_key = target_match.group(1), target_match.group(2), target_match.group(3)
        if option_key == '*' and field == 'selector':
            return _expert_category_selector(game_key, option_key, sub_key, value, None, None, None, target, actor, reason, dry_run, kind, field)
        categories_file = ARCHIVE / 'games' / game_key / 'categories.json'
        if not categories_file.exists():
            return fail(f'unknown game {game_key}', 404)
        if not expert_covers(actor, game_key) and not is_editor(actor):
            return fail(f'{actor!r} is not an expert covering {game_key}, '
                        f'nor an editor', 403)
        categories = json.loads(categories_file.read_text())
        option = next((o for d in categories['dimensions'] for o in d['options']
                    if o['key'] == option_key), None)
        if not option:
            return fail(f'{game_key} defines no category {option_key!r}', 404)
        if field == 'key':
            return _expert_category_key(game_key, option_key, sub_key, value, option, categories, categories_file, target, actor, reason, dry_run, kind, field)
        if field == 'subSelector' and (not sub_key):
            return _expert_category_sub_selector(game_key, option_key, sub_key, value, option, categories, categories_file, target, actor, reason, dry_run, kind, field)
        if sub_key:
            # a subcategory's label or rule: the same edit, on the inner record
            sub = next((s for s in option.get('subcategories', []) if s['key'] == sub_key), None)
            if not sub:
                return fail(f'{option["label"]} has no subcategory {sub_key!r}', 404)
            if field not in ('label', 'rule'):
                return fail('a subcategory has a label and a rule; metrics are the category\'s')
            option = sub
        if field == 'metrics':
            return _expert_category_metrics(game_key, option_key, sub_key, value, option, categories, categories_file, target, actor, reason, dry_run, kind, field)
        return _expert_category_text(option, field, value, dry_run, categories_file,
                                     categories, target, actor, reason)

    def _expert_group(actor, target, field, value, reason, dry_run, kind):
        """Apply one logged expert group correction within its jurisdiction."""
        groups_doc = load_groups()
        group = next((g for g in groups_doc['groups'] if g['key'] == target.lower()), None)
        if not group:
            return fail(f'no group with the key {target!r}', 404)
        if not covers_group(actor, group) and not is_editor(actor):
            return fail(f'{actor} holds no scope covering the {group["title"]} group',
                        403)
        if not (1 <= len(value) <= 80):
            return fail('a title fits in 80 characters')
        old_value = group.get('title')
        if old_value == value:
            return fail('that is already its title')
        group['title'] = value
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'field': field,
                            'from': old_value, 'to': value})
        save_groups(groups_doc)
        log_edit('group', target.lower(), field, old_value, value, actor, reason)
        return old_value, value, [], None

    def _notify_expert_edit(kind, actor, target, field, voided, reason, run_dir):
        """Announce a committed expert correction with its affected run or page."""
        if kind == 'run':
            edited_run = json.loads((run_dir / 'run.json').read_text())
            what, where = (movie_md(edited_run) + in_category(edited_run),
                           f'{SITE_URL}/runs/{target}/')
        elif kind == 'game':
            what, where = (f'the game [{target}](<{SITE_URL}/games/{target}/>)',
                           f'{SITE_URL}/games/{target}/')
        elif kind == 'group':
            what, where = (f'the group [{target}](<{SITE_URL}/groups/{target}/>)', None)
        else:
            what, where = (f'the category {target}', None)
        notify_edit(actor, what, [field], voided if kind == 'run' else (), reason,
                    link=where)

    def _locked_expert_edit(dry_run, edit_form):
        """Process the expert edit request under the archive write lock."""
        auth_error = auth_precheck(edit_form)
        if auth_error:
            return auth_error
        actor, error = request_identity(edit_form, 'expert')
        if error:
            return error
        kind = (edit_form.get('kind') or '').strip()
        target = (edit_form.get('target') or '').strip()
        field = (edit_form.get('field') or '').strip()
        value = (edit_form.get('value') or '').strip()
        reason = (edit_form.get('reason') or '').strip()
        if kind not in EXPERT_EDITABLE:
            return fail('kind must be run, game, category or group')
        if field not in EXPERT_EDITABLE[kind] and not (
                kind == 'run' and field.startswith('metric:')):
            return fail(f'{field!r} is not expert-editable on a {kind}; the record '
                        f'allows: {", ".join(EXPERT_EDITABLE[kind])}. Member content '
                        f'is never edited by anybody but its author.')
        if not (8 <= len(reason) <= 500):
            return fail('say why, publicly: the edit log carries your reason')
        if not dry_run:
            checkout_branch()

        outcome = {
            'run': _expert_run, 'game': _expert_game,
            'category': _expert_category, 'group': _expert_group,
        }[kind](actor, target, field, value, reason, dry_run, kind)
        if not isinstance(outcome, tuple) or len(outcome) != 4:
            return outcome
        old_value, value, voided, run_dir = outcome

        ensure_member(actor)
        commit_push(f'Expert edit {kind} {target}: {field}\n\n'
                    f'From: {str(old_value)[:120]}\nTo: {value[:120]}\n'
                    f'By: {actor}\nReason: {reason}\nVia: archivist')
        # a run is said the way runs are said; anything else names its kind
        _notify_expert_edit(kind, actor, target, field, voided, reason, run_dir)
        return True, (field, kind, old_value, target, value)

    @app.post('/api/expert/edit')
    def expert_edit():
        """Correct one field within expert jurisdiction, with a public audit trail.

        Editors may correct library shape only, never member authorship.
        Reads kind, target, field, value, reason (8–500 chars), dry_run and
        optional movie or thumbnail upload. Answers old/new values or preview;
        category metrics may also report seeded runs.
        """
        edit_form = request.form
        dry_run = edit_form.get('dry_run') in ('1', 'true', 'yes')
        refresh_archive()
        with lock:
            locked_result = _locked_expert_edit(dry_run, edit_form)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        field, kind, old_value, target, value = locked_result[1]

        return jsonify({'ok': True, 'kind': kind, 'key': target, 'field': field,
                        'from': old_value, 'to': value})


    def _move_time_constraints(run, goal, wants_time):
        """Require the destination to agree with the run's stated time."""
        needs_stated_time = (run.get('videoOnly')
                             or not (run.get('movie') or {}).get('frames'))
        if goal != 'unclassified' and wants_time and needs_stated_time \
                and not run.get('duration'):
            return fail('the destination category ranks by time, but this run has '
                        'no stated duration')
        if not wants_time and run.get('videoOnly') and run.get('duration'):
            return fail('the destination category does not rank by time, but this '
                        'video-only run carries a stated duration')
        return None

    def _move_unclassified_guard(run):
        """Keep verified or undescribed runs from entering Unclassified."""
        if any(not verification.get('invalidated')
               for verification in run.get('verifications', [])):
            return fail('this run holds live verifications bound to a defined goal; '
                        'it cannot be moved to Unclassified')
        if not run.get('goalDescription'):
            return fail('an Unclassified run must already state what it does')
        return None

    def _move_goal(run_dir, target_game, goal, raw_sub, categories):
        """Validate the destination goal, subcategory, metrics and stated time."""
        valid_goals = {o['key'] for d in categories.get('dimensions', [])
                       for o in d.get('options', [])}
        valid_goals.add('unclassified')
        if goal not in valid_goals:
            return None, fail(f'{goal!r} is not a goal {target_game} defines')
        new_category = {'goal': goal}
        run = json.loads((run_dir / 'run.json').read_text())
        if goal == 'unclassified':
            guard = _move_unclassified_guard(run)
            if guard:
                return None, guard
        else:
            sub_error = place_subcategory(categories, new_category, raw_sub)
            if sub_error:
                return None, fail(sub_error)

        target_option = option_in(categories, goal)
        metric_defs = (target_option or {}).get('metrics')
        wants_time = metric_defs is None or any(
            metric_def['key'] == 'time' for metric_def in metric_defs)
        missing_metrics = [
            metric_def['label'] for metric_def in (metric_defs or [])
            if metric_def['key'] != 'time'
            and not isinstance((run.get('metrics') or {}).get(metric_def['key']),
                               (int, float))
        ]
        if missing_metrics:
            return None, fail('the destination category requires scoring values this run '
                        'does not carry: ' + ', '.join(missing_metrics))
        time_error = _move_time_constraints(run, goal, wants_time)
        if time_error is not None:
            return None, time_error
        return (run, new_category), None

    def _move_games(run_dir, target_game, actor):
        """Require coverage of both source and destination archived games."""
        source_game = f'{run_dir.parent.parent.parent.name}/{run_dir.parent.parent.name}'
        if source_game == target_game:
            return None, fail('pick a different game; category-only moves use Edit run')
        if not expert_covers(actor, source_game):
            return None, fail(f'{actor!r} is not an expert covering {source_game}', 403)
        if not expert_covers(actor, target_game):
            return None, fail(f'{actor!r} is not an expert covering {target_game}', 403)
        target_game_doc, categories = load_game(*target_game.split('/'))
        if not target_game_doc or categories is None:
            return None, fail(f'unknown game {target_game}', 404)
        return (source_game, categories), None

    def _commit_run_move(run_dir, run_id, target_game, source_game, goal, new_category, run, actor, reason):
        """Relocate the run and log its category and invalidated acts in one commit."""
        target_dir = ARCHIVE / 'games' / target_game / 'runs' / run_id
        if target_dir.exists():
            return None, fail(f'{run_id} already has a folder in {target_game}', 409)
        old_category = run.get('category') or {}
        old_place = source_game + ':' + old_category.get('goal', '')
        if old_category.get('sub'):
            old_place += '/' + old_category['sub']
        new_place = target_game + ':' + goal + (
            '/' + new_category['sub'] if new_category.get('sub') else '')

        run['game'] = target_game
        run['category'] = new_category
        voided = void_acts_for(run, ['goal'], actor)
        (run_dir / 'run.json').write_text(json.dumps(
            {k: v for k, v in run.items() if not k.startswith('_')}, indent=1) + '\n')
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(run_dir), str(target_dir))
        log_edit('run', run_id, 'game/category', old_place, new_place, actor, reason)
        if voided:
            log_edit('run', run_id, 'acts voided', ', '.join(voided),
                     'by the change of game/category', actor, reason)
        ensure_member(actor)
        commit_push(f'Move {run_id}: {source_game} to {target_game} by expert {actor}\n\n'
                    f'From: {old_place}\nTo: {new_place}\n'
                    f'Reason: {reason}\nVia: archivist')
        notify_discord(f'\u27a1\ufe0f **{member_md(actor)}** moved '
                       + movie_md(run) + f' to {new_place}'
                       + (' (' + ' and '.join(voided) + ' invalidated)' if voided else '')
                       + f' \u00b7 {" ".join(reason.split())[:160]}',
                       wait_for=f'{SITE_URL}/runs/{run_id}/')
        return (old_place, new_place, voided), None

    def _locked_run_move(move_form):
        """Process the run move request under the archive write lock."""
        auth_error = auth_precheck(move_form)
        if auth_error:
            return auth_error
        actor, error = request_identity(move_form, 'expert')
        if error:
            return error
        run_id = (move_form.get('run') or '').strip()
        target_game = (move_form.get('game') or '').strip()
        goal = (move_form.get('goal') or '').strip()
        raw_sub = (move_form.get('sub') or '').strip()
        reason = (move_form.get('reason') or '').strip()
        if not re.fullmatch(r'M[0-9]+', run_id):
            return fail('run must be a run id like M100001')
        if not re.fullmatch(r'[a-z0-9-]+/[a-z0-9-]+', target_game):
            return fail('game must be system/slug')
        if not (8 <= len(reason) <= 500):
            return fail('say why, publicly: the edit log carries your reason')

        checkout_branch()
        run_dir = find_run(run_id)
        if not run_dir:
            return fail(f'unknown run {run_id}', 404)
        game_data, error = _move_games(run_dir, target_game, actor)
        if error is not None:
            return error
        source_game, categories = game_data
        goal_data, error = _move_goal(run_dir, target_game, goal, raw_sub, categories)
        if error is not None:
            return error
        run, new_category = goal_data
        result, error = _commit_run_move(run_dir, run_id, target_game, source_game,
                                         goal, new_category, run, actor, reason)
        if error is not None:
            return error
        old_place, new_place, voided = result
        return True, (new_place, old_place, run_id, voided)

    @app.post('/api/run/move')
    def run_move():
        """Move a run recorded under the wrong game into its real game.

        Who: an expert covering both the source and destination games
        Reads: run, game, goal, sub (when required), reason (8 to 500 chars)
        Writes: the relocated run folder, its run.json game/category, and edits.json,
            all in one archive commit
        Answers: {ok, run, from, to}
        """
        move_form = request.form
        refresh_archive()
        with lock:
            locked_result = _locked_run_move(move_form)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        new_place, old_place, run_id, voided = locked_result[1]
        return jsonify({'ok': True, 'run': run_id, 'from': old_place, 'to': new_place,
                        'voided': voided})


    def _author_credit_conflict(run, new_authors):
        """Reject credit for someone who has acted on the run as a non-author."""
        acted = ({current_name(x['user']).lower() for x in run.get('reproductions', [])}
                 | {current_name(x['user']).lower() for x in run.get('verifications', [])}
                 | {current_name(l['user']).lower() for l in run.get('likes', [])})
        clash = [a for a in new_authors if current_name(a).lower() in acted]
        if clash:
            return fail(f'cannot credit {", ".join(clash)} as author: they already '
                        f'reproduced, verified, or liked this run (authors may not '
                        f'act on their own runs)')
        return None

    def _new_author_list(edit_form):
        """Read a nonempty list of credited run authors."""
        new_authors = [a.strip() for a in (edit_form.get('authors') or '').split(',') if a.strip()]
        if not new_authors:
            return None, fail('a run needs at least one author')
        return new_authors, None

    def _edit_authors(edit_form, run, run_dir, run_id, user, is_author, dry_run, changed, befores):
        """Validate and apply the run's authors revision, if supplied."""
        if 'authors' in edit_form:
            if not is_author:
                return fail("an author list is never an expert's edit: who made "
                            "a thing is moderation's question", 403)
            new_authors, author_error = _new_author_list(edit_form)
            if author_error:
                return author_error
            conflict = _author_credit_conflict(run, new_authors)
            if conflict:
                return conflict
            if [a['user'] for a in run['authors']] != new_authors:
                run['authors'] = [{'user': a} for a in new_authors]
                changed.append('authors')
                if not dry_run:
                    ensure_member(user)
        return None

    def _edit_notes(edit_form, run, run_dir, run_id, user, is_author, dry_run, changed, befores):
        """Validate and apply the run's notes revision, if supplied."""
        if 'notes' in edit_form:
            notes = (edit_form.get('notes') or '').replace('\r\n', '\n').replace('\r', '\n')
            if len(notes.encode()) > 1024 * 1024:
                return None, fail('notes exceed 1 MB')
            try:
                old_notes = (run_dir / 'notes.md').read_text()
            except OSError:
                old_notes = ''
            # the form edits the author's part; the archive's own header (an
            # import's disclaimer) stays on top, untouched, whatever is sent
            notes_header, old_body = split_notes_header(old_notes)
            sent = notes.strip() + '\n' if notes.strip() else ''
            if notes_header and sent.startswith(notes_header):
                sent = sent[len(notes_header):]
            notes = notes_header + sent
            if old_body != sent:
                changed.append('notes')
        return (notes if 'notes' in edit_form else None), None

    def _edit_emulator(edit_form, run, run_dir, run_id, user, is_author, dry_run, changed, befores):
        """Validate and apply the run's emulator revision, if supplied."""
        if 'emulator' in edit_form:
            new_emulator = (edit_form.get('emulator') or '').strip()[:120]
            if new_emulator != run.get('contract', {}).get('emulator', ''):
                run.setdefault('contract', {})['emulator'] = new_emulator
                changed.append('emulator')
        return None

    def _edit_files(edit_form, run, run_dir, run_id, user, is_author, dry_run, changed, befores):
        """Validate and apply the run's files revision, if supplied."""
        if 'files_set' in edit_form:
            # the files list, whole: the form always sends every row, so an
            # emptied list clears it. A legacy single `rom` is replaced by
            # the list the moment the author or an expert revises it
            new_files, files_error = parse_file_rows(edit_form)
            if files_error:
                return fail(files_error)
            old_files = run.get('contract', {}).get('files')
            if old_files is None and run.get('contract', {}).get('rom'):
                old_files = [run['contract']['rom']]
            if (old_files or []) != new_files:
                befores['files'] = '; '.join(f"{f.get('name', '')} {f.get('sha1', '')}".strip()
                                             for f in (old_files or []))
                contract = run.setdefault('contract', {})
                contract.pop('rom', None)
                if new_files:
                    contract['files'] = new_files
                else:
                    contract.pop('files', None)
                changed.append('files')
        return None

    def _edit_completed(edit_form, run, run_dir, run_id, user, is_author, dry_run, changed, befores):
        """Validate and apply the run's completed revision, if supplied."""
        if 'completed' in edit_form:
            completed_value = (edit_form.get('completed') or '').strip()
            if completed_value:
                if not re.fullmatch(r'(19[89]\d|20\d{2})-(0[1-9]|1[0-2])'
                                    r'-(0[1-9]|[12]\d|3[01])', completed_value):
                    return fail('completed must be a date like 2021-10-26')
                if completed_value > time.strftime('%Y-%m-%d', time.gmtime()):
                    return fail('completed cannot be in the future')
            if completed_value != run.get('completed', ''):
                befores['completed'] = run.get('completed', '')
                if completed_value:
                    run['completed'] = completed_value
                else:
                    run.pop('completed', None)
                changed.append('completed')
        return None

    def _edit_warnings(edit_form, run, run_dir, run_id, user, is_author, dry_run, changed, befores):
        """Validate and apply the run's warnings revision, if supplied."""
        if 'content_warnings_set' in edit_form:
            # the content disclosures (#49): the form always sends the marker,
            # so no box ticked means "none" and clears them
            new_warnings = sorted(set(edit_form.getlist('content_warnings')))
            if any(w not in CW_ALLOWED for w in new_warnings):
                return fail('unknown content warning')
            old_warnings = sorted(run.get('contentWarnings', []))
            if new_warnings != old_warnings:
                befores['contentWarnings'] = ', '.join(old_warnings)
                if new_warnings:
                    run['contentWarnings'] = new_warnings
                else:
                    run.pop('contentWarnings', None)
                changed.append('contentWarnings')
        return None

    def _related_run_ids(raw_ids, run_id):
        """Validate unique recommendation IDs and their eight-run limit."""
        related_ids = []
        for rid_ in raw_ids:
            if not re.fullmatch(r'M[0-9]+', rid_):
                return None, fail(f'{rid_!r} is not a run id like M100001')
            if rid_ == run_id:
                return None, fail('a run cannot recommend itself')
            if not find_run(rid_):
                return None, fail(f'unknown run {rid_}', 404)
            if rid_ not in related_ids:
                related_ids.append(rid_)
        if len(related_ids) > 8:
            return None, fail('at most 8 designated runs')
        return related_ids, None

    def _edit_related(edit_form, run, run_dir, run_id, user, is_author, dry_run, changed, befores):
        """Validate and apply the run's related revision, if supplied."""
        if 'related' in edit_form:
            # the run page's "You may also like" picks (up to 8 run ids,
            # shown before every computed suggestion); presentation only,
            # so nothing is voided
            raw_ids = [t for t in re.split(r'[,\s]+', edit_form.get('related') or '') if t]
            related_ids, error = _related_run_ids(raw_ids, run_id)
            if error is not None:
                return error
            if related_ids != run.get('related', []):
                befores['related'] = ', '.join(run.get('related', []))
                if related_ids:
                    run['related'] = related_ids
                else:
                    run.pop('related', None)
                changed.append('related')
        return None

    def _edit_description(edit_form, run, run_dir, run_id, user, is_author, dry_run, changed, befores):
        """Validate and apply the run's description revision, if supplied."""
        if 'goalDescription' in edit_form:
            goal_description = (edit_form.get('goalDescription') or '').strip()
            if len(goal_description) > 500:
                return fail('a goal description fits in 500 characters')
            if is_uncl_run(run) and not goal_description:
                return fail('an Unclassified run states its own goal; it cannot lose '
                            'its description')
            if goal_description != run.get('goalDescription', ''):
                befores['goalDescription'] = run.get('goalDescription', '')
                if goal_description:
                    run['goalDescription'] = goal_description
                else:
                    run.pop('goalDescription', None)
                changed.append('goalDescription')
        return None

    def _edit_movie(edit_form, run, run_dir, run_id, user, is_author, dry_run, changed, befores):
        """Validate and apply the run's movie revision, if supplied."""
        movie_upload = request.files.get('movie')
        if movie_upload and movie_upload.filename:
            old_movie = run.get('movie')
            old_value = (f"{old_movie['file']} (sha1 {old_movie.get('sha1', '?')[:12]})"
                         if old_movie else 'none: the run was video-only')
            if not dry_run:
                if old_movie:
                    (run_dir / old_movie['file']).unlink(missing_ok=True)
                value, movie_error = attach_movie(run, run_dir, movie_upload)
                if movie_error:
                    return movie_error
            else:
                ext = movie_upload.filename.rsplit('.', 1)[-1].lower()
                movie_bytes = movie_upload.read()
                if not movie_bytes or len(movie_bytes) > MOVIE_MAX:
                    return fail(MOVIE_MUST_BE_NON_EMPTY_AND_UNDER_MAX)
                value = f"{run['id']}.{ext} (sha1 {hashlib.sha1(movie_bytes).hexdigest()[:12]})"
            befores['movie'] = old_value
            changed.append('movie')
        return None

    def _edit_encode(edit_form, run, run_dir, run_id, user, is_author, dry_run, changed, befores):
        """Validate and apply the run's encode revision, if supplied."""
        if 'encode' in edit_form:
            encode_url = (edit_form.get('encode') or '').strip()
            if encode_url:
                encode_provider = providers.resolve(encode_url)
                if not encode_provider:
                    return fail('encode must be a watchable URL on a platform we accept')
                if encode_url != (run.get('encodes') or [{}])[0].get('url', ''):
                    befores['encode'] = (run.get('encodes') or [{}])[0].get('url', '')
                    run['encodes'] = [{'kind': encode_provider['kind'], 'url': encode_url}]
                    changed.append('encode')
        return None

    def _edit_metric_value(run, metric_key, raw, metric_keys, changed, befores):
        """Validate and apply a single scoring metric revision."""
        if raw == '':
            return None
        if metric_key not in metric_keys:
            return fail(f'this category states no metric {metric_key!r}')
        try:
            metric_value = float(raw)
        except ValueError:
            return fail(f'{metric_key} must be a number (seconds for times)')
        if metric_value < 0:
            return fail(f'{metric_key} cannot be negative')
        if metric_value != (run.get('metrics') or {}).get(metric_key, 0):
            befores[f'metric:{metric_key}'] = str((run.get('metrics') or {}).get(metric_key, 0))
            run.setdefault('metrics', {})[metric_key] = metric_value
            changed.append(f'metric:{metric_key}')
        return None

    def _edit_metrics(edit_form, run, run_dir, run_id, user, is_author, dry_run, changed, befores):
        """Validate and apply the run's metrics revision, if supplied."""
        system, slug = run['game'].split('/')
        game, categories = load_game(system, slug)
        goal = (run.get('category') or {}).get('goal')
        option = next((o for dimension in (categories or {}).get('dimensions', [])
                     for o in dimension['options'] if o['key'] == goal), None)
        metric_keys = {mm['key'] for mm in (option or {}).get('metrics', [])
                  if mm['key'] != 'time'}
        for form_key in list(edit_form.keys()):
            if not form_key.startswith('metric_'):
                continue
            metric_key = form_key[len('metric_'):]
            raw = (edit_form.get(form_key) or '').strip()
            metric_error = _edit_metric_value(run, metric_key, raw, metric_keys, changed, befores)
            if metric_error:
                return None, metric_error
        return option, None

    def _legacy_run_duration(run, legacy_frames):
        """Derive the legacy run's time from movie frames and system FPS."""
        derived = None
        if legacy_frames:
            movie_fps = (run.get('movie') or {}).get('fps')
            if not movie_fps:
                try:
                    movie_fps = json.loads((ARCHIVE / 'systems.json').read_text()).get(
                        run['game'].split('/')[0], {}).get('fps')
                except (OSError, ValueError):
                    movie_fps = None
            if movie_fps:
                derived = run['movie']['frames'] / movie_fps
        return derived

    def _edit_time(edit_form, run, run_dir, run_id, user, is_author, dry_run, changed, befores, option):
        """Validate and apply the run's time revision, if supplied."""
        option_metrics = (option or {}).get('metrics')
        option_wants_time = option_metrics is None or any(mm['key'] == 'time' for mm in option_metrics)
        stated_time = (edit_form.get('time') or '').strip()
        # a legacy run that never stated a duration still ranks by its
        # frames; an empty time on one means "keep deriving", never an error
        legacy_frames = run.get('duration') is None and (run.get('movie') or {}).get('frames')
        if not ('time' in edit_form and option_wants_time and not (stated_time == '' and legacy_frames)):
            return None
        time_match = re.fullmatch(r'(?:(\d{1,3}):)?(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?', stated_time)
        if not time_match:
            return fail('this category ranks by time, so the run states it as [h:]mm:ss or [h:]mm:ss.mmm')
        hours, minutes, seconds, fraction = time_match.groups()
        stated_ms = (int(hours or 0) * 3600000 + int(minutes) * 60000
                     + int(seconds) * 1000
                     + (int(fraction.ljust(3, '0')) if fraction else 0))
        duration = stated_ms / 1000
        if duration <= 0:
            return fail('a run that takes no time at all is not a run')
        # only a real change is a change: the form sends the record's own
        # value back on every save, and that must never void anything.
        # A legacy run's record is its frames-derived time: the form
        # prefills that, so getting it back (to the millisecond the form
        # rounds to) means "keep deriving", not a newly stated duration
        derived = _legacy_run_duration(run, legacy_frames)
        # The picker speaks in whole milliseconds and rounds half up, so
        # the record comes back through it rounded. Comparing against the
        # value the form WOULD show is what makes a round trip silent: a
        # stored duration of sub-millisecond precision (a time imported
        # from a movie or an encode) used to land a hair past a half-
        # millisecond tolerance and read as a scoring change, which voided
        # the verifications of somebody who only touched the encode.
        def form_ms(value):
            """Round a stored duration as the browser's picker displays it."""
            return int(value * 1000 + 0.5)      # as the picker rounds it
        same_as_record = (stated_ms == form_ms(run['duration']) if run.get('duration') is not None
                          else derived is not None and abs(duration - derived) < 0.002)
        if not same_as_record:
            befores['duration'] = str(run.get('duration'))
            run['duration'] = duration
            changed.append('duration')
        return None

    def _partition_attachments(old_attachments, attachments_delete):
        """Separate retained attachments from requested file removals."""
        remaining_existing = []
        deleted_files_to_remove = []
        for a in old_attachments:
            fname = pathlib.Path(a['file']).name
            if fname in attachments_delete:
                deleted_files_to_remove.append(a['file'])
            else:
                remaining_existing.append(a)
        return remaining_existing, deleted_files_to_remove

    def _add_revision_attachments(run, remaining_existing, changed):
        """Validate and append new supplementary uploads."""
        new_attachments, attachment_error = read_attachments(remaining_existing)
        if attachment_error:
            return None, attachment_error
        if new_attachments:
            existing_files = {pathlib.Path(a['file']).name for a in remaining_existing}
            clash = [name for name, _ in new_attachments if name in existing_files]
            if clash:
                return None, fail(f'attachment {clash[0]!r} already exists on this run')
            run.setdefault('attachments', []).extend(
                {'file': f'attachments/{name}', 'role': 'supplementary'}
                for name, _ in new_attachments)
            if 'attachments' not in changed:
                changed.append('attachments')
        return new_attachments, None

    def _edit_attachments(run, is_author, changed, befores):
        """Apply attachment removals and additions after validating ownership and limits."""
        attachments_delete = [
            re.sub(r'[^A-Za-z0-9._-]', '_', pathlib.Path(f).name)
            for f in request.form.getlist('attachments_delete') if f
        ]
        has_new_attachments = bool(request.files.getlist('attachments'))
        if attachments_delete or has_new_attachments:
            if not is_author:
                return None, None, fail("supplementary files are the authors' own uploads", 403)

        old_attachments = list(run.get('attachments') or [])
        befores['attachments'] = ', '.join(pathlib.Path(a['file']).name for a in old_attachments) or '(none)'

        remaining_existing, deleted_files_to_remove = _partition_attachments(
            old_attachments, attachments_delete)
        if deleted_files_to_remove:
            run['attachments'] = remaining_existing
            if not run['attachments']:
                run.pop('attachments', None)
            if 'attachments' not in changed:
                changed.append('attachments')

        new_attachments, attachment_error = _add_revision_attachments(run, remaining_existing, changed)
        if attachment_error:
            return None, None, attachment_error
        return deleted_files_to_remove, new_attachments, None

    def _persist_run_revision(run, run_dir, changed, user, notes, deleted_files_to_remove, new_attachments):
        """Void obsolete acts and write the changed run and uploaded files."""
        voided = void_acts_for(run, changed, user)
        if 'notes' in changed:
            (run_dir / 'notes.md').write_text(notes)
        for rel in deleted_files_to_remove:
            (run_dir / rel).unlink(missing_ok=True)
        if new_attachments:
            (run_dir / 'attachments').mkdir(exist_ok=True)
            for name, data in new_attachments:
                (run_dir / 'attachments' / name).write_bytes(data)
        if (run_dir / 'attachments').exists() and not any((run_dir / 'attachments').iterdir()):
            try:
                (run_dir / 'attachments').rmdir()
            except OSError:
                pass
        (run_dir / 'run.json').write_text(json.dumps(
            {k: v for k, v in run.items() if not k.startswith('_')}, indent=1))
        return voided

    def _revision_field_value(run, field):
        """Describe a revised field for the public edit log."""
        return ('(see the run)' if field in ('notes', 'authors') else
                      str((run.get('metrics') or {}).get(field.split(':', 1)[1], '')
                          if field.startswith('metric:') else
                          {'emulator': run.get('contract', {}).get('emulator', ''),
                           'completed': run.get('completed', ''),
                           'goalDescription': run.get('goalDescription', ''),
                           'encode': (run.get('encodes') or [{}])[0].get('url', ''),
                           'duration': run.get('duration', ''),
                           'contentWarnings': ', '.join(run.get('contentWarnings', [])),
                           'related': ', '.join(run.get('related', [])),
                           'files': '; '.join(f"{f.get('name', '')} {f.get('sha1', '')}".strip()
                                              for f in run.get('contract', {}).get('files', [])),
                           'attachments': ', '.join(pathlib.Path(a['file']).name for a in (run.get('attachments') or [])) or '(none)',
                           }.get(field, ''))[:300])

    def _log_run_revision(run, run_id, changed, befores, user, is_author, reason, voided):
        """Log every changed field and commit the complete run revision."""
        # every revision joins the same history the expert edits live in: the
        # author owes nobody a justification for editing their own work, but
        # the history prevails either way
        for field in changed:
            log_edit('run', run_id, field,
                     befores.get(field, '(previous value in git history)'),
                     _revision_field_value(run, field),
                     user, "The author's own revision." if is_author else reason)
        commit_push(f'Edit {run_id}: {", ".join(changed)} by '
                    f'{"author" if is_author else "expert"} {user}\n\nVia: archivist')
        notify_edit(user, movie_md(run) + in_category(run), changed, voided,
                    '' if is_author else reason,
                    link=f'{SITE_URL}/runs/{run_id}/')

    def _apply_edit_fields(edit_form, run, run_dir, run_id, user, is_author,
                           dry_run, changed, befores):
        """Apply requested run fields in order, stopping at the first invalid edit."""
        error = _edit_authors(edit_form, run, run_dir, run_id, user, is_author, dry_run, changed, befores)
        if error is not None:
            return None, None, error
        # Only what actually differs is a change (issue #38): the form sends
        # every field every time, and a browser textarea submits CRLF, which
        # used to rewrite an untouched 96-line notes file on every edit.
        notes, error = _edit_notes(edit_form, run, run_dir, run_id, user, is_author, dry_run, changed, befores)
        if error is not None:
            return None, None, error
        error = _edit_emulator(edit_form, run, run_dir, run_id, user, is_author, dry_run, changed, befores)
        if error is not None:
            return None, None, error
        error = _edit_files(edit_form, run, run_dir, run_id, user, is_author, dry_run, changed, befores)
        if error is not None:
            return None, None, error
        # stated metric values: only the keys this run's category defines;
        # an empty field leaves the value untouched, an explicit 0 returns
        # it to "not yet stated" (which ranks last)
        option, error = _edit_metrics(edit_form, run, run_dir, run_id, user, is_author, dry_run, changed, befores)
        if error is not None:
            return None, None, error
        error = _edit_completed(edit_form, run, run_dir, run_id, user, is_author, dry_run, changed, befores)
        if error is not None:
            return None, None, error
        error = _edit_warnings(edit_form, run, run_dir, run_id, user, is_author, dry_run, changed, befores)
        if error is not None:
            return None, None, error
        error = _edit_related(edit_form, run, run_dir, run_id, user, is_author, dry_run, changed, befores)
        if error is not None:
            return None, None, error
        error = _edit_description(edit_form, run, run_dir, run_id, user, is_author, dry_run, changed, befores)
        if error is not None:
            return None, None, error
        # Authors and covering experts may replace the movie one item at a time.
        # The reproduction roster remains historical, while void_acts_for
        # marks its records obsolete because they synced the old file.
        error = _edit_movie(edit_form, run, run_dir, run_id, user, is_author, dry_run, changed, befores)
        if error is not None:
            return None, None, error
        error = _edit_encode(edit_form, run, run_dir, run_id, user, is_author, dry_run, changed, befores)
        if error is not None:
            return None, None, error
        # the run's time is the one its authors state, whatever the movie
        # holds; a score category has no time to state, so one left empty
        # there is no error (issue #62)
        error = _edit_time(edit_form, run, run_dir, run_id, user, is_author, dry_run, changed, befores, option)
        if error is not None:
            return None, None, error
        return notes, option, None

    def _preview_run_edit(run, changed, dry_run):
        """Report the acts a scoring or reproduction-information edit would void."""
        would_void = []
        if any(c in SCORING_FIELDS or c.startswith('metric:') for c in changed):
            if live_acts(run)['verifications']: would_void.append('verifications')
        if any(c in REPRO_FIELDS for c in changed):
            if live_acts(run)['reproductions']: would_void.append('reproductions')
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_change': changed, 'would_void': would_void})
        return None

    def _locked_edit_run(dry_run, edit_form):
        """Process the edit run request under the archive write lock."""
        auth_error = auth_precheck(edit_form)
        if auth_error:
            return auth_error
        if not dry_run:
            checkout_branch()
        user, error = request_identity(edit_form)
        if error:
            return error
        paced = pace_gate(edit_form, user, 'edit')
        if paced:
            return paced
        run_id = (edit_form.get('run') or '').strip()
        run_dir = find_run(run_id) if re.fullmatch(r'M[0-9]+', run_id) else None
        if not run_dir:
            return fail(f'unknown run {run_id}', 404)
        run = json.loads((run_dir / 'run.json').read_text())
        is_author = current_name(user).lower() in run_authors_now(run)
        if not is_author and not expert_covers(user, run['game']):
            return fail("only the run's authors or a covering expert may edit it", 403)
        reason = (edit_form.get('reason') or '').strip()
        if not is_author and not (8 <= len(reason) <= 500):
            return fail('an expert edit states its public reason (8 to 500 '
                        'characters), published in the edit log')
        changed = []
        befores = {'emulator': run.get('contract', {}).get('emulator', '')}
        notes, option, error = _apply_edit_fields(
            edit_form, run, run_dir, run_id, user, is_author, dry_run, changed, befores)
        if error is not None:
            return error
        deleted_files_to_remove, new_attachments, error = _edit_attachments(
            run, is_author, changed, befores)
        if error is not None:
            return error
        if not changed:
            return fail('nothing to change: every value sent already matches the '
                        'record (send notes, emulator, completed, goalDescription, '
                        'encode, attachments, or time)')
        # what this revision would void (the form asks before sending)
        preview = _preview_run_edit(run, changed, dry_run)
        if preview is not None:
            return preview
        voided = _persist_run_revision(run, run_dir, changed, user, notes,
                                       deleted_files_to_remove, new_attachments)
        _log_run_revision(run, run_id, changed, befores, user, is_author, reason, voided)
        return True, (changed, run_id, voided)

    @app.post('/api/edit')
    def edit_run():
        """Revise one run through the logged, git-reversible edit path.

        Authors may edit their work; covering experts require a public reason.
        Authors alone may change attribution or supplementary uploads.
        Reads run, editable fields, optional attachments, reason and dry_run.
        Answers {ok, run, changed} or a preview of proposed changes.
        """
        edit_form = request.form
        dry_run = edit_form.get('dry_run') in ('1', 'true', 'yes')
        with lock:
            locked_result = _locked_edit_run(dry_run, edit_form)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        changed, run_id, voided = locked_result[1]
        return jsonify({'ok': True, 'run': run_id, 'changed': changed, 'voided': voided})


    def _record_movie_duration(run, game_key):
        """Resolve duration for a legacy movie lacking a stated duration."""
        fps = (run.get('movie') or {}).get('fps')
        if not fps:
            try:
                fps = json.loads((ARCHIVE / 'systems.json').read_text()).get(
                    game_key.split('/')[0], {}).get('fps')
            except (OSError, ValueError):
                fps = None
        if fps:
            return run['movie']['frames'] / fps
        return None

    def _record_duration(run, game_key):
        """Report the run's stated or legacy frame-derived duration."""
        seconds = None
        seconds_source = None
        if run.get('duration'):
            seconds = run['duration']
            seconds_source = 'record'
        elif not run.get('videoOnly') and (run.get('movie') or {}).get('frames'):
            seconds = _record_movie_duration(run, game_key)
            if seconds is not None:
                seconds_source = 'movie'
        return seconds, seconds_source

    @app.get('/api/run/record')
    def run_record():
        """The run's record as the archivist holds it right now, for the edit
        form: run.json, the notes text, the game's categories, and what the
        caller may do with it (author, covering expert, editor).

        Who: anybody may read; the permissions reflect the session
        Reads: query run (M-id)
        Answers: {ok, run, notes, game: {key, title, system}, categories,
            may: {author, expert, editor}}, Cache-Control: no-store; 404 unknown
        """
        run_id = (request.args.get('run') or '').strip()
        if not re.fullmatch(r'M[0-9]+', run_id):
            return fail('run must be a run id like M100001')
        refresh_archive()
        run_dir = find_run(run_id)
        if not run_dir:
            return fail(f'unknown run {run_id}', 404)
        run = json.loads((run_dir / 'run.json').read_text())
        notes_path = run_dir / 'notes.md'
        notes = notes_path.read_text() if notes_path.exists() else ''
        # the archive's own header (the import disclaimer, a leading quote block)
        # is not the author's notes; their own quotes further down are
        notes = split_notes_header(notes)[1]
        game_key = f'{run_dir.parent.parent.parent.name}/{run_dir.parent.parent.name}'
        game = json.loads((run_dir.parent.parent / 'game.json').read_text())
        categories = json.loads((run_dir.parent.parent / 'categories.json').read_text())
        # the effective run time in seconds: stated duration wins; legacy runs
        # that never stated one derive it from frames/fps, falling back to the
        # system's fps when the movie file carries none
        seconds, seconds_source = _record_duration(run, game_key)
        who = session_user()
        may = {'author': bool(who) and current_name(who).lower() in run_authors_now(run),
               'expert': bool(who) and expert_covers(who, game_key),
               'editor': bool(who) and is_editor(who)}
        resp = jsonify({'ok': True, 'run': {k: v for k, v in run.items() if not k.startswith('_')},
                        'notes': notes, 'seconds': seconds, 'secondsSource': seconds_source,
                        'game': {'key': game_key, 'title': game.get('title'), 'system': game_key.split('/')[0]},
                        'categories': categories, 'may': may, 'user': who})
        resp.headers['Cache-Control'] = 'no-store'
        return resp


    def _locked_like(dry_run, like_form):
        """Process the like request under the archive write lock."""
        auth_error = auth_precheck(like_form)
        if auth_error:
            return auth_error
        if not dry_run:
            checkout_branch()
        user, error = request_identity(like_form)
        if error:
            return error
        paced = pace_gate(like_form, user, 'like')
        if paced:
            return paced
        run_id = (like_form.get('run') or '').strip()
        run_dir = find_run(run_id) if re.fullmatch(r'M[0-9]+', run_id) else None
        if not run_dir:
            return fail(f'unknown run {run_id}', 404)
        run = json.loads((run_dir / 'run.json').read_text())
        if run.get('withdrawn'):
            return fail(f'{run_id} has been withdrawn; no further acts apply')
        if current_name(user).lower() in run_authors_now(run):
            return fail('authors cannot like their own run')
        # The same star both ways: a second press takes the like back. Taking
        # it back deletes the entry outright, no tombstone and no log line, as
        # if it never happened: a like is a mood, not an act of authority, and
        # nobody owes the record an explanation for a change of heart. (The
        # git commit remains, as every commit does.)
        existing_like = [l for l in run.get('likes', []) if l['user'].lower() == user.lower()]
        if existing_like:
            run['likes'] = [l for l in run['likes'] if l['user'].lower() != user.lower()]
            liked = False
        else:
            run.setdefault('likes', []).append(
                {'user': user, 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso()})
            liked = True
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'liked': liked,
                            'likes': len(run['likes'])})
        (run_dir / 'run.json').write_text(json.dumps(
            {k: v for k, v in run.items() if not k.startswith('_')}, indent=1))
        ensure_member(user)
        commit_push(f'{"Like" if liked else "Unlike"} {run_id}: by {user}\n\nVia: archivist')
        return True, (liked, run, run_id)

    @app.post('/api/like')
    def like():
        """Thumbs-up: everybody except the run's own authors, one per run.
        Works on any run, Imported included — it feeds player points and orders
        the Unclassified rankings.

        Who: any member except the run's authors
        Reads: form fields run, dry_run
        Answers: {ok, run, liked, likes}
        """
        like_form = request.form
        dry_run = like_form.get('dry_run') in ('1', 'true', 'yes')
        with lock:
            locked_result = _locked_like(dry_run, like_form)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        liked, run, run_id = locked_result[1]
        return jsonify({'ok': True, 'run': run_id, 'liked': liked, 'likes': len(run['likes'])})


    def _locked_verify(dry_run, verification_form):
        """Process the verify request under the archive write lock."""
        auth_error = auth_precheck(verification_form)
        if auth_error:
            return auth_error
        if not dry_run:
            checkout_branch()
        act_error, run_dir, run, user = act_common(verification_form)
        if act_error:
            return act_error
        if any(a['user'].lower() == user.lower() and (not a.get('invalidated') or a['invalidated'].get('cause') != 'edit')
               for a in run.get('verifications', [])):
            return fail('you have already verified this run; one verification per member')
        if (run.get('category') or {}).get('goal') == 'unclassified':
            return fail('Unclassified runs cannot be verified because no goal is defined; '
                        'they can be reproduced and liked')
        if not run.get('encodes'):
            return fail('this run has no encode linked; verification needs one to judge from')

        entry = {'user': user, 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso()}
        game_key = f'{run_dir.parent.parent.parent.name}/{run_dir.parent.parent.name}'
        if expert_covers(user, game_key):
            entry['expert'] = True
        if (verification_form.get('notes') or '').strip():
            entry['notes'] = verification_form.get('notes').strip()
        run.setdefault('verifications', []).append(entry)
        sync_status(run)
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_record': entry,
                            'status': run['status']})

        (run_dir / 'run.json').write_text(json.dumps(
            {k: v for k, v in run.items() if not k.startswith('_')}, indent=1))
        ensure_member(user)
        commit_push(f'Verify {run["id"]}: by {user}\n\nVia: archivist')
        # every act is about a run in a category, and the category closes the
        # line: "verified [PS2] Athens 2004 by toca, Pole Vault" says what
        # was judged, and the reproduction notice says the same
        notify_discord(f'\u2713 **{member_md(user)}** verified '
                       + movie_md(run) + in_category(run),
                       wait_for=f'{SITE_URL}/runs/{run["id"]}/')
        return True, (run,)

    @app.post('/api/verify')
    def verify():
        """Verify a run's goal from its encode, the ranking gate.

        A non-author member may verify an encoded run with a defined goal;
        expert scope is stamped at the time of the act. Reads run, notes,
        dry_run. Answers status and verifications, or a dry-run preview.
        """
        verification_form = request.form
        dry_run = verification_form.get('dry_run') in ('1', 'true', 'yes')
        with lock:
            locked_result = _locked_verify(dry_run, verification_form)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        run, = locked_result[1]
        return jsonify({'ok': True, 'run': run['id'], 'status': run['status'],
                        'verifications': len([a for a in run['verifications'] if not a.get('invalidated')])})


    def _locked_withdraw(dry_run, withdrawal_form):
        """Process the withdraw request under the archive write lock."""
        auth_error = auth_precheck(withdrawal_form)
        if auth_error:
            return auth_error
        if not dry_run:
            checkout_branch()
        user, error = request_identity(withdrawal_form)
        if error:
            return error
        run_id = (withdrawal_form.get('run') or '').strip()
        run_dir = find_run(run_id) if re.fullmatch(r'M[0-9]+', run_id) else None
        if not run_dir:
            return fail(f'unknown run {run_id}', 404)
        run = json.loads((run_dir / 'run.json').read_text())
        is_author = current_name(user).lower() in run_authors_now(run)
        if not is_author:
            return fail("withdrawing is the author's own voluntary act; an expert "
                        "who must remove a run deletes it instead", 403)
        if run.get('withdrawn'):
            return fail(f'{run_id} is already withdrawn')
        reason = (withdrawal_form.get('reason') or '').strip()
        if not reason:
            return fail('a withdrawal must state its reason; it is shown in the open')
        if len(reason) > ACT_NOTES_MAX:
            return fail(f'reason exceeds {ACT_NOTES_MAX} characters')

        run['withdrawn'] = {'by': user, 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(),
                          'reason': reason, 'role': 'author'}
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_withdraw': run['withdrawn']})
        (run_dir / 'run.json').write_text(json.dumps(
            {k: v for k, v in run.items() if not k.startswith('_')}, indent=1))
        ensure_member(user)
        commit_push(f'Withdraw {run_id}: by {user}\n\nReason: {reason}\nVia: archivist')
        return True, (run, run_id)

    @app.post('/api/withdraw')
    def withdraw():
        """Take a run out of the listings.

        Withdrawing is a voluntary act: only the run's own authors may do it (an
        expert who must remove a run deletes it, on the record). Nothing is
        erased: the record, the movie file and the reason stay in the archive,
        because the principles forbid erasing a contribution (1.2, 2.8.2). The
        site stops listing it and says why.

        Who: one of the run's authors
        Reads: form fields run, reason, dry_run
        Answers: {ok, run, withdrawn}
        """
        withdrawal_form = request.form
        dry_run = withdrawal_form.get('dry_run') in ('1', 'true', 'yes')
        refresh_archive()
        with lock:
            locked_result = _locked_withdraw(dry_run, withdrawal_form)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        run, run_id = locked_result[1]
        return jsonify({'ok': True, 'run': run_id, 'withdrawn': run['withdrawn']})
