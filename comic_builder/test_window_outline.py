"""The GNOME Wayland window outline."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMainWindow, QMenu, QWidget

from .window_outline import OutlineInstaller, WindowOutline, wants_outlines


class WindowOutlineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_only_gnome_on_wayland_gets_outlines(self):
        self.assertTrue(wants_outlines("wayland", {"XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}))
        self.assertTrue(wants_outlines("wayland", {"XDG_CURRENT_DESKTOP": "GNOME"}))
        self.assertFalse(wants_outlines("wayland", {"XDG_CURRENT_DESKTOP": "KDE"}))
        self.assertFalse(wants_outlines("xcb", {"XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}))
        self.assertFalse(wants_outlines("windows", {}))
        self.assertFalse(wants_outlines("offscreen", {}))

    def test_outline_covers_the_window_stays_on_top_and_lets_clicks_through(self):
        window = QMainWindow()
        try:
            window.setCentralWidget(QLabel("content"))
            outline = WindowOutline(window)
            window.resize(400, 300)
            window.show()
            self.app.processEvents()
            self.assertEqual(outline.geometry(), window.rect())
            self.assertTrue(outline.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
            # A widget added later must not end up above the outline.
            late = QWidget(window)
            late.show()
            self.app.processEvents()
            self.assertIs(window.children()[-1], outline)
            # The point under the outline belongs to the content, for clicks and drops.
            self.assertIsNot(window.childAt(QPoint(200, 150)), outline)
            window.resize(500, 350)
            self.app.processEvents()
            self.assertEqual(outline.geometry(), window.rect())
        finally:
            window.close()

    def test_installer_outlines_windows_and_dialogs_once_but_not_menus(self):
        installer = OutlineInstaller()
        self.app.installEventFilter(installer)
        window, dialog, menu = QMainWindow(), QDialog(), QMenu()
        frameless = QWidget(None, Qt.WindowType.FramelessWindowHint)
        try:
            for widget in (window, dialog, menu, frameless):
                widget.show()
            window.hide(); window.show()
            self.app.processEvents()
            direct = Qt.FindChildOption.FindDirectChildrenOnly
            self.assertEqual(len(window.findChildren(WindowOutline, options=direct)), 1)
            self.assertEqual(len(dialog.findChildren(WindowOutline, options=direct)), 1)
            self.assertEqual(menu.findChildren(WindowOutline), [])
            self.assertEqual(frameless.findChildren(WindowOutline), [])
        finally:
            self.app.removeEventFilter(installer)
            for widget in (window, dialog, menu, frameless):
                widget.close()


if __name__ == "__main__":
    unittest.main()
