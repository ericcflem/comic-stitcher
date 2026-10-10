"""A small always-on-top tab on the right screen edge that opens dropped comics.

Run with ``ComicStitcher.exe --drop-tab`` (or ``python launch.py --drop-tab``).
It does not load the main interface; a drop simply starts Comic Stitcher with
the dropped paths, and Comic Stitcher's single-instance handoff passes them to
an open window when there is one.
"""
import hashlib
import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, QProcess, QRectF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QIcon, QPainter, QPainterPath, QPen
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon, QWidget

from .drop_tab_support import (
    COLLAPSED_SIZE, DEFAULT_OFFSET, ENABLE_AUTOSTART_FLAG, HOVER_DELAY_MS, UNINSTALL_FLAG,
    autostart_enabled, autostart_supported, clamp_offset, launch_command, needs_x11,
    set_autostart, tab_rect,
)

CLICK_SLOP = 4
ICON_SIZE = 48
TOP_PAD = 16
ICON_GAP = 8
HINT_GAP = 4
BOTTOM_PAD = 14
MAIN_TEXT = "Drop to open in\nComic Stitcher"
OPENING_TEXT = "Opening…"
HINT_TEXT = "or click to open"
QUIT_MESSAGE = b"quit\n"
SHOW_MESSAGE = b"show\n"


def control_name():
    return "ComicStitcherDropTab-" + hashlib.sha256(str(Path.home()).encode()).hexdigest()[:20]


_ICON = None


def app_icon():
    global _ICON
    if _ICON is not None:
        return _ICON
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    path = root / "comic-stitcher.png"
    if not path.exists():
        path = root / "assets" / "app" / "comic-stitcher.png"
    _ICON = QIcon(str(path))
    return _ICON


def local_paths(mime):
    if mime is None or not mime.hasUrls():
        return []
    return [Path(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()]


def open_in_comic_stitcher(paths=()):
    program, arguments = launch_command(paths)
    working = str(Path(program).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent)
    started = QProcess.startDetached(program, arguments, working)
    # PySide6 returns (ok, pid) for this overload; accept a bare bool too.
    return bool(started[0] if isinstance(started, tuple) else started)


class DropTabControl(QObject):
    """Keeps one drop tab per user and lets the uninstaller close it."""
    quit_requested = Signal()
    show_requested = Signal()

    def __init__(self, name=None, parent=None):
        super().__init__(parent)
        self.name = name or control_name()
        self.lock = QLockFile(str(Path(tempfile.gettempdir()) / (self.name + ".lock")))
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._accept)

    def claim(self):
        if not self.lock.tryLock(0):
            return False
        QLocalServer.removeServer(self.name)
        if not self.server.listen(self.name):
            self.lock.unlock()
            return False
        return True

    def _accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(socket.deleteLater)
            self._read(socket)

    def _read(self, socket):
        message = bytes(socket.readAll())
        if message.startswith(QUIT_MESSAGE.strip()):
            self.quit_requested.emit()
        elif message.startswith(SHOW_MESSAGE.strip()):
            self.show_requested.emit()

    def close(self):
        self.server.close()
        self.lock.unlock()

    @staticmethod
    def send_quit(name=None):
        return DropTabControl.send(QUIT_MESSAGE, name)

    @staticmethod
    def send_show(name=None):
        return DropTabControl.send(SHOW_MESSAGE, name)

    @staticmethod
    def send(message, name=None):
        socket = QLocalSocket()
        socket.connectToServer(name or control_name())
        if not socket.waitForConnected(1000):
            return False
        socket.write(message)
        sent = socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()
        return sent


