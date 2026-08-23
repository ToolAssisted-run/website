#!/usr/bin/env python3
"""Negative tests for the archive validator.

validate.py is the only thing standing between a malformed commit and the
permanent record, and until now every test only ever asserted that a GOOD
archive passes — a validator with all its rules deleted would have been just
as green. Here each rule gets exactly one mutation of a known-good archive,
and must reject it with its own message (so an unrelated error cannot make a
case pass by accident).

Hermetic: everything happens inside a temp dir; the real archive is only ever
read (validate.py and schema/ are copied out of it).

Usage: tests/test_validate.py [real_archive_dir]
"""
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mkarchive  # noqa: E402

REAL_ARCHIVE = pathlib.Path(sys.argv[1] if len(sys.argv) > 1
                            else pathlib.Path.home() / 'ToolAssisted-archive')
PNG = mkarchive.PNG
ROM_SHA1 = hashlib.sha1(b'romlike bytes').hexdigest()

failures = []


def ck(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (f'  [{detail}]' if detail and not cond else ''))
    if not cond:
        failures.append(name)


def baseline(root):
    """A valid archive: one plain run, one fully-verified run with rosters and
    a closed case, one unclassified run, one run carrying attachments."""
    mkarchive.make_archive(root, [
        mkarchive.run_spec('M900201', frames=6000, authors=['Ada']),
        mkarchive.run_spec(
            'M900202', frames=5000, authors=['Bo'],
            status={'reproduced': 'community', 'verified': 'provisional'},
            reproductions=[{'user': 'Rep', 'date': '2026-03-01'}],
            verifications=[{'user': 'Ver', 'date': '2026-03-02'},
                           {'user': 'Ver2', 'date': '2026-03-03'}],
            likes=[{'user': 'Fan', 'date': '2026-03-04'}],
            cases=[{'id': 1, 'openedBy': 'Skeptic', 'date': '2026-03-05',
                    'reason': 'Closed case fixture.',
                    'verifiers': ['Ver', 'Ver2'],
                    'reaffirmations': [{'user': 'Ver', 'date': '2026-03-06', 'reaffirm': True},
                                       {'user': 'Ver2', 'date': '2026-03-06', 'reaffirm': True}],
                    'status': 'closed'}],
            reports=[{'id': 1, 'kind': 'spam-malicious', 'by': 'Fan',
                      'date': '2026-03-07', 'details': 'x', 'status': 'open'}]),
        mkarchive.run_spec('M900203', goal='unclassified', frames=900, authors=['Cy'],
                           goalDescription='A playaround.'),
        mkarchive.run_spec('M900204', frames=4000, authors=['Dee'],
                           attachments=[{'file': 'attachments/config.txt', 'role': 'config'}],
                           contract={'emulator': 'BizHawk 2.11',
                                     'rom': {'name': 'Game (U).nes', 'sha1': ROM_SHA1}}),
    ])
    att = root / 'games/nes/testgame/runs/M900204/attachments'
    att.mkdir(parents=True, exist_ok=True)
    (att / 'config.txt').write_text('key = value\n')
    # the validator lives beside the data it checks
    shutil.copy2(REAL_ARCHIVE / 'validate.py', root / 'validate.py')
    shutil.copytree(REAL_ARCHIVE / 'schema', root / 'schema', dirs_exist_ok=True)
    return root


