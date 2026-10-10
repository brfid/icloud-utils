#!/usr/bin/env python3
"""Install the independent mail adapter and source-owned agent guidance.

Deployment preserves private routing and review state and never reads, replaces,
or exports the Himalaya account configuration or its credentials.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

MARKER = '# icloud-utils mail launcher'


def atomic_text(path, text, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f'.{path.name}-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(text)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def interpreter():
    for candidate in ['/opt/homebrew/bin/python3', '/usr/local/bin/python3', sys.executable]:
        if Path(candidate).is_file():
            result = subprocess.run([candidate, '-c', 'import sys; print(int(sys.version_info >= (3,11)))'],
                                    text=True, capture_output=True, timeout=10, check=True)
            if result.stdout.strip() == '1':
                return candidate
    raise RuntimeError('Python 3.11 or newer is required')


def install(home, config, skills_dir=None, himalaya='himalaya', himalaya_config=None, account='icloud'):
    source = Path(__file__).resolve().parent
    support = home / 'Library/Application Support/icloud-utils/mail'
    runtime = support / 'runtime'
    canonical, alias = home / 'bin/icloud-mail', home / 'bin/icmail'
    if os.path.lexists(canonical) and (canonical.is_symlink() or MARKER not in canonical.read_text()):
        raise RuntimeError(f'Refusing to replace unrelated command: {canonical}')
    if os.path.lexists(alias) and (not alias.is_symlink() or os.readlink(alias) != 'icloud-mail'):
        raise RuntimeError(f'Refusing to replace unrelated command alias: {alias}')
    if runtime.exists() and not (runtime / '.icloud-utils-mail').is_file():
        raise RuntimeError(f'Refusing to replace an unrecognized mail runtime: {runtime}')
    skill_sources = [source / 'skills' / name for name in ('icloud-mail', 'icloud-utils')]
    if skills_dir:
        for skill in skill_sources:
            target = skills_dir / skill.name
            if target.exists():
                entrypoint = target / 'SKILL.md'
                if not entrypoint.is_file() or f'name: {skill.name}\n' not in entrypoint.read_text():
                    raise RuntimeError(f'Refusing to replace an unrelated skill: {target}')
    support.mkdir(parents=True, exist_ok=True, mode=0o700)
    support.chmod(0o700)
    stamp = dt.datetime.now(dt.UTC).strftime('%Y%m%dT%H%M%S.%fZ')
    migration = support / 'migration' / stamp
    migration.mkdir(parents=True, mode=0o700)
    migration.chmod(0o700)
    for existing in (canonical, alias, config):
        if os.path.lexists(existing):
            shutil.copy2(existing, migration / existing.name, follow_symlinks=False)
    staging = Path(tempfile.mkdtemp(prefix='.install-', dir=support))
    try:
        shutil.copy2(source / 'icloud_mail.py', staging / 'icloud_mail.py')
        shutil.copytree(source / 'docs', staging / 'docs')
        (staging / '.icloud-utils-mail').write_text('Managed by install_mail.py\n')
        subprocess.run([interpreter(), '-B', str(staging / 'icloud_mail.py'), '--version'],
                       check=True, capture_output=True, timeout=10)
        if runtime.exists():
            os.replace(runtime, migration / 'runtime')
        os.replace(staging, runtime)
        if not config.exists():
            config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            config.parent.chmod(0o700)
            routing = {'account': account, 'himalaya': himalaya,
                       'himalaya_config': str(himalaya_config or home / '.config/himalaya/config.toml'),
                       'state_dir': str(support / 'state')}
            text = '# Private toolkit routing; account identity and credentials remain in Himalaya/Keychain.\n[mail]\n'
            text += ''.join(f'{key} = {json.dumps(value)}\n' for key, value in routing.items())
            atomic_text(config, text + 'timeout_seconds = 30\n', 0o600)
        launcher = '#!/bin/sh\n' + MARKER + '\nexec ' + ' '.join(shlex.quote(value) for value in
                   [interpreter(), '-B', str(runtime / 'icloud_mail.py'), '--config', str(config)]) + ' "$@"\n'
        atomic_text(canonical, launcher, 0o755)
        temporary = alias.with_name(f'.icmail-{os.getpid()}')
        try:
            temporary.symlink_to('icloud-mail')
            os.replace(temporary, alias)
        finally:
            if os.path.lexists(temporary):
                temporary.unlink()
        if skills_dir:
            skills_dir.mkdir(parents=True, exist_ok=True)
            for skill in skill_sources:
                target = skills_dir / skill.name
                skill_staging = Path(tempfile.mkdtemp(prefix=f'.{skill.name}-', dir=skills_dir))
                try:
                    for item in skill.iterdir():
                        if item.is_dir():
                            shutil.copytree(item, skill_staging / item.name)
                        else:
                            shutil.copy2(item, skill_staging / item.name)
                    entrypoint = skill_staging / 'SKILL.md'
                    entrypoint.write_text(entrypoint.read_text().replace('../../docs/', 'references/'))
                    (skill_staging / 'references').symlink_to(runtime / 'docs', target_is_directory=True)
                    if target.exists():
                        shutil.move(str(target), str(migration / f'skill-{skill.name}'))
                    os.replace(skill_staging, target)
                finally:
                    if skill_staging.exists():
                        shutil.rmtree(skill_staging)
        return {'command': str(canonical), 'alias': str(alias), 'config': str(config),
                'runtime': str(runtime), 'migration': str(migration), 'skillsUpdated': bool(skills_dir)}
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path.home(), help='installation home; use a disposable home for tests')
    parser.add_argument('--config', type=Path, help='existing toolkit mail config, preserved during updates')
    parser.add_argument('--skills-dir', type=Path, help='explicit agent skill destination; existing matching skills are backed up')
    parser.add_argument('--himalaya', default='himalaya', help='backend executable for a new toolkit configuration')
    parser.add_argument('--himalaya-config', type=Path, help='existing private Himalaya config; never modified')
    parser.add_argument('--account', default='icloud', help='existing account key, not a login address')
    args = parser.parse_args()
    home = args.home.expanduser().resolve()
    config = (args.config or home / '.config/icloud-utils/mail.toml').expanduser().resolve()
    try:
        result = install(home, config, args.skills_dir.expanduser().resolve() if args.skills_dir else None,
                         args.himalaya, args.himalaya_config.expanduser().resolve() if args.himalaya_config else None, args.account)
        print(json.dumps(result, indent=2))
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'Mail installation failed: {error}. Prior managed files are retained under the private migration directory.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
