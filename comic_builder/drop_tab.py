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
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QLockFile, QObject, QProcess, QRectF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QIcon, QPainter, QPainterPath, QPen
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from shiboken6 import isValid
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QMenu,
    QMessageBox, QPushButton, QSlider, QSpinBox, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from .drop_tab_support import (
    DEFAULT_HEIGHT, DEFAULT_OFFSET, DEFAULT_THICKNESS, ENABLE_AUTOSTART_FLAG, HOVER_DELAY_MS,
    HOVER_RANGE, MIN_HEIGHT, THICKNESS_RANGE, UNINSTALL_FLAG, autostart_enabled,
    autostart_supported, clamp_height, clamp_hover_delay, clamp_offset, clamp_thickness,
    launch_command, max_height, needs_x11, offset_range, set_autostart, tab_rect,
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
OPENING_SHOWN_MS = 900  # How long "Opening…" shows after a drop.
WATCH_INTERVAL_MS = 400  # How often an open panel checks the pointer is still over it.
QUIT_MESSAGE = b"quit\n"
SHOW_MESSAGE = b"show\n"


def mouse_position_unreliable(platform_name=None, environ=None):
    """True for an X11 window on a Wayland desktop (XWayland).

    There the mouse position is only updated while the pointer is over an
    XWayland window; over native Wayland windows and the desktop it stays at
    the last place it was seen. Enter and leave events are still delivered.
    """
    platform_name = QGuiApplication.platformName() if platform_name is None else platform_name
    environ = os.environ if environ is None else environ
    return platform_name == "xcb" and bool(environ.get("WAYLAND_DISPLAY"))


class MenuAutoClose(QObject):
    """Closes a menu after the pointer has left it, for XWayland.

    There a menu cannot see clicks on native Wayland windows, so clicking
    elsewhere would not close it.
    """
    AFTER_LEAVING_MS = 700
    IF_NEVER_ENTERED_MS = 2500

    def __init__(self, menu, parent=None):
        super().__init__(parent)
        self.menu = menu
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(menu.close)
        menu.aboutToShow.connect(lambda: self.timer.start(self.IF_NEVER_ENTERED_MS))
        menu.aboutToHide.connect(self.timer.stop)
        menu.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Enter:
            self.timer.stop()
        elif event.type() == QEvent.Type.Leave:
            self.timer.start(self.AFTER_LEAVING_MS)
        return False


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
        self.sockets = []

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
            self.sockets.append(socket)
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(lambda s=socket: self._discard(s))
            self._read(socket)

    def _discard(self, socket):
        if socket in self.sockets:
            self.sockets.remove(socket)
        if isValid(socket):
            socket.deleteLater()

    def _read(self, socket):
        if not isValid(socket):
            return
        message = bytes(socket.readAll())
        if message.startswith(QUIT_MESSAGE.strip()):
            self.quit_requested.emit()
        elif message.startswith(SHOW_MESSAGE.strip()):
            self.show_requested.emit()

    def close(self):
        # Detach and close connections first; left for Qt to delete with the server,
        # they report "disconnected" while half-deleted, which crashes on Linux.
        for socket in self.sockets:
            if isValid(socket):
                for signal in (socket.readyRead, socket.disconnected):
                    try:
                        signal.disconnect()
                    except (RuntimeError, TypeError):
                        pass
                socket.abort()
                socket.deleteLater()
        self.sockets.clear()
        self.server.close()
        self.lock.unlock()

    @staticmethod
    def send_quit(name=None):
        return DropTabControl.send(QUIT_MESSAGE, name)

    @staticmethod
    def send_show(name=None):
        return DropTabControl.send(SHOW_MESSAGE, name)

    @staticmethod
    def send(message, name=None, seconds=3):
        """Send a message to the running tab, retrying while it starts up."""
        from PySide6.QtCore import QCoreApplication
        socket = QLocalSocket()
        deadline = time.monotonic() + seconds
        while True:
            socket.connectToServer(name or control_name())
            if socket.waitForConnected(250):
                break
            socket.abort()
            if time.monotonic() >= deadline:
                return False
            if QCoreApplication.instance() is not None:
                QCoreApplication.processEvents()
            time.sleep(.05)
        written = socket.write(message) == len(message)
        socket.waitForBytesWritten(1000)  # Its result is unreliable on Windows pipes.
        # Closing waits for anything still pending, so the message is not lost if
        # this process exits straight away (as the uninstaller does).
        socket.disconnectFromServer()
        if socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
            socket.waitForDisconnected(1000)
        return written


class DropTab(QWidget):
    def __init__(self, settings=None, opener=open_in_comic_stitcher, parent=None):
        # TEMPORARY test switch for Linux, to find which window settings stop drops
        # under XWayland: COMIC_STITCHER_TAB_STYLE=a (default), b or c.
        style = os.environ.get("COMIC_STITCHER_TAB_STYLE", "a").strip().lower()
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        if style in ("a", "b"):
            flags |= Qt.WindowType.Tool
        if style == "a":
            flags |= Qt.WindowType.WindowDoesNotAcceptFocus
        super().__init__(parent, flags)
        self.setWindowTitle("Comic Stitcher drop tab")
        if style == "a":
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        if style in ("a", "b"):
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAcceptDrops(True)
        self.setToolTip("Drag comics, PDFs or folders here to open them in Comic Stitcher.\n"
                        "Drag up or down to move. Right-click for options.")
        self.settings = settings or QSettings("ComicStitcher", "Comic Stitcher")
        self.opener = opener
        self.thickness = clamp_thickness(self.settings.value("drop_tab/thickness", DEFAULT_THICKNESS))
        self.tab_height = clamp_height(self.settings.value("drop_tab/height", DEFAULT_HEIGHT), 1 << 30)
        self.hover_delay = clamp_hover_delay(self.settings.value("drop_tab/hover_delay", HOVER_DELAY_MS))
        self.offset = self._stored_offset()
        self.anchor_y = None
        self.expanded = False
        self.hovered = False
        self.opening = False
        self.press_y = None
        self.press_offset = self.offset
        self.moved = False
        self.collapse_timer = QTimer(self)
        self.collapse_timer.setSingleShot(True)
        self.collapse_timer.timeout.connect(self._collapse)
        self.opening_timer = QTimer(self)
        self.opening_timer.setSingleShot(True)
        self.opening_timer.setInterval(OPENING_SHOWN_MS)
        self.opening_timer.timeout.connect(self._opening_done)
        # A safety net: an open panel closes once the pointer is gone, even if a
        # "mouse left" signal was missed (menus and other windows can swallow it).
        self.watch_timer = QTimer(self)
        self.watch_timer.setInterval(WATCH_INTERVAL_MS)
        self.watch_timer.timeout.connect(self._watch_pointer)
        self.drag_over = False
        self.mouse_position_unreliable = mouse_position_unreliable()
        self.menu_closer = None
        self.hover_timer = QTimer(self)
        self.hover_timer.setSingleShot(True)
        self.hover_timer.setInterval(self.hover_delay)
        self.hover_timer.timeout.connect(lambda: self._expand(QCursor.pos().y()))
        self.menu = None
        # Decode the icon now so the first reveal is not slowed by loading it.
        app_icon().pixmap(ICON_SIZE, ICON_SIZE)
        app = QGuiApplication.instance()
        app.primaryScreenChanged.connect(self._watch_screen)
        self._screen = None
        self._watch_screen(QGuiApplication.primaryScreen())

    # Placement -----------------------------------------------------------
    def _stored_offset(self):
        return clamp_offset(self.settings.value("drop_tab/offset", DEFAULT_OFFSET), 1 << 30, self.tab_height)

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
        height = clamp_height(self.tab_height, available[3])
        self.offset = clamp_offset(self.offset, available[3], height)
        self.setGeometry(*tab_rect(available, self.offset, self.expanded, self.expanded_size(),
                                   (self.thickness, height), self.anchor_y))
        self.update()

    def _expand(self, anchor_y=None):
        """Open the panel; on a tall tab it opens where the pointer is."""
        self.anchor_y = anchor_y
        self._set_expanded(True)

    def apply_settings(self, thickness=None, height=None, offset=None, hover_delay=None):
        """Change the tab's shape or timing, save it, and redraw it straight away."""
        if thickness is not None:
            self.thickness = clamp_thickness(thickness)
            self.settings.setValue("drop_tab/thickness", self.thickness)
        if height is not None:
            self.tab_height = clamp_height(height, self.available()[3])
            self.settings.setValue("drop_tab/height", self.tab_height)
        if offset is not None:
            self.offset = int(offset)
        if hover_delay is not None:
            self.hover_delay = clamp_hover_delay(hover_delay)
            self.hover_timer.setInterval(self.hover_delay)
            self.settings.setValue("drop_tab/hover_delay", self.hover_delay)
        self.place()
        if offset is not None or height is not None:
            self.settings.setValue("drop_tab/offset", self.offset)

    def _set_expanded(self, expanded):
        self.hover_timer.stop()
        if expanded:
            self.watch_timer.start()
        else:
            self.watch_timer.stop()
        if self.expanded != expanded:
            self.expanded = expanded
            self.place()

    def _pointer_inside(self):
        if self.mouse_position_unreliable:
            return self.hovered  # Under XWayland, trust enter/leave events instead.
        return self.frameGeometry().contains(QCursor.pos())

    def _collapse(self):
        if self.opening:
            return  # "Opening…" finishes on its own timer.
        if self.expanded and self._pointer_inside():
            # Windows can report a "leave" when the panel grows under the pointer,
            # most noticeably on the first reveal. Keep it open and check again shortly.
            self.collapse_timer.start(300)
            return
        self._set_expanded(False)

    def _opening_done(self):
        self.opening = False
        self.update()
        self._watch_pointer()

    def _watch_pointer(self):
        if not self.expanded:
            self.watch_timer.stop()
            return
        if self.opening or self.drag_over or self.press_y is not None:
            return
        if not self._pointer_inside():
            self.collapse_timer.stop()
            self._set_expanded(False)

    # Dragging files ------------------------------------------------------
    def dragEnterEvent(self, event):
        if local_paths(event.mimeData()):
            event.acceptProposedAction()
            self.collapse_timer.stop()
            self.opening_timer.stop()
            self.opening = False
            self.drag_over = True
            if not self.expanded:
                self._expand(self.mapToGlobal(event.position().toPoint()).y())
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if local_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.drag_over = False
        # A short delay absorbs the leave/enter pair some platforms send when the tab grows.
        self.collapse_timer.start(150)

    def dropEvent(self, event):
        paths = local_paths(event.mimeData())
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        self.drag_over = False
        self.opening = True
        self.update()
        # Windows Explorer waits for dropEvent to return before ending its drag.
        QTimer.singleShot(0, lambda: self.open_paths(paths))
        # Not tied to mouse movement, so nothing the pointer does can cancel it.
        self.opening_timer.start()

    def hide_tab(self):
        for timer in (self.hover_timer, self.collapse_timer, self.opening_timer, self.watch_timer):
            timer.stop()
        self.opening = False
        self.drag_over = False
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
            available_height = self.available()[3]
            self.offset = clamp_offset(self.press_offset + int(delta), available_height,
                                       clamp_height(self.tab_height, available_height))
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
            self._expand(event.globalPosition().y())

    def enterEvent(self, event):
        self.hovered = True
        self.collapse_timer.stop()
        if not self.expanded:
            self.hover_timer.start()
        self.update()

    def leaveEvent(self, event):
        self.hovered = False
        self.hover_timer.stop()
        if self.expanded:
            # Growing the panel can send a brief leave/enter pair; the delay absorbs it.
            self.collapse_timer.start(300)
        self.update()

    def contextMenuEvent(self, event):
        if self.menu is not None:
            if self.mouse_position_unreliable and self.menu_closer is None:
                self.menu_closer = MenuAutoClose(self.menu, self)
            self.menu.exec(event.globalPos())
            self._watch_pointer()  # Close straight away if the menu was dismissed elsewhere.

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
            radius = self.thickness / 2
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


class SliderField(QWidget):
    """A slider with a matching number box, kept in step."""

    def __init__(self, low, high, value, suffix, on_change, parent=None):
        super().__init__(parent)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.box = QSpinBox()
        self.box.setSuffix(suffix)
        for control in (self.slider, self.box):
            control.setRange(low, high)
            control.setValue(value)
        self.slider.setMinimumWidth(220)
        self.slider.valueChanged.connect(self.box.setValue)
        self.box.valueChanged.connect(self.slider.setValue)
        self.box.valueChanged.connect(on_change)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.box)

    def value(self):
        return self.box.value()

    def set_range(self, low, high):
        for control in (self.slider, self.box):
            control.blockSignals(True)
            control.setRange(low, high)
            control.blockSignals(False)

    def set_value(self, value):
        self.box.setValue(value)