def run_validate(root):
    r = subprocess.run([sys.executable, str(root / 'validate.py')],
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def rj(root, rid, game='nes/testgame'):
    return root / 'games' / game / 'runs' / rid / 'run.json'


def edit(root, rid, fn, game='nes/testgame'):
    p = rj(root, rid, game)
    d = json.loads(p.read_text())
    fn(d)
    p.write_text(json.dumps(d, indent=1) + '\n')


def rdir(root, rid, game='nes/testgame'):
    return root / 'games' / game / 'runs' / rid


# ---------------------------------------------------------------- mutations
def m_stray_file(root):
    (root / 'games/nes/testgame/runs/stray.txt').write_text('x')

def m_missing_runjson(root):
    rj(root, 'M900201').unlink()

def m_id_mismatch(root):
    edit(root, 'M900201', lambda d: d.update(id='M999999'))

def m_unknown_category(root):
    edit(root, 'M900201', lambda d: d.update(category={'goal': 'no-such-goal'}))

def m_sub_required(root):
    # the option grows subcategories; the run names none
    cp = root / 'games/nes/testgame/categories.json'
    d = json.loads(cp.read_text())
    opt = next(o for o in d['dimensions'][0]['options'] if o['key'] == 'fastest')
    opt['subcategories'] = [{'key': 'any', 'label': 'any%'}]
    cp.write_text(json.dumps(d, indent=1) + '\n')

def m_sub_unknown(root):
    m_sub_required(root)
    edit(root, 'M900201', lambda d: d['category'].update(sub='nope'))

def m_sub_without_subs(root):
    edit(root, 'M900201', lambda d: d['category'].update(sub='any'))

def m_uncl_no_description(root):
    edit(root, 'M900203', lambda d: d.pop('goalDescription'))

def m_uncl_verified(root):
    edit(root, 'M900203', lambda d: d.update(
        verifications=[{'user': 'Ver', 'date': '2026-03-01'}],
        status={'reproduced': 'none', 'verified': 'provisional'}))

def m_movie_missing(root):
    (rdir(root, 'M900201') / 'M900201.bk2').unlink()

def m_movie_too_big(root):
    (rdir(root, 'M900201') / 'M900201.bk2').write_bytes(b'\0' * (101 * 1024 * 1024))

def m_notes_too_big(root):
    (rdir(root, 'M900201') / 'notes.md').write_text('x' * (1024 * 1024 + 10))

def m_thumb_missing(root):
    (rdir(root, 'M900201') / 'thumb.png').unlink()

def m_thumb_too_big(root):
    (rdir(root, 'M900201') / 'thumb.png').write_bytes(PNG + b'\0' * (256 * 1024))

def m_thumb_not_image(root):
    (rdir(root, 'M900201') / 'thumb.png').write_bytes(b'definitely not a png')

def m_attachment_missing(root):
    (rdir(root, 'M900204') / 'attachments/config.txt').unlink()

def m_attachment_too_big(root):
    (rdir(root, 'M900204') / 'attachments/config.txt').write_text('x' * (128 * 1024 + 10))

def m_attachment_not_utf8(root):
    (rdir(root, 'M900204') / 'attachments/config.txt').write_bytes(b'\xff\xfe\x00binary')

def m_attachment_bad_ext(root):
    p = rdir(root, 'M900204')
    (p / 'attachments/evil.exe').write_text('x')
    (p / 'attachments/config.txt').unlink()
    edit(root, 'M900204', lambda d: d.update(
        attachments=[{'file': 'attachments/evil.exe', 'role': 'x'}]))

def m_attachment_is_rom(root):
    (rdir(root, 'M900204') / 'attachments/config.txt').write_bytes(b'romlike bytes')

def m_attachments_total(root):
    p = rdir(root, 'M900204') / 'attachments'
    files = []
    for i in range(6):
        (p / f'big{i}.txt').write_text('x' * (100 * 1024))
        files.append({'file': f'attachments/big{i}.txt', 'role': 'x'})
    edit(root, 'M900204', lambda d: d.update(
        attachments=d['attachments'] + files))

def m_self_reproduction(root):
    edit(root, 'M900202', lambda d: d.update(
        reproductions=[{'user': 'Bo', 'date': '2026-03-01',
                        'screenshot': 'reproductions/1-Rep.png'}]))

def m_duplicate_reproduction(root):
    edit(root, 'M900202', lambda d: d.update(
        reproductions=d['reproductions'] + [{'user': 'Rep', 'date': '2026-03-09',
                                             'screenshot': 'reproductions/1-Rep.png'}]))

def m_self_verification(root):
    edit(root, 'M900202', lambda d: d['verifications'].append(
        {'user': 'Bo', 'date': '2026-03-09'}))

def m_duplicate_verification(root):
    edit(root, 'M900202', lambda d: d['verifications'].append(
        {'user': 'Ver', 'date': '2026-03-09'}))

def m_screenshot_missing(root):
    (rdir(root, 'M900202') / 'reproductions/1-Rep.png').unlink()

def m_screenshot_too_big(root):
    (rdir(root, 'M900202') / 'reproductions/1-Rep.png').write_bytes(
        PNG + b'\0' * (512 * 1024))

def m_screenshot_bad_ext(root):
    p = rdir(root, 'M900202')
    (p / 'reproductions/1-Rep.bmp').write_bytes(PNG)
    (p / 'reproductions/1-Rep.png').unlink()
    edit(root, 'M900202', lambda d: d['reproductions'][0].update(
        screenshot='reproductions/1-Rep.bmp'))

def m_screenshot_not_image(root):
    (rdir(root, 'M900202') / 'reproductions/1-Rep.png').write_bytes(b'not an image')

def m_self_like(root):
    edit(root, 'M900202', lambda d: d['likes'].append({'user': 'Bo', 'date': '2026-03-09'}))

def m_duplicate_like(root):
    edit(root, 'M900202', lambda d: d['likes'].append({'user': 'Fan', 'date': '2026-03-09'}))

def m_report_id_collision(root):
    edit(root, 'M900201', lambda d: d.update(reports=[
        {'id': 1, 'kind': 'other', 'by': 'Fan', 'date': '2026-03-09',
         'details': 'collides', 'status': 'open'}]))

def m_resolved_without_resolver(root):
    edit(root, 'M900202', lambda d: d['reports'][0].update(status='resolved'))

def m_duplicate_case_id(root):
    edit(root, 'M900202', lambda d: d['cases'].append(dict(d['cases'][0])))

def m_case_verifier_not_in_roster(root):
    edit(root, 'M900202', lambda d: d['cases'][0].update(
        verifiers=['Ver', 'Ver2', 'Ghost']))

def m_vote_outside_snapshot(root):
    edit(root, 'M900202', lambda d: d['cases'][0]['reaffirmations'].append(
        {'user': 'Outsider', 'date': '2026-03-09', 'reaffirm': True}))

def m_duplicate_vote(root):
    edit(root, 'M900202', lambda d: d['cases'][0]['reaffirmations'].append(
        {'user': 'Ver', 'date': '2026-03-09', 'reaffirm': True}))

def m_case_status_lies(root):
    edit(root, 'M900202', lambda d: d['cases'][0].update(status='open'))

def m_upheld_without_invalidation(root):
    def f(d):
        d['cases'][0]['reaffirmations'] = [
            {'user': 'Ver', 'date': '2026-03-06', 'reaffirm': False},
            {'user': 'Ver2', 'date': '2026-03-06', 'reaffirm': False}]
        d['cases'][0]['status'] = 'upheld'
    edit(root, 'M900202', f)

def m_status_reproduced_lies(root):
    edit(root, 'M900201', lambda d: d.update(
        status={'reproduced': 'community', 'verified': 'none'}))

def m_status_verified_lies(root):
    # claiming confirmed with no expert-stamped verification behind it
    edit(root, 'M900202', lambda d: d.update(
        status={'reproduced': 'community', 'verified': 'confirmed',
                'console': d['status'].get('console', 'none')}))

def m_undeclared_file(root):
    (rdir(root, 'M900201') / 'secret.bin').write_bytes(b'\0\1\2')

def m_schema_violation_run(root):
    edit(root, 'M900201', lambda d: d.update(thumbnail='picture.gif'))

def m_schema_violation_author(root):
    (root / 'authors' / 'ada.json').write_text(json.dumps({'username': 'Ada'}))

def m_missing_categories(root):
    (root / 'games/nes/testgame/categories.json').unlink()

def m_run_without_movie(root):
    edit(root, 'M900201', lambda d: d.pop('movie'))

def m_console_status_lies(root):
    edit(root, 'M900202', lambda d: d['status'].update(console='community'))

def m_console_imported_on_native(root):
    edit(root, 'M900202', lambda d: d['status'].update(console='imported'))

def m_console_without_proof(root):
    edit(root, 'M900202', lambda d: d.update(consoleVerifications=[
        {'user': 'Hardware', 'date': '2026-03-08', 'proof': 'not-a-link'}]))

def m_console_self_act(root):
    edit(root, 'M900202', lambda d: d.update(consoleVerifications=[
        {'user': 'Bo', 'date': '2026-03-08', 'proof': 'https://example.com/v'}]))

def m_console_duplicate(root):
    edit(root, 'M900202', lambda d: d.update(consoleVerifications=[
        {'user': 'Hardware', 'date': '2026-03-08', 'proof': 'https://example.com/v'},
        {'user': 'Hardware', 'date': '2026-03-09', 'proof': 'https://example.com/w'}]))

def m_console_screenshot_misplaced(root):
    p = rdir(root, 'M900202') / 'reproductions'
    (p / 'stray.png').write_bytes(PNG)
    edit(root, 'M900202', lambda d: d.update(consoleVerifications=[
        {'user': 'Hardware', 'date': '2026-03-08', 'proof': 'https://example.com/v',
         'screenshot': 'reproductions/stray.png'}]))

def m_negative_frames(root):
    edit(root, 'M900201', lambda d: d['movie'].update(frames=-5))

def m_broken_json(root):
    rj(root, 'M900201').write_text('{ this is not json')

def _groups(root, doc):
    (root / 'groups.json').write_text(json.dumps(doc, indent=1))

def _experts(root, entries):
    """Grant each entry as a role event, which is where scopes live now."""
    events = [{'user': e['user'], 'role': 'expert', 'scope': e['scope'],
               'action': 'granted', 'by': 'founder', 'date': '2026-01-01',
               'reason': 'a fixture grant, long enough to pass'}
              for e in entries]
    (root / 'roles.json').write_text(json.dumps({'events': events}, indent=1))

def m_group_unknown_game(root):
    _groups(root, {'groups': [{'key': 'fam', 'title': 'Fam',
                               'games': ['nes/testgame', 'nes/ghost']}]})

def m_group_duplicate_key(root):
    _groups(root, {'groups': [{'key': 'fam', 'title': 'Fam', 'games': ['nes/testgame']},
                              {'key': 'fam', 'title': 'Other', 'games': []}]})

def m_founder_revoked(root):
    import json as _json
    f = root / 'roles.json'
    d = _json.loads(f.read_text())
    d['events'].append({'user': 'Root', 'role': 'founder', 'action': 'revoked',
                        'by': 'committee', 'date': '2026-03-01',
                        'reason': 'a coup, which the validator must refuse'})
    f.write_text(_json.dumps(d, indent=1))

def m_ratified_without_date(root):
    g = root / 'games' / 'nes' / 'testgame' / 'game.json'
    d = json.loads(g.read_text())
    d['ratifiedBy'] = 'SomeExpert'          # and no ratifiedAt: not an act
    g.write_text(json.dumps(d, indent=1))

def m_attested_without_expert(root):
    (root / 'authors' / 'ada.json').write_text(json.dumps(
        {'username': 'Ada', 'claimed': True, 'claimMethod': 'attested',
         'attestation': 'verified by a long enough method'}, indent=1))

def m_attested_without_method(root):
    (root / 'authors' / 'ada.json').write_text(json.dumps(
        {'username': 'Ada', 'claimed': True, 'claimMethod': 'attested',
         'attestedBy': 'Root', 'attestation': 'trust me'}, indent=1))

def m_attested_by_without_method_field(root):
    (root / 'authors' / 'ada.json').write_text(json.dumps(
        {'username': 'Ada', 'claimed': True, 'attestedBy': 'Root'}, indent=1))

def m_content_removed_but_movie_present(root):
    edit(root, 'M900201', lambda d: d.__setitem__('withdrawn', {
        'by': 'Root', 'date': '2026-08-17', 'role': 'expert',
        'reason': 'Imported without every author.', 'contentRemoved': True}))

def m_unclaimed_author_record(root):
    (root / 'authors' / 'ghost.json').write_text(json.dumps(
        {'username': 'Ghost', 'claimed': False}, indent=1))

def m_actor_without_member_record(root):
    (root / 'authors' / 'rep.json').unlink()

def m_group_reserved_key(root):
    _groups(root, {'groups': [{'key': 'unclassified', 'title': 'Fam',
                               'games': ['nes/testgame']}]})

def m_group_bad_key(root):
    _groups(root, {'groups': [{'key': 'Not A Key', 'title': 'Fam', 'games': []}]})

def m_scope_unknown_group(root):
    _experts(root, [{'user': 'Root', 'scope': 'group:nonexistent'}])

def m_scope_unknown_game(root):
    _experts(root, [{'user': 'Root', 'scope': 'nes/ghost'}])

def m_scope_unknown_system(root):
    _experts(root, [{'user': 'Root', 'scope': 'megadrive'}])


def _rename_rec(root, new_name, old_name):
    (root / 'authors' / f'{new_name.lower()}.json').write_text(json.dumps(
        {'username': new_name, 'claimed': True, 'claimedBy': old_name,
         'claimedAt': '2026-08-19', 'claimMethod': 'committee',
         'attestedBy': 'Root',
         'attestation': 'fixture: a name claim was approved'}, indent=1) + '\n')

def m_ghost_record(root):
    # 'Ada' keeps her record AND is claimed away: the approval must have
    # deleted the old record, so this state is an error
    _rename_rec(root, 'SecondAda', 'Ada')

def m_former_name_selfact(root):
    # Ada renamed to RenAda (old record gone), then likes her own run under
    # the new name: the credit under the former name is still hers
    (root / 'authors/ada.json').unlink()
    _rename_rec(root, 'RenAda', 'Ada')
    edit(root, 'M900201', lambda d: d.update(
        likes=[{'user': 'RenAda', 'date': '2026-03-08'}]))

CASES = [
    ('stray file in runs/', m_stray_file, 'stray file'),
    ('a run in a category with subcategories must name one', m_sub_required, 'has subcategories'),
    ('a subcategory the category does not define', m_sub_unknown, 'not one of them'),
    ('a subcategory where the category has none', m_sub_without_subs, 'has none'),
    ('a superseded name cannot keep its member record', m_ghost_record, 'superseded by'),
    ('self-acts resolve through renames', m_former_name_selfact, 'their own run'),
    ('missing run.json', m_missing_runjson, 'missing run.json'),
    ('run id must match its folder', m_id_mismatch, '!= folder name'),
    ('unknown category option', m_unknown_category, 'unknown category'),
    ('unclassified needs a goal description', m_uncl_no_description, 'goalDescription'),
    ('unclassified cannot be verified', m_uncl_verified, 'cannot hold a live verification'),
    ('declared movie must exist', m_movie_missing, 'declared movie'),
    ('movie size cap', m_movie_too_big, 'movie exceeds'),
    ('notes size cap', m_notes_too_big, 'notes.md exceeds'),
    ('declared thumbnail must exist', m_thumb_missing, 'declared thumbnail'),
    ('thumbnail size cap', m_thumb_too_big, 'thumbnail exceeds'),
    ('thumbnail must be a real image', m_thumb_not_image, 'not a real'),
    ('declared attachment must exist', m_attachment_missing, 'declared attachment'),
    ('attachment size cap', m_attachment_too_big, 'attachment'),
    ('attachment must be UTF-8', m_attachment_not_utf8, 'not valid UTF-8'),
    ('attachment extension allowlist', m_attachment_bad_ext, 'extension not allowed'),
    ('attachment may not be the ROM', m_attachment_is_rom, 'matches the declared ROM hash'),
    ('attachments total cap', m_attachments_total, 'text attachments exceed'),
    ('no self-reproduction', m_self_reproduction, 'reproduction by'),
    ('no duplicate reproduction', m_duplicate_reproduction, 'duplicate reproduction'),
    ('no self-verification', m_self_verification, 'verification by'),
    ('no duplicate verification', m_duplicate_verification, 'duplicate verification'),
    ('declared screenshot must exist', m_screenshot_missing, 'declared screenshot'),
    ('screenshot size cap', m_screenshot_too_big, 'screenshot'),
    ('screenshot extension allowlist', m_screenshot_bad_ext, 'extension not allowed'),
    ('screenshot must be a real image', m_screenshot_not_image, 'not a real'),
    ('no self-like', m_self_like, 'cannot like their own run'),
    ('no duplicate like', m_duplicate_like, 'duplicate like'),
    ('report ids are globally unique', m_report_id_collision, 'collides'),
    ('resolved report needs a resolver', m_resolved_without_resolver, 'resolvedBy'),
    ('no duplicate case id', m_duplicate_case_id, 'duplicate case id'),
    ('case verifiers come from the roster', m_case_verifier_not_in_roster,
     'not in the verifications roster'),
    ('only the snapshot may vote', m_vote_outside_snapshot, 'snapshot may vote'),
    ('no duplicate vote', m_duplicate_vote, 'duplicate vote'),
    ('case status cannot lie', m_case_status_lies, 'votes derive'),
    ('upheld case invalidates its verifications', m_upheld_without_invalidation,
     'not invalidated'),
    ('status.reproduced cannot lie', m_status_reproduced_lies, 'status.reproduced'),
    ('status.verified cannot lie', m_status_verified_lies, 'status.verified'),
    ('no undeclared files', m_undeclared_file, 'undeclared file'),
    ('run schema enforced', m_schema_violation_run, 'schema violation'),
    ('author schema enforced', m_schema_violation_author, 'schema violation'),
    ('missing categories.json is reported', m_missing_categories, 'categories.json'),
    ('run without movie is reported', m_run_without_movie, 'movie.file'),
    ('status.console cannot lie', m_console_status_lies, 'status.console'),
    ('only imported runs can inherit console verification', m_console_imported_on_native,
     'not imported'),
    ('console verification without proof', m_console_without_proof, 'schema violation'),
    ('no self console-verification', m_console_self_act, 'consoleVerification by'),
    ('no duplicate console verification', m_console_duplicate, 'duplicate consoleVerification'),
    ('console screenshots live under console/', m_console_screenshot_misplaced,
     'must live under console/'),
    ('negative frame counts', m_negative_frames, 'schema violation'),
    ('unparseable run.json is reported', m_broken_json, 'not valid JSON'),
    ('a group cannot list a game that is not here', m_group_unknown_game,
     'not a game in this archive'),
    ('group keys are unique', m_group_duplicate_key, 'duplicate group key'),
    ('group key shape enforced', m_group_bad_key, 'schema violation'),
    ('the unclassified key is reserved', m_group_reserved_key, 'is reserved'),
    ('a content-removed withdrawal that kept its movie',
     m_content_removed_but_movie_present, 'still here'),
    ('a revoked founder role', m_founder_revoked, 'permanent'),
    ('a ratification with no date on it', m_ratified_without_date,
     'ratifiedBy and ratifiedAt go together'),
    ('an attestation with no expert behind it', m_attested_without_expert,
     'without the expert'),
    ('an attestation with no stated method', m_attested_without_method,
     'without a public method'),
    ('attestedBy without the matching method', m_attested_by_without_method_field,
     'claimMethod is neither'),
    ('an author record that is not a member', m_unclaimed_author_record,
     'records exist only for members'),
    ('an act by somebody with no member record', m_actor_without_member_record,
     'no member record'),
    ('expert scope over an unknown group', m_scope_unknown_group, 'no such group'),
    ('expert scope over an unknown game', m_scope_unknown_game,
     'not a game in this archive'),
    ('expert scope over an unknown system', m_scope_unknown_system,
     'not a system in systems.json'),
]


def main():
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        base = baseline(td / 'base')
        code, out = run_validate(base)
        ck('baseline archive is valid', code == 0, out[-400:])
        if code:
            print(out[-2000:])
            print('---', len(failures), 'failures')
            sys.exit(1)

        for i, (name, mutate, expect) in enumerate(CASES):
            work = td / f'case{i}'
            shutil.copytree(base, work)
            mutate(work)
            code, out = run_validate(work)
            ok = code == 1 and expect in out
            ck(f'rejects: {name}', ok,
               f'exit={code} expected {expect!r} in output; got: {out.strip()[-200:]}')
            shutil.rmtree(work)

        # an act recorded under a name later renamed away is still a member's
        # act: the record moved, the person did not
        work = td / 'renamed-ok'
        shutil.copytree(base, work)
        (work / 'authors/dee.json').unlink()
        _rename_rec(work, 'NewDee', 'Dee')
        edit(work, 'M900201', lambda d: d.update(
            likes=[{'user': 'Dee', 'date': '2026-03-08'}]))
        code, out = run_validate(work)
        ck('an act under a former name belongs to the member it became',
           code == 0, out.strip()[-300:])
        shutil.rmtree(work)

        # a missing dependency must fail loudly, not silently skip schema checks
        work = td / 'nodep'
        shutil.copytree(base, work)
        stub = work / 'stub'
        stub.mkdir()
        (stub / 'jsonschema.py').write_text('raise ImportError("simulated absence")\n')
        r = subprocess.run([sys.executable, str(work / 'validate.py')],
                           capture_output=True, text=True,
                           env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(stub)})
        ck('missing jsonschema fails loudly', r.returncode != 0
           and 'jsonschema' in (r.stdout + r.stderr), (r.stdout + r.stderr)[-200:])

    print('---', len(failures), 'failures')
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
