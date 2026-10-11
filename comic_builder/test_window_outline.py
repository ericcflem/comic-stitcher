"""The GNOME Wayland window outline."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMainWindow, QMenu, QWidget

from .window_outline import OUTLINE_COLOR, OutlineInstaller, WindowOutline, wants_outlines


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
        content = QLabel("content")
        window.setCentralWidget(content)
        outline = WindowOutline(window)
        window.resize(400, 300)
        window.show()
        self.app.processEvents()
        self.assertEqual(outline.geometry(), window.rect())
        self.assertTrue(outline.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        # The point under the outline belongs to the content, for clicks and drops.
        self.assertIs(window.childAt(QPoint(200, 150)), content)
        # A red box added later over the left edge must not cover the outline.
        late = QWidget(window)
        late.setGeometry(0, 0, 60, 60)
        late.setAutoFillBackground(True)
        palette = late.palette()
        palette.setColor(late.backgroundRole(), QColor("red"))
        late.setPalette(palette)
        late.show()
        self.app.processEvents()
        image = window.grab().toImage()
        self.assertEqual(image.pixelColor(0, 30).name(), OUTLINE_COLOR.name())
        self.assertEqual(image.pixelColor(30, 30).name(), QColor("red").name())
        window.resize(500, 350)
        self.app.processEvents()
        self.assertEqual(outline.geometry(), window.rect())
        window.showMaximized()
        self.app.processEvents()
        self.assertFalse(outline.isVisible())
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
