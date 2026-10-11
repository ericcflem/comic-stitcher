"""Give windows a visible edge on GNOME's Wayland desktop.

There, Qt draws each window's frame itself, and its GNOME-style frame does not
draw a shadow yet and uses an almost-white border, so a window disappears
against a light background. This adds a 1 px gray outline around each
window's content, matching the border Fusion draws around buttons. Other
platforms, including Windows and KDE, draw their own frames and are untouched.
"""
import os

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

OUTLINE_COLOR = QColor(180, 180, 180)  # #b4b4b4, Fusion's button border


def wants_outlines(platform_name=None, environ=None):
    """True on GNOME's Wayland session, where Qt draws a shadowless frame."""
    platform_name = QGuiApplication.platformName() if platform_name is None else platform_name
    environ = os.environ if environ is None else environ
    desktops = environ.get("XDG_CURRENT_DESKTOP", "").upper().split(":")
    return platform_name == "wayland" and "GNOME" in desktops


class WindowOutline(QWidget):
    """A click-through outline drawn on top of one window's content."""

    def __init__(self, window):
        super().__init__(window)
        self.setObjectName("windowOutline")
        # Clicks, drags and drops go straight through to the window underneath.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Owned by the outline, so it can never fire after the window is gone.
        self._refit = QTimer(self)
        self._refit.setSingleShot(True)
        self._refit.setInterval(0)
        self._refit.timeout.connect(self.fit)
        window.installEventFilter(self)
        self.fit()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.WindowStateChange):
            self.fit()
        elif event.type() == QEvent.Type.ChildAdded:
            # Widgets added later would otherwise be stacked above the outline.
            self._refit.start()
        return False

    def fit(self):
        window = self.parentWidget()
        if window is None:
            return
        self.setGeometry(window.rect())
        # A maximized or full-screen window meets the screen edge; no outline needed.
        self.setVisible(not (window.isMaximized() or window.isFullScreen()))
        self.raise_()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(QPen(OUTLINE_COLOR, 1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))


def _wants_outline(widget):
    if not widget.isWindow():
        return False
    if widget.windowType() not in (Qt.WindowType.Window, Qt.WindowType.Dialog):
        return False  # Menus, tooltips and pop-ups are drawn differently.
    return not widget.windowFlags() & Qt.WindowType.FramelessWindowHint


class OutlineInstaller(QObject):
    """Adds an outline to each normal window and dialog the first time it is shown."""

    def eventFilter(self, watched, event):
        if (event.type() == QEvent.Type.Show and isinstance(watched, QWidget)
                and _wants_outline(watched)
                and watched.findChild(WindowOutline, options=Qt.FindChildOption.FindDirectChildrenOnly) is None):
            WindowOutline(watched)
        return False


def install(app):
    """Outline every window on GNOME's Wayland desktop. Returns the installer, or None."""
    if not wants_outlines():
        return None
    installer = OutlineInstaller(app)
    app.installEventFilter(installer)
    return installer
