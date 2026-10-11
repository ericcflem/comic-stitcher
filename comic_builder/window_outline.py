"""Give Comic Stitcher's windows a visible edge on GNOME's Wayland desktop.

There, Qt draws each window's frame itself, and its GNOME-style frame does not
draw a shadow yet and uses an almost-white border, so a window disappears
against a light background. Windows that mix in OutlinedWindow draw a 1 px
gray edge (Fusion's button border) inside a 1 px margin that their contents
never cover. Other platforms, including Windows and KDE, draw their own frames
and are untouched.

Each window draws its own edge in its own paintEvent. There is deliberately no
application-wide event filter: with PySide, such a filter is called for
windows that are part-way through being deleted, which crashes.
"""
import os

from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QDialog

OUTLINE_COLOR = QColor(180, 180, 180)  # #b4b4b4, Fusion's button border


def wants_outlines(platform_name=None, environ=None):
    """True on GNOME's Wayland session, where Qt draws a shadowless frame."""
    platform_name = QGuiApplication.platformName() if platform_name is None else platform_name
    environ = os.environ if environ is None else environ
    desktops = environ.get("XDG_CURRENT_DESKTOP", "").upper().split(":")
    return platform_name == "wayland" and "GNOME" in desktops


class OutlinedWindow:
    """Mix in before QMainWindow or QDialog, and call _setup_outline() in __init__."""

    _outlined = False

    def _setup_outline(self, enabled=None):
        self._outlined = wants_outlines() if enabled is None else enabled
        if self._outlined:
            margins = self.contentsMargins()
            self.setContentsMargins(margins.left() + 1, margins.top() + 1,
                                    margins.right() + 1, margins.bottom() + 1)

    def paintEvent(self, event):
        super().paintEvent(event)
        # A maximized or full-screen window meets the screen edge; no outline needed.
        if self._outlined and not (self.isMaximized() or self.isFullScreen()):
            painter = QPainter(self)
            painter.setPen(QPen(OUTLINE_COLOR, 1))
            painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
            painter.end()


class OutlinedDialog(OutlinedWindow, QDialog):
    """A plain QDialog that draws the GNOME Wayland outline."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._setup_outline()
