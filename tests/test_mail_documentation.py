"""Validate portable examples and documentation without accessing an account."""
import json
from pathlib import Path
import re
import tempfile
import tomllib
import unittest

from support import ROOT
from icloud_mail import Config
from support.documentation import local_link_errors


class MailDocumentationTests(unittest.TestCase):
    def test_source_links_and_anchors_resolve(self):
        paths = [ROOT / 'README.md', ROOT / 'AGENTS.md', *sorted((ROOT / 'docs').glob('*.md')),
                 *sorted((ROOT / 'skills').glob('*/SKILL.md'))]
        self.assertEqual(local_link_errors(paths), [])

    def test_portable_account_and_routing_examples_are_accepted_without_credentials(self):
        example = (ROOT / 'example.himalaya.toml').read_text()
        accounts = tomllib.loads(example)
        with tempfile.TemporaryDirectory(prefix='icloud-mail-docs-') as temporary:
            directory = Path(temporary)
            backend = directory / 'himalaya.toml'
            backend.write_text(example)
            routing = tomllib.loads((ROOT / 'example.mail.toml').read_text())['mail']
            routing.update(himalaya_config=str(backend), state_dir=str(directory / 'state'))
            path = directory / 'mail.toml'
            path.write_text('[mail]\n' + ''.join(f'{key} = {json.dumps(value)}\n' for key, value in routing.items()))
            config = Config.load(path)
            self.assertEqual(config.account_settings(), accounts['accounts'][routing['account']])
            self.assertEqual(config.mailbox('inbox'), 'INBOX')
            self.assertFalse(config.state_dir.exists())
        blocks = re.findall(r'```toml\n(.*?)```', (ROOT / 'docs/mail.md').read_text(), re.DOTALL)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(tomllib.loads(blocks[0]), accounts)


if __name__ == '__main__':
    unittest.main()