class DropTab(QWidget):
    def __init__(self, settings=None, opener=open_in_comic_stitcher, parent=None):
        flags = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                 | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        super().__init__(parent, flags)
        self.setWindowTitle("Comic Stitcher drop tab")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAcceptDrops(True)
        self.setToolTip("Drag comics, PDFs or folders here to open them in Comic Stitcher.\n"
                        "Drag up or down to move. Right-click for options.")
        self.settings = settings or QSettings("ComicStitcher", "Comic Stitcher")
        self.opener = opener
        self.offset = self._stored_offset()
        self.expanded = False
        self.hovered = False
        self.opening = False
        self.press_y = None
        self.press_offset = self.offset
        self.moved = False
        self.collapse_timer = QTimer(self)
        self.collapse_timer.setSingleShot(True)
        self.collapse_timer.timeout.connect(self._collapse)
        self.hover_timer = QTimer(self)
        self.hover_timer.setSingleShot(True)
        self.hover_timer.setInterval(HOVER_DELAY_MS)
        self.hover_timer.timeout.connect(lambda: self._set_expanded(True))
        self.menu = None
        # Decode the icon now so the first reveal is not slowed by loading it.
        app_icon().pixmap(ICON_SIZE, ICON_SIZE)
        app = QGuiApplication.instance()
        app.primaryScreenChanged.connect(self._watch_screen)
        self._screen = None
        self._watch_screen(QGuiApplication.primaryScreen())

    # Placement -----------------------------------------------------------
    def _stored_offset(self):
        return clamp_offset(self.settings.value("drop_tab/offset", DEFAULT_OFFSET), 1 << 30)

    def _watch_screen(self, screen):
        if self._screen is not None:
            try:
                self._screen.availableGeometryChanged.disconnect(self.place)
            except (RuntimeError, TypeError):
                pass
        self._screen = screen
        if screen is not None:
            screen.availableGeometryChanged.connect(self.place)
        self.place()

    def available(self):
        screen = self._screen or QGuiApplication.primaryScreen()
        if screen is None:
            return (0, 0, 1280, 720)
        area = screen.availableGeometry()
        return (area.x(), area.y(), area.width(), area.height())

    def _hint_font(self):
        font = QFont(self.font())
        font.setPointSizeF(max(6.0, font.pointSizeF() * 0.85))
        return font

    def expanded_size(self):
        """Fit the panel to its icon and text, plus half again for padding."""
        metrics = QFontMetrics(self.font())
        hint = QFontMetrics(self._hint_font())
        lines = MAIN_TEXT.split("\n") + [OPENING_TEXT]
        content = max([ICON_SIZE, hint.horizontalAdvance(HINT_TEXT)]
                      + [metrics.horizontalAdvance(line) for line in lines])
        height = (TOP_PAD + ICON_SIZE + ICON_GAP + 2 * metrics.lineSpacing()
                  + HINT_GAP + hint.lineSpacing() + BOTTOM_PAD)
        return (int(round(content * 1.5)), int(height))

    def place(self, *_):
        available = self.available()
        self.offset = clamp_offset(self.offset, available[3])
        self.setGeometry(*tab_rect(available, self.offset, self.expanded, self.expanded_size()))
        self.update()

    def _set_expanded(self, expanded):
        self.hover_timer.stop()
        if self.expanded != expanded:
            self.expanded = expanded
            self.place()

    def _pointer_inside(self):
        return self.frameGeometry().contains(QCursor.pos())

    def _collapse(self):
        if self.expanded and not self.opening and self._pointer_inside():
            # Windows can report a "leave" when the panel grows under the pointer,
            # most noticeably on the first reveal. Keep it open and check again shortly.
            self.collapse_timer.start(300)
            return
        self.opening = False
        self._set_expanded(False)

    # Dragging files ------------------------------------------------------
    def dragEnterEvent(self, event):
        if local_paths(event.mimeData()):
            event.acceptProposedAction()
            self.collapse_timer.stop()
            self.opening = False
            self._set_expanded(True)
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if local_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        # A short delay absorbs the leave/enter pair some platforms send when the tab grows.
        self.collapse_timer.start(150)

    def dropEvent(self, event):
        paths = local_paths(event.mimeData())
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        self.opening = True
        self.update()
        # Windows Explorer waits for dropEvent to return before ending its drag.
        QTimer.singleShot(0, lambda: self.open_paths(paths))
        self.collapse_timer.start(900)

    def hide_tab(self):
        self.hover_timer.stop()
        self.collapse_timer.stop()
        self.opening = False
        self.expanded = False
        self.hide()

    def show_tab(self):
        self.expanded = False
        self.place()
        self.show()
        self.raise_()

    def toggle_visible(self):
        if self.isVisible():
            self.hide_tab()
        else:
            self.show_tab()

    def open_paths(self, paths=()):
        if not self.opener(paths):
            QMessageBox.warning(None, "Comic Stitcher", "Comic Stitcher could not be started.")

    # Clicking and moving the tab ----------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.hover_timer.stop()
            self.press_y = event.globalPosition().y()
            self.press_offset = self.offset
            self.moved = False

    def mouseMoveEvent(self, event):
        if self.press_y is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        delta = event.globalPosition().y() - self.press_y
        if abs(delta) > CLICK_SLOP:
            self.moved = True
        if self.moved:
            self.offset = clamp_offset(self.press_offset + int(delta), self.available()[3])
            self.place()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.press_y is None:
            return
        self.press_y = None
        if self.moved:
            self.settings.setValue("drop_tab/offset", self.offset)
        elif self.expanded:
            self.open_paths()
        else:
            # A click on the slim tab shows what it is; a click on the panel opens the app.
            self._set_expanded(True)

    def enterEvent(self, event):
        self.hovered = True
        self.collapse_timer.stop()
        if not self.expanded:
            self.hover_timer.start()
        self.update()

    def leaveEvent(self, event):
        self.hovered = False
        self.hover_timer.stop()
        if self.expanded and not self.opening:
            # Growing the panel can send a brief leave/enter pair; the delay absorbs it.
            self.collapse_timer.start(300)
        self.update()

    def contextMenuEvent(self, event):
        if self.menu is not None:
            self.menu.exec(event.globalPos())

    # Drawing -------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        accent = palette.highlight().color()
        rect = QRectF(self.rect()).adjusted(1, 1, 0, -1)
        if not self.expanded:
            # Rounded on the left only, flush with the screen edge on the right.
            path = QPainterPath()
            radius = COLLAPSED_SIZE[0] / 2
            path.addRoundedRect(rect.adjusted(0, 0, radius, 0), radius, radius)
            colour = QColor(accent)
            colour.setAlpha(255 if self.hovered else 190)
            painter.fillPath(path, colour)
            return
        path = QPainterPath()
        path.addRoundedRect(rect.adjusted(0, 0, 12, 0), 12, 12)
        background = QColor(palette.window().color())
        background.setAlpha(245)
        painter.fillPath(path, background)
        # A smooth solid border: accent while a file is over the tab, quieter otherwise.
        border = QColor(accent) if (self.opening or not self.hovered) else QColor(palette.mid().color())
        painter.setPen(QPen(border, 1.5))
        painter.drawPath(path)
        app_icon().paint(painter, int((self.width() - ICON_SIZE) / 2), TOP_PAD, ICON_SIZE, ICON_SIZE)
        centre = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        text_top = TOP_PAD + ICON_SIZE + ICON_GAP
        line = QFontMetrics(self.font()).lineSpacing()
        painter.setPen(palette.windowText().color())
        painter.drawText(QRectF(0, text_top, self.width(), 2 * line), centre,
                         OPENING_TEXT if self.opening else MAIN_TEXT)
        if self.hovered and not self.opening:
            painter.setFont(self._hint_font())
            painter.setPen(palette.placeholderText().color())
            painter.drawText(QRectF(0, text_top + 2 * line + HINT_GAP, self.width(), line), centre, HINT_TEXT)


