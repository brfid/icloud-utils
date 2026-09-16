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

    def test_default_fetch_verifies_every_file_without_hashing(self):
        result, data = self.run_cli('fetch', '--json', '--reserve-mib', '0', '--', str(self.root))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data['counts']['regularFiles'], 4)
        self.assertEqual(data['counts']['filesVerified'], 4)
        self.assertEqual(data['counts']['readBytes'], sum(path.stat().st_size for path in [
            self.root/'local.txt', self.root/'.hidden', self.root/'empty',
            self.root/'nested/App.pkg/Contents/data']))
        self.assertEqual(data['counts']['hashedFiles'], 0)
        self.assertEqual(data['hashes'], [])

    def test_download_ahead_usage_bounds_and_check_rejection(self):
        for value in ['0', '17', '-1', 'abc', '1.5']:
            with self.subTest(value=value):
                result, data = self.run_cli('fetch', '--json', '--download-ahead', value, '--', str(self.root))
                self.assertEqual(result.returncode, 2)
                self.assertEqual(data['failures'][0]['stage'], 'usage')
        for value in ['1', '16']:
            with self.subTest(value=value):
                result, data = self.run_cli('fetch', '--json', '--download-ahead', value,
                                            '--reserve-mib', '0', '--', str(self.root))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(data['success'])
        result, data = self.run_cli('check', '--json', '--download-ahead', '1', '--', str(self.root))
        self.assertEqual(result.returncode, 2)
        self.assertIn('only valid with fetch', data['failures'][0]['explanation'])

    def test_bounded_request_ahead_default_and_serial_one(self):
        for index in range(8):
            (self.root/f'window-{index:02d}').write_bytes(f'payload {index}'.encode())
        for selected in [None, 1, 4, 16]:
            with self.subTest(download_ahead=selected):
                trace = self.root.parent/(self.root.name+f'-trace-{selected}')
                trace.write_text('')
                self.addCleanup(trace.unlink)
                options = [] if selected is None else ['--download-ahead', str(selected)]
                result, data = self.run_cli(
                    'fetch', '--json', '--verbose', '--reserve-mib', '0', *options, '--', str(self.root),
                    env={'ICMAT_TEST_FRESHNESS': 'stale', 'ICMAT_TEST_FAKE_DOWNLOAD_REQUEST': '1',
                         'ICMAT_TEST_FETCH_TRACE': str(trace)})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(data['counts']['downloadRequests'], 12)
                self.assertEqual(data['counts']['filesVerified'], 12)
                self.assertEqual(data['counts']['hashedFiles'], 0)
                self.assertEqual(data['hashes'], [])
                self.assertEqual(result.stdout.count('"schemaVersion"'), 1)
                for phrase in ['Inventory ', 'Explicit download request',
                               'Awaiting verification read', 'Verification progress']:
                    self.assertIn(phrase, result.stderr)
                events = [json.loads(line) for line in trace.read_text().splitlines()]
                window = selected or 4
                self.assertLessEqual(max(event['pending'] for event in events), window)
                self.assertEqual(events[-1]['pending'], 0)
                active_reads = 0
                for event in events:
                    if event['event'] == 'verification-start':
                        active_reads += 1
                    elif event['event'] == 'verification-complete':
                        active_reads -= 1
                    self.assertIn(active_reads, (0, 1))
                self.assertEqual(active_reads, 0)
                first_complete = next(index for index, event in enumerate(events)
                                      if event['event'] == 'verification-complete')
                requests_before_first_complete = [event for event in events[:first_complete]
                                                  if event['event'] == 'request']
                self.assertEqual(len(requests_before_first_complete), min(window, 12))
                if window == 1:
                    self.assertEqual([event['event'] for event in events],
                                     ['request', 'verification-start', 'verification-complete'] * 12)

    def test_failed_verification_does_not_free_request_window(self):
        for index in range(8):
            (self.root/f'window-{index:02d}').write_bytes(f'payload {index}'.encode())
        result, data = self.run_cli(
            'fetch', '--json', '--download-ahead', '4', '--reserve-mib', '0', '--', str(self.root),
            env={'ICMAT_TEST_FRESHNESS': 'stale', 'ICMAT_TEST_FAKE_DOWNLOAD_REQUEST': '1',
                 'ICMAT_TEST_FILE_CHANGE_FAILURE': '1'})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(data['counts']['downloadRequests'], 4)
        self.assertEqual(data['counts']['filesVerified'], 0)

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

    def test_missing_and_stale_freshness_are_not_reported_current(self):
        for state in ['unknown', 'stale']:
            result, data = self.run_cli('check', '--json', '--', str(self.root),
                                        env={'ICMAT_TEST_FRESHNESS': state})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(data['freshness'][state], 4)
            self.assertEqual(data['freshness']['current'], 0)

    def test_concurrent_truncation_is_retried_and_not_accepted_as_unchanged(self):
        changing = self.root/'0-changing.bin'
        changing.write_bytes(b'x' * (1024 * 1024))
        marker = self.root.parent/(self.root.name+'-read-open')
        environment = dict(
            os.environ,
            ICMAT_TEST_READ_MARKER=str(marker),
            ICMAT_TEST_READ_MARKER_SUFFIX=changing.name,
            ICMAT_TEST_READ_DELAY_MILLIS='500',
        )
        process = subprocess.Popen(
            [BINARY, 'fetch', '--json', '--reserve-mib', '0', '--sha256', '--', str(self.root)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment)
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(marker.exists(), 'worker did not reach the open-file test point')
        changing.write_bytes(b'short after truncation\n')
        stdout, stderr = process.communicate(timeout=10)
        marker.unlink(missing_ok=True)
        self.assertEqual(process.returncode, 0, stderr)
        data = json.loads(stdout)
        hashes = {item['path']: item['sha256'] for item in data['hashes']}
        self.assertEqual(hashes[str(changing)], hashlib.sha256(changing.read_bytes()).hexdigest())
        self.assertEqual(data['counts']['filesVerified'], data['counts']['regularFiles'])

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
        self.assertRegex(alias.stdout, r'^icloud-materialize 1\.1\.0')
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
