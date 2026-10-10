"""Behaviour of the screen-edge drop tab widget."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QPoint, QPointF, QSettings, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from .drop_tab import DropTab, DropTabControl, DropTabSettingsDialog, local_paths
from .drop_tab_support import COLLAPSED_SIZE


def mime_for(*urls):
    data = QMimeData()
    data.setUrls([QUrl(url) if "://" in url else QUrl.fromLocalFile(url) for url in urls])
    return data


def drag_enter(tab, data):
    event = QDragEnterEvent(QPoint(2, 10), Qt.DropAction.CopyAction, data,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    tab.dragEnterEvent(event)
    return event


def drop(tab, data):
    event = QDropEvent(QPointF(2, 10), Qt.DropAction.CopyAction, data,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    tab.dropEvent(event)
    return event


def wait_for(predicate, seconds=1.0):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(.005)
    return predicate()


class DropTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.settings = QSettings(str(Path(self.folder.name) / "settings.ini"), QSettings.Format.IniFormat)
        self.opened = []
        self.tab = DropTab(self.settings, opener=lambda paths: self.opened.append(list(paths)) or True)
        self.tab.show()

    def tearDown(self):
        self.tab.close()
        self.tab.deleteLater()
        self.settings.sync()
        self.folder.cleanup()

    def test_only_local_files_are_accepted(self):
        self.assertEqual(local_paths(mime_for("https://example.com/a.cbz")), [])
        self.assertEqual(local_paths(None), [])
        self.assertFalse(drag_enter(self.tab, mime_for("https://example.com/a.cbz")).isAccepted())
        self.assertFalse(self.tab.expanded)

    def test_drag_expands_and_leave_collapses(self):
        self.assertEqual((self.tab.width(), self.tab.height()), COLLAPSED_SIZE)
        self.assertTrue(drag_enter(self.tab, mime_for("/comics/one.cbz")).isAccepted())
        self.assertTrue(self.tab.expanded)
        self.assertEqual((self.tab.width(), self.tab.height()), self.tab.expanded_size())
        self.tab.dragLeaveEvent(None)
        self.assertTrue(wait_for(lambda: not self.tab.expanded))

    def test_drop_opens_every_dropped_path(self):
        data = mime_for("/comics/one.cbz", "/comics/two folder")
        drag_enter(self.tab, data)
        self.assertTrue(drop(self.tab, data).isAccepted())
        self.assertTrue(wait_for(lambda: self.opened))
        self.assertEqual(self.opened, [[Path("/comics/one.cbz"), Path("/comics/two folder")]])

    def test_first_click_explains_second_click_opens(self):
        QTest.mouseClick(self.tab, Qt.MouseButton.LeftButton, pos=QPoint(4, 40))
        self.assertTrue(self.tab.expanded)
        self.assertEqual(self.opened, [])
        QTest.mouseClick(self.tab, Qt.MouseButton.LeftButton, pos=QPoint(20, 40))
        self.assertEqual(self.opened, [[]])

    def test_hover_expands_after_a_delay_and_leaving_collapses(self):
        self.tab.hover_timer.setInterval(30)
        self.tab.enterEvent(None)
        self.assertFalse(self.tab.expanded)
        self.assertTrue(wait_for(lambda: self.tab.expanded))
        self.tab.leaveEvent(None)
        self.assertTrue(wait_for(lambda: not self.tab.expanded))

    def test_stray_leave_while_pointer_is_inside_keeps_it_open(self):
        self.tab.hover_timer.setInterval(10)
        self.tab.enterEvent(None)
        self.assertTrue(wait_for(lambda: self.tab.expanded))
        inside = [True]
        with patch.object(self.tab, "_pointer_inside", lambda: inside[0]):
            self.tab.leaveEvent(None)
            self.assertFalse(wait_for(lambda: not self.tab.expanded, seconds=0.5))
            inside[0] = False
            self.assertTrue(wait_for(lambda: not self.tab.expanded))

    def test_leaving_before_the_delay_keeps_it_slim(self):
        self.tab.hover_timer.setInterval(30)
        self.tab.enterEvent(None)
        self.tab.leaveEvent(None)
        self.assertFalse(wait_for(lambda: self.tab.expanded, seconds=0.2))

    def test_panel_is_fitted_to_its_content(self):
        width, height = self.tab.expanded_size()
        self.assertLess(width, 220)
        self.assertGreater(width, 48 * 1.5 - 1)
        self.assertGreater(height, 48)

    def test_settings_apply_live_and_are_saved(self):
        self.tab.apply_settings(thickness=20, height=300, offset=250, hover_delay=900)
        self.assertEqual((self.tab.width(), self.tab.height()), (20, 300))
        self.assertEqual(self.tab.hover_timer.interval(), 900)
        self.assertEqual(int(self.settings.value("drop_tab/thickness")), 20)
        self.assertEqual(int(self.settings.value("drop_tab/height")), 300)
        self.assertEqual(int(self.settings.value("drop_tab/hover_delay")), 900)
        reopened = DropTab(self.settings, opener=lambda paths: True)
        self.assertEqual((reopened.thickness, reopened.tab_height, reopened.hover_delay), (20, 300, 900))
        reopened.deleteLater()

    def test_settings_dialog_reset_restores_defaults(self):
        self.tab.apply_settings(thickness=20, height=300, hover_delay=900)
        dialog = DropTabSettingsDialog(self.tab)
        dialog.reset()
        self.assertEqual((self.tab.thickness, self.tab.tab_height, self.tab.hover_delay), (8, 96, 450))
        dialog.deleteLater()

    def test_hide_and_show_again(self):
        self.tab.toggle_visible()
        self.assertFalse(self.tab.isVisible())
        self.tab.show_tab()
        self.assertTrue(self.tab.isVisible())
        self.assertFalse(self.tab.expanded)

    def test_dragging_the_tab_moves_and_remembers_it(self):
        start = self.tab.offset
        QTest.mousePress(self.tab, Qt.MouseButton.LeftButton, pos=QPoint(4, 40))
        QTest.mouseMove(self.tab, QPoint(4, 100))
        QTest.mouseRelease(self.tab, Qt.MouseButton.LeftButton, pos=QPoint(4, 100))
        self.assertEqual(self.opened, [])
        self.assertNotEqual(self.tab.offset, start)
        self.assertEqual(int(self.settings.value("drop_tab/offset")), self.tab.offset)


class ControlTests(unittest.TestCase):
    def test_second_tab_is_refused_and_quit_message_arrives(self):
        app = QApplication.instance() or QApplication([])
        name = "cs-droptab-test-" + uuid4().hex[:8]
        first = DropTabControl(name)
        second = DropTabControl(name)
        quits = []
        first.quit_requested.connect(lambda: quits.append(True))
        try:
            if not first.claim():
                self.skipTest("This execution environment blocks local sockets")
            self.assertFalse(second.claim())
            self.assertTrue(DropTabControl.send_quit(name))
            deadline = time.monotonic() + 2
            while not quits and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.005)
            self.assertEqual(quits, [True])
            shows = []
            first.show_requested.connect(lambda: shows.append(True))
            self.assertTrue(DropTabControl.send_show(name))
            deadline = time.monotonic() + 2
            while not shows and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.005)
            self.assertEqual(shows, [True])
        finally:
            first.close()


if __name__ == "__main__":
    unittest.main()
