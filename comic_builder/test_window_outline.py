"""The GNOME Wayland window outline."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMainWindow, QMenu, QWidget

from .window_outline import OUTLINE_COLOR, OutlineInstaller, add_outline, outline_of, wants_outlines


class WindowOutlineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # Owned by the application, as in the real program.
        self.installer = OutlineInstaller(self.app)
        self.app.installEventFilter(self.installer)

    def tearDown(self):
        self.app.removeEventFilter(self.installer)
        self.installer.deleteLater()
        self.app.processEvents()

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
        window.resize(400, 300)
        window.show()
        self.app.processEvents()
        outline = outline_of(window)
        self.assertIsNotNone(outline)
        self.assertEqual(outline.geometry(), window.rect())
        self.assertTrue(outline.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        # The point under the outline belongs to the content, for clicks and drops.
        self.assertIs(window.childAt(QPoint(200, 150)), content)
        # A red box added later over the left edge must not cover the outline.
        late = QWidget(window)
        late.setGeometry(0, 0, 60, 60)
        late.setStyleSheet("background: red;")
        late.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
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
        window.deleteLater()
        self.app.processEvents()

    def test_windows_and_dialogs_get_one_outline_but_menus_do_not(self):
        window, dialog, menu = QMainWindow(), QDialog(), QMenu()
        frameless = QWidget(None, Qt.WindowType.FramelessWindowHint)
        widgets = (window, dialog, menu, frameless)
        for widget in widgets:
            widget.show()
        window.hide()
        window.show()
        add_outline(window)  # Asking twice must not add a second outline.
        self.app.processEvents()
        named = lambda widget: [child for child in widget.findChildren(QWidget, "windowOutline")]
        self.assertEqual(len(named(window)), 1)
        self.assertEqual(len(named(dialog)), 1)
        self.assertEqual(named(menu), [])
        self.assertEqual(named(frameless), [])
        for widget in widgets:
            widget.close()
            widget.deleteLater()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
