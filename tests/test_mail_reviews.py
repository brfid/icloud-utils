"""Keep fetched receipts distinct from validated, explicit review outcomes."""
import json
import unittest
from support.mail import MailFixture


class MailReviewTests(MailFixture, unittest.TestCase):
    def test_semantically_corrupt_ledgers_are_rejected_and_preserved(self):
        self.read()
        path = self.state / 'reviews.json'
        original = json.loads(path.read_text())
        key = next(iter(original['entries']))
        changes = [('uid', '0'), ('uidValidity', 0), ('uidValidity', True), ('scope', 'bad'),
                   ('contentSha256', 'bad'), ('token', 'bad'), ('mailbox', ''), ('account', ''),
                   ('fetchedAt', 'yesterday'), ('fetchedAt', '2026-10-09T00:00:00'),
                   ('reviewedAt', '2026-10-09T00:00:00+00:00'),
                   ('outcome', 'clear'), ('note', []), ('due', '2026-02-30'), ('resolvedAt', True)]
        invalid_ledgers = []
        for field, value in changes:
            data = json.loads(json.dumps(original))
            data['entries'][key][field] = value
            invalid_ledgers.append(data)
        data = json.loads(json.dumps(original))
        data['schemaVersion'] = True
        invalid_ledgers.append(data)
        data = json.loads(json.dumps(original))
        data['entries']['wrong-key'] = data['entries'].pop(key)
        invalid_ledgers.append(data)
        data = json.loads(json.dumps(original))
        extra = dict(data['entries'][key], uid='8')
        import icloud_mail
        data['entries'][icloud_mail.Ledger.key(extra['scope'], extra['mailbox'], extra['uidValidity'], extra['uid'])] = extra
        invalid_ledgers.append(data)
        for data in invalid_ledgers:
            with self.subTest(data=data):
                path.write_text(json.dumps(data))
                before = path.read_bytes()
                for arguments in (('review', 'status'), ('review', 'ack', 'unknown', '--outcome', 'clear')):
                    result, report = self.run_cli(*arguments)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertEqual(report['error']['stage'], 'state')
                    self.assertEqual(path.read_bytes(), before)

    def test_duplicate_json_fields_and_shared_or_symlinked_ledgers_are_preserved(self):
        self.read()
        path = self.state / 'reviews.json'
        original = path.read_bytes()
        path.write_text('{"schemaVersion": 1, "entries": {}, "entries": {}}')
        duplicate = path.read_bytes()
        result, data = self.run_cli('review', 'status')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(data['error']['stage'], 'state')
        self.assertEqual(path.read_bytes(), duplicate)
        path.write_bytes(original)
        path.chmod(0o644)
        result, data = self.run_cli('review', 'status')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(data['error']['stage'], 'state')
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        self.assertEqual(path.read_bytes(), original)
        path.chmod(0o600)
        retained = path.with_name('retained.json')
        path.rename(retained)
        path.symlink_to(retained)
        result, data = self.run_cli('read', '--mailbox', 'INBOX', '7')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(data['error']['stage'], 'state')
        self.assertTrue(path.is_symlink())
        self.assertEqual(retained.read_bytes(), original)

    def test_review_dates_and_outcome_fields_are_validated_before_state_changes(self):
        token = self.read()
        before = (self.state / 'reviews.json').read_bytes()
        for extra in (('--due', '20261201'), ('--due', '2026-02-30'), ('--due', ''), ('--note', '  ')):
            result, data = self.run_cli('review', 'ack', token, '--outcome', 'clear', *extra)
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertEqual(data['error']['stage'], 'usage')
            self.assertEqual((self.state / 'reviews.json').read_bytes(), before)

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



if __name__ == '__main__':
    unittest.main()
