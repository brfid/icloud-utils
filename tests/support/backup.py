"""Private temporary source/store configuration for backup contract tests."""
import contextlib
import io
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from support import ROOT
import icloud_backup as ib

RESTIC = shutil.which('restic')


class BackupFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.source = self.root/'source'; self.source.mkdir()
        (self.source/'version.txt').write_text('first version\n')
        self.c = ib.Config(self.root/'backups', (ib.Source('source', self.source, ('.git','*.git','.DS_Store')),),
                           state_dir=self.root/'state', cache_dir=self.root/'cache', flag_path=self.root/'warning.txt',
                           restic=RESTIC or '/missing/restic', reserve_mib=0)
        self.app = ib.App(self.c)
        self.alerts = mock.patch.object(ib, 'notify')
        self.alerts.start()
        self.stdout = contextlib.redirect_stdout(io.StringIO()); self.stdout.__enter__()

    def tearDown(self):
        self.stdout.__exit__(None,None,None)
        self.alerts.stop()
        self.tmp.cleanup()
