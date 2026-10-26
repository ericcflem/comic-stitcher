"""Shared interface helpers: headings, buttons, icons, tooltips, image conversion and small widgets."""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from PIL import Image, ImageOps
from PySide6.QtCore import QFile, QEvent, QObject, QSize, Qt, Signal, QRectF
from PySide6.QtGui import QImage, QPixmap, QPainter, QColor, QPen, QIcon
from PySide6.QtWidgets import (QComboBox, QFormLayout, QLabel, QSizePolicy, QStackedWidget,
    QVBoxLayout, QWidget, QStyle, QDialogButtonBox, QProxyStyle, QStyleFactory)
from ..backend import stitched_image, rotated_image
from ..colour import standard_mode


def compact_filename_range(names):
    if len(names) == 1:
        return names[0]
    first, last = Path(names[0]), Path(names[-1])
    final_number = last.stem.rsplit(" - ", 1)[-1]
    extensions = {Path(name).suffix.lower() for name in names}
    suffix = first.suffix if len(extensions) == 1 else " (mixed formats)"
    return f"{first.stem}...{final_number}{suffix}"


def fit_height(window, width):
    """Size a window to this width and just the height its contents need there
    (wrapped text included), with no spare space between them."""
    layout = window.layout()
    window.ensurePolished()
    layout.activate()
    height = layout.totalHeightForWidth(width) if layout.hasHeightForWidth() else -1
    window.resize(width, max(height, layout.totalMinimumSize().height(), window.minimumSizeHint().height()))


def ui_heading(text, size=24):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"font-size:{size}px; font-weight:700; color:#203c55;")
    return label


class _DropDownHeight(QObject):
    """Fixes every drop-down list to one height as it is set up, so none grows
    to fill a taller form row or shrinks in a tight one."""

    def __init__(self, height, parent):
        super().__init__(parent)
        self.height = height

    def eventFilter(self, watched, event):
        # Polish: first set-up. StyleChange: a window's style sheet was applied
        # again, which can restore the style's own minimum height.
        if event.type() in (QEvent.Polish, QEvent.StyleChange) and isinstance(watched, QComboBox):
            if watched.minimumHeight() != self.height or watched.maximumHeight() != self.height:
                watched.setFixedHeight(self.height)
        return False


class _WindowsButtonOrder(QProxyStyle):
    """Fusion, with dialog buttons in the Windows order on every system (Linux
    desktops otherwise reorder them, putting Cancel in the middle)."""

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.SH_DialogButtonLayout:
            return QDialogButtonBox.WinLayout
        return super().styleHint(hint, option, widget, returnData)


def ui_app_style(app):
    """The Fusion style with Windows dialog button order, and drop-down lists of
    one height everywhere: the height an ordinary one has with a 24-pixel
    minimum, as the Create your comic screen always used."""
    if getattr(app, "_drop_down_height", None) is not None:
        return
    app._button_order_style = _WindowsButtonOrder(QStyleFactory.create("Fusion"))
    app.setStyle(app._button_order_style)
    probe = QComboBox()
    probe.addItem("Automatic")
    probe.setStyleSheet("QComboBox { min-height:24px; }")
    app._drop_down_height = _DropDownHeight(probe.sizeHint().height(), app)
    probe.deleteLater()
    app.installEventFilter(app._drop_down_height)


def ui_primary(button):
    """The blue button for a screen's main action: a little taller than the
    app's other buttons, which all share the standard height."""
    button.setStyleSheet("QPushButton { background:#1976cf; color:white; border:1px solid #1976cf; "
                        "border-radius:5px; padding:7px 14px; font-weight:600; } "
                        "QPushButton:hover { background:#1266b6; } "
                        "QPushButton:disabled { background:#b9c5d0; border-color:#b9c5d0; }")


