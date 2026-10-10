"""Exercise mail adapter contracts against a disposable Himalaya process."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]

FAKE = r'''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
scenario=json.loads(pathlib.Path(os.environ['MAIL_TEST_SCENARIO']).read_text())
args=sys.argv[1:]
if args==['--version']:
    print('himalaya v'+scenario.get('version','2.1.0'))
    sys.exit(0)
with open(os.environ['MAIL_TEST_CALLS'],'a') as f:
    f.write(json.dumps(args)+'\n')
while args and args[0].startswith('-'):
    flag=args.pop(0)
    if flag not in ('--json',): args.pop(0)
kind=tuple(args[:2])
if kind==('imap','status'):
    countfile=pathlib.Path(os.environ['MAIL_TEST_CALLS']+'.status')
    count=int(countfile.read_text())+1 if countfile.exists() else 1
    countfile.write_text(str(count))
    epoch=scenario.get('epoch',10)
    if scenario.get('change_epoch') and count%2==0: epoch+=1
    print(json.dumps({'uid_validity':epoch,'messages':1,'unseen':1}))
elif kind==('account','check'):
    ok=scenario.get('auth_ok',True)
    print(json.dumps({'account':'icloud','backends':[{'backend':'imap','ok':ok,'error':None if ok else 'private-auth-error'}]}))
elif kind==('mailbox','list'):
    print(json.dumps({'mailboxes':[{'name':'INBOX'}]}))
elif args[0]=='envelope':
    print(json.dumps({'envelopes':[{'id':'7','subject':'Synthetic fixture','flags':[]}]}))
elif kind==('message','read'):
    if scenario.get('fail'):
        print('private-body-and-credential-sentinel')
        print('private-stderr-sentinel',file=sys.stderr)
        sys.exit(7)
    if scenario.get('block'):
        pathlib.Path(os.environ['MAIL_TEST_STARTED']).write_text('started')
        marker=os.environ['MAIL_TEST_ORPHAN']
        code='import time,pathlib; time.sleep(1.5); pathlib.Path('+repr(marker)+').write_text("orphan")'
        subprocess.Popen([sys.executable,'-c',code])
        time.sleep(20)
    if scenario.get('invalid_json'): print('private-not-json')
    else: print(json.dumps({'message':scenario.get('message','Subject: Synthetic\r\n\r\nFixture body.\r\n')}))
elif kind==('attachment','list'):
    print(json.dumps({'attachments':[{'id':'3','size':5,'inline':False,'filename':'part.txt'}]}))
elif kind==('attachment','download'):
    target=pathlib.Path(args[args.index('--dir')+1])
    values=args[args.index('--')+1:]
    rows=[]
    for part in values[1:]:
        path=target/('part-'+part+'.txt')
        path.write_bytes(('part'+part).encode())
        rows.append({'id':part,'size':len(path.read_bytes()),'inline':False,'path':str(path)})
    if scenario.get('partial_download'): rows=[]
    print(json.dumps({'attachments':rows}))
else:
    print('unexpected command',file=sys.stderr)
    sys.exit(3)
'''


class MailFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='icloud-mail-test-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.fake = self.directory / 'himalaya'
        self.fake.write_text(FAKE)
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
                                MAIL_TEST_STARTED=str(self.directory / 'started'),
                                MAIL_TEST_ORPHAN=str(self.directory / 'orphan'))

    def write_config(self, timeout=30):
        settings = {'account': 'icloud', 'himalaya': str(self.fake),
                    'himalaya_config': str(self.backend_config), 'state_dir': str(self.state)}
        self.config.write_text('[mail]\n' + ''.join(f'{key} = {json.dumps(value)}\n' for key,value in settings.items()) + f'timeout_seconds = {timeout}\n')

    def scenario_set(self, **settings):
        self.scenario.write_text(json.dumps(settings))

    def command(self, *arguments):
        return [sys.executable, '-B', str(ROOT / 'icloud_mail.py'), '--config', str(self.config), '--json', *arguments]

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


class MailAdapterTests(MailFixture, unittest.TestCase):
    def test_listing_does_not_review_and_read_requires_explicit_ack(self):
        listed, data = self.run_cli('list', '--mailbox', 'INBOX', '--page', '2', '--page-size', '1')
        self.assertEqual(listed.returncode, 0, listed.stdout)
        self.assertTrue(data['data']['mayHaveMore'])
        self.assertFalse(data['data']['scopeCoverageComplete'])
        self.assertFalse(self.state.exists())
        token = self.read()
        self.assertNotIn('Fixture body', (self.state / 'reviews.json').read_text())
        _, data = self.run_cli('list', '--mailbox', 'INBOX')
        self.assertFalse(data['data']['envelopes'][0]['review']['reviewed'])
        self.ack(token)
        self.ack(token)
        _, data = self.run_cli('list', '--mailbox', 'INBOX')
        self.assertTrue(data['data']['envelopes'][0]['review']['reviewed'])
        self.assertEqual((self.state / 'reviews.json').stat().st_mode & 0o777, 0o600)
        reads = [call for call in self.logged_commands() if 'read' in call]
        self.assertEqual(len(reads), 1)
        self.assertNotIn('--seen', reads[0])
        self.assertIn('--backend', reads[0])
        self.assertIn('--raw', reads[0])

    def test_epoch_reset_is_a_new_identity_and_mid_read_reset_fails(self):
        token = self.read()
        self.ack(token)
        self.scenario_set(epoch=11)
        _, data = self.run_cli('list', '--mailbox', 'INBOX')
        self.assertFalse(data['data']['envelopes'][0]['review']['reviewed'])
        before = (self.state / 'reviews.json').read_bytes()
        self.scenario_set(epoch=12, change_epoch=True)
        result, data = self.run_cli('read', '--mailbox', 'INBOX', '7')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(data['error']['stage'], 'identity')
        self.assertEqual((self.state / 'reviews.json').read_bytes(), before)

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

    def test_changed_content_under_same_identity_preserves_prior_receipt(self):
        token = self.read()
        self.ack(token)
        path = self.state / 'reviews.json'
        before = path.read_bytes()
        self.scenario_set(message='Subject: Changed fixture\r\n\r\nChanged contents')
        result, data = self.run_cli('read', '--mailbox', 'INBOX', '7')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(data['error']['stage'], 'identity')
        self.assertEqual(path.read_bytes(), before)

    def test_shared_state_directory_is_not_silently_chmodded(self):
        self.state.mkdir(mode=0o755)
        self.state.chmod(0o755)
        result, data = self.run_cli('read', '--mailbox', 'INBOX', '7')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(data['error']['stage'], 'state')
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o755)
        self.assertFalse((self.state / 'reviews.json').exists())

    def test_toolkit_configuration_rejects_credential_settings(self):
        self.config.write_text(self.config.read_text() + 'password = "fixture-password"\n')
        result, data = self.run_cli('doctor')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(data['error']['stage'], 'configuration')
        self.assertNotIn('fixture-password', result.stdout)
        self.assertEqual(self.logged_commands(), [])

    def test_doctor_checks_backend_ok_even_when_process_exits_zero(self):
        self.scenario_set(auth_ok=False)
        result, data = self.run_cli('doctor')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(data['error']['stage'], 'authentication')
        self.assertNotIn('private-auth-error', result.stdout)
        self.assertFalse(self.state.exists())

    def test_unsupported_versions_fail_before_account_access(self):
        self.scenario_set(version='2.2.0')
        result, data = self.run_cli('doctor')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(data['error']['stage'], 'compatibility')
        self.assertEqual(self.logged_commands(), [])

    def test_mutations_sequences_and_arbitrary_backend_flags_are_rejected(self):
        cases = [('send',), ('read','--mailbox','INBOX','7','--seen'),
                 ('read','--mailbox','INBOX','1:*'),
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

    def test_pending_actions_require_review_and_resolve_explicitly(self):
        result, _ = self.run_cli('review','ack','unknown','--outcome','clear')
        self.assertEqual(result.returncode, 1)
        token = self.read()
        result, data = self.run_cli('review','ack',token,'--outcome','needs-attention','--note','Reply to fixture','--due','2026-12-01')
        self.assertEqual(result.returncode, 0, result.stdout)
        _, data = self.run_cli('review','status')
        self.assertEqual(len(data['data']['pending']),1)
        self.assertEqual(data['data']['awaitingReview'],[])
        result, _ = self.run_cli('review','ack',token,'--outcome','clear')
        self.assertEqual(result.returncode,1)
        result, _ = self.run_cli('review','resolve',token)
        self.assertEqual(result.returncode,0)
        _, data = self.run_cli('review','status')
        self.assertEqual(data['data']['pending'],[])

    def test_private_account_namespace_and_corrupt_state_are_not_silently_reused(self):
        token = self.read()
        self.backend_config.write_text(self.backend_config.read_text().replace('login@example.invalid','another@example.invalid'))
        result, _ = self.run_cli('review','ack',token,'--outcome','clear')
        self.assertEqual(result.returncode,1)
        path=self.state/'reviews.json'
        path.write_text('{broken')
        before=path.read_bytes()
        result,data=self.run_cli('review','status')
        self.assertEqual(result.returncode,1)
        self.assertEqual(data['error']['stage'],'state')
        self.assertEqual(path.read_bytes(),before)

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


class MailInstallationTests(MailFixture, unittest.TestCase):
    def test_install_update_preserves_private_config_and_survives_checkout_move(self):
        checkout=self.directory/'checkout'
        shutil.copytree(ROOT,checkout,ignore=shutil.ignore_patterns('.git','__pycache__','.DS_Store'))
        home=self.directory/'home'; skills=home/'.codex/skills'
        command=[sys.executable,'-B',str(checkout/'install_mail.py'),'--home',str(home),'--skills-dir',str(skills),
                 '--himalaya',str(self.fake),'--himalaya-config',str(self.backend_config)]
        backend_before=self.backend_config.read_bytes()
        result=subprocess.run(command,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        config=home/'.config/icloud-utils/mail.toml'
        config.write_text(config.read_text().replace('timeout_seconds = 30','timeout_seconds = 11'))
        private_before=config.read_bytes()
        result=subprocess.run(command,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(config.read_bytes(),private_before)
        self.assertEqual(self.backend_config.read_bytes(),backend_before)
        self.assertEqual(config.stat().st_mode&0o777,0o600)
        self.assertEqual(os.readlink(home/'bin/icmail'),'icloud-mail')
        self.assertIn('references/mail.md',(skills/'icloud-mail/SKILL.md').read_text())
        self.assertTrue((skills/'icloud-mail/references/mail.md').is_file())
        checkout.rename(self.directory/'removed-checkout')
        result=subprocess.run([home/'bin/icmail','--json','status'],capture_output=True,text=True,env=self.environment,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertFalse(json.loads(result.stdout)['data']['networkChecked'])
        self.assertEqual(self.logged_commands(),[])
        self.assertTrue((skills/'icloud-utils/references/operations.md').is_file())

    def test_install_refuses_unrelated_alias_before_creating_runtime(self):
        home=self.directory/'home'; (home/'bin').mkdir(parents=True)
        alias=home/'bin/icmail'; alias.write_text('unrelated')
        result=subprocess.run([sys.executable,'-B',ROOT/'install_mail.py','--home',home],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,1)
        self.assertEqual(alias.read_text(),'unrelated')
        self.assertFalse((home/'Library').exists())


if __name__ == '__main__':
    unittest.main()
