"""Check the read-only subprocess contract and child lifecycle."""
import json
import signal
import subprocess
import time
import unittest
from support.mail import MailFixture


class MailAdapterTests(MailFixture, unittest.TestCase):
    def test_backend_fixture_rejects_extra_flags_and_wrong_routing(self):
        command = [str(self.fake), '--json', '--log-level', 'off', '--config', str(self.backend_config),
                   '--account', 'icloud', '--backend', 'imap', 'message', 'read', '--mailbox=INBOX', '--raw', '--', '7']
        for invalid in (command + ['--seen'], [value if value != 'imap' else 'smtp' for value in command],
                        [value if value != str(self.backend_config) else '/wrong-config' for value in command]):
            result = subprocess.run(invalid, capture_output=True, text=True, env=self.environment, timeout=5)
            self.assertNotEqual(result.returncode, 0)

    def test_all_read_operations_obey_the_strict_backend_contract(self):
        for arguments in (('doctor',), ('mailboxes',), ('attachments', '--mailbox', 'INBOX', '7'),
                          ('search', '--mailbox=--seen', '--query=subject invoice')):
            with self.subTest(arguments=arguments):
                result, data = self.run_cli(*arguments)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue(data['success'])

    def test_backend_failures_do_not_leak_streams_or_create_receipts(self):
        for scenario in ({'fail':True}, {'invalid_json':True}, {'message':''}):
            with self.subTest(scenario=scenario):
                self.scenario_set(**scenario)
                result, data = self.run_cli('read', '--mailbox', 'INBOX', '7')
                self.assertEqual(result.returncode, 1)
                self.assertFalse(data['success'])
                self.assertNotIn('sentinel', result.stdout + result.stderr)
                self.assertNotIn('private-not-json', result.stdout + result.stderr)
                self.assertFalse(self.state.exists())

    def test_doctor_checks_backend_ok_even_when_process_exits_zero(self):
        self.scenario_set(auth_ok=False)
        result, data = self.run_cli('doctor')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(data['error']['stage'], 'authentication')
        self.assertNotIn('private-auth-error', result.stdout)
        self.assertFalse(self.state.exists())

    def test_unsupported_versions_fail_before_network_operations(self):
        self.scenario_set(version='2.2.0')
        result, data = self.run_cli('doctor')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(data['error']['stage'], 'compatibility')
        self.assertEqual(self.logged_commands(), [])

    def test_mutations_sequences_and_arbitrary_backend_flags_are_rejected(self):
        cases = [('send',), ('read','--mailbox','INBOX','7','--seen'),
                 ('read','--mailbox','INBOX','1:*'),
                 ('read','--mailbox','INBOX','9' * 5000),
                 ('list','--mailbox','INBOX','--page-size','101'),
                 ('download','--mailbox','INBOX','7','--part','3','--part','3','--target',str(self.directory/'new'))]
        for arguments in cases:
            result, data = self.run_cli(*arguments)
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertEqual(data['error']['stage'], 'usage')
        self.assertEqual(self.logged_commands(), [])
        query = '--seen --account another'
        result, _ = self.run_cli('search','--mailbox','INBOX','--query='+query)
        self.assertEqual(result.returncode, 0)
        search = next(call for call in self.logged_commands() if 'search' in call)
        self.assertEqual(search[search.index('--')+1:], [query])

    def test_download_needs_new_target_and_reports_partial_results(self):
        existing=self.directory/'existing'; existing.mkdir()
        (existing/'keep').write_text('preserve')
        result,data=self.run_cli('download','--mailbox','INBOX','7','--part','3','--target',str(existing))
        self.assertEqual(result.returncode,1)
        self.assertEqual(data['error']['stage'],'destination')
        self.assertEqual((existing/'keep').read_text(),'preserve')
        target=self.directory/'download'
        result,data=self.run_cli('download','--mailbox','INBOX','7','--part','3','--target',str(target))
        self.assertEqual(result.returncode,0,result.stdout)
        self.assertEqual((target/'part-3.txt').read_bytes(),b'part3')
        self.scenario_set(partial_download=True)
        result,data=self.run_cli('download','--mailbox','INBOX','7','--part','3','--target',str(self.directory/'partial'))
        self.assertEqual(result.returncode,1)
        self.assertEqual(data['error']['stage'],'download')
        self.assertFalse(self.state.exists())

    def test_timeout_kills_backend_descendants_without_acknowledgment(self):
        self.write_config(timeout=1)
        self.scenario_set(block=True)
        result,data=self.run_cli('read','--mailbox','INBOX','7')
        self.assertEqual(result.returncode,1)
        self.assertEqual(data['error']['stage'],'timeout')
        time.sleep(.8)
        self.assertFalse((self.directory/'orphan').exists())
        self.assertFalse(self.state.exists())

    def test_sigterm_reaps_backend_and_emits_interrupted_result(self):
        self.scenario_set(block=True)
        process=subprocess.Popen(self.command('read','--mailbox','INBOX','7'),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=self.environment)
        try:
            deadline=time.monotonic()+5
            while not (self.directory/'started').exists() and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertTrue((self.directory/'started').exists())
            process.send_signal(signal.SIGTERM)
            stdout,stderr=process.communicate(timeout=5)
            self.assertEqual(process.returncode,143,stdout+stderr)
            self.assertEqual(json.loads(stdout)['error']['stage'],'interrupted')
            time.sleep(1.7)
            self.assertFalse((self.directory/'orphan').exists())
        finally:
            if process.poll() is None:
                process.kill(); process.communicate()



if __name__ == '__main__':
    unittest.main()
