"""Community HTTP endpoints; registered by the executable archivist entrypoint."""
import json
import re
import shutil
import time
from flask import jsonify, request
import selfimport
from settings import now_iso, ACT_NOTES_MAX, ARCHIVE, DISCOURSE_KEY, DISCOURSE_URL, DUMPS_DIR, SITE_URL, THUMB_FETCH_BASE
from webutil import fail
from gitstore import checkout_branch, commit_push, find_run, lock, refresh_archive
from notify import member_md, notify_discord
from records import already_covers, append_role_event, case_derived_status, ensure_member, expert_covers, held_roles, is_committee, is_editor, is_founder, is_site_expert, load_claims, load_experts, log_deletion, may_decide_claims, next_report_id, save_claims, scope_covers, scope_exists, sync_status
from forumapi import close_announce_topic, committee_size, count_votes, votes_cast, forum_account_exists, member_email_masked, publish_group, publish_roles, read_committee_poll, send_pm, sync_expert_group, topics_for_imported, unlock_forum_username


def register(app, *, ANNUL_WORDS, GRANT_WORDS, REPORT_KINDS, _deletion_gate, _import_identity, _refresh_dumps, act_common, auth_precheck, pace_gate, request_identity):
    """Attach the community endpoints with explicit request dependencies."""

    @app.post('/api/run/delete')
    def run_delete():
        """An expert deletes a movie outright: tests, spam, non-TAS, mistakes.

        This is the fast lane beside withdrawal (which keeps a tombstone) and
        all-author erasure (Terms 4.1). It exists for things that were never
        really works; the reason is public and permanent even though the run is
        neither.

        Who: an expert covering the run's game (`key` plus `expert`)
        Reads: form fields run, reason (8 to 500 chars), dry_run
        Answers: {ok, deleted, note}; dry_run: {ok, dry_run, would_delete, game}
        """
        deletion_form = request.form
        dry_run = deletion_form.get('dry_run') in ('1', 'true', 'yes')
        refresh_archive()
        with lock:
            auth_error = auth_precheck(deletion_form)
            if auth_error:
                return auth_error
            actor, reason, error = _deletion_gate(deletion_form)
            if error:
                return error
            run_id = (deletion_form.get('run') or '').strip()
            run_dir = find_run(run_id) if re.fullmatch(r'M[0-9]+', run_id) else None
            if not run_dir:
                return fail(f'unknown run {run_id}', 404)
            game_key = f'{run_dir.parent.parent.parent.name}/{run_dir.parent.parent.name}'
            if not expert_covers(actor, game_key):
                return fail(f'{actor!r} is not an expert covering {game_key}', 403)
            run = json.loads((run_dir / 'run.json').read_text())
            title = f'{game_key} ({(run.get("category") or {}).get("goal", "?")})'
            if dry_run:
                return jsonify({'ok': True, 'dry_run': True, 'would_delete': run_id,
                                'game': game_key})
            checkout_branch()
            run_dir = find_run(run_id)
            if not run_dir:
                return fail(f'unknown run {run_id}', 404)
            shutil.rmtree(run_dir)
            log_deletion('run', run_id, title, actor, reason)
            ensure_member(actor)
            commit_push(f'Delete {run_id}: by expert {actor}\n\nReason: {reason}\nVia: archivist')
        # the run's own announce topic closes with the reason (member replies
        # stay readable), and Discord hears; both best-effort, after the write
        close_announce_topic((run.get('forum') or {}).get('topicId'),
                             f'This run was deleted from the archive by expert {actor}. '
                             f'Reason, from the public log: {reason}')
        notify_discord(f'\U0001f5d1\ufe0f Run {run_id} ({title}) was deleted by expert '
                       f'**{member_md(actor)}**: {reason}')
        return jsonify({'ok': True, 'deleted': run_id,
                        'note': 'Gone, with your reason in the site log. Withdrawal and '
                                'all-author erasure remain the routes for genuine works.'})


    def _authored_runs_by(target_lower):
        """Find archived works credited to a member before deleting their record."""
        return [run_json_path for run_json_path in ARCHIVE.glob('games/*/*/runs/*/run.json')
                if any(a.get('user', '').lower() == target_lower
                       for a in json.loads(run_json_path.read_text()).get('authors', []))]

    def _member_role_gate(target_roles, actor):
        """Protect Founders and sitting Committee members from improper deletion."""
        if any(role == 'founder' for role, s in target_roles):
            return fail('the Founder cannot be deleted (Governance 2.2.2)', 403)
        if any(role == 'committee' for role, s in target_roles) and not is_founder(actor):
            return fail('a sitting Committee member is deleted by the Founder alone, '
                        'never by fellow Committee members', 403)
        return None

    def _locked_member_delete(deletion_form, dry_run):
        """Process the member delete request under the archive write lock."""
        auth_error = auth_precheck(deletion_form)
        if auth_error:
            return auth_error
        actor, reason, error = _deletion_gate(deletion_form)
        if error:
            return error
        if not is_committee(actor):
            return fail('only the Steering Committee deletes a member', 403)
        target = (deletion_form.get('target') or '').strip()
        if not re.fullmatch(r'[A-Za-z0-9. _-]{2,40}', target):
            return fail('target must be the member being deleted')
        author_file = ARCHIVE / 'authors' / f'{selfimport.slugify(target)}.json'
        if not author_file.exists():
            return fail(f'no member record for {target}', 404)
        if target.lower() == actor.lower():
            return fail('deleting yourself is not a decision to make alone; ask '
                        'another Committee member')
        target_roles = [(role, scope) for (holder, role, scope) in held_roles()
                        if holder == target.lower()]
        # The Committee does not eat itself: a sitting Committee member is the
        # Founder's alone to delete, and the Founder is nobody's (2.2.2).
        role_error = _member_role_gate(target_roles, actor)
        if role_error:
            return role_error
        target_lower = target.lower()
        authored_runs = _authored_runs_by(target_lower)
        if authored_runs:
            return fail(f'{target} authored {len(authored_runs)} archived run(s); a member '
                        f'with works here is removed through withdrawal or erasure, '
                        f'never a record deletion', 409)
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_delete': target})
        checkout_branch()
        if not author_file.exists():
            return fail(f'no member record for {target}', 404)
        # the deletion revokes whatever they held, in the same commit: a
        # deleted member on the roster would be a ghost with authority
        today = time.strftime('%Y-%m-%d', time.gmtime())
        for role, scope in target_roles:
            event = {'user': target, 'role': role, 'action': 'revoked', 'by': actor,
                  'date': today, 'at': now_iso(), 'reason': f'Member deleted. {reason}'}
            if scope:
                event['scope'] = scope
            append_role_event(event)
        author_file.unlink()
        log_deletion('member', target, target, actor, reason)
        commit_push(f'Delete member {target}: by {actor}\n\n'
                    f'Reason: {reason}\nVia: archivist')
        return True, (target, target_roles)

    @app.post('/api/member/delete')
    def member_delete():
        """The Steering Committee deletes a member record: spam accounts, tests.

        Refused while the member holds any role or authored any run: those are
        real entanglements with the community and each has its own procedure.
        Their name in other runs' credits is text and stays.

        Who: a Steering Committee member; a sitting Committee member is the
            Founder's alone to delete, and the Founder is nobody's
        Reads: form fields target (username), reason, dry_run
        Answers: {ok, deleted, roles_revoked}; 409 while the member authored runs
        """
        deletion_form = request.form
        dry_run = deletion_form.get('dry_run') in ('1', 'true', 'yes')
        refresh_archive()
        with lock:
            locked_result = _locked_member_delete(deletion_form, dry_run)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        target, target_roles = locked_result[1]
        for role, scope in target_roles:
            publish_group(role, target, add=False)
        return jsonify({'ok': True, 'deleted': target,
                        'roles_revoked': [role for role, s in target_roles]})


    def _locked_report(dry_run, report_form):
        """Process the report request under the archive write lock."""
        auth_error = auth_precheck(report_form)
        if auth_error:
            return auth_error
        if not dry_run:
            checkout_branch()
        user, error = request_identity(report_form)
        if error:
            return error
        paced = pace_gate(report_form, user, 'report')
        if paced:
            return paced
        run_id = (report_form.get('run') or '').strip()
        run_dir = find_run(run_id) if re.fullmatch(r'M[0-9]+', run_id) else None
        if not run_dir:
            return fail(f'unknown run {run_id}', 404)
        kind = (report_form.get('kind') or '').strip()
        if kind not in REPORT_KINDS:
            return fail(f'kind must be one of: {", ".join(sorted(REPORT_KINDS))}')
        details = (report_form.get('details') or '').strip()
        if len(details) > ACT_NOTES_MAX:
            return fail(f'details exceed {ACT_NOTES_MAX} characters')
        if kind == 'other' and not details:
            return fail("an 'other' report needs details")
        run = json.loads((run_dir / 'run.json').read_text())
        report_entry = {'id': next_report_id(), 'by': user,
               'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(),
               'kind': kind, 'status': 'open'}
        if details:
            report_entry['details'] = details
        run.setdefault('reports', []).append(report_entry)
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_file': report_entry})
        (run_dir / 'run.json').write_text(json.dumps(
            {k: v for k, v in run.items() if not k.startswith('_')}, indent=1))
        ensure_member(user)
        commit_push(f'Report R{report_entry["id"]} on {run_id}: {kind} by {user}\n\nVia: archivist')
        return True, (report_entry, run_id)

    @app.post('/api/report')
    def report():
        """Report a run — public, uniquely identified, addressed by the covering
        expert, permanently listed in the site log.

        Who: any member
        Reads: form fields run, kind (one of REPORT_KINDS), details, dry_run
        Answers: {ok, run, report: 'R<id>', note}; dry_run: {ok, dry_run, would_file}
        """
        report_form = request.form
        dry_run = report_form.get('dry_run') in ('1', 'true', 'yes')
        with lock:
            locked_result = _locked_report(dry_run, report_form)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        report_entry, run_id = locked_result[1]
        return jsonify({'ok': True, 'run': run_id, 'report': f'R{report_entry["id"]}',
                        'note': 'Filed in the open. The covering expert will address it; '
                                'it is permanently listed in the site log.'})


    def _report_id_from(form):
        """Parse the numeric identifier of a report being resolved."""
        try:
            return int(form.get('report') or ''), None
        except ValueError:
            return None, fail('report must be a report id number')

    def _locked_report_resolve(dry_run, resolution_form):
        """Process the report resolve request under the archive write lock."""
        auth_error = auth_precheck(resolution_form)
        if auth_error:
            return auth_error
        if not dry_run:
            checkout_branch()
        expert, error = request_identity(resolution_form, 'expert')
        if error:
            return error
        run_id = (resolution_form.get('run') or '').strip()
        run_dir = find_run(run_id) if re.fullmatch(r'M[0-9]+', run_id) else None
        if not run_dir:
            return fail(f'unknown run {run_id}', 404)
        run = json.loads((run_dir / 'run.json').read_text())
        if not expert_covers(expert, run['game']):
            return fail(f'{expert!r} is not an expert covering {run["game"]}', 403)
        report_id, report_error = _report_id_from(resolution_form)
        if report_error:
            return report_error
        report_entry = next((x for x in run.get('reports', []) if x['id'] == report_id), None)
        if not report_entry:
            return fail(f'no report R{report_id} on this run', 404)
        if report_entry['status'] != 'open':
            return fail(f'report R{report_id} is already {report_entry["status"]}')
        outcome = (resolution_form.get('outcome') or '').strip()
        if outcome not in ('resolved', 'dismissed'):
            return fail('outcome must be resolved or dismissed')
        resolution = (resolution_form.get('resolution') or '').strip()
        if not resolution:
            return fail('a public resolution text is required; it is logged in the open')
        if len(resolution) > ACT_NOTES_MAX:
            return fail(f'resolution exceeds {ACT_NOTES_MAX} characters')
        report_entry['status'] = outcome
        report_entry['resolvedBy'] = expert
        report_entry['resolvedAt'] = time.strftime('%Y-%m-%d', time.gmtime())
        report_entry['resolution'] = resolution
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_resolve': report_entry})
        (run_dir / 'run.json').write_text(json.dumps(
            {k: v for k, v in run.items() if not k.startswith('_')}, indent=1))
        ensure_member(expert)
        commit_push(f'Report R{report_id} {outcome} on {run_id}: by expert {expert}\n\n'
                    f'Resolution: {resolution}\nVia: archivist')
        return True, (outcome, report_id)

    @app.post('/api/report/resolve')
    def report_resolve():
        """The covering expert resolves or dismisses a report — logged in the open.

        Who: an expert covering the run's game
        Reads: form fields run, report (id number), outcome (resolved|dismissed),
            resolution, dry_run
        Answers: {ok, report, status}
        """
        resolution_form = request.form
        dry_run = resolution_form.get('dry_run') in ('1', 'true', 'yes')
        refresh_archive()
        with lock:
            locked_result = _locked_report_resolve(dry_run, resolution_form)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        outcome, report_id = locked_result[1]
        return jsonify({'ok': True, 'report': f'R{report_id}', 'status': outcome})


    def _locked_case_open(case_form, dry_run):
        """Validate and write the case open under the archive lock."""
        auth_error = auth_precheck(case_form)
        if auth_error:
            return auth_error
        if not dry_run:
            checkout_branch()
        act_error, run_dir, run, user = act_common(case_form)
        if act_error:
            return act_error
        reason = (case_form.get('reason') or '').strip()
        if not reason:
            return fail('a dispute needs a reason')
        if len(reason) > ACT_NOTES_MAX:
            return fail(f'reason exceeds {ACT_NOTES_MAX} characters')
        live_verifications = [a for a in run.get('verifications', []) if not a.get('invalidated')]
        if not live_verifications:
            return fail('this run has no live verifications to dispute')
        if any(c.get('status') == 'open' for c in run.get('cases', [])):
            return fail('this run already has an open case')
        case = {'id': max([c['id'] for c in run.get('cases', [])] + [0]) + 1,
                'openedBy': user,
                'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(),
                'reason': reason,
                'verifiers': [a['user'] for a in live_verifications],
                'reaffirmations': [],
                'status': 'open'}
        run.setdefault('cases', []).append(case)
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_open': case})
        (run_dir / 'run.json').write_text(json.dumps(
            {k: v for k, v in run.items() if not k.startswith('_')}, indent=1))
        ensure_member(user)
        commit_push(f'Case {case["id"]} opened on {run["id"]}: by {user}\n\nVia: archivist')
        return True, (case, run)

    @app.post('/api/case/open')
    def case_open():
        """A dispute opens a case — never auto-disqualifies. The run's verifiers
        (snapshotted now) are asked to reaffirm.

        Who: a member who is not one of the run's authors
        Reads: form fields run, reason, notes, dry_run
        Answers: {ok, run, case, verifiersAsked}; dry_run: {ok, dry_run, would_open}
        """
        case_form = request.form
        dry_run = case_form.get('dry_run') in ('1', 'true', 'yes')
        with lock:
            locked_result = _locked_case_open(case_form, dry_run)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        case, run = locked_result[1]
        return jsonify({'ok': True, 'run': run['id'], 'case': case['id'],
                        'verifiersAsked': case['verifiers']})


    def _apply_case_vote(run, case, user, reaffirm, today, case_id):
        """Update withdrawn verifications and resolve the snapshotted case."""
        if not reaffirm:
            for verification in run.get('verifications', []):
                if verification['user'].lower() == user.lower() and not verification.get('invalidated'):
                    verification['invalidated'] = {'by': user, 'date': today, 'at': now_iso(),
                                        'reason': f'withdrew during case {case_id}'}
        case['status'] = case_derived_status(case)
        if case['status'] != 'open':
            case['resolvedAt'] = today
        if case['status'] == 'upheld':
            # shortfall upholds the dispute: the run returns to pending —
            # every snapshot verification is invalidated; the run returns to
            # pending and OTHER members may verify it (each member has one
            # verification per run, spent whether or not it survived)
            snapshot = {u.lower() for u in case['verifiers']}
            for verification in run.get('verifications', []):
                if verification['user'].lower() in snapshot and not verification.get('invalidated'):
                    verification['invalidated'] = {'by': 'case', 'date': today, 'at': now_iso(),
                                        'reason': f'case {case_id} upheld'}
        sync_status(run)

    def _case_vote_id(vote_form):
        """Read the case number named by a verifier's vote."""
        try:
            return int(vote_form.get('case') or ''), None
        except ValueError:
            return None, fail('case must be a case id number')

    def _locked_case_vote(dry_run, vote_form):
        """Process the case vote request under the archive write lock."""
        auth_error = auth_precheck(vote_form)
        if auth_error:
            return auth_error
        if not dry_run:
            checkout_branch()
        act_error, run_dir, run, user = act_common(vote_form)
        if act_error:
            return act_error
        case_id, case_error = _case_vote_id(vote_form)
        if case_error:
            return case_error
        case = next((c for c in run.get('cases', []) if c['id'] == case_id), None)
        if not case:
            return fail(f'no case {case_id} on this run', 404)
        if case['status'] != 'open':
            return fail(f'case {case_id} is already {case["status"]}')
        if user.lower() not in {u.lower() for u in case['verifiers']}:
            return fail('only the verifiers asked at case-open time may vote')
        if user.lower() in {v['user'].lower() for v in case.get('reaffirmations', [])}:
            return fail('you have already voted on this case')
        reaffirm = vote_form.get('reaffirm') in ('1', 'true', 'yes')
        today = time.strftime('%Y-%m-%d', time.gmtime())
        vote = {'user': user, 'date': today, 'at': now_iso(), 'reaffirm': reaffirm}
        if (vote_form.get('notes') or '').strip():
            vote['notes'] = vote_form.get('notes').strip()
        case.setdefault('reaffirmations', []).append(vote)
        _apply_case_vote(run, case, user, reaffirm, today, case_id)
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_vote': vote,
                            'case_status': case['status'], 'status': run['status']})
        (run_dir / 'run.json').write_text(json.dumps(
            {k: v for k, v in run.items() if not k.startswith('_')}, indent=1))
        ensure_member(user)
        commit_push(f'Case {case_id} vote on {run["id"]}: '
                    f'{"reaffirmed" if reaffirm else "withdrawn"} by {user}\n\nVia: archivist')
        return True, (case, case_id, run)

    @app.post('/api/case/vote')
    def case_vote():
        """A snapshotted verifier reaffirms (or withdraws) their verification.

        Who: a verifier snapshotted on the case when it opened
        Reads: form fields run, case (id number), reaffirm, notes, dry_run
        Answers: {ok, run, case, case_status, status}
        """
        vote_form = request.form
        dry_run = vote_form.get('dry_run') in ('1', 'true', 'yes')
        with lock:
            locked_result = _locked_case_vote(dry_run, vote_form)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        case, case_id, run = locked_result[1]
        return jsonify({'ok': True, 'run': run['id'], 'case': case_id,
                        'case_status': case['status'], 'status': run['status']})


    def _locked_expert_appoint(appointer, dry_run, reason, scope, user):
        """Process the expert appoint request under the archive write lock."""
        if not dry_run:
            checkout_branch()
        roster = load_experts()
        appointer_scopes = [e for e in roster if e['user'].lower() == appointer.lower()]
        # Two doors in (Governance 2.5.3 and 2.5.6): any single Committee
        # member may appoint an expert at any scope, the whole site included,
        # and an expert appoints downward into scopes their own scope covers.
        # Equal scope still does not qualify on the expert door, or an expert
        # could clone themselves without anybody wider agreeing; a Committee
        # seat is the wider agreement.
        if not (is_committee(appointer)
                or any(scope_covers(e['scope'], scope) for e in appointer_scopes)):
            return fail(f'{appointer} holds no scope that covers {scope} and no '
                        f'Committee seat; appointment runs downward, or from the '
                        f'Committee (Governance 2.5.3)', 403)
        if any(e['user'].lower() == user.lower() and e['scope'] == scope
               for e in roster):
            return fail(f'{user} already holds {scope}', 409)
        held_already = already_covers(user, scope)
        if held_already:
            return fail(f'{user} already speaks for {scope} through their '
                        f'{held_already} scope; a narrower appointment would add '
                        f'nothing', 409)
        if forum_account_exists(user) is False:
            return fail(f'no forum account named {user}; they need one before they '
                        f'can act as an expert', 404)
        entry = {'user': user, 'role': 'expert', 'scope': scope, 'action': 'granted',
                 'by': appointer, 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(),
                 'reason': reason}
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_append': entry})
        append_role_event(entry)
        ensure_member(user)
        commit_push(f'Appoint {user} as expert for {scope}\n\n'
                    f'By: {appointer}\nReason: {reason}\nVia: archivist')
        return True, ()

    @app.post('/api/expert/appoint')
    def expert_appoint():
        """An expert appoints another, downward and in the open.

        Who: a Committee member, or an expert whose own scope covers the target
            scope (`key` plus `expert`)
        Reads: form fields user, scope, reason (8 to 500 chars), dry_run
        Answers: {ok, user, scope, by, forum, note}
        """
        appointment_form = request.form
        appointer, error = request_identity(appointment_form, 'expert')
        if error:
            return error
        user = (appointment_form.get('user') or '').strip()
        scope = (appointment_form.get('scope') or '').strip()
        reason = (appointment_form.get('reason') or '').strip()
        if not re.fullmatch(r'[A-Za-z0-9. _-]{2,40}', user):
            return fail('user must be the forum account being appointed')
        if not scope_exists(scope):
            return fail(f'no such scope: {scope!r} names no game, system or group here')
        if len(reason) < 8:
            return fail('say why, publicly: an appointment is authority over other '
                        "people's work")
        if len(reason) > 500:
            return fail('reason must be under 500 characters')
        dry_run = appointment_form.get('dry_run') in ('1', 'true', 'yes')

        refresh_archive()
        with lock:
            locked_result = _locked_expert_appoint(appointer, dry_run, reason, scope, user)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        note = sync_expert_group(user, add=True)
        return jsonify({'ok': True, 'user': user, 'scope': scope, 'by': appointer,
                        'forum': note,
                        'note': 'The appointment is public: it shows as a badge on the '
                                'members list and in the role log on their own page, '
                                'with your name and your reason.'})


    @app.post('/api/editor/appoint')
    def editor_appoint():
        """A single Committee seat grants the editor role, in the open.

        The library's shape, nothing else (see is_editor). Unscoped, so there is
        no downward door: only the Committee gives it. Taking it away is a role
        removal like any other: a Committee poll through /api/role/decide.

        Who: a Committee member (`key` plus `expert`)
        Reads: form fields user, reason, dry_run
        Answers: {ok, user, by, note}
        """
        appointment_form = request.form
        appointer, error = request_identity(appointment_form, 'expert')
        if error:
            return error
        user = (appointment_form.get('user') or '').strip()
        reason = (appointment_form.get('reason') or '').strip()
        if not re.fullmatch(r'[A-Za-z0-9. _-]{2,40}', user):
            return fail('user must be the forum account being appointed')
        if len(reason) < 8:
            return fail('say why, publicly: the appointment is published with your name')
        if len(reason) > 500:
            return fail('reason must be under 500 characters')
        dry_run = appointment_form.get('dry_run') in ('1', 'true', 'yes')

        refresh_archive()
        with lock:
            if not dry_run:
                checkout_branch()
            if not is_committee(appointer):
                return fail(f'{appointer} holds no Committee seat; the editor role '
                            f'is the Committee\'s to give', 403)
            if is_editor(user):
                return fail(f'{user} is already an editor', 409)
            if forum_account_exists(user) is False:
                return fail(f'no forum account named {user}; they need one before a '
                            f'role means anything', 404)
            entry = {'user': user, 'role': 'editor', 'action': 'granted',
                     'by': appointer, 'date': time.strftime('%Y-%m-%d', time.gmtime()),
                     'at': now_iso(), 'reason': reason}
            if dry_run:
                return jsonify({'ok': True, 'dry_run': True, 'would_append': entry})
            append_role_event(entry)
            ensure_member(user)
            commit_push(f'Appoint {user} as editor\n\n'
                        f'By: {appointer}\nReason: {reason}\nVia: archivist')
        return jsonify({'ok': True, 'user': user, 'by': appointer,
                        'note': 'The appointment is public: an Editor badge on the '
                                'members list and a line in the role log on their '
                                'page, with your name and your reason.'})


    @app.post('/api/roles/publish')
    @app.post('/api/expert/sync')                    # the name it shipped under
    def roles_publish():
        """Publish archive-owned roles to forum groups, never the reverse.

        Requires a site-wide expert (`key` plus `expert`); accepts dry_run.
        Forum membership cannot grant a role or write to the archive.
        Answers {ok, dry_run, groups, note}.
        """
        publish_form = request.form
        caller, error = request_identity(publish_form, 'expert')
        if error:
            return error
        refresh_archive(0)          # publishing must print the truth, not a cache
        if not is_site_expert(caller):
            return fail('only site-wide experts may publish the roster', 403)
        if not DISCOURSE_KEY:
            return fail('the forum is not configured on this server', 503)
        dry_run = publish_form.get('dry_run') in ('1', 'true', 'yes')
        report = publish_roles(dry=dry_run)
        return jsonify({'ok': True, 'dry_run': dry_run, 'groups': report,
                        'note': 'Membership of these groups is derived from roles.json. '
                                'Editing a group on the forum grants nothing and is '
                                'undone by the next publish.'})


    def _founder_seat_gate(target, action):
        """Require a real change to an existing forum account's committee seat."""
        holds = any(u == target.lower() and r == 'committee'
                    for (u, r, s) in held_roles())
        if action == 'granted' and holds:
            return fail(f'{target} already sits on the Committee', 409)
        if action == 'revoked' and not holds:
            return fail(f'{target} does not sit on the Committee', 404)
        if action == 'granted' and forum_account_exists(target) is False:
            return fail(f'no forum account named {target}; they need one before a '
                        f'seat means anything', 404)
        return None

    def _locked_founder_committee(action, caller, dry_run, reason, target):
        """Process the founder committee request under the archive write lock."""
        if not dry_run:
            checkout_branch()
        seat_error = _founder_seat_gate(target, action)
        if seat_error:
            return seat_error
        entry = {'user': target, 'role': 'committee', 'action': action,
                 'by': caller, 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(),
                 'reason': f'{"Seated" if action == "granted" else "Unseated"} by the '
                           f'Founder. {reason}'}
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_append': entry})
        append_role_event(entry)
        if action == 'granted':
            ensure_member(target)
        commit_push(f'Committee: {target} {action} by the Founder\n\n'
                    f'Reason: {reason}\nVia: archivist')
        return True, ()

    @app.post('/api/founder/committee')
    def founder_committee():
        """The Founder seats and unseats Steering Committee members, directly.

        The Committee's own poll route (/api/role/decide) exists alongside this and
        keeps its thresholds; this is the Founder acting as Founder. Every use is a
        role event with 'founder' on it, public in the site log and on the member's
        page, and the person is told. It is not quiet power, it is fast power.

        Who: the Founder (`key` plus `user`)
        Reads: form fields target, action (granted|revoked), reason, dry_run
        Answers: {ok, target, action, by, forum, told}
        """
        decision_form = request.form
        caller, error = request_identity(decision_form, 'user')
        if error:
            return error
        refresh_archive()
        if not is_founder(caller):
            return fail('only the Founder does this; the Committee route is '
                        '/api/role/decide with a poll', 403)
        target = (decision_form.get('target') or '').strip()
        action = (decision_form.get('action') or '').strip()
        reason = (decision_form.get('reason') or '').strip()
        if not re.fullmatch(r'[A-Za-z0-9. _-]{2,40}', target):
            return fail('target must be the forum account the decision is about')
        if action not in ('granted', 'revoked'):
            return fail('action must be granted or revoked')
        if not (8 <= len(reason) <= 500):
            return fail('say why, publicly: a seat on the Committee is authority over '
                        'the whole place')
        dry_run = decision_form.get('dry_run') in ('1', 'true', 'yes')
        with lock:
            locked_result = _locked_founder_committee(action, caller, dry_run, reason, target)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        forum_note = publish_group('committee', target, add=(action == 'granted'))
        told = send_pm(
            target,
            f'You were {"seated on" if action == "granted" else "unseated from"} '
            f'the Steering Committee',
            (f'The Founder ({caller}) {"seated you on" if action == "granted" else "unseated you from"} '
             f'the Steering Committee.\n\nReason given: {reason}\n\n'
             f'The decision is public in the site log and on your member page.'))
        return jsonify({'ok': True, 'target': target, 'action': action, 'by': caller,
                        'forum': forum_note, 'told': told})


    def _role_majority_gate(poll, action, role, size):
        """Count a closed Committee poll against its constitutional threshold."""
        words = GRANT_WORDS if action == 'granted' else ANNUL_WORDS
        votes = count_votes(poll, words)
        # Granting is an ordinary decision (Governance 2.3.3, 2.4.1): a simple
        # majority of the votes cast. So is every removal except a Committee
        # seat's (2.3.5): unseating the Committee alone needs a hard majority,
        # two thirds of every sitting member, counted whether they voted or not.
        # Expert annulment keeps its own rule (2.5.4) in its own endpoint.
        # A simple majority is more than half of the votes cast (2.1.1): absence
        # is abstention and there is no quorum. A hard majority is two thirds of
        # every sitting member (2.1.2), counted whether they voted or not.
        cast = votes_cast(poll)
        if action == 'granted' or role != 'committee':
            enough, needed = cast > 0 and votes * 2 > cast, 'a simple majority of the votes cast'
            against = f'{votes} of the {cast} votes cast'
        else:
            enough, needed = votes * 3 >= size * 2, ('a hard majority of the Committee, '
                                                     'two thirds of all sitting members')
            against = f'{votes} of {size} committee members'
        if not enough:
            return None, None, fail(f'{against} went to '
                        f'{"grant" if action == "granted" else "remove"} this role; '
                        f'{needed} is required (Governance '
                        f'{"2.3.3" if action == "granted" else "2.3.5"})', 409)
        return votes, cast, None

    def _role_seat_gate(target, role, action):
        """Refuse redundant role changes and grants to nonexistent forum accounts."""
        holds = any(u == target.lower() and r == role
                    for (u, r, s) in held_roles())
        if action == 'granted' and holds:
            return fail(f'{target} already holds {role}', 409)
        if action == 'revoked' and not holds:
            return fail(f'{target} does not hold {role}', 404)
        if action == 'granted' and forum_account_exists(target) is False:
            return fail(f'no forum account named {target}; they need one before a '
                        f'role means anything', 404)
        return None

    def _role_request_gate(role, action, target, post_id, reason):
        """Validate the role decision target, action and proof post."""
        if role not in ('committee', 'moderator', 'editor'):
            return fail('role must be committee, moderator or editor; an expert scope '
                        'is appointed downward instead, and annulled by /api/expert/annul')
        if action not in ('granted', 'revoked'):
            return fail('action must be granted or revoked')
        if not re.fullmatch(r'[A-Za-z0-9. _-]{2,40}', target):
            return fail('target must be the forum account the decision is about')
        if not post_id.isdigit():
            return fail('post must be the id of the forum post carrying the Committee poll')
        if reason and len(reason) > 400:
            return fail('reason must be under 400 characters')
        return None

    def _locked_role_decide(action, caller, cast, dry_run, label, proof, reason, role, size, target, votes):
        """Process the role decide request under the archive write lock."""
        if not dry_run:
            checkout_branch()
        seat_error = _role_seat_gate(target, role, action)
        if seat_error:
            return seat_error
        reason_suffix = f' {reason}' if reason else ''
        entry = {'user': target, 'role': role, 'action': action, 'by': 'committee',
                 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(), 'proof': proof,
                 'reason': (f'{"Joined" if action == "granted" else "Left"} {label} '
                            f'by a Committee vote, {votes} of {size}.{reason_suffix}')}
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_append': entry,
                            'votes': votes, 'cast': cast, 'committee': size, 'proof': proof})
        append_role_event(entry)
        if action == 'granted':
            ensure_member(target)
        commit_push(f'Roles: {target} {action} {role} by Committee vote\n\n'
                    f'Vote: {votes} of {size}\nProof: {proof}\n'
                    f'Recorded by: {caller}\nVia: archivist')
        return True, ()

    @app.post('/api/role/decide')
    def role_decide():
        """Record a finished, checkable Committee poll as an archive role event.

        Any member may record it; forum group membership does not decide roles.
        Reads target, role, action, post (proof), reason, dry_run.
        Answers {ok, user, role, action, votes, committee, proof, forum};
        returns 409 when the poll falls short.
        """
        decision_form = request.form
        caller, error = request_identity(decision_form, 'user')
        if error:
            return error
        target = (decision_form.get('target') or '').strip()
        role = (decision_form.get('role') or '').strip()
        action = (decision_form.get('action') or '').strip()
        post_id = (decision_form.get('post') or '').strip()
        reason = (decision_form.get('reason') or '').strip()
        request_error = _role_request_gate(role, action, target, post_id, reason)
        if request_error:
            return request_error
        poll, poll_error = read_committee_poll(post_id)
        if poll_error:
            return fail(poll_error, 409)
        size = committee_size()
        if size <= 0:
            return fail('the Committee is empty in the archive; there is nothing to '
                        'count a majority against', 409)
        votes, cast, majority_error = _role_majority_gate(poll, action, role, size)
        if majority_error is not None:
            return majority_error
        dry_run = decision_form.get('dry_run') in ('1', 'true', 'yes')
        proof = f'{DISCOURSE_URL}/p/{post_id}'
        label = {'committee': 'the Steering Committee', 'moderator': 'moderator',
                 'editor': 'editor'}[role]

        refresh_archive()
        with lock:
            locked_result = _locked_role_decide(action, caller, cast, dry_run, label, proof, reason, role, size, target, votes)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        forum_note = publish_group(role, target, add=(action == 'granted'))
        return jsonify({'ok': True, 'user': target, 'role': role, 'action': action,
                        'votes': votes, 'cast': cast, 'committee': size, 'proof': proof, 'forum': forum_note})


    def _locked_expert_annul(caller, cast, dry_run, for_annul, proof, scope, size, target):
        """Process the expert annul request under the archive write lock."""
        if not dry_run:
            checkout_branch()
        matching_scopes = [appointment for appointment in load_experts()
                if appointment['user'].lower() == target.lower()
                and (not scope or appointment['scope'] == scope)]
        dropped = len(matching_scopes)
        if not dropped:
            return fail(f'{target} holds no such scope', 404)
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_drop': dropped,
                            'votes': for_annul, 'cast': cast, 'committee': size, 'proof': proof})
        today = time.strftime('%Y-%m-%d', time.gmtime())
        for appointment in matching_scopes:
            append_role_event({
                'user': target, 'role': 'expert', 'scope': appointment['scope'],
                'action': 'revoked', 'by': 'committee', 'date': today, 'at': now_iso(), 'proof': proof,
                'reason': f'Annulled by a Committee vote, {for_annul} of {size}.'})
        commit_push(f'Annul: {target} loses {scope or "every scope"}\n\n'
                    f'Committee vote: {for_annul} of {size}\nProof: {proof}\n'
                    f'Applied by: {caller}\nVia: archivist')
        return True, (dropped,)

    @app.post('/api/expert/annul')
    def expert_annul():
        """Apply a Committee decision to annul an appointment (Governance 2.5.4).

        We do not implement voting: the forum already has it. This reads the poll,
        checks it was a genuine Committee decision with a majority of the Committee
        behind it, and then edits the roster. Everything a member needs to check the
        call themselves is in the post it names.

        Who: any member applies it; the Committee poll named by `post` decides
        Reads: form fields target, scope (optional: every scope), post, dry_run
        Answers: {ok, target, dropped, votes, committee, proof, forum}
        """
        annulment_form = request.form
        caller, error = request_identity(annulment_form, 'user')
        if error:
            return error
        target = (annulment_form.get('target') or '').strip()
        scope = (annulment_form.get('scope') or '').strip()
        post_id = (annulment_form.get('post') or '').strip()
        if not target:
            return fail('target must be the expert whose appointment is annulled')
        if not post_id.isdigit():
            return fail('post must be the id of the forum post carrying the Committee poll')
        poll, poll_error = read_committee_poll(post_id)
        if poll_error:
            return fail(poll_error, 409)
        size = committee_size()
        if size <= 0:
            return fail('the committee group is empty or unreadable; nothing to count '
                        'a majority against', 409)
        for_annul = count_votes(poll, ANNUL_WORDS)
        cast = votes_cast(poll)
        # a simple majority: more than half of the votes cast (2.1.1, 2.5.4)
        if cast <= 0 or for_annul * 2 <= cast:
            return fail(f'{for_annul} of the {cast} votes cast went to annul; a simple '
                        f'majority of the votes cast is required', 409)
        dry_run = annulment_form.get('dry_run') in ('1', 'true', 'yes')
        proof = f'{DISCOURSE_URL}/p/{post_id}'
        refresh_archive()
        with lock:
            locked_result = _locked_expert_annul(caller, cast, dry_run, for_annul, proof, scope, size, target)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        dropped, = locked_result[1]
        still = any(appointment['user'].lower() == target.lower() for appointment in load_experts())
        forum_note = sync_expert_group(target, add=False) if not still else 'still an expert elsewhere'
        return jsonify({'ok': True, 'target': target, 'dropped': dropped,
                        'votes': for_annul, 'cast': cast, 'committee': size, 'proof': proof, 'forum': forum_note})


    @app.post('/api/expert/resign')
    def expert_resign():
        """Step down from a scope. Always available, needs nobody's agreement.

        Who: the expert themselves (`key` plus `user`)
        Reads: form fields scope (optional: every scope), dry_run
        Answers: {ok, user, dropped, forum}
        """
        resignation_form = request.form
        user, error = request_identity(resignation_form, 'user')
        if error:
            return error
        scope = (resignation_form.get('scope') or '').strip()
        dry_run = resignation_form.get('dry_run') in ('1', 'true', 'yes')
        refresh_archive()
        with lock:
            if not dry_run:
                checkout_branch()
            matching_scopes = [appointment for appointment in load_experts()
                    if appointment['user'].lower() == user.lower() and (not scope or appointment['scope'] == scope)]
            if not matching_scopes:
                return fail(f'{user} holds no such scope', 404)
            if dry_run:
                return jsonify({'ok': True, 'dry_run': True, 'would_drop': len(matching_scopes)})
            dropped = len(matching_scopes)
            today = time.strftime('%Y-%m-%d', time.gmtime())
            for appointment in matching_scopes:
                append_role_event({'user': user, 'role': 'expert', 'scope': appointment['scope'],
                                   'action': 'revoked', 'by': user, 'date': today, 'at': now_iso(),
                                   'reason': 'Stepped down of their own accord.'})
            commit_push(f'Resign: {user} steps down from '
                        f'{scope or "every scope"}\n\nVia: archivist')
        remaining_roster = load_experts()
        still = any(appointment['user'].lower() == user.lower() for appointment in remaining_roster)
        forum_note = sync_expert_group(user, add=False) if not still else 'still an expert elsewhere'
        return jsonify({'ok': True, 'user': user, 'dropped': dropped, 'forum': forum_note})


    def _locked_claim_request(dry_run, evidence, identity, member):
        """Validate and write the claim request under the archive lock."""
        author_file = ARCHIVE / 'authors' / f'{selfimport.slugify(identity)}.json'
        if author_file.exists():
            author_record = json.loads(author_file.read_text())
            if author_record.get('claimed'):
                return fail(f'{identity} is already claimed by '
                            f'{author_record.get("claimedBy") or "somebody"}', 409)
        claims_doc = load_claims()
        if any(claim['status'] == 'open' and claim['identity'].lower() == identity.lower()
               for claim in claims_doc['requests']):
            return fail(f'a claim for {identity} is already open', 409)
        if any(claim['status'] == 'open' and claim['member'].lower() == member.lower()
               for claim in claims_doc['requests']):
            return fail('you already have a claim open; it has to be answered first', 409)
        entry = {'member': member, 'identity': identity, 'evidence': evidence,
                 'date': time.strftime('%Y-%m-%d', time.gmtime()), 'at': now_iso(), 'status': 'open'}
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_file': entry})
        checkout_branch()
        claims_doc = load_claims()
        claims_doc['requests'].append(entry)
        save_claims(claims_doc)
        ensure_member(member)
        commit_push(f'Claim requested: {member} asks for {identity}\n\n'
                    f'Evidence: {evidence}\nVia: archivist')
        return True, (entry,)

    @app.post('/api/claim/request')
    def claim_request():
        """Ask to be handed a name held for an author elsewhere.

        Who: any member (`key` plus `member`)
        Reads: form fields identity, evidence (8 to 1000 chars), dry_run
        Answers: {ok, request, note}; 409 when the name is claimed or a claim is open
        """
        claim_form = request.form
        member, error = request_identity(claim_form, 'member')
        if error:
            return error
        identity = (claim_form.get('identity') or '').strip()
        evidence = (claim_form.get('evidence') or '').strip()
        if not re.fullmatch(r'[A-Za-z0-9. _-]{2,40}', identity):
            return fail('identity must be the name you are claiming')
        if not (8 <= len(evidence) <= 1000):
            return fail('say what shows the name is yours: a post from that account, a '
                        'channel hosting your encodes, anything somebody can check')
        dry_run = claim_form.get('dry_run') in ('1', 'true', 'yes')
        refresh_archive()
        with lock:
            locked_result = _locked_claim_request(dry_run, evidence, identity, member)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        entry, = locked_result[1]
        return jsonify({'ok': True, 'request': entry,
                        'note': 'Filed. The Steering Committee answers it, and you will '
                                'hear either way. While it is open they can see a masked '
                                'form of the address on your forum account, enough to '
                                'recognise it and not enough to write to you.'})


    @app.post('/api/claim/pending')
    def claim_pending():
        """Open claims, for the people who answer them.

        Carries the requester's forum email, read live and never stored, so the
        Committee can reach somebody about their own claim.

        Who: those who decide claims (the Steering Committee)
        Reads: nothing beyond identity
        Answers: {ok, pending, note}
        """
        request_form = request.form
        caller, error = request_identity(request_form, 'user')
        if error:
            return error
        refresh_archive(0)
        if not may_decide_claims(caller):
            return fail('the Steering Committee answers name claims', 403)
        pending_claims = []
        for claim in load_claims()['requests']:
            if claim['status'] != 'open':
                continue
            pending_claims.append(dict(claim, email=member_email_masked(claim['member'])))
        return jsonify({'ok': True, 'pending': pending_claims,
                        'note': 'The addresses here are masked and read from the forum as '
                                'you ask for them. The whole address is never sent by this '
                                'service, is not in the archive, and never appears on the '
                                'site.'})


    def _approved_claim_record(open_claim, member, today, caller):
        """Bind a Committee-approved identity to its member record."""
        authors_dir = ARCHIVE / 'authors'
        author_file = authors_dir / f'{selfimport.slugify(open_claim["identity"])}.json'
        author_record = json.loads(author_file.read_text()) if author_file.exists() else {
            'username': open_claim['identity']}
        if author_record.get('claimed') and (author_record.get('claimedBy') or '').lower() != member.lower():
            return fail(f'{open_claim["identity"]} is already claimed by '
                        f'{author_record.get("claimedBy")}', 409)
        author_record.update({'username': author_record.get('username') or open_claim['identity'],
                    'claimed': True, 'claimedBy': member, 'claimedAt': today, 'claimedAtTime': now_iso(),
                    'claimMethod': 'committee', 'attestedBy': caller,
                    'attestation': (f'Claim approved by the Steering Committee. '
                                    f'{open_claim["evidence"]}')[:1000]})
        authors_dir.mkdir(exist_ok=True)
        author_file.write_text(json.dumps(author_record, indent=1) + '\n')
        # the claimed record IS this person's member record now; the one
        # their registration name wrote at first login is superseded, and
        # keeping it would list a member who no longer exists
        old_record_file = authors_dir / f'{selfimport.slugify(member)}.json'
        if old_record_file != author_file and old_record_file.exists():
            old_record_file.unlink()
        return None

    def _claim_decision_gate(action, decision_note):
        """Require a public reason when denying an identity claim."""
        if action not in ('approved', 'denied'):
            return fail('action must be approved or denied')
        if action == 'denied' and not (8 <= len(decision_note) <= 500):
            return fail('say why it was denied: the person is told, and they can answer it')
        if len(decision_note) > 500:
            return fail('note must be under 500 characters')
        return None

    def _locked_claim_decide(action, caller, decision_note, dry_run, identity):
        """Process the claim decide request under the archive write lock."""
        claims_doc = load_claims()
        open_claim = next((claim for claim in claims_doc['requests']
                    if claim['status'] == 'open' and claim['identity'].lower() == identity.lower()),
                   None)
        if not open_claim:
            return fail(f'no claim for {identity} is open', 404)
        if open_claim['member'].lower() == caller.lower():
            return fail('you cannot answer your own claim', 403)
        today = time.strftime('%Y-%m-%d', time.gmtime())
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'request': open_claim, 'would': action})
        checkout_branch()
        claims_doc = load_claims()
        open_claim = next((claim for claim in claims_doc['requests']
                    if claim['status'] == 'open' and claim['identity'].lower() == identity.lower()),
                   None)
        if not open_claim:
            return fail(f'no claim for {identity} is open', 404)
        open_claim.update(status=action, decidedBy=caller, decidedAt=today, note=decision_note)
        member = open_claim['member']
        if action == 'approved':
            claim_error = _approved_claim_record(open_claim, member, today, caller)
            if claim_error is not None:
                return claim_error
        save_claims(claims_doc)
        commit_push(f'Claim {action}: {member} for {open_claim["identity"]}\n\n'
                    f'By: {caller}\n' + (f'Note: {decision_note}\n' if decision_note else '')
                    + 'Via: archivist')
        return True, (member, open_claim)

    @app.post('/api/claim/decide')
    def claim_decide():
        """The Committee answers a claim, and the person is told either way.

        Who: those who decide claims, never on their own claim
        Reads: form fields identity, action (approved|denied), note, dry_run
        Answers: {ok, identity, member, action, by, rename, told}
        """
        decision_form = request.form
        caller, error = request_identity(decision_form, 'user')
        if error:
            return error
        refresh_archive()
        if not may_decide_claims(caller):
            return fail('the Steering Committee answers name claims', 403)
        identity = (decision_form.get('identity') or '').strip()
        action = (decision_form.get('action') or '').strip()
        decision_note = (decision_form.get('note') or '').strip()
        decision_error = _claim_decision_gate(action, decision_note)
        if decision_error:
            return decision_error
        dry_run = decision_form.get('dry_run') in ('1', 'true', 'yes')
        with lock:
            locked_result = _locked_claim_decide(action, caller, decision_note, dry_run, identity)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        member, open_claim = locked_result[1]
        renamed, rename_note = (unlock_forum_username(member, open_claim['identity'])
                                if action == 'approved' else (False, 'no rename'))
        told = send_pm(
            member,
            f'Your claim to the name {open_claim["identity"]} was {action}',
            (f'The Steering Committee {action} your claim to **{open_claim["identity"]}**.\n\n'
             + (((f'Your forum account has been renamed and the name is yours. '
                  if renamed else
                  f'The name is yours here. Renaming your forum account to it did not '
                  f'go through, so an admin will do that by hand; nothing else waits '
                  f'on it. ')
                 + f'Your profile '
                   f'now carries an **Import my movies** button for your publications '
                   f'at the site the name comes from, co-authored ones included; '
                   f'importing a co-authored work is your responsibility.\n\n')
                if action == 'approved' else '')
             + (f'Reason given: {decision_note}\n\n' if decision_note else '')
             + f'Answered by {caller}. You can reply to this message if you think this '
               f'is wrong; the decision is recorded in the site log either way.'))
        return jsonify({'ok': True, 'identity': open_claim['identity'], 'member': member,
                        'action': action, 'by': caller, 'renamed': renamed,
                        'rename': rename_note, 'told': told})


    def _locked_claim_attest(dry_run, expert, identity, member, method):
        """Process the claim attest request under the archive write lock."""
        if not dry_run:
            checkout_branch()
        authors_dir = ARCHIVE / 'authors'
        author_file = authors_dir / f'{selfimport.slugify(identity)}.json'
        author_record = json.loads(author_file.read_text()) if author_file.exists() else {'username': identity}
        if author_record.get('claimed') and (author_record.get('claimedBy') or '').lower() != member.lower():
            return fail(f'{identity} is already claimed by {author_record.get("claimedBy")}', 409)
        author_record.update({'username': author_record.get('username') or identity,
                    'claimed': True,
                    'claimedBy': member,
                    'claimedAt': time.strftime('%Y-%m-%d', time.gmtime()),
                    'claimedAtTime': now_iso(),
                    'claimMethod': 'attested',
                    'attestedBy': expert,
                    'attestation': method})
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_write': author_record})
        authors_dir.mkdir(exist_ok=True)
        author_file.write_text(json.dumps(author_record, indent=1) + '\n')
        old_record_file = authors_dir / f'{selfimport.slugify(member)}.json'
        if old_record_file != author_file and old_record_file.exists():
            old_record_file.unlink()
        commit_push(f'Attest {identity}: verified by expert {expert}\n\n'
                    f'Member: {member}\nMethod: {method}\nVia: archivist')
        return True, (author_record,)

    @app.post('/api/claim/attest')
    def claim_attest():
        """Publicly attest an external author's identity without a prior claim.

        A claim-deciding Committee member (`key` plus `expert`) records who and
        how in the author record and site log; external bans do not disqualify.
        Reads member, identity, method (12–1000 chars), dry_run.
        Answers {ok, identity, member, attestedBy, rename, note}.
        """
        attestation_form = request.form
        expert, error = request_identity(attestation_form, 'expert')
        if error:
            return error
        refresh_archive()
        if not may_decide_claims(expert):
            return fail('only the Steering Committee assesses identity', 403)
        member = (attestation_form.get('member') or '').strip()
        identity = (attestation_form.get('identity') or '').strip()
        method = (attestation_form.get('method') or '').strip()
        if not re.fullmatch(r'[A-Za-z0-9. _-]{2,40}', member):
            return fail('member must be the forum account being attested')
        if not re.fullmatch(r'[A-Za-z0-9. _-]{2,40}', identity):
            return fail('identity must be the name being claimed')
        if len(method) < 12:
            return fail('say how you verified it: the reason is public and it is the '
                        'whole point of an attestation')
        if len(method) > 1000:
            return fail('method must be under 1000 characters')
        dry_run = attestation_form.get('dry_run') in ('1', 'true', 'yes')

        with lock:
            locked_result = _locked_claim_attest(dry_run, expert, identity, member, method)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        author_record, = locked_result[1]
        renamed, rename_note = unlock_forum_username(member, identity)
        return jsonify({'ok': True, 'identity': author_record['username'], 'member': member,
                        'attestedBy': expert, 'renamed': renamed, 'rename': rename_note,
                        'note': 'The attestation is public: it names you as the expert who '
                                'made the call, and the site log carries it. The '
                                'member can now import their movies from their '
                                'own profile.'})


    @app.post('/api/import/scan')
    def import_scan():
        """What of my TASVideos catalog is not in the archive yet?

        Who: a logged-in member who has claimed the identity (session only)
        Reads: nothing
        Answers: {ok, user} plus selfimport.scan's result
        """
        user, error = _import_identity()
        if error:
            return error
        if not (DUMPS_DIR / 'metadata' / 'publications.json').exists():
            return fail('the tasvideos backup is not available on this server; try later', 503)
        _refresh_dumps()
        with lock:
            checkout_branch()
            return jsonify({'ok': True, 'user': user,
                            **selfimport.scan(DUMPS_DIR, ARCHIVE, user)})


    @app.post('/api/import/run')
    def import_run():
        """Import the next batch of my pending TASVideos publications. Call
        repeatedly until remaining is 0; each batch is one archive commit.

        Who: a logged-in member who has claimed the identity (session only)
        Reads: form field select (publication ids)
        Answers: {ok, user} plus selfimport.import_batch's result
        """
        user, error = _import_identity()
        if error:
            return error
        if not (DUMPS_DIR / 'metadata' / 'publications.json').exists():
            return fail('the tasvideos backup is not available on this server; try later', 503)
        _refresh_dumps()
        # nothing is imported that was not picked by id: the member chooses which
        # of their movies come over, co-authored ones included, and picking a
        # co-authored one is the act that carries the responsibility for it
        raw = (request.form.get('select') or '').replace(',', ' ').replace('M', ' ')
        try:
            select = sorted({int(s) for s in raw.split() if s.strip()})
        except ValueError:
            return fail('select must be publication ids, like "910001 910002"')
        if not select:
            return fail('select which of your movies to import; nothing is imported '
                        'unpicked')
        if len(select) > 500:
            return fail('that is more than one member has ever published; check the list')
        with lock:
            checkout_branch()
            result = selfimport.import_batch(DUMPS_DIR, ARCHIVE, user,
                                          time.strftime('%Y-%m-%d', time.gmtime()),
                                          THUMB_FETCH_BASE, limit=6, select=select)
            if result['imported']:
                result['topics'] = topics_for_imported(ARCHIVE, result['imported'])
                commit_push(f"Self-import for {user}: {', '.join(result['imported'])}")
        if result['imported']:
            # one line per batch, not per movie: a large import in six-movie
            # batches would otherwise flood the channel. The first few ids carry
            # the links; the source stays implicit, each run page naming its own.
            imported_ids = result['imported']
            shown = ', '.join(f'[{i}](<{SITE_URL}/runs/{i}/>)' for i in imported_ids[:3])
            more = f' +{len(imported_ids) - 3} more' if len(imported_ids) > 3 else ''
            word = 'movie' if len(imported_ids) == 1 else f'{len(imported_ids)} movies'
            notify_discord(f'\U0001f4e5 **{member_md(user)}** imported {word}: {shown}{more}',
                           wait_for=f'{SITE_URL}/runs/{imported_ids[0]}/')
        return jsonify({'ok': True, 'user': user, **result})
