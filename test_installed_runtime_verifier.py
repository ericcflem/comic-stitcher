"""Regression tests for installer-generated files and failure reports."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import package_shared_runtime
import verify_installed_runtime


class InstalledRuntimeTests(unittest.TestCase):
    def test_only_root_inno_exe_with_companion_dat_is_allowed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'unins000.dat').write_bytes(b'installer data')
            (root / 'subfolder').mkdir()
            (root / 'subfolder/unins001.dat').write_bytes(b'installer data')
            actual = {'unins000.exe': 'hash', 'unins002.exe': 'hash',
                      'other.exe': 'hash', 'subfolder/unins001.exe': 'hash'}
            self.assertEqual(package_shared_runtime.installer_generated_files(root, actual),
                             {'unins000.exe'})

    def test_native_failure_still_compares_payload_and_writes_report(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / 'result.json'
            def compare(_folder, _record, target):
                target.write_text(json.dumps({'passed': True, 'checked_files': 620}))
            with patch.object(package_shared_runtime, 'validate', side_effect=ValueError('changed binary')), \
                    patch.object(verify_installed_runtime.installer_evidence, 'compare', side_effect=compare) as comparison:
                result = verify_installed_runtime.verify(root, root / 'input.json', output)
            self.assertFalse(result['passed'])
            self.assertTrue(output.is_file())
            self.assertEqual(comparison.call_count, 1)
            self.assertEqual(result['checks']['shared_runtime']['error'], 'changed binary')
            self.assertTrue(result['checks']['payload']['passed'])

    def test_missing_record_still_writes_report(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / 'result.json'
            with patch.object(package_shared_runtime, 'validate', return_value={}), \
                    patch.object(package_shared_runtime, 'binaries', return_value={}):
                result = verify_installed_runtime.verify(root, root / 'missing.json', output)
            self.assertFalse(result['passed'])
            self.assertTrue(output.is_file())
            self.assertIn('error', result['checks']['payload'])


if __name__ == '__main__':
    unittest.main()
