"""Exercise the native CLI contract and its debug-only failure injection."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest


BINARY = os.environ.get('ICMAT_TEST_BINARY')


@unittest.skipUnless(BINARY and Path(BINARY).is_file(), 'set ICMAT_TEST_BINARY to a built debug executable')
class MaterializerCLI(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root/'nested/App.pkg/Contents').mkdir(parents=True)
        (self.root/'empty-dir').mkdir()
        (self.root/'local.txt').write_bytes(b'local payload\n')
        (self.root/'.hidden').write_bytes(b'hidden\n')
        (self.root/'empty').write_bytes(b'')
        (self.root/'nested/App.pkg/Contents/data').write_bytes(b'package data\n')
        (self.root/'link').symlink_to('local.txt')

    def tearDown(self):
        self.temporary.cleanup()

    def run_cli(self, *arguments, env=None, timeout=15):
        environment = dict(os.environ)
        if env:
            environment.update(env)
        result = subprocess.run([BINARY, *arguments], capture_output=True, text=True,
                                env=environment, timeout=timeout)
        return result, json.loads(result.stdout) if result.stdout else None

    def test_check_inventory_overlapping_root_dedup_and_root_symlink(self):
        alias = self.root.parent/(self.root.name+'-alias')
        alias.symlink_to(self.root, target_is_directory=True)
        self.addCleanup(alias.unlink)
        result, data = self.run_cli('check', '--json', '--reserve-mib', '0', '--',
                                    str(alias), str(self.root/'nested'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(data['success'])
        self.assertTrue(data['enumerationComplete'])
        self.assertEqual(data['counts']['rootsRequested'], 2)
        self.assertEqual(data['counts']['rootsResolved'], 1)
        self.assertEqual(data['counts']['regularFiles'], 4)
        self.assertEqual(data['counts']['symlinksSkipped'], 1)
        self.assertEqual(data['counts']['readBytes'], 0)
        self.assertEqual(data['counts']['downloadRequests'], 0)

    def test_fetch_reads_and_hashes_every_local_file_and_rerun_makes_no_requests(self):
        expected = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in [self.root/'local.txt', self.root/'.hidden', self.root/'empty',
                                 self.root/'nested/App.pkg/Contents/data']}
        for _ in range(2):
            result, data = self.run_cli('fetch', '--json', '--reserve-mib', '0', '--sha256', '--', str(self.root))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(data['success'])
            self.assertEqual(data['counts']['filesVerified'], 4)
            self.assertEqual(data['counts']['hashedFiles'], 4)
            self.assertEqual(data['counts']['downloadRequests'], 0)
            self.assertEqual({item['path']: item['sha256'] for item in data['hashes']}, expected)

    def test_check_reports_simulated_dataless_without_request_or_read(self):
        result, data = self.run_cli('check', '--json', '--reserve-mib', '0', '--', str(self.root),
                                    env={'ICMAT_TEST_DATALESS_SUFFIX': 'local.txt'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data['counts']['datalessFiles'], 1)
        self.assertEqual(data['datalessFiles'][0]['path'], str(self.root/'local.txt'))
        self.assertEqual(data['counts']['downloadRequests'], 0)
        self.assertEqual(data['counts']['readBytes'], 0)

    def test_missing_root_and_guarded_icloud_ancestor_are_clean_json_failures(self):
        missing, missing_data = self.run_cli('check', '--json', '--', str(self.root/'absent'))
        self.assertEqual(missing.returncode, 1)
        self.assertFalse(missing_data['success'])
        self.assertEqual(missing_data['failures'][0]['stage'], 'resolve-root')
        guarded, guarded_data = self.run_cli('check', '--json', '--', str(Path.home()))
        self.assertEqual(guarded.returncode, 1)
        self.assertIn('iCloud Drive root', guarded_data['failures'][0]['explanation'])

    def test_usage_failure_is_one_json_object(self):
        result, data = self.run_cli('check', '--json', '--sha256', '--', str(self.root))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(data['failures'][0]['stage'], 'usage')
        self.assertEqual(result.stdout.count('"schemaVersion"'), 1)

    def test_resolution_enumeration_and_read_timeouts(self):
        for stage in ['resolution', 'enumeration', 'read']:
            result, data = self.run_cli(
                'fetch' if stage == 'read' else 'check', '--json', '--timeout', '0.2',
                '--deadline', '1', '--reserve-mib', '0', '--', str(self.root),
                env={'ICMAT_TEST_BLOCK_STAGE': stage}, timeout=5)
            self.assertEqual(result.returncode, 1)
            self.assertTrue(data['timedOut'])
            self.assertFalse(data['success'])

    def test_interrupt_returns_json_and_signal_status(self):
        environment = dict(os.environ, ICMAT_TEST_BLOCK_STAGE='enumeration')
        process = subprocess.Popen(
            [BINARY, 'check', '--json', '--timeout', '10', '--deadline', '20', '--', str(self.root)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment)
        time.sleep(.3)
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 130, stderr)
        self.assertEqual(json.loads(stdout)['interruptedBySignal'], signal.SIGINT)

    def test_policy_and_space_failures_are_visible(self):
        policy, policy_data = self.run_cli('check', '--json', '--', str(self.root),
                                           env={'ICMAT_TEST_POLICY_FAILURE': '1'})
        self.assertEqual(policy.returncode, 1)
        self.assertEqual(policy_data['failures'][0]['stage'], 'io-policy')
        space, space_data = self.run_cli('fetch', '--json', '--reserve-mib', '1', '--', str(self.root),
                                         env={'ICMAT_TEST_FREE_BYTES': '0'})
        self.assertEqual(space.returncode, 1)
        self.assertEqual(space_data['failures'][0]['stage'], 'space-preflight')

    def test_space_loss_during_work_and_download_failure(self):
        injected = {'ICMAT_TEST_DATALESS_SUFFIX': 'local.txt', 'ICMAT_TEST_SPACE_DURING': '1'}
        space, space_data = self.run_cli('fetch', '--json', '--reserve-mib', '0', '--', str(self.root), env=injected)
        self.assertEqual(space.returncode, 1)
        self.assertTrue(any(item['stage'] == 'download-request' for item in space_data['failures']))
        failed, failed_data = self.run_cli(
            'fetch', '--json', '--reserve-mib', '0', '--', str(self.root),
            env={'ICMAT_TEST_DATALESS_SUFFIX': 'local.txt', 'ICMAT_TEST_DOWNLOAD_FAILURE': '1'})
        self.assertEqual(failed.returncode, 1)
        self.assertGreater(failed_data['counts']['downloadRequestFailures'], 0)

    def test_special_file_is_inventoried_and_never_read(self):
        fifo = self.root/'fifo'
        os.mkfifo(fifo)
        result, data = self.run_cli('fetch', '--json', '--reserve-mib', '0', '--', str(self.root))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(data['counts']['specialFilesUnsupported'], 1)
        self.assertTrue(any(item['stage'] == 'unsupported-special-file' for item in data['failures']))

    def test_hard_links_are_accounted_and_each_path_is_verified(self):
        os.link(self.root/'local.txt', self.root/'hard-link.txt')
        result, data = self.run_cli('fetch', '--json', '--reserve-mib', '0', '--sha256', '--', str(self.root))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data['counts']['regularFiles'], 5)
        self.assertEqual(data['counts']['uniqueFileObjects'], 4)
        self.assertEqual(data['counts']['filesVerified'], 5)
        hashes = {item['path']: item['sha256'] for item in data['hashes']}
        self.assertEqual(hashes[str(self.root/'local.txt')], hashes[str(self.root/'hard-link.txt')])

    def test_metadata_refusal_and_unstable_inventory_fail_explicitly(self):
        denied, denied_data = self.run_cli(
            'check', '--json', '--', str(self.root),
            env={'ICMAT_TEST_ENUMERATION_FAILURE_SUFFIX': 'nested'})
        self.assertEqual(denied.returncode, 1)
        self.assertFalse(denied_data['enumerationComplete'])
        self.assertTrue(any(item['stage'] == 'enumerate-directory' and item['path'].endswith('/nested')
                            for item in denied_data['failures']))
        unstable, unstable_data = self.run_cli(
            'check', '--json', '--', str(self.root), env={'ICMAT_TEST_FORCE_UNSTABLE': '1'})
        self.assertEqual(unstable.returncode, 1)
        self.assertTrue(any(item['stage'] == 'inventory-stability' for item in unstable_data['failures']))

    def test_file_change_exhausting_retry_prevents_verification(self):
        result, data = self.run_cli(
            'fetch', '--json', '--reserve-mib', '0', '--', str(self.root),
            env={'ICMAT_TEST_FILE_CHANGE_FAILURE': '1'})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(data['counts']['filesVerified'], 0)
        self.assertTrue(any(item['stage'] == 'coordinated-read' and 'replacement' in item['explanation']
                            for item in data['failures']))

    def test_legacy_placeholder_is_reported_without_being_modified(self):
        placeholder = self.root/'.old.pdf.icloud'
        placeholder.write_bytes(b'legacy-marker')
        before = placeholder.read_bytes()
        checked, check_data = self.run_cli('check', '--json', '--', str(self.root))
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(check_data['counts']['legacyPlaceholders'], 1)
        fetched, fetch_data = self.run_cli('fetch', '--json', '--reserve-mib', '0', '--', str(self.root))
        self.assertEqual(fetched.returncode, 1)
        self.assertTrue(any(item['stage'] == 'legacy-placeholder' for item in fetch_data['failures']))
        self.assertEqual(placeholder.read_bytes(), before)

    def test_missing_freshness_is_not_reported_current(self):
        result, data = self.run_cli('check', '--json', '--', str(self.root),
                                    env={'ICMAT_TEST_FRESHNESS': 'unknown'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data['freshness']['unknown'], 4)
        self.assertEqual(data['freshness']['current'], 0)

    def test_installer_and_alias_work_in_temporary_destination(self):
        prefix = self.root/'commands'
        installer = Path(__file__).resolve().parents[1]/'scripts/install-materializer'
        installed = subprocess.run([installer, '--prefix', prefix, '--binary', BINARY],
                                   capture_output=True, text=True)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertEqual((prefix/'icloud-materialize').stat().st_ino,
                         (prefix/'icmat').resolve().stat().st_ino)
        alias = subprocess.run([prefix/'icmat', '--version'], capture_output=True, text=True)
        self.assertEqual(alias.returncode, 0, alias.stderr)
        self.assertRegex(alias.stdout, r'^icloud-materialize 1\.0\.0')
        removed = subprocess.run([installer, '--prefix', prefix, '--uninstall'],
                                 capture_output=True, text=True)
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertFalse((prefix/'icloud-materialize').exists())
        self.assertFalse((prefix/'icmat').exists())

    def test_installer_refuses_unrelated_alias(self):
        prefix = self.root/'commands'; prefix.mkdir()
        (prefix/'icmat').write_text('unrelated')
        installer = Path(__file__).resolve().parents[1]/'scripts/install-materializer'
        result = subprocess.run([installer, '--prefix', prefix, '--binary', BINARY],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('unrelated alias', result.stderr)


if __name__ == '__main__':
    unittest.main()
