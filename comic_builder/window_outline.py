"""Give windows a visible edge on GNOME's Wayland desktop.

There, Qt draws each window's frame itself, and its GNOME-style frame does not
draw a shadow yet and uses an almost-white border, so a window disappears
against a light background. This adds a 1 px gray outline around each
window's content, matching the border Fusion draws around buttons. Other
platforms, including Windows and KDE, draw their own frames and are untouched.

The outline is a plain styled QWidget (no Python overrides), and a single
listener owned by the application keeps every outline sized and on top. Nothing
per-window runs Python code while Qt is deleting a window.
"""
import os

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import QWidget
from shiboken6 import isValid

OUTLINE_COLOR = QColor(180, 180, 180)  # #b4b4b4, Fusion's button border
OUTLINE_NAME = "windowOutline"
_FIT_EVENTS = (QEvent.Type.Show, QEvent.Type.Resize, QEvent.Type.WindowStateChange)


def wants_outlines(platform_name=None, environ=None):
    """True on GNOME's Wayland session, where Qt draws a shadowless frame."""
    platform_name = QGuiApplication.platformName() if platform_name is None else platform_name
    environ = os.environ if environ is None else environ
    desktops = environ.get("XDG_CURRENT_DESKTOP", "").upper().split(":")
    return platform_name == "wayland" and "GNOME" in desktops


def wants_outline(widget):
    """Normal windows and dialogs; not menus, tooltips, pop-ups or frameless windows."""
    if not widget.isWindow():
        return False
    if widget.windowType() not in (Qt.WindowType.Window, Qt.WindowType.Dialog):
        return False
    return not widget.windowFlags() & Qt.WindowType.FramelessWindowHint


def outline_of(window):
    return window.findChild(QWidget, OUTLINE_NAME, Qt.FindChildOption.FindDirectChildrenOnly)


def add_outline(window):
    """Give a window its outline, if it does not have one, and return it."""
    outline = outline_of(window)
    if outline is None:
        outline = QWidget(window)
        outline.setObjectName(OUTLINE_NAME)
        # Clicks, drags and drops go straight through to the window underneath.
        outline.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        outline.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        outline.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        outline.setStyleSheet(f"QWidget#{OUTLINE_NAME} {{ background: transparent; "
                              f"border: 1px solid {OUTLINE_COLOR.name()}; }}")
    fit_outline(window)
    return outline


def fit_outline(window):
    outline = outline_of(window)
    if outline is None:
        return
    outline.setGeometry(window.rect())
    # A maximized or full-screen window meets the screen edge; no outline needed.
    outline.setVisible(not (window.isMaximized() or window.isFullScreen()))
    outline.raise_()


def _refit_later(window):
    # Widgets added later would otherwise be stacked above the outline.
    QTimer.singleShot(0, lambda: isValid(window) and fit_outline(window))


class OutlineInstaller(QObject):
    """Adds an outline to each window when shown, and keeps it sized and on top."""

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind in _FIT_EVENTS or kind == QEvent.Type.ChildAdded:
            if isinstance(watched, QWidget) and isValid(watched) and wants_outline(watched):
                if kind == QEvent.Type.Show:
                    add_outline(watched)
                elif outline_of(watched) is not None:
                    # A child was added. It may still be under construction, so it is
                    # not touched here; the outline is simply raised again shortly.
                    _refit_later(watched)
                else:
                    fit_outline(watched)
        return False


def install(app):
    """Outline every window on GNOME's Wayland desktop. Returns the installer, or None."""
    if not wants_outlines():
        return None
    installer = OutlineInstaller(app)  # Owned by the application, so it lives as long.
    app.installEventFilter(installer)
    return installer
