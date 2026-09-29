"""Library HTTP endpoints; registered by the executable archivist entrypoint."""
import json
import re
import shutil
import time
from flask import jsonify, request
from settings import now_iso, ARCHIVE, SITE_URL, slugify
from webutil import fail
from gitstore import checkout_branch, commit_push, lock, refresh_archive
from notify import member_md, notify_discord
from records import append_role_event, covers_group, ensure_member, expert_covers, expert_covers_system, held_roles, is_committee, is_editor, is_site_expert, load_emulators, load_experts, load_groups, log_deletion, log_edit, save_emulators, save_groups
from forumapi import close_announce_topic, ensure_game_topic


def register(app, *, GAME_PROPERTY_FIELDS, SYSTEM_FPS_DEFAULT, SYSTEM_FPS_MAX, SYSTEM_FPS_MIN, SYSTEM_KEY, SYSTEM_KEY_MAX, _category_gate, _deletion_gate, auth_precheck, load_systems, notify_edit, option_in, pace_gate, parse_game_property, parse_metric_defs, request_identity, system_key_for):
    """Attach the library endpoints with explicit request dependencies."""

    @app.get('/api/categories')
    def categories_of_game():
        """A game's category definitions, fresh from the checkout (refreshed at
        most 20 s old). The submit form asks here instead of the raw-file CDN,
        whose 5-minute cache showed a renamed category under its old label.

        Who: anybody
        Reads: query arg game (system/slug)
        Answers: the categories.json document with ok: true, Cache-Control: no-store
        """
        game_key = (request.args.get('game') or '').strip()
        if not re.fullmatch(r'[a-z0-9-]+/[a-z0-9-]+', game_key):
            return fail('game must be system/slug')
        refresh_archive()
        categories_file = ARCHIVE / 'games' / game_key / 'categories.json'
        if not categories_file.exists():
            return fail(f'unknown game {game_key}', 404)
        resp = jsonify({'ok': True, **json.loads(categories_file.read_text())})
        resp.headers['Cache-Control'] = 'no-store'
        return resp


    def _create_subcategory(categories, parent_key, metric_defs, option_key, game_key, dry_run, label, rule, categories_file, expert, category_form):
        """Add a subcategory and move existing category runs in the same commit."""
        parent = option_in(categories, parent_key)
        if not parent:
            return fail(f'{game_key} defines no category {parent_key!r}', 404)
        if metric_defs:
            return fail('a subcategory ranks by its category\'s metrics; define those on the category')
        subs = parent.setdefault('subcategories', [])
        if any(s['key'] == option_key for s in subs):
            return fail(f'{option_key!r} already exists in {parent["label"]}', 409)
        # the first subcategory changes what the category's runs need: a
        # run already there would then name none, so the category must
        # be empty, or the new subcategory must take them all
        holders = [rp for rp in (ARCHIVE / 'games' / game_key / 'runs').glob('*/run.json')
                   if (json.loads(rp.read_text()).get('category') or {}).get('goal') == parent_key
                   and not (json.loads(rp.read_text()).get('category') or {}).get('sub')]
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'key': option_key, 'parent': parent_key,
                            'runs_moved': len(holders) if not subs else 0})
        moved = 0
        if not subs and holders:
            for rp in holders:
                run_doc = json.loads(rp.read_text())
                run_doc['category']['sub'] = option_key
                rp.write_text(json.dumps(run_doc, indent=1) + '\n')
                moved += 1
        subs.append({'key': option_key, 'label': label, **({'rule': rule} if rule else {})})
        categories_file.write_text(json.dumps(categories, indent=1) + '\n')
        log_edit('category', f'{game_key}:{parent_key}/{option_key}', 'added', '', label, expert,
                 (category_form.get('reason') or 'Created it.').strip()[:500])
        ensure_member(expert)
        commit_push(f'Subcategory add {game_key}:{parent_key}/{option_key}: by {expert}\n\n'
                    f'Label: {label}\nRuns moved into it: {moved}\nVia: archivist')
        return jsonify({'ok': True, 'game': game_key, 'key': option_key, 'parent': parent_key,
                        'label': label, 'runs_moved': moved})

    def _category_text_gate(label, rule, parent):
        """Require a short label and an appropriately sized category rule."""
        if not (1 <= len(label) <= 80):
            return fail('a label fits in 80 characters')
        if not (1 <= len(rule) <= 2000) and not (parent and len(rule) <= 2000):
            return fail('a rule fits in 2000 characters of markdown; it is what a '
                        'verifier holds a run to')
        return None

    def _new_category_dimension(categories, option_key, game_key):
        """Find the goal dimension and refuse a duplicate option address."""
        goal_dimension = next((d for d in categories['dimensions'] if d['key'] == 'goal'),
                              categories['dimensions'][0] if categories['dimensions'] else None)
        if goal_dimension is None:
            categories['dimensions'] = [{'key': 'goal', 'name': 'Category', 'options': []}]
            goal_dimension = categories['dimensions'][0]
        if any(o['key'] == option_key for d in categories['dimensions'] for o in d['options']):
            return None, fail(f'{option_key!r} already exists on this game', 409)
        return goal_dimension, None

    def _locked_category_add(category_form, dry_run):
        """Process the category add request under the archive write lock."""
        auth_error = auth_precheck(category_form)
        if auth_error:
            return auth_error
        if not dry_run:
            checkout_branch()
        expert, game_key, categories_file, categories, error = _category_gate(category_form, need_expert=False)
        if error:
            return error
        paced = pace_gate(category_form, expert, 'create')
        if paced:
            return paced
        metric_defs, metric_error = parse_metric_defs(category_form.get('metrics'))
        if metric_error:
            return fail(metric_error)
        label = (category_form.get('label') or '').strip()
        rule = (category_form.get('rule') or '').strip()
        text_error = _category_text_gate(label, rule, category_form.get('parent'))
        if text_error:
            return text_error
        # 'key' is the submitter-key auth field; the option key travels as
        # option_key (the same collision removal/decide once had)
        option_key = slugify((category_form.get('option_key') or label).strip())
        if not option_key:
            return fail('the label yields an empty key')
        if option_key == 'unclassified':
            return fail('unclassified is reserved: every game already has it')
        parent_key = (category_form.get('parent') or '').strip()
        if parent_key:
            return _create_subcategory(categories, parent_key, metric_defs, option_key,
                                       game_key, dry_run, label, rule, categories_file,
                                       expert, category_form)
        goal_dimension, option_error = _new_category_dimension(categories, option_key, game_key)
        if option_error:
            return option_error
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'key': option_key})
        goal_dimension['options'].append({'key': option_key, 'label': label, 'rule': rule,
                               **({'metrics': metric_defs} if metric_defs else {})})
        categories_file.write_text(json.dumps(categories, indent=1) + '\n')
        log_edit('category', f'{game_key}:{option_key}', 'added', '', label, expert,
                 (category_form.get('reason') or 'Created it.').strip()[:500])
        ensure_member(expert)
        game_title = json.loads((ARCHIVE / 'games' / game_key / 'game.json')
                            .read_text()).get('title', game_key)
        commit_push(f'Category add {game_key}:{option_key}: by {expert}\n\n'
                    f'Label: {label}\nVia: archivist')
        notify_discord(f'\U0001f5c2\ufe0f **{member_md(expert)}** created the category '
                       f'[{label}](<{SITE_URL}/games/{game_key}/>) in '
                       f'[[{game_key.split("/")[0].upper()}] {game_title}]'
                       f'(<{SITE_URL}/games/{game_key}/>)',
                       wait_for=f'{SITE_URL}/games/{game_key}/')
        return True, (game_key, label, option_key)

    @app.post('/api/category/add')
    def category_add():
        """Any member adds a category (creation is everybody's; only experts
        edit what exists). The creator defines its metrics; the edit log carries
        the act.

        Who: any member (session, or `key` plus `user`)
        Reads: form fields parent (optional: the category this one becomes a
            subcategory of; then metrics are refused and the rule may be empty), game, label, rule, option_key, metrics (JSON array),
            reason, dry_run
        Answers: {ok, game, key, label}; dry_run: {ok, dry_run, key}; 409 when the
            key exists
        """
        category_form = request.form
        dry_run = category_form.get('dry_run') in ('1', 'true', 'yes')
        refresh_archive()
        with lock:
            locked_result = _locked_category_add(category_form, dry_run)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        game_key, label, option_key = locked_result[1]
        return jsonify({'ok': True, 'game': game_key, 'key': option_key, 'label': label})


    def _category_reorder_items(categories, option_key, game_key):
        """Select goal options or a named category's subcategory options."""
        if option_key:
            option = option_in(categories, option_key)
            if not option:
                return None, fail(f'{game_key} defines no category {option_key!r}', 404)
            items = option.get('subcategories') or []
            return (items, option, None, f'{option_key} subcategories'), None
        dimension = next((d for d in categories['dimensions'] if d['key'] == 'goal'),
                         categories['dimensions'][0] if categories['dimensions'] else None)
        if dimension is None:
            return None, fail('this game has no categories yet')
        return (dimension['options'], None, dimension, 'categories'), None

    def _locked_category_reorder(dry_run, form):
        """Process the category reorder request under the archive write lock."""
        auth_error = auth_precheck(form)
        if auth_error:
            return auth_error
        if not dry_run:
            checkout_branch()
        expert, game_key, categories_file, categories, error = _category_gate(form)
        if error:
            return error
        wanted = [k.strip() for k in (form.get('order') or '').split(',') if k.strip()]
        option_key = (form.get('option') or '').strip()
        target, error = _category_reorder_items(categories, option_key, game_key)
        if error is not None:
            return error
        items, option, dimension, what = target
        have = [x['key'] for x in items]
        if sorted(wanted) != sorted(have):
            return fail(f'order must list exactly the {what}: {", ".join(have)}')
        if wanted == have:
            return fail('that is already the order')
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'order': wanted})
        by_key = {x['key']: x for x in items}
        reordered = [by_key[k] for k in wanted]
        if option_key:
            option['subcategories'] = reordered
        else:
            dimension['options'] = reordered
        categories_file.write_text(json.dumps(categories, indent=1) + '\n')
        log_edit('category', f'{game_key}:{option_key or "*"}', 'order', ', '.join(have), ', '.join(wanted),
                 expert, (form.get('reason') or 'Reordered.').strip()[:500])
        ensure_member(expert)
        commit_push(f'Category order {game_key}{":" + option_key if option_key else ""}: by {expert}\n\n'
                    f'Order: {", ".join(wanted)}\nVia: archivist')
        return True, (game_key, wanted)

    @app.post('/api/category/reorder')
    def category_reorder():
        """Put a game's categories, or one category's subcategories, in the order
        given: the popular ones first, at the left of every selector. Pure
        order; nothing else about them changes.

        Who: an expert covering the game, or an editor (`key` plus `expert`)
        Reads: form fields game, order (comma-separated keys, the whole set),
            option (optional: reorder that category's subcategories instead),
            reason (optional), dry_run
        Answers: {ok, game, order}; 400 when the keys are not exactly the set
        """
        form = request.form
        dry_run = form.get('dry_run') in ('1', 'true', 'yes')
        refresh_archive()
        with lock:
            locked_result = _locked_category_reorder(dry_run, form)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        game_key, wanted = locked_result[1]
        return jsonify({'ok': True, 'game': game_key, 'order': wanted})


    def _delete_subcategory(sub_key, option, game_key, option_key, dry_run, categories_file, categories, expert, category_form):
        """Remove one subcategory and release last-subcategory runs in a logged commit."""
        subs = option.get('subcategories') or []
        sub = next((s for s in subs if s['key'] == sub_key), None)
        if not sub:
            return fail(f'{option["label"]} has no subcategory {sub_key!r}', 404)
        holders = [rp for rp in (ARCHIVE / 'games' / game_key / 'runs').glob('*/run.json')
                   if (json.loads(rp.read_text()).get('category') or {}).get('goal') == option_key
                   and (json.loads(rp.read_text()).get('category') or {}).get('sub') == sub_key]
        last = len(subs) == 1
        # the last subcategory dissolves the level: its runs stay in the
        # category, naming none (the mirror of the first one taking them);
        # any other subcategory must be empty to go
        if holders and not last:
            return fail(f'{sub_key!r} holds {len(holders)} run(s); a subcategory with runs '
                        f'in it is their home, not clutter', 409)
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'runs_released': len(holders) if last else 0})
        released = 0
        if last:
            for rp in holders:
                run_doc = json.loads(rp.read_text())
                run_doc['category'].pop('sub', None)
                rp.write_text(json.dumps(run_doc, indent=1) + '\n')
                released += 1
        option['subcategories'] = [s for s in subs if s['key'] != sub_key]
        if not option['subcategories']:
            option.pop('subcategories')
        categories_file.write_text(json.dumps(categories, indent=1) + '\n')
        log_edit('category', f'{game_key}:{option_key}/{sub_key}', 'removed', sub.get('label', sub_key),
                 '', expert, (category_form.get('reason') or 'Removed unused by a covering expert.').strip()[:500])
        ensure_member(expert)
        commit_push(f'Subcategory remove {game_key}:{option_key}/{sub_key}: by expert {expert}\n\n'
                    f'Runs released into the category: {released}\nVia: archivist')
        return jsonify({'ok': True, 'game': game_key, 'removed': f'{option_key}/{sub_key}',
                        'runs_released': released})

    def _locked_category_delete(category_form, dry_run):
        """Process the category delete request under the archive write lock."""
        auth_error = auth_precheck(category_form)
        if auth_error:
            return auth_error
        if not dry_run:
            checkout_branch()
        expert, game_key, categories_file, categories, error = _category_gate(category_form)
        if error:
            return error
        option_key = (category_form.get('option') or '').strip()
        option = next((option for dimension in categories['dimensions'] for option in dimension['options']
                    if option['key'] == option_key), None)
        if not option:
            return fail(f'{game_key} defines no category {option_key!r}', 404)
        sub_key = (category_form.get('sub') or '').strip()
        if sub_key:
            return _delete_subcategory(sub_key, option, game_key, option_key, dry_run,
                                       categories_file, categories, expert, category_form)
        runs_in_category = [json.loads(run_json_path.read_text())['id']
                 for run_json_path in (ARCHIVE / 'games' / game_key / 'runs').glob('*/run.json')
                 if (json.loads(run_json_path.read_text()).get('category') or {}).get('goal') == option_key]
        if runs_in_category:
            return fail(f'{option_key!r} holds {len(runs_in_category)} run(s) ({", ".join(runs_in_category[:4])}'
                        f'{"…" if len(runs_in_category) > 4 else ""}); a category with runs '
                        f'in it is their home, not clutter', 409)
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True})
        for dimension in categories['dimensions']:
            dimension['options'] = [option for option in dimension['options'] if option['key'] != option_key]
        categories_file.write_text(json.dumps(categories, indent=1) + '\n')
        log_edit('category', f'{game_key}:{option_key}', 'removed', option.get('label', option_key),
                 '', expert,
                 (category_form.get('reason') or 'Removed unused by a covering expert.').strip()[:500])
        ensure_member(expert)
        commit_push(f'Category remove {game_key}:{option_key}: by expert {expert}\n\n'
                    f'Via: archivist')
        return True, (game_key, option_key)

    @app.post('/api/category/delete')
    def category_delete():
        """Remove an option no run has ever used. Anything referenced stays: a
        category with runs in it is the runs' home, not clutter.

        Who: an expert covering the game, or an editor (`key` plus `expert`)
        Reads: form fields game, option, sub (optional: remove that subcategory
            alone; the last one may hold runs, which then stay in the category
            naming none), reason, dry_run
        Answers: {ok, game, removed}; 409 while any run sits in the category
        """
        category_form = request.form
        dry_run = category_form.get('dry_run') in ('1', 'true', 'yes')
        refresh_archive()
        with lock:
            locked_result = _locked_category_delete(category_form, dry_run)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        game_key, option_key = locked_result[1]
        return jsonify({'ok': True, 'game': game_key, 'removed': option_key})


    def _log_deleted_game_runs(run_dirs, game, game_key, actor, reason):
        """Log every deleted run and collect forum topics needing closure."""
        deleted_runs = []
        orphan_topics = []
        for run_dir in run_dirs:
            try:
                run_doc = json.loads((run_dir / 'run.json').read_text())
                run_title = f'{game.get("title", game_key)} ' \
                         f'({(run_doc.get("category") or {}).get("goal", "?")})'
                if (run_doc.get('forum') or {}).get('topicId'):
                    orphan_topics.append((run_dir.name, run_doc['forum']['topicId']))
            except Exception:                                 # noqa: BLE001
                run_title = game.get('title', game_key)
            log_deletion('run', run_dir.name, run_title, actor,
                         f'Its game {game_key} was deleted. {reason}')
            deleted_runs.append(run_dir.name)
        return deleted_runs, orphan_topics

    def _remove_deleted_game_references(game_key, actor, reason):
        """Release the game from groups and revoke scopes over its deleted address."""
        groups_doc = load_groups()
        changed = False
        for group in groups_doc['groups']:
            if game_key in group.get('games', []):
                group['games'] = [g for g in group['games'] if g != game_key]
                changed = True
        if changed:
            save_groups(groups_doc)
        today = time.strftime('%Y-%m-%d', time.gmtime())
        for (holder, role, scope), event in list(held_roles().items()):
            if role == 'expert' and scope == game_key:
                append_role_event({'user': event['user'], 'role': 'expert',
                                   'scope': scope, 'action': 'revoked', 'by': actor,
                                   'date': today, 'at': now_iso(),
                                   'reason': f'The game was deleted. {reason}'})

    def _locked_game_delete(deletion_form, dry_run):
        """Process the game delete request under the archive write lock."""
        auth_error = auth_precheck(deletion_form)
        if auth_error:
            return auth_error
        actor, reason, error = _deletion_gate(deletion_form)
        if error:
            return error
        game_match = re.fullmatch(r'([a-z0-9-]+)/([a-z0-9-]+)', (deletion_form.get('game') or '').strip())
        if not game_match:
            return fail('game must be system/slug')
        game_key = game_match.group(0)
        system, slug = game_match.groups()
        game_dir = ARCHIVE / 'games' / system / slug
        if not (game_dir / 'game.json').exists():
            return fail(f'unknown game {game_key}', 404)
        if not expert_covers(actor, game_key):
            return fail(f'{actor!r} is not an expert covering {game_key}', 403)
        game = json.loads((game_dir / 'game.json').read_text())
        run_dirs = sorted(run_dir for run_dir in (game_dir / 'runs').glob('M*') if run_dir.is_dir())
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_delete': game_key,
                            'runs_deleted': [run_dir.name for run_dir in run_dirs]})
        checkout_branch()
        game_dir = ARCHIVE / 'games' / system / slug
        if not (game_dir / 'game.json').exists():
            return fail(f'unknown game {game_key}', 404)
        run_dirs = sorted(run_dir for run_dir in (game_dir / 'runs').glob('M*') if run_dir.is_dir())
        deleted_runs, orphan_topics = _log_deleted_game_runs(
            run_dirs, game, game_key, actor, reason)
        shutil.rmtree(game_dir)
        # the game leaves any group it sat in; a group cannot hold a ghost
        _remove_deleted_game_references(game_key, actor, reason)
        log_deletion('game', game_key, game.get('title', game_key), actor, reason)
        ensure_member(actor)
        commit_push(f'Delete game {game_key}: by expert {actor}\n\n'
                    f'Reason: {reason}\n'
                    f'Runs deleted with it: {", ".join(deleted_runs) or "none"}\n'
                    f'Via: archivist')
        return True, (actor, deleted_runs, game, game_key, orphan_topics, reason)

    @app.post('/api/game/delete')
    def game_delete():
        """An expert deletes a game outright, and its runs go with it.

        The use case is a game that should never have been archived: rule
        violations, spam, fabrications. Every deleted run gets its own line in
        deletions.json beside the game's, so the log carries the whole act, and
        git history keeps the bytes. Works that were genuine but mis-homed are
        moved by an expert run edit, never by deletion.

        Who: an expert covering the game
        Reads: form fields game (system/slug), reason, dry_run
        Answers: {ok, deleted, runs_deleted}
        """
        deletion_form = request.form
        dry_run = deletion_form.get('dry_run') in ('1', 'true', 'yes')
        refresh_archive()
        with lock:
            locked_result = _locked_game_delete(deletion_form, dry_run)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        actor, deleted_runs, game, game_key, orphan_topics, reason = locked_result[1]
        # each deleted run's announce topic closes with the reason; Discord
        # hears once for the whole act; all best-effort, after the write
        for orphan_run_id, orphan_topic in orphan_topics:
            close_announce_topic(orphan_topic,
                                 f'This run was deleted from the archive with its game '
                                 f'{game_key}, by expert {actor}. Reason, from the public '
                                 f'log: {reason}')
        notify_discord(f'\U0001f5d1\ufe0f Game {game.get("title", game_key)} ({game_key}) was '
                       f'deleted by expert **{member_md(actor)}** with '
                       f'{len(deleted_runs)} run(s): {reason}')
        return jsonify({'ok': True, 'deleted': game_key, 'runs_deleted': deleted_runs})


    def _locked_group_delete(deletion_form, dry_run):
        """Process the group delete request under the archive write lock."""
        auth_error = auth_precheck(deletion_form)
        if auth_error:
            return auth_error
        actor, reason, error = _deletion_gate(deletion_form)
        if error:
            return error
        group_key = (deletion_form.get('group') or '').strip().lower()
        groups_doc = load_groups()
        group = next((g for g in groups_doc['groups'] if g['key'] == group_key), None)
        if not group:
            return fail(f'no group with the key {group_key!r}', 404)
        if not covers_group(actor, group) and not is_editor(actor):
            return fail(f'{actor} holds no scope covering the {group["title"]} group', 403)
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_delete': group_key,
                            'released': group.get('games', [])})
        checkout_branch()
        groups_doc = load_groups()
        group = next((g for g in groups_doc['groups'] if g['key'] == group_key), None)
        if not group:
            return fail(f'no group with the key {group_key!r}', 404)
        released = group.get('games', [])
        groups_doc['groups'] = [g for g in groups_doc['groups'] if g['key'] != group_key]
        save_groups(groups_doc)
        today = time.strftime('%Y-%m-%d', time.gmtime())
        for (holder, role, scope), event in list(held_roles().items()):
            if role == 'expert' and scope == f'group:{group_key}':
                append_role_event({'user': event['user'], 'role': 'expert',
                                   'scope': scope, 'action': 'revoked', 'by': actor,
                                   'date': today, 'at': now_iso(),
                                   'reason': f'The group was deleted. {reason}'})
        log_deletion('group', group_key, group.get('title', group_key), actor, reason)
        ensure_member(actor)
        commit_push(f'Delete group {group_key}: by expert {actor}\n\n'
                    f'Reason: {reason}\nReleased: {", ".join(released) or "no games"}\n'
                    f'Via: archivist')
        return True, (group_key, released)

    @app.post('/api/group/delete')
    def group_delete():
        """An expert deletes a group outright; its games become ungrouped and the
        derived Unclassified group picks them up at the next build.

        Who: an expert whose scope covers the group, or an editor
        Reads: form fields group (key), reason, dry_run
        Answers: {ok, deleted, released}
        """
        deletion_form = request.form
        dry_run = deletion_form.get('dry_run') in ('1', 'true', 'yes')
        refresh_archive()
        with lock:
            locked_result = _locked_group_delete(deletion_form, dry_run)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        group_key, released = locked_result[1]
        return jsonify({'ok': True, 'deleted': group_key, 'released': released})


    def _new_game_properties(game_form):
        """Validate optional game identity and community-link properties."""
        properties = {}
        for property_field in GAME_PROPERTY_FIELDS:
            property_value, property_error = parse_game_property(
                property_field, game_form.get(property_field))
            if property_error:
                return None, fail(property_error)
            if property_value is not None:
                properties[property_field] = property_value
        return properties, None

    def _save_new_game(system, slug, game_key, game, first_category, group_key, expert, title, cat_key):
        """Archive the game, initial goal and group placement in one commit."""
        checkout_branch()
        game_dir = ARCHIVE / 'games' / system / slug
        if (game_dir / 'game.json').exists():
            return fail(f'{game_key} already exists', 409)
        game_dir.mkdir(parents=True, exist_ok=True)
        (game_dir / 'game.json').write_text(json.dumps(game, indent=1) + '\n')
        (game_dir / 'categories.json').write_text(json.dumps(
            {'dimensions': [{'key': 'goal', 'name': 'Category',
                             'options': [first_category]}]}, indent=1) + '\n')
        (game_dir / 'runs').mkdir(exist_ok=True)
        if group_key:
            groups_doc = load_groups()
            group = next((g for g in groups_doc['groups'] if g['key'] == group_key), None)
            group['games'] = sorted(set(group['games']) | {game_key})
            save_groups(groups_doc)
        ensure_member(expert)
        ensure_game_topic(*game_key.split('/'), title)
        commit_push(f'Create {game_key}: by {expert}\n\n'
                    f'Title: {title}\nFirst category: {cat_key}\n'
                    f'Group: {group_key or "none"}\nVia: archivist')
        notify_discord(f'\U0001f5c2\ufe0f **{member_md(expert)}** created the '
                       f'[game](<{SITE_URL}/games/{game_key}/>) {title}'
                       + (f' in the {group_key} group' if group_key else ''),
                       wait_for=f'{SITE_URL}/games/{game_key}/')
        return None

    def _new_game_identity(game_form, expert):
        """Validate the new game address and any proposed group placement."""
        system = (game_form.get('system') or '').strip()
        title = (game_form.get('title') or '').strip()[:120]
        group_key = (game_form.get('group') or '').strip().lower()
        if system not in json.loads((ARCHIVE / 'systems.json').read_text()):
            return None, fail(f'unknown system {system!r}: systems are curated')
        if not title:
            return None, fail('a game needs a title')
        slug = slugify(title)
        if not slug:
            return None, fail('that title yields an empty slug')
        game_key = f'{system}/{slug}'
        if (ARCHIVE / 'games' / system / slug / 'game.json').exists():
            return None, fail(f'{game_key} already exists', 409)
        groups_doc = load_groups()
        group = next((g for g in groups_doc['groups'] if g['key'] == group_key), None) if group_key else None
        if group_key and not group:
            return None, fail(f'no group with the key {group_key!r}', 404)
        # Authority: over the group you are filling out, or over the system the
        # game lands in. A group expert creating into their own group is the
        # case this exists for, and the game is not in the group yet, so the
        # group is what has to be checked rather than the game.
        # creation is everybody's (good faith; experts moderate). Placing
        # the game into a group is curation and still needs scope over it.
        if group and not covers_group(expert, group) and not is_editor(expert):
            return None, fail(f'{expert} holds no scope covering the '
                        f'{group["title"]} group', 403)
        return (system, title, slug, game_key, group_key), None

    def _locked_game_create(dry_run, game_form):
        """Process the game create request under the archive write lock."""
        auth_error = auth_precheck(game_form)
        if auth_error:
            return auth_error
        expert, error = request_identity(game_form, 'user')
        if error:
            return error
        paced = pace_gate(game_form, expert, 'create')
        if paced:
            return paced
        identity, error = _new_game_identity(game_form, expert)
        if error is not None:
            return error
        system, title, slug, game_key, group_key = identity
        today = time.strftime('%Y-%m-%d', time.gmtime())
        properties, error = _new_game_properties(game_form)
        if error is not None:
            return error
        game = {'title': title, 'system': system, 'createdBy': expert, **properties,
                'createdAt': today}
        cat_label = (game_form.get('cat_label') or 'fastest completion').strip()[:80]
        cat_rule = (game_form.get('cat_rule')
                    or 'Complete the game as fast as possible.').strip()[:500]
        cat_key = slugify(game_form.get('cat_key') or cat_label)
        metric_defs, metric_error = parse_metric_defs(game_form.get('metrics'))
        if metric_error:
            return fail(metric_error)
        if not cat_key or cat_key == 'unclassified':
            return fail('bad first-category key')
        first_category = {'key': cat_key, 'label': cat_label, 'rule': cat_rule,
                     **({'metrics': metric_defs} if metric_defs else {})}
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_create': game_key,
                            'game': game, 'category': first_category,
                            'group': group_key or None})
        save_error = _save_new_game(system, slug, game_key, game,
                                    first_category, group_key, expert, title, cat_key)
        if save_error is not None:
            return save_error
        return True, (cat_key, game_key, group_key)

    @app.post('/api/game/create')
    def game_create():
        """Create a game and initial category, real on arrival.

        Any member may create a game; group placement requires covering scope
        or the editor role. Reads system, title, optional group and properties,
        cat_label, cat_rule, cat_key, metrics, dry_run.
        Answers {ok, game, category, group, note}; 409 for an existing game.
        """
        game_form = request.form
        dry_run = game_form.get('dry_run') in ('1', 'true', 'yes')
        refresh_archive()
        with lock:
            locked_result = _locked_game_create(dry_run, game_form)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        cat_key, game_key, group_key = locked_result[1]
        return jsonify({'ok': True, 'game': game_key, 'category': cat_key,
                        'group': group_key or None,
                        'note': 'It has no runs yet, so it shows as an empty game until '
                                'somebody archives one.'})


    def _new_system_fps(system_form):
        """Parse a submitter's initial system frame rate without rounding."""
        try:
            fps = float((system_form.get('fps') or SYSTEM_FPS_DEFAULT))
        except ValueError:
            return None, fail('the frame rate is a number, as exact as you have it: '
                              '60.0988138974405, not 60')
        if not (SYSTEM_FPS_MIN <= fps <= SYSTEM_FPS_MAX) or fps != fps:
            return None, fail(f'the frame rate must be between {SYSTEM_FPS_MIN:g} and '
                              f'{SYSTEM_FPS_MAX:g} frames a second')
        return fps, None

    def _locked_system_create(dry_run, system_form):
        """Process the system create request under the archive write lock."""
        auth_error = auth_precheck(system_form)
        if auth_error:
            return auth_error
        actor, error = request_identity(system_form, 'expert')
        if error:
            return error
        paced = pace_gate(system_form, actor, 'create')
        if paced:
            return paced
        name = ' '.join((system_form.get('name') or '').split())
        if not 2 <= len(name) <= 40:
            return fail('a system needs its name as people write it, up to 40 characters')
        key = (system_form.get('system') or '').strip().lower() or system_key_for(name)
        if not SYSTEM_KEY.fullmatch(key) or not 2 <= len(key) <= SYSTEM_KEY_MAX:
            return fail(f'a system key is lowercase words joined by hyphens, 2 to '
                        f'{SYSTEM_KEY_MAX} characters: nes, segacd, bandai-terebikko. '
                        f'It is the first part of every game address on that system '
                        f'and never changes')
        fps, fps_error = _new_system_fps(system_form)
        if fps_error:
            return fps_error
        systems_doc = load_systems()
        if key in systems_doc:
            return fail(f'{key!r} is already a system here: {systems_doc[key]["name"]}', 409)
        clash = next((k for k, v in systems_doc.items()
                      if v.get('name', '').lower() == name.lower()), None)
        if clash:
            return fail(f'{name} is already here under the key {clash!r}', 409)
        entry = {'name': name, 'fps': fps}
        if system_form.get('hard') in ('1', 'true', 'yes', 'on'):
            entry['hardToReproduce'] = True
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_create': {key: entry}})
        checkout_branch()
        systems_doc = load_systems()
        if key in systems_doc:
            return fail(f'{key!r} is already a system here', 409)
        systems_doc[key] = entry
        (ARCHIVE / 'systems.json').write_text(json.dumps(systems_doc, indent=1) + '\n')
        ensure_member(actor)
        commit_push(f'System {key}: created by {actor}\n\n'
                    f'Name: {name}\nFrame rate: {fps}\n'
                    f'Hard to reproduce: {"yes" if entry.get("hardToReproduce") else "no"}\n'
                    f'By: {actor}\nVia: archivist')
        notify_discord(f'\U0001f579\ufe0f **{member_md(actor)}** added the system '
                       f'**{name}**: runs on it can be submitted now')
        return True, (entry, key)

    @app.post('/api/system/create')
    def system_create():
        """Create a system that can immediately receive runs.

        Any member may supply name, system key, fps, hard and dry_run; absent
        key is derived from name, absent fps defaults to 60. Only whole-site
        experts or the Committee correct it later. Answers {ok, key, system}
        or dry-run preview; 409 when the name or key is taken.
        """
        system_form = request.form
        dry_run = system_form.get('dry_run') in ('1', 'true', 'yes')
        refresh_archive()
        with lock:
            locked_result = _locked_system_create(dry_run, system_form)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        entry, key = locked_result[1]
        return jsonify({'ok': True, 'key': key, 'system': entry,
                        'note': 'Games can be created on it now. Its frame rate '
                                'times every movie filed under it, and a whole-site '
                                'expert sets it from the expert panel.'})


    def _system_name(system_form, systems_doc, key, entry, changed, befores):
        """Validate a renamed system and detect name collisions."""
        if 'name' in system_form:
            name = ' '.join((system_form.get('name') or '').split())
            if not 2 <= len(name) <= 40:
                return fail('a system needs its name as people write it, up to 40 characters')
            clash = next((k for k, v in systems_doc.items()
                          if k != key and v.get('name', '').lower() == name.lower()), None)
            if clash:
                return fail(f'{name} is already here under the key {clash!r}', 409)
            if name != entry['name']:
                befores['name'] = entry['name']
                entry['name'] = name
                changed.append('name')
        return None

    def _system_fps(system_form, entry, changed, befores):
        """Validate a changed frame rate without rounding it."""
        if (system_form.get('fps') or '').strip():
            try:
                fps = float(system_form['fps'].strip())
            except ValueError:
                return fail('the frame rate is a number, as exact as you have it')
            if not SYSTEM_FPS_MIN <= fps <= SYSTEM_FPS_MAX:
                return fail(f'the frame rate must be between {SYSTEM_FPS_MIN:g} and '
                            f'{SYSTEM_FPS_MAX:g} frames a second')
            if fps != entry['fps']:
                befores['fps'] = str(entry['fps'])
                entry['fps'] = fps
                changed.append('fps')
        return None

    def _system_flags(system_form, entry, changed, befores):
        """Apply only explicitly requested system reproduction-flag changes."""
        for field, flag in (('hard', 'hardToReproduce'),):
            if not (system_form.get(field) or '').strip():
                continue                       # empty says "leave it as it is"
            want = system_form.get(field) in ('1', 'true', 'yes', 'on')
            if want == bool(entry.get(flag)):
                continue
            befores[flag] = str(bool(entry.get(flag)))
            if want:
                entry[flag] = True
            else:
                entry.pop(flag, None)
            changed.append(flag)

    def _locked_system_edit(dry_run, system_form):
        """Process the system edit request under the archive write lock."""
        auth_error = auth_precheck(system_form)
        if auth_error:
            return auth_error
        actor, error = request_identity(system_form, 'expert')
        if error:
            return error
        if not (is_committee(actor) or is_site_expert(actor)):
            return fail('a system is corrected by a whole-site expert or by the '
                        'Steering Committee', 403)
        paced = pace_gate(system_form, actor, 'create')
        if paced:
            return paced
        key = (system_form.get('system') or '').strip().lower()
        systems_doc = load_systems()
        if key not in systems_doc:
            return fail(f'no such system: {key!r}', 404)
        entry = dict(systems_doc[key])
        changed, befores = [], {}
        name_error = _system_name(system_form, systems_doc, key, entry, changed, befores)
        if name_error is not None:
            return name_error
        fps_error = _system_fps(system_form, entry, changed, befores)
        if fps_error is not None:
            return fps_error
        _system_flags(system_form, entry, changed, befores)
        if not changed:
            return fail('nothing sent differs from the record')
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_change': changed,
                            'would_be': {key: entry}})
        checkout_branch()
        systems_doc = load_systems()
        if key not in systems_doc:
            return fail(f'no such system: {key!r}', 404)
        systems_doc[key] = entry
        (ARCHIVE / 'systems.json').write_text(json.dumps(systems_doc, indent=1) + '\n')
        for field in changed:
            log_edit('system', key, field, befores[field], str(entry.get(field)), actor,
                     (system_form.get('reason') or '').strip() or 'correcting the system record')
        ensure_member(actor)
        commit_push(f'System {key}: {", ".join(changed)} by {actor}\n\n'
                    + ''.join(f'{f}: {befores[f]} -> {entry.get(f)}\n' for f in changed)
                    + f'By: {actor}\nVia: archivist')
        notify_edit(actor, f'the system **{entry["name"]}**', changed,
                    reason=(system_form.get('reason') or '').strip())
        return True, (changed, entry, key)

    @app.post('/api/system/edit')
    def system_edit():
        """Correct a system: its name, its frame rate, its two flags.

        The key is never among them. It opens every game address filed under the
        system, and a run cannot move between systems, so a renamed key would
        break every address it ever had.

        Who: a whole-site expert, or the Steering Committee
        Reads: form fields system (its key), name, fps, hard (each
            optional; only what is sent changes), dry_run
        Answers: {ok, key, system, changed}; dry_run: {ok, dry_run, would_change}
        """
        system_form = request.form
        dry_run = system_form.get('dry_run') in ('1', 'true', 'yes')
        refresh_archive()
        with lock:
            locked_result = _locked_system_edit(dry_run, system_form)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        changed, entry, key = locked_result[1]
        return jsonify({'ok': True, 'key': key, 'system': entry, 'changed': changed})


    def _system_delete_blockers(key):
        """Refuse deleting a system still referenced by games or expert scopes."""
        games_dir = ARCHIVE / 'games' / key
        held = sorted(g.name for g in games_dir.glob('*') if (g / 'game.json').is_file()) \
            if games_dir.is_dir() else []
        if held:
            return fail(f'{key} still holds {len(held)} game'
                        f'{"s" if len(held) != 1 else ""} ({", ".join(held[:3])}'
                        f'{", ..." if len(held) > 3 else ""}); a system is only '
                        f'removed once nothing is filed under it', 409)
        scoped = sorted({e['user'] for e in load_experts() if e['scope'] == key})
        if scoped:
            return fail(f'{", ".join(scoped)} hold{"s" if len(scoped) == 1 else ""} '
                        f'expert scope over {key}; take the scope back first', 409)
        return None

    def _locked_system_delete(dry_run, system_form):
        """Process the system delete request under the archive write lock."""
        auth_error = auth_precheck(system_form)
        if auth_error:
            return auth_error
        actor, error = request_identity(system_form, 'expert')
        if error:
            return error
        if not is_committee(actor):
            return fail('a system is removed by the Steering Committee', 403)
        paced = pace_gate(system_form, actor, 'create')
        if paced:
            return paced
        key = (system_form.get('system') or '').strip().lower()
        reason = (system_form.get('reason') or '').strip()
        systems_doc = load_systems()
        if key not in systems_doc:
            return fail(f'no such system: {key!r}', 404)
        if not 8 <= len(reason) <= 500:
            return fail('say why, in a sentence: it is public and permanent')
        blocker = _system_delete_blockers(key)
        if blocker:
            return blocker
        name = systems_doc[key]['name']
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_delete': {key: systems_doc[key]}})
        checkout_branch()
        systems_doc = load_systems()
        if key not in systems_doc:
            return fail(f'no such system: {key!r}', 404)
        systems_doc.pop(key)
        (ARCHIVE / 'systems.json').write_text(json.dumps(systems_doc, indent=1) + '\n')
        ensure_member(actor)
        commit_push(f'System {key}: removed by {actor}\n\n'
                    f'Name: {name}\nReason: {reason}\nBy: {actor}\nVia: archivist')
        notify_discord(f'\U0001f5d1\ufe0f **{member_md(actor)}** removed the empty '
                       f'system **{name}**: {reason}')
        return True, (key, name)

    @app.post('/api/system/delete')
    def system_delete():
        """Remove a system nothing stands on.

        Refused while any game is filed under it, and while any role names it as
        a scope: both would be left pointing at a machine the archive no longer
        knows. Emptying it first is somebody's deliberate work, not a side effect
        of this.

        Who: the Steering Committee
        Reads: form fields system (its key), reason, dry_run
        Answers: {ok, key, name}; dry_run: {ok, dry_run, would_delete}
        """
        system_form = request.form
        dry_run = system_form.get('dry_run') in ('1', 'true', 'yes')
        refresh_archive()
        with lock:
            locked_result = _locked_system_delete(dry_run, system_form)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        key, name = locked_result[1]
        return jsonify({'ok': True, 'key': key, 'name': name})


    def _emulator_quick_chips(edit_form, doc, system, changed, am_site, am_editor):
        """Validate and apply the requested emulator quick chips change."""
        if 'quick_chips' in edit_form:
            try:
                qc_data = json.loads(edit_form['quick_chips'])
            except Exception as ex:
                return fail(f'invalid quick_chips format: {ex}')
            if not isinstance(qc_data, list):
                return fail('quick_chips must be a JSON array of strings')
            clean_qc = [str(x).strip() for x in qc_data if str(x).strip()]
            if doc['systems'][system].get('quick_chips') != clean_qc:
                doc['systems'][system]['quick_chips'] = clean_qc
                changed.append('quick_chips')
        return None

    def _mapped_system_tool(tool, catalog_list):
        """Validate one tool mapping against the shared catalog."""
        tid = (tool.get('id') or '').strip().lower()
        if not tid or not re.fullmatch(r'[a-z0-9-_]+', tid):
            return None, fail(f'invalid tool id: {tid!r}')
        cat_entry = next((c for c in catalog_list if c.get('id') == tid), None)
        if cat_entry and cat_entry.get('kind') == 'game_tool':
            return None, fail(
                f'{cat_entry.get("name", tid)!r} is a game-specific tool and cannot be mapped to a system')
        entry = {'id': tid}
        if 'versions' in tool and isinstance(tool['versions'], list):
            entry['versions'] = [str(v).strip() for v in tool['versions'] if str(v).strip()]
        if 'cores' in tool and isinstance(tool['cores'], list):
            entry['cores'] = [str(c).strip() for c in tool['cores'] if str(c).strip()]
        return entry, None

    def _emulator_tools(edit_form, doc, system, changed, am_site, am_editor):
        """Validate and apply the requested emulator tools change."""
        if 'tools' in edit_form:
            try:
                tools_data = json.loads(edit_form['tools'])
            except Exception as ex:
                return fail(f'invalid tools format: {ex}')
            if not isinstance(tools_data, list):
                return fail('tools must be a JSON array of tool objects')

            clean_tools = []
            catalog_list = doc.get('catalog', [])
            for t in tools_data:
                entry, error = _mapped_system_tool(t, catalog_list)
                if error:
                    return error
                clean_tools.append(entry)

            if doc['systems'][system].get('tools') != clean_tools:
                doc['systems'][system]['tools'] = clean_tools
                changed.append('tools')
        return None

    def _emulator_catalog(edit_form, doc, system, changed, am_site, am_editor):
        """Validate and apply the requested emulator catalog change."""
        if 'catalog' in edit_form and (am_site or am_editor):
            try:
                cat_data = json.loads(edit_form['catalog'])
            except Exception as ex:
                return fail(f'invalid catalog format: {ex}')
            if not isinstance(cat_data, list):
                return fail('catalog must be a JSON array of tool objects')
            doc['catalog'] = cat_data
            changed.append('catalog')
        return None

    def _locked_emulators_edit(dry_run, edit_form):
        """Process the emulators edit request under the archive write lock."""
        auth_error = auth_precheck(edit_form)
        if auth_error:
            return auth_error
        actor, error = request_identity(edit_form, 'expert')
        if error:
            return error

        system = (edit_form.get('system') or '').strip().lower()
        reason = (edit_form.get('reason') or '').strip()

        if not 8 <= len(reason) <= 500:
            return fail('say why: public reason required between 8 and 500 characters')

        am_site = is_site_expert(actor)
        am_editor = is_editor(actor)

        doc = load_emulators()
        all_systems = load_systems()

        if not system:
            return fail('system key required')

        if system != 'default' and system not in all_systems:
            return fail(f'unknown system: {system!r}', 404)

        if not expert_covers_system(actor, system):
            return fail(f'{actor!r} does not have authority over {system!r} (system expert, site-wide expert, or editor required)', 403)

        changed = []

        if 'systems' not in doc:
            doc['systems'] = {}
        if system not in doc['systems']:
            doc['systems'][system] = {}

        # Update quick chips for this system if provided
        error = _emulator_quick_chips(edit_form, doc, system, changed, am_site, am_editor)
        if error is not None:
            return error

        # Update mapped tools for this system if provided
        error = _emulator_tools(edit_form, doc, system, changed, am_site, am_editor)
        if error is not None:
            return error

        # Update catalog if provided (site expert or editor only)
        error = _emulator_catalog(edit_form, doc, system, changed, am_site, am_editor)
        if error is not None:
            return error

        if not changed:
            return fail('nothing sent differs from the record')

        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'system': system, 'would_change': changed})

        checkout_branch()
        save_emulators(doc)
        log_edit('system', system, f'emulators ({", ".join(changed)})', 'previous', 'updated', actor, reason)
        ensure_member(actor)
        commit_push(f'Emulators ({system}): {", ".join(changed)} by {actor}\n\nReason: {reason}\nBy: {actor}\nVia: archivist')

        return jsonify({'ok': True, 'system': system, 'changed': changed})
        return True, ()

    @app.post('/api/emulators/edit')
    def emulators_edit():
        """Curate emulator presets, recommended versions, cores, and quick chips.

        Who: an editor or site-wide expert for all systems and global defaults;
             a system expert for their assigned system only.
        Reads: form fields system, quick_chips (JSON array), presets (JSON array),
               reason, dry_run
        Answers: {ok, system, changed}; dry_run: {ok, dry_run, would_change}
        """
        edit_form = request.form
        dry_run = edit_form.get('dry_run') in ('1', 'true', 'yes')
        refresh_archive()
        with lock:
            locked_result = _locked_emulators_edit(dry_run, edit_form)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result


    def _new_group_games(games, groups_doc):
        """Check every proposed game exists and belongs to no other group."""
        for game_key in games:
            if not re.fullmatch(r'[a-z0-9-]+/[a-z0-9-]+', game_key) or \
                    not (ARCHIVE / 'games' / game_key / 'game.json').is_file():
                return fail(f'no such game: {game_key!r}', 404)
            other = next((x for x in groups_doc['groups'] if game_key in x.get('games', [])), None)
            if other:
                return fail(f'{game_key} already belongs to the {other["title"]} group; a game '
                            f'belongs to one', 409)
        return None

    def _new_group_identity(key, title):
        """Validate a group key and title without claiming a reserved key."""
        if not re.fullmatch(r'[a-z0-9]+(-[a-z0-9]+)*', key or ''):
            return fail('the group key must be lowercase words joined by hyphens')
        if key in ('uncategorized', 'unclassified'):
            return fail(f'{key} is reserved for the derived group that gathers '
                        f'every game no group has claimed')
        if not (1 <= len(title) <= 80):
            return fail('a group needs a title')
        return None

    def _group_key_conflict(groups_doc, key):
        """Reject a group key already present in the current archive."""
        if any(g['key'] == key for g in groups_doc['groups']):
            return fail(f'a group with the key {key!r} already exists', 409)
        return None

    def _locked_group_create(dry_run, group_form):
        """Process the group create request under the archive write lock."""
        auth_error = auth_precheck(group_form)
        if auth_error:
            return auth_error
        expert, error = request_identity(group_form, 'expert')
        if error:
            return error
        paced = pace_gate(group_form, expert, 'create')
        if paced:
            return paced
        key = (group_form.get('group') or '').strip().lower()
        title = (group_form.get('title') or '').strip()
        games = [g.strip() for g in (group_form.get('games') or '').replace(',', ' ').split() if g.strip()]
        identity_error = _new_group_identity(key, title)
        if identity_error:
            return identity_error
        groups_doc = load_groups()
        conflict = _group_key_conflict(groups_doc, key)
        if conflict:
            return conflict
        games_error = _new_group_games(games, groups_doc)
        if games_error is not None:
            return games_error
        prospective_group = {'key': key, 'games': games}
        if not covers_group(expert, prospective_group) and not is_editor(expert):
            return fail(f'{expert} holds no scope covering '
                        f'{"every game listed" if games else "an empty group"}; '
                        f'a group gathers games you already speak for', 403)
        today = time.strftime('%Y-%m-%d', time.gmtime())
        # real on arrival: ratification is gone as a mechanism
        entry = {'key': key, 'title': title, 'games': games,
                 'createdBy': expert, 'createdAt': today}
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_create': entry})
        checkout_branch()
        groups_doc = load_groups()
        conflict = _group_key_conflict(groups_doc, key)
        if conflict:
            return conflict
        groups_doc['groups'].append(entry)
        save_groups(groups_doc)
        ensure_member(expert)
        commit_push(f'Group {key}: created by {expert}\n\n'
                    f'Title: {title}\nGames: {", ".join(games) or "none yet"}\n'
                    f'Via: archivist')
        notify_discord(f'\U0001f5c2\ufe0f **{member_md(expert)}** created the '
                       f'[group](<{SITE_URL}/groups/{key}/>) {title}, '
                       f'{len(games) or "no"} game{"s" if len(games) != 1 else ""} in it',
                       wait_for=f'{SITE_URL}/groups/{key}/')
        return True, (games, key)

    @app.post('/api/group/create')
    def group_create():
        """Create a group, real on arrival, exactly like a
        game: naming a family of games is a curatorial claim, not a fact.

        You may only gather games you already have authority over, which is the same
        rule appointment follows. An empty group is site scope only, since there is
        nothing yet to derive authority from.

        Who: an expert whose scope covers every listed game (site scope for an
            empty group), or an editor
        Reads: form fields group (key), title, games (space or comma separated
            system/slug keys), dry_run
        Answers: {ok, group, games, note}; 409 when the key or a game is taken
        """
        group_form = request.form
        dry_run = group_form.get('dry_run') in ('1', 'true', 'yes')
        refresh_archive()
        with lock:
            locked_result = _locked_group_create(dry_run, group_form)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        games, key = locked_result[1]
        return jsonify({'ok': True, 'group': key, 'games': games,
                        'note': 'The group exists. A mistaken one is deleted by an '
                                'expert, on the record.'})


    def _group_game_check(expert, game_key, group):
        """Require an existing game, authority over it and no duplicate membership."""
        if not (ARCHIVE / 'games' / game_key / 'game.json').is_file():
            return fail(f'no such game: {game_key!r}', 404)
        if not expert_covers(expert, game_key) and not is_editor(expert):
            return fail(f'{expert} holds no scope covering {game_key}; a group cannot '
                        f'reach a game its curator may not speak for', 403)
        if game_key in group['games']:
            return fail(f'{game_key} is already in this group', 409)
        return None

    def _group_membership_check(groups_doc, group, key, expert, add, move, drop):
        """Check each requested add, move and removal before any archive write."""
        for game_key in add:
            error = _group_game_check(expert, game_key, group)
            if error:
                return error
            other = next((x for x in groups_doc['groups'] if x['key'] != key
                          and game_key in x.get('games', [])), None)
            if other:
                return fail(f'{game_key} already belongs to the {other["title"]} group; a game '
                            f'belongs to one (move it instead)', 409)
        for game_key in move:
            error = _group_game_check(expert, game_key, group)
            if error:
                return error
        for game_key in drop:
            if game_key not in group['games']:
                return fail(f'{game_key} is not in this group', 404)
        return None

    def _move_group_memberships(groups_doc, key, move):
        """Remove moved games from their former groups before inserting them."""
        moved_from = {}
        for other in groups_doc['groups']:
            if other['key'] == key:
                continue
            hits = [game_key for game_key in move if game_key in other.get('games', [])]
            if hits:
                other['games'] = [game_key for game_key in other['games'] if game_key not in hits]
                for game_key in hits:
                    moved_from[game_key] = other['key']
        return moved_from

    def _commit_group_edit(key, add, move, drop, title, expert):
        """Apply a group membership and title revision in one logged commit."""
        checkout_branch()
        groups_doc = load_groups()
        group = next((g for g in groups_doc['groups'] if g['key'] == key), None)
        if not group:
            return None, fail(f'no group with the key {key!r}', 404)
        before_games = list(group['games'])
        before_title = group['title']
        # a move pulls the game out of whatever group held it, first
        moved_from = _move_group_memberships(groups_doc, key, move)
        group['games'] = sorted((set(group['games']) | set(add) | set(move)) - set(drop))
        if title:
            group['title'] = title
        save_groups(groups_doc)
        change_summary = ', '.join(filter(None, [
            f'+{" ".join(add)}' if add else '',
            ' '.join(f'{game_key} moved in from {moved_from[game_key]}' if game_key in moved_from
                     else f'{game_key} moved in' for game_key in move) if move else '',
            f'-{" ".join(drop)}' if drop else '',
            f'retitled {title!r}' if title else '']))
        log_edit('group', key, 'games' if (add or move or drop) else 'title',
                 ', '.join(before_games) if (add or move or drop) else before_title,
                 ', '.join(group['games']) if (add or move or drop) else group['title'],
                 expert, f'Changed from the group form: {change_summary}')
        ensure_member(expert)
        commit_push(f'Group {key}: {change_summary}\n\nBy: {expert}\nVia: archivist')
        notify_discord(f'\U0001f5c2\ufe0f **{member_md(expert)}** changed the '
                       f'[group](<{SITE_URL}/groups/{key}/>) {group["title"]}: {change_summary}',
                       wait_for=f'{SITE_URL}/groups/{key}/')
        return group, None

    def _group_edit_target(key, expert, add, move, drop, title):
        """Require a nonempty revision and authority over its existing group."""
        if not (add or move or drop or title):
            return None, None, fail('nothing to change')
        groups_doc = load_groups()
        group = next((g for g in groups_doc['groups'] if g['key'] == key), None)
        if not group:
            return None, None, fail(f'no group with the key {key!r}', 404)
        if not covers_group(expert, group) and not is_editor(expert):
            return None, None, fail(f'{expert} holds no scope covering the {group["title"]} group', 403)
        return groups_doc, group, None

    def _locked_group_edit(dry_run, group_form):
        """Process the group edit request under the archive write lock."""
        auth_error = auth_precheck(group_form)
        if auth_error:
            return auth_error
        expert, error = request_identity(group_form, 'expert')
        if error:
            return error
        key = (group_form.get('group') or '').strip().lower()
        add = [g.strip() for g in (group_form.get('add') or '').replace(',', ' ').split() if g.strip()]
        move = [g.strip() for g in (group_form.get('move') or '').replace(',', ' ').split() if g.strip()]
        drop = [g.strip() for g in (group_form.get('remove') or '').replace(',', ' ').split() if g.strip()]
        title = (group_form.get('title') or '').strip()
        groups_doc, group, error = _group_edit_target(key, expert, add, move, drop, title)
        if error is not None:
            return error
        membership_error = _group_membership_check(
            groups_doc, group, key, expert, add, move, drop)
        if membership_error:
            return membership_error
        if title and not (1 <= len(title) <= 80):
            return fail('a title must be under 80 characters')
        after = sorted((set(group['games']) | set(add) | set(move)) - set(drop))
        if dry_run:
            return jsonify({'ok': True, 'dry_run': True, 'would_hold': after,
                            'title': title or group['title']})
        group, error = _commit_group_edit(key, add, move, drop, title, expert)
        if error is not None:
            return error
        return True, (group, key)

    @app.post('/api/group/edit')
    def group_edit():
        """Add, move in or remove games, or retitle. Adding needs authority over
        the game as well as the group: a group is not a way to reach games you do
        not cover. `move` differs from `add` in one way: it pulls the game out of
        whatever group holds it, because a game belongs to one group.

        Who: an expert whose scope covers the group, or an editor; adding or
            moving a game in needs scope over that game too
        Reads: form fields group, add, move, remove (game key lists), title, dry_run
        Answers: {ok, group, games, title}; dry_run: {ok, dry_run, would_hold, title}
        """
        group_form = request.form
        dry_run = group_form.get('dry_run') in ('1', 'true', 'yes')
        refresh_archive()
        with lock:
            locked_result = _locked_group_edit(dry_run, group_form)
        if not (isinstance(locked_result, tuple) and len(locked_result) == 2
                and locked_result[0] is True):
            return locked_result
        group, key = locked_result[1]
        return jsonify({'ok': True, 'group': key, 'games': group['games'], 'title': group['title']})
