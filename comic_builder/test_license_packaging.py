import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import sys
from unittest.mock import patch

# The packaging scripts are in packaging/, beside comic_builder.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
import package_licenses  # noqa: E402


class LicensePackagingTests(unittest.TestCase):
    def test_public_documents_refresh_without_shipping_internal_records(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder)
            (destination / 'licenses').mkdir()
            (destination / 'STATUS.md').write_text('Old private release record')
            (destination / 'LICENSE_AUDIT.md').write_text('Old coverage summary')
            (destination / 'SOURCE_ACCESS.md').write_text('Old guide location')
            (destination / 'licenses/RELEASE-LICENSE-REVIEW.md').write_text('Old duplicate')
            supplier = destination / 'licenses/supplier-license.txt'
            supplier.write_text('Unmodified supplier terms')
            package_licenses.copy_public_documents(destination)
            self.assertFalse((destination / 'STATUS.md').exists())
            self.assertFalse((destination / 'SOURCE_ACCESS.md').exists())
            self.assertFalse((destination / 'LICENSE_AUDIT.md').exists())
            self.assertFalse((destination / 'licenses/RELEASE-LICENSE-REVIEW.md').exists())
            self.assertEqual((destination / 'docs/SOURCE_ACCESS.md').read_bytes(),
                             (package_licenses.ROOT / 'docs/SOURCE_ACCESS.md').read_bytes())
            self.assertTrue((destination / 'docs/START_HERE.md').is_file())
            self.assertTrue((destination / 'docs/THIRD_PARTY_ACKNOWLEDGEMENTS.md').is_file())
            self.assertTrue((destination / 'licenses/SOURCE-AND-LICENSES.txt').is_file())
            self.assertEqual(supplier.read_text(), 'Unmodified supplier terms')

    def test_runtime_closure_ignores_unselected_extras(self):
        def dist(name):
            return SimpleNamespace(metadata={'Name': name}, requires={
                'root': ['child>=1', 'optional; extra == "unused"'],
                'child': ['root>=1'],
            }.get(name, []))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'requirements.txt'
            path.write_text('root>=1\n')
            with patch.object(package_licenses.metadata, 'distribution', side_effect=dist):
                found = package_licenses.runtime_distributions(path)
            self.assertEqual(set(found), {'root', 'child'})

    def test_runtime_closure_skips_other_platforms_requirements(self):
        import sys
        other = 'linux' if sys.platform == 'win32' else 'win32'
        def dist(name):
            if name == 'elsewhere':
                raise AssertionError('a requirement for another platform was looked up')
            return SimpleNamespace(metadata={'Name': name}, requires=[])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'requirements.txt'
            path.write_text(f'root>=1\nelsewhere>=1; sys_platform == "{other}"\n'
                            f'here>=1; sys_platform == "{sys.platform}"\n')
            with patch.object(package_licenses.metadata, 'distribution', side_effect=dist):
                found = package_licenses.runtime_distributions(path)
            self.assertEqual(set(found), {'root', 'here'})

    def test_notices_include_nested_native_notices_and_keep_index_local(self):
        from pathlib import PurePosixPath
        self.assertTrue(package_licenses.notice_member(PurePosixPath('wheel/licenses/native/libjpeg.txt')))
        self.assertTrue(package_licenses.notice_member(PurePosixPath('package/NOTICE')))
        self.assertFalse(package_licenses.notice_member(PurePosixPath('package/example.py')))
        self.assertFalse(package_licenses.notice_member(PurePosixPath('packaging/licenses/__init__.py')))
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            (target / 'unrar').mkdir()
            (target / 'unrar/license.txt').write_text('Matched notice')
            (target / 'build-inventory.json').write_text(json.dumps({'packages': [
                {'name': 'Library <name>', 'version': '1', 'license': 'BSD'}]}))
            package_licenses.write_index(target)
            index = (target / 'index.html').read_text()
            self.assertIn('href="unrar/license.txt"', index)
            self.assertIn('Library &lt;name&gt;', index)

    def test_missing_dependency_notice_blocks_collection(self):
        missing = SimpleNamespace(files=[], metadata={'Name': 'missing'}, requires=[])
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(package_licenses, 'runtime_distributions', return_value={'missing': missing}):
                with self.assertRaisesRegex(RuntimeError, 'No license notice'):
                    package_licenses.collect(Path(folder))

    def test_qt_bundle_policy_rejects_virtual_keyboard_and_unreviewed_modules(self):
        from build_policy import validate_qt_bundle
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'Qt6Core.dll').write_bytes(b'test')
            validate_qt_bundle(root)
            (root / 'Qt6VirtualKeyboard.dll').write_bytes(b'test')
            with self.assertRaisesRegex(RuntimeError, 'Qt6VirtualKeyboard'):
                validate_qt_bundle(root)
            (root / 'Qt6VirtualKeyboard.dll').unlink()
            (root / 'Qt6FutureModule.dll').write_bytes(b'test')
            with self.assertRaisesRegex(RuntimeError, 'FutureModule'):
                validate_qt_bundle(root)

    def test_qt_hook_filters_plugin_before_dependency_collection(self):
        import runpy
        root = Path(package_licenses.__file__).parent
        standard = (['PySide6.QtCore'], [('qtvirtualkeyboardplugin.dll', 'plugins/platforminputcontexts'),
                                       ('qsvg.dll', 'plugins/imageformats')], [('data', '.')])
        with patch('PyInstaller.utils.hooks.qt.add_qt6_dependencies', return_value=standard):
            result = runpy.run_path(str(root / 'build_hooks/hook-PySide6.QtGui.py'))
        self.assertEqual(result['hiddenimports'], standard[0])
        self.assertEqual(result['datas'], standard[2])
        self.assertEqual(result['binaries'], [('qsvg.dll', 'plugins/imageformats')])

    def test_embedded_runtime_notices_are_collected_and_indexed(self):
        from pathlib import PurePosixPath
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / 'COPYING.txt'; source.write_text('Matching embedded-runtime license')
            def dist(name):
                return SimpleNamespace(files=[PurePosixPath('licenses/COPYING.txt')],
                                       locate_file=lambda _: source, version='1.2',
                                       metadata={'Name': name, 'License': 'GPL with exception / Apache'})
            destination = root / 'dist'; destination.mkdir()
            with patch.object(package_licenses, 'runtime_distributions', return_value={}), patch.object(
                    package_licenses.metadata, 'distribution', side_effect=dist), patch.object(
                    package_licenses.metadata, 'version', return_value='1.2'):
                package_licenses.collect(destination)
            data = json.loads((destination / 'licenses/build-inventory.json').read_text())
            # Two build tools that may embed runtime code, plus the Microsoft prerequisite.
            self.assertEqual([tool['name'] for tool in data['runtime_tool_notices']],
                             ['pyinstaller', 'pyinstaller-hooks-contrib',
                              'Microsoft Visual C++ x64 prerequisite'])
            self.assertFalse(data['release_clearance'])
            self.assertEqual(data['application_license']['id'], 'MIT')
            self.assertEqual((destination / 'LICENSE').read_bytes(), (package_licenses.ROOT / 'LICENSE').read_bytes())
            self.assertTrue((destination / 'docs/PROVENANCE.md').is_file())
            self.assertTrue((destination / 'licenses/ComicStitcher-MIT.txt').is_file())
            self.assertTrue((destination / 'licenses/THIRD_PARTY_ACKNOWLEDGEMENTS.md').is_file())
            index = (destination / 'licenses/index.html').read_text()
            self.assertIn('href="ComicStitcher-MIT.txt"', index)
            for tool in data['runtime_tool_notices']:
                self.assertIn(tool['name'], index)
                for notice in tool['notices']:
                    self.assertTrue((destination / 'licenses' / notice).is_file())

    def test_frozen_helpers_are_resolved_even_when_not_runtime_requirements(self):
        fake = SimpleNamespace(metadata={'Name': 'setuptools'})
        archive = SimpleNamespace(open_embedded_archive=lambda _: SimpleNamespace(
            toc={'setuptools._vendor.jaraco.context': None, 'comic_builder.gui': None,
                 'launch': None, 'json': None, 'unknown_helper': None}))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'ComicStitcher.exe').write_bytes(b'fake executable')
            with patch('PyInstaller.archive.readers.CArchiveReader', return_value=archive), patch.object(
                    package_licenses.metadata, 'packages_distributions', return_value={'setuptools': ['setuptools']}), patch.object(
                    package_licenses.metadata, 'distribution', return_value=fake):
                found, evidence = package_licenses.frozen_distributions(root)
            self.assertEqual(set(found), {'setuptools'})
            self.assertEqual(evidence['unmapped_roots'], ['unknown_helper'])
            self.assertTrue(evidence['inspected'])

    def test_frozen_vendor_license_is_copied_and_indexed(self):
        from pathlib import PurePosixPath
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            notice = root / 'LICENSE'; notice.write_text('Vendor copyright and full terms')
            member = PurePosixPath('setuptools/_vendor/jaraco_context-6.1.0.dist-info/licenses/LICENSE')
            dist = SimpleNamespace(files=[member], locate_file=lambda _: notice,
                                   version='84', metadata={'Name': 'setuptools', 'License': 'MIT'}, read_text=lambda _: '')
            destination = root / 'dist'; destination.mkdir()
            with patch.object(package_licenses, 'runtime_distributions', return_value={}), patch.object(
                    package_licenses, 'frozen_distributions', return_value=({'setuptools': dist},
                    {'inspected': True, 'modules': ['setuptools._vendor.jaraco.context'], 'unmapped_roots': []})), patch.object(
                    package_licenses.metadata, 'distribution', return_value=dist), patch.object(
                    package_licenses.metadata, 'version', return_value='84'):
                package_licenses.collect(destination)
            expected = destination / 'licenses/setuptools' / member
            self.assertEqual(expected.read_text(), 'Vendor copyright and full terms')
            self.assertIn(expected.relative_to(destination / 'licenses').as_posix(),
                          (destination / 'licenses/index.html').read_text())

    def test_native_notices_reject_version_mismatch_and_record_runtime_copies(self):
        import native_notices
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); destination = root / 'dist'; destination.mkdir()
            target = root / 'licenses'; target.mkdir()
            (destination / 'Qt6Core.dll').write_bytes(b'fake Qt')
            with self.assertRaisesRegex(RuntimeError, 'Audit the new version'):
                native_notices.collect_native_notices(destination, target, '6.12.0')
            (destination / 'libssl-3.dll').write_bytes(b'fake OpenSSL')
            with patch.object(native_notices, 'pe_version', return_value='3.0.19'):
                with self.assertRaisesRegex(RuntimeError, 'OpenSSL notice'):
                    native_notices.collect_native_notices(destination, target, '6.11.2')
            (destination / 'libssl-3.dll').unlink()
            (destination / 'VCRUNTIME140.dll').write_bytes(b'fake runtime')
            with patch.object(native_notices, 'pe_version', return_value='14.42'):
                data = native_notices.collect_native_notices(destination, target, '6.11.2')
            self.assertEqual(data['microsoft_runtimes'][0]['version'], '14.42')
            self.assertFalse(data['microsoft_runtimes'][0]['redistribution_basis_verified'])
