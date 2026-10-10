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

from icloud_mail import Config, Failure
from installer_support import atomic_text, install_alias, stable_python, validate_alias

MARKER = '# icloud-utils mail launcher'
DOCUMENTATION_FILES = ('README.md', 'AGENTS.md', 'RECOVERY.txt', 'LICENSE')


def selected_config(home, canonical, runtime, explicit):
    """Recover only our generated launcher's routing; never execute its text."""
    if explicit is not None:
        return explicit.expanduser().resolve()
    if canonical.exists():
        try:
            lines = canonical.read_text().splitlines()
            command = shlex.split(lines[2])
            if len(lines) != 3 or lines[1] != MARKER or len(command) != 7:
                raise ValueError()
            if command[0] != 'exec' or command[2:5] != ['-B', str(runtime / 'icloud_mail.py'), '--config'] or command[6] != '$@':
                raise ValueError()
            path = Path(command[5])
            if not path.is_absolute():
                raise ValueError()
        except (IndexError, ValueError):
            raise RuntimeError('Cannot determine the installed mail configuration; supply --config explicitly.') from None
        if not path.is_file():
            raise RuntimeError('The installed mail configuration is missing; restore it or deliberately supply --config. Routing was not reset.')
        return path.resolve()
    return home / '.config/icloud-utils/mail.toml'


def install(home, config=None, skills_dir=None, himalaya='himalaya', himalaya_config=None, account='icloud'):
    source = Path(__file__).resolve().parent.parent
    toolkit = home / 'Library/Application Support/icloud-utils'
    support = toolkit / 'mail'
    documentation = toolkit / 'documentation'
    runtime = support / 'runtime'
    canonical, alias = home / 'bin/icloud-mail', home / 'bin/icmail'
    if os.path.lexists(canonical) and (canonical.is_symlink() or MARKER not in canonical.read_text()):
        raise RuntimeError(f'Refusing to replace unrelated command: {canonical}')
    validate_alias(alias, canonical)
    if os.path.lexists(runtime) and (runtime.is_symlink() or not (runtime / '.icloud-utils-mail').is_file()):
        raise RuntimeError(f'Refusing to replace an unrecognized mail runtime: {runtime}')
    if os.path.lexists(documentation) and (documentation.is_symlink() or not (documentation / '.icloud-utils-documentation').is_file()):
        raise RuntimeError(f'Refusing to replace unrecognized toolkit documentation: {documentation}')
    config = selected_config(home, canonical, runtime, config)
    routing = {'account': account, 'himalaya': himalaya,
               'himalaya_config': str(himalaya_config or home / '.config/himalaya/config.toml'),
               'state_dir': str(support / 'state')}
    text = '# Private toolkit routing; account identity and credentials remain in Himalaya/Keychain.\n[mail]\n'
    text += ''.join(f'{key} = {json.dumps(value, ensure_ascii=False)}\n' for key, value in routing.items()) + 'timeout_seconds = 30\n'
    if config.exists():
        Config.load(config)
    else:
        with tempfile.TemporaryDirectory(prefix='icloud-mail-routing-') as validation:
            candidate = Path(validation) / 'mail.toml'
            candidate.write_text(text)
            Config.load(candidate)
    skill_sources = [source / 'skills' / name for name in ('icloud-mail', 'icloud-utils')]
    if skills_dir:
        for skill in skill_sources:
            target = skills_dir / skill.name
            if target.exists():
                entrypoint = target / 'SKILL.md'
                if not entrypoint.is_file() or f'name: {skill.name}\n' not in entrypoint.read_text():
                    raise RuntimeError(f'Refusing to replace an unrelated skill: {target}')
    python = stable_python()
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
    doc_staging = Path(tempfile.mkdtemp(prefix='.documentation-', dir=toolkit))
    try:
        shutil.copy2(source / 'python/icloud_mail.py', staging / 'icloud_mail.py')
        shutil.copytree(source / 'docs', doc_staging / 'docs')
        shutil.copytree(source / 'examples', doc_staging / 'examples')
        for filename in DOCUMENTATION_FILES:
            shutil.copy2(source / filename, doc_staging / filename)
        (doc_staging / '.icloud-utils-documentation').write_text('Toolkit documentation managed by install_mail.py\n')
        (staging / '.icloud-utils-mail').write_text('Managed by install_mail.py\n')
        subprocess.run([python, '-B', str(staging / 'icloud_mail.py'), '--version'],
                       check=True, capture_output=True, timeout=10)
        if documentation.exists():
            doc_migration = toolkit / 'migration' / stamp
            doc_migration.mkdir(parents=True, mode=0o700)
            os.replace(documentation, doc_migration / 'documentation')
        os.replace(doc_staging, documentation)
        if runtime.exists():
            os.replace(runtime, migration / 'runtime')
        os.replace(staging, runtime)
        if not config.exists():
            config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            atomic_text(config, text, 0o600)
        launcher = '#!/bin/sh\n' + MARKER + '\nexec ' + ' '.join(shlex.quote(value) for value in
                   [python, '-B', str(runtime / 'icloud_mail.py'), '--config', str(config)]) + ' "$@"\n'
        atomic_text(canonical, launcher, 0o755)
        install_alias(alias, canonical)
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
                    entrypoint.write_text(entrypoint.read_text().replace('../../docs/', 'references/docs/'))
                    (skill_staging / 'references').symlink_to(documentation, target_is_directory=True)
                    if target.exists():
                        shutil.move(str(target), str(migration / f'skill-{skill.name}'))
                    os.replace(skill_staging, target)
                finally:
                    if skill_staging.exists():
                        shutil.rmtree(skill_staging)
        return {'command': str(canonical), 'alias': str(alias), 'config': str(config),
                'runtime': str(runtime), 'documentation': str(documentation),
                'migration': str(migration), 'skillsUpdated': bool(skills_dir)}
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if doc_staging.exists():
            shutil.rmtree(doc_staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path.home(), help='installation home; use a disposable home for tests')
    parser.add_argument('--config', type=Path, help='toolkit routing path; default: preserve the managed launcher selection, then ~/.config/icloud-utils/mail.toml')
    parser.add_argument('--skills-dir', type=Path, help='explicit agent skill destination; existing matching skills are backed up')
    parser.add_argument('--himalaya', default='himalaya', help='backend executable for a new toolkit configuration')
    parser.add_argument('--himalaya-config', type=Path, help='existing private Himalaya config; never modified')
    parser.add_argument('--account', default='icloud', help='existing account key, not a login address')
    args = parser.parse_args()
    home = args.home.expanduser().resolve()
    try:
        result = install(home, args.config, args.skills_dir.expanduser().resolve() if args.skills_dir else None,
                         args.himalaya, args.himalaya_config.expanduser().resolve() if args.himalaya_config else None, args.account)
        print(json.dumps(result, indent=2))
    except (OSError, RuntimeError, Failure, subprocess.SubprocessError) as error:
        print(f'Mail installation failed: {error}. Prior managed files are retained under the private migration directory.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
