"""Reject malformed private routing and account tables before backend access."""
import json
import unittest
from support.mail import MailFixture


class MailConfigurationTests(MailFixture, unittest.TestCase):
    def test_malformed_configuration_returns_structured_failure_before_backend_access(self):
        original = self.config.read_text()
        for invalid in ('mail = []', 'mail = "private-sentinel"',
                        original.replace('state_dir = ' + json.dumps(str(self.state)), 'state_dir = 42'),
                        original.replace('account = "icloud"', 'account = ' + json.dumps('\0')),
                        original.replace('himalaya = ' + json.dumps(str(self.fake)), 'himalaya = ' + json.dumps('\0'))):
            with self.subTest(invalid=invalid):
                self.config.write_text(invalid)
                result, data = self.run_cli('status')
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(data['error']['stage'], 'configuration')
                self.assertNotIn('private-sentinel', result.stdout + result.stderr)
                self.assertEqual(self.logged_commands(), [])

    def test_malformed_account_tables_and_aliases_fail_without_tracebacks(self):
        original = self.backend_config.read_text()
        for invalid in ('accounts = []', '[accounts]\nicloud = []',
                        original.replace('imap.sort.fallback = true', 'imap.sort = false'),
                        original.replace('mailbox.alias.inbox = "INBOX"', 'mailbox.alias = []'),
                        original.replace('mailbox.alias.inbox = "INBOX"', 'mailbox.alias.inbox = 42')):
            with self.subTest(invalid=invalid):
                self.backend_config.write_text(invalid)
                result, data = self.run_cli('list', '--mailbox', 'inbox')
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(data['error']['stage'], 'configuration')
                self.assertNotIn('Traceback', result.stderr)
                self.assertEqual(self.logged_commands(), [])

    def test_toolkit_configuration_rejects_credential_settings(self):
        self.config.write_text(self.config.read_text() + 'password = "fixture-password"\n')
        result, data = self.run_cli('doctor')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(data['error']['stage'], 'configuration')
        self.assertNotIn('fixture-password', result.stdout)
        self.assertEqual(self.logged_commands(), [])



if __name__ == '__main__':
    unittest.main()
