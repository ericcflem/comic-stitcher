"""Behaviour of the screen-edge drop tab widget."""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, QSettings, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu

from .drop_tab import (DropTab, DropTabControl, DropTabSettingsDialog, MenuAutoClose, local_paths,
                       mouse_position_unreliable)
from .drop_tab_support import DEFAULT_THICKNESS


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
        # By default the slim tab is as tall as the panel it opens into.
        self.assertEqual((self.tab.width(), self.tab.height()),
                         (DEFAULT_THICKNESS, self.tab.expanded_size()[1]))
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

    def test_mouse_returning_after_a_drop_does_not_leave_it_stuck_open(self):
        self.tab.opening_timer.setInterval(30)
        data = mime_for("/comics/one.pdf")
        drag_enter(self.tab, data)
        drop(self.tab, data)
        with patch.object(self.tab, "_pointer_inside", lambda: False):
            # Opening a right-click menu and dismissing it sends enter and leave events.
            self.tab.enterEvent(None)
            self.tab.leaveEvent(None)
            self.tab.enterEvent(None)
            self.assertTrue(wait_for(lambda: not self.tab.expanded, seconds=2))
        self.assertFalse(self.tab.opening)

    def test_open_panel_closes_when_pointer_is_gone_even_without_a_leave(self):
        self.tab._expand()
        with patch.object(self.tab, "_pointer_inside", lambda: False):
            self.assertTrue(wait_for(lambda: not self.tab.expanded, seconds=2))

    def test_open_panel_stays_while_pointer_is_over_it(self):
        self.tab._expand()
        with patch.object(self.tab, "_pointer_inside", lambda: True):
            self.assertFalse(wait_for(lambda: not self.tab.expanded, seconds=1))

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
        # Half again as wide as its widest line or the icon, whatever the fonts.
        from PySide6.QtGui import QFontMetrics
        from .drop_tab import HINT_TEXT, ICON_SIZE, MAIN_TEXT, OPENING_TEXT
        metrics = QFontMetrics(self.tab.font())
        widest = max([ICON_SIZE, QFontMetrics(self.tab._hint_font()).horizontalAdvance(HINT_TEXT)]
                     + [metrics.horizontalAdvance(line) for line in MAIN_TEXT.split("\n") + [OPENING_TEXT]])
        width, height = self.tab.expanded_size()
        self.assertEqual(width, round(widest * 1.5))
        self.assertGreater(height, ICON_SIZE + 2 * metrics.lineSpacing())

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
        self.tab.apply_settings(thickness=20, height=300, hover_delay=900, colour="gray")
        dialog = DropTabSettingsDialog(self.tab)
        dialog.reset()
        self.assertEqual((self.tab.thickness, self.tab.tab_height, self.tab.hover_delay, self.tab.colour_mode),
                         (8, self.tab.expanded_size()[1], 450, "accent"))
        dialog.deleteLater()

    def test_gray_colour_is_saved_and_used(self):
        from PySide6.QtGui import QColor
        from .drop_tab import TAB_GRAY
        self.assertEqual(self.tab.tab_colour(), self.tab.palette().highlight().color())
        self.tab.apply_settings(colour="gray")
        self.assertEqual(self.tab.tab_colour(), QColor(TAB_GRAY))
        self.assertEqual(self.settings.value("drop_tab/color"), "gray")
        reopened = DropTab(self.settings, opener=lambda paths: True)
        self.assertEqual(reopened.colour_mode, "gray")
        reopened.deleteLater()

    def test_a_saved_height_is_kept(self):
        self.tab.apply_settings(height=120)
        reopened = DropTab(self.settings, opener=lambda paths: True)
        self.assertEqual(reopened.tab_height, 120)
        reopened.deleteLater()

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


class XWaylandTests(unittest.TestCase):
    """Under XWayland the mouse position goes stale over native Wayland windows."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_xwayland_is_recognised(self):
        self.assertTrue(mouse_position_unreliable("xcb", {"WAYLAND_DISPLAY": "wayland-0"}))
        self.assertFalse(mouse_position_unreliable("xcb", {"DISPLAY": ":0"}))
        self.assertFalse(mouse_position_unreliable("wayland", {"WAYLAND_DISPLAY": "wayland-0"}))
        self.assertFalse(mouse_position_unreliable("windows", {}))

    def test_leaving_closes_the_panel_even_when_the_position_is_stale(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = QSettings(str(Path(folder) / "settings.ini"), QSettings.Format.IniFormat)
            tab = DropTab(settings, opener=lambda paths: True)
            tab.show()
            tab.mouse_position_unreliable = True
            tab._expand()
            tab.enterEvent(None)
            stale = tab.frameGeometry().center()  # The last place XWayland saw the mouse.
            with patch("comic_stitcher.drop_tab.QCursor.pos", return_value=stale):
                self.assertFalse(wait_for(lambda: not tab.expanded, seconds=0.6))  # Still hovered.
                tab.leaveEvent(None)
                self.assertTrue(wait_for(lambda: not tab.expanded, seconds=2))
            tab.close()
            tab.deleteLater()

    def test_menu_closes_after_the_pointer_leaves_it(self):
        menu = QMenu()
        menu.addAction("Something")
        closer = MenuAutoClose(menu)
        closer.AFTER_LEAVING_MS = 20
        menu.popup(QPoint(10, 10))
        self.assertTrue(wait_for(menu.isVisible))
        QApplication.sendEvent(menu, QEvent(QEvent.Type.Enter))
        self.assertFalse(wait_for(lambda: not menu.isVisible(), seconds=0.3))
        QApplication.sendEvent(menu, QEvent(QEvent.Type.Leave))
        self.assertTrue(wait_for(lambda: not menu.isVisible()))
        menu.deleteLater()

    def test_menu_closes_if_the_pointer_never_reaches_it(self):
        menu = QMenu()
        menu.addAction("Something")
        closer = MenuAutoClose(menu)
        menu.aboutToShow.emit()  # As when the menu opens.
        self.assertTrue(closer.timer.isActive())
        self.assertEqual(closer.timer.interval(), MenuAutoClose.IF_NEVER_ENTERED_MS)
        QApplication.sendEvent(menu, QEvent(QEvent.Type.Enter))
        self.assertFalse(closer.timer.isActive())
        menu.deleteLater()


class ControlTests(unittest.TestCase):
    def test_second_tab_is_refused_and_quit_message_arrives(self):
        app = QApplication.instance() or QApplication([])
        name = "cs-droptab-test-" + uuid4().hex[:8]
        first = DropTabControl(name)
        second = DropTabControl(name)
        quits, shows = [], []
        first.quit_requested.connect(lambda: quits.append(True))
        first.show_requested.connect(lambda: shows.append(True))
        try:
            if not first.claim():
                self.skipTest("This execution environment blocks local sockets")
            self.assertFalse(second.claim())
            # Send from another thread, as another program would: on Windows a pipe
            # write only completes once the listening side reads it.
            for send, received in ((DropTabControl.send_quit, quits), (DropTabControl.send_show, shows)):
                results = []
                sender = threading.Thread(target=lambda send=send, results=results: results.append(send(name)))
                sender.start()
                deadline = time.monotonic() + 5
                while (sender.is_alive() or not received) and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(.005)
                sender.join(1)
                self.assertEqual(received, [True], f"message not received; sender returned {results}")
                self.assertEqual(results, [True])
        finally:
            first.close()
            for control in (first, second):
                control.deleteLater()
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
