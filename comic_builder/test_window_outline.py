"""The GNOME Wayland window outline."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMargins, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from .window_outline import OUTLINE_COLOR, OutlinedDialog, prefer_xwayland_on_gnome, wants_outlines


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

    def test_gnome_wayland_asks_for_xwayland_with_a_fallback(self):
        gnome = {"WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}
        environ = dict(gnome)
        self.assertTrue(prefer_xwayland_on_gnome(environ, "linux"))
        self.assertEqual(environ["QT_QPA_PLATFORM"], "xcb;wayland")

    def test_xwayland_is_not_forced_elsewhere_or_against_a_choice(self):
        gnome = {"WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}
        cases = [
            (dict(gnome), "win32"),
            ({"WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "KDE"}, "linux"),
            ({"DISPLAY": ":0", "XDG_CURRENT_DESKTOP": "GNOME"}, "linux"),
            (dict(gnome, QT_QPA_PLATFORM="wayland"), "linux"),
            (dict(gnome, COMIC_STITCHER_NATIVE_WAYLAND="1"), "linux"),
        ]
        for environ, platform in cases:
            before = dict(environ)
            self.assertFalse(prefer_xwayland_on_gnome(environ, platform), environ)
            self.assertEqual(environ, before)

    def test_elsewhere_windows_are_unchanged(self):
        dialog = OutlinedDialog()
        self.assertEqual(dialog.contentsMargins(), QMargins(0, 0, 0, 0))
        self.assertFalse(dialog._outlined)
        dialog.deleteLater()

    def test_outline_is_drawn_at_the_edge_and_contents_stay_inside_it(self):
        dialog = OutlinedDialog()
        dialog._setup_outline(enabled=True)
        content = QWidget()
        content.setStyleSheet("background: red;")
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(content)
        dialog.resize(200, 120)
        dialog.show()
        self.app.processEvents()
        image = dialog.grab().toImage()
        red = QColor("red").name()
        self.assertEqual(image.pixelColor(0, 60).name(), OUTLINE_COLOR.name())
        self.assertEqual(image.pixelColor(199, 60).name(), OUTLINE_COLOR.name())
        self.assertEqual(image.pixelColor(100, 119).name(), OUTLINE_COLOR.name())
        self.assertEqual(image.pixelColor(1, 60).name(), red)
        self.assertEqual(image.pixelColor(100, 60).name(), red)
        dialog.close()
        dialog.deleteLater()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
