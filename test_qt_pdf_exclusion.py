"""Packaging regression checks; no GUI or Windows runtime required."""
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from build_policy import validate_qt_bundle


class ExclusionTests(unittest.TestCase):
    def test_hook_keeps_image_plugins_and_filters_unused_plugins(self):
        root = Path(__file__).resolve().parent
        names = ["qpdf.dll", "qpdfd.dll", "libqpdf.so", "libqpdf.dylib",
                 "qtvirtualkeyboardplugin.dll", "qjpeg.dll", "qwebp.dll", "qsvg.dll"]
        standard = (["PySide6.QtCore"], [(name, "plugins") for name in names], [])
        with patch("PyInstaller.utils.hooks.qt.add_qt6_dependencies", return_value=standard):
            result = runpy.run_path(str(root / "build_hooks/hook-PySide6.QtGui.py"))
        self.assertEqual(result["binaries"], [(name, "plugins") for name in names[-3:]])
        self.assertEqual(result["hiddenimports"], standard[0])

    def test_bundle_guard_rejects_pdf_even_if_plugin_is_reintroduced(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "Qt6Core.dll").touch()
            validate_qt_bundle(root)
            for name in ("qpdf.dll", "qpdfd.dll", "Qt6Pdf.dll", "Qt6PdfWidgets.dll", "Qt6WebEngineCore.dll"):
                item = root / name
                item.touch()
                with self.assertRaises(RuntimeError):
                    validate_qt_bundle(root)
                item.unlink()

if __name__ == "__main__":
    unittest.main()
