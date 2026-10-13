"""PySide6 desktop interface for Comic Stitcher."""
from __future__ import annotations

import sys
import time
import os
import shutil
import textwrap
import tempfile
import copy
import json
from functools import partial
from dataclasses import dataclass, replace
from collections import OrderedDict, deque
from datetime import datetime
from pathlib import Path
from threading import Condition
from uuid import uuid4

from PIL import Image, ImageOps
from PySide6.QtCore import QFile, QEvent, QSettings, QSize, Qt, QThread, QTimer, Signal, QUrl, QRectF, QSignalBlocker, QMimeData
from PySide6.QtGui import QDesktopServices, QImage, QPixmap, QShortcut, QKeySequence, QPainter, QColor, QPen, QDrag, QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QStackedWidget, QTabWidget, QVBoxLayout, QWidget, QGroupBox, QStyle, QGridLayout,
)

from .backend import (
    BookPlan, BoundaryOverrideRequired, ExportOptions, check_sources, export_book, planned_names,
    safe_book_name, scan_folder, scan_manual_folder, stitched_image, rotated_image,
    encoding_workers, POSSIBLE_MIN_SCORE, copy_encoding_options, ENCODER_FIELDS,
    estimate_export_size, ESTIMATE_SAMPLES,
)
from . import __version__, diagnostics
from .import_sources import expand_sources
from .single_instance import LaunchBroker
from .detector import collect_images
from .archive_import import ARCHIVE_EXTS, import_archive
from .pdf_import import import_pdf, pdf_workers, rendered_dimensions, suggest_pdf_dpi
from .pdf_jpegs import inspect_pdf_jpegs
from .metadata_dialog import MetadataDialog
from .comicvine import ComicVineClient
from .key_store import can_remember_key, forget_key, protect_key, retrieve_key
from .window_outline import OutlinedDialog, OutlinedWindow, prefer_xwayland_on_gnome
from .comicinfo import parse_xml
from .review_report import save_report
from .session import (cached_folder_scan, clear_session, read_session, restore_plan,
                      save_session, session_path)
from .screenshot_import import (CropChoice, StagedScreenshots, analyze_screenshots,
                                looks_like_screenshot_folder, stage_screenshots)
from .screenshot_geometry import screenshot_workers


def compact_filename_range(names):
    if len(names) == 1:
        return names[0]
    first, last = Path(names[0]), Path(names[-1])
    final_number = last.stem.rsplit(" - ", 1)[-1]
    extensions = {Path(name).suffix.lower() for name in names}
    suffix = first.suffix if len(extensions) == 1 else " (mixed formats)"
    return f"{first.stem}...{final_number}{suffix}"


def ui_heading(text, size=24):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"font-size:{size}px; font-weight:700; color:#203c55;")
    return label


def ui_primary(button):
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
    from . import icons_resource  # Generated locally by package_icons.py.
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
    image = ImageOps.exif_transpose(image)
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


def spread_pixmap(a: Path, b: Path, size: tuple[int, int] = (2400, 1800), rotation: int = 0, right_to_left: bool = False, page_rotations: tuple[int, int] = (0, 0)) -> QPixmap:
    if right_to_left:
        a, b = b, a
    im = stitched_image(a, b, rotation, page_rotations[::-1] if right_to_left else page_rotations)
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


class JoinedPairView(QWidget):
    """Draw the two source pages at one scale, with their seam touching."""

    def __init__(self):
        super().__init__()
        self.left: QPixmap | None = None
        self.right: QPixmap | None = None
        self.page_sizes: tuple[tuple[int, int], tuple[int, int]] | None = None
        self.message = "Loading pages…"
        self.rotation = 0
        self.setMinimumSize(160, 120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def load_pair(self, left_size: tuple[int, int], right_size: tuple[int, int]):
        self.page_sizes = (left_size, right_size)
        self.left = self.right = None
        self.message = "Loading pages…"
        self.update()

    def show_pair(self, left: QPixmap, right: QPixmap):
        self.left, self.right = left, right
        self.update()

    def show_error(self):
        self.left = self.right = None
        self.message = "Could not load pages"
        self.update()

    def image_rects(self) -> tuple[QRectF, QRectF] | None:
        if not self.page_sizes:
            return None
        (left_width, left_height), (right_width, right_height) = self.page_sizes
        common_height = min(left_height, right_height)
        if not common_height or not self.width() or not self.height():
            return None
        scale = min(self.width() / (left_width + right_width), self.height() / common_height)
        x = (self.width() - (left_width + right_width) * scale) / 2
        y = (self.height() - common_height * scale) / 2
        left_rect = QRectF(x, y, left_width * scale, common_height * scale)
        right_rect = QRectF(left_rect.right(), y, right_width * scale, common_height * scale)
        return left_rect, right_rect

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#20242a"))
        if self.rotation and self.page_sizes and self.left is not None and self.right is not None:
            (lw, lh), (rw, rh) = self.page_sizes
            common = min(lh, rh)
            width = lw + rw
            turned_width, turned_height = (common, width) if self.rotation % 180 else (width, common)
            scale = min(self.width() / turned_width, self.height() / turned_height)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.translate(self.width() / 2, self.height() / 2)
            painter.rotate(self.rotation)
            painter.translate(-width * scale / 2, -common * scale / 2)
            for pixmap, x, page_width, height in ((self.left, 0, lw, lh),
                                                   (self.right, lw, rw, rh)):
                painter.drawPixmap(QRectF(x * scale, 0, page_width * scale, common * scale),
                                   pixmap, QRectF(0, 0, pixmap.width(), pixmap.height() * common / height))
            return
        rects = self.image_rects()
        if not rects or self.left is None or self.right is None:
            painter.setPen(QColor("#d6dde5"))
            painter.drawText(self.rect(), Qt.AlignCenter, self.message)
            return
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        for pixmap, destination, (_, source_height) in zip(
                (self.left, self.right), rects, self.page_sizes, strict=True):
            # Export crops the taller page at the bottom. Show the same join.
            common_height = min(height for _, height in self.page_sizes)
            source = QRectF(0, 0, pixmap.width(), pixmap.height() * common_height / source_height)
            painter.drawPixmap(destination, pixmap, source)


class Worker(QThread):
    progress = Signal(int, int)
    result = Signal(object)
    failure = Signal(str)

    def __init__(self, fn, *args):
        super().__init__()
        self.fn = fn
        self.args = args

    def run(self):
        try:
            self.result.emit(self.fn(*self.args, progress=self.progress.emit))
        except Exception as exc:
            diagnostics.exception("Scan or export failed")
            self.failure.emit(str(exc))


def readable_size(size: int) -> str:
    """Bytes in the units the system's file manager shows: Windows Explorer
    counts 1,024-byte kilobytes, while Linux desktops use 1,000."""
    step = 1024 if sys.platform == "win32" else 1000
    for unit in ("KB", "MB", "GB"):
        size /= step
        if size < step or unit == "GB":
            return f"{size:.1f} {unit}" if size < 10 else f"{size:,.0f} {unit}"
    return ""


class SizeEstimateWorker(QThread):
    """Estimates export size in the background; a newer request replaces an older one.

    Keeps its helper processes between estimates while the export screen is open,
    since starting them is slow on Windows; release() lets them go.
    """
    ready = Signal(int, object)
    provisional = Signal(int, object)  # An early estimate while more samples are encoded.
    failed = Signal(int, str)

    def __init__(self):
        super().__init__()
        self.condition = Condition()
        self.requested = None
        self.stopping = False
        self.cancelled = False
        self.releasing = False
        self.pool = None
        self.pool_size = 0

    def request(self, generation, plan, opt):
        with self.condition:
            self.requested = (generation, copy.deepcopy(plan), replace(opt))
            self.cancelled = False
            self.releasing = False
            self.condition.notify()

    def cancel(self):
        with self.condition:
            self.requested = None
            self.cancelled = True

    def release(self):
        """Cancel, and end the helper processes once idle."""
        with self.condition:
            self.requested = None
            self.cancelled = True
            self.releasing = True
            self.condition.notify()

    def stop(self):
        with self.condition:
            self.stopping = True
            self.requested = None
            self.condition.notify_all()

    def _interrupted(self):
        return self.stopping or self.cancelled or self.requested is not None

    def _end_pool(self):
        if self.pool is not None:
            self.pool.shutdown(wait=False, cancel_futures=True)
            self.pool, self.pool_size = None, 0

    def _pool_for(self, workers):
        if workers <= 1:
            return None
        if self.pool is None or self.pool_size != workers:
            from concurrent.futures import ProcessPoolExecutor
            from multiprocessing import get_context
            self._end_pool()
            self.pool = ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn"))
            self.pool_size = workers
        return self.pool

    def run(self):
        try:
            while True:
                with self.condition:
                    while self.requested is None and not self.stopping and not self.releasing:
                        self.condition.wait()
                    if self.stopping:
                        return
                    if self.requested is None:  # Released while idle.
                        self.releasing = False
                        self._end_pool()
                        continue
                    generation, plan, opt = self.requested
                    self.requested = None
                started = time.monotonic()
                try:
                    workers = encoding_workers(opt.workers)
                    result = estimate_export_size(
                        plan, opt, cancelled=self._interrupted, workers=workers,
                        executor=self._pool_for(min(workers, ESTIMATE_SAMPLES)),
                        progress=lambda sizes, _done, _total, generation=generation:
                            self.provisional.emit(generation, sizes))
                except Exception as exc:  # The estimate is optional; export reports real problems.
                    diagnostics.note("Size estimate failed: %s", exc)
                    self._end_pool()  # A broken pool is not reused.
                    self.failed.emit(generation, str(exc))
                    continue
                if result is not None:
                    diagnostics.note("Size estimate shown after %.1f s", time.monotonic() - started)
                    self.ready.emit(generation, result)
        finally:
            self._end_pool()


class ReviewImageWorker(QThread):
    """Decode one pair at a time, discarding queued pairs after navigation."""

    ready = Signal(int, object, object)
    failed = Signal(int, str)

    def __init__(self):
        super().__init__()
        self.condition = Condition()
        self.requested: tuple[int, Path, Path] | None = None
        self.stopping = False

    def request(self, generation: int, left: Path, right: Path):
        with self.condition:
            self.requested = (generation, left, right)
            self.condition.notify()

    def stop(self):
        with self.condition:
            self.stopping = True
            self.requested = None
            self.condition.notify_all()

    def run(self):
        while True:
            with self.condition:
                while self.requested is None and not self.stopping:
                    self.condition.wait()
                if self.stopping:
                    return
                generation, left, right = self.requested
                self.requested = None
            try:
                with Image.open(left) as image:
                    image.draft("RGB", (1800, 2200))
                    first = pixels_from_pil(image, (1800, 2200))
                with Image.open(right) as image:
                    image.draft("RGB", (1800, 2200))
                    second = pixels_from_pil(image, (1800, 2200))
                diagnostics.note("Pair images decoded: request %d", generation)
                self.ready.emit(generation, first, second)
            except Exception as exc:
                diagnostics.exception("Pair image decoding failed")
                self.failed.emit(generation, str(exc))


class CropPreviewWorker(QThread):
    ready = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self):
        super().__init__()
        self.condition = Condition()
        self.requested = None
        self.stopping = False

    def request(self, generation, path):
        with self.condition:
            self.requested = (generation, path)
            self.condition.notify()

    def stop(self):
        with self.condition:
            self.stopping = True
            self.requested = None
            self.condition.notify_all()

    def run(self):
        while True:
            with self.condition:
                while self.requested is None and not self.stopping:
                    self.condition.wait()
                if self.stopping:
                    return
                generation, path = self.requested
                self.requested = None
            try:
                with Image.open(path) as image:
                    image.draft("RGB", (1300, 1000))
                    pixels = pixels_from_pil(image, (1300, 1000))
                self.ready.emit(generation, pixels)
            except Exception as exc:
                self.failed.emit(generation, str(exc))


