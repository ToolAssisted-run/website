#!/usr/bin/env python3
"""The VPS deploy script, as far as it runs without being root on the host.

tar-site-sync is root, on the live origin, and the service it deploys is not.
It pulled the archive checkout itself, so a file a direct push had changed
came back root's: the service could read systems.json and never write it, and
for three days adding a system answered with a crash. Nothing tested the
script, and CI skipped every suite for a push that touched only infra/.

The script cannot be run here (it is all absolute paths, systemctl and nginx),
but the two functions that decide who touches the archive can: they are taken
out of the real file and run under bash with stand-ins for runuser and getent,
so what is tested is the text that ships, not a copy of it.

Needs bash; skips without it.

Usage: tests/test_deploy_script.py
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
VPS = REPO / 'infra' / 'vps'
SCRIPT = VPS / 'tar-site-sync'

failures = []


def ck(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (f'  [{detail}]' if detail and not cond else ''))
    if not cond:
        failures.append(name)


def shell_function(text, name):
    """One function out of the script, from its name to its closing brace."""
    found = re.search(rf'^{re.escape(name)}\(\) \{{\n.*?^\}}\n', text, re.M | re.S)
    return found.group(0) if found else None


def somebody_else():
    """A user of this machine who is neither root nor whoever runs the test."""
    import pwd
    me = os.getuid()
    for entry in pwd.getpwall():
        if entry.pw_uid not in (0, me):
            return entry.pw_name
    return None


def main():
    bash = shutil.which('bash')
    if os.name == 'nt' or not bash:
        print('SKIP no bash here: the deploy script is not exercised')
        print('--- 0 failures')
        return
    text = SCRIPT.read_text(encoding='utf-8')
    code = [l for l in text.splitlines() if not l.lstrip().startswith('#')]

    # ---------- every script on the host at least parses ----------
    for script in sorted(VPS.iterdir()):
        if not script.is_file():
            continue
        head = script.read_bytes()[:64]
        if not (head.startswith(b'#!') and b'bash' in head.splitlines()[0]):
            continue
        parsed = subprocess.run([bash, '-n', str(script)], capture_output=True, text=True)
        ck(f'{script.name} parses', parsed.returncode == 0, parsed.stderr[-300:])

    # ---------- nothing reaches the archive checkout as root ----------
    # The rule the incident broke. A bare `git` in the checkout is root's git,
    # and what it writes is root's.
    # git as a command, which the `.git` of a path is not
    git_command = r'(?<![\w./-])git\s'
    reaching = [l for l in code if re.search(git_command, l)
                and ('$archive' in l or '/opt/archivist/archive' in l)]
    ck('the script still pulls the archive (or this suite guards nothing)',
       len(reaching) >= 5, str(len(reaching)))
    bare = [l.strip() for l in reaching
            if len(re.findall(r'as_archive_owner git\s', l)) != len(re.findall(git_command, l))]
    ck('every git command in the archive checkout runs as its owner', not bare, str(bare))
    ck('and nothing changes into the checkout to run one there',
       not [l for l in code if re.search(r'\bcd\s+("?\$archive|/opt/archivist/archive)', l)])
    ck('the checkout is named in one place',
       sum('/opt/archivist/archive' in l for l in code) == 1,
       str([l for l in code if '/opt/archivist/archive' in l]))
    last = [l for l in code if l.strip()][-1]
    ck('a checkout the service cannot write fails the deploy, as a bad vhost does',
       last.startswith('exit ') and 'archive_failed' in last and 'nginx_failed' in last, last)

    # ---------- the generator's packages: installed, and checked ----------
    # The host once had no pip, and the install line ended in
    # `2>/dev/null || true`: every site build then failed on an import for two
    # days while the deploy stayed green.
    pip_lines = [l for l in code if re.search(r'\bpip3 install\b', l)]
    ck('the generator\'s packages are installed by the deploy', bool(pip_lines), str(pip_lines))
    ck('and a failed install is never silenced',
       not [l for l in pip_lines if '2>/dev/null' in l or '|| true' in l], str(pip_lines))
    ck('pip itself is installed when the host has none',
       any('python3-pip' in l and 'apt-get install' in l for l in code))
    ck('and the generator\'s imports are checked as the service user, failing the deploy',
       any(l.strip().startswith('if ! as_archive_owner /usr/bin/python3 -c') and 'mdit_py_plugins' in l
           for l in code) and 'deps_failed' in last, last)

    as_owner = shell_function(text, 'as_archive_owner')
    strays = shell_function(text, 'archive_strays')
    ck('the two functions that decide who touches the archive are there to test',
       bool(as_owner) and bool(strays))
    if not (as_owner and strays):
        print('---', len(failures), 'failures')
        sys.exit(1)

    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        stubs = td / 'bin'
        stubs.mkdir()
        # runuser, without the privilege: says how it was called, then runs
        # what it was given, so the command's own environment can be read
        (stubs / 'runuser').write_text(
            '#!/bin/bash\n'
            f'printf "%s\\n" "$@" > {td}/runuser.args\n'
            'while [ $# -gt 0 ] && [ "$1" != "--" ]; do shift; done\n'
            'shift\nexec "$@"\n')
        (stubs / 'getent').write_text(
            '#!/bin/bash\n'
            'if [ "$2" = archivist ]; then\n'
            '  echo "archivist:x:997:997::/var/lib/archivist:/usr/sbin/nologin"\n'
            'else\n  exit 2\nfi\n')
        for stub in stubs.iterdir():
            stub.chmod(0o755)

        def run(body, **env):
            called = td / 'runuser.args'
            if called.exists():
                called.unlink()
            # bash reads ~/.bashrc when its stdin is a socket, as a test
            # runner's can be: nothing of the kind, so stderr is the script's
            done = subprocess.run(
                [bash, '--norc', '--noprofile', '-c',
                 'set -euo pipefail\n' + as_owner + strays + body],
                capture_output=True, text=True, cwd=str(td), stdin=subprocess.DEVNULL,
                env=dict({'PATH': f'{stubs}:/usr/bin:/bin', 'HOME': '/root'}, **env))
            args = called.read_text().splitlines() if called.exists() else None
            return done, args

        # ---------- as the owner, when the owner is not root ----------
        done, args = run('archive_owner=archivist\n'
                         'as_archive_owner sh -c \'echo "home=$HOME"; echo "said=$1"\' _ "two words"\n')
        ck('a checkout that is not root\'s has its git run through runuser',
           args is not None and args[:3] == ['-u', 'archivist', '--'], str(args))
        ck('and the command arrives whole, arguments and all',
           'said=two words' in done.stdout, done.stdout + done.stderr)
        ck('with the owner\'s home, not root\'s, for git to read its configuration from',
           'home=/var/lib/archivist' in done.stdout, done.stdout + done.stderr)
        done, args = run('archive_owner=ghost\nas_archive_owner sh -c \'echo "home=$HOME"\'\n'
                         'echo "carried on"\n')
        ck('an owner with no account entry does not stop the deploy',
           done.returncode == 0 and 'carried on' in done.stdout, done.stdout + done.stderr)
        ck('and git is still kept out of root\'s home',
           'home=/nonexistent' in done.stdout, done.stdout + done.stderr)
        done, args = run('archive_owner=archivist\n'
                         'as_archive_owner sh -c "exit 7" || echo "rc=$?"\n')
        ck('a git that fails as the owner is a failure the script can see',
           'rc=7' in done.stdout, done.stdout + done.stderr)

        # ---------- as root, where the checkout is root's ----------
        done, args = run('archive_owner=root\nas_archive_owner echo "ran directly"\n'
                         'as_archive_owner sh -c "exit 3" || echo "rc=$?"\n')
        ck('a checkout that is root\'s is root\'s to pull, as it always was',
           args is None and 'ran directly' in done.stdout, f'{args} {done.stdout}{done.stderr}')
        ck('and its failures are seen the same way', 'rc=3' in done.stdout, done.stdout)

        # ---------- what the checkout holds that is not its owner's ----------
        import pwd
        me = pwd.getpwuid(os.getuid()).pw_name
        checkout = td / 'archive'
        (checkout / 'games' / 'wii' / 'some-game').mkdir(parents=True)
        (checkout / '.git').mkdir()
        for name in ('systems.json', 'games/wii/some-game/categories.json', '.git/index'):
            (checkout / name).write_text('{}\n')
        entries = sum(1 for _ in checkout.rglob('*')) + 1          # and the folder itself

        done, _ = run(f'archive={checkout}\narchive_owner={me}\n'
                      'archive_failed=0\narchive_strays || archive_failed=1\n'
                      'echo "failed=$archive_failed"\n')
        ck('a checkout that is all its owner\'s is reported as nothing at all',
           done.stdout.strip() == 'failed=0' and not done.stderr.strip(),
           done.stdout + done.stderr)

        other = somebody_else()
        ck('this machine has a second account to stand in for the service',
           other is not None)
        if other:
            done, _ = run(f'archive={checkout}\narchive_owner={other}\n'
                          'archive_failed=0\narchive_strays || archive_failed=1\n'
                          'echo "failed=$archive_failed"\n')
            ck('entries that are somebody else\'s fail the deploy without stopping it',
               done.returncode == 0 and done.stdout.strip() == 'failed=1',
               done.stdout + done.stderr)
            ck('and are counted', f'archive: {entries} entries' in done.stderr, done.stderr)
            ck('and named, so the operator sees which',
               'systems.json' in done.stderr and 'categories.json' in done.stderr, done.stderr)
            ck('and the line that puts it right is given, for that owner and that checkout',
               f'sudo chown -R {other}: {checkout}' in done.stderr, done.stderr)
            ck('on stderr, where a deploy log shows it as an error',
               'failed=1' == done.stdout.strip(), done.stdout)

            # a checkout with far more strays than anybody wants listed
            for i in range(40):
                (checkout / f'stray-{i:02d}.json').write_text('{}\n')
            done, _ = run(f'archive={checkout}\narchive_owner={other}\n'
                          'archive_strays || echo "failed"\n')
            listed = [l for l in done.stderr.splitlines() if l.startswith('  /')]
            ck('a long list is cut at ten and the rest is counted',
               len(listed) == 10 and f'and {entries + 40 - 10} more' in done.stderr,
               done.stderr[-400:])

        done, _ = run(f'archive={checkout}\narchive_owner=root\n'
                      'archive_strays && echo "nothing to say"\n')
        ck('a host whose checkout is root\'s has no strays to speak of',
           'nothing to say' in done.stdout and not done.stderr.strip(),
           done.stdout + done.stderr)
        done, _ = run(f'archive={td}/no-such-checkout\narchive_owner={me}\n'
                      'archive_strays && echo "nothing to say"\n')
        ck('a host with no archive checkout has none either',
           'nothing to say' in done.stdout, done.stdout + done.stderr)

    print('---', len(failures), 'failures')
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
