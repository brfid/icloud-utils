"""Verify disposable deployments, preserved routing, and standalone documentation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

from support.documentation import local_link_errors
from support.mail import MailFixture, ROOT


class MailInstallationTests(MailFixture, unittest.TestCase):
    def install(self, home, *arguments, checkout=ROOT):
        result = subprocess.run([sys.executable, '-B', str(checkout / 'install_mail.py'), '--home', str(home),
                                 *map(str, arguments)], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def assert_local_links(self, paths):
        self.assertEqual(local_link_errors(paths), [])

    def test_custom_config_is_preserved_when_update_omits_config(self):
        home = self.directory / 'home with spaces'
        custom = self.directory / 'private routing' / "mail 'custom'.toml"
        self.install(home, '--config', custom, '--himalaya', self.fake, '--himalaya-config', self.backend_config)
        custom.write_text(custom.read_text().replace('timeout_seconds = 30', 'timeout_seconds = 11'))
        before = custom.read_bytes()
        installed = self.install(home)
        self.assertEqual(installed['config'], str(custom))
        self.assertEqual(custom.read_bytes(), before)
        self.assertFalse((home / '.config/icloud-utils/mail.toml').exists())
        result = subprocess.run([home / 'bin/icmail', '--json', 'status'], capture_output=True,
                                text=True, env=self.environment, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['data']['configuration'], str(custom))
        other = self.directory / 'other.toml'
        installed = self.install(home, '--config', other, '--himalaya', self.fake,
                                 '--himalaya-config', self.backend_config)
        self.assertEqual(installed['config'], str(other))
        self.assertEqual(custom.read_bytes(), before)
        self.assertEqual(self.install(home)['config'], str(other))

    def test_missing_discovered_config_and_ambiguous_launcher_fail_before_update(self):
        home = self.directory / 'home'
        custom = self.directory / 'custom.toml'
        installed = self.install(home, '--config', custom)
        launcher = home / 'bin/icloud-mail'
        before = launcher.read_bytes()
        custom.unlink()
        command = [sys.executable, '-B', str(ROOT / 'install_mail.py'), '--home', str(home)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(launcher.read_bytes(), before)
        self.assertFalse(custom.exists())
        self.assertFalse((home / '.config/icloud-utils/mail.toml').exists())
        launcher.write_text('#!/bin/sh\n# icloud-utils mail launcher\nexec false "$@"\n')
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 1)
        self.assertIn('--config', result.stderr)
        self.assertEqual(len(list(Path(installed['migration']).parent.iterdir())), 1)

    def test_documentation_is_complete_and_outlives_mail_runtime_and_checkout(self):
        checkout = self.directory / 'checkout'
        shutil.copytree(ROOT, checkout, ignore=shutil.ignore_patterns('.git', '__pycache__', '.DS_Store'))
        home = self.directory / 'home'
        skills = home / '.codex/skills'
        installed = self.install(home, '--skills-dir', skills, '--himalaya', self.fake,
                                 '--himalaya-config', self.backend_config, checkout=checkout)
        checkout.rename(self.directory / 'removed-checkout')
        result = subprocess.run([home / 'bin/icmail', '--json', 'status'], capture_output=True,
                                text=True, env=self.environment, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        documentation = Path(json.loads(result.stdout)['data']['documentation'])
        self.assertFalse(documentation.is_relative_to(Path(installed['runtime'])))
        shutil.rmtree(installed['runtime'])
        self.assertTrue(documentation.is_file())
        self.assert_local_links(list(Path(installed['documentation']).rglob('*.md')) +
                                [skills / name / 'SKILL.md' for name in ('icloud-utils', 'icloud-mail')])
        self.assertTrue((skills / 'icloud-utils/references/RECOVERY.txt').is_file())
        self.assertFalse((home / 'Library/LaunchAgents').exists())
        self.assertFalse((home / 'Library/Application Support/icloud-backup').exists())

    def test_install_update_preserves_private_config_and_survives_checkout_move(self):
        checkout=self.directory/'checkout'
        shutil.copytree(ROOT,checkout,ignore=shutil.ignore_patterns('.git','__pycache__','.DS_Store'))
        home=self.directory/'home'; skills=home/'.codex/skills'
        command=[sys.executable,'-B',str(checkout/'install_mail.py'),'--home',str(home),'--skills-dir',str(skills),
                 '--himalaya',str(self.fake),'--himalaya-config',str(self.backend_config)]
        backend_before=self.backend_config.read_bytes()
        result=subprocess.run(command,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        result = subprocess.run([home / 'bin/icmail', '--json', 'read', '--mailbox', 'INBOX', '7'],
                                capture_output=True, text=True, env=self.environment, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        ledger = home / 'Library/Application Support/icloud-utils/mail/state/reviews.json'
        ledger_before = ledger.read_bytes()
        config=home/'.config/icloud-utils/mail.toml'
        config.write_text(config.read_text().replace('timeout_seconds = 30','timeout_seconds = 11'))
        private_before=config.read_bytes()
        result=subprocess.run(command,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(config.read_bytes(),private_before)
        self.assertEqual(self.backend_config.read_bytes(),backend_before)
        self.assertEqual(ledger.read_bytes(), ledger_before)
        self.assertEqual(config.stat().st_mode&0o777,0o600)
        self.assertEqual(os.readlink(home/'bin/icmail'),'icloud-mail')
        self.assertIn('references/docs/mail.md',(skills/'icloud-mail/SKILL.md').read_text())
        self.assertTrue((skills/'icloud-mail/references/docs/mail.md').is_file())
        checkout.rename(self.directory/'removed-checkout')
        result=subprocess.run([home/'bin/icmail','--json','status'],capture_output=True,text=True,env=self.environment,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertFalse(json.loads(result.stdout)['data']['networkChecked'])
        self.assertEqual(len([call for call in self.logged_commands() if 'read' in call]), 1)
        self.assertFalse((home / 'Library/Application Support/icloud-utils/mail/runtime/installer_support.py').exists())
        self.assertTrue((skills/'icloud-utils/references/docs/operations.md').is_file())

    def test_unrecognized_documentation_and_skills_are_preserved_before_runtime_creation(self):
        for name in ('documentation', 'skill'):
            with self.subTest(name=name):
                home = self.directory / name
                skills = home / '.codex/skills'
                target = (home / 'Library/Application Support/icloud-utils/documentation' if name == 'documentation'
                          else skills / 'icloud-utils')
                target.mkdir(parents=True)
                sentinel = target / 'keep.txt'
                sentinel.write_text('unrelated')
                result = subprocess.run([sys.executable, '-B', ROOT / 'install_mail.py', '--home', home,
                                         '--skills-dir', skills], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(sentinel.read_text(), 'unrelated')
                self.assertFalse((home / 'Library/Application Support/icloud-utils/mail/runtime').exists())
                self.assertFalse((home / '.config/icloud-utils/mail.toml').exists())

    def test_install_refuses_unrelated_alias_before_creating_runtime(self):
        home=self.directory/'home'; (home/'bin').mkdir(parents=True)
        alias=home/'bin/icmail'; alias.write_text('unrelated')
        result=subprocess.run([sys.executable,'-B',ROOT/'install_mail.py','--home',home],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,1)
        self.assertEqual(alias.read_text(),'unrelated')
        self.assertFalse((home/'Library').exists())


if __name__ == '__main__':
    unittest.main()