class DropMarker(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(QPen(QColor("#ed3c9b"), 3, Qt.DotLine))
        painter.drawLine(self.width() // 2, 4, self.width() // 2, self.height() - 4)


class ThumbnailStrip(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        self.dragMoveEvent(event)

    def dragMoveEvent(self, event):
        if self.owner.update_page_drop(event.source(), event.position().x()):
            event.setDropAction(Qt.MoveAction); event.accept()
        else: event.ignore()

    def dragLeaveEvent(self, event):
        self.owner.clear_page_drop()
        event.accept()

    def dropEvent(self, event):
        if self.owner.finish_page_drop(event.source(), event.position().x()):
            event.setDropAction(Qt.MoveAction); event.accept()
        else: event.ignore()


class PageThumbnail(QPushButton):
    """Drag to an insertion boundary, keeping joined spreads intact."""
    def __init__(self, index, owner):
        super().__init__(str(index + 1))
        self.index = index
        self.owner = owner
        self.setAcceptDrops(True)
        self.press_position = None

    def mousePressEvent(self, event):
        self.press_position = event.position().toPoint() if event.button() == Qt.LeftButton else None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (self.press_position is not None and event.buttons() & Qt.LeftButton and
                (event.position().toPoint() - self.press_position).manhattanLength() >= QApplication.startDragDistance()):
            data = QMimeData()
            data.setData('application/x-comic-stitcher-page', str(self.index).encode())
            drag = QDrag(self); drag.setMimeData(data)
            if not self.icon().isNull(): drag.setPixmap(self.icon().pixmap(80, 80))
            self.setDown(False); self.press_position = None
            try: drag.exec(Qt.MoveAction)
            finally: self.owner.clear_page_drop()
        else:
            super().mouseMoveEvent(event)

    def dragEnterEvent(self, event):
        self.dragMoveEvent(event)

    def dragMoveEvent(self, event):
        if self.owner.update_page_drop(event.source(), self.x() + event.position().x()):
            event.setDropAction(Qt.MoveAction); event.accept()
        else: event.ignore()

    def dragLeaveEvent(self, event):
        self.owner.clear_page_drop()
        event.accept()

    def dropEvent(self, event):
        if self.owner.finish_page_drop(event.source(), self.x() + event.position().x()):
            event.setDropAction(Qt.MoveAction); event.accept()
        else: event.ignore()


class ThumbnailWorker(QThread):
    """Decode only visible thumbnails, prioritizing the latest scroll position."""

    ready = Signal(int, int, object)
    failed = Signal(int, int)

    def __init__(self):
        super().__init__()
        self.condition = Condition()
        self.jobs = deque()
        self.pending = set()
        self.entries = []
        self.generation = 0
        self.stopping = False

    def reset(self, entries: list[tuple[Path, Path | None, int]]) -> int:
        with self.condition:
            self.generation += 1
            self.entries = entries
            self.jobs.clear()
            self.pending.clear()
            self.condition.notify_all()
            return self.generation

    def request(self, generation: int, index: int):
        with self.condition:
            if generation != self.generation or not 0 <= index < len(self.entries):
                return
            if index in self.pending:
                self.jobs.remove(index)
            else:
                self.pending.add(index)
            self.jobs.appendleft(index)
            self.condition.notify()

    def stop(self):
        with self.condition:
            self.stopping = True
            self.jobs.clear()
            self.condition.notify_all()

    def run(self):
        while True:
            with self.condition:
                while not self.jobs and not self.stopping:
                    self.condition.wait()
                if self.stopping:
                    return
                index = self.jobs.popleft()
                self.pending.remove(index)
                generation = self.generation
                left, right, rotation, *extra = self.entries[index]
                page_rotations = extra[0] if extra else (0, 0)
            try:
                if right is None:
                    with Image.open(left) as image:
                        image.draft("RGB", (100, 84))
                        if rotation:
                            oriented = ImageOps.exif_transpose(image)
                            try:
                                turned = rotated_image(oriented, rotation)
                                try:
                                    thumbnail = pixels_from_pil(turned, (100, 84))
                                finally:
                                    turned.close()
                            finally:
                                oriented.close()
                        else:
                            thumbnail = pixels_from_pil(image, (100, 84))
                else:
                    image = stitched_image(left, right, rotation, page_rotations)
                    try:
                        thumbnail = pixels_from_pil(image, (100, 84))
                    finally:
                        image.close()
                self.ready.emit(generation, index, thumbnail)
            except Exception:
                self.failed.emit(generation, index)


class AdvancedSettingsWidget(QWidget):
    reset_requested = Signal()
    def __init__(self, opt: ExportOptions, current: str, parent=None):
        super().__init__(parent)
        self.opt = opt
        layout = QVBoxLayout(self)
        self.format = QComboBox()
        self.format.addItems(["JPEG", "WebP", "PNG"])
        self.format.setCurrentText({"jpeg": "JPEG", "webp": "WebP", "png": "PNG"}.get(current, "JPEG"))
        self.format.hide()
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)
        jpeg = QWidget()
        jf = QFormLayout(jpeg)
        self.progressive = QCheckBox("Progressive")
        self.progressive.setChecked(opt.jpeg_progressive)
        self.optimize = QCheckBox("Optimize Huffman tables")
        self.optimize.setChecked(opt.jpeg_optimize)
        self.subsampling = QComboBox()
        self.subsampling.addItems(["4:4:4 (highest color detail)", "4:2:2", "4:2:0"])
        self.subsampling.setCurrentIndex(opt.jpeg_subsampling if opt.jpeg_subsampling in range(3) else 0)
        self.smooth = QSpinBox()
        self.smooth.setRange(0, 100)
        self.smooth.setValue(opt.jpeg_smooth)
        jf.addRow(self.progressive)
        jf.addRow(self.optimize)
        jf.addRow("Chroma subsampling", self.subsampling)
        jf.addRow("Smoothing", self.smooth)
        form_hint(jf, self.progressive,
                  "Saves JPEG in multiple passes. A reader may show a rough version before the full image has loaded. "
                  "Encoding can take longer; the finished image uses the same selected quality.")
        form_hint(jf, self.optimize,
                  "Builds efficient Huffman tables for this image. This can reduce file size "
                  "without changing the image data, but takes more time and memory while encoding.")
        form_hint(jf, self.subsampling,
                  "Controls how much color detail JPEG retains relative to brightness.\n"
                  "4:4:4 keeps full color detail, useful around colored lettering and line art; largest files.\n"
                  "4:2:2 reduces horizontal color detail; a middle ground.\n"
                  "4:2:0 reduces color detail in both directions; smaller files, but colored edges may soften.")
        for index, description in enumerate(("Full color resolution; largest files.",
                                              "Less horizontal color detail; middle ground.",
                                              "Less color detail in both directions; smaller files.")):
            self.subsampling.setItemData(index, description, Qt.ToolTipRole)
        form_hint(jf, self.smooth,
                  "JPEG smoothing factor, 0–100. Zero leaves it off. Increasing it can suppress fine noise "
                  "and compression artifacts, but can blur lettering, textures, and fine line work.")
        self.stack.addWidget(jpeg)
        webp = QWidget()
        wf = QFormLayout(webp)
        self.lossless = QCheckBox("Lossless WebP")
        self.lossless.setChecked(opt.webp_lossless)
        self.method = QSpinBox()
        self.method.setRange(0, 6)
        self.method.setValue(opt.webp_method)
        wf.addRow(self.lossless)
        wf.addRow("Compression effort", self.method)
        form_hint(wf, self.lossless,
                  "Preserves decoded pixel values instead of using lossy WebP compression. Files may be larger. "
                  "In lossless mode, the main WebP quality value controls compression effort, not image quality.")
        form_hint(wf, self.method,
                  "WebP encoder method, 0–6. Higher values generally spend more CPU time seeking "
                  "a smaller or better-encoded file; they do not change the selected quality setting.")
        self.stack.addWidget(webp)
        png = QWidget()
        pf = QFormLayout(png)
        self.compress = QSpinBox()
        self.compress.setRange(0, 9)
        self.compress.setValue(opt.png_compress)
        pf.addRow("Compression effort (lossless)", self.compress)
        form_hint(pf, self.compress,
                  "PNG compression level, 0–9. Zero is fastest and makes larger files; nine works harder "
                  "for smaller files. Pixel quality stays identical at every level. Default: 6.")
        self.stack.addWidget(png)
        self.format.currentIndexChanged.connect(self.stack.setCurrentIndex)
        self.stack.setCurrentIndex(self.format.currentIndex())
        reset = QPushButton("Reset to defaults")
        reset.setToolTip(tooltip_text("Resets JPEG, WebP, and PNG encoder settings and quality values in this dialog. "
                                       "Click OK to save them; Cancel keeps your previous choices."))
        reset.clicked.connect(self.reset_defaults)
        layout.addWidget(reset, alignment=Qt.AlignRight)

    def reset_defaults(self):
        self.progressive.setChecked(False)
        self.optimize.setChecked(True)
        self.subsampling.setCurrentIndex(0)
        self.smooth.setValue(0)
        self.lossless.setChecked(False)
        self.method.setValue(4)
        self.compress.setValue(6)
        self.reset_requested.emit()

    def apply(self):
        self.opt.jpeg_progressive = self.progressive.isChecked()
        self.opt.jpeg_optimize = self.optimize.isChecked()
        self.opt.jpeg_subsampling = self.subsampling.currentIndex()
        self.opt.jpeg_smooth = self.smooth.value()
        self.opt.webp_lossless = self.lossless.isChecked()
        self.opt.webp_method = self.method.value()
        self.opt.png_compress = self.compress.value()



class ResizeSettingsWidget(QWidget):
    def __init__(self, values: dict, parent=None):
        super().__init__(parent)
        box = QVBoxLayout(self)
        box.addWidget(QLabel("Choose how the reduced copy will be resized."))
        form = QFormLayout()
        self.mode = QComboBox()
        self.mode.addItems(["Height", "Width"])
        self.mode.setCurrentText(values["mode"].title())
        form.addRow("Resize by", self.mode)
        form_hint(form, self.mode,
                  "Height scales each page proportionally toward the same height. Width scales each page "
                  "proportionally toward the same width. Joined spreads are resized after stitching, "
                  "so their shape is preserved.")
        self.pixels = QSpinBox()
        self.pixels.setRange(1, 20000)
        self.pixels.setSuffix(" px")
        self.pixels.setValue(values["pixels"])
        form.addRow("Target dimension", self.pixels)
        form_hint(form, self.pixels,
                  "Target height or width in pixels. The other dimension is calculated automatically "
                  "to keep each image's proportions. Default: 1920 pixels high.")
        rule = QLabel("Pages are never enlarged. Smaller pages keep their original dimensions.")
        rule.setWordWrap(True)
        form.addRow("", rule)
        self.gamma = QCheckBox("Gamma corrected resize")
        self.gamma.setChecked(values["gamma"])
        form.addRow("", self.gamma)
        form_hint(form, self.gamma,
                  "Blend colors in linear light while resizing, then convert them back for display. "
                  "This can keep bright details from becoming too dark and help colored edges look "
                  "more natural. It uses more processing time and assumes sRGB color.")
        self.filter = QComboBox()
        self.filter.addItems(["Bilinear", "Bicubic", "Lanczos"])
        self.filter.setCurrentText(values["filter"].title())
        form.addRow("Resampling", self.filter)
        descriptions = ("Bilinear: Fast, basic smoothing. Fine textures and edges can look softer.",
                        "Bicubic: A balance of smoothness and detail, often keeping edges clearer than Bilinear.",
                        "Lanczos: Keeps fine detail and tends to look sharpest. High-contrast edges can show subtle halos.")
        form_hint(form, self.filter, "Choose how new pixels are calculated.\n" + "\n".join(descriptions))
        for index, description in enumerate(descriptions):
            self.filter.setItemData(index, description, Qt.ToolTipRole)
        box.addLayout(form)
        box.addStretch()

    def values(self) -> dict:
        return {"mode": self.mode.currentText().lower(), "pixels": self.pixels.value(),
                "reduce_only": True, "gamma": self.gamma.isChecked(),
                "filter": self.filter.currentText().lower()}


class OutputOptionsDialog(OutlinedWindow, QDialog):
    """Edit a copy of the output settings; apply nothing until OK."""

    def __init__(self, opt: ExportOptions, resize_values: dict, parent=None, *, size_limits=None):
        super().__init__(parent)
        self._setup_outline()  # GNOME on Wayland: a visible window edge
        self.setWindowTitle("Output options")
        self.resize(650, 450)
        self.setStyleSheet("QTabWidget::pane { border:1px solid #ccd5dd; } "
                           "QFormLayout { spacing:12px; }")
        self.draft = replace(opt)
        self.keep_originals = opt.keep_originals
        box = QVBoxLayout(self)
        tabs = QTabWidget()
        box.addWidget(tabs)

        image_tab = QWidget()
        image_box = QVBoxLayout(image_tab)
        form = QFormLayout()
        self.format = QComboBox()
        self.format.addItems(["JPEG", "WebP", "PNG"])
        self.format.setCurrentText({"jpeg": "JPEG", "webp": "WebP", "png": "PNG"}.get(opt.format, "JPEG"))
        form.addRow("Output format", self.format)
        self.format_label = form.labelForField(self.format)
        form_hint(form, self.format,
                  "Choose the format used when re-encoding final pages, including the reduced copy. "
                  "For a full-size copy with unjoined pages kept unchanged, choose the joined spread format below.")
        self.jpeg_quality = QSpinBox()
        self.jpeg_quality.setRange(1, 100)
        self.jpeg_quality.setValue(opt.jpeg_quality)
        form.addRow("JPEG quality", self.jpeg_quality)
        form_hint(form, self.jpeg_quality,
                  "JPEG quality, 1–100. Higher values preserve more detail but usually make larger "
                  "files. This setting is remembered next time you open the app. Default: 90.")
        self.webp_quality = QSpinBox()
        self.webp_quality.setRange(1, 100)
        self.webp_quality.setValue(opt.webp_quality)
        form.addRow("WebP quality", self.webp_quality)
        form_hint(form, self.webp_quality,
                  "For lossy WebP, 1–100 controls image quality and file size. In lossless WebP mode, "
                  "it controls compression effort: higher is slower, not higher image quality. "
                  "The value is remembered next time you open the app. Default: 90.")
        self.jpeg_quality_label = form.labelForField(self.jpeg_quality)
        self.webp_quality_label = form.labelForField(self.webp_quality)
        self.spread_format = QComboBox()
        self.spread_format.addItems(["PNG (lossless)", "JPEG", "WebP"])
        self.spread_format.setCurrentIndex({"png": 0, "jpeg": 1, "webp": 2}[opt.spread_format])
        form.addRow("Format for joined/rotated pages", self.spread_format)
        form_hint(form, self.spread_format,
                  "When ordinary pages are copied unchanged, newly stitched spreads and rotated "
                  "single pages must be encoded. PNG avoids another lossy step; JPEG can be "
                  "smaller but re-encodes the edited image. WebP uses the WebP quality and advanced settings.")
        self.spread_format_label = form.labelForField(self.spread_format)
        image_box.addLayout(form)
        self.advanced = AdvancedSettingsWidget(self.draft, opt.format, image_tab)
        self.advanced.reset_requested.connect(lambda: (self.jpeg_quality.setValue(90),
                                                       self.webp_quality.setValue(90)))
        image_box.addWidget(self.advanced)
        tabs.addTab(image_tab, "Format")

        resize_tab = QWidget()
        resize_box = QVBoxLayout(resize_tab)
        sizes_form = QFormLayout()
        self.size_output = QComboBox()
        self.size_output.addItems(["Full size only", "Reduced size only", "Both sizes"])
        self.size_output.setCurrentIndex({"full": 0, "reduced": 1, "both": 2}[opt.size_output])
        sizes_form.addRow("Output sizes", self.size_output)
        form_hint(sizes_form, self.size_output,
                  "Full size keeps the final images at their source dimensions. Reduced size "
                  "creates a resized comic. Both sizes makes separate Full and Reduced copies.")
        resize_box.addLayout(sizes_form)
        self.resize_settings = ResizeSettingsWidget(resize_values, resize_tab)
        resize_box.addWidget(self.resize_settings)
        if size_limits:
            def limit_target():
                axis = self.resize_settings.mode.currentText().lower()
                self.resize_settings.pixels.setMaximum(min(20000, size_limits[axis]))
            self.resize_settings.mode.currentIndexChanged.connect(limit_target)
            limit_target()
        self.reduced_custom = QCheckBox("Use a different image format and quality for the reduced copy")
        self.reduced_custom.setChecked(opt.reduced_custom)
        self.reduced_custom.setToolTip(tooltip_text("Unchecked: use the Format tab's settings for the reduced copy. Checked: choose its own image format, quality, and advanced encoder settings below."))
        resize_box.addWidget(self.reduced_custom)
        self.reduced_controls = QWidget()
        reduced_box = QVBoxLayout(self.reduced_controls)
        reduced_form = QFormLayout()
        self.reduced_draft = replace(copy_encoding_options(replace(opt, reduced_custom=True), True))
        self.reduced_format = QComboBox()
        self.reduced_format.addItems(["JPEG", "WebP", "PNG"])
        self.reduced_format.setCurrentText(self.reduced_draft.format.upper() if self.reduced_draft.format != "webp" else "WebP")
        reduced_form.addRow("Reduced copy format", self.reduced_format)
        self.reduced_quality = QSpinBox()
        self.reduced_quality.setRange(1, 100)
        self.reduced_quality.setValue(self.reduced_draft.jpeg_quality if self.reduced_draft.format == "jpeg" else self.reduced_draft.webp_quality)
        reduced_form.addRow("Quality", self.reduced_quality)
        self.reduced_quality_label = reduced_form.labelForField(self.reduced_quality)
        reduced_box.addLayout(reduced_form)
        self.reduced_advanced = AdvancedSettingsWidget(self.reduced_draft, self.reduced_draft.format)
        reduced_box.addWidget(self.reduced_advanced)
        resize_box.addWidget(self.reduced_controls)
        self.reduced_custom.toggled.connect(self._refresh)
        self.reduced_format.currentIndexChanged.connect(self._refresh)
        self.reduced_advanced.lossless.toggled.connect(self._refresh)
        self.reduced_advanced.reset_requested.connect(lambda: self.reduced_quality.setValue(90))
        resize_scroll = QScrollArea()
        resize_scroll.setWidgetResizable(True)
        resize_scroll.setWidget(resize_tab)
        tabs.addTab(resize_scroll, "Resize")

        self.format.currentIndexChanged.connect(self._refresh)
        self.spread_format.currentIndexChanged.connect(self._refresh)
        self.size_output.currentIndexChanged.connect(self._refresh)
        self.advanced.lossless.toggled.connect(self._refresh)
        note = QLabel("The reduced copy uses the Format tab settings unless separate settings are enabled on the Resize tab.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#617080;")
        box.addWidget(note)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("OK")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        box.addLayout(buttons)
        self._refresh()

    def _refresh(self, *_):
        full = self.size_output.currentIndex() != 1
        reduced = self.size_output.currentIndex() != 0
        need_format = not self.keep_originals or reduced
        self.format.setVisible(need_format)
        self.format_label.setVisible(need_format)
        self.format_label.setText("Reduced copy format" if self.keep_originals else "Output format")
        self.spread_format.setVisible(self.keep_originals and full)
        self.spread_format_label.setVisible(self.keep_originals and full)
        spread_jpeg = self.keep_originals and full and self.spread_format.currentIndex() == 1
        jpeg = (need_format and self.format.currentText() == "JPEG") or spread_jpeg
        webp = (need_format and self.format.currentText() == "WebP") or (self.keep_originals and full and self.spread_format.currentIndex() == 2)
        self.jpeg_quality.setVisible(jpeg)
        self.jpeg_quality_label.setVisible(jpeg)
        self.jpeg_quality_label.setText("Edited page JPEG quality" if spread_jpeg and not need_format else "JPEG quality")
        self.webp_quality.setVisible(webp)
        self.webp_quality_label.setVisible(webp)
        self.webp_quality_label.setText("WebP lossless effort" if self.advanced.lossless.isChecked() else "WebP quality")
        active_format = self.format.currentText() if need_format else self.spread_format.currentText()
        self.advanced.format.setCurrentIndex({"JPEG": 0, "WebP": 1}.get(active_format, 2))
        self.resize_settings.setEnabled(reduced)
        self.reduced_custom.setEnabled(reduced)
        self.reduced_controls.setVisible(reduced and self.reduced_custom.isChecked())
        self.reduced_advanced.format.setCurrentIndex(self.reduced_format.currentIndex())
        self.reduced_quality.setVisible(self.reduced_format.currentText() != "PNG")
        self.reduced_quality_label.setVisible(self.reduced_format.currentText() != "PNG")
        self.reduced_quality_label.setText("Lossless effort" if self.reduced_format.currentText() == "WebP" and self.reduced_advanced.lossless.isChecked() else "Quality")

    def apply_to(self, opt: ExportOptions) -> dict:
        opt.format = self.format.currentText().lower()
        opt.spread_format = ("png", "jpeg", "webp")[self.spread_format.currentIndex()]
        opt.jpeg_quality = self.jpeg_quality.value()
        opt.webp_quality = self.webp_quality.value()
        opt.size_output = ("full", "reduced", "both")[self.size_output.currentIndex()]
        self.advanced.apply()
        for name in ("jpeg_progressive", "jpeg_optimize", "jpeg_subsampling", "jpeg_smooth",
                     "webp_lossless", "webp_method", "png_compress"):
            setattr(opt, name, getattr(self.draft, name))
        self.reduced_advanced.apply()
        opt.reduced_custom = self.reduced_custom.isChecked()
        self.reduced_draft.format = self.reduced_format.currentText().lower()
        self.reduced_draft.jpeg_quality = self.reduced_quality.value()
        self.reduced_draft.webp_quality = self.reduced_quality.value()
        opt.reduced_settings = {name: getattr(self.reduced_draft, name) for name in ENCODER_FIELDS}
        return self.resize_settings.values()


class PdfPreviewWorker(QThread):
    """Read PDF dimensions and a small page preview without blocking the dialog."""
    ready = Signal(int, int, object, object)
    failed = Signal(str)
    resolution_found = Signal(object)  # dpi of the PDF's page images, or None

    def __init__(self, source: Path, right_to_left: bool = False):
        super().__init__()
        self.source = source
        self.right_to_left = right_to_left
        self.condition = Condition()
        self.requested = None
        self.stopping = False

    def request(self, generation: int, index: int, smooth_images: bool):
        with self.condition:
            self.requested = (generation, index, smooth_images)
            self.condition.notify()

    def stop(self):
        with self.condition:
            self.stopping = True
            self.requested = None
            self.condition.notify_all()

    def run(self):
        try:
            found = suggest_pdf_dpi(self.source)
        except Exception as exc:  # A suggestion is optional; the preview reports real problems.
            diagnostics.note("PDF resolution not measured: %s", exc)
            found = None
        self.resolution_found.emit(found)
        try:
            import pypdfium2 as pdfium
            document = pdfium.PdfDocument(self.source)
            try:
                count = len(document)
                if not count:
                    raise ValueError("The PDF has no pages.")
                while True:
                    with self.condition:
                        while self.requested is None and not self.stopping:
                            self.condition.wait()
                        if self.stopping:
                            return
                        generation, index, smooth_images = self.requested
                        self.requested = None
                    source_index = min(index, count - 1)
                    if self.right_to_left:
                        source_index = count - 1 - source_index
                    page = document[source_index]
                    try:
                        points = page.get_size()
                        if min(points) <= 0:
                            raise ValueError("This page has invalid dimensions.")
                        scale = min(260 / points[0], 340 / points[1])
                        bitmap = page.render(scale=scale, fill_color=(255, 255, 255, 255),
                                             no_smoothimage=not smooth_images)
                        try:
                            image = bitmap.to_pil()
                            pixels = pixels_from_pil(image, (260, 340))
                        finally:
                            bitmap.close()
                    finally:
                        page.close()
                    self.ready.emit(generation, count, points, pixels)
            finally:
                document.close()
        except Exception as exc:
            self.failed.emit(str(exc))


class PdfImportDialog(OutlinedWindow, QDialog):
    def __init__(self, source: Path, dpi: int, workers: int,
                 smooth_images: bool = True, parent=None, *, manual: bool = False, right_to_left: bool = False):
        super().__init__(parent)
        self._setup_outline()  # GNOME on Wayland: a visible window edge
        self.source = source
        self.preview_generation = 0
        self.current_size = None
        self.setWindowTitle("Import PDF")
        self.resize(750, 475)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(16)
        box = QHBoxLayout()
        box.setSpacing(28)
        outer.addLayout(box)
        settings = QVBoxLayout()
        settings.setSpacing(18)
        settings.addWidget(ui_heading("Render settings"))
        explanation = QLabel(
            "Each page becomes a temporary PNG.\n\nNo edge scanning runs in manual mode.\n\n"
            "A long book can take several minutes." if manual else
            "Each page becomes a temporary PNG.\n\nA long book can take several minutes.\n\n"
            "")
        explanation.setWordWrap(True)
        settings.addWidget(explanation)
        form = QFormLayout()
        self.dpi = QSpinBox()
        self.dpi.setRange(72, 600)
        self.dpi.setSuffix(" dpi")
        self.dpi.setValue(dpi)
        form.addRow("Rendering resolution", self.dpi)
        form_hint(form, self.dpi,
                  "Higher resolution keeps more detail for fine text and line art, but takes longer "
                  "and uses more memory and temporary disk space. Default: 300 dpi. "
                  "When the PDF's pages are images, it is set to match them.")
        self.usual_dpi = dpi
        self.suggested_dpi = None
        self.resolution_checked = False
        self.dpi_chosen_by_user = False
        self._setting_dpi = False
        self.dpi_note = QLabel("Checking the resolution of this PDF's images…")
        self.dpi_note.setWordWrap(True)
        form.addRow("", self.dpi_note)
        self.dpi.valueChanged.connect(self._dpi_changed)
        self.workers = QSpinBox()
        self.workers.setRange(0, 8)
        self.workers.setSpecialValueText(f"Automatic ({pdf_workers()} processes)")
        self.workers.setValue(workers)
        self.workers.hide()
        form_hint(form, self.workers,
                  "Independent processes render different groups of PDF pages. Automatic uses "
                  "up to four to limit memory use. Fewer processes leave more capacity for other programs.")
        self.dpi.valueChanged.connect(self.update_dimensions)
        self.smooth_images = QCheckBox("Smooth embedded images")
        self.smooth_images.setChecked(smooth_images)
        form.addRow("", self.smooth_images)
        form_hint(form, self.smooth_images,
                  "PDFium smooths images when scaling them by default. Turn this off to compare "
                  "harder image edges; it may introduce jagged pixels. Text and vector drawing "
                  "remain antialiased, and color handling is unchanged.")
        self.smooth_images.toggled.connect(self._request_preview)
        settings.addLayout(form)
        settings.addStretch()
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.start_button = QPushButton("Render and review" if manual else "Render and scan")
        self.start_button.setDefault(True)
        ui_primary(self.start_button)
        self.start_button.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.start_button)
        box.addLayout(settings, 1)

        right = QVBoxLayout()
        self.preview_label = QLabel("Loading page preview…")
        self.preview_label.setFixedSize(260, 340)
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setStyleSheet("background:#20242a; color:#d6dde5; border-radius:4px;")
        right.addWidget(self.preview_label, alignment=Qt.AlignCenter)
        self.dimensions = QLabel("Reading page dimensions…")
        self.dimensions.setMinimumWidth(300)
        self.dimensions.setAlignment(Qt.AlignCenter)
        self.dimensions.setWordWrap(True)
        right.addWidget(self.dimensions)
        page_row = QHBoxLayout()
        previous = QPushButton("←")
        previous.clicked.connect(lambda: self.page.setValue(self.page.value() - 1))
        page_row.addWidget(previous)
        page_row.addWidget(QLabel("Page"))
        self.page = QSpinBox()
        self.page.setRange(1, 1)
        self.page.valueChanged.connect(self._request_preview)
        page_row.addWidget(self.page)
        next_page = QPushButton("→")
        next_page.clicked.connect(lambda: self.page.setValue(self.page.value() + 1))
        page_row.addWidget(next_page)
        box.addLayout(right)
        footer = QHBoxLayout()
        footer.addLayout(page_row)
        footer.addStretch()
        footer.addLayout(buttons)
        outer.addLayout(footer)
        self.preview_worker = PdfPreviewWorker(source, right_to_left=right_to_left)
        self.preview_worker.ready.connect(self._preview_ready)
        self.preview_worker.failed.connect(self._preview_failed)
        self.preview_worker.resolution_found.connect(self._resolution_found)
        self.preview_worker.start()
        self._request_preview()

    def _dpi_changed(self, *_):
        if not self._setting_dpi:
            self.dpi_chosen_by_user = True
        self._update_dpi_note()

    def _resolution_found(self, dpi):
        self.resolution_checked = True
        self.suggested_dpi = dpi
        if dpi is not None and not self.dpi_chosen_by_user:
            self._setting_dpi = True
            try:
                self.dpi.setValue(dpi)
            finally:
                self._setting_dpi = False
        self._update_dpi_note()

    def _update_dpi_note(self):
        found = self.suggested_dpi
        if found is None:
            # Still checking, or the pages are not mostly images (text and drawings).
            self.dpi_note.setVisible(not self.resolution_checked)
            return
        self.dpi_note.show()
        value = self.dpi.value()
        if value == found:
            text = f"Matches the images in this PDF (about {found} dpi)."
            if not self.dpi_chosen_by_user and found != self.usual_dpi:
                text += f" Your usual setting is {self.usual_dpi} dpi."
        elif value > found:
            text = (f"The images in this PDF are about {found} dpi. A higher setting makes "
                    "larger pages without adding detail to them.")
        else:
            text = (f"The images in this PDF are about {found} dpi. A lower setting makes "
                    "smaller pages with less of their detail.")
        self.dpi_note.setText(text)

    def _request_preview(self, *_):
        self.preview_generation += 1
        self.preview_label.setText("Loading page preview…")
        self.current_size = None
        self.update_dimensions()
        self.preview_worker.request(self.preview_generation, self.page.value() - 1,
                                    self.smooth_images.isChecked())

    def _preview_ready(self, generation, count, points, pixels):
        if generation != self.preview_generation:
            return
        with QSignalBlocker(self.page):
            self.page.setMaximum(count)
        self.current_size = points
        self.preview_label.setPixmap(QPixmap.fromImage(qimage_from_pixels(pixels)))
        self.update_dimensions()

    def _preview_failed(self, message):
        self.preview_label.setText("Preview unavailable")
        self.dimensions.setText(f"Could not read page: {message}")

    def update_dimensions(self, *_):
        if self.current_size is None:
            self.dimensions.setText("Reading page dimensions…")
        else:
            width, height = rendered_dimensions(self.current_size, self.dpi.value())
            self.dimensions.setText(f"Page {self.page.value()} output: {width:,} × {height:,} px "
                                    f"at {self.dpi.value()} dpi\nPreview scaled to fit")

    def done(self, result):
        self.preview_worker.stop()
        self.preview_worker.wait()
        super().done(result)


class CropThumbnailList(QListWidget):
    """End spacers let the first and last real thumbnails also center."""

    def __init__(self):
        super().__init__()
        self.leading = QListWidgetItem()
        self.trailing = QListWidgetItem()
        for item in (self.leading, self.trailing):
            item.setFlags(Qt.NoItemFlags)
            QListWidget.addItem(self, item)

    def addItem(self, text):
        item = QListWidgetItem(text)
        item.setSizeHint(QSize(128, 110))
        QListWidget.insertItem(self, QListWidget.count(self) - 1, item)

    def item(self, index):
        return QListWidget.item(self, index + 1)

    def currentRow(self):
        return QListWidget.currentRow(self) - 1

    def setCurrentRow(self, index):
        QListWidget.setCurrentRow(self, index + 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        padding = max(0, self.viewport().width() // 2 - 64)
        for item in (self.leading, self.trailing):
            item.setSizeHint(QSize(padding, 110))
        self.doItemsLayout()


class ScreenshotCropDialog(OutlinedWindow, QDialog):
    """Review the book's crop guide over the full capture before staging."""

    def __init__(self, proposals, single_size=None, parent=None, calibration=None):
        super().__init__(parent)
        self._setup_outline()  # GNOME on Wayland: a visible window edge
        self.setWindowTitle("Review screenshot crops")
        self.setWindowFlags(self.windowFlags() | Qt.WindowMinMaxButtonsHint)
        self.setSizeGripEnabled(True)
        self._initial_screen_fit = False
        self.back_to_summary = False
        self.preview_generation = 0
        self.preview_cache = OrderedDict()
        self.preview_pending = None
        self.crop_preview_worker = CropPreviewWorker()
        self.crop_preview_worker.ready.connect(self._crop_preview_ready)
        self.crop_preview_worker.failed.connect(self._crop_preview_failed)
        self.crop_thumb_worker = ThumbnailWorker()
        self.crop_thumb_worker.ready.connect(self._crop_thumb_ready)
        self.crop_thumb_loaded = set()
        self.finished.connect(self._stop_crop_workers)
        self.resize(1320, 850)
        self.proposals = proposals
        self.sources = [item.path for item in proposals]
        self.canvases = [item.canvas for item in proposals]
        self.crop_thumb_generation = self.crop_thumb_worker.reset([(path, None, 0) for path in self.sources])
        self.replaced: set[int] = set()
        self.sizes = {}
        for proposal in calibration or proposals:
            known = self.sizes.setdefault(proposal.canvas, {})
            if proposal.source == "measured" and proposal.kind in ("single", "double"):
                known[proposal.kind] = max(known.get(proposal.kind, 0), proposal.page_width)
        for canvas, known in self.sizes.items():
            if single_size is not None and abs(canvas[1] - single_size[1]) <= 2:
                known.update(single=single_size[0], double=single_size[0] * 2)
            if "double" in known and "single" not in known:
                known["single"] = round(known["double"] / 2)
            if "single" in known and "double" not in known:
                known["double"] = known["single"] * 2
        self.fallback_size = single_size
        self.base_size = single_size or next(((values["single"], canvas[1])
                                              for canvas, values in self.sizes.items()
                                              if "single" in values), None)
        self.choices: list[CropChoice | None] = []
        for item in proposals:
            if item.box is not None and item.kind in ("single", "double"):
                self.choices.append(CropChoice(item.path, item.kind, item.box,
                                               item.source != "measured"))
            else:
                self.choices.append(None)
        outer = QVBoxLayout(self)
        instruction = QLabel("Check the crop outlines. Choose a type for any screenshot marked Review.")
        instruction.setWordWrap(True)
        outer.addWidget(instruction)
        book_row = QHBoxLayout()
        book_row.addWidget(QLabel("Book single-page size: width"))
        self.book_width = QSpinBox()
        self.book_width.setRange(100, 30000)
        self.book_width.setValue(self.base_size[0] if self.base_size else 1988)
        book_row.addWidget(self.book_width)
        book_row.addWidget(QLabel("height"))
        self.book_height = QSpinBox()
        self.book_height.setRange(100, 30000)
        self.book_height.setValue(self.base_size[1] if self.base_size else 3056)
        book_row.addWidget(self.book_height)
        recalibrate = QPushButton("Use these dimensions")
        recalibrate.clicked.connect(self.set_book_size)
        book_row.addWidget(recalibrate)
        book_row.addStretch()
        self.book_size_widget = QWidget()
        self.book_size_widget.setLayout(book_row)
        self.book_size_widget.hide()
        outer.addWidget(self.book_size_widget)
        self.family_selection = {}
        self.family_combo = QComboBox()
        self.family_combo.setToolTip("Choose the measured page-size family for this screenshot, then choose Single or Double. The crop outline updates to match.")
        self.family_combo.currentIndexChanged.connect(self._select_crop_family)
        outer.addWidget(self.family_combo)
        content = QHBoxLayout()
        self.list = CropThumbnailList()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setFlow(QListWidget.LeftToRight)
        self.list.setWrapping(False)
        self.list.setIconSize(QSize(110, 82))
        self.list.setSpacing(0)
        self.list.setFixedHeight(126)
        for index in range(len(proposals)):
            self.list.addItem("")
            self._label_item(index)

        side = QVBoxLayout()
        self.page_label = QLabel()
        side.addWidget(self.page_label)
        self.preview_image = FitImage()
        side.addWidget(self.preview_image, 1)
        legend = QLabel("Blue: double  ·  Red: sideways double  ·  Yellow: single. "
                        "The bright pair shows the selected crop.")
        legend.setWordWrap(True)
        side.addWidget(legend)
        choices = QHBoxLayout()
        for caption, kind in (("Single", "single"), ("Double", "double"),
                              ("Sideways — keep", "sideways")):
            button = QPushButton(caption)
            button.clicked.connect(lambda _=False, k=kind: self.choose(k))
            review_control(button, {"single": "▯", "double": "▯▯", "sideways": "▤"}[kind], caption.replace(" — keep", ""),
                           "Select the crop for a sideways spread; this does not rotate the image." if kind == "sideways" else f"Use the {kind}-page crop")
            choices.addWidget(button)
        replace_button = QPushButton("Replace screenshot…")
        replace_button.clicked.connect(self.replace_screenshot)
        review_control(replace_button, "▯", "Replace", "Replace this screenshot with another image")
        choices.addWidget(replace_button)
        choices.insertStretch(0)
        choices.addStretch()
        side.insertLayout(1, choices)
        manual = QHBoxLayout()
        manual.addWidget(QLabel("Adjust crop: left"))
        self.crop_left = QSpinBox()
        self.crop_left.setRange(0, 30000)
        manual.addWidget(self.crop_left)
        manual.addWidget(QLabel("right (exclusive)"))
        self.crop_right = QSpinBox()
        self.crop_right.setRange(1, 30000)
        manual.addWidget(self.crop_right)
        adjust = QPushButton("Use adjusted crop")
        adjust.clicked.connect(self.adjust_crop)
        manual.addWidget(adjust)
        nevermind = QPushButton("Cancel")
        nevermind.clicked.connect(lambda: self.guide_edit_button.setChecked(False))
        manual.addWidget(nevermind)
        self.manual_widget = QWidget()
        self.manual_widget.setLayout(manual)
        self.manual_widget.hide()
        edit = QPushButton("Adjust crop…")
        review_control(edit, "▯", "Adjust", "Adjust crop boundaries and book dimensions")
        edit.setCheckable(True)
        edit.toggled.connect(self.manual_widget.setVisible)
        edit.toggled.connect(self.book_size_widget.setVisible)
        edit.toggled.connect(lambda _: self.show_page(self.list.currentRow()))
        choices.insertWidget(choices.count() - 1, edit)
        self.guide_edit_button = edit
        side.insertWidget(2, self.manual_widget)
        content.addLayout(side, 1)
        outer.addLayout(content, 1)
        outer.addWidget(self.list)
        controls = QHBoxLayout()
        controls.addStretch()
        back = QPushButton("Back to summary")
        back.clicked.connect(self._return_to_summary)
        cancel = QPushButton("Cancel this import")
        cancel.clicked.connect(self.reject)
        controls.addWidget(back)
        self.crop_footer_buttons = [back, cancel]
        approve = QPushButton("Approve crops and continue")
        self.crop_footer_buttons.append(approve)
        approve.clicked.connect(self.approve)
        controls.addWidget(cancel)
        controls.addWidget(approve)
        outer.addLayout(controls)
        self.crop_shortcuts = []
        for key, step in (("Left", -1), ("Right", 1)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(lambda d=step: self.list.setCurrentRow(max(0, min(len(self.sources) - 1, self.list.currentRow() + d))))
            self.crop_shortcuts.append(shortcut)
        self.list.horizontalScrollBar().valueChanged.connect(self._request_crop_thumbnails)
        self.list.currentRowChanged.connect(lambda row: self.show_page(row - 1))
        self.list.setCurrentRow(next((i for i, c in enumerate(self.choices) if c is None), 0))

    def _fit_available_screen(self):
        screen = self.screen() or QApplication.primaryScreen()
        if screen:
            bounds = screen.availableGeometry().adjusted(20, 30, -20, -30)
            frame_extra = self.frameGeometry().size() - self.size()
            self.resize(min(1320, bounds.width() - frame_extra.width()),
                        min(850, bounds.height() - frame_extra.height()))
            frame = self.frameGeometry()
            self.move(bounds.x() + (bounds.width() - frame.width()) // 2,
                      bounds.y() + (bounds.height() - frame.height()) // 2)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._initial_screen_fit:
            self._initial_screen_fit = True
            self._fit_available_screen()
        for worker in (self.crop_preview_worker, self.crop_thumb_worker):
            if not worker.isRunning() and not worker.stopping:
                worker.start()
        QTimer.singleShot(0, self._request_crop_thumbnails)

    def _stop_crop_workers(self, *_):
        for worker in (self.crop_preview_worker, self.crop_thumb_worker):
            worker.stop()
            worker.wait()

    def closeEvent(self, event):
        self._stop_crop_workers()
        super().closeEvent(event)

    def _return_to_summary(self):
        self.back_to_summary = True
        self.reject()

    def _center_crop_thumbnail(self):
        index = self.list.currentRow()
        if index < 0:
            return
        rect = self.list.visualItemRect(self.list.item(index))
        scrollbar = self.list.horizontalScrollBar()
        scrollbar.setValue(scrollbar.value() + rect.center().x() - self.list.viewport().width() // 2)

    def _request_crop_thumbnails(self, *_):
        viewport = self.list.viewport().rect()
        for index in range(len(self.sources)):
            if index not in self.crop_thumb_loaded and self.list.visualItemRect(self.list.item(index)).intersects(viewport):
                self.crop_thumb_worker.request(self.crop_thumb_generation, index)

    def _crop_thumb_ready(self, generation, index, pixels):
        if generation != self.crop_thumb_generation:
            return
        from PySide6.QtGui import QIcon
        self.list.item(index).setIcon(QIcon(QPixmap.fromImage(qimage_from_pixels(pixels))))
        self.crop_thumb_loaded.add(index)

    def _crop_preview_ready(self, generation, pixels):
        if generation != self.preview_generation or self.preview_pending is None:
            return
        self.preview_cache[self.preview_pending] = QPixmap.fromImage(qimage_from_pixels(pixels))
        self.preview_pending = None
        while len(self.preview_cache) > 4:
            self.preview_cache.popitem(last=False)
        self.show_page(self.list.currentRow())

    def _crop_preview_failed(self, generation, message):
        if generation == self.preview_generation:
            self.preview_pending = None
            self.preview_image.setText(f"Could not load screenshot: {message}")

    def _label_item(self, index):
        choice = self.choices[index]
        self.list.item(index).setText(f"{index + 1}" + (" · Review" if choice is None else ""))
        self.list.item(index).setToolTip(str(self.sources[index]))

    def _select_crop_family(self, *_):
        index = self.list.currentRow()
        width = self.family_combo.currentData()
        if index >= 0 and width is not None:
            self.family_selection[index] = width
            choice = self.choices[index]
            if choice is not None:
                self.choices[index] = None
                self._label_item(index)
            self.show_page(index)

    def _dimensions(self, index):
        proposal = self.proposals[index]
        family = self.family_selection.get(index, proposal.family_width)
        if family is not None and proposal.family_widths:
            return {"single": family, "double": family * 2}
        canvas = self.canvases[index]
        known = self.sizes.get(canvas, {}).copy()
        if not known and self.base_size:
            known = {"single": self.base_size[0], "double": 2 * self.base_size[0]}
        # A replacement screenshot for a sideways spread may use a taller
        # emulator canvas. Its page width scales with the capture height.
        return known

    def _guide(self, kind, index):
        width, height = self.canvases[index]
        known = self._dimensions(index)
        if "double" not in known:
            return None
        if kind == "sideways":
            standard_height = self.base_size[1] if self.base_size else height
            crop_width = round(standard_height * height / known["double"])
        else:
            crop_width = known.get(kind)
        if crop_width is None or crop_width > width:
            return None
        left = (width - crop_width) // 2
        return (left, 0, left + crop_width, height)

    def show_page(self, index):
        if index < 0:
            return
        QTimer.singleShot(0, self._center_crop_thumbnail)
        path = self.sources[index]
        canvas = self.canvases[index]
        choice = self.choices[index]
        proposal = self.proposals[index]
        self.family_combo.blockSignals(True)
        self.family_combo.clear()
        self.family_combo.setVisible(bool(proposal.family_widths))
        for width in proposal.family_widths:
            self.family_combo.addItem(f"Page-size family: {width} × {canvas[1]} single / {width * 2} × {canvas[1]} double", width)
        selected = self.family_selection.get(index, proposal.family_width)
        if selected is not None:
            self.family_combo.setCurrentIndex(self.family_combo.findData(selected))
        elif proposal.family_widths:
            self.family_selection[index] = proposal.family_widths[0]
        self.family_combo.blockSignals(False)
        self.page_label.setText(f"Screenshot {index + 1} of {len(self.sources)}" +
                                (f" · {choice.kind.title()}" if choice else " · Choose a crop type"))
        box = choice.box if choice else (0, 0, canvas[0], canvas[1])
        self.crop_left.setMaximum(canvas[0] - 1)
        self.crop_right.setMaximum(canvas[0])
        self.crop_left.setValue(box[0])
        self.crop_right.setValue(box[2])
        self._request_crop_thumbnails()
        if path not in self.preview_cache:
            if self.preview_pending != path:
                self.preview_generation += 1
                self.preview_pending = path
                self.crop_preview_worker.request(self.preview_generation, path)
            self.preview_image.show_pixmap(None)
            self.preview_image.setText("Loading screenshot preview…")
            return
        pixmap = self.preview_cache[path]
        self.preview_cache.move_to_end(path)
        painted = QPixmap(pixmap)
        painter = QPainter(painted)
        if choice:
            left, _, right, _ = choice.box
            scale = painted.width() / canvas[0]
            shade = QColor(0, 0, 0, 110)
            painter.fillRect(0, 0, round(left * scale), painted.height(), shade)
            painter.fillRect(round(right * scale), 0, painted.width() - round(right * scale),
                             painted.height(), shade)
        for kind, color in (("double", "#20a9ff"), ("sideways", "#ff526b"),
                            ("single", "#ffe55c")):
            guide = choice.box if choice and choice.kind == kind else self._guide(kind, index)
            if choice and not self.guide_edit_button.isChecked() and choice.kind != kind:
                continue
            if guide:
                painter.setPen(QPen(QColor(color), 4 if choice and choice.kind == kind else 2))
                for edge in (guide[0], guide[2]):
                    x = round(edge * painted.width() / canvas[0])
                    painter.drawLine(x, 0, x, painted.height())
        painter.end()
        self.preview_image.show_pixmap(painted)
        box = choice.box if choice else (0, 0, canvas[0], canvas[1])
        self.crop_left.setMaximum(canvas[0] - 1)
        self.crop_right.setMaximum(canvas[0])
        self.crop_left.setValue(box[0])
        self.crop_right.setValue(box[2])

    def set_book_size(self):
        self.base_size = (self.book_width.value(), self.book_height.value())
        for canvas in list(self.sizes) + [c for c in self.canvases if c not in self.sizes]:
            if abs(canvas[1] - self.base_size[1]) <= max(2, round(self.base_size[1] * .002)):
                self.sizes[canvas] = {"single": self.base_size[0],
                                      "double": self.base_size[0] * 2}
        for index, choice in enumerate(self.choices):
            if (choice is not None and not choice.custom and
                    (choice.inferred or self.proposals[index].box is None or
                     choice.kind != self.proposals[index].kind)):
                box = self._guide(choice.kind, index)
                if box:
                    self.choices[index] = CropChoice(choice.source, choice.kind, box,
                                                     choice.inferred, flagged=choice.flagged)
                    self._label_item(index)
        self.show_page(self.list.currentRow())

    def choose(self, kind):
        index = self.list.currentRow()
        box = self._guide(kind, index)
        if box is None:
            QMessageBox.warning(self, "No page size", "Enter a single-page size on import, "
                                "or adjust this crop by hand after measuring a clear page.")
            return
        old = self.choices[index]
        self.choices[index] = CropChoice(self.sources[index], kind, box,
                                         old.inferred if old and old.kind == kind else False,
                                         flagged=(index not in self.replaced and
                                                  (kind == "sideways" or
                                                   self.proposals[index].review_reason is not None and
                                                   "middle width" in self.proposals[index].review_reason)))
        self._label_item(index)
        self.show_page(index)

    def adjust_crop(self):
        index = self.list.currentRow()
        left, right = self.crop_left.value(), self.crop_right.value()
        if left >= right:
            QMessageBox.warning(self, "Crop", "The right boundary must be after the left boundary.")
            return
        previous = self.choices[index]
        if previous is None:
            QMessageBox.information(self, "Choose a type", "Choose Single, Double, or Sideways first.")
            return
        self.choices[index] = CropChoice(self.sources[index], previous.kind,
                                         (left, 0, right, self.canvases[index][1]), True, True,
                                         previous.flagged)
        self._label_item(index)
        self.guide_edit_button.setChecked(False)
        self.show_page(index)

    def replace_screenshot(self):
        index = self.list.currentRow()
        path, _ = QFileDialog.getOpenFileName(self, "Replace this screenshot", "",
                                              "Images (*.png *.jpg *.jpeg *.webp *.tif *.tiff *.bmp)")
        if not path:
            return
        replacement = Path(path)
        try:
            with Image.open(replacement) as image:
                canvas = image.size
                image.verify()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Replace screenshot", str(exc))
            return
        self.preview_cache.pop(self.sources[index], None)
        self.preview_pending = None
        self.sources[index] = replacement
        self.canvases[index] = canvas
        self.crop_thumb_generation = self.crop_thumb_worker.reset([(p, None, 0) for p in self.sources])
        self.crop_thumb_loaded.clear()
        self.replaced.add(index)
        self.choices[index] = None
        self._label_item(index)
        self.show_page(index)

    def approve(self):
        unresolved = next((index for index, choice in enumerate(self.choices) if choice is None), None)
        if unresolved is not None:
            self.list.setCurrentRow(unresolved)
            QMessageBox.information(self, "Review crop", "Choose a crop type for every Review item.")
            return
        self.accept()

    def approved_choices(self):
        return list(self.choices)


class ImportDropZone(QFrame):
    """Clickable target for a file or folder, with a matching browse button."""

    chosen = Signal()
    folder_chosen = Signal()
    dropped = Signal(object)

    def __init__(self, heading: str, detail: str, browse: str, parent=None, *, folder_browse=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            "ImportDropZone { background: #f5f8fb; border: 2px dashed #94a9bd; "
            "border-radius: 14px; } "
            "ImportDropZone:hover { background: #eaf3fa; border-color: #397cac; } "
            "ImportDropZone QLabel { border: none; background: transparent; } ")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 20, 18, 20)
        layout.setSpacing(12)
        layout.addStretch()
        icon = QLabel()
        icon.setPixmap(ui_icon(self, "folder" if heading == "Folder of images" else "COMIC", 84))
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("font-size: 84px; font-weight: 700; color: #397cac;")
        icon.setMinimumHeight(100)
        icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(icon)
        title = QLabel(heading)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 19px; font-weight: 600; color: #1d3348;")
        title.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(title)
        subtitle = QLabel(detail)
        subtitle.setWordWrap(True)
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: #526577;")
        subtitle.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(subtitle)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.files_button = QPushButton(browse)
        self.files_button.clicked.connect(self.chosen)
        buttons.addWidget(self.files_button)
        if folder_browse:
            self.folder_button = QPushButton(folder_browse)
            self.folder_button.clicked.connect(self.folder_chosen)
            buttons.addWidget(self.folder_button)
        buttons.addStretch()
        layout.addLayout(buttons)
        layout.addStretch()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.chosen.emit()
            event.accept()
        else:
            super().mousePressEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls()
                 if url.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            # Return control to the drag source before opening modal import dialogs.
            QTimer.singleShot(0, lambda: self.dropped.emit(paths))


class ImportChoiceDialog(OutlinedWindow, QDialog):
    """Confirm the detected folder workflow and the pair review style."""

    def __init__(self, source: Path, *, folder: bool, screenshot_hint: bool = False,
                 sampled: int = 0, pdf_smooth_images: bool = True, parent=None):
        super().__init__(parent)
        self._setup_outline()  # GNOME on Wayland: a visible window edge
        self.setWindowTitle("Start comic import")
        self.resize(540, 310)
        self.setStyleSheet("QComboBox { min-height:28px; } QPushButton { padding:7px 14px; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)
        layout.addWidget(ui_heading("Import this folder?" if folder else "Import this file?"))
        source_row = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(ui_icon(self, "folder" if folder else source.suffix[1:].upper(), 56))
        source_row.addWidget(icon)
        filename = ui_heading(source.name, 18)
        source_row.addWidget(filename, 1)
        layout.addLayout(source_row)
        if folder:
            detail = (f"Checked {sampled} sample images. "
                      + ("They look like emulator screenshots with black surroundings."
                         if screenshot_hint else "They look like ordinary comic pages."))
            if sampled == 0:
                detail = "No readable images could be sampled. The folder type could not be determined."
            hint = QLabel(detail)
            hint.setWordWrap(True)
            layout.addWidget(hint)
            row = QFormLayout()
            self.folder_type = QComboBox()
            self.folder_type.addItems(["Comic page images", "Emulator screenshots — crop and split"])
            self.folder_type.setCurrentIndex(int(screenshot_hint))
            row.addRow("Folder contains", self.folder_type)
            layout.addLayout(row)
        else:
            self.folder_type = None
            file_types = {
                ".pdf": "Comic Stitcher will check how to import this PDF before pair review.",
                ".cbz": "Pages will be extracted from this CBZ comic archive before pair review.",
                ".cb7": "Pages will be extracted from this CB7 comic archive before pair review.",
                ".cbr": "Pages will be extracted from this CBR comic archive before pair review.",
                ".zip": "Pages will be extracted from this ZIP archive before pair review.",
                ".7z": "Pages will be extracted from this 7z archive before pair review.",
                ".rar": "Pages will be extracted from this RAR archive before pair review.",
            }
            self.file_description = QLabel(file_types.get(source.suffix.lower(),
                                                         "Comic Stitcher will import this file."))
            self.file_description.setWordWrap(True)
            layout.addWidget(self.file_description)
        self.pdf_smooth_images = None
        row = QFormLayout()
        self.review_mode = QComboBox()
        self.review_mode.addItems(["Suggested pairs", "Manual — every neighboring pair"])
        self.review_mode.setToolTip(tooltip_text(
            "Suggested pairs analyzes page edges and shows Likely and Possible matches. "
            "Manual shows every neighboring pair without scoring their edges."))
        row.addRow("Pair review", self.review_mode)
        self.right_to_left = QCheckBox("Right-to-left page order")
        self.right_to_left.setToolTip(tooltip_text(
            "Use when the front cover is the last page or image in the source. Reverse the source page "
            "sequence before pair detection, then check and stitch spreads in right-to-left order. "
            "The finished comic is numbered from the front cover onward."))
        row.addRow(self.right_to_left)
        layout.addLayout(row)
        controls = QHBoxLayout()
        controls.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        start = QPushButton("Continue")
        ui_primary(start)
        start.setDefault(True)
        start.clicked.connect(self.accept)
        controls.addWidget(cancel)
        controls.addWidget(start)
        layout.addLayout(controls)

    def screenshots(self) -> bool:
        return self.folder_type is not None and self.folder_type.currentIndex() == 1


@dataclass
class QueuedComic:
    plan: BookPlan
    options: ExportOptions
    session_source: Path | None
    import_work: tempfile.TemporaryDirectory | None
    screenshot_stage_dir: Path | None
    reimport_source: Path | None = None
    source_stat: tuple[int, int] | None = None
    pdf_options: dict | None = None


def export_queued_comic(job: QueuedComic, progress=None):
    """Recreate discarded imports for one job, then always release its working pages."""
    if job.reimport_source is None:
        return export_book(job.plan, job.options, progress=progress)
    source = job.reimport_source
    stat = source.stat()
    if (stat.st_size, stat.st_mtime_ns) != job.source_stat:
        raise ValueError("The original comic has changed since review. Import and review it again.")
    with tempfile.TemporaryDirectory(prefix="comic-stitcher-queue-") as working:
        destination = Path(working) / "pages"
        prepare_progress = (lambda n, total: progress(round(50 * n / max(1, total)), 100)) if progress else None
        if source.suffix.lower() == ".pdf":
            scan = import_pdf(source, destination, manual=job.plan.scan.manual_mode, right_to_left=job.plan.scan.right_to_left,
                              progress=prepare_progress, **(job.pdf_options or {}))
        else:
            scan = import_archive(source, destination, manual=job.plan.scan.manual_mode, right_to_left=job.plan.scan.right_to_left,
                                  progress=prepare_progress)
        old = job.plan.scan
        if ([(p.path.name, p.width, p.height) for p in scan.pages] !=
                [(p.path.name, p.width, p.height) for p in old.pages] or
                [(c.left, c.right, c.likely, c.continuity) for c in scan.candidates] !=
                [(c.left, c.right, c.likely, c.continuity) for c in old.candidates[:job.plan.base_candidate_count]]):
            raise ValueError("The imported pages or suggestions changed. Review this comic again.")
        scan.candidates = list(old.candidates)
        plan = copy.deepcopy(job.plan)
        plan.scan = scan
        export_progress = (lambda n, total: progress(50 + round(50 * n / max(1, total)), 100)) if progress else None
        return export_book(plan, job.options, progress=export_progress)


def queued_outputs(plan: BookPlan, opt: ExportOptions) -> list[Path]:
    """All final destinations, including both sizes and retained loose pages."""
    folder = plan.scan.output_folder or plan.scan.folder
    title = safe_book_name(opt.book_name)
    kinds = ("full", "reduced") if opt.size_output == "both" else (opt.size_output,)
    paths = []
    for kind in kinds:
        suffix = f" - {'Full' if kind == 'full' else 'Reduced'}" if len(kinds) == 2 else ""
        if opt.cbz:
            paths.append(folder / f"{title}{suffix}.{opt.archive_format}")
        if opt.keep_images:
            paths.append(folder / f"{title}{suffix} - PNG pages")
    return paths


class ComicWindow(OutlinedWindow, QMainWindow):
    def __init__(self):
        super().__init__()
        self._setup_outline()  # GNOME on Wayland: a visible window edge
        self.setWindowTitle("Comic Stitcher")
        icon_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
        icon_path = icon_root / "comic-stitcher.png"
        if not icon_path.exists():
            icon_path = icon_root / "assets" / "app" / "comic-stitcher.png"
        icon = QIcon(str(icon_path))
        self.setWindowIcon(icon)
        QApplication.instance().setWindowIcon(icon)
        self.compact_size = QSize(750, 500)
        self.resize(self.compact_size)
        self.setAcceptDrops(True)
        self.settings = QSettings("ComicStitcher", "Comic Stitcher")
        self.comicvine_session_key = retrieve_key(self.settings.value("comicvine_key_protected", ""))
        self.comicvine_client = ComicVineClient(self.comicvine_session_key)
        self.plan: BookPlan | None = None
        self.import_work: tempfile.TemporaryDirectory | None = None
        self.batch_import_active = False
        self.queue_jobs: list[QueuedComic] = []
        self.import_prompt_active = False
        self.pending_imports: deque[Path] = deque()
        self.active_queue_job: QueuedComic | None = None
        self.queue_completed = 0
        self.queue_results: list[Path] = []
        self.screenshot_source: Path | None = None
        self.screenshot_flagged_pages: set[int] = set()
        self.inferred_notice_shown = False
        self.screenshot_stage_dir: Path | None = None
        self.possible_review_started = False
        self.active_manual_mode = False
        self.active_right_to_left = False
        self.review_report_path: Path | None = None
        self.pending_session: dict | None = None
        self.session_source: Path | None = None
        self.pdf_session_options: dict = {}
        self.preview_checkpoint_timer = QTimer(self)
        self.preview_checkpoint_timer.setSingleShot(True)
        self.preview_checkpoint_timer.timeout.connect(self._save_preview_position)
        self.worker: Worker | None = None
        self.preview_index = 0
        self.selected_first: int | None = None
        self.comparing: tuple[int, int] | None = None
        self.seam = False
        self.boss = False
        self.thumb_generation = 0
        self.thumb_cache: OrderedDict[tuple[Path, Path | None, int], QPixmap] = OrderedDict()
        self.thumb_keys: list[tuple[Path, Path | None, int]] = []
        self.highlighted: set[int] = set()
        self.thumb_worker = ThumbnailWorker()
        self.thumb_worker.ready.connect(self._thumbnail_ready)
        self.thumb_worker.failed.connect(self._thumbnail_failed)
        self.thumb_worker.start()
        self.review_generation = 0
        self.review_worker = ReviewImageWorker()
        self.review_worker.ready.connect(self._review_images_ready)
        self.review_worker.failed.connect(self._review_images_failed)
        self.review_worker.start()
        self.opt = ExportOptions("")
        self.opt.jpeg_quality = int(self.settings.value("jpeg_quality", 90))
        self.opt.webp_quality = int(self.settings.value("webp_quality", 90))
        image_format = str(self.settings.value("output_format", "jpeg"))
        spread_format = str(self.settings.value("spread_format", "jpeg"))
        output_sizes = str(self.settings.value("output_sizes", "full"))
        self.opt.format = image_format if image_format in ("jpeg", "webp", "png") else "jpeg"
        self.opt.spread_format = spread_format if spread_format in ("png", "jpeg", "webp") else "jpeg"
        self.opt.size_output = output_sizes if output_sizes in ("full", "reduced", "both") else "full"
        for name, default in (("jpeg_progressive", False), ("jpeg_optimize", True),
                              ("jpeg_subsampling", 0), ("jpeg_smooth", 0),
                              ("webp_lossless", False), ("webp_method", 4),
                              ("png_compress", 6)):
            setattr(self.opt, name, self.settings.value(name, default, type=type(default)))
        self.opt.reduced_custom = self.settings.value("reduced_custom", False, type=bool)
        try:
            self.opt.reduced_settings = json.loads(self.settings.value("reduced_settings", "{}"))
        except (ValueError, TypeError):
            self.opt.reduced_settings = {}
        self.review_rotation = 0
        self.custom_resize_values = {
            "mode": self.settings.value("resize_mode", "height"),
            "pixels": self.settings.value("resize_pixels", 1920, type=int),
            "reduce_only": self.settings.value("resize_reduce_only", True, type=bool),
            "gamma": self.settings.value("resize_gamma", True, type=bool),
            "filter": self.settings.value("resize_filter", "bilinear"),
        }
        if self.custom_resize_values["mode"] not in ("height", "width"):
            self.custom_resize_values["mode"] = "height"
        if self.custom_resize_values["filter"] not in ("bilinear", "bicubic", "lanczos"):
            self.custom_resize_values["filter"] = "bilinear"
        if not 1 <= self.custom_resize_values["pixels"] <= 20000:
            self.custom_resize_values["pixels"] = 1920
        self.replay_base_name = ""
        self.replay_count = 0
        self.pages = CurrentPageStack()
        # The wide preview's button row must not impose its minimum width on
        # the compact import and progress screens while those pages are hidden.
        self.pages.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.setCentralWidget(self.pages)
        self._build_start()
        self._build_review()
        self._build_possible_choice()
        self._build_preview()
        self._build_export()
        self._build_progress()
        self._build_boss()
        self._install_shortcuts()
        self.pages.currentChanged.connect(self._update_shortcuts)
        self._update_shortcuts()
        QTimer.singleShot(0, self.show_startup_notice)

    def show_startup_notice(self):
        if self.settings.value("hide_source_notice", False, type=bool):
            return
        notice = QMessageBox(self)
        notice.setWindowTitle("Your originals are safe")
        notice.setText("Comic Stitcher never modifies your original comic files or images.")
        notice.setInformativeText("Cropping, joining, rotation, and conversion create separate output files. Keep your originals until you have checked the results.")
        checkbox = QCheckBox("Don’t show me this again", notice)
        notice.setCheckBox(checkbox)
        notice.exec()
        self.settings.setValue("hide_source_notice", checkbox.isChecked())

    def show_about(self):
        dialog = OutlinedDialog(self)
        dialog.setWindowTitle("About Comic Stitcher")
        dialog.resize(590, 380)
        layout = QVBoxLayout(dialog)
        layout.addWidget(ui_heading("Comic Stitcher"))
        layout.addWidget(QLabel(f"Version {__version__}"))
        credit = QLabel('Interface icons by <a href="https://phosphoricons.com/">Phosphor Icons</a> (MIT).')
        credit.setOpenExternalLinks(True)
        layout.addWidget(credit)
        layout.addWidget(QLabel("Page icons: Created by Eric Fleming."))
        logo_credit = QLabel("Application icon: Developed by Eric Fleming using Gemini and Grok, with manual editing.")
        logo_credit.setWordWrap(True)
        layout.addWidget(logo_credit)
        libraries = QLabel("Uses Qt/PySide6 under LGPLv3, Pillow, NumPy, PDFium, pypdf, "
                           "and archive libraries including LGPL components. Packaged RAR import uses UnRAR.")
        libraries.setWordWrap(True)
        layout.addWidget(libraries)
        rights = QLabel("Full third-party license texts and notices are supplied with the application. "
                        "LGPL terms allow replacement of those libraries and reverse engineering for debugging modifications to them.")
        rights.setWordWrap(True)
        layout.addWidget(rights)
        root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
        folder = root / "licenses"
        if not folder.is_dir() and not getattr(sys, "frozen", False):
            folder = root / "assets" / "licenses"
        index = folder / "index.html"
        notices = QPushButton("View third-party notices")
        notices.setObjectName("view_license_notices")
        notices.setEnabled(index.is_file())
        notices.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(index))))
        layout.addWidget(notices)
        files = QPushButton("Open license folder")
        files.setObjectName("open_license_folder")
        files.setEnabled(folder.is_dir())
        files.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))))
        layout.addWidget(files)
        okay = QPushButton("Close")
        okay.clicked.connect(dialog.accept)
        layout.addWidget(okay)
        dialog.exec()

    def show_settings(self):
        dialog = OutlinedDialog(self)
        dialog.setWindowTitle("Settings")
        dialog.resize(540, 510)
        layout = QVBoxLayout(dialog)
        layout.addWidget(ui_heading("Processing resources"))
        hint = QLabel("More workers can finish sooner, but use more memory and leave less capacity for other programs.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        counts = {}
        for key, label, maximum, automatic in (("pdf_workers", "PDF rendering processes", 8, pdf_workers()),
                ("screenshot_workers", "Screenshot workers", min(16, os.cpu_count() or 1), screenshot_workers()),
                ("encoder_workers", "Encoding workers", min(16, os.cpu_count() or 1), encoding_workers())):
            control = QSpinBox(dialog)
            control.setRange(0, maximum)
            control.setSpecialValueText(f"Automatic ({automatic})")
            control.setValue(self.settings.value(key, 0, type=int))
            form.addRow(label, control)
            form_hint(form, control, "Automatic limits parallel work to leave room for other tasks. PDF rendering uses up to four processes; screenshot measurement/cropping and encoding use about half the logical processors, up to six. Set a lower count for a more responsive computer or a higher count for faster processing when memory allows.")
            counts[key] = control
        layout.addLayout(form)
        notice = QCheckBox("Show the original-file notice at startup")
        notice.setChecked(not self.settings.value("hide_source_notice", False, type=bool))
        layout.addWidget(notice)
        key_group = QGroupBox("Optional ComicVine lookup")
        key_form = QFormLayout(key_group)
        api_key = QLineEdit(self.comicvine_session_key)
        api_key.setObjectName("comicvine_api_key")
        api_key.setEchoMode(QLineEdit.Password)
        api_key.setPlaceholderText("Your ComicVine API key")
        api_key.setToolTip(tooltip_text("Used only when you search ComicVine from the metadata editor. The key is not included in comics, queued jobs, review reports or diagnostic logs."))
        key_form.addRow("API key", api_key)
        rememberable = can_remember_key()
        if os.name == "nt":
            remember_key = QCheckBox("Remember on this Windows account")
            remember_help = "Windows protects the remembered key for your user account."
        else:
            remember_key = QCheckBox("Remember in this computer's keyring")
            remember_help = ("Your desktop keyring (such as GNOME Keyring or KDE Wallet) protects the remembered key."
                             if rememberable else
                             "No desktop keyring was found, so the key can only be kept until Comic Stitcher closes.")
        remember_key.setObjectName("remember_comicvine_key")
        remember_key.setChecked(rememberable and bool(self.settings.value("comicvine_key_protected", "")))
        remember_key.setEnabled(rememberable)
        remember_key.setToolTip(tooltip_text(remember_help + " Unchecked: retain it only until Comic Stitcher closes. Clear the API key and click OK to remove it."))
        key_form.addRow("", remember_key)
        key_link = QLabel('<a href="https://comicvine.gamespot.com/api/">Get an API key / ComicVine API terms</a>')
        key_link.setOpenExternalLinks(True); key_form.addRow("", key_link)
        layout.addWidget(key_group)
        for label, action in (("Open diagnostic logs", self.open_diagnostic_logs), ("Open pair review reports", self.open_review_reports)):
            button = QPushButton(label)
            button.clicked.connect(action)
            layout.addWidget(button)
        about = QPushButton("About and credits…")
        about.clicked.connect(self.show_about)
        layout.addWidget(about)
        row = QHBoxLayout()
        cancel = QPushButton("Cancel"); cancel.clicked.connect(dialog.reject)
        okay = QPushButton("OK"); okay.clicked.connect(dialog.accept); ui_primary(okay)
        row.addStretch(); row.addWidget(cancel); row.addWidget(okay); layout.addLayout(row)
        if dialog.exec() == QDialog.Accepted:
            entered_key = api_key.text().strip()
            if any(character.isspace() for character in entered_key):
                QMessageBox.warning(self, "Check API key", "The API key must not contain spaces or line breaks.")
                return
            protected = ""
            try:
                if entered_key and remember_key.isChecked():
                    protected = protect_key(entered_key)
            except ValueError as exc:
                QMessageBox.warning(self, "API key could not be saved", str(exc))
                return
            if protected:
                self.settings.setValue("comicvine_key_protected", protected)
            else:
                # A keyring keeps the key outside the settings file, so remove it there too.
                forget_key(self.settings.value("comicvine_key_protected", ""))
                self.settings.remove("comicvine_key_protected")
            if entered_key != self.comicvine_session_key:
                self.comicvine_client.set_key(entered_key)
            self.comicvine_session_key = entered_key
            for key, control in counts.items():
                self.settings.setValue(key, control.value())
            self.settings.setValue("hide_source_notice", not notice.isChecked())
            self.encoder_workers.setValue(counts["encoder_workers"].value())

    def _compact_target_size(self):
        progress = hasattr(self, "progress_page") and self.pages.currentWidget() is self.progress_page
        finished = progress and not self.done_button.isHidden()
        size = QSize(580, 240) if progress and not finished else self.compact_size
        if hasattr(self, "export_page") and self.pages.currentWidget() is self.export_page:
            size = QSize(800, 560)
            # Wrapped explanations need more lines with larger fonts (for example
            # Ubuntu's default), so grow to fit the content rather than overlap it.
            page = self.export_page
            needed = page.minimumSizeHint().height()
            if page.hasHeightForWidth():
                needed = max(needed, page.heightForWidth(size.width()))
            if needed > size.height():
                # Only the height grows, and never beyond the screen's usable height.
                screen = self.screen() or QApplication.primaryScreen()
                if screen:
                    frame = self.frameGeometry().height() - self.geometry().height()
                    needed = min(needed, screen.availableGeometry().height() - frame)
                size.setHeight(max(size.height(), needed))
        return size

    def _apply_compact_size(self, size, page):
        if self.pages.currentWidget() is not page or self.isMaximized() or self.isFullScreen():
            return  # The screen has changed since, or the window was maximized on purpose.
        self.resize(size)
        screen = self.screen() or QApplication.primaryScreen()
        if screen:
            bounds = screen.availableGeometry()
            self.move(bounds.center() - self.rect().center())

    def _compact_window(self):
        was_maximized = self.isMaximized() or self.isFullScreen()
        if was_maximized or self.isMinimized():
            # Only ask when needed: on Wayland this is a request to the desktop, whose
            # reply restates the current size and would undo the resize below.
            self.showNormal()
        size = self._compact_target_size()
        page = self.pages.currentWidget()
        self.resize(size)
        screen = self.screen() or QApplication.primaryScreen()
        if screen:
            bounds = screen.availableGeometry()
            self.move(bounds.center() - self.rect().center())
        if sys.platform != "win32":
            # Linux window managers (Wayland in particular) finish leaving the maximized
            # state after this returns and then restore the old size, undoing the resize
            # above. Apply it again once they have caught up. Windows applies it at once.
            self._pending_compact = (size, page)
            QTimer.singleShot(0, lambda: self._apply_compact_size(size, page))
            QTimer.singleShot(250, lambda: self._apply_compact_size(size, page))
            if was_maximized:
                QTimer.singleShot(600, lambda: self._apply_compact_size(size, page))

    def changeEvent(self, event):
        super().changeEvent(event)
        pending = getattr(self, "_pending_compact", None)
        if (pending and event.type() == QEvent.Type.WindowStateChange
                and not (self.isMaximized() or self.isFullScreen())):
            self._pending_compact = None
            QTimer.singleShot(0, lambda: self._apply_compact_size(*pending))

    def _expand_window(self):
        self.showMaximized()

    def _install_shortcuts(self):
        self.shortcuts = {}
        for key, action in (("Y", lambda: self._review_key("yes")),
                            ("Up", lambda: self._review_key("up")),
                            ("N", lambda: self._review_key("no")),
                            ("Down", lambda: self._review_key("down")),
                            ("Space", lambda: self._review_key("seam")),
                            ("Backspace", lambda: self._review_key("back")),
                            ("Left", lambda: self._review_key("left")),
                            ("Right", lambda: self._review_key("right")),
                            ("Escape", lambda: self._review_key("escape")),
                            ("B", lambda: self._review_key("boss"))):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.activated.connect(action)
            self.shortcuts[key] = shortcut

    def _update_shortcuts(self, *_):
        current = self.pages.currentWidget()
        for key, shortcut in self.shortcuts.items():
            shortcut.setEnabled(current == self.review or
                                (current == self.preview and (key in ("Left", "Right", "Space", "Escape") or
                                 (self.preview_seam_active and key in ("Up", "Down")))) or
                                (current == self.boss_page and key == "B"))
        if current == self.review:
            # The scan/progress page may still own keyboard focus while Qt is
            # switching the stacked page and maximizing the window.
            QTimer.singleShot(0, self._focus_review)

    def _focus_review(self):
        if self.pages.currentWidget() == self.review:
            self.review.setFocus(Qt.OtherFocusReason)

    def _review_key(self, action):
        screen = self.pages.currentWidget()
        if action == "boss":
            if screen == self.review:
                self.pages.setCurrentWidget(self.boss_page)
            elif screen == self.boss_page:
                self.pages.setCurrentWidget(self.review)
            return
        if screen == self.preview:
            if action == "seam":
                self.preview_seam()
                return
            if action == "escape" and self.preview_seam_active:
                self._close_preview_seam()
                return
            if self.preview_seam_active and action in ("up", "down"):
                scroll = self.preview_seam_area.verticalScrollBar()
                scroll.setValue(scroll.value() + (-150 if action == "up" else 150))
                return
        if screen == self.preview and action in ("left", "right"):
            self.navigate((1 if action == "left" else -1) if self.plan.scan.right_to_left
                          else (-1 if action == "left" else 1))
            return
        if screen != self.review:
            return
        if action in ("seam", "escape"):
            if action == "seam" or self.seam:
                self.toggle_seam()
        elif self.seam and action in ("up", "down"):
            scroll = self.seam_area.verticalScrollBar()
            scroll.setValue(scroll.value() + (-150 if action == "up" else 150))
        elif not self.seam and action in ("yes", "up"):
            self.decide(True)
        elif not self.seam and action in ("no", "down"):
            self.decide(False)
        elif not self.seam and action in ("back", "left"):
            self.review_back()

    def _build_start(self):
        start = QWidget()
        box = QVBoxLayout(start)
        box.setContentsMargins(20, 16, 20, 16)
        box.setSpacing(16)
        title = QLabel("Comic Stitcher")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 34px; font-weight: 700; color: #203c55;")
        title_row = QHBoxLayout()
        title_row.addSpacing(44)
        title_row.addWidget(title, 1)
        gear = QPushButton("⚙")
        gear.setFixedSize(44, 44)
        gear.setIcon(interface_icon("settings"))
        if not gear.icon().isNull():
            gear.setText("")
            gear.setIconSize(QSize(32, 32))
        gear.setStyleSheet("QPushButton { font-size:28px; } QToolTip { font-size:12px; }")
        gear.setToolTip("Settings")
        gear.setAccessibleName("Settings")
        gear.clicked.connect(self.show_settings)
        title_row.addWidget(gear)
        box.addLayout(title_row)
        # Retain these state controls for the later workflow, but choose them
        # in the confirmation dialog instead of crowding the opening screen.
        self.review_mode = QComboBox(start)
        self.review_mode.addItems(["Suggested pairs", "Manual — every neighboring pair"])
        self.review_mode.hide()
        self.screenshot_mode = QCheckBox(start)
        self.screenshot_mode.hide()
        self.import_zone = ImportDropZone(
            "Drop comic files or folders here",
            "PDFs, comic archives, page images or screenshots",
            "Choose files…", start, folder_browse="Choose folder…")
        self.import_zone.chosen.connect(self.choose_file)
        self.import_zone.folder_chosen.connect(self.choose_folder)
        self.import_zone.dropped.connect(self._dropped_import)
        box.addWidget(self.import_zone, 1)
        queue_row = QHBoxLayout()
        self.queue_status = QLabel("Queue empty")
        queue_row.addWidget(self.queue_status)
        queue_row.addStretch()
        self.queue_view_button = QPushButton("View queue…")
        self.queue_view_button.clicked.connect(self.show_queue)
        self.queue_view_button.setEnabled(False)
        queue_row.addWidget(self.queue_view_button)
        self.next_import_button = QPushButton("Review next comic")
        self.next_import_button.clicked.connect(self._open_next_pending)
        self.next_import_button.hide()
        queue_row.addWidget(self.next_import_button)
        self.queue_start_button = QPushButton("Start queue")
        self.queue_start_button.clicked.connect(self.start_queue)
        self.queue_start_button.setEnabled(False)
        queue_row.addWidget(self.queue_start_button)
        box.addLayout(queue_row)
        self.pages.addWidget(start)

    def _build_review(self):
        self.review = QWidget()
        self.review.setFocusPolicy(Qt.StrongFocus)
        box = QVBoxLayout(self.review)
        self.review_heading = QLabel()
        self.review_heading.setAlignment(Qt.AlignCenter)
        self.review_heading.setStyleSheet("font-size:19px; font-weight:bold;")
        box.addWidget(self.review_heading)
        self.review_view = QStackedWidget()
        self.joined_review = JoinedPairView()
        self.review_view.addWidget(self.joined_review)
        self.seam_area = QScrollArea()
        self.seam_area.setWidgetResizable(False)
        self.seam_area.setAlignment(Qt.AlignHCenter)
        self.seam_image = QLabel()
        self.seam_image.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.seam_area.setWidget(self.seam_image)
        self.review_view.addWidget(self.seam_area)
        box.addWidget(self.review_view, 1)
        controls = QHBoxLayout()
        self.back_button = QPushButton("← Back")
        self.back_button.clicked.connect(self.review_back)
        self.no_button = QPushButton("✕ No (N / ↓)")
        self.no_button.clicked.connect(lambda: self.decide(False))
        self.seam_button = QPushButton("▥\nSeam")
        self.seam_button.clicked.connect(self.toggle_seam)
        rotate_left = QPushButton("↶ Rotate left")
        rotate_left.clicked.connect(lambda: self.rotate_candidate(-90))
        rotate_right = QPushButton("↷ Rotate right")
        rotate_right.clicked.connect(lambda: self.rotate_candidate(90))
        self.yes_button = QPushButton("✓ Yes (Y / ↑)")
        self.yes_button.clicked.connect(lambda: self.decide(True))
        for button in (self.back_button, self.no_button, self.seam_button,
                       rotate_left, rotate_right, self.yes_button):
            controls.addWidget(button)
        controls.insertStretch(0)
        controls.addStretch()
        for button, symbol, label, tip in ((self.back_button, "←", "Back", "Revisit previous decision (Left / Backspace)"),
                (self.no_button, "✕", "No", "Reject pair (N / Down)"),
                (self.seam_button, "▥", "Seam", "Inspect seam (Space)"),
                (rotate_left, "↶", "Rotate left", "Rotate counterclockwise"),
                (rotate_right, "↷", "Rotate right", "Rotate clockwise"),
                (self.yes_button, "✓", "Yes", "Approve pair (Y / Up)")):
            review_control(button, symbol, label, tip)
        self.yes_button.setStyleSheet(self.yes_button.styleSheet() + "QPushButton:hover { background:#d6ecdf; }")
        self.no_button.setStyleSheet(self.no_button.styleSheet() + "QPushButton:hover { background:#f7dddd; }")
        box.insertLayout(1, controls)
        remove_row = QHBoxLayout()
        remove_row.addStretch()
        self.remove_review_button = QPushButton("Remove a page…")
        self.remove_review_button.setToolTip("Exclude one displayed page from the finished comic.")
        self.remove_review_button.clicked.connect(self.remove_review_page)
        remove_row.addWidget(self.remove_review_button)
        self.remove_review_button.hide()
        self.pages.addWidget(self.review)

    def _build_possible_choice(self):
        self.possible_choice = QWidget()
        box = QVBoxLayout(self.possible_choice)
        box.addStretch()
        heading = QLabel("Likely pairs reviewed")
        heading.setAlignment(Qt.AlignCenter)
        heading.setStyleSheet("font-size:26px; font-weight:bold;")
        box.addWidget(heading)
        self.possible_message = QLabel()
        self.possible_message.setAlignment(Qt.AlignCenter)
        self.possible_message.setWordWrap(True)
        box.addWidget(self.possible_message)
        row = QHBoxLayout()
        row.addStretch()
        back = QPushButton("← Revisit last pair")
        back.clicked.connect(self._possible_choice_back)
        row.addWidget(back)
        review = QPushButton("Review Possible")
        review.clicked.connect(self._review_possible)
        row.addWidget(review)
        self.review_possible_button = review
        onward = QPushButton("Continue to Final Preview")
        onward.clicked.connect(self._defer_possible)
        row.addWidget(onward)
        row.addStretch()
        box.addLayout(row)
        box.addStretch()
        self.pages.addWidget(self.possible_choice)

    def _possible_choice_back(self):
        if self.plan.back():
            self._save_review_report()
            self.pages.setCurrentWidget(self.review)
            self.show_candidate()

    def _review_possible(self):
        self.possible_review_started = True
        self.plan.deferred_possible = False
        self._save_review_report()
        self.pages.setCurrentWidget(self.review)
        self.show_candidate()

    def _defer_possible(self):
        self.plan.deferred_possible = True
        self._save_review_report()
        diagnostics.note("Possible pairs deferred: %d", self.plan.remaining_possible())
        self.enter_preview()

    def _build_preview(self):
        self.preview = QWidget()
        box = QVBoxLayout(self.preview)
        self.preview_heading = QLabel()
        box.addWidget(self.preview_heading)
        self.thumbnail_status = QLabel()
        self.thumbnail_status.setStyleSheet("color:#52606d;")
        self.thumbnail_status.hide()
        center = QHBoxLayout()
        self.main_image, self.compare_image = FitImage(), FitImage()
        center.addWidget(self.main_image, 1)
        center.addWidget(self.compare_image, 1)
        self.compare_image.hide()
        self.preview_pair = JoinedPairView()
        center.addWidget(self.preview_pair, 1)
        self.preview_pair.hide()
        self.preview_view = QStackedWidget()
        page_view = QWidget()
        page_view.setLayout(center)
        self.preview_view.addWidget(page_view)
        self.preview_seam_area = QScrollArea()
        self.preview_seam_area.setWidgetResizable(False)
        self.preview_seam_area.setAlignment(Qt.AlignHCenter)
        self.preview_seam_image = QLabel()
        self.preview_seam_image.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.preview_seam_area.setWidget(self.preview_seam_image)
        self.preview_view.addWidget(self.preview_seam_area)
        self.preview_seam_active = False
        box.addWidget(self.preview_view, 1)
        self.thumb_area = QScrollArea()
        self.thumb_area.setWidgetResizable(False)
        self.thumb_area.setFixedHeight(154)
        self.thumb_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.thumb_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.thumb_content = ThumbnailStrip(self)
        self.page_drop_marker = DropMarker(self.thumb_content)
        self.page_drop_boundary = None
        self.thumb_row = QHBoxLayout(self.thumb_content)
        self.thumb_row.setContentsMargins(4, 4, 4, 4)
        self.thumb_area.setWidget(self.thumb_content)
        self.thumb_area.horizontalScrollBar().valueChanged.connect(self._queue_visible_thumbnails)
        self.thumb_area.viewport().installEventFilter(self)
        box.addWidget(self.thumb_area)
        controls = QHBoxLayout()
        for caption, callback in (("← Previous", lambda: self.navigate(-1)),
                                  ("Next →", lambda: self.navigate(1)),
                                  ("Join as Spread", self.join_selected),
                                  ("Split Back Into Pages", self.split_selected),
                                  ("Back to Pair Review", self.return_review),
                                  ("Continue to Export", self.show_export)):
            button = QPushButton(caption)
            button.clicked.connect(callback)
            controls.addWidget(button)
            if caption.startswith("Join"):
                self.join_button = button
            elif caption.startswith("Split"):
                self.split_button = button
            elif caption.startswith("← Previous"):
                self.previous_preview_button = button
            elif caption.startswith("Next"):
                self.next_preview_button = button
            elif caption == "Back to Pair Review":
                self.return_review_button = button
        box.addLayout(controls)
        seam = QPushButton("Inspect selected seam")
        seam.clicked.connect(self.preview_seam)
        controls.insertWidget(3, seam)
        self.preview_seam_button = seam
        self.rotate_preview_left = QPushButton("↶ Rotate page")
        self.rotate_preview_left.clicked.connect(lambda: self.rotate_preview_page(-90))
        controls.insertWidget(5, self.rotate_preview_left)
        self.rotate_preview_right = QPushButton("↷ Rotate page")
        self.rotate_preview_right.clicked.connect(lambda: self.rotate_preview_page(90))
        controls.insertWidget(6, self.rotate_preview_right)
        remove_row = QHBoxLayout()
        self.remove_preview_button = QPushButton("Remove current page…")
        self.remove_preview_button.clicked.connect(self.remove_preview_page)
        self.undo_remove_button = QPushButton("Undo last edit")
        self.undo_remove_button.clicked.connect(self.undo_preview_removal)
        remove_row.addWidget(self.remove_preview_button)
        remove_row.addWidget(self.undo_remove_button)
        remove_row.addStretch()
        controls.insertStretch(0)
        controls.addStretch()
        specs = (("←", "Previous"), ("→", "Next"), ("⋈", "Join"), ("▥", "Seam"), ("✂", "Split"),
                 ("↶", "Rotate left"), ("↷", "Rotate right"), ("↩", "Pair review"), ("✓", "Export"))
        buttons = [controls.itemAt(i).widget() for i in range(controls.count()) if controls.itemAt(i).widget()]
        for button, (symbol, label) in zip(buttons, specs, strict=True):
            review_control(button, symbol, label)
        self.swap_button = QPushButton("Swap selected pages")
        self.swap_button.clicked.connect(self.swap_selected)
        review_control(self.swap_button, "⇄", "Swap", "Swap the two selected neighboring pages")
        controls.insertWidget(3, self.swap_button)
        box.removeItem(controls)
        box.insertLayout(1, controls)
        review_control(self.remove_preview_button, "⌫", "Remove", "Remove current page after confirmation")
        review_control(self.undo_remove_button, "↶", "Undo", "Undo the last page move, swap, rotation, join, split, or removal")
        remove_row.removeWidget(self.remove_preview_button)
        remove_row.removeWidget(self.undo_remove_button)
        controls.insertWidget(controls.count() - 2, self.remove_preview_button)
        controls.insertWidget(controls.count() - 2, self.undo_remove_button)
        self.pages.addWidget(self.preview)

    def _build_export(self):
        self.export_page = QWidget()
        self.export_page.setStyleSheet(
            "QGroupBox { font-weight:600; font-size:15px; border:1px solid #cdd6de; "
            "border-radius:8px; margin-top:26px; background:#f9fbfd; } "
            "QGroupBox::title { subcontrol-origin:margin; subcontrol-position:top left; top:0px; left:12px; padding:0 4px; } "
            "QWidget#exportBookContent, QWidget#exportBookViewport { background:#f9fbfd; } "
            "QGroupBox QLabel, QGroupBox QCheckBox { font-weight:400; color:#000000; } "
            "QLineEdit, QComboBox, QSpinBox { min-height:24px; } "
            "QPushButton { padding:5px 10px; }")
        box = QVBoxLayout(self.export_page)
        box.setContentsMargins(20, 16, 20, 16)
        box.setSpacing(12)
        box.addWidget(ui_heading("Create your comic", 28))
        form = QFormLayout()
        self.book_name = QLineEdit()
        self.book_name.textChanged.connect(self.refresh_export)
        form.addRow("Book name", self.book_name)
        form_hint(form, self.book_name,
                  "Sets the finished archive's name (plus .cbz or .cb7) and the processed-image folder name. "
                  "If you choose Book name + number below, it also prefixes each numbered image filename. "
                  "When creating both sizes, the output names gain Full and Reduced suffixes.")
        self.naming = QComboBox()
        self.naming.addItems(["Number only", "Book name - number", "Custom prefix - number"])
        self.naming.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.naming.setMinimumContentsLength(24)
        self.naming.setCurrentIndex(int(self.settings.value("naming", 1)))
        self.naming.currentIndexChanged.connect(self.refresh_export)
        form.addRow("Filename style", self.naming)
        form_hint(form, self.naming,
                  "Choose numbered filenames such as 001.jpg, or include the book name, "
                  "such as Book Title - 001.jpg. The examples update to reflect your name, numbering, "
                  "and selected image format. Custom prefix lets page names differ from the archive name.")
        self.custom_page_prefix = QLineEdit(str(self.settings.value("custom_page_prefix", "")))
        self.custom_page_prefix.setPlaceholderText("Superhero v01 - 300dpi - 2025 - digital")
        self.custom_page_prefix.textChanged.connect(self.refresh_export)
        self.custom_prefix_row = QWidget()
        custom_layout = QVBoxLayout(self.custom_prefix_row)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        custom_layout.addWidget(QLabel("Custom page prefix"))
        custom_layout.addWidget(self.custom_page_prefix)
        self.custom_prefix_row.setVisible(self.naming.currentIndex() == 2)
        form.addRow(QLabel(""), self.custom_prefix_row)
        form_hint(form, self.custom_page_prefix,
                  "Enter the text before the page number. Comic Stitcher adds ' - ', the padded "
                  "number, and the appropriate image extension. This does not change the archive name. "
                  "Characters that cannot be used in filenames are replaced with underscores.")
        self.start_number = QComboBox()
        self.start_number.addItems(["Start at 1", "Start at 0 (cover)"])
        self.start_number.setCurrentIndex(1 if self.settings.value("start_number", 1, type=int) == 0 else 0)
        self.start_number.currentIndexChanged.connect(self.refresh_export)
        form.addRow("Numbering", self.start_number)
        form_hint(form, self.start_number,
                  "Choose whether the first final image is numbered 0 or 1. If the filename style "
                  "includes the title, the first file might be Book Title - 000.jpg or "
                  "Book Title - 001.jpg, depending on the chosen number width and output format. "
                  "Each stitched spread counts as one image.")
        self.padding = QComboBox()
        self.padding.addItems(["Automatic", "2 digits", "3 digits", "4 digits"])
        self.padding.setCurrentIndex({0: 0, 2: 1, 3: 2, 4: 3}.get(
            self.settings.value("number_padding", 0, type=int), 0))
        self.padding.currentIndexChanged.connect(self.refresh_export)
        form.addRow("Number padding", self.padding)
        form_hint(form, self.padding,
                  "Controls how many digits appear in each page number, adding leading zeroes. "
                  "For example, 3 digits produces 001 and 002 (or 000 for a cover). "
                  "Automatic grows to fit the number of final images.")
        self.handling = QComboBox()
        self.handling.addItems(["Re-encode all final pages", "Copy unmodified pages without re-encoding"])
        self.handling.currentIndexChanged.connect(self.refresh_export)
        form.addRow("Image handling", self.handling)
        form_hint(form, self.handling,
                  "Re-encode all final pages: both ordinary pages and joined spreads are saved in "
                  "the selected output format. Copy unmodified pages: pages unchanged during review are copied "
                  "byte-for-byte, except rotated single pages and joined spreads, which must be encoded. "
                  "A reduced copy must encode its pages in the chosen reduced-copy format. "
                  "Screenshot crops and splits have already been prepared as PNG pages before export.")
        self.archive_format = QComboBox()
        self.archive_format.addItems(["CBZ (ZIP)", "CB7 (7-Zip)", "No archive"])
        saved_archive = str(self.settings.value("archive_format", "cbz"))
        self.archive_format.setCurrentIndex({"cbz": 0, "cb7": 1, "none": 2}.get(saved_archive, 0))
        self.archive_format.currentIndexChanged.connect(self._archive_changed)
        form.addRow("Archive format", self.archive_format)
        form_hint(form, self.archive_format,
                  "CBZ packages images as a ZIP archive; CB7 uses a 7z container. "
                  "Both store the already-encoded images in reading order without changing their quality. "
                  "Choose No archive if you want only a folder of processed images.")
        for index, description in enumerate(("Most widely supported comic archive; images stored inside ZIP.",
                                              "7z comic archive; verify that your comic reader supports CB7.",
                                              "Do not create an archive; keep processed images instead.")):
            self.archive_format.setItemData(index, description, Qt.ToolTipRole)
        self.keep_images = QCheckBox("Keep lossless PNG pages")
        self.keep_images.setChecked(self.settings.value("keep_images", False, type=bool))
        self.keep_images.stateChanged.connect(self.refresh_export)
        form.addRow("", self.keep_images)
        self.front_to_back = QCheckBox("Save comic in front-to-back page order")
        self.front_to_back.setChecked(self.settings.value("front_to_back", False, type=bool))
        self.front_to_back.setToolTip(tooltip_text(
            "Checked: the finished CBZ/CB7 and saved PNG pages start with the front cover. "
            "Unchecked: keep back-to-front order, with the back cover first. "
            "Joined spreads stay intact and keep their approved orientation. The original PDF is not changed."))
        self.front_to_back.hide()
        self.front_to_back.toggled.connect(self.refresh_export)
        form.addRow("", self.front_to_back)
        self._archive_changed()
        form_hint(form, self.keep_images,
                  "Keep lossless PNG copies of the final pages after joining, rotation, and any resizing, "
                  "before JPEG/WebP encoding. Both sizes saves separate Full and Reduced PNG folders. "
                  "PNG cannot recover detail already lost in a JPEG source. No archive saves only these PNG pages. "
                  "")
        self.include_comicinfo = QCheckBox("Include ComicInfo.xml")
        self.metadata_button = QPushButton("Edit metadata…")
        self.metadata_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.metadata_button.setEnabled(False)
        self.include_comicinfo.setToolTip(tooltip_text("Add optional comic metadata to each archive and saved PNG folder. Existing ComicInfo.xml information is retained unless you change or exclude it. A page list is included that marks the front cover and two-page spreads for library apps; existing page details are kept and updated to match the final comic."))
        self.include_comicinfo.toggled.connect(self._metadata_toggled)
        self.metadata_button.clicked.connect(self.show_metadata)
        metadata_row = QHBoxLayout()
        metadata_row.addWidget(self.include_comicinfo)
        metadata_row.addWidget(self.metadata_button)
        metadata_row.addStretch()
        form.addRow("", metadata_row)
        self.encoder_workers = QSpinBox()
        self.encoder_workers.setRange(0, min(16, os.cpu_count() or 1))
        self.encoder_workers.setSpecialValueText(f"Automatic ({encoding_workers()} workers)")
        self.encoder_workers.hide()
        form_hint(form, self.encoder_workers,
                  "Number of pages encoded at the same time. Automatic uses about half your "
                  "logical processors, up to six, leaving capacity for other tasks. Using more "
                  "workers can shorten an export but increases memory use and can slow other "
                  "programs you are using; fewer workers keep the computer more responsive.")
        self.encoder_workers.setValue(min(self.encoder_workers.maximum(),
                                          int(self.settings.value("encoder_workers", 0))))
        self.encoder_workers.valueChanged.connect(self.refresh_export)
        self.encoder_workers.valueChanged.connect(
            lambda value: self.settings.setValue("encoder_workers", value))
        self.output_options_button = QPushButton("Output options…")
        self.output_options_button.clicked.connect(self.show_output_options)
        form.addRow("", self.output_options_button)
        form_hint(form, self.output_options_button,
                  "Choose output image format, JPEG or WebP quality, format for rotated/stitched pages, advanced encoder options, "
                  "and full-size or reduced-size output. Changes take effect after you click OK.")
        book_group = QGroupBox("Book")
        book_box = QVBoxLayout(book_group)
        self.book_scroll = QScrollArea()
        self.book_scroll.setWidgetResizable(True)
        self.book_scroll.setFrameShape(QFrame.NoFrame)
        self.book_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.book_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.book_scroll.viewport().setObjectName("exportBookViewport")
        self.book_scroll.viewport().setAttribute(Qt.WA_StyledBackground, True)
        book_content = QWidget()
        book_content.setObjectName("exportBookContent")
        book_content.setAttribute(Qt.WA_StyledBackground, True)
        book_form = QFormLayout(book_content)
        self.book_form = book_form
        # Keep controls at their natural height; surplus rows scroll within Book.
        book_form.setSizeConstraint(QFormLayout.SetMinimumSize)
        book_form.setContentsMargins(0, 0, 4, 0)
        self.book_scroll.setWidget(book_content)
        book_box.addWidget(self.book_scroll)
        book_form.setVerticalSpacing(12)
        for _ in range(3):
            row = form.takeRow(0)
            book_form.addRow(row.labelItem.widget(), row.fieldItem.widget())
        number = form.takeRow(0)
        padding = form.takeRow(0)
        numbers = QHBoxLayout()
        numbers.addWidget(number.fieldItem.widget())
        numbers.addSpacing(20)
        numbers.addWidget(padding.labelItem.widget())
        numbers.addWidget(padding.fieldItem.widget())
        book_form.addRow(number.labelItem.widget(), numbers)
        box.addWidget(book_group)
        output_group = QGroupBox("Output")
        output_box = QVBoxLayout(output_group)
        output_box.setSizeConstraint(QVBoxLayout.SetMinimumSize)
        summary_row = QHBoxLayout()
        self.output_summary = QLabel()
        self.output_summary.setWordWrap(True)
        self.output_summary.setStyleSheet("color:#203c55; padding:6px; font-size:13px;")
        summary_row.addWidget(self.output_summary, 1)
        # Move the existing options control out of its form row.
        options_row = form.takeRow(self.output_options_button)
        if options_row.labelItem:
            options_row.labelItem.widget().deleteLater()
        self.output_options_button.setText("Edit options…")
        summary_row.addWidget(self.output_options_button)
        output_box.addLayout(summary_row)
        estimate_row = QHBoxLayout()
        self.estimate_size = QCheckBox("Estimate size")
        self.estimate_size.setChecked(self.settings.value("estimate_size", True, type=bool))
        self.estimate_size.setToolTip(tooltip_text(
            "Encodes a band from pages spread through the book with the chosen settings, using the "
            "same number of processes as export, to estimate how large the finished comic will be. "
            "Pages copied without re-encoding are counted exactly. Updates shortly after a setting "
            "changes; an early estimate is refined as the remaining pages finish."))
        self.estimate_size.toggled.connect(self._estimate_toggled)
        estimate_row.addWidget(self.estimate_size)
        self.size_estimate = QLabel("")
        self.size_estimate.setStyleSheet("color:#203c55; padding-left:6px;")
        estimate_row.addWidget(self.size_estimate, 1)
        output_box.addLayout(estimate_row)
        self.estimate_generation = 0
        self.estimate_png_pages = False
        self.estimate_timer = QTimer(self)
        self.estimate_timer.setSingleShot(True)
        self.estimate_timer.setInterval(700)
        self.estimate_timer.timeout.connect(self._start_size_estimate)
        self.size_worker = SizeEstimateWorker()
        self.size_worker.ready.connect(self._size_estimate_ready)
        self.size_worker.provisional.connect(
            lambda generation, sizes: self._size_estimate_ready(generation, sizes, final=False))
        self.size_worker.failed.connect(self._size_estimate_failed)
        self.pages.currentChanged.connect(self._schedule_size_estimate)
        form.setVerticalSpacing(10)
        output_box.addLayout(form)
        box.addWidget(output_group)
        self.export_hint = QLabel("Choose your settings, then review the output before creating the comic.")
        self.export_hint.setWordWrap(True)
        box.addWidget(self.export_hint)
        self.keep_queue_pages = QCheckBox("Keep imported pages for faster queued export")
        self.keep_queue_pages.setToolTip(tooltip_text(
            "Unchecked: clear temporary PDF/archive pages after adding to the queue and import them again "
            "when exporting. This saves disk space. Checked: retain imported pages until export; faster, "
            "but a large queue can use substantial disk space."))
        self.keep_queue_pages.hide()
        box.addStretch()
        controls = QHBoxLayout()
        back = QPushButton("Back to Comic Preview")
        back.clicked.connect(self.return_preview_from_export)
        self.create_button = QPushButton("Review final output")
        ui_primary(self.create_button)
        self.create_button.clicked.connect(self.start_export)
        self.add_queue_button = QPushButton("Add to queue")
        self.add_queue_button.clicked.connect(self.add_to_queue)
        controls.addWidget(back)
        controls.addStretch()
        controls.addWidget(self.add_queue_button)
        controls.addWidget(self.create_button)
        box.addLayout(controls)
        self.pages.addWidget(self.export_page)
        self.refresh_export()

    def _build_progress(self):
        self.progress_page = QWidget()
        box = QVBoxLayout(self.progress_page)
        box.addStretch()
        self.progress_source = QLabel()
        self.progress_source.setAlignment(Qt.AlignCenter)
        self.progress_source.setWordWrap(True)
        self.progress_source.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.progress_source.setStyleSheet("color:#617080; font-size:13px;")
        box.addWidget(self.progress_source)
        self.progress_label = QLabel()
        self.progress_label.setAlignment(Qt.AlignCenter)
        self.progress_label.setWordWrap(True)
        self.progress_label.setStyleSheet("font-size:19px; font-weight:700; color:#203c55;")
        self.progress_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.progress_bar = QProgressBar()
        box.addWidget(self.progress_label)
        box.addWidget(self.progress_bar)
        self.progress_bar.setMinimumHeight(26)
        self.progress_caption = QLabel("Overall progress")
        self.progress_caption.setAlignment(Qt.AlignCenter)
        self.progress_caption.setStyleSheet("color:#617080;")
        box.addWidget(self.progress_caption)
        self.done_button = QPushButton("Process another comic")
        self.done_button.clicked.connect(self.new_comic)
        self.done_button.hide()
        self.repeat_button = QPushButton("Export this comic again")
        self.repeat_button.clicked.connect(self.repeat_export)
        self.repeat_button.hide()
        box.addWidget(self.repeat_button)
        box.addWidget(self.done_button)
        box.addStretch()
        self.pages.addWidget(self.progress_page)

    def new_comic(self):
        if self.screenshot_stage_dir is not None and self.replay_count > 0:
            shutil.rmtree(self.screenshot_stage_dir, ignore_errors=True)
        self.screenshot_stage_dir = None
        self.plan = None
        self.preview_checkpoint_timer.stop()
        self.preview_index = 0
        self.pending_session = None
        self.session_source = None
        self.screenshot_source = None
        self.screenshot_flagged_pages.clear()
        self.inferred_notice_shown = False
        self.screenshot_mode.setChecked(False)
        self.replay_base_name = ""
        self.replay_count = 0
        self.possible_review_started = False
        self.review_report_path = None
        self.active_manual_mode = False
        self.active_right_to_left = False
        self.review_mode.setCurrentIndex(0)
        if self.import_work is not None:
            self.import_work.cleanup()
            self.import_work = None
        self.review_generation += 1
        self.thumb_generation = self.thumb_worker.reset([])
        self.thumb_cache.clear()
        self.repeat_button.hide()
        self.pages.setCurrentIndex(0)
        self._compact_window()

    def _build_boss(self):
        self.boss_page = QWidget()
        box = QVBoxLayout(self.boss_page)
        box.addWidget(QLabel("Quarterly Asset Reconciliation — FY2026"))
        box.addWidget(QLabel("Department                    Q1           Q2           Variance\n"
                             "Regional inventory             1,284        1,285        +0.08%\n"
                             "Undistributed assets             412          412          0.00%"))
        box.addStretch()
        box.addWidget(QLabel("Press B to return"))
        self.pages.addWidget(self.boss_page)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            # Windows Explorer waits for dropEvent to return before ending its drag.
            QTimer.singleShot(0, lambda: self._dropped_import(paths))

    def _dropped_import(self, paths):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self._import_paths(paths)

    def _external_import(self, paths):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if not paths:
            return
        if self.import_prompt_active or self.plan is not None or (self.worker and self.worker.isRunning()):
            self.pending_imports.extend(expand_sources(paths))
            self.batch_import_active = True
            self._refresh_queue_status()
            self.refresh_export()
            return
        self._import_paths(paths)

    def _import_paths(self, paths):
        paths = expand_sources(paths)
        if not paths:
            return
        if (len(paths) > 1 and
                all(p.is_dir() or p.is_file() and p.suffix.lower() in ARCHIVE_EXTS | {".pdf"}
                    for p in paths)):
            if self.plan is not None or (self.worker and self.worker.isRunning()):
                QMessageBox.information(self, "Import", "Finish the current comic before adding more imports.")
                return
            self.import_prompt_active = True
            try:
                accepted = self.choose_batch_storage()
            finally:
                self.import_prompt_active = False
            if not accepted:
                return
            self.batch_import_active = True
            self.pending_imports.extend(path.resolve() for path in paths)
            self._refresh_queue_status()
            self._open_next_pending()
            return
        if len(paths) == 1 and paths[0].is_dir():
            source = paths[0]
        elif len(paths) == 1 and paths[0].is_file() and paths[0].suffix.lower() in ARCHIVE_EXTS | {".pdf"}:
            source = paths[0]
        elif all(p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"} for p in paths) and len({p.parent.resolve() for p in paths}) == 1:
            source = paths[0].parent
        else:
            QMessageBox.warning(self, "Import", "Drop one PDF, one supported archive, one folder, or image files from one folder.")
            return
        self._prepare_import(source)

    def choose_batch_storage(self) -> bool:
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Temporary storage for this batch")
        dialog.setText("Choose how Comic Stitcher stores imported pages while you review this batch.")
        dialog.setInformativeText(
            "Clear temporary pages (recommended): uses less disk space. PDFs and archives are imported "
            "again when the queue runs, so total processing takes longer. Only one queued book's "
            "imported pages need storage at a time.\n\n"
            "Keep imported pages: exports start faster, but disk usage accumulates as you review books. "
            "Large batches can require substantial free disk space. Pages are cleared after each export.\n\n"
            "Your review decisions are kept either way. "
            "Ordinary image folders need no extra import storage; screenshot crop staging is retained either way.")
        clear = dialog.addButton("Clear temporary pages", QMessageBox.AcceptRole)
        keep = dialog.addButton("Keep for faster export", QMessageBox.ActionRole)
        dialog.addButton(QMessageBox.Cancel)
        dialog.setDefaultButton(clear)
        dialog.exec()
        if dialog.clickedButton() not in (clear, keep):
            return False
        self.keep_queue_pages.setChecked(dialog.clickedButton() is keep)
        return True

    def _open_next_pending(self):
        if self.plan is not None or (self.worker and self.worker.isRunning()):
            return
        if self.pending_imports:
            source = self.pending_imports.popleft()
            self._refresh_queue_status()
            self._prepare_import(source)

    def _prepare_import(self, source: Path):
        self.import_prompt_active = True
        try:
            return self._prepare_import_dialog(source)
        finally:
            self.import_prompt_active = False

    def _prepare_import_dialog(self, source: Path):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Please wait", "A scan or export is still running.")
            return
        if self.plan is not None:
            QMessageBox.information(self, "Import", "Choose Process another comic before importing a new one.")
            return
        source = source.resolve()
        if source.is_dir() and not collect_images(source):
            comic_files = [p for p in source.iterdir() if p.is_file() and
                           p.suffix.lower() in ARCHIVE_EXTS | {".pdf"}]
            if comic_files:
                message = (f"This folder contains {len(comic_files)} comic file(s), rather than page images. "
                           "Select the comic files inside it and drag them onto Comic Stitcher to review them as a batch.")
            else:
                message = ("No supported page images were found directly inside this folder. "
                           "Choose a folder containing the pages or screenshots for one comic. "
                           "If the pages are in subfolders, select those individual folders and drag them onto Comic Stitcher.")
            QMessageBox.information(self, "Choose comic files or a page folder", message)
            return
        screenshot_hint, sampled = (looks_like_screenshot_folder(source)
                                     if source.is_dir() else (False, 0))
        dialog = ImportChoiceDialog(source, folder=source.is_dir(),
                                    screenshot_hint=screenshot_hint, sampled=sampled,
                                    pdf_smooth_images=(
                                        str(self.settings.value("pdf_smooth_images", "true")).lower()
                                        != "false"), parent=self)
        dialog.review_mode.setCurrentIndex(self.review_mode.currentIndex())
        if dialog.exec() != QDialog.Accepted:
            return
        self.review_mode.setCurrentIndex(dialog.review_mode.currentIndex())
        self.screenshot_mode.setChecked(dialog.screenshots())
        self.active_right_to_left = dialog.right_to_left.isChecked()
        if dialog.pdf_smooth_images is not None:
            self.start_scan(source, pdf_smooth_images=dialog.pdf_smooth_images.isChecked())
        else:
            self.start_scan(source)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder of comic images")
        if folder:
            self._import_paths([Path(folder)])

    def choose_file(self):
        sources, _ = QFileDialog.getOpenFileNames(
            self, "Select comic file", "",
            "Comic files (*.cbz *.cbr *.cb7 *.zip *.rar *.7z *.pdf);;"
            "PDF documents (*.pdf);;Comic archives (*.cbz *.cbr *.cb7 *.zip *.rar *.7z)")
        if sources:
            self._import_paths([Path(source) for source in sources])

    def _run_worker(self, fn, args, success, progress_handler=None, failure_handler=None):
        source = args[0] if args else None
        if isinstance(source, Path):
            name = source.name
        elif isinstance(source, QueuedComic):
            name = source.options.book_name
        elif isinstance(source, BookPlan):
            name = (args[1].book_name if len(args) > 1 and isinstance(args[1], ExportOptions)
                    else (source.scan.source_archive or source.scan.folder).name)
        else:
            name = ""
        self.progress_source.setText(name)
        self.worker = Worker(fn, *args)
        self.worker.progress.connect(lambda n, total: self.progress_bar.setValue(int(100 * n / max(total, 1))))
        if progress_handler is not None:
            self.worker.progress.connect(progress_handler)
        self.worker.result.connect(success)
        self.worker.failure.connect(failure_handler or self._worker_error)
        self.worker.start()

    def _start_screenshot_import(self, folder: Path):
        app_folder = diagnostics.log_path().parent.parent
        self.screenshot_stage_dir = (app_folder / "screenshot-staging" /
                                     session_path(folder, app_folder).stem)
        checkpoint = read_session(folder, app_folder)
        if checkpoint and "forced_pairs" in checkpoint.get("folder_scan", {}):
            QMessageBox.information(self, "Rescan required",
                                    "This screenshot review used the old forced-pair scoring. "
                                    "The new scoring needs a fresh screenshot import. "
                                    "Your saved review will remain until you start the new import.")
            checkpoint = None
        if checkpoint and checkpoint.get("folder_scan", {}).get("screenshot_stage"):
            choice = QMessageBox(self)
            choice.setWindowTitle("Saved screenshot review found")
            choice.setText(f"Resume your screenshot review of {folder.name}?")
            resume = choice.addButton("Resume review", QMessageBox.AcceptRole)
            choice.addButton("Start over", QMessageBox.DestructiveRole)
            choice.addButton("Cancel", QMessageBox.RejectRole)
            choice.setDefaultButton(resume)
            choice.exec()
            if choice.clickedButton() == resume:
                try:
                    scan = cached_folder_scan(folder, checkpoint)
                except (KeyError, ValueError, OSError) as exc:
                    QMessageBox.warning(self, "Saved review unavailable", str(exc))
                else:
                    self.pending_session = checkpoint
                    self.session_source = folder
                    self.screenshot_source = folder
                    self.screenshot_flagged_pages = scan.flagged_pages
                    self.review_mode.setCurrentIndex(int(scan.manual_mode))
                    self.scan_done(scan)
                    return
            elif choice.clickedButton() is None or choice.buttonRole(choice.clickedButton()) == QMessageBox.RejectRole:
                return
        elif checkpoint:
            if QMessageBox.question(self, "Existing review",
                                    "A normal image-folder review is saved for this folder. "
                                    "Starting screenshot import will replace it. Continue?") != QMessageBox.Yes:
                return
        self.screenshot_workers_count = self.settings.value("screenshot_workers", 0, type=int)
        clear_session(folder, app_folder)
        shutil.rmtree(self.screenshot_stage_dir, ignore_errors=True)
        self.screenshot_source = folder
        self.screenshot_single_size = None
        self.pending_session = None
        self.session_source = None
        self.progress_label.setText("Measuring screenshots…")
        self.progress_bar.setValue(0)
        self.pages.setCurrentWidget(self.progress_page)
        self._compact_window()
        diagnostics.note("Screenshot analysis started: %s", folder)
        self._run_worker(analyze_screenshots, (folder, self.screenshot_single_size,
                                               self.screenshot_workers_count),
                         self._screenshot_analysis_done,
                         lambda done, total: self.progress_label.setText(
                             f"Measuring screenshots: {done} of {total}…"))

    def screenshot_crop_summary(self, proposals, attention):
        measured_single = sum(p.kind == "single" and p.source == "measured" for p in proposals)
        measured_double = sum(p.kind == "double" and p.source == "measured" for p in proposals)
        inferred = [p for p in proposals if p.kind == "double" and p.box is not None and p.source != "measured"]
        inferred_single = sum(p.kind == "single" and p.box is not None and p.source != "measured" for p in proposals)
        lines = [f"{measured_single} single-page screenshots matched the page dimensions.",
                 f"{inferred_single} single-page screenshots use the established crop profile.",
                 f"{measured_double} double-page screenshots matched the spread dimensions.",
                 f"{len(inferred)} double-page screenshots use the established crop despite hidden edges.",
                 f"{len(attention)} screenshots need a crop decision."]
        shortfalls, excesses = [], []
        for p in inferred:
            if p.measured_box:
                visible = p.measured_box[2] - p.measured_box[0]
                double = p.box[2] - p.box[0]
                singles = [q.page_width for q in proposals if q.canvas == p.canvas and q.kind == "single" and q.source == "measured"]
                single = max(singles) if singles else round(double / 2)
                shortfalls.append(max(0, double - visible))
                excesses.append(visible - single)
        if shortfalls:
            def span(values):
                return str(min(values)) if min(values) == max(values) else f"{min(values)}–{max(values)}"
            lines.append(f"Visible content was {span(shortfalls)} pixels narrower than the double-page crop, but {span(excesses)} pixels wider than a single page. These screenshots will use the double-page crop.")
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Screenshot measurements complete")
        mixed = {}
        for proposal in proposals:
            if proposal.family_widths:
                mixed[proposal.canvas] = proposal.family_widths
        if mixed:
            lines.insert(0, "Multiple recurring page-size families were detected:\n" + "\n".join(
                f"Screenshot height {canvas[1]}: single widths " + ", ".join(map(str, widths)) + " pixels."
                for canvas, widths in mixed.items()))
            lines.insert(1, "Some pages may originally have been taller and were scaled down during capture. Cropping cannot restore lost resolution. Consider taking new screenshots at the original page height, or continue using each detected family's own crop dimensions. Hidden-edge pages use the smallest established crop that contains the visible content, unless an exposed edge identifies another family. You can inspect or change these choices in Review crop previews. Unresolved crops still need your choice.")
            dialog.setIcon(QMessageBox.Warning)
        dialog.setText("These pages may have been reduced during capture" if mixed else "Ready to crop your screenshots")
        dialog.setInformativeText("\n\n".join(lines))
        verify = dialog.addButton("Review crop previews", QMessageBox.ActionRole)
        proceed = dialog.addButton("Review required crops" if attention else "Continue with detected sizes" if mixed else "Continue to cropping", QMessageBox.AcceptRole)
        dialog.addButton(QMessageBox.Cancel)
        dialog.setDefaultButton(proceed)
        dialog.exec()
        if dialog.clickedButton() == verify:
            return True
        return False if dialog.clickedButton() == proceed else None

    def _screenshot_analysis_done(self, proposals):
        self.progress_bar.setValue(100)
        attention = [index for index, item in enumerate(proposals)
                     if item.box is None or item.source == "review"]
        verify = self.screenshot_crop_summary(proposals, attention)
        if verify is None:
            self.pages.setCurrentIndex(0)
            self._compact_window()
            return
        if verify:
            attention = list(range(len(proposals)))
        reviewed = iter(())
        if attention:
            review = ScreenshotCropDialog([proposals[i] for i in attention],
                                          self.screenshot_single_size, self,
                                          calibration=proposals)
            if review.exec() != QDialog.Accepted:
                if review.back_to_summary:
                    revised = list(proposals)
                    for index, choice in zip(attention, review.choices, strict=True):
                        if choice is not None:
                            revised[index] = replace(proposals[index], path=choice.source, kind=choice.kind,
                                                     box=choice.box, canvas=review.canvases[attention.index(index)],
                                                     source=("review" if choice.flagged else proposals[index].source
                                                             if (choice.source == proposals[index].path and
                                                                 choice.kind == proposals[index].kind and
                                                                 choice.box == proposals[index].box) else "profile"))
                    QTimer.singleShot(0, lambda: self._screenshot_analysis_done(revised))
                    return
                self.screenshot_source = None
                self.screenshot_stage_dir = None
                self.pages.setCurrentIndex(0)
                self._compact_window()
                return
            reviewed = iter(review.approved_choices())
        attention_set = set(attention)
        choices = [next(reviewed) if index in attention_set else
                   CropChoice(item.path, item.kind, item.box)
                   for index, item in enumerate(proposals)]
        self._stage_approved_screenshots(choices)

    def _stage_approved_screenshots(self, choices):
        destination = self.screenshot_stage_dir / "pages"
        screenshot_count = len(choices)
        page_count = sum(2 if choice.kind == "double" else 1 for choice in choices)
        self.progress_label.setText(f"Cropping screenshots: 0 of {screenshot_count}…")
        self.progress_bar.setValue(0)
        self._run_worker(partial(stage_screenshots, right_to_left=self.active_right_to_left),
                         (choices, destination, self.screenshot_source,
                          self.review_mode.currentIndex() == 1,
                          self.screenshot_workers_count),
                         self._screenshot_staged,
                         lambda done, total: self._screenshot_stage_progress(
                             done, screenshot_count, page_count))

    def _screenshot_stage_progress(self, done: int, screenshot_count: int,
                                   page_count: int):
        if done <= screenshot_count:
            self.progress_label.setText(
                f"Cropping screenshots: {done} of {screenshot_count}…")
        else:
            scan_percent = min(100, round(100 * (done - screenshot_count) / screenshot_count))
            activity = "Reading" if self.review_mode.currentIndex() == 1 else "Scanning"
            self.progress_label.setText(
                f"{activity} {page_count} cropped pages for Pair Review… "
                f"{scan_percent}% complete")

    def _screenshot_staged(self, staged: StagedScreenshots):
        self.session_source = self.screenshot_source
        self.screenshot_flagged_pages = staged.flagged_pages
        self.inferred_notice_shown = False
        diagnostics.note("Screenshot import staged: %d pages; %d unusual-width pages flagged",
                         len(staged.scan.pages), len(staged.flagged_pages))
        self.scan_done(staged.scan)

    def _pdf_progress(self, done: int, total: int):
        passes = 2 if self.active_manual_mode else 3
        pages = total // passes
        if not pages:
            return
        if done <= pages:
            self.progress_label.setText(f"{'Extracting JPEG' if self.pdf_session_options.get('extract_jpegs') else 'Rendering PDF'} pages: {done} of {pages}…")
        else:
            scan_percent = min(100, round(100 * (done - pages) / ((passes - 1) * pages)))
            activity = "Reading" if self.active_manual_mode else "Scanning"
            self.progress_label.setText(f"{activity} {pages} {'extracted' if self.pdf_session_options.get('extract_jpegs') else 'rendered'} pages… {scan_percent}% complete")

    def _worker_error(self, message):
        QMessageBox.critical(self, "Comic Stitcher",
                             f"{message}\n\nDiagnostic log: {diagnostics.log_path()}")
        if self.progress_label.text().startswith("Creating"):
            self.pages.setCurrentWidget(self.export_page)
            self._compact_window()
        else:
            self.screenshot_source = None
            self.screenshot_flagged_pages.clear()
            if self.screenshot_stage_dir is not None:
                shutil.rmtree(self.screenshot_stage_dir, ignore_errors=True)
                self.screenshot_stage_dir = None
            if self.import_work is not None:
                self.import_work.cleanup()
                self.import_work = None
            self.pages.setCurrentIndex(0)
            self._compact_window()

    def start_scan(self, folder, *, pdf_dpi=None, pdf_workers_count=None,
                   pdf_smooth_images=None, manual_mode=None, pdf_checked=False, extract_jpegs=False):
        folder = Path(folder).resolve()
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Please wait", "A scan or export is still running.")
            return
        if self.plan is not None:
            QMessageBox.information(self, "Import", "Choose Process another comic before importing a new one.")
            return
        if self.screenshot_mode.isChecked():
            if not folder.is_dir():
                QMessageBox.information(self, "Screenshot import",
                                        "Choose a folder of emulator screenshots for this import mode.")
                return
            self._start_screenshot_import(folder)
            return
        self.screenshot_source = None
        self.screenshot_flagged_pages.clear()
        app_folder = diagnostics.log_path().parent.parent
        if manual_mode is None:
            manual_mode = self.review_mode.currentIndex() == 1
        checkpoint = read_session(folder, app_folder) if not pdf_checked else None
        if checkpoint:
            choice = QMessageBox(self)
            choice.setWindowTitle("Saved review found")
            choice.setText(f"Resume your review of {folder.name}?")
            saved_mode = "Manual" if checkpoint.get("manual_mode", False) else "Suggested pairs"
            choice.setInformativeText(f"Saved mode: {saved_mode}, {'right-to-left' if checkpoint.get('right_to_left', False) else 'left-to-right'}. Your decisions and joins will be restored. "
                                      "Starting over uses the selected mode and replaces the saved review.")
            resume = choice.addButton("Resume review", QMessageBox.AcceptRole)
            choice.addButton("Start over", QMessageBox.DestructiveRole)
            choice.addButton("Cancel", QMessageBox.RejectRole)
            choice.setDefaultButton(resume)
            choice.exec()
            if choice.clickedButton() == resume:
                self.pending_session = checkpoint
                manual_mode = bool(checkpoint.get("manual_mode", False))
                self.active_right_to_left = bool(checkpoint.get("right_to_left", False))
                self.review_mode.setCurrentIndex(int(manual_mode))
                if folder.suffix.lower() == ".pdf":
                    options = checkpoint.get("pdf_options", {})
                    pdf_dpi = options.get("dpi", 300)
                    pdf_workers_count = options.get("workers", 0)
                    pdf_smooth_images = options.get("smooth_images", True)
                    extract_jpegs = bool(options.get("extract_jpegs", False))
            elif choice.clickedButton() is None or choice.buttonRole(choice.clickedButton()) == QMessageBox.RejectRole:
                return
            else:
                self.pending_session = None
                clear_session(folder, app_folder)
        elif not pdf_checked and session_path(folder, app_folder).exists():
            clear_session(folder, app_folder)
        self.session_source = folder
        self.active_manual_mode = manual_mode
        self.pdf_session_options = {"dpi": pdf_dpi or 300, "workers": pdf_workers_count or 0,
                                    "smooth_images": pdf_smooth_images is not False, "extract_jpegs": extract_jpegs}
        if folder.is_file() and folder.suffix.lower() == ".pdf" and pdf_dpi is None and not pdf_checked:
            self.progress_label.setText("Checking PDF page images…")
            self.progress_bar.setValue(0)
            self.pages.setCurrentWidget(self.progress_page)
            self._compact_window()
            self._run_worker(inspect_pdf_jpegs, (folder,),
                             lambda report: self._pdf_images_checked(folder, report, manual_mode, pdf_smooth_images))
            return
        if folder.is_file() and folder.suffix.lower() == ".pdf" and pdf_dpi is None:
            dialog = PdfImportDialog(folder, int(self.settings.value("pdf_dpi", 300)),
                                     int(self.settings.value("pdf_workers", 0)),
                                     (pdf_smooth_images if pdf_smooth_images is not None else
                                      str(self.settings.value("pdf_smooth_images", "true")).lower() != "false"),
                                     self, manual=manual_mode, right_to_left=self.active_right_to_left)
            if dialog.exec() != QDialog.Accepted:
                self.pages.setCurrentIndex(0)
                self._compact_window()
                return
            pdf_dpi, pdf_workers_count = dialog.dpi.value(), dialog.workers.value()
            pdf_smooth_images = dialog.smooth_images.isChecked()
            if dialog.dpi_chosen_by_user or dialog.suggested_dpi is None:
                self.settings.setValue("pdf_dpi", pdf_dpi)
            self.settings.setValue("pdf_workers", pdf_workers_count)
            self.settings.setValue("pdf_smooth_images", pdf_smooth_images)
            self.pdf_session_options = {"dpi": pdf_dpi, "workers": pdf_workers_count,
                                        "smooth_images": pdf_smooth_images, "extract_jpegs": False}
        if self.pending_session and folder.is_dir():
            try:
                self.scan_done(cached_folder_scan(folder, self.pending_session))
                return
            except (KeyError, TypeError, ValueError, OSError) as exc:
                diagnostics.note("Saved folder scan unusable: %s", exc)
                self.pending_session = None
        self.progress_label.setText(f"{'Reading pages in' if manual_mode else 'Scanning'} {folder.name}…")
        diagnostics.note("%s import started: %s", "Manual" if manual_mode else "Suggested", folder)
        self.progress_bar.setValue(0)
        self.done_button.hide()
        self.pages.setCurrentWidget(self.progress_page)
        self._compact_window()
        if folder.is_file() and folder.suffix.lower() in ARCHIVE_EXTS | {".pdf"}:
            self.import_work = tempfile.TemporaryDirectory(prefix="ComicStitcher_import_")
            destination = Path(self.import_work.name) / "pages"
            if folder.suffix.lower() == ".pdf":
                self.progress_label.setText("Extracting original JPEG pages…" if extract_jpegs else
                                            f"Rendering {folder.name} to PNG pages… this may take several minutes.")
                self._run_worker(partial(import_pdf, right_to_left=self.active_right_to_left, extract_jpegs=extract_jpegs), (folder, destination, pdf_dpi or 300,
                                              pdf_workers_count or 0,
                                              True if pdf_smooth_images is None else pdf_smooth_images,
                                              manual_mode),
                                 self.scan_done)
                self.worker.progress.connect(self._pdf_progress)
            else:
                self._run_worker(partial(import_archive, right_to_left=self.active_right_to_left), (folder, destination, manual_mode), self.scan_done)
        else:
            self._run_worker(partial(scan_manual_folder if manual_mode else scan_folder,
                                     right_to_left=self.active_right_to_left),
                             (folder,), self.scan_done)

    def _pdf_images_checked(self, source, report, manual_mode, smooth_images):
        self.import_prompt_active = True
        try:
            self._pdf_images_checked_dialog(source, report, manual_mode, smooth_images)
        finally:
            self.import_prompt_active = False

    def _pdf_images_checked_dialog(self, source, report, manual_mode, smooth_images):
        if self.worker is not None:
            self.worker.wait()
        extract = False
        if report is not None:
            choice = QMessageBox(self)
            choice.setWindowTitle("Original JPEG pages available")
            choice.setText(f"All {report.pages} PDF pages contain one complete, directly extractable JPEG.")
            sizes = "; ".join(f"{width:,} × {height:,} px" for width, height in report.sizes[:6])
            if len(report.sizes) > 6:
                sizes += f"; {len(report.sizes) - 6} additional sizes"
            choice.setInformativeText(
                f"Original image dimensions: {sizes}\n\n"
                "Extract the original JPEGs without rendering or re-encoding them. "
                "Any required RGB color profile is preserved without recompressing the image. DPI and smoothing settings will not apply. You will still review the imported pages before export.")
            original = choice.addButton("Extract original JPEGs", QMessageBox.AcceptRole)
            render = choice.addButton("Render to PNG instead", QMessageBox.ActionRole)
            choice.addButton(QMessageBox.Cancel)
            choice.setDefaultButton(original)
            choice.exec()
            if choice.clickedButton() not in (original, render):
                self.pages.setCurrentIndex(0)
                self._compact_window()
                return
            extract = choice.clickedButton() is original
        self.start_scan(source, pdf_checked=True, extract_jpegs=extract,
                        pdf_dpi=int(self.settings.value("pdf_dpi", 300)) if extract else None,
                        manual_mode=manual_mode, pdf_smooth_images=smooth_images)

    def scan_done(self, scan):
        # Folder-based imports publish beside the input folder. File imports
        # already carry their source file's destination from the importer.
        if scan.screenshot_source is not None:
            scan.output_folder = scan.screenshot_source.parent
        elif scan.source_archive is None:
            scan.output_folder = scan.folder.parent
        if scan.screenshot_source is not None:
            self.screenshot_source = scan.screenshot_source
            self.screenshot_flagged_pages = scan.flagged_pages
            self.screenshot_stage_dir = scan.folder.parent
        self.active_manual_mode = scan.manual_mode
        self.active_right_to_left = scan.right_to_left
        diagnostics.note("%s import finished: %d pages; %d adjacent candidates",
                         "Manual" if scan.manual_mode else "Suggested",
                         len(scan.pages), len(scan.candidates))
        self.progress_bar.setValue(100)
        try:
            self.plan = BookPlan(scan)
        except (ValueError, OSError) as exc:
            self._worker_error(str(exc))
            return
        resumed = self.pending_session
        self.pending_session = None
        if resumed:
            try:
                self.plan = restore_plan(scan, resumed)
            except (KeyError, TypeError, ValueError, IndexError) as exc:
                diagnostics.note("Saved review incompatible with scan: %s", exc)
                QMessageBox.warning(self, "Saved review unavailable",
                                    f"The saved decisions could not be restored: {exc}\nStarting a new review.")
                resumed = None
        self.replay_base_name = ""
        self.replay_count = 0
        self.possible_review_started = bool(resumed and resumed.get("possible_started"))
        reports_folder = diagnostics.log_path().parent.parent / "review-reports"
        self.review_report_path = (reports_folder /
                                   f"review-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}.json")
        self._save_review_report()
        if self.screenshot_source is not None:
            suggested_name = self.screenshot_source.name
        elif scan.source_archive and scan.source_archive.suffix.lower() == ".pdf":
            suggested_name = scan.source_archive.stem
        elif scan.source_archive:
            suggested_name = f"{scan.source_archive.stem} - Rebuilt"
        else:
            suggested_name = scan.folder.name
        self.opt.book_name = suggested_name
        with QSignalBlocker(self.include_comicinfo):
            self.include_comicinfo.setChecked(self.plan.include_comicinfo)
        self.metadata_button.setEnabled(self.plan.include_comicinfo)
        self.book_name.setText(suggested_name)
        saved_handling = self.settings.value("image_handling", None)
        jpeg_sources = all(p.path.suffix.lower() in (".jpg", ".jpeg") for p in scan.pages)
        self.handling.setCurrentIndex(int(saved_handling) if str(saved_handling) in ("0", "1")
                                      else int(jpeg_sources))
        if jpeg_sources:
            self.statusBar().showMessage("JPEG source pages detected: unjoined pages can be copied without re-encoding.")
        if scan.manual_mode:
            self.statusBar().showMessage(f"{len(scan.pages)} pages; manual review of neighboring pairs")
        elif scan.numbering_assumed:
            self.statusBar().showMessage("Page numbers could not be read consistently; using natural filename order.")
        else:
            suggested = sum(c.likely or c.continuity >= POSSIBLE_MIN_SCORE for c in scan.candidates)
            self.statusBar().showMessage(f"{len(scan.pages)} pages; {suggested} suggested pairs")
        if resumed and resumed.get("stage") in ("preview", "export"):
            self.preview_index = max(0, int(resumed.get("preview_index", 0)))
            self.enter_preview()
            if resumed.get("stage") == "export":
                self.show_export()
            return
        if self.plan.deferred_possible:
            self.enter_preview()
            return
        if self.plan.current():
            self.pages.setCurrentWidget(self.review)
            self._expand_window()
            self.show_candidate()
        else:
            self._expand_window()
            self.enter_preview()

    def show_candidate(self):
        item = self.plan.current()
        if item is None:
            self.enter_preview()
            return
        _, c = item
        if (not c.likely and not self.possible_review_started and
                any(candidate.likely for candidate in self.plan.scan.candidates)):
            remaining = self.plan.remaining_possible()
            self.possible_message.setText(
                f"{remaining} lower-scoring Possible pairs remain. Review them individually, or continue "
                "with these pairs unjoined. You can return to them from Final Preview and join any "
                "pages manually there.")
            self.pages.setCurrentWidget(self.possible_choice)
            self.review_possible_button.setFocus()
            return
        a, b = (self.plan.scan.pages[j] for j in (c.left, c.right))
        if self.plan.scan.right_to_left:
            a, b = b, a
        self.seam = False
        self.review_view.setCurrentIndex(0)
        self.review_rotation = self.plan.rotations.get(c.left, 0)
        self.joined_review.rotation = self.review_rotation
        self.joined_review.load_pair((a.width, a.height), (b.width, b.height))
        self.review_generation += 1
        diagnostics.note("Pair Review requested: %d / %d (request %d)",
                         c.left + 1, c.right + 1, self.review_generation)
        self.review_worker.request(self.review_generation, a.path, b.path)
        done = len(self.plan.decisions)
        label = "Manual" if self.plan.scan.manual_mode else "Likely" if c.likely else "Possible"
        warning = " · Unequal heights: taller page will be cropped at bottom" if a.height != b.height else ""
        self.review_heading.setText(f"{label} pair · decision {done + 1} · {a.path.name} + {b.path.name}{warning}")
        self._rotation_heading()
        self.back_button.setEnabled(bool(self.plan.history))
        self.seam_button.review_caption.setText("Seam")
        set_review_icon(self.seam_button, "seam-view", "▥")

    def _review_images_ready(self, generation, left, right):
        if generation != self.review_generation or self.plan is None:
            return
        try:
            self.joined_review.show_pair(QPixmap.fromImage(qimage_from_pixels(left)),
                                         QPixmap.fromImage(qimage_from_pixels(right)))
            diagnostics.note("Pair Review displayed: request %d", generation)
        except Exception as exc:
            diagnostics.exception("Pair image display failed")
            self._review_images_failed(generation, str(exc))

    def _review_images_failed(self, generation, message):
        if generation == self.review_generation:
            self.joined_review.show_error()
            QMessageBox.critical(self, "Preview", f"{message}\n\nDiagnostic log: {diagnostics.log_path()}")

    def _rotation_heading(self):
        heading = self.review_heading.text().split(" · rotate spread ")[0]
        if self.review_rotation:
            heading += f" · rotate spread {self.review_rotation}° clockwise"
        self.review_heading.setText(heading)

    def rotate_candidate(self, delta: int):
        if self.plan is None or self.plan.current() is None:
            return
        if self.seam:
            self.toggle_seam()
        self.review_rotation = (self.review_rotation + delta) % 360
        self.joined_review.rotation = self.review_rotation
        self.joined_review.update()
        self._rotation_heading()

    def open_diagnostic_logs(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(diagnostics.log_path().parent)))

    def open_review_reports(self):
        folder = diagnostics.log_path().parent.parent / "review-reports"
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _save_review_report(self):
        if self.plan is None or self.review_report_path is None:
            return
        try:
            save_report(self.plan, self.review_report_path)
            if self.session_source:
                stage = ("export" if self.pages.currentWidget() == self.export_page else
                         "preview" if self.pages.currentWidget() == self.preview else "review")
                save_session(self.session_source, diagnostics.log_path().parent.parent,
                             self.plan, possible_started=self.possible_review_started,
                             stage=stage, preview_index=self.preview_index,
                             pdf_options=self.pdf_session_options)
        except OSError:
            diagnostics.exception("Could not save pair review report")
            self.statusBar().showMessage("Could not save pair review report; see diagnostic log.")

    def _save_preview_position(self):
        if self.plan and self.session_source and self.pages.currentWidget() == self.preview and self.replay_count == 0:
            try:
                save_session(self.session_source, diagnostics.log_path().parent.parent,
                             self.plan, possible_started=self.possible_review_started,
                             stage="preview", preview_index=self.preview_index,
                             pdf_options=self.pdf_session_options)
            except OSError:
                diagnostics.exception("Could not save preview position")

    def toggle_seam(self):
        item = self.plan.current()
        if item is None:
            return
        if self.seam:
            self.seam = False
            self.review_view.setCurrentIndex(0)
            self.seam_button.review_caption.setText("Seam")
            set_review_icon(self.seam_button, "seam-view", "▥")
            return
        c = item[1]
        try:
            a, b = (self.plan.scan.pages[j].path for j in (c.left, c.right))
            if self.plan is not None and self.plan.scan.right_to_left:
                a, b = b, a
            with Image.open(a) as left, Image.open(b) as right:
                h = min(left.height, right.height)
                strip = min(240, left.width // 3, right.width // 3)
                L = left.crop((left.width - strip, 0, left.width, h)).convert("RGB")
                R = right.crop((0, 0, strip, h)).convert("RGB")
                seam = Image.new("RGB", (2 * strip, h))
                seam.paste(L, (0, 0))
                seam.paste(R, (strip, 0))
                pix = pixmap_from_pil(seam, (900, 6000))
                painter = QPainter(pix)
                painter.setPen(QPen(QColor("#ffe500"), 2))
                seam_x = round(strip * pix.width() / seam.width)
                painter.drawLine(seam_x, 0, seam_x, pix.height() - 1)
                painter.end()
            self.seam_image.setPixmap(pix)
            self.seam_image.resize(pix.size())
            self.seam_area.verticalScrollBar().setValue(0)
            self.seam = True
            self.review_view.setCurrentIndex(1)
            self.seam_button.review_caption.setText("Pages")
            set_review_icon(self.seam_button, "seam-view", "▥", zoomed=True)
        except Exception as exc:
            QMessageBox.critical(self, "Seam View", str(exc))

    def _close_preview_seam(self):
        self.preview_seam_active = False
        self.preview_view.setCurrentIndex(0)
        self.preview_seam_button.review_caption.setText("Seam")
        set_review_icon(self.preview_seam_button, "seam-view", "▥")
        self._update_shortcuts()

    def preview_seam(self):
        if self.preview_seam_active:
            self._close_preview_seam()
            return
        if not self.comparing:
            return
        entries = self.plan.entries()
        # Only two individual adjacent pages have a prospective join seam.
        if any(entries[i][1] is not None for i in self.comparing):
            return
        a, b = (self.plan.scan.pages[entries[i][0]].path for i in self.comparing)
        turns = tuple(self.plan.rotations.get(entries[i][0], 0) for i in self.comparing)
        self._show_preview_seam(a, b, turns)

    def _show_preview_seam(self, a, b, turns=(0, 0)):
        try:
            if self.plan is not None and self.plan.scan.right_to_left:
                a, b = b, a
                turns = turns[::-1]
            with Image.open(a) as left_source, Image.open(b) as right_source:
                left = rotated_image(ImageOps.exif_transpose(left_source), turns[0])
                right = rotated_image(ImageOps.exif_transpose(right_source), turns[1])
                h = min(left.height, right.height)
                strip = min(240, left.width // 3, right.width // 3)
                seam = Image.new("RGB", (2 * strip, h))
                seam.paste(left.crop((left.width - strip, 0, left.width, h)).convert("RGB"), (0, 0))
                seam.paste(right.crop((0, 0, strip, h)).convert("RGB"), (strip, 0))
                pix = pixmap_from_pil(seam, (900, 6000))
                painter = QPainter(pix)
                painter.setPen(QPen(QColor("#ffe500"), 2))
                seam_x = round(strip * pix.width() / seam.width)
                painter.drawLine(seam_x, 0, seam_x, pix.height() - 1)
                painter.end()
            self.preview_seam_image.setPixmap(pix)
            self.preview_seam_image.resize(pix.size())
        except Exception as exc:
            QMessageBox.warning(self, "Seam View", str(exc))
            return
        self.preview_seam_area.verticalScrollBar().setValue(0)
        self.preview_seam_active = True
        self.preview_view.setCurrentIndex(1)
        self.preview_seam_button.review_caption.setText("Pages")
        set_review_icon(self.preview_seam_button, "seam-view", "▥", zoomed=True)
        self.preview_seam_area.setFocus(Qt.OtherFocusReason)
        self._update_shortcuts()

    def decide(self, yes):
        if self.seam:
            self.toggle_seam()
        item = self.plan.current()
        self.plan.decide(yes)
        if yes:
            self.plan.set_rotation(item[1].left, self.review_rotation)
        self._save_review_report()
        self.show_candidate()

    def _confirm_page_removal(self, index: int) -> bool:
        page = self.plan.scan.pages[index]
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Warning)
        dialog.setWindowTitle("Remove page from finished comic?")
        dialog.setText(f"Remove page {index + 1}: {page.path.name}?")
        dialog.setInformativeText(
            "This page will disappear from Final Comic Preview and the exported comic. "
            "Are you sure?")
        remove = dialog.addButton("Remove from comic", QMessageBox.DestructiveRole)
        keep = dialog.addButton("Keep page", QMessageBox.RejectRole)
        dialog.setDefaultButton(keep)
        dialog.exec()
        return dialog.clickedButton() == remove

    def remove_review_page(self):
        item = self.plan.current() if self.plan else None
        if item is None:
            return
        candidate = item[1]
        choice = QMessageBox(self)
        choice.setWindowTitle("Which page should be removed?")
        choice.setText("Choose one page from this pair to exclude from the finished comic.")
        left = self.plan.scan.pages[candidate.left]
        right = self.plan.scan.pages[candidate.right]
        remove_left = choice.addButton(f"Left: {left.path.name}", QMessageBox.ActionRole)
        remove_right = choice.addButton(f"Right: {right.path.name}", QMessageBox.ActionRole)
        cancel = choice.addButton("Cancel", QMessageBox.RejectRole)
        choice.setDefaultButton(cancel)
        choice.exec()
        selected = (candidate.left if choice.clickedButton() == remove_left else
                    candidate.right if choice.clickedButton() == remove_right else None)
        if selected is None or not self._confirm_page_removal(selected):
            return
        try:
            self.plan.remove_page(selected)
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot remove page", str(exc))
            return
        diagnostics.note("Page excluded from comic: %s", self.plan.scan.pages[selected].path.name)
        self._save_review_report()
        self.show_candidate()

    def review_back(self):
        if self.plan.back():
            if not any(not self.plan.scan.candidates[index].likely for index in self.plan.decisions):
                self.possible_review_started = False
            self._save_review_report()
            self.show_candidate()

    def return_review(self):
        if self.plan.deferred_possible:
            self.plan.deferred_possible = False
            self.possible_review_started = True
            self._save_review_report()
            self.pages.setCurrentWidget(self.review)
            self.show_candidate()
        elif self.plan.current() is not None:
            self._save_review_report()
            self.pages.setCurrentWidget(self.review)
            self.show_candidate()
        elif self.plan.history:
            self.plan.back()
            if not any(not self.plan.scan.candidates[index].likely for index in self.plan.decisions):
                self.possible_review_started = False
            self._save_review_report()
            self.pages.setCurrentWidget(self.review)
            self.show_candidate()
        else:
            QMessageBox.information(self, "Pair Review", "No earlier pair decision is available.")

    def enter_preview(self):
        self.preview_index = max(0, self.preview_index)
        self.selected_first = None
        self.comparing = None
        self._expand_window()
        self.pages.setCurrentWidget(self.preview)
        self._save_review_report()
        if self.plan.deferred_possible:
            self.return_review_button.review_caption.setText("Possible")
            self.return_review_button.setToolTip("Review remaining Possible pairs")
        else:
            self.return_review_button.review_caption.setText("Pair review")
            self.return_review_button.setToolTip("Back to Pair Review")
        self.preview_heading.setText("Preparing comic preview…")
        if self.review_report_path:
            self.statusBar().showMessage("Pair review report saved. Open it from Review → Open pair review reports.")

        QTimer.singleShot(0, self._populate_preview)

    def _populate_preview(self):
        if not self.isVisible() or self.plan is None or self.pages.currentWidget() != self.preview:
            return
        self.rebuild_thumbnails()
        self.show_preview()
        if self.screenshot_flagged_pages and not self.inferred_notice_shown:
            self.inferred_notice_shown = True
            QTimer.singleShot(0, lambda: QMessageBox.information(
                self, "Check unusual-width screenshots",
                "A few screenshots had widths between the ordinary single and double sizes. "
                "Their resulting pages are marked with a pink ! in the filmstrip. "
                "Check whether any was a sideways spread that should be reshot before export."))

    def rebuild_thumbnails(self):
        self.clear_page_drop()
        entries = self.plan.entries()
        self.thumb_keys = [(self.plan.scan.pages[a].path,
                            self.plan.scan.pages[b].path if b is not None else None,
                            self.plan.rotations.get(a, 0), self.plan.joined_rotations.get(a, (0, 0)))
                           for a, b in entries]
        if self.plan.scan.right_to_left:
            self.thumb_keys = [(right, left, rotation, parts[::-1]) if right is not None else (left, right, rotation, parts)
                               for left, right, rotation, parts in self.thumb_keys]
        self.thumb_row.setDirection(QHBoxLayout.RightToLeft if self.plan.scan.right_to_left else QHBoxLayout.LeftToRight)
        self.thumb_generation = self.thumb_worker.reset(self.thumb_keys.copy())
        while self.thumb_row.count():
            item = self.thumb_row.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
                item.widget().deleteLater()
        self.thumb_buttons = []
        self.highlighted.clear()
        for index, (a, b) in enumerate(entries):
            button = PageThumbnail(index, self)
            button.setFixedSize(105, 112)
            marked = a in self.screenshot_flagged_pages or (
                b is not None and b in self.screenshot_flagged_pages)
            pix = self.thumb_cache.get(self.thumb_keys[index])
            if pix is not None:
                button.setIcon(pix)
                button.setIconSize(QSize(100, 84))
                button.setText("")
            button.setToolTip(f"Final page {index + 1}" + (" (spread)" if b is not None else "") +
                              (" — check unusual-width screenshot" if marked else "") +
                              "\nDrag to the left or right half of a thumbnail; the pink dotted line shows the insertion position. Shift-click a neighboring thumbnail to compare or swap.")
            if marked:
                button.setStyleSheet("border:4px solid #ed3c9b;")
                badge = QLabel("!", button)
                badge.setAlignment(Qt.AlignCenter)
                badge.setFixedSize(27, 27)
                badge.move(76, 2)
                badge.setStyleSheet("background:#ed3c9b; color:white; border-radius:13px; "
                                    "font-size:19px; font-weight:bold;")
                badge.setAttribute(Qt.WA_TransparentForMouseEvents)
            button.clicked.connect(lambda _=False, i=index: self.thumbnail_clicked(i))
            self.thumb_row.addWidget(button)
            self.thumb_buttons.append(button)
        self.thumb_row.addStretch()
        # QScrollArea was given its widget before the thumbnails existed. With
        # widgetResizable=False, adjustSize() leaves it at 8x8 on some Qt
        # versions, clipping every button even though their icons are ready.
        self._resize_thumbnail_strip()
        self._queue_visible_thumbnails()
        if self.pages.currentWidget() == self.preview:
            # Rebuilding the strip resets its scroll range. The new buttons
            # acquire their positions after Qt processes the layout update.
            QTimer.singleShot(0, self._center_current_thumbnail)

    def eventFilter(self, watched, event):
        if (getattr(self, "thumb_area", None) is not None and
                watched is self.thumb_area.viewport() and event.type() == QEvent.Resize and
                getattr(self, "thumb_buttons", None)):
            QTimer.singleShot(0, self._resize_thumbnail_strip)
        return super().eventFilter(watched, event)

    def _resize_thumbnail_strip(self):
        if not getattr(self, "thumb_buttons", None):
            return
        viewport_width = self.thumb_area.viewport().width()
        # The padding lets the first and last thumbnail also reach the center.
        side = max(4, (viewport_width - self.thumb_buttons[0].width()) // 2)
        self.thumb_row.setContentsMargins(side, 4, side, 4)
        width = (2 * side + sum(button.width() for button in self.thumb_buttons) +
                 len(self.thumb_buttons) * self.thumb_row.spacing())
        self.thumb_content.setFixedSize(width, 124)
        self.thumb_row.activate()
        if self.pages.currentWidget() == self.preview:
            self._center_current_thumbnail()

    def _center_current_thumbnail(self):
        if not getattr(self, "thumb_buttons", None):
            return
        button = self.thumb_buttons[max(0, min(self.preview_index, len(self.thumb_buttons) - 1))]
        target = button.x() + button.width() // 2 - self.thumb_area.viewport().width() // 2
        self.thumb_area.horizontalScrollBar().setValue(target)

    def _queue_visible_thumbnails(self, *_):
        if not getattr(self, "thumb_buttons", None) or not self.thumb_worker.isRunning():
            return
        bar = self.thumb_area.horizontalScrollBar()
        pitch = 105 + self.thumb_row.spacing()
        pad = self.thumb_row.contentsMargins().left()
        first = max(0, (bar.value() - pad) // pitch - 3)
        last = min(len(self.thumb_buttons),
                   (bar.value() + max(800, self.thumb_area.viewport().width()) - pad) // pitch + 4)
        if self.plan.scan.right_to_left:
            first, last = max(0, len(self.thumb_buttons) - last), min(len(self.thumb_buttons), len(self.thumb_buttons) - first)
        # The worker appends at the front, so request in reverse display order.
        for index in reversed(range(first, last)):
            if self.thumb_buttons[index].icon().isNull():
                self.thumb_worker.request(self.thumb_generation, index)

    def _thumbnail_ready(self, generation: int, index: int, image: tuple[bytes, int, int]):
        if generation != self.thumb_generation or index >= len(self.thumb_buttons):
            return
        pix = QPixmap.fromImage(qimage_from_pixels(image))
        button = self.thumb_buttons[index]
        button.setIcon(pix)
        button.setIconSize(QSize(100, 84))
        button.setText("")
        key = self.thumb_keys[index]
        self.thumb_cache[key] = pix
        self.thumb_cache.move_to_end(key)
        if len(self.thumb_cache) > 256:
            self.thumb_cache.popitem(last=False)


    def _thumbnail_failed(self, generation: int, index: int):
        if generation == self.thumb_generation and index < len(self.thumb_buttons):
            self.thumb_buttons[index].setText("Image error")

    def thumbnail_clicked(self, index):
        if (QApplication.keyboardModifiers() & Qt.ShiftModifier and
                self.selected_first is not None and abs(index - self.selected_first) == 1):
            a, b = sorted((index, self.selected_first))
            self.comparing = (a, b)
            self.preview_index = a
        else:
            self.selected_first = index
            self.preview_index = index
            self.comparing = None
        self.show_preview(follow=False)

    def show_preview(self, follow=True):
        self._close_preview_seam()
        entries = self.plan.entries()
        review_control(self.previous_preview_button, "→" if self.plan.scan.right_to_left else "←", "Previous",
                       icon_label="Next" if self.plan.scan.right_to_left else "Previous")
        review_control(self.next_preview_button, "←" if self.plan.scan.right_to_left else "→", "Next",
                       icon_label="Previous" if self.plan.scan.right_to_left else "Next")
        if not entries:
            return
        self.preview_index = max(0, min(self.preview_index, len(entries) - 1))
        a, b = entries[self.preview_index]
        try:
            if self.comparing:
                first, second = (entries[i] for i in self.comparing)
                if first[1] is None and second[1] is None:
                    left = self.plan.scan.pages[first[0]]
                    right = self.plan.scan.pages[second[0]]
                    if self.plan.scan.right_to_left:
                        left, right = right, left
                    self.main_image.hide()
                    self.compare_image.hide()
                    self.preview_pair.show()
                    self.preview_pair.rotation = 0
                    indices = (second[0], first[0]) if self.plan.scan.right_to_left else (first[0], second[0])
                    turns = [self.plan.rotations.get(i, 0) for i in indices]
                    sizes = [(page.height, page.width) if turn % 180 else (page.width, page.height)
                             for page, turn in zip((left, right), turns, strict=True)]
                    self.preview_pair.load_pair(*sizes)
                    self.preview_pair.show_pair(page_pixmap(left.path, rotation=turns[0]), page_pixmap(right.path, rotation=turns[1]))
                else:
                    self.preview_pair.hide()
                    self.main_image.show()
                    self.main_image.show_pixmap(page_pixmap(self.plan.scan.pages[first[0]].path,
                                                            rotation=self.plan.rotations.get(first[0], 0))
                                                if first[1] is None else spread_pixmap(
                                                    self.plan.scan.pages[first[0]].path,
                                                    self.plan.scan.pages[first[1]].path,
                                                    rotation=self.plan.rotations.get(first[0], 0),
                                                    right_to_left=self.plan.scan.right_to_left,
                                                               page_rotations=self.plan.joined_rotations.get(first[0], (0, 0))))
                    self.compare_image.show()
                    self.compare_image.show_pixmap(page_pixmap(self.plan.scan.pages[second[0]].path,
                                                               rotation=self.plan.rotations.get(second[0], 0))
                                                   if second[1] is None else spread_pixmap(
                                                       self.plan.scan.pages[second[0]].path,
                                                       self.plan.scan.pages[second[1]].path,
                                                       rotation=self.plan.rotations.get(second[0], 0),
                                                       right_to_left=self.plan.scan.right_to_left,
                                                               page_rotations=self.plan.joined_rotations.get(second[0], (0, 0))))
            else:
                self.preview_pair.hide()
                self.main_image.show()
                self.compare_image.hide()
                self.main_image.show_pixmap(page_pixmap(self.plan.scan.pages[a].path,
                                                        rotation=self.plan.rotations.get(a, 0)) if b is None
                                            else spread_pixmap(self.plan.scan.pages[a].path,
                                                               self.plan.scan.pages[b].path,
                                                               rotation=self.plan.rotations.get(a, 0),
                                                               right_to_left=self.plan.scan.right_to_left,
                                                               page_rotations=self.plan.joined_rotations.get(a, (0, 0))))
        except Exception as exc:
            QMessageBox.critical(self, "Preview", str(exc))
        height_warning = ""
        if b is not None and self.plan.scan.pages[a].height != self.plan.scan.pages[b].height:
            height_warning = " · taller page cropped at bottom"
        if self.comparing:
            p, q = (entries[i][0] for i in self.comparing)
            if self.plan.scan.pages[p].height != self.plan.scan.pages[q].height:
                height_warning = " · joining will crop taller page at bottom"
        self.preview_heading.setText(f"Finished comic · page {self.preview_index + 1} of {len(entries)}"
                                     + (" · compare adjacent pages" if self.comparing else "") +
                                     (" · ! Check unusual-width screenshot" if a in self.screenshot_flagged_pages or
                                      (b is not None and b in self.screenshot_flagged_pages) else "") +
                                     height_warning)
        highlighted = {self.preview_index}
        if self.comparing:
            highlighted.update(self.comparing)
        for index in self.highlighted - highlighted:
            self.thumb_buttons[index].setStyleSheet(self._thumbnail_border(index, False))
        for index in highlighted - self.highlighted:
            self.thumb_buttons[index].setStyleSheet(self._thumbnail_border(index, True))
        self.highlighted = highlighted
        if follow:
            self._center_current_thumbnail()
        if self.thumb_buttons[self.preview_index].icon().isNull():
            self.thumb_worker.request(self.thumb_generation, self.preview_index)
        self._queue_visible_thumbnails()
        self.join_button.setEnabled(bool(self.comparing and
             entries[self.comparing[0]][1] is None and entries[self.comparing[1]][1] is None and
             self.comparing[1] == self.comparing[0] + 1))
        self.swap_button.setEnabled(self.comparing is not None)
        self.preview_seam_button.setEnabled(bool(self.comparing and
            all(entries[i][1] is None for i in self.comparing)))
        self.split_button.setEnabled(b is not None and self.comparing is None)
        self.remove_preview_button.setEnabled(b is None and self.comparing is None)
        self.undo_remove_button.setEnabled(bool(self.plan.preview_history))
        self.undo_remove_button.setToolTip("Undo " + self.plan.preview_history[-1]["_action"]
                                           if self.plan.preview_history else "No final-review edits to undo")
        for button in (self.rotate_preview_left, self.rotate_preview_right):
            button.setEnabled(self.comparing is None)
        self.preview_checkpoint_timer.start(800)

    def _thumbnail_border(self, index: int, selected: bool) -> str:
        a, b = self.plan.entries()[index]
        marked = a in self.screenshot_flagged_pages or (
            b is not None and b in self.screenshot_flagged_pages)
        if marked:
            return "border:4px solid #ed3c9b;" + ("background:#d7f4dd;" if selected else "")
        return "border:3px solid #4a9e66;" if selected else ""

    def rotate_preview_page(self, delta: int):
        if self.plan is None or self.comparing:
            return
        left, _ = self.plan.entries()[self.preview_index]
        self.plan.set_rotation(left, (self.plan.rotations.get(left, 0) + delta) % 360)
        self._save_review_report()
        self.rebuild_thumbnails()
        self.show_preview()

    def navigate(self, delta):
        self.comparing = None
        self.preview_index = max(0, min(self.preview_index + delta, len(self.plan.entries()) - 1))
        self.selected_first = self.preview_index
        self.show_preview(follow=True)

    def clear_page_drop(self):
        self.page_drop_boundary = None
        self.page_drop_marker.hide()

    def page_drop_at(self, x):
        rtl = self.plan.scan.right_to_left
        for index, button in enumerate(self.thumb_buttons):
            midpoint = button.x() + button.width() / 2
            if (rtl and x > midpoint) or (not rtl and x < midpoint):
                return index
        return len(self.thumb_buttons)

    def update_page_drop(self, source, x):
        if not isinstance(source, PageThumbnail) or source.owner is not self or source not in self.thumb_buttons:
            self.clear_page_drop(); return False
        boundary = self.page_drop_at(x)
        self.page_drop_boundary = boundary
        rtl = self.plan.scan.right_to_left
        if boundary == 0:
            button = self.thumb_buttons[0]
            edge = button.x() + (button.width() if rtl else 0)
        elif boundary == len(self.thumb_buttons):
            button = self.thumb_buttons[-1]
            edge = button.x() + (0 if rtl else button.width())
        else:
            before, after = self.thumb_buttons[boundary - 1:boundary + 1]
            edge = ((before.x() + after.x() + after.width()) / 2 if rtl else
                    (before.x() + before.width() + after.x()) / 2)
        self.page_drop_marker.setGeometry(round(edge) - 3, 2, 6, self.thumb_content.height() - 4)
        self.page_drop_marker.show(); self.page_drop_marker.raise_()
        return True

    def finish_page_drop(self, source, x):
        if not self.update_page_drop(source, x):
            return False
        boundary = self.page_drop_boundary
        target = boundary - (1 if source.index < boundary else 0)
        index = source.index
        self.clear_page_drop()
        if index != target:
            QTimer.singleShot(0, lambda: self.move_preview_entry(index, target))
        return True

    def move_preview_entry(self, source, target):
        if self.plan is None or source == target:
            return
        self.plan.move_entry(source, target)
        self.preview_index = target
        self.comparing = None
        self.selected_first = target
        self._save_review_report()
        self.rebuild_thumbnails()
        self.show_preview()

    def swap_selected(self):
        if not self.comparing:
            return
        first, second = self.comparing
        self.plan.swap_entries(first, second)
        self._save_review_report()
        self.rebuild_thumbnails()
        self.comparing = (first, second)
        self.show_preview()

    def join_selected(self):
        if not self.comparing:
            return
        left = self.plan.entries()[self.comparing[0]][0]
        right = self.plan.entries()[self.comparing[1]][0]
        try:
            self.plan.join(left, right=right)
        except BoundaryOverrideRequired as exc:
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Question)
            dialog.setWindowTitle("Join across scanner boundary?")
            dialog.setText(str(exc))
            dialog.setInformativeText("These are adjacent individual pages. Join them anyway?")
            join_button = dialog.addButton("Join anyway", QMessageBox.AcceptRole)
            dialog.addButton("Cancel", QMessageBox.RejectRole)
            dialog.setDefaultButton(join_button)
            dialog.exec()
            if dialog.clickedButton() != join_button:
                return
            self.plan.join(left, override_boundary=True, right=right)
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot join", str(exc))
            return
        self.comparing = None
        self._save_review_report()
        self.selected_first = None
        self.rebuild_thumbnails()
        self.show_preview()

    def split_selected(self):
        left, right = self.plan.entries()[self.preview_index]
        if right is None:
            return
        self.plan.split(left)
        self._save_review_report()
        self.selected_first = None
        self.rebuild_thumbnails()
        self.show_preview()

    def remove_preview_page(self):
        if self.plan is None or self.comparing:
            return
        index, right = self.plan.entries()[self.preview_index]
        if right is not None:
            return
        if not self._confirm_page_removal(index):
            return
        try:
            self.plan.remove_page(index)
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot remove page", str(exc))
            return
        diagnostics.note("Page excluded from comic: %s", self.plan.scan.pages[index].path.name)
        self.comparing = None
        self.selected_first = None
        self._save_review_report()
        self.rebuild_thumbnails()
        self.show_preview()

    def undo_preview_removal(self):
        if self.plan is None:
            return
        saved = self.plan.undo_preview()
        if saved is None:
            return
        entries = self.plan.entries()
        self.preview_index = next((i for i, entry in enumerate(entries)
                                   if saved["_focus"] in entry), min(self.preview_index, len(entries) - 1))
        self.comparing = None
        self.selected_first = self.preview_index
        self._save_review_report()
        self.rebuild_thumbnails()
        self.show_preview()

    def _metadata_toggled(self, checked):
        self.metadata_button.setEnabled(checked)
        if self.plan is not None:
            self.plan.include_comicinfo = checked
            self._save_review_report()
        self.refresh_export()

    def get_comicvine_client(self):
        if not self.comicvine_client.configured:
            self.show_settings()
        return self.comicvine_client if self.comicvine_client.configured else None

    def show_metadata(self):
        if self.plan is None:
            return
        dialog = MetadataDialog(self.plan.comicinfo_xml, self,
                                search_hint=(self.plan.scan.source_archive.stem if self.plan.scan.source_archive else self.book_name.text()),
                                lookup_provider=self.get_comicvine_client)
        if dialog.exec() == QDialog.Accepted:
            self.plan.comicinfo_xml = dialog.result_xml
            self._save_review_report()
            self.refresh_export()

    def show_export(self):
        self.pages.setCurrentWidget(self.export_page)
        self._save_review_report()
        self._compact_window()
        self.refresh_export()

    def return_preview_from_export(self):
        self.pages.setCurrentWidget(self.preview)
        self._expand_window()

    def show_output_options(self):
        self.options_from_screen()
        limits = {"width": 1, "height": 1}
        for left, right in (self.plan.entries() if self.plan else []):
            page = self.plan.scan.pages[left]
            width, height = page.width, page.height
            if right is not None:
                other = self.plan.scan.pages[right]
                width += other.width
                height = min(height, other.height)
            if self.plan.rotations.get(left, 0) in (90, 270):
                width, height = height, width
            limits["width"] = max(limits["width"], width)
            limits["height"] = max(limits["height"], height)
        dialog = OutputOptionsDialog(self.opt, self.custom_resize_values, self, size_limits=limits if self.plan else None)
        if dialog.exec() == QDialog.Accepted:
            self.custom_resize_values = dialog.apply_to(self.opt)
            self._save_output_preferences()
            self.refresh_export()

    def _save_output_preferences(self):
        self.settings.setValue("reduced_custom", self.opt.reduced_custom)
        self.settings.setValue("reduced_settings", json.dumps(self.opt.reduced_settings))
        for key, value in (("jpeg_quality", self.opt.jpeg_quality),
                           ("webp_quality", self.opt.webp_quality),
                           ("output_format", self.opt.format),
                           ("spread_format", self.opt.spread_format),
                           ("output_sizes", self.opt.size_output)):
            self.settings.setValue(key, value)
        for name in ("jpeg_progressive", "jpeg_optimize", "jpeg_subsampling", "jpeg_smooth",
                     "webp_lossless", "webp_method", "png_compress"):
            self.settings.setValue(name, getattr(self.opt, name))
        for key, value in self.custom_resize_values.items():
            self.settings.setValue("resize_" + key, value)
        self.settings.sync()

    def _update_naming_examples(self, opt: ExportOptions):
        """Show the actual first filename under each naming style."""
        if self.plan is None:
            return
        try:
            title = safe_book_name(opt.book_name)
        except ValueError:
            title = "Book Title"
        sample = replace(opt, book_name=title, custom_prefix=None)
        reduced = sample.size_output == "reduced"
        try:
            number = planned_names(self.plan, replace(sample, prefixed=False), reduced=reduced)[0]
            titled = planned_names(self.plan, replace(sample, prefixed=True), reduced=reduced)[0]
        except ValueError:
            number, titled = "001.jpg", f"{title} - 001.jpg"
        try:
            custom = planned_names(self.plan, replace(sample, custom_prefix=self.custom_page_prefix.text()),
                                   reduced=reduced)[0]
        except ValueError:
            custom = "Enter a custom page prefix"
        with QSignalBlocker(self.naming):
            self.naming.setItemText(2, f"Custom prefix ({custom})")
            self.naming.setItemText(0, f"Number only ({number})")
            self.naming.setItemText(1, titled)
        self.naming.setItemData(0, f"First filename: {number}", Qt.ToolTipRole)
        self.naming.setItemData(1, f"First filename: {titled}", Qt.ToolTipRole)
        self.naming.setItemData(2, f"First filename: {custom}", Qt.ToolTipRole)

    def _archive_changed(self, *_):
        if self.archive_format.currentIndex() == 2:
            self.keep_images.setChecked(True)
        self.keep_images.setEnabled(self.archive_format.currentIndex() != 2)
        self.settings.setValue("archive_format", ("cbz", "cb7", "none")[self.archive_format.currentIndex()])
        self.refresh_export()

    def options_from_screen(self):
        self.opt.comicinfo_xml = ((self.plan.comicinfo_xml or '<ComicInfo/>')
                                 if self.plan is not None and self.include_comicinfo.isChecked() else None)
        self.opt.book_name = self.book_name.text()
        self.opt.prefixed = self.naming.currentIndex() == 1
        self.opt.custom_prefix = self.custom_page_prefix.text() if self.naming.currentIndex() == 2 else None
        self.opt.start = 1 if self.start_number.currentIndex() == 0 else 0
        self.opt.padding = (0, 2, 3, 4)[self.padding.currentIndex()]
        self.opt.keep_originals = self.handling.currentIndex() == 1
        self.opt.cbz = self.archive_format.currentIndex() != 2
        self.opt.archive_format = "cb7" if self.archive_format.currentIndex() == 1 else "cbz"
        self.opt.keep_images = self.keep_images.isChecked()
        self.opt.front_to_back = self.front_to_back.isChecked() if self.plan and self.plan.scan.right_to_left else True
        self.opt.workers = self.encoder_workers.value()
        values = self.custom_resize_values
        self.opt.resize_mode = values["mode"]
        self.opt.resize_pixels = values["pixels"]
        self.opt.resize_reduce_only = True
        self.opt.resize_gamma = values["gamma"]
        self.opt.resize_filter = values["filter"]
        return self.opt

    def refresh_export(self, *_):
        if hasattr(self, "book_form"):
            # Hidden form rows must also hide their labels and spacing. Measure
            # the ordinary three-row layout with the current platform font/style.
            self.book_form.setRowVisible(self.custom_prefix_row, False)
            self.book_scroll.widget().ensurePolished()
            self.book_form.invalidate()
            normal_height = self.book_form.minimumSize().height()
            self.book_scroll.setFixedHeight(normal_height + 2)
            self.book_form.setRowVisible(self.custom_prefix_row, self.naming.currentIndex() == 2)
        if hasattr(self, "create_button"):
            self.create_button.setVisible(not self.batch_import_active)
            self.add_queue_button.setText("Review final output" if self.batch_import_active else "Review queued output")
            if self.batch_import_active:
                ui_primary(self.add_queue_button)
            else:
                self.add_queue_button.setStyleSheet("")
        if self.plan is None:
            return
        self.front_to_back.setVisible(self.plan.scan.right_to_left)
        try:
            opt = self.options_from_screen()
            self._update_naming_examples(opt)
            summary = [self._format_summary(copy_encoding_options(opt, opt.size_output == "reduced")),
                       {"full": "Full size", "reduced": "Reduced size", "both": "Both sizes"}[opt.size_output]]
            if opt.size_output == "both" and opt.reduced_custom:
                summary.append("Reduced: " + self._format_summary(copy_encoding_options(opt, True)))
            if opt.size_output != "full":
                summary.extend([f"{opt.resize_mode.title()} {opt.resize_pixels} px", opt.resize_filter.title(),
                                "Gamma correction" if opt.resize_gamma else "No gamma correction"])
            self.output_summary.setText("  ·  ".join(summary[:2]) +
                                        ("\n" + "  ·  ".join(summary[2:]) if len(summary) > 2 else ""))
            safe_book_name(opt.book_name)
            planned_names(self.plan, opt, reduced=opt.size_output == "reduced")
            if not opt.cbz and not opt.keep_images:
                raise ValueError("Choose an archive, processed images, or both.")
            self.export_hint.setText("Choose your settings, then review the output before creating the comic.")
            self.create_button.setEnabled(True)
            self.add_queue_button.setEnabled(True)
            self._schedule_size_estimate()
        except (ValueError, IndexError) as exc:
            self.export_hint.setText(str(exc))
            self.create_button.setEnabled(False)
            self.add_queue_button.setEnabled(False)
            self._cancel_size_estimate("")

    def _estimate_wanted(self):
        return (hasattr(self, "size_worker") and self.estimate_size.isChecked() and self.plan is not None
                and self.pages.currentWidget() is self.export_page and self.create_button.isEnabled())

    def _schedule_size_estimate(self, *_):
        if not self._estimate_wanted():
            self._cancel_size_estimate("")
            return
        self.estimate_generation += 1  # Any result still on its way is now out of date.
        self.size_estimate.setText("Estimating…")
        self.estimate_timer.start()

    def _cancel_size_estimate(self, text):
        if hasattr(self, "size_worker"):
            self.estimate_timer.stop()
            self.estimate_generation += 1
            if self.pages.currentWidget() is self.export_page and self.estimate_size.isChecked():
                self.size_worker.cancel()
            else:
                self.size_worker.release()
            self.size_estimate.setText(text)

    def _start_size_estimate(self):
        if not self._estimate_wanted():
            return
        try:
            opt = self.options_from_screen()
        except (ValueError, IndexError):
            self.size_estimate.setText("")
            return
        self.estimate_png_pages = not opt.cbz
        if not self.size_worker.isRunning():
            self.size_worker.start()  # Only once an estimate is first wanted.
        self.size_worker.request(self.estimate_generation, self.plan, opt)

    def _size_estimate_ready(self, generation, sizes, final=True):
        if generation != self.estimate_generation:
            return
        what = " of PNG pages" if self.estimate_png_pages else ""
        if len(sizes) == 1:
            text = f"About {readable_size(next(iter(sizes.values())))}{what}"
        else:
            text = (f"Full: about {readable_size(sizes['full'])}  ·  "
                    f"Reduced: about {readable_size(sizes['reduced'])}{what}")
        self.size_estimate.setText(text if final else text + " (refining…)")

    def _size_estimate_failed(self, generation, _message):
        if generation == self.estimate_generation:
            self.size_estimate.setText("Size estimate unavailable")

    def _estimate_toggled(self, checked):
        self.settings.setValue("estimate_size", checked)
        self._schedule_size_estimate()

    def export_review_text(self, opt: ExportOptions) -> str:
        title = safe_book_name(opt.book_name)
        full_names = planned_names(self.plan, opt)
        reduced_names = planned_names(self.plan, opt, reduced=True)
        lines = [f"Source: {self.plan.scan.source_archive or self.plan.scan.folder}",
                 f"{len(self.plan.scan.pages)} source pages → {len(full_names)} final pages per copy "
                 f"({len(self.plan.approved())} joined spreads)",
                 f"Numbering: starts at {opt.start}"]
        if self.plan.rotations:
            lines.append(f"Rotated final pages: {len(self.plan.rotations)} (orientation shown in final preview)")
        kinds = ("full", "reduced") if opt.size_output == "both" else (opt.size_output,)
        for kind in kinds:
            names = reduced_names if kind == "reduced" else full_names
            tag = f" - {'Full' if kind == 'full' else 'Reduced'}" if opt.size_output == "both" else ""
            lines.extend(("", f"{kind.title()} copy: {names[0]} … {names[-1]}"))
            if kind == "full" and opt.keep_originals:
                lines.append(f"Unchanged single pages copied without re-encoding; joins and rotated singles: {opt.spread_format.upper()}")
            else:
                quality = (f", {'lossless effort' if opt.format == 'webp' and opt.webp_lossless else 'quality'} "
                           f"{opt.jpeg_quality if opt.format == 'jpeg' else opt.webp_quality}"
                           if opt.format != "png" else " (lossless)")
                lines.append(f"Format: {opt.format.upper()}{' (lossless)' if opt.format == 'webp' and opt.webp_lossless else ''}{quality}")
            if kind == "reduced":
                lines.append(f"Resize by {opt.resize_mode} to {opt.resize_pixels} px; "
                             f"{opt.resize_filter}; {'gamma correction' if opt.resize_gamma else 'no gamma correction'}; "
                             f"{'never enlarge pages'}")
            if opt.cbz:
                lines.append(f"{opt.archive_format.upper()}: {title}{tag}.{opt.archive_format}")
            if opt.keep_images:
                lines.append(f"Lossless PNG pages: {title}{tag} - PNG pages")
        if self.plan.scan.right_to_left:
            lines.append("Page order: " + ("Front to back (front cover first)" if opt.front_to_back
                                            else "Back to front (back cover first)"))
        return "\n".join(lines)

    def _format_summary(self, opt, original=False):
        if original or opt.keep_originals and opt.size_output == "full":
            return f"Original formats · joined/rotated pages: {opt.spread_format.upper()}"
        if opt.format == "png":
            return "PNG · lossless"
        if opt.format == "webp" and opt.webp_lossless:
            return f"WebP · lossless · effort {opt.webp_quality}"
        return f"{ 'JPEG' if opt.format == 'jpeg' else 'WebP'} · quality " + str(
            opt.jpeg_quality if opt.format == "jpeg" else opt.webp_quality)

    def _queue_action_text(self):
        if not self.batch_import_active:
            return "Add to queue"
        return "Queue comic and review next" if self.pending_imports else "Queue comic and start exports"

    def confirm_export(self, opt: ExportOptions, *, queue: bool = False) -> bool:
        dialog = OutlinedDialog(self)
        dialog.setWindowTitle("Review queued comic" if queue else "Review comic export")
        dialog.resize(780 if opt.size_output == "both" else 620, 540)
        dialog.setStyleSheet(
            "QGroupBox { font-size:17px; font-weight:600; background:#fff; border:1px solid #ccd5de; "
            "border-radius:9px; margin-top:28px; } "
            "QGroupBox::title { subcontrol-origin:margin; subcontrol-position:top left; top:0px; left:12px; } "
            "QPushButton { padding:7px 14px; }")
        box = QVBoxLayout(dialog)
        message = "This is what will be created when you start the queue. Does it look right?"
        if queue and self.batch_import_active and not self.pending_imports:
            message = (f"This is the last comic. Confirm to queue it and start exporting all "
                       f"{len(self.queue_jobs) + 1} queued comics. Does it look right?")
        box.setContentsMargins(22, 20, 22, 20)
        box.setSpacing(14)
        box.addWidget(ui_heading("Ready to queue" if queue else "Ready to export", 28))
        note = QLabel(message if queue else "This is what will be created.")
        note.setWordWrap(True)
        box.addWidget(note)
        if getattr(self, "export_name_notice", ""):
            notice = QLabel(self.export_name_notice)
            notice.setWordWrap(True)
            notice.setTextFormat(Qt.PlainText)
            box.addWidget(notice)
        source = self.plan.scan.source_archive or self.plan.scan.folder
        source_label = ui_heading(f"Source: {source.name}", 17)
        source_label.setToolTip(str(source))
        box.addWidget(source_label)
        stats = [f"{len(self.plan.scan.pages)} source pages", f"{len(self.plan.entries())} final pages",
                 f"{len(self.plan.approved())} spreads joined", f"Numbering starts at {opt.start}"]
        if self.plan.removed_pages:
            stats.append(f"{len(self.plan.removed_pages)} removed")
        if self.plan.rotations:
            stats.append(f"{len(self.plan.rotations)} rotated")
        counts = QGridLayout()
        counts.setSpacing(8)
        for index, text in enumerate(stats):
            pill = QLabel(text)
            pill.setAlignment(Qt.AlignCenter)
            pill.setStyleSheet("background:white; border:1px solid #ccd5de; "
                              "padding:8px 10px; border-radius:13px;")
            counts.addWidget(pill, index // 4, index % 4)
        box.addLayout(counts)
        copies = QHBoxLayout()
        kinds = ("full", "reduced") if opt.size_output == "both" else (opt.size_output,)
        for kind in kinds:
            card = QGroupBox(f"{kind.title()} copy")
            content = QVBoxLayout(card)
            content.setContentsMargins(14, 16, 14, 14)
            content.setSpacing(10)
            def line(text, content=content):
                label = QLabel(text)
                label.setWordWrap(True)
                label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                content.addWidget(label)
            line(self._format_summary(copy_encoding_options(opt, kind == "reduced"), original=kind == "full" and opt.keep_originals))
            names = planned_names(self.plan, opt, reduced=kind == "reduced")
            line("Filename pattern")
            line(compact_filename_range(names))
            suffix = f" - {kind.title()}" if len(kinds) == 2 else ""
            title = safe_book_name(opt.book_name) + suffix
            if opt.cbz:
                line(f"{opt.archive_format.upper()} archive\n{title}.{opt.archive_format}")
            if opt.keep_images:
                line(f"Lossless PNG pages\n{title} - PNG pages")
            if kind == "reduced":
                line(f"{opt.resize_mode.title()} {opt.resize_pixels} px · {opt.resize_filter.title()}")
                line(("Gamma correction" if opt.resize_gamma else "No gamma correction") + " · " +
                     ("Never enlarge pages"))
            content.addStretch()
            copies.addWidget(card, 1)
        box.addLayout(copies)
        if self.plan.scan.right_to_left:
            order = QLabel("Page order: " + ("Front to back — front cover first" if opt.front_to_back
                                             else "Back to front — back cover first"))
            box.addWidget(order)
        if opt.comicinfo_xml is not None:
            box.addWidget(QLabel("ComicInfo.xml included · Final page count and page references updated"))
        destination = QLabel(f"Destination: {self.plan.scan.output_folder or self.plan.scan.folder}")
        destination.setWordWrap(True)
        destination.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.addWidget(destination)
        box.addStretch()
        controls = QHBoxLayout()
        back = QPushButton("Back to Settings")
        back.clicked.connect(dialog.reject)
        create = QPushButton(self._queue_action_text() if queue else "Create Comic")
        create.setDefault(True)
        ui_primary(create)
        create.clicked.connect(dialog.accept)
        controls.addWidget(back)
        controls.addStretch()
        if queue and self.batch_import_active and not self.pending_imports:
            review = QPushButton("Review batch…")
            def review_batch():
                self.queue_followup = "review"
                dialog.accept()
            review.clicked.connect(review_batch)
            controls.addWidget(review)
        controls.addWidget(create)
        box.addLayout(controls)
        accepted = dialog.exec() == QDialog.Accepted
        dialog.deleteLater()
        return accepted

    def _preflight_export(self, opt: ExportOptions) -> list[Path]:
        self.export_name_notice = ""
        planned_names(self.plan, opt, reduced=opt.size_output == "reduced")
        if opt.size_output == "both":
            planned_names(self.plan, opt, reduced=True)
        if not opt.cbz and not opt.keep_images:
            raise ValueError("Choose an archive, processed images, or both.")
        check_sources(self.plan.scan)
        if opt.comicinfo_xml is not None:
            parse_xml(opt.comicinfo_xml)
        outputs = queued_outputs(self.plan, opt)
        reserved = {str(path).casefold() for job in self.queue_jobs
                    for path in queued_outputs(job.plan, job.options)}
        folder = self.plan.scan.output_folder or self.plan.scan.folder
        existing = {str(path).casefold() for path in folder.iterdir()}
        def conflicts(paths):
            return any(str(path).casefold() in reserved or str(path).casefold() in existing
                       for path in paths)
        if conflicts(outputs):
            base = safe_book_name(opt.book_name)
            index = 1
            while True:
                candidate = f"{base} - New export" + (f" {index}" if index > 1 else "")
                proposed = replace(opt, book_name=candidate)
                outputs = queued_outputs(self.plan, proposed)
                if not conflicts(outputs):
                    break
                index += 1
            opt.book_name = candidate
            self.book_name.setText(candidate)
            self.export_name_notice = (f'An output named “{base}” already exists or is queued. '
                                       f'This export will use “{candidate}” instead.')
        return outputs

    def _remember_export_preferences(self, opt: ExportOptions) -> None:
        self._save_output_preferences()
        self.settings.setValue("naming", self.naming.currentIndex())
        self.settings.setValue("custom_page_prefix", self.custom_page_prefix.text())
        self.settings.setValue("start_number", opt.start)
        self.settings.setValue("number_padding", opt.padding)
        self.settings.setValue("image_handling", self.handling.currentIndex())
        self.settings.setValue("keep_images", self.keep_images.isChecked())
        if self.plan is not None and self.plan.scan.right_to_left:
            self.settings.setValue("front_to_back", opt.front_to_back)
        self.settings.sync()

    def start_export(self):
        opt = self.options_from_screen()
        try:
            self._preflight_export(opt)
        except Exception as exc:
            QMessageBox.warning(self, "Cannot export", str(exc))
            return
        if not self.confirm_export(opt):
            return
        self._remember_export_preferences(opt)
        self._cancel_size_estimate("")
        self.size_worker.release()  # Export needs the processor.
        self.progress_label.setText("Creating comic…")
        self.progress_bar.setValue(0)
        self.done_button.hide()
        self.repeat_button.hide()
        self.pages.setCurrentWidget(self.progress_page)
        self._compact_window()
        self._run_worker(export_book, (self.plan, opt), self.export_done)

    def add_to_queue(self):
        self.queue_followup = "start"
        opt = replace(self.options_from_screen())
        try:
            self._preflight_export(opt)
        except Exception as exc:
            QMessageBox.warning(self, "Cannot queue comic", str(exc))
            return
        if not self.confirm_export(opt, queue=True):
            return
        self._remember_export_preferences(opt)
        self._save_review_report()
        job = QueuedComic(copy.deepcopy(self.plan), opt, self.session_source,
                          self.import_work, self.screenshot_stage_dir)
        source = self.plan.scan.source_archive
        if self.import_work is not None and source is not None and not self.keep_queue_pages.isChecked():
            stat = source.stat()
            job.reimport_source = source
            job.source_stat = (stat.st_size, stat.st_mtime_ns)
            job.pdf_options = dict(self.pdf_session_options)
            self.import_work.cleanup()
            job.import_work = None
        self.queue_jobs.append(job)
        self.import_work = None  # Retained imports are owned by the queued job.
        diagnostics.note("Comic added to queue: %s", opt.book_name)
        self.new_comic()
        self._refresh_queue_status()
        if self.pending_imports:
            QTimer.singleShot(0, self._open_next_pending)
        elif self.batch_import_active:
            self.batch_import_active = False
            if self.queue_followup == "review":
                QTimer.singleShot(0, lambda: self.show_queue(batch_review=True))
            else:
                QTimer.singleShot(0, self.start_queue)

    def _refresh_queue_status(self):
        count = len(self.queue_jobs)
        waiting = len(self.pending_imports)
        text = (f"{count} comic{'s' if count != 1 else ''} ready to export"
                if count else "Queue empty")
        if waiting:
            text += f" · {waiting} waiting for review"
        self.queue_status.setText(text)
        self.queue_view_button.setEnabled(bool(count))
        self.queue_start_button.setEnabled(bool(count) and self.active_queue_job is None and
                                           not (self.worker and self.worker.isRunning()))
        self.next_import_button.setVisible(bool(waiting))

    def show_queue(self, *, batch_review=False):
        dialog = OutlinedDialog(self)
        dialog.setWindowTitle("Review batch" if batch_review else "Comics ready to export")
        dialog.resize(760, 480)
        dialog.setStyleSheet("QListWidget { font-size:14px; } "
                            "QListWidget::item { padding:10px; border-bottom:1px solid #d8e0e8; } "
                            "QListWidget::item:selected { color:#102a43; background:#dceeff; } "
                            "QPushButton { padding:7px 12px; }")
        layout = QVBoxLayout(dialog)
        note = QLabel("Output files and folders will be saved alongside each comic's originals. "
                      "Removing an item leaves its source untouched.")
        note.setWordWrap(True)
        layout.addWidget(note)
        listing = QListWidget()
        listing.setWordWrap(True)
        listing.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(listing)
        controls = QHBoxLayout()
        remove = QPushButton("Remove selected")
        start = QPushButton("Start export")
        action = {"next": None}
        def refresh():
            listing.clear()
            for job in self.queue_jobs:
                opt = job.options
                kinds = ("full", "reduced") if opt.size_output == "both" else (opt.size_output,)
                copies = []
                for kind in kinds:
                    encoding = self._format_summary(copy_encoding_options(opt, kind == "reduced"),
                                                    original=kind == "full" and opt.keep_originals)
                    copies.append(f"{kind.title()}: {encoding}")
                outputs = queued_outputs(job.plan, opt)
                destinations = "\n".join(path.name for path in outputs)
                item = QListWidgetItem(f"{opt.book_name}\n" + " · ".join(copies) + "\n" + destinations)
                item.setToolTip("\n".join(str(path) for path in outputs))
                listing.addItem(item)
            remove.setEnabled(False)
            start.setEnabled(bool(self.queue_jobs))
        listing.currentRowChanged.connect(lambda index: remove.setEnabled(index >= 0))
        def remove_selected():
            index = listing.currentRow()
            if index < 0:
                return
            job = self.queue_jobs.pop(index)
            if job.import_work is not None:
                job.import_work.cleanup()
            refresh()
            self._refresh_queue_status()
        remove.clicked.connect(remove_selected)
        controls.addWidget(remove)
        def finish(next_action):
            action["next"] = next_action
            dialog.accept()
        files = QPushButton("Add comic files…")
        files.clicked.connect(lambda: finish("files"))
        controls.addWidget(files)
        folder = QPushButton("Add folder…")
        folder.clicked.connect(lambda: finish("folder"))
        controls.addWidget(folder)
        controls.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(dialog.reject)
        controls.addWidget(close)
        start.clicked.connect(lambda: finish("start"))
        ui_primary(start)
        controls.addWidget(start)
        layout.addLayout(controls)
        refresh()
        dialog.exec()
        dialog.deleteLater()
        if action["next"] == "start":
            self.start_queue()
        elif action["next"] in ("files", "folder"):
            self._add_batch_items(folder=action["next"] == "folder")

    def _add_batch_items(self, *, folder=False):
        if folder:
            selected = QFileDialog.getExistingDirectory(self, "Add comic folder or batch folder")
            paths = [Path(selected)] if selected else []
        else:
            selected, _ = QFileDialog.getOpenFileNames(
                self, "Add comic files", "",
                "Comic files (*.cbz *.cbr *.cb7 *.zip *.rar *.7z *.pdf)")
            paths = [Path(path) for path in selected]
        if paths:
            self.batch_import_active = True
            self._import_paths(paths)

    def start_queue(self):
        if (not self.queue_jobs or self.active_queue_job is not None or
                self.worker and self.worker.isRunning()):
            return
        self.queue_completed = 0
        self.queue_results.clear()
        self.done_button.hide()
        self.repeat_button.hide()
        self.pages.setCurrentWidget(self.progress_page)
        self._compact_window()
        self._start_next_queued_export()

    def _start_next_queued_export(self):
        if not self.queue_jobs:
            self.active_queue_job = None
            self.progress_bar.setValue(100)
            paths = [path.name for path in self.queue_results]
            visible = paths[:5]
            extra = f"\n…and {len(paths) - 5} more outputs" if len(paths) > 5 else ""
            self.progress_label.setText(
                f"Queue finished: {self.queue_completed} comics created.\n" +
                "\n".join(visible) + extra)
            self.done_button.show()
            self._compact_window()
            self._refresh_queue_status()
            return
        job = self.queue_jobs[0]
        self.active_queue_job = job
        self._refresh_queue_status()
        self.progress_bar.setValue(0)
        self.progress_label.setText(
            f"Creating comic {self.queue_completed + 1} of "
            f"{self.queue_completed + len(self.queue_jobs)}: {job.options.book_name}…")
        self._run_worker(export_queued_comic, (job,), self._queued_export_done,
                         progress_handler=self._queued_export_progress,
                         failure_handler=self._queued_export_failed)

    def _queued_export_progress(self, done, total):
        self.progress_bar.setValue(round(100 * done / max(1, total)))
        job = self.active_queue_job
        if job is not None:
            phase = "Re-importing and checking pages" if job.reimport_source and done < 50 else "Creating comic"
            self.progress_label.setText(f"{phase}: {job.options.book_name}…")

    def _completed_export_cleanup(self, source=None, import_work=None, screenshot_stage=None):
        actions = []
        if source:
            actions.append(("saved review", lambda: clear_session(source, diagnostics.log_path().parent.parent)))
        if import_work is not None:
            actions.append(("imported pages", import_work.cleanup))
        if screenshot_stage is not None:
            actions.append(("cropped pages", lambda: shutil.rmtree(screenshot_stage)))
        for label, cleanup in actions:
            try:
                cleanup()
            except Exception:
                diagnostics.exception(f"Comic was created, but could not clean up {label}")

    def _queued_export_done(self, destination):
        job = self.queue_jobs.pop(0)
        self._completed_export_cleanup(job.session_source, job.import_work, job.screenshot_stage_dir)
        diagnostics.note("Queued comic created: %s", destination)
        self.queue_results.extend(queued_outputs(job.plan, job.options))
        self.queue_completed += 1
        self.active_queue_job = None
        QTimer.singleShot(0, self._queue_advance_when_ready)

    def _queue_advance_when_ready(self):
        # Worker.result is emitted before QThread has necessarily finished.
        # Keep the prior QThread alive until its run() fully exits.
        if self.worker and self.worker.isRunning():
            QTimer.singleShot(20, self._queue_advance_when_ready)
        else:
            self._start_next_queued_export()

    def _queued_export_failed(self, message):
        diagnostics.note("Queued export stopped: %s", message)
        QMessageBox.warning(self, "Queue stopped",
                            f"Could not create {self.active_queue_job.options.book_name}: {message}\n\n"
                            "This comic and the remaining jobs are still in the queue. "
                            "Correct the issue and start the queue again.")
        self.active_queue_job = None
        self.pages.setCurrentIndex(0)
        self._compact_window()
        QTimer.singleShot(0, self._queue_refresh_when_ready)

    def _queue_refresh_when_ready(self):
        if self.worker and self.worker.isRunning():
            QTimer.singleShot(20, self._queue_refresh_when_ready)
        else:
            self._refresh_queue_status()

    def export_done(self, destination):
        self.preview_checkpoint_timer.stop()
        self._completed_export_cleanup(self.session_source)
        if self.replay_count == 0:
            self.replay_base_name = self.book_name.text()
        self.replay_count += 1
        outputs = list(destination if isinstance(destination, tuple) else (destination,))
        if self.plan is not None:
            for path in queued_outputs(self.plan, self.opt):
                if path.is_dir() and path not in outputs:
                    outputs.append(path)
        self.progress_label.setText("Created:\n" + "\n".join(path.name for path in outputs))
        self.progress_source.clear()
        self.done_button.show()
        self.repeat_button.show()
        self._compact_window()

    def repeat_export(self):
        """Keep the reviewed book and settings; suggest an unused test name."""
        if self.plan is None:
            return
        folder = self.plan.scan.output_folder or self.plan.scan.folder
        base = safe_book_name(self.replay_base_name or self.book_name.text())
        index = self.replay_count + 1
        while True:
            candidate = f"{base} - Test {index}"
            names = (f"{candidate}{suffix}{ext}" for suffix in ("", " - Full", " - Reduced")
                     for ext in (".cbz", ".cb7", " - PNG pages"))
            if not any((folder / name).exists() for name in names):
                break
            index += 1
        self.book_name.setText(candidate)
        self.show_export()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Please wait", "The current scan or export must finish before closing.")
            event.ignore()
        elif self.queue_jobs and QMessageBox.question(
                self, "Discard queued exports?",
                f"{len(self.queue_jobs)} reviewed comic(s) are waiting to export. "
                "Closing will discard the queue and its export settings. "
                "Saved review decisions can still be resumed by opening each source again.\n\n"
                "Close anyway?", QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) != QMessageBox.Yes:
            event.ignore()
        else:
            self.preview_checkpoint_timer.stop()
            if self.replay_count == 0:
                self._save_preview_position()
            self.thumb_worker.stop()
            self.review_worker.stop()
            self.size_worker.stop()
            if (not self.thumb_worker.wait(3000) or not self.review_worker.wait(3000)
                    or not self.size_worker.wait(10000)):
                event.ignore()
                self.statusBar().showMessage("Finishing an image before closing…")
                return
            if self.import_work is not None:
                self.import_work.cleanup()
                self.import_work = None
            for job in self.queue_jobs:
                if job.import_work is not None:
                    job.import_work.cleanup()
            self.queue_jobs.clear()
            if self.screenshot_stage_dir is not None and self.replay_count > 0:
                shutil.rmtree(self.screenshot_stage_dir, ignore_errors=True)
            super().closeEvent(event)

def main():
    prefer_xwayland_on_gnome()  # GNOME on Wayland: GNOME's own frame, shadow and centring
    app = QApplication(sys.argv)
    if sys.platform.startswith("linux"):
        # Matches windows to the app-menu entry linux/install.sh creates (Wayland app id).
        QApplication.setDesktopFileName("comic-stitcher")
    app.setStyle("Fusion")
    broker = LaunchBroker()
    try:
        if not broker.start([Path(argument) for argument in sys.argv[1:]]):
            return 0
        window = ComicWindow()
        broker.paths_received.connect(window._external_import)
        window.show()
        return app.exec()
    finally:
        broker.close()