def ui_icon(widget, kind, size=72):
    standard = QStyle.SP_DirIcon if kind == "folder" else QStyle.SP_FileIcon
    pixmap = widget.style().standardIcon(standard).pixmap(size, size)
    if kind not in ("folder", "file"):
        painter = QPainter(pixmap)
        painter.fillRect(3, size // 2, size - 6, size // 3, QColor("#c73737" if kind == "PDF" else "#397cac"))
        painter.setPen(QColor("white"))
        font = painter.font()
        font.setBold(True)
        font.setPixelSize(max(12, size // 5))
        painter.setFont(font)
        painter.drawText(QRectF(3, size // 2, size - 6, size // 3), Qt.AlignCenter, kind)
        painter.end()
    return pixmap


class ProgressLabel(QLabel):
    """A wrapping label that reports text changes, so the window can make room."""
    text_changed = Signal()

    def setText(self, text):
        changed = text != self.text()
        super().setText(text)
        if changed:
            self.text_changed.emit()


class CurrentPageStack(QStackedWidget):
    # Hidden review pages must not force a progress window to stay large.
    def minimumSizeHint(self):
        page = self.currentWidget()
        return page.minimumSizeHint() if page else QSize(0, 0)

    def sizeHint(self):
        page = self.currentWidget()
        return page.sizeHint() if page else QSize(750, 500)


def tooltip_text(message: str) -> str:
    """Wrap long explanations while preserving intentional line breaks."""
    return "\n".join(textwrap.fill(line, width=90) if line else ""
                     for line in message.splitlines())


def form_hint(form: QFormLayout, widget: QWidget, message: str) -> None:
    """Make the same explanation available over the control and its label."""
    message = tooltip_text(message)
    widget.setToolTip(message)
    label = form.labelForField(widget)
    if label is not None:
        label.setToolTip(message)


try:
    from .. import icons_resource  # Generated locally by package_icons.py.
except ImportError:
    icons_resource = None


REVIEW_ICONS = {"Back": "left-caret", "Previous": "left-caret", "Next": "right-caret",
                "No": "no", "Yes": "check", "Seam": "seam-view", "Rotate left": "rotate-left",
                "Rotate right": "rotate-right", "Join": "merge", "Split": "split",
                "Remove": "delete", "Undo": "rotate-left", "Pair review": "left-arrow",
                "Export": "check", "Single": "single", "Double": "double", "Sideways": "sideways",
                "Swap": "swap", "Replace": "swap", "Adjust": "crop"}


def interface_icon(name, zoomed=False):
    """Resolve bundled vector controls and user-created page icons."""
    svg_name = ('zoom-out' if zoomed else 'zoom-in') if name == 'seam-view' else name
    if QFile.exists(f":/comic-icons/{svg_name}.svg"):
        icon = QIcon(f":/comic-icons/{svg_name}.svg")
        if not icon.isNull():
            return icon
    return QIcon(f":/comic-icons/{name}.png")


def set_review_icon(button, name, symbol='', zoomed=False):
    icon = interface_icon(name, zoomed)
    if not icon.isNull():
        ratio = button.devicePixelRatioF()
        # QIcon already applies the display scale; do not multiply it twice.
        size = QSize(44, 32) if name in ('single', 'double', 'sideways') else QSize(32, 32)
        pix = icon.pixmap(size, ratio)
        button.review_icon.setPixmap(pix)
    else:
        button.review_icon.setText(symbol)


def review_control(button, symbol, label, tip=None, icon_label=None):
    button.setText("")
    button.setAutoDefault(False)
    button.setDefault(False)
    button.setFixedSize(88, 68)
    button.setStyleSheet("QPushButton { border:1px solid transparent; border-radius:6px; background:transparent; padding:0; } QPushButton:hover { background:#e4edf5; border-color:#718ea6; } QPushButton:pressed, QPushButton:checked { background:#d4e4f1; border-color:#718ea6; } QPushButton:disabled { color:#9aa5ae; }")
    if not hasattr(button, "review_icon"):
        layout = QVBoxLayout(button)
        layout.setContentsMargins(4, 6, 4, 6)
        layout.setSpacing(3)
        button.review_icon = QLabel()
        button.review_caption = QLabel()
        for widget in (button.review_icon, button.review_caption):
            widget.setAlignment(Qt.AlignCenter)
            widget.setAttribute(Qt.WA_TransparentForMouseEvents)
            layout.addWidget(widget)
        button.review_icon.setFixedHeight(32)
        button.review_caption.setFixedHeight(19)
        button.review_icon.setStyleSheet("font-size:28px; background:transparent; border:0;")
        button.review_caption.setStyleSheet("font-size:12px; background:transparent; border:0;")
    set_review_icon(button, REVIEW_ICONS.get(icon_label or label, ''), symbol)
    button.review_caption.setText(label)
    button.setToolTip(tip or label)
    button.setAccessibleName(label.replace("\n", " "))


def pixels_from_pil(image: Image.Image, max_size: tuple[int, int]) -> tuple[bytes, int, int]:
    """Keep Pillow and raw pixels in workers; construct Qt images on the GUI thread."""
    image = standard_mode(ImageOps.exif_transpose(image))  # 16-bit grey and CMYK shown correctly.
    image.thumbnail(max_size, Image.Resampling.LANCZOS)
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    return image.tobytes(), image.width, image.height


def qimage_from_pixels(pixels: tuple[bytes, int, int]) -> QImage:
    data, width, height = pixels
    image = QImage(width, height, QImage.Format_RGBA8888)
    if image.isNull():
        raise MemoryError(f"Could not allocate preview image ({width} × {height}).")
    image.bits()[:] = data
    return image


def qimage_from_pil(image: Image.Image, max_size: tuple[int, int]) -> QImage:
    return qimage_from_pixels(pixels_from_pil(image, max_size))


def pixmap_from_pil(image: Image.Image, max_size: tuple[int, int]) -> QPixmap:
    return QPixmap.fromImage(qimage_from_pil(image, max_size))


def page_pixmap(path: Path, size: tuple[int, int] = (1800, 2200), rotation: int = 0) -> QPixmap:
    with Image.open(path) as im:
        if rotation:
            oriented = ImageOps.exif_transpose(im)
            try:
                turned = rotated_image(oriented, rotation)
                try:
                    return pixmap_from_pil(turned, size)
                finally:
                    turned.close()
            finally:
                oriented.close()
        return pixmap_from_pil(im, size)


def spread_pixmap(a: Path, b: Path, size: tuple[int, int] = (2400, 1800), rotation: int = 0, right_to_left: bool = False,
                  page_rotations: tuple[int, int] = (0, 0), overlap: tuple[int, str] = (0, "right")) -> QPixmap:
    if right_to_left:
        a, b = b, a
    im = stitched_image(a, b, rotation, page_rotations[::-1] if right_to_left else page_rotations, overlap)
    try:
        return pixmap_from_pil(im, size)
    finally:
        im.close()


class FitImage(QLabel):
    def __init__(self):
        super().__init__()
        self._pixmap: QPixmap | None = None
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(80, 120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background:#20242a; color:#d6dde5; border-radius:6px;")

    def show_pixmap(self, pixmap: QPixmap | None) -> None:
        self._pixmap = pixmap
        self._fit()

    def _fit(self):
        if self._pixmap and not self._pixmap.isNull():
            super().setPixmap(self._pixmap.scaled(self.size(), Qt.KeepAspectRatio,
                                                    Qt.SmoothTransformation))
        else:
            self.clear()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()


def readable_size(size: int) -> str:
    """Bytes in the units the system's file manager shows: Windows Explorer
    counts 1,024-byte kilobytes, while Linux desktops use 1,000."""
    step = 1024 if sys.platform == "win32" else 1000
    for unit in ("KB", "MB", "GB"):
        size /= step
        if size < step or unit == "GB":
            return f"{size:.1f} {unit}" if size < 10 else f"{size:,.0f} {unit}"
    return ""


class DropMarker(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(QPen(QColor("#ed3c9b"), 3, Qt.DotLine))
        painter.drawLine(self.width() // 2, 4, self.width() // 2, self.height() - 4)
