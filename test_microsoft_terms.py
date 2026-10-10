"""Fail-closed agreement/version binding checks."""
import json
import tempfile
import unittest
from pathlib import Path
from shutil import copyfile

from verify_microsoft_terms import ROOT, verify_terms


class MicrosoftTermsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        record = json.loads((ROOT / 'installer/microsoft-terms-review.json').read_text())
        for name in ['installer/microsoft-terms-review.json', *record['files']]:
            destination = self.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            copyfile(ROOT / name, destination)

    def test_retained_inputs_match(self):
        self.assertEqual(verify_terms(self.root)['runtime_version'], '14.51.36247.0')

    def test_changed_agreement_requires_review(self):
        path = self.root / 'assets/licenses/microsoft/Runtime-LICENSE.txt'
        path.write_text(path.read_text() + '\nUnreviewed change\n')
        with self.assertRaisesRegex(ValueError, 'Retained terms/evidence changed'):
            verify_terms(self.root)

    def test_different_installer_cannot_use_retained_terms(self):
        path = self.root / 'different.exe'
        path.write_bytes(b'MZ different prerequisite')
        with self.assertRaisesRegex(ValueError, 'fresh terms review'):
            verify_terms(self.root, path)


if __name__ == '__main__':
    unittest.main()
