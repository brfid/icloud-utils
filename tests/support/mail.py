"""Disposable account, routing, backend, and CLI helpers for mail tests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from support import ROOT


class MailFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='icloud-mail-test-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.fake = self.directory / 'himalaya'
        self.fake.write_text('#!' + sys.executable + '\n' + (ROOT / 'tests/support/himalaya.py').read_text().split('\n', 1)[1])
        self.fake.chmod(0o755)
        self.backend_config = self.directory / 'himalaya.toml'
        self.backend_config.write_text('''[accounts.icloud]
email = "sender@example.invalid"
mailbox.alias.inbox = "INBOX"
imap.server = "imaps://imap.example.invalid:993"
imap.sort.fallback = true
imap.sasl.plain.username = "login@example.invalid"
imap.sasl.plain.password.command = ["never-executed-by-wrapper"]
''')
        self.state = self.directory / 'state'
        self.config = self.directory / 'mail.toml'
        self.write_config()
        self.scenario = self.directory / 'scenario.json'
        self.scenario.write_text('{}')
        self.calls = self.directory / 'calls.jsonl'
        self.environment = dict(os.environ, MAIL_TEST_SCENARIO=str(self.scenario),
                                MAIL_TEST_CALLS=str(self.calls),
                                MAIL_TEST_CONFIG=str(self.backend_config),
                                MAIL_TEST_STARTED=str(self.directory / 'started'),
                                MAIL_TEST_ORPHAN=str(self.directory / 'orphan'))

    def write_config(self, timeout=30):
        settings = {'account': 'icloud', 'himalaya': str(self.fake),
                    'himalaya_config': str(self.backend_config), 'state_dir': str(self.state)}
        self.config.write_text('[mail]\n' + ''.join(f'{key} = {json.dumps(value)}\n' for key,value in settings.items()) + f'timeout_seconds = {timeout}\n')

    def scenario_set(self, **settings):
        self.scenario.write_text(json.dumps(settings))

    def command(self, *arguments):
        return [sys.executable, '-B', str(ROOT / 'python/icloud_mail.py'), '--config', str(self.config), '--json', *arguments]

    def run_cli(self, *arguments):
        result = subprocess.run(self.command(*arguments), capture_output=True, text=True,
                                env=self.environment, timeout=10)
        return result, json.loads(result.stdout)

    def read(self):
        result, data = self.run_cli('read', '--mailbox', 'inbox', '7')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return data['data']['review']['receipt']

    def ack(self, token, *extra):
        result, data = self.run_cli('review', 'ack', token, '--outcome', 'clear', *extra)
        self.assertEqual(result.returncode, 0, result.stdout)
        return data

    def logged_commands(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []
