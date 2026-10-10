"""Check shared deployment primitives independently of component runtimes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from support import ROOT
from installer_support import atomic_text, install_alias, require_python, stable_python


class InstallerSupportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='icloud-installer-test-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()

    def test_interpreter_selection_skips_missing_broken_and_old_candidates(self):
        broken = self.directory / 'not executable'
        broken.write_text('not an interpreter')
        old = self.directory / 'old python'
        old.write_text('#!/bin/sh\n[ "$#" = 2 ] && [ "$1" = -c ] || exit 7\nprintf "0\\n"\n')
        old.chmod(0o700)
        self.assertEqual(stable_python([self.directory / 'missing', broken, old, sys.executable]), sys.executable)
        with self.assertRaisesRegex(RuntimeError, '3.11'):
            stable_python([broken, old])

    def test_default_selection_preserves_the_invoking_python(self):
        with mock.patch('installer_support.subprocess.run', return_value=
                        subprocess.CompletedProcess([], 0, '1\n', '')) as probe:
            self.assertEqual(stable_python(), sys.executable)
        self.assertEqual(probe.call_args.args[0][0], sys.executable)

    def test_old_python_is_rejected_before_component_imports(self):
        with mock.patch('installer_support.sys.version_info', (3, 10)):
            with self.assertRaisesRegex(SystemExit, 'Python 3.11.*Set PYTHON'):
                require_python()

    def test_installer_entry_points_preserve_override_arguments_and_exit_status(self):
        checkout = self.directory / 'checkout with spaces'
        shutil.copytree(ROOT, checkout, ignore=shutil.ignore_patterns('.git', '__pycache__', '.DS_Store'))
        arguments = ['--config', str(self.directory / 'private routing/mail.toml'), '--help']
        for component in ('backup', 'mail'):
            with self.subTest(component=component):
                log = self.directory / (component + '.json')
                interpreter = self.directory / 'selected python'
                interpreter.write_text(
                    '#!' + sys.executable + '\nimport json, pathlib, sys\n'
                    f'pathlib.Path({str(log)!r}).write_text(json.dumps(sys.argv[1:]))\n'
                    'raise SystemExit(7)\n')
                interpreter.chmod(0o700)
                result = subprocess.run([checkout / ('scripts/install-' + component), *arguments],
                                        env=dict(os.environ, PYTHON=str(interpreter)), cwd='/',
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
                self.assertEqual(json.loads(log.read_text()),
                                 ['-B', str(checkout / ('python/install_' + component + '.py')), *arguments])

    def test_interpreter_timeout_allows_the_next_candidate(self):
        with mock.patch('installer_support.subprocess.run', side_effect=[
            subprocess.TimeoutExpired('candidate', 10),
            subprocess.CompletedProcess([], 0, '1\n', '')]):
            self.assertEqual(stable_python([sys.executable, sys.executable]), sys.executable)

    def test_atomic_publication_failure_preserves_previous_content_and_permissions(self):
        path = self.directory / 'private/config.toml'
        atomic_text(path, 'previous')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with mock.patch('installer_support.os.replace', side_effect=OSError('injected publish failure')):
            with self.assertRaises(OSError):
                atomic_text(path, 'replacement', 0o700)
        self.assertEqual(path.read_text(), 'previous')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_alias_preserves_arguments_and_exit_status_and_becomes_relative(self):
        canonical = self.directory / 'command with spaces'
        canonical.write_text('#!/bin/sh\n[ "$#" = 2 ] && [ "$1" = "argument with spaces" ] && [ "$2" = --json ] || exit 99\nexit 7\n')
        canonical.chmod(0o700)
        alias = self.directory / 'short'
        alias.symlink_to(canonical)
        install_alias(alias, canonical)
        self.assertEqual(os.readlink(alias), canonical.name)
        result = subprocess.run([alias, 'argument with spaces', '--json'], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 7)

    def test_alias_collision_preserves_regular_files_and_dangling_links(self):
        canonical = self.directory / 'canonical'
        alias = self.directory / 'alias'
        alias.write_text('unrelated')
        with self.assertRaisesRegex(RuntimeError, 'unrelated'):
            install_alias(alias, canonical)
        self.assertEqual(alias.read_text(), 'unrelated')
        alias.unlink()
        alias.symlink_to('missing-unrelated')
        with self.assertRaisesRegex(RuntimeError, 'unrelated'):
            install_alias(alias, canonical)
        self.assertEqual(os.readlink(alias), 'missing-unrelated')


if __name__ == '__main__':
    unittest.main()
