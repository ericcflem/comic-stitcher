"""Light and dark appearance.

Comic Stitcher follows the system's light or dark setting, as it is when the
app starts. In dark mode it uses its own dark palette for Qt's Fusion style
(the same on Windows and Linux, with check boxes that look different when
checked), and its own colors (headings, hints, panels, highlights) switch to
dark-mode versions through tone(). The interface icons, drawn in black, are
drawn in the text color instead.

On Linux the light or dark setting is asked of the desktop itself (the
settings portal, or GNOME's own setting) before Qt, whose answer depends on
which of its desktop plugins could load: in the AppImage, without the
system's own GTK, it could miss dark mode. Light mode there uses Fusion's own
light palette, so it doesn't pick up half of a desktop theme either.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPalette

_dark = False

# name: (light, dark)
TONES = {
    "heading": ("#203c55", "#8fc1ef"),        # Screen headings and progress text.
    "title": ("#1d3348", "#e3eaf1"),          # The drop zone's title.
    "muted": ("#617080", "#aeb8c2"),          # Hints and secondary text.
    "text": ("#000000", "#e8eaed"),           # Ordinary text inside panels.
    "panel": ("#f9fbfd", "#26292d"),          # Create your comic's panels.
    "panel_border": ("#cdd6de", "#4a5057"),
    "drop": ("#f5f8fb", "#26292d"),           # The start screen's drop zone.
    "drop_border": ("#94a9bd", "#61758a"),
    "drop_hover": ("#eaf3fa", "#2d3743"),
    "accent": ("#397cac", "#5aa0d8"),         # Drop zone hover border and icon.
    "control_hover": ("#e4edf5", "#34404c"),  # Review buttons under the pointer.
    "control_border": ("#718ea6", "#7590a8"),
    "control_pressed": ("#d4e4f1", "#3b4a58"),
    "control_disabled": ("#9aa5ae", "#69727c"),
    "primary_disabled": ("#b9c5d0", "#3b4450"),
    "primary_disabled_text": ("#ffffff", "#8b95a0"),
    "good": ("#1b5e20", "#86c98a"),           # The lossless WebP suggestion.
    "yes_hover": ("#d6ecdf", "#23422f"),
    "no_hover": ("#f7dddd", "#4a2828"),
    "selected_page": ("#d7f4dd", "#24442c"),  # A selected thumbnail's background.
    "notice": ("#fff4d6", "#4a3c12"),         # The centering bar: something to do.
    "notice_border": ("#e0b44a", "#b08a2e"),
    "notice_text": ("#5c4300", "#ffe6a0"),
    "settled": ("#eef3ee", "#1f3324"),        # The centering bar: nothing to do.
    "settled_border": ("#a9c3a9", "#4d7a57"),
    "settled_text": ("#2f5130", "#c2e5c7"),
    "list_line": ("#d8e0e8", "#3a3f45"),      # The queue's item dividers.
    "list_selected": ("#dceeff", "#23415e"),
    "list_selected_text": ("#102a43", "#e8f1fa"),
    "tab_border": ("#ccd5dd", "#4a5057"),
    "switch_track": ("#d0d7de", "#5a6169"),   # Seam View's page-on-top switch.
    "switch_knob": ("#ffffff", "#e8eaed"),
}


PORTAL = ["gdbus", "call", "--session", "--timeout", "2", "--dest", "org.freedesktop.portal.Desktop",
          "--object-path", "/org/freedesktop/portal/desktop",
          "--method", "org.freedesktop.portal.Settings.ReadOne", "org.freedesktop.appearance", "color-scheme"]
GNOME = ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"]


def _run(command: list[str]) -> str:
    if not shutil.which(command[0]):
        return ""
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=3, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def portal_choice(output: str) -> bool | None:
    """The portal's color-scheme answer ("(<<uint32 1>>,)"): 1 prefers dark,
    2 prefers light, 0 no preference (None)."""
    match = re.search(r"uint32 (\d+)", output)
    return {"1": True, "2": False}.get(match.group(1)) if match else None


def gnome_choice(output: str) -> bool | None:
    value = output.strip().strip("'")
    return {"prefer-dark": True, "prefer-light": False}.get(value)


def linux_desktop_is_dark() -> bool | None:
    """The desktop's own light or dark setting, or None if it gives none."""
    choice = portal_choice(_run(PORTAL))
    return choice if choice is not None else gnome_choice(_run(GNOME))


def system_is_dark() -> bool:
    if sys.platform.startswith("linux"):
        choice = linux_desktop_is_dark()
        if choice is not None:
            return choice
    hints = QGuiApplication.styleHints()
    scheme = getattr(hints, "colorScheme", None)
    if scheme is not None and scheme() != Qt.ColorScheme.Unknown:
        return scheme() == Qt.ColorScheme.Dark
    return QGuiApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128


def is_dark() -> bool:
    return _dark


def tone(name: str) -> str:
    return TONES[name][1 if _dark else 0]


def dark_palette() -> QPalette:
    """A dark palette for Fusion, the same on every system."""
    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: "#202124", QPalette.ColorRole.WindowText: "#e8eaed",
        QPalette.ColorRole.Base: "#2b2d31", QPalette.ColorRole.AlternateBase: "#303236",
        QPalette.ColorRole.Text: "#e8eaed", QPalette.ColorRole.PlaceholderText: "#9aa0a6",
        QPalette.ColorRole.Button: "#33363b", QPalette.ColorRole.ButtonText: "#e8eaed",
        QPalette.ColorRole.BrightText: "#ffffff", QPalette.ColorRole.ToolTipBase: "#2b2d31",
        QPalette.ColorRole.ToolTipText: "#e8eaed", QPalette.ColorRole.Highlight: "#1976cf",
        QPalette.ColorRole.HighlightedText: "#ffffff", QPalette.ColorRole.Link: "#7ab7ef",
        QPalette.ColorRole.LinkVisited: "#b39ddb", QPalette.ColorRole.Light: "#4a4d53",
        QPalette.ColorRole.Midlight: "#3d4045", QPalette.ColorRole.Mid: "#2a2c30",
        QPalette.ColorRole.Dark: "#17181a", QPalette.ColorRole.Shadow: "#0b0b0c",
    }
    for role, value in colors.items():
        palette.setColor(role, QColor(value))
    for role, value in ((QPalette.ColorRole.WindowText, "#7c8187"), (QPalette.ColorRole.Text, "#7c8187"),
                        (QPalette.ColorRole.ButtonText, "#7c8187"), (QPalette.ColorRole.Button, "#2a2c30"),
                        (QPalette.ColorRole.Base, "#25272a"), (QPalette.ColorRole.Highlight, "#3a4a5c")):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(value))
    return palette


def apply_theme(app, dark: bool | None = None) -> None:
    """Choose light or dark once, as the app starts."""
    global _dark
    _dark = system_is_dark() if dark is None else dark
    if _dark:
        app.setPalette(dark_palette())
    elif sys.platform.startswith("linux"):
        app.setPalette(app.style().standardPalette())


def tinted(icon: QIcon, size: int = 128) -> QIcon:
    """A black line icon redrawn in the text color, for dark mode."""
    if not _dark or icon.isNull():
        return icon
    pixmap = icon.pixmap(size, size)
    painter = QPainter(pixmap)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), QColor(TONES["text"][1]))
    painter.end()
    return QIcon(pixmap)