def build_menu(tab, app):
    menu = QMenu()
    open_action = QAction("Open Comic Stitcher", menu)
    open_action.triggered.connect(lambda: tab.open_paths())
    menu.addAction(open_action)
    if autostart_supported():
        menu.addSeparator()
        sign_in = QAction("Start when I sign in", menu)
        sign_in.setCheckable(True)

        def refresh():
            sign_in.setChecked(autostart_enabled())

        def toggle(checked):
            try:
                set_autostart(checked)
            except OSError as error:
                QMessageBox.warning(None, "Comic Stitcher", f"Could not change the sign-in setting:\n{error}")
            refresh()

        sign_in.triggered.connect(toggle)
        menu.aboutToShow.connect(refresh)
        menu.addAction(sign_in)
    menu.addSeparator()
    visible_action = QAction("Hide drop tab", menu)
    visible_action.triggered.connect(tab.toggle_visible)
    menu.aboutToShow.connect(lambda: visible_action.setText(
        "Hide drop tab" if tab.isVisible() else "Show drop tab"))
    menu.addAction(visible_action)
    close_action = QAction("Close drop tab", menu)
    close_action.triggered.connect(app.quit)
    menu.addAction(close_action)
    return menu


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if UNINSTALL_FLAG in argv[1:]:
        from PySide6.QtCore import QCoreApplication
        _app = QCoreApplication(argv)
        DropTabControl.send_quit()
        try:
            set_autostart(False)
        except OSError:
            pass
        return 0
    if needs_x11():
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    app = QApplication(argv)
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(app_icon())
    if ENABLE_AUTOSTART_FLAG in argv[1:]:
        try:
            set_autostart(True)
        except OSError:
            pass
    control = DropTabControl()
    if not control.claim():
        # A drop tab is already running for this user; bring it back if it was hidden.
        DropTabControl.send_show()
        return 0
    control.quit_requested.connect(app.quit)
    tab = DropTab()
    control.show_requested.connect(tab.show_tab)
    menu = build_menu(tab, app)
    tab.menu = menu
    tray = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = QSystemTrayIcon(app_icon())
        tray.setToolTip("Comic Stitcher drop tab")
        tray.setContextMenu(menu)
        # Clicking the tray icon hides the tab or brings it back.
        tray.activated.connect(lambda reason: tab.toggle_visible()
                               if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        tray.show()
    tab.show()
    try:
        return app.exec()
    finally:
        if tray is not None:
            tray.hide()
        control.close()