class DropTabSettingsDialog(QDialog):
    """Changes apply to the real tab as you move the sliders."""

    def __init__(self, tab, parent=None):
        super().__init__(parent)
        self.tab = tab
        self.setWindowTitle("Drop tab settings")
        self.setWindowIcon(app_icon())
        available_height = tab.available()[3]
        height = clamp_height(tab.tab_height, available_height)
        self.thickness = SliderField(*THICKNESS_RANGE, tab.thickness, " px",
                                     lambda v: tab.apply_settings(thickness=v))
        self.height_field = SliderField(MIN_HEIGHT, max_height(available_height), height, " px",
                                        self._height_changed)
        self.position = SliderField(*offset_range(available_height, height), tab.offset, " px",
                                    lambda v: tab.apply_settings(offset=v))
        self.delay = SliderField(*HOVER_RANGE, tab.hover_delay, " ms",
                                 lambda v: tab.apply_settings(hover_delay=v))
        self.delay.slider.setSingleStep(50)
        self.delay.box.setSingleStep(50)
        form = QFormLayout()
        form.addRow("How far it sticks out:", self.thickness)
        form.addRow("Height:", self.height_field)
        form.addRow("Distance from the top:", self.position)
        form.addRow("Open on hover after:", self.delay)
        note = QLabel("The tab stays clear of the top and bottom of the screen so it does not cover "
                      "window controls or the taskbar. You can also drag the tab to move it.")
        note.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        reset = QPushButton("Reset to defaults")
        buttons.addButton(reset, QDialogButtonBox.ButtonRole.ResetRole)
        reset.clicked.connect(self.reset)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def _height_changed(self, value):
        self.tab.apply_settings(height=value)
        # A taller tab has less room to move; keep the position slider honest.
        self.position.set_range(*offset_range(self.tab.available()[3], self.tab.tab_height))
        self.position.set_value(self.tab.offset)

    def reset(self):
        self.thickness.set_value(DEFAULT_THICKNESS)
        self.height_field.set_value(DEFAULT_HEIGHT)
        self.position.set_value(DEFAULT_OFFSET)
        self.delay.set_value(HOVER_DELAY_MS)


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
    settings_action = QAction("Drop tab settings…", menu)

    def show_settings():
        if not tab.isVisible():
            tab.show_tab()
        DropTabSettingsDialog(tab).exec()

    settings_action.triggered.connect(show_settings)
    menu.addAction(settings_action)
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
