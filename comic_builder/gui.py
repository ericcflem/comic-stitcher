"""PySide6 desktop interface for Comic Stitcher."""
from __future__ import annotations

import sys
import os
import shutil
import tempfile
import copy
import json
from functools import partial
from dataclasses import replace
from collections import OrderedDict, deque
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps
from PySide6.QtCore import QEvent, QSettings, QSize, Qt, QThread, QTimer, QUrl, QSignalBlocker
from PySide6.QtGui import QCursor, QFontMetrics, QDesktopServices, QPixmap, QShortcut, QKeySequence, QIcon
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFileDialog,
    QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidgetItem, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QStackedWidget, QVBoxLayout,
    QWidget, QGroupBox)

from .backend import (keeps_png, BookPlan, BoundaryOverrideRequired, ExportOptions, check_sources,
    export_book, planned_names, safe_book_name, scan_folder, scan_manual_folder, rotated_image,
    encoding_workers, POSSIBLE_MIN_SCORE, copy_encoding_options, lossless_alternative,
    LOSSLESS_HINT_SHARE, LOSSLESS_CHECK_SKIP_SHARE, offered)
from . import __version__, diagnostics
from .import_sources import expand_sources
from .startup_notice import finish_startup, take_startup_id
from .single_instance import LaunchBroker
from .colour import standard_mode
from .detector import collect_images
from .archive_import import ARCHIVE_EXTS, import_archive
from .pdf_import import import_pdf, pdf_workers
from .pdf_jpegs import check_pdf_jpegs
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
from .crop_summary import crop_summary
from .screenshot_geometry import family_box, holds_art, own_size
from .ui.common import (fit_height, ui_heading, ui_app_style, ui_primary, ProgressLabel,
    CurrentPageStack, tooltip_text, form_hint, interface_icon, set_review_icon, review_control,
    qimage_from_pixels, page_pixmap, spread_pixmap, FitImage, readable_size,
    DropMarker)
from .ui.workers import Worker, SizeEstimateWorker, LosslessCheckWorker, ReviewImageWorker, ThumbnailWorker
from .ui.overlap import OverlapDialog, JoinedPairView
from .ui.output_options import ARCHIVE_CHOICES, pdf_page_options, OutputOptionsDialog
from .ui.pdf_dialog import PdfImportDialog
from .ui.crop_review import ScreenshotCropDialog
from .ui.import_dialogs import ImportDropZone, REVIEW_MODES, NO_PAIR_REVIEW, ImportChoiceDialog
from .ui.thumbnails import (DRAG_SCROLL_ZONE, DRAG_SCROLL_MIN, DRAG_SCROLL_MAX,
    DRAG_SCROLL_INTERVAL, ThumbnailStrip, PageThumbnail, QueueList)
from .ui.queue import QueuedComic, export_queued_comic, queued_outputs


EXPORT_HINT = "This comic is in the queue with these settings; any change you make here updates it."


# The start screen's size, and the progress screen's width (its height fits its contents).
START_SIZE = QSize(640, 420)
PROGRESS_WIDTH = 580


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
        self.resize(START_SIZE)
        self.setAcceptDrops(True)
        self.settings = QSettings("ComicStitcher", "Comic Stitcher")
        self._merge_worker_settings()
        self.comicvine_session_key = retrieve_key(self.settings.value("comicvine_key_protected", ""))
        self.comicvine_client = ComicVineClient(self.comicvine_session_key)
        self.plan: BookPlan | None = None
        self.import_work: tempfile.TemporaryDirectory | None = None
        self.batch_import_active = False
        self.queue_jobs: list[QueuedComic] = []
        # The comic on the Create your comic screen, already in queue_jobs;
        # editing_queued when it was reopened from the queue (pairs fixed).
        self.current_job: QueuedComic | None = None
        self.editing_queued = False
        self.import_prompt_active = False
        self.pdf_jpeg_reason = None  # Why the last PDF checked can't give up its original JPEGs.
        self.pending_imports: deque[Path] = deque()
        self.active_queue_job: QueuedComic | None = None
        self.queue_completed = 0
        self.queue_results: list[Path] = []
        self.screenshot_source: Path | None = None
        self.screenshot_flagged_pages: set[int] = set()
        self.page_notes: dict[int, list[str]] = {}  # Page index: notes from its file name.
        self.inferred_notice_shown = False
        self.screenshot_stage_dir: Path | None = None
        self.possible_review_started = False
        self.active_manual_mode = False
        self.skip_pair_review = False
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
                              ("webp_sharp_yuv", False), ("png_compress", 6)):
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
        # The usual output options are saved; PDF's own are kept only until the app closes.
        self.usual_opt, self.usual_resize_values = self.opt, self.custom_resize_values
        self.pdf_opt = self.pdf_resize_values = None
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
        cmyk_credit = QLabel('CMYK profile for untagged pages: CGATS TR 001 from Clinton Ingram\'s '
                             '<a href="https://github.com/saucecontrol/Compact-ICC-Profiles">Compact ICC '
                             'Profiles</a> (public domain, CC0).')
        cmyk_credit.setOpenExternalLinks(True)
        cmyk_credit.setWordWrap(True)
        layout.addWidget(cmyk_credit)
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
        layout = QVBoxLayout(dialog)
        layout.addWidget(ui_heading("Processing resources"))
        hint = QLabel("More workers can finish sooner, but use more memory and leave less capacity for other programs.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        workers = QSpinBox(dialog)
        workers.setObjectName("workers")
        workers.setRange(0, max(1, min(16, os.cpu_count() or 1)))
        workers.setSpecialValueText(f"Automatic ({encoding_workers()})")
        workers.setValue(min(workers.maximum(), self.worker_count()))
        form.addRow("Worker processes", workers)
        form_hint(form, workers,
                  "How many pages are worked on at the same time when scanning, rendering PDF pages, "
                  "measuring and cropping screenshots, and encoding. Automatic uses about half the "
                  f"logical processors, up to six ({encoding_workers()} on this computer); PDF rendering "
                  f"uses up to four ({pdf_workers()} here), as rendered pages take more memory. Set a lower "
                  "count for a more responsive computer, or a higher count for faster processing when "
                  "memory allows.")
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
        fit_height(dialog, 540)
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
            self.settings.setValue("workers", workers.value())
            self.settings.setValue("hide_source_notice", not notice.isChecked())
            self.refresh_export()

    def _merge_worker_settings(self):
        """Earlier versions kept three worker counts (PDF rendering, screenshots,
        encoding); one now covers every job, starting from the encoding count."""
        old = ("encoder_workers", "pdf_workers", "screenshot_workers")
        if not self.settings.contains("workers"):
            self.settings.setValue("workers", self.settings.value("encoder_workers", 0, type=int))
        for key in old:
            self.settings.remove(key)

    def worker_count(self) -> int:
        """The Worker processes setting; 0 is Automatic."""
        return max(0, self.settings.value("workers", 0, type=int))

    def _compact_target_size(self):
        progress = hasattr(self, "progress_page") and self.pages.currentWidget() is self.progress_page
        finished = progress and not self.done_button.isHidden()
        if progress:
            # Just as large as the messages, bar and any buttons need.
            minimum = self._progress_minimum()
            size = QSize(max(PROGRESS_WIDTH, minimum.width()), minimum.height())
        elif hasattr(self, "start_page") and self.pages.currentWidget() is self.start_page:
            size = START_SIZE.expandedTo(self.start_page.minimumSizeHint())
        else:
            size = self.compact_size
        if hasattr(self, "export_page") and self.pages.currentWidget() is self.export_page:
            size = QSize(max(800, self.export_page.minimumSizeHint().width()), 560)
            margins = self.export_page.layout().contentsMargins()
            self._fit_export_settings(size.width() - margins.left() - margins.right())
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
        self._hold_page_minimum()  # Before resizing, so the old screen's minimum is gone.
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
                            ("B", lambda: self._review_key("boss")),
                            ("M", lambda: self._review_key("marked"))):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.activated.connect(action)
            self.shortcuts[key] = shortcut

    def _update_shortcuts(self, *_):
        current = self.pages.currentWidget()
        for key, shortcut in self.shortcuts.items():
            shortcut.setEnabled(current == self.review or
                                (current == self.preview and key in ("Left", "Right", "Space", "Escape", "M")) or
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
            if action == "marked":
                self.next_marked_page()
                return
        if screen == self.preview and action in ("left", "right"):
            self.navigate((1 if action == "left" else -1) if self.plan.scan.right_to_left
                          else (-1 if action == "left" else 1))
            return
        if screen != self.review:
            return
        if action == "seam":
            self.toggle_seam()
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
        # The title bar names the program; the settings gear sits with the queue buttons.
        gear = QPushButton("⚙")
        gear.setFlat(True)
        gear.setFixedSize(40, 40)
        gear.setIcon(interface_icon("settings"))
        if not gear.icon().isNull():
            gear.setText("")
            gear.setIconSize(QSize(30, 30))
        gear.setStyleSheet("QPushButton { font-size:26px; } QToolTip { font-size:12px; }")
        gear.setToolTip("Settings")
        gear.setAccessibleName("Settings")
        gear.clicked.connect(self.show_settings)
        self.settings_button = gear
        # Retain these state controls for the later workflow, but choose them
        # in the confirmation dialog instead of crowding the opening screen.
        self.review_mode = QComboBox(start)
        self.review_mode.addItems(REVIEW_MODES)
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
        queue_row.addWidget(gear)
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
        self.start_page = start
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
        box.addWidget(self.preview_view, 1)
        self.thumb_area = QScrollArea()
        self.thumb_area.setWidgetResizable(False)
        self.thumb_area.setFixedHeight(154)
        self.thumb_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.thumb_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.thumb_content = ThumbnailStrip(self)
        self.page_drop_marker = DropMarker(self.thumb_content)
        self.page_drop_boundary = None
        # Holding a dragged page near either end of the strip keeps scrolling it.
        self.drag_scroll = None  # (dragged thumbnail, x in the visible strip)
        self.drag_scroll_timer = QTimer(self)
        self.drag_scroll_timer.setInterval(DRAG_SCROLL_INTERVAL)
        self.drag_scroll_timer.timeout.connect(self._drag_scroll_tick)
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
        self.preview_seam_button.setToolTip(tooltip_text(
            "Two selected single pages: inspect the seam they would have as a spread (Space). "
            "A spread: line up its halves where both repeat a strip of art at the seam."))
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
            "QLineEdit, QSpinBox { min-height:24px; } "
            "QScrollArea#exportSettings, QWidget#exportSettingsViewport, QWidget#exportSettingsContent "
            "{ background:transparent; }")
        page_box = QVBoxLayout(self.export_page)
        page_box.setContentsMargins(20, 16, 20, 16)
        page_box.setSpacing(12)
        page_box.addWidget(ui_heading("Create your comic", 28))
        # The Book and Output sections sit in their own area above the footer (hint
        # and buttons), so a short window scrolls them instead of overlapping.
        self.export_scroll = QScrollArea()
        self.export_scroll.setObjectName("exportSettings")
        self.export_scroll.setWidgetResizable(True)
        self.export_scroll.setFrameShape(QFrame.NoFrame)
        self.export_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.export_scroll.viewport().setObjectName("exportSettingsViewport")
        settings_content = QWidget()
        settings_content.setObjectName("exportSettingsContent")
        box = QVBoxLayout(settings_content)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(12)
        self.export_scroll.setWidget(settings_content)
        self.export_scroll.viewport().installEventFilter(self)
        page_box.addWidget(self.export_scroll, 1)
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
        self.archive_format.addItems(["CBZ (ZIP)", "CB7 (7-Zip)", "PDF", "No archive"])
        saved_archive = str(self.settings.value("archive_format", "cbz"))
        if saved_archive not in ARCHIVE_CHOICES or saved_archive == "pdf":
            saved_archive = "cbz"  # PDF is never the remembered choice.
        self.archive_format.setCurrentIndex(ARCHIVE_CHOICES.index(saved_archive))
        self.archive_previous = saved_archive
        self.archive_format.currentIndexChanged.connect(self._archive_changed)
        form.addRow("Archive format", self.archive_format)
        form_hint(form, self.archive_format,
                  "CBZ packages images as a ZIP archive; CB7 uses a 7z container; PDF puts one image "
                  "on each page. All three store the already-encoded images in reading order without "
                  "changing their quality. A PDF holds JPEG and PNG pages, so choosing it asks which to "
                  "use; it is kept until Comic Stitcher closes, then your usual archive format returns. "
                  "Choose No archive to save the finished pages in a folder instead, in the "
                  "format you chose (one folder for each size).")
        for index, description in enumerate(("Most widely supported comic archive; images stored inside ZIP.",
                                              "7z comic archive; verify that your comic reader supports CB7.",
                                              "A PDF with one page per image, in JPEG or PNG. "
                                              "ComicInfo.xml has no place in a PDF.",
                                              "No archive: save the finished pages in a folder, in the chosen format.")):
            self.archive_format.setItemData(index, description, Qt.ToolTipRole)
        self.keep_images = QCheckBox("Also keep lossless PNG pages")
        self.keep_images.setChecked(self.settings.value("keep_images", False, type=bool))
        self.keep_images.stateChanged.connect(self.refresh_export)
        form.addRow("", self.keep_images)
        self.front_to_back = QCheckBox("Save comic in front-to-back page order")
        self.front_to_back.setChecked(self.settings.value("front_to_back", False, type=bool))
        self.front_to_back.setToolTip(tooltip_text(
            "Checked: the finished comic and saved PNG pages start with the front cover. "
            "Unchecked: keep back-to-front order, with the back cover first. "
            "Joined spreads stay intact and keep their approved orientation. The original PDF is not changed."))
        self.front_to_back.hide()
        self.front_to_back.toggled.connect(self.refresh_export)
        form.addRow("", self.front_to_back)
        self._archive_changed()
        form_hint(form, self.keep_images,
                  "Also save lossless PNG copies of the final pages, after joining, rotation, and any resizing "
                  "but before JPEG/WebP encoding, in a folder beside the comic. Both sizes saves separate Full and "
                  "Reduced PNG folders. PNG cannot recover detail already lost in a JPEG source. Unavailable when "
                  "there's no archive and the pages are already being saved as PNG.")
        self.keep_images_hint = self.keep_images.toolTip()
        self.save_cover = QCheckBox("Also save cover image")
        self.save_cover.setChecked(self.settings.value("save_cover", False, type=bool))
        self.save_cover.toggled.connect(self.refresh_export)
        form.addRow("", self.save_cover)
        form_hint(form, self.save_cover,
                  "Also save the front cover, exactly as it is in the comic, as an image beside it "
                  "(Book - Cover.jpg). Both sizes saves a cover for each.")
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
        self.save_comicinfo = QCheckBox("Also save ComicInfo.xml")
        self.save_comicinfo.setChecked(self.settings.value("save_comicinfo", False, type=bool))
        self.save_comicinfo.toggled.connect(self.refresh_export)
        form.addRow("", self.save_comicinfo)
        self.save_comicinfo_hint = tooltip_text(
            "Also save the comic's ComicInfo.xml as a file beside it (Book - ComicInfo.xml), as well as "
            "inside the archive. Beside a PDF, which has no place for it, it's the only copy.")
        self.save_comicinfo.setToolTip(self.save_comicinfo_hint)
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
        # Shown when lossless WebP would make the comic far smaller (clean line art).
        self.lossless_hint_row = QWidget()
        lossless_row = QHBoxLayout(self.lossless_hint_row)
        lossless_row.setContentsMargins(0, 0, 0, 0)
        self.lossless_hint = QLabel("")
        self.lossless_hint.setWordWrap(True)
        self.lossless_hint.setStyleSheet("color:#1b5e20; padding-left:6px;")
        lossless_row.addWidget(self.lossless_hint, 1)
        self.lossless_button = QPushButton("Use lossless WebP")
        self.lossless_button.setToolTip(tooltip_text(
            "Saves the pages Comic Stitcher encodes as lossless WebP: every pixel is kept exactly, "
            "and clean line art such as black-and-white strips is often several times smaller "
            "than JPEG. For this comic only: your usual output settings aren't changed, and the "
            "next comic starts with them again."))
        self.lossless_button.clicked.connect(self._use_lossless_webp)
        lossless_row.addWidget(self.lossless_button)
        self.lossless_hint_row.hide()
        output_box.addWidget(self.lossless_hint_row)
        self.estimate_full_size = None
        self.lossless_for_this_comic = False  # Lossless WebP from the suggestion: never saved.
        self.lossless_field = "format"
        self.lossless_checks = []  # Running quick checks (kept until finished).
        self.lossless_checked = (None, None)  # (plan, share) from the latest quick check.
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
        self.size_worker.lossless.connect(self._lossless_estimate_ready)
        self.pages.currentChanged.connect(self._schedule_size_estimate)
        self.pages.currentChanged.connect(self._hold_page_minimum)
        form.setVerticalSpacing(10)
        output_box.addLayout(form)
        box.addWidget(output_group)
        self.export_hint = QLabel(EXPORT_HINT)
        self.export_hint.setWordWrap(True)
        self.keep_queue_pages = QCheckBox("Keep imported pages for faster queued export")
        self.keep_queue_pages.setToolTip(tooltip_text(
            "Unchecked: clear temporary PDF/archive pages after adding to the queue and import them again "
            "when exporting. This saves disk space. Checked: retain imported pages until export; faster, "
            "but a large queue can use substantial disk space."))
        self.keep_queue_pages.hide()
        box.addStretch()
        controls = QHBoxLayout()
        self.export_back_button = QPushButton("Back to Comic Preview")
        self.export_back_button.clicked.connect(self.return_preview_from_export)
        # This comic is already in the queue; the blue button moves on.
        self.create_button = QPushButton("Start export")
        ui_primary(self.create_button)
        self.create_button.clicked.connect(self.export_screen_continue)
        self.export_queue_button = QPushButton("View queue…")
        self.export_queue_button.setToolTip(tooltip_text(
            "See the queued comics, this one included: reorder them, remove one, add another comic, "
            "or double-click one to change its settings."))
        self.export_queue_button.clicked.connect(lambda: self.show_queue(reviewing=True))
        self.export_queue_button.hide()
        controls.addWidget(self.export_back_button)
        controls.addStretch()
        controls.addWidget(self.export_queue_button)
        controls.addWidget(self.create_button)
        page_box.addWidget(self.export_hint)
        page_box.addLayout(controls)
        self.pages.addWidget(self.export_page)
        self.refresh_export()

    def _build_progress(self):
        self.progress_page = QWidget()
        box = QVBoxLayout(self.progress_page)
        box.addStretch()
        self.progress_source = ProgressLabel()
        self.progress_source.setAlignment(Qt.AlignCenter)
        self.progress_source.setWordWrap(True)
        self.progress_source.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.progress_source.setStyleSheet("color:#617080; font-size:13px;")
        box.addWidget(self.progress_source)
        self.progress_label = ProgressLabel()
        self.progress_label.setAlignment(Qt.AlignCenter)
        self.progress_label.setWordWrap(True)
        self.progress_label.setStyleSheet("font-size:19px; font-weight:700; color:#203c55;")
        self.progress_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        for label in (self.progress_source, self.progress_label):
            label.text_changed.connect(self._progress_text_changed)
        self.progress_page.installEventFilter(self)
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
        # Its messages change length as work moves on; re-measure them as they do.
        self.progress_page.installEventFilter(self)
        self.pages.addWidget(self.progress_page)

    def _show_progress_page(self):
        """Show the progress screen for new work. The buttons from a finished
        export (Process another comic, Export this comic again) belong to that
        export only; left showing, they would leave this work running unseen."""
        self.done_button.hide()
        self.repeat_button.hide()
        self.pages.setCurrentWidget(self.progress_page)

    def new_comic(self):
        if self.worker and self.worker.isRunning():
            return  # Work is still under way; its own screen follows when it finishes.
        self._end_lossless_for_this_comic()
        self._stop_lossless_checks()
        if self.current_job is not None:  # Leaving a comic that was never settled: drop it.
            if self.current_job in self.queue_jobs:
                self.queue_jobs.remove(self.current_job)
            self.current_job = None
            self.editing_queued = False
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
        self.skip_pair_review = False
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
            "Comic files (*.cbz *.CBZ *.cbr *.CBR *.cb7 *.CB7 *.zip *.ZIP *.rar *.RAR *.7z *.7Z *.pdf *.PDF);;"
            "PDF documents (*.pdf *.PDF);;Comic archives (*.cbz *.CBZ *.cbr *.CBR *.cb7 *.CB7 *.zip *.ZIP *.rar *.RAR *.7z *.7Z)")
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
        self.screenshot_workers_count = self.worker_count()
        clear_session(folder, app_folder)
        shutil.rmtree(self.screenshot_stage_dir, ignore_errors=True)
        self.screenshot_source = folder
        self.screenshot_single_size = None
        self.pending_session = None
        self.session_source = None
        self.progress_label.setText("Measuring screenshots…")
        self.progress_bar.setValue(0)
        self._show_progress_page()
        self._compact_window()
        diagnostics.note("Screenshot analysis started: %s", folder)
        self._run_worker(analyze_screenshots, (folder, self.screenshot_single_size,
                                               self.screenshot_workers_count),
                         self._screenshot_analysis_done,
                         lambda done, total: self.progress_label.setText(
                             f"Measuring screenshots: {done} of {total}…"))

    def screenshot_crop_summary(self, proposals, attention):
        heading, lines = crop_summary(proposals, attention)
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Screenshot measurements complete")
        dialog.setIcon(QMessageBox.NoIcon)  # A report, not a warning; the text says what needs a look.
        dialog.setText(heading)
        dialog.setInformativeText("\n\n".join(lines))
        verify = dialog.addButton("Review crop previews", QMessageBox.ActionRole)
        proceed = dialog.addButton("Review required crops" if attention else "Continue to cropping", QMessageBox.AcceptRole)
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
            book_family = review.book_family
        else:
            book_family = None
        attention_set = set(attention)
        choices = [next(reviewed) if index in attention_set else
                   CropChoice(item.path, item.kind, item.box)
                   for index, item in enumerate(proposals)]
        if book_family is not None:
            # "Use this size for all pages" also covers pages that didn't need review.
            canvas, width = book_family
            for index, item in enumerate(proposals):
                if (index in attention_set or item.canvas != canvas or
                        own_size(item, review.canvas_families.get(canvas, ())) or
                        item.kind not in ("single", "double")):
                    continue
                box = family_box(canvas, item.kind, width)
                if box is not None and box != item.box and holds_art(box, getattr(item, "measured_box", None)):
                    choices[index] = CropChoice(item.path, item.kind, box, True)
        self._stage_approved_screenshots(choices)

    def _stage_approved_screenshots(self, choices):
        destination = self.screenshot_stage_dir / "pages"
        screenshot_count = len(choices)
        page_count = sum(2 if choice.kind == "double" else 1 for choice in choices)
        self.progress_label.setText(f"Cropping screenshots: 0 of {screenshot_count}…")
        self.progress_bar.setValue(0)
        self._run_worker(partial(stage_screenshots, right_to_left=self.active_right_to_left),
                         (choices, destination, self.screenshot_source,
                          self.review_mode.currentIndex() >= 1,
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
            activity = "Reading" if self.review_mode.currentIndex() >= 1 else "Scanning"
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
        self.skip_pair_review = self.review_mode.currentIndex() == NO_PAIR_REVIEW
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
            manual_mode = self.review_mode.currentIndex() >= 1
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
                self.skip_pair_review = False  # The saved decisions are restored instead.
                manual_mode = bool(checkpoint.get("manual_mode", False))
                self.active_right_to_left = bool(checkpoint.get("right_to_left", False))
                self.review_mode.setCurrentIndex(int(manual_mode))
                if folder.suffix.lower() == ".pdf":
                    options = checkpoint.get("pdf_options", {})
                    pdf_dpi = options.get("dpi", 300)
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
        if pdf_workers_count is None:
            pdf_workers_count = self.worker_count()
        self.pdf_session_options = {"dpi": pdf_dpi or 300, "workers": pdf_workers_count,
                                    "smooth_images": pdf_smooth_images is not False, "extract_jpegs": extract_jpegs}
        if folder.is_file() and folder.suffix.lower() == ".pdf" and pdf_dpi is None and not pdf_checked:
            self.progress_label.setText("Checking PDF page images…")
            self.progress_bar.setValue(0)
            self._show_progress_page()
            self._compact_window()
            self._run_worker(check_pdf_jpegs, (folder,),
                             lambda result: self._pdf_images_checked(folder, *result, manual_mode, pdf_smooth_images))
            return
        if folder.is_file() and folder.suffix.lower() == ".pdf" and pdf_dpi is None:
            dialog = PdfImportDialog(folder, int(self.settings.value("pdf_dpi", 300)),
                                     (pdf_smooth_images if pdf_smooth_images is not None else
                                      str(self.settings.value("pdf_smooth_images", "true")).lower() != "false"),
                                     self, manual=manual_mode, right_to_left=self.active_right_to_left,
                                     jpeg_reason=self.pdf_jpeg_reason if pdf_checked else None)
            if dialog.exec() != QDialog.Accepted:
                self.pages.setCurrentIndex(0)
                self._compact_window()
                return
            pdf_dpi = dialog.dpi.value()
            pdf_smooth_images = dialog.smooth_images.isChecked()
            if dialog.dpi_chosen_by_user or dialog.suggested_dpi is None:
                self.settings.setValue("pdf_dpi", pdf_dpi)
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
        self._show_progress_page()
        self._compact_window()
        if folder.is_file() and folder.suffix.lower() in ARCHIVE_EXTS | {".pdf"}:
            self.import_work = tempfile.TemporaryDirectory(prefix="ComicStitcher_import_")
            destination = Path(self.import_work.name) / "pages"
            if folder.suffix.lower() == ".pdf":
                self.progress_label.setText("Extracting original JPEG pages…" if extract_jpegs else
                                            f"Rendering {folder.name} to PNG pages… this may take several minutes.")
                self._run_worker(partial(import_pdf, right_to_left=self.active_right_to_left, extract_jpegs=extract_jpegs), (folder, destination, pdf_dpi or 300,
                                              pdf_workers_count,
                                              True if pdf_smooth_images is None else pdf_smooth_images,
                                              manual_mode),
                                 self.scan_done)
                self.worker.progress.connect(self._pdf_progress)
            else:
                self._run_worker(partial(import_archive, right_to_left=self.active_right_to_left,
                                         workers=self.worker_count()),
                                 (folder, destination, manual_mode), self.scan_done)
        else:
            self._run_worker(partial(scan_manual_folder if manual_mode else scan_folder,
                                     right_to_left=self.active_right_to_left, workers=self.worker_count()),
                             (folder,), self.scan_done)

    def _pdf_images_checked(self, source, report, reason, manual_mode, smooth_images):
        self.pdf_jpeg_reason = reason
        if reason:
            diagnostics.note("Original JPEGs not offered for %s: %s", source.name, reason)
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

    def _show_page_notes(self, notes):
        """Pages worth a look, from their file names: say so once, before review."""
        def pages(indexes):
            numbers = [str(i + 1) for i in indexes]
            return ("Page " + numbers[0] if len(numbers) == 1 else
                    "Pages " + ", ".join(numbers[:-1]) + " and " + numbers[-1])
        for indexes, text in notes:
            diagnostics.note("Page note: %s: %s", pages(indexes), text)
        shown = [f"• {pages(indexes)}: {text}." for indexes, text in notes[:12]]
        if len(notes) > 12:
            shown.append(f"• …and {len(notes) - 12} more.")
        details = "\n".join(shown)
        if any(text.startswith("Names differ only in capitalisation") for _, text in notes):
            details += ("\n\nPages whose names differ only in capitalisation are kept in the order Linux file "
                        "managers show them (lowercase first), so check their order. If one is a duplicate, "
                        "remove it in the final review. If they all belong in the comic, keep them: the "
                        "finished comic numbers every page, so their names won't clash.")
        details += "\n\nIn the final review they're marked with a pink !. Press M there to go from one to the next."
        notice = QMessageBox(QMessageBox.Information, "Pages to check",
                             "One place in this comic is worth a look" if len(notes) == 1 else
                             f"{len(notes)} places in this comic are worth a look", QMessageBox.Ok, self)
        notice.setInformativeText(details)
        notice.setAttribute(Qt.WA_DeleteOnClose)
        notice.open()  # Doesn't hold up the review.
        self.page_notes_notice = notice

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
        if not resumed and self.skip_pair_review:
            # No pair review: every neighboring pair stays unjoined. The final review can still join them.
            self.plan.decisions = {index: False for index in range(len(scan.candidates))}
            diagnostics.note("Pair review skipped: %d pages", len(scan.pages))
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
        self._start_lossless_check()  # Ready, usually, by the time Create your comic opens.
        if self.skip_pair_review and not resumed:
            self.statusBar().showMessage(f"{len(scan.pages)} pages; pair review skipped")
        elif scan.manual_mode:
            self.statusBar().showMessage(f"{len(scan.pages)} pages; manual review of neighboring pairs")
        elif scan.numbering_assumed:
            self.statusBar().showMessage("Page numbers could not be read consistently; using natural filename order.")
        else:
            suggested = sum(offered(c) for c in scan.candidates)
            for c in scan.candidates:
                if c.metrics.get("overlap"):
                    diagnostics.note("Overlapping halves found: pages %d and %d, %d px repeated%s",
                                     c.left + 1, c.right + 1, c.metrics["overlap"],
                                     " (edges also match)" if c.likely or c.continuity >= POSSIBLE_MIN_SCORE else "")
            self.statusBar().showMessage(f"{len(scan.pages)} pages; {suggested} suggested pairs")
        self.page_notes = {}
        for indexes, text in scan.page_notes:
            for index in indexes:
                self.page_notes.setdefault(index, []).append(text)
        if scan.page_notes and not resumed:
            self._show_page_notes(scan.page_notes)
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
        index, c = item
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
        overlap, top = self.plan.pair_layout(index) or (0, "left")
        self.joined_review.load_pair((a.width, a.height), (b.width, b.height), overlap, top)
        self.review_generation += 1
        diagnostics.note("Pair Review requested: %d / %d (request %d)",
                         c.left + 1, c.right + 1, self.review_generation)
        self.review_worker.request(self.review_generation, a.path, b.path)
        done = len(self.plan.decisions)
        label = "Manual" if self.plan.scan.manual_mode else "Likely" if c.likely else "Possible"
        if index in self.plan.proposed_layouts:
            if overlap:
                label += f" · lined up in Seam ({overlap} px, {top} page on top)"
        elif c.metrics.get("overlap"):
            label += f" · overlapping halves ({c.metrics['overlap']} px repeated, shown lined up)"
        elif overlap:
            label += f" · thin border at the seam covered ({overlap} px)"
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

    def _visual_halves(self, first: int, second: int, turns=(0, 0)):
        """Two pages as they sit side by side (right-to-left books swap them), each
        turned as it is in the comic, ready for Line up spread."""
        if self.plan.scan.right_to_left:
            first, second, turns = second, first, tuple(turns)[::-1]
        halves = []
        for index, turn in zip((first, second), turns, strict=True):
            with Image.open(self.plan.scan.pages[index].path) as source:
                page = standard_mode(ImageOps.exif_transpose(source)).convert("RGB")
            halves.append(rotated_image(page, turn))
        return halves

    def toggle_seam(self):
        """Pair review's Seam: the pair's seam as it would be joined, with the tools to line it up."""
        item = self.plan.current() if self.plan is not None else None
        if item is None:
            return
        index, c = item
        try:
            halves = self._visual_halves(c.left, c.right)
        except Exception as exc:
            QMessageBox.critical(self, "Seam View", str(exc))
            return
        layout = self.plan.pair_layout(index) or (0, "left")
        dialog = OverlapDialog(*halves, layout, self, accept_text="Use this alignment", find_on_open=False)
        if dialog.exec() != QDialog.Accepted:
            return
        self.plan.proposed_layouts[index] = dialog.overlap()
        diagnostics.note("Pair review alignment: pages %d and %d, %d px, %s on top",
                         c.left + 1, c.right + 1, *dialog.overlap())
        self.show_candidate()

    def preview_seam(self):
        """Final review's Seam: a spread's seam to line up, or two selected pages' to join."""
        if not self.comparing:
            self.adjust_spread_overlap()
            return
        entries = self.plan.entries()
        # Only two individual adjacent pages have a prospective join seam.
        if any(entries[i][1] is not None for i in self.comparing):
            return
        first, second = (entries[i][0] for i in self.comparing)
        turns = tuple(self.plan.rotations.get(i, 0) for i in (first, second))
        try:
            halves = self._visual_halves(first, second, turns)
        except Exception as exc:
            QMessageBox.warning(self, "Seam View", str(exc))
            return
        joinable = self.join_button.isEnabled()
        dialog = OverlapDialog(*halves, (0, "left"), self, accept_text="Join as spread" if joinable else "Close")
        if dialog.exec() != QDialog.Accepted or not joinable:
            return
        if self.join_selected() and dialog.overlap()[0]:
            pixels, top = dialog.overlap()
            self.plan.set_overlap(first, pixels, top, checkpoint=False)  # Undone with the join.
            self._save_review_report()
            self.rebuild_thumbnails()
            self.show_preview()

    def adjust_spread_overlap(self):
        """Line up the current spread's halves where they repeat art at the seam."""
        if self.plan is None or self.comparing:
            return
        left, right = self.plan.entries()[self.preview_index]
        if right is None:
            return
        try:
            halves = self._visual_halves(left, right, self.plan.joined_rotations.get(left, (0, 0)))
        except Exception as exc:
            QMessageBox.warning(self, "Line up spread", str(exc))
            return
        dialog = OverlapDialog(*halves, self.plan.join_overlaps.get(left, (0, "left")), self,
                               find_on_open=left not in self.plan.join_overlaps)
        if dialog.exec() != QDialog.Accepted:
            return
        pixels, top = dialog.overlap()
        try:
            self.plan.set_overlap(left, pixels, top)
        except ValueError as exc:
            QMessageBox.warning(self, "Line up spread", str(exc))
            return
        diagnostics.note("Spread overlap set: page %d, %d px, %s on top", left, pixels, top)
        self._save_review_report()
        self.rebuild_thumbnails()
        self.show_preview()

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
                            self.plan.rotations.get(a, 0), self.plan.joined_rotations.get(a, (0, 0)),
                            self.plan.join_overlaps.get(a, (0, "right")))
                           for a, b in entries]
        if self.plan.scan.right_to_left:
            self.thumb_keys = [(right, left, rotation, parts[::-1], overlap) if right is not None
                               else (left, right, rotation, parts, overlap)
                               for left, right, rotation, parts, overlap in self.thumb_keys]
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
            notes = self.page_notes.get(a, []) + (self.page_notes.get(b, []) if b is not None else [])
            pix = self.thumb_cache.get(self.thumb_keys[index])
            if pix is not None:
                button.setIcon(pix)
                button.setIconSize(QSize(100, 84))
                button.setText("")
            button.setToolTip(f"Final page {index + 1}" + (" (spread)" if b is not None else "") +
                              (" — check unusual-width screenshot" if marked else "") +
                              "".join(f"\n{note}." for note in dict.fromkeys(notes)) +
                              "\nDrag to the left or right half of a thumbnail; the pink dotted line shows the insertion position. Shift-click a neighboring thumbnail to compare or swap.")
            if marked or notes:
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
        if (watched is getattr(self, "progress_page", None)
                and event.type() in (QEvent.LayoutRequest, QEvent.Resize)):
            QTimer.singleShot(0, self._fit_progress_text)
        if (watched is getattr(self, "progress_page", None) and event.type() == QEvent.Resize
                and event.size().width() != event.oldSize().width()):
            QTimer.singleShot(0, self._progress_text_changed)  # Wrapping changes with width.
        if (getattr(self, "export_scroll", None) is not None and watched is self.export_scroll.viewport()
                and event.type() == QEvent.Resize and event.size().width() != event.oldSize().width()):
            QTimer.singleShot(0, self._fit_export_settings)
        return super().eventFilter(watched, event)

    def _fit_export_settings(self, width=None):
        """Keep the window from shrinking below what Book and Output need.

        Their height depends on the width, as some text wraps. On a screen too
        short for all of it, the area scrolls rather than the window growing
        past the screen.
        """
        content = self.export_scroll.widget()
        # Neither scroll area scrolls sideways, so hold them to their contents'
        # width: dragged narrower, the window would cut the fields off.
        book = self.book_scroll.widget()
        book_width = book.minimumSizeHint().width() + self.book_scroll.verticalScrollBar().sizeHint().width()
        if book_width != self.book_scroll.minimumWidth():
            self.book_scroll.setMinimumWidth(book_width)
        content_width = content.minimumSizeHint().width() + self.export_scroll.verticalScrollBar().sizeHint().width()
        if content_width != self.export_scroll.minimumWidth():
            self.export_scroll.setMinimumWidth(content_width)
        width = width or self.export_scroll.viewport().width()
        if width <= 0:
            return
        layout = content.layout()
        needed = layout.totalHeightForWidth(width) if layout.hasHeightForWidth() else -1
        needed = max(needed, layout.totalMinimumSize().height())
        screen = self.screen() or QApplication.primaryScreen()
        if screen:
            # Leave room for the title bar, heading, hint and buttons.
            others = self.export_page.minimumSizeHint().height() - self.export_scroll.minimumHeight()
            frame = max(0, self.frameGeometry().height() - self.geometry().height())
            needed = min(needed, screen.availableGeometry().height() - others - frame)
        if needed != self.export_scroll.minimumHeight():
            self.export_scroll.setMinimumHeight(max(0, needed))
        # The hint below may wrap onto more lines; count them in the minimum too.
        hint_height = self.export_hint.heightForWidth(width)
        if hint_height > 0 and hint_height != self.export_hint.minimumHeight():
            self.export_hint.setMinimumHeight(hint_height)
        self._hold_page_minimum()

    def _fit_progress_text(self):
        """Count wrapped lines of the progress messages in the window's minimum
        size, so a longer message (such as the scanning step's) is never cut off."""
        page = self.progress_page
        margins = page.layout().contentsMargins()
        width = page.width() - margins.left() - margins.right()
        if width <= 0:
            return
        changed = False
        for label in (self.progress_source, self.progress_label):
            height = label.heightForWidth(width) if label.text() else 0
            if height != label.minimumHeight():
                label.setMinimumHeight(max(0, height))
                changed = True
        if changed and self.pages.currentWidget() is page:
            self._hold_page_minimum()

    PROGRESS_SAMPLE_TEXT = "Scanning 0000 extracted pages… 100% complete"

    def _progress_minimum(self):
        """Wide enough for the usual progress messages on one line, so the window
        needn't change size as the work moves from one step to the next, and tall
        enough for whatever the messages wrap to (a long file name, say)."""
        self.progress_label.ensurePolished()
        margins = self.progress_page.layout().contentsMargins()
        sample = QFontMetrics(self.progress_label.font()).horizontalAdvance(self.PROGRESS_SAMPLE_TEXT)
        width = max(360, sample + 12 + margins.left() + margins.right())
        text_width = max(width, self.progress_page.width()) - margins.left() - margins.right()
        for label in (self.progress_source, self.progress_label):
            height = label.heightForWidth(text_width) if label.text() else 0
            label.setMinimumHeight(max(0, height))
        return self.progress_page.minimumSizeHint().expandedTo(QSize(width, 0))

    def _progress_text_changed(self):
        if self.pages.currentWidget() is self.progress_page:
            self._hold_page_minimum()

    def _hold_page_minimum(self, *_):
        """Give the window the current screen's minimum size.

        The page stack ignores its pages' sizes, so a small screen isn't held to
        the large review screens' size. The compact screens (start, progress and
        export) therefore get their minimum explicitly; the full-screen review
        screens get none.
        """
        if not all(hasattr(self, name) for name in ("start_page", "progress_page", "export_page")):
            return
        page = self.pages.currentWidget()
        if page is self.progress_page:
            minimum = self._progress_minimum()
        elif page in (self.start_page, self.export_page):
            minimum = page.minimumSizeHint()
        else:
            minimum = QSize(0, 0)
        if minimum != self.pages.minimumSize():
            self.pages.setMinimumSize(minimum)
            # The window updates its own minimum later; do it now, or a resize made
            # straight after switching screens is held to the previous screen's size.
            if self.layout() is not None:
                self.layout().activate()

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
                                                               page_rotations=self.plan.joined_rotations.get(first[0], (0, 0)),
                                                               overlap=self.plan.join_overlaps.get(first[0], (0, "right"))))
                    self.compare_image.show()
                    self.compare_image.show_pixmap(page_pixmap(self.plan.scan.pages[second[0]].path,
                                                               rotation=self.plan.rotations.get(second[0], 0))
                                                   if second[1] is None else spread_pixmap(
                                                       self.plan.scan.pages[second[0]].path,
                                                       self.plan.scan.pages[second[1]].path,
                                                       rotation=self.plan.rotations.get(second[0], 0),
                                                       right_to_left=self.plan.scan.right_to_left,
                                                               page_rotations=self.plan.joined_rotations.get(second[0], (0, 0)),
                                                               overlap=self.plan.join_overlaps.get(second[0], (0, "right"))))
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
                                                               page_rotations=self.plan.joined_rotations.get(a, (0, 0)),
                                                               overlap=self.plan.join_overlaps.get(a, (0, "right"))))
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
        # Two single pages: inspect their prospective seam. One spread: line up its halves.
        self.preview_seam_button.setEnabled(bool(self.comparing and
            all(entries[i][1] is None for i in self.comparing)) or (b is not None and self.comparing is None))
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

    def marked_entries(self):
        """Final pages marked with a pink !: unusual-width screenshots and
        pages with notes from their file names."""
        marked = self.screenshot_flagged_pages | set(self.page_notes)
        return [index for index, (a, b) in enumerate(self.plan.entries())
                if a in marked or (b is not None and b in marked)]

    def next_marked_page(self):
        """M in the final review: the next marked page, round to the first."""
        if self.plan is None:
            return
        marked = self.marked_entries()
        if not marked:
            self.statusBar().showMessage("No pages are marked.", 4000)
            return
        target = next((i for i in marked if i > self.preview_index), marked[0])
        self.comparing = None
        self.preview_index = self.selected_first = target
        self.show_preview(follow=True)
        a, b = self.plan.entries()[target]
        notes = self.page_notes.get(a, []) + (self.page_notes.get(b, []) if b is not None else [])
        self.statusBar().showMessage(f"Marked page {marked.index(target) + 1} of {len(marked)}"
                                     + "".join(f" · {note}" for note in dict.fromkeys(notes)), 8000)

    def navigate(self, delta):
        self.comparing = None
        self.preview_index = max(0, min(self.preview_index + delta, len(self.plan.entries()) - 1))
        self.selected_first = self.preview_index
        self.show_preview(follow=True)

    def clear_page_drop(self):
        self.page_drop_boundary = None
        self.page_drop_marker.hide()
        self.drag_scroll = None
        self.drag_scroll_timer.stop()

    def page_drag_left(self):
        """A thumbnail or the strip says a drag left it: passing from one
        thumbnail to the next, or a page scrolling out from under a still
        pointer, isn't leaving the strip."""
        viewport = self.thumb_area.viewport()
        if not viewport.rect().contains(viewport.mapFromGlobal(QCursor.pos())):
            self.clear_page_drop()

    def _drag_scroll_step(self, visible_x):
        """Pixels to scroll the strip per tick for a drag at this point: faster
        the closer to an end, none away from both ends."""
        width = self.thumb_area.viewport().width()
        zone = min(DRAG_SCROLL_ZONE, width // 4)
        if visible_x < zone:
            depth = zone - visible_x
        elif visible_x > width - zone:
            depth = -(visible_x - (width - zone))
        else:
            return 0
        depth = max(-zone, min(zone, depth))
        step = max(DRAG_SCROLL_MIN, round(DRAG_SCROLL_MAX * abs(depth) / max(1, zone)))
        return -step if depth > 0 else step

    def _drag_scroll_tick(self):
        if self.drag_scroll is None:
            self.drag_scroll_timer.stop()
            return
        source, visible_x = self.drag_scroll
        bar = self.thumb_area.horizontalScrollBar()
        before = bar.value()
        bar.setValue(before + self._drag_scroll_step(visible_x))
        if bar.value() == before:
            self.drag_scroll_timer.stop()  # Reached the end of the strip.
            return
        # The pointer hasn't moved, but the pages under it have.
        self.update_page_drop(source, visible_x + bar.value())

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
        visible_x = x - self.thumb_area.horizontalScrollBar().value()
        if self._drag_scroll_step(visible_x):
            self.drag_scroll = (source, visible_x)
            if not self.drag_scroll_timer.isActive():
                self.drag_scroll_timer.start()
        else:
            self.drag_scroll = None
            self.drag_scroll_timer.stop()
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
            return False
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
                return False
            self.plan.join(left, override_boundary=True, right=right)
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot join", str(exc))
            return False
        self.comparing = None
        self._save_review_report()
        self.selected_first = None
        self.rebuild_thumbnails()
        self.show_preview()
        return True

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
        if not self.editing_queued:
            self._save_review_report()
            if self.current_job is None and self.plan is not None:
                # Reaching this screen queues the comic with the settings shown.
                self.current_job = QueuedComic(self.plan, replace(self.options_from_screen()),
                                               self.session_source, None, self.screenshot_stage_dir)
                self.queue_jobs.append(self.current_job)
                diagnostics.note("Comic added to queue: %s", self.current_job.options.book_name)
        self._refresh_queue_status()
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
        pdf = self._archive_choice() == "pdf"
        dialog = OutputOptionsDialog(self.opt, self.custom_resize_values, self,
                                     size_limits=limits if self.plan else None, pdf=pdf)
        if dialog.exec() != QDialog.Accepted:
            return False
        self.custom_resize_values = dialog.apply_to(self.opt)
        if pdf:
            self.pdf_resize_values = self.custom_resize_values
        else:
            self.usual_resize_values = self.custom_resize_values
            self._save_output_preferences()
        self.refresh_export()
        return True

    def _save_output_preferences(self):
        """Save the usual output options; PDF's own are never saved."""
        opt = self.usual_opt
        if getattr(self, "lossless_for_this_comic", False):
            if self._lossless_still_chosen(opt):
                # Chosen for this comic only: keep the saved format choices.
                saved = self._saved_formats()
                opt = replace(opt, **{self.lossless_field: saved[self.lossless_field],
                                      "webp_lossless": saved["webp_lossless"]})
            else:
                self.lossless_for_this_comic = False  # Edit options chose something else.
        self.settings.setValue("reduced_custom", opt.reduced_custom)
        self.settings.setValue("reduced_settings", json.dumps(opt.reduced_settings))
        for key, value in (("jpeg_quality", opt.jpeg_quality),
                           ("webp_quality", opt.webp_quality),
                           ("output_format", opt.format),
                           ("spread_format", opt.spread_format),
                           ("output_sizes", opt.size_output)):
            self.settings.setValue(key, value)
        for name in ("jpeg_progressive", "jpeg_optimize", "jpeg_subsampling", "jpeg_smooth",
                     "webp_lossless", "webp_method", "webp_sharp_yuv", "png_compress"):
            self.settings.setValue(name, getattr(opt, name))
        for key, value in self.usual_resize_values.items():
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

    def _archive_choice(self):
        return ARCHIVE_CHOICES[self.archive_format.currentIndex()]

    def _use_pdf_options(self, pdf: bool):
        """Switch between the usual output options and PDF's own for this session."""
        if pdf:
            if self.pdf_opt is None:
                self.pdf_opt = pdf_page_options(self.usual_opt)
                self.pdf_resize_values = dict(self.usual_resize_values)
            self.opt, self.custom_resize_values = self.pdf_opt, self.pdf_resize_values
        else:
            self.opt, self.custom_resize_values = self.usual_opt, self.usual_resize_values

    def _archive_changed(self, *_):
        choice, previous = self._archive_choice(), getattr(self, "archive_previous", "cbz")
        if choice == "pdf" and previous != "pdf":
            self._use_pdf_options(True)
            # Choosing PDF asks for the page format straight away; Cancel keeps the previous choice.
            if not self.show_output_options():
                self._use_pdf_options(False)
                with QSignalBlocker(self.archive_format):
                    self.archive_format.setCurrentIndex(ARCHIVE_CHOICES.index(previous))
                choice = previous
        elif choice != "pdf" and previous == "pdf":
            self._use_pdf_options(False)
        self.archive_previous = choice
        if choice != "pdf":
            self.settings.setValue("archive_format", choice)
        self.refresh_export()

    def _update_save_comicinfo(self, opt):
        """Shown with Include ComicInfo.xml; grayed out with no archive, whose
        page folders hold the file already."""
        self.save_comicinfo.setVisible(opt.comicinfo_xml is not None)
        self.save_comicinfo.setEnabled(opt.cbz)
        self.save_comicinfo.setToolTip(self.save_comicinfo_hint if opt.cbz else tooltip_text(
            "With no archive, ComicInfo.xml is already saved in each folder of pages."))

    def _update_keep_images(self, opt):
        """Gray out "Also keep lossless PNG pages" when, with no archive, every size's
        pages are already being saved as PNG."""
        sizes = ("full", "reduced") if opt.size_output == "both" else (opt.size_output,)
        useful = any(keeps_png(replace(opt, keep_images=True), size) for size in sizes)
        self.keep_images.setEnabled(useful)
        self.keep_images.setToolTip(getattr(self, "keep_images_hint", "") if useful else tooltip_text(
            "The pages are already being saved as PNG, in a folder, since there's no archive."))

    def options_from_screen(self):
        self.opt.comicinfo_xml = ((self.plan.comicinfo_xml or '<ComicInfo/>')
                                 if self.plan is not None and self.include_comicinfo.isChecked() else None)
        self.opt.book_name = self.book_name.text()
        self.opt.prefixed = self.naming.currentIndex() == 1
        self.opt.custom_prefix = self.custom_page_prefix.text() if self.naming.currentIndex() == 2 else None
        self.opt.start = 1 if self.start_number.currentIndex() == 0 else 0
        self.opt.padding = (0, 2, 3, 4)[self.padding.currentIndex()]
        self.opt.keep_originals = self.handling.currentIndex() == 1
        self.opt.cbz = self._archive_choice() != "none"
        self.opt.archive_format = self._archive_choice() if self.opt.cbz else "cbz"
        self.opt.keep_images = self.keep_images.isChecked()
        self.opt.save_cover = self.save_cover.isChecked()
        self.opt.save_comicinfo = self.save_comicinfo.isChecked()
        self.opt.front_to_back = self.front_to_back.isChecked() if self.plan and self.plan.scan.right_to_left else True
        self.opt.workers = self.worker_count()
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
            self.create_button.setText("Review next comic" if self.pending_imports else "Start export")
            self.export_back_button.setVisible(not self.editing_queued)
        if hasattr(self, "export_scroll"):
            QTimer.singleShot(0, self._fit_export_settings)  # After any text change is laid out.
        if self.plan is None:
            return
        self.front_to_back.setVisible(self.plan.scan.right_to_left)
        try:
            opt = self.options_from_screen()
            self._update_keep_images(opt)
            self._update_save_comicinfo(opt)
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
            self.export_hint.setText(EXPORT_HINT)
            self.create_button.setEnabled(True)
            if self.current_job is not None:
                self.current_job.options = replace(opt)  # The queued entry follows the screen.
            self._schedule_size_estimate()
        except (ValueError, IndexError) as exc:
            self.export_hint.setText(str(exc))
            self.create_button.setEnabled(False)
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
        self._show_early_lossless_hint()
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
            self._show_early_lossless_hint()

    def _start_size_estimate(self):
        if not self._estimate_wanted():
            return
        if self.editing_queued and self.current_job.reimport_source is not None:
            self.size_estimate.setText("Not available: this comic's pages are imported again at export")
            return
        try:
            opt = self.options_from_screen()
        except (ValueError, IndexError):
            self.size_estimate.setText("")
            return
        self.estimate_png_pages = not opt.cbz
        if not self.size_worker.isRunning():
            self.size_worker.start()  # Only once an estimate is first wanted.
        share = self._lossless_share()
        self.size_worker.request(self.estimate_generation, self.plan, opt,
                                 compare_lossless=share is None or share <= LOSSLESS_CHECK_SKIP_SHARE)

    def _size_estimate_ready(self, generation, sizes, final=True):
        if generation != self.estimate_generation:
            return
        what = " of pages, in a folder" if self.estimate_png_pages else ""
        if len(sizes) == 1:
            text = f"About {readable_size(next(iter(sizes.values())))}{what}"
        else:
            text = (f"Full: about {readable_size(sizes['full'])}  ·  "
                    f"Reduced: about {readable_size(sizes['reduced'])}{what}")
        self.size_estimate.setText(text if final else text + " (refining…)")
        if final:
            self.estimate_full_size = sizes.get("full")

    def _lossless_estimate_ready(self, generation, lossless_size):
        if generation != self.estimate_generation or not self.estimate_full_size:
            return
        if lossless_size > LOSSLESS_HINT_SHARE * self.estimate_full_size:
            self._show_lossless_hint(None)  # The full estimate outweighs the quick check.
            return
        both = self.opt.size_output == "both"
        self._show_lossless_hint(
            f"{'The full-size copy' if both else 'This comic'} would be about "
            f"{readable_size(lossless_size)} as lossless WebP, instead of about "
            f"{readable_size(self.estimate_full_size)}, with no loss of quality.")

    def _start_lossless_check(self):
        """Begin the quick lossless WebP check while this comic is reviewed."""
        self._stop_lossless_checks()
        if self.plan is None:
            return
        opt = replace(self.usual_opt, keep_originals=self.handling.currentIndex() == 1,
                      cbz=True, archive_format="cbz", size_output="full")
        if lossless_alternative(opt) is None:
            return  # The usual format is already lossless.
        worker = LosslessCheckWorker(self.plan, opt)
        worker.done.connect(self._lossless_check_done)
        worker.finished.connect(lambda worker=worker: self.lossless_checks.remove(worker)
                                if worker in self.lossless_checks else None)
        self.lossless_checks.append(worker)
        worker.start(QThread.Priority.LowPriority)

    def _stop_lossless_checks(self):
        for worker in self.lossless_checks:
            worker.requestInterruption()
        self.lossless_checked = (None, None)

    def _lossless_check_done(self, plan, share):
        if plan is not self.plan:
            return
        self.lossless_checked = (plan, share)
        if self.lossless_hint_row.isHidden():
            self._show_early_lossless_hint()

    def _lossless_share(self):
        plan, share = self.lossless_checked
        return share if plan is not None and plan is self.plan else None

    def _show_early_lossless_hint(self):
        """The suggestion as soon as the quick check supports it; the full estimate adds sizes."""
        share = self._lossless_share()
        wanted = (share is not None and share <= LOSSLESS_HINT_SHARE
                  and self.pages.currentWidget() is self.export_page and self.create_button.isEnabled()
                  and lossless_alternative(self.opt) is not None)
        self._show_lossless_hint("Lossless WebP would likely make this comic much smaller, with no loss "
                                 "of quality." if wanted else None)

    def _show_lossless_hint(self, text):
        if not hasattr(self, "lossless_hint_row"):
            return
        shown = text is not None
        if shown:
            self.lossless_hint.setText(text)
        if shown == self.lossless_hint_row.isHidden():
            self.lossless_hint_row.setVisible(shown)
            QTimer.singleShot(0, self._fit_export_settings)

    def _use_lossless_webp(self):
        """Lossless WebP for the pages this comic encodes; this comic only.

        What suits one book (clean line art) can be a poor choice for the next (a
        full-colour scan), so the saved output settings keep the usual format and
        the next comic starts with it again.
        """
        self.lossless_field = "spread_format" if self.opt.keep_originals else "format"
        setattr(self.opt, self.lossless_field, "webp")
        self.opt.webp_lossless = True
        self.lossless_for_this_comic = True
        diagnostics.note("Lossless WebP chosen for this comic from the size suggestion")
        self.refresh_export()

    def _saved_formats(self) -> dict:
        """The format choices saved by Edit options."""
        image = str(self.settings.value("output_format", "jpeg"))
        spread = str(self.settings.value("spread_format", "jpeg"))
        return {"format": image if image in ("jpeg", "webp", "png") else "jpeg",
                "spread_format": spread if spread in ("png", "jpeg", "webp") else "jpeg",
                "webp_lossless": self.settings.value("webp_lossless", False, type=bool)}

    def _lossless_still_chosen(self, opt) -> bool:
        return getattr(opt, self.lossless_field) == "webp" and opt.webp_lossless

    def _end_lossless_for_this_comic(self):
        """Back to the saved format choices, as the next comic starts."""
        if self.lossless_for_this_comic:
            saved = self._saved_formats()
            setattr(self.usual_opt, self.lossless_field, saved[self.lossless_field])
            self.usual_opt.webp_lossless = saved["webp_lossless"]
            self.lossless_for_this_comic = False

    def _size_estimate_failed(self, generation, _message):
        if generation == self.estimate_generation:
            self.size_estimate.setText("Size estimate unavailable")

    def _estimate_toggled(self, checked):
        self.settings.setValue("estimate_size", checked)
        self._schedule_size_estimate()

    def _format_summary(self, opt, original=False):
        if original or opt.keep_originals and opt.size_output == "full":
            return f"Original formats · joined/rotated pages: {opt.spread_format.upper()}"
        if opt.format == "png":
            return "PNG · lossless"
        if opt.format == "webp" and opt.webp_lossless:
            return f"WebP · lossless · effort {opt.webp_quality}"
        return f"{ 'JPEG' if opt.format == 'jpeg' else 'WebP'} · quality " + str(
            opt.jpeg_quality if opt.format == "jpeg" else opt.webp_quality) + (
            " · sharp YUV" if opt.format == "webp" and opt.webp_sharp_yuv else "")

    def _preflight_export(self, opt: ExportOptions) -> list[Path]:
        self.export_name_notice = ""
        planned_names(self.plan, opt, reduced=opt.size_output == "reduced")
        if opt.size_output == "both":
            planned_names(self.plan, opt, reduced=True)
        if not (self.current_job is not None and self.current_job.reimport_source is not None):
            check_sources(self.plan.scan)  # Re-imported pages are checked when they're imported.
        if opt.comicinfo_xml is not None:
            parse_xml(opt.comicinfo_xml)
        outputs = queued_outputs(self.plan, opt)
        reserved = {str(path).casefold() for job in self.queue_jobs if job is not self.current_job
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
        self.settings.setValue("save_cover", self.save_cover.isChecked())
        self.settings.setValue("save_comicinfo", self.save_comicinfo.isChecked())
        if self.plan is not None and self.plan.scan.right_to_left:
            self.settings.setValue("front_to_back", opt.front_to_back)
        self.settings.sync()

    def export_screen_continue(self):
        """The blue button: Review next comic, or Start export."""
        if not self._settle_current_job():
            return
        if self.pending_imports:
            self._commit_current_job()
            self.new_comic()
            QTimer.singleShot(0, self._open_next_pending)
        elif self.queue_jobs == [self.current_job] and not self.editing_queued:
            # Just this comic: export it directly, keeping it ready to export again.
            self.queue_jobs.clear()
            self.current_job = None
            self._refresh_queue_status()
            self.start_export(checked=True)
        else:
            self._commit_current_job()
            self.new_comic()
            self.start_queue()

    def _settle_current_job(self) -> bool:
        """Check this comic's settings before moving on; False (after saying why) if they won't do."""
        if self.current_job is None:
            return True
        opt = self.options_from_screen()
        try:
            self._preflight_export(opt)
        except Exception as exc:
            QMessageBox.warning(self, "Check this comic's settings", str(exc))
            return False
        if self.export_name_notice:
            QMessageBox.information(self, "Name changed", self.export_name_notice)
        self._remember_export_preferences(opt)
        self.current_job.options = replace(opt)
        return True

    def _commit_current_job(self):
        """This comic stays queued as it is; the window moves on to another."""
        job = self.current_job
        if job is None:
            return
        if not self.editing_queued:
            self._save_review_report()
            job.plan = copy.deepcopy(self.plan)
            job.session_source = self.session_source
            job.screenshot_stage_dir = self.screenshot_stage_dir
            job.import_work = self.import_work
            source = self.plan.scan.source_archive
            if self.import_work is not None and source is not None and not self.keep_queue_pages.isChecked():
                stat = source.stat()
                job.reimport_source = source
                job.source_stat = (stat.st_size, stat.st_mtime_ns)
                job.pdf_options = dict(self.pdf_session_options)
                self.import_work.cleanup()
                job.import_work = None
            self.import_work = None  # Retained imports are owned by the queued job.
            self.screenshot_stage_dir = None  # So is a screenshot staging folder.
        diagnostics.note("Queued comic settled: %s", job.options.book_name)
        self.current_job = None
        self.editing_queued = False

    def _load_options_to_screen(self, opt: ExportOptions):
        """Show a queued comic's settings on the Create your comic screen."""
        self.book_name.setText(opt.book_name)
        if opt.custom_prefix is not None:
            self.custom_page_prefix.setText(opt.custom_prefix)
        self.naming.setCurrentIndex(2 if opt.custom_prefix is not None else 1 if opt.prefixed else 0)
        self.start_number.setCurrentIndex(0 if opt.start == 1 else 1)
        self.padding.setCurrentIndex({0: 0, 2: 1, 3: 2, 4: 3}.get(opt.padding, 0))
        self.handling.setCurrentIndex(1 if opt.keep_originals else 0)
        choice = opt.archive_format if opt.cbz else "none"
        resize = {"mode": opt.resize_mode, "pixels": opt.resize_pixels, "reduce_only": opt.resize_reduce_only,
                  "gamma": opt.resize_gamma, "filter": opt.resize_filter}
        with QSignalBlocker(self.archive_format):
            self.archive_format.setCurrentIndex(ARCHIVE_CHOICES.index(choice))
        self.archive_previous = choice
        if choice == "pdf":
            self.pdf_opt, self.pdf_resize_values = replace(opt), resize
        else:
            self.usual_opt, self.usual_resize_values = replace(opt), resize
            # A queued comic switched to lossless WebP by the suggestion keeps it, for itself only.
            self.lossless_field = "spread_format" if opt.keep_originals else "format"
            saved = self._saved_formats()
            self.lossless_for_this_comic = (self._lossless_still_chosen(opt) and not (
                saved[self.lossless_field] == "webp" and saved["webp_lossless"]))
        self._use_pdf_options(choice == "pdf")
        self.keep_images.setChecked(opt.keep_images)
        self.save_cover.setChecked(opt.save_cover)
        self.save_comicinfo.setChecked(opt.save_comicinfo)
        self.front_to_back.setChecked(opt.front_to_back)
        with QSignalBlocker(self.include_comicinfo):
            self.include_comicinfo.setChecked(opt.comicinfo_xml is not None)
        self.metadata_button.setEnabled(opt.comicinfo_xml is not None)

    def _edit_queued_job(self, job: QueuedComic):
        """Open a queued comic's settings. Its pages and pairs are kept as reviewed."""
        self.plan = job.plan
        self.current_job = job
        self.editing_queued = True
        self.active_right_to_left = job.plan.scan.right_to_left
        self._load_options_to_screen(job.options)
        self.show_export()

    def start_export(self, checked=False):
        """Export the comic on screen by itself (Start export with nothing else queued)."""
        opt = self.options_from_screen()
        if not checked:
            try:
                self._preflight_export(opt)
            except Exception as exc:
                QMessageBox.warning(self, "Cannot export", str(exc))
                return
            self._remember_export_preferences(opt)
        self._cancel_size_estimate("")
        self.size_worker.release()  # Export needs the processor.
        self.progress_label.setText("Creating comic…")
        self.progress_bar.setValue(0)
        self._show_progress_page()
        self._compact_window()
        self._run_worker(export_book, (self.plan, opt), self.export_done)

    def _refresh_queue_status(self):
        count = len(self.queue_jobs)
        waiting = len(self.pending_imports)
        self.queue_view_button.setEnabled(bool(count))
        if hasattr(self, "export_queue_button"):
            self.export_queue_button.setText(f"View queue ({count})…")
            self.export_queue_button.setVisible(bool(count))
        self.queue_start_button.setEnabled(bool(count) and self.active_queue_job is None and
                                           not (self.worker and self.worker.isRunning()))
        self.next_import_button.setVisible(bool(waiting))

    def show_queue(self, *, reviewing=False):
        """The queued comics, in export order. From the Create your comic screen
        (reviewing) the comic on screen is among them; starting the queue is left
        to that screen's blue button."""
        dialog = OutlinedDialog(self)
        dialog.setWindowTitle("Queue")
        dialog.resize(760, 480)
        dialog.setStyleSheet("QListWidget { font-size:14px; } "
                            "QListWidget::item { padding:10px; border-bottom:1px solid #d8e0e8; } "
                            "QListWidget::item:selected { color:#102a43; background:#dceeff; }")
        layout = QVBoxLayout(dialog)
        note = QLabel("Comics are exported from the top down; drag one to change the order. Double-click "
                      "a comic to change its settings. Output files and folders will be saved alongside "
                      "each comic's originals. Removing a comic leaves its source untouched.")
        note.setWordWrap(True)
        layout.addWidget(note)
        listing = QueueList()
        listing.setToolTip(tooltip_text("Drag a comic to change the export order, or select it and press "
                                        "Ctrl with Up, Down, Home or End. Double-click to change its settings."))
        listing.setWordWrap(True)
        listing.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(listing)
        controls = QHBoxLayout()
        remove = QPushButton("Remove selected")
        start = QPushButton("Start export")
        action = {"next": None, "job": None}
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
                item.setData(Qt.UserRole, id(job))
                listing.addItem(item)
            remove.setEnabled(False)
            start.setEnabled(bool(self.queue_jobs))
        listing.currentRowChanged.connect(lambda index: remove.setEnabled(index >= 0))
        def job_at(row):
            jobs = {id(job): job for job in self.queue_jobs}
            return jobs[listing.item(row).data(Qt.UserRole)]
        def reordered():
            # The list's new order becomes the queue's order.
            order = [job_at(row) for row in range(listing.count())]
            if len(order) == len(self.queue_jobs) and order != self.queue_jobs:
                self.queue_jobs[:] = order
                diagnostics.note("Queue reordered: %s", " | ".join(job.options.book_name for job in order))
        listing.reordered.connect(reordered)
        def finish(next_action, job=None):
            action["next"], action["job"] = next_action, job
            dialog.accept()
        def remove_selected():
            row = listing.currentRow()
            if row < 0:
                return
            job = job_at(row)
            if job is self.current_job:
                if QMessageBox.question(
                        dialog, "Remove this comic?",
                        f"“{job.options.book_name}” is the comic you're working on. Remove it from the queue "
                        "and move on? Its source is untouched, and its saved review can be resumed by "
                        "opening it again.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                    return
                finish("skip current")
                return
            self.queue_jobs.remove(job)
            if job.import_work is not None:
                job.import_work.cleanup()
            refresh()
            self._refresh_queue_status()
        remove.clicked.connect(remove_selected)
        listing.itemDoubleClicked.connect(lambda item: finish("edit", job_at(listing.row(item))))
        controls.addWidget(remove)
        another = QPushButton("Add another comic")
        another.setToolTip(tooltip_text("Keep these comics queued and go back to the start screen to add more."))
        another.clicked.connect(lambda: finish("another"))
        controls.addWidget(another)
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
        another.setVisible(reviewing)
        for button in (files, folder, start):
            button.setVisible(not reviewing)
        refresh()
        dialog.exec()
        dialog.deleteLater()
        self._queue_window_action(action["next"], action["job"])

    def _queue_window_action(self, next_action, job=None):
        if next_action == "start":
            self.start_queue()
        elif next_action in ("files", "folder"):
            self._add_batch_items(folder=next_action == "folder")
        elif next_action == "another":
            if self._settle_current_job():
                self._commit_current_job()
                self.new_comic()
                self._refresh_queue_status()
        elif next_action == "skip current":
            self.queue_jobs.remove(self.current_job)
            self.current_job = None
            self.editing_queued = False
            self.new_comic()
            self._refresh_queue_status()
            if self.pending_imports:
                QTimer.singleShot(0, self._open_next_pending)
        elif next_action == "edit" and job is not None and job is not self.current_job:
            if self.current_job is not None:
                if not self._settle_current_job():
                    return
                self._commit_current_job()
                self.new_comic()
            self._edit_queued_job(job)

    def _add_batch_items(self, *, folder=False):
        if folder:
            selected = QFileDialog.getExistingDirectory(self, "Add comic folder or batch folder")
            paths = [Path(selected)] if selected else []
        else:
            selected, _ = QFileDialog.getOpenFileNames(
                self, "Add comic files", "",
                "Comic files (*.cbz *.CBZ *.cbr *.CBR *.cb7 *.CB7 *.zip *.ZIP *.rar *.RAR *.7z *.7Z *.pdf *.PDF)")
            paths = [Path(path) for path in selected]
        if paths:
            self.batch_import_active = True
            self._import_paths(paths)

    def start_queue(self):
        if (not self.queue_jobs or self.active_queue_job is not None or
                self.worker and self.worker.isRunning()):
            return
        self.batch_import_active = False
        self.queue_completed = 0
        self.queue_results.clear()
        self._show_progress_page()
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
        job.options.workers = self.worker_count()  # The current setting, even if changed since queueing.
        if job.pdf_options is not None:
            job.pdf_options["workers"] = job.options.workers
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
                if path.exists() and path not in outputs:  # Folders, cover and ComicInfo.xml too.
                    outputs.append(path)
        self.progress_label.setText("Created:\n" + "\n".join(path.name for path in outputs))
        self.progress_source.clear()
        self.done_button.show()
        self.repeat_button.show()
        self._compact_window()

    def repeat_export(self):
        """Keep the reviewed book and settings; suggest an unused test name."""
        if self.plan is None or (self.worker and self.worker.isRunning()):
            return
        folder = self.plan.scan.output_folder or self.plan.scan.folder
        base = safe_book_name(self.replay_base_name or self.book_name.text())
        index = self.replay_count + 1
        while True:
            candidate = f"{base} - Test {index}"
            names = (f"{candidate}{suffix}{ext}" for suffix in ("", " - Full", " - Reduced")
                     for ext in (".cbz", ".cb7", ".pdf", " - PNG pages", " - JPEG pages", " - WebP pages", " - Pages",
                                 " - Cover.jpg", " - Cover.webp", " - Cover.png", " - ComicInfo.xml"))
            if not any((folder / name).exists() for name in names):
                break
            index += 1
        self.book_name.setText(candidate)
        self.show_export()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Please wait", "The current scan or export must finish before closing.")
            event.ignore()
        elif [job for job in self.queue_jobs if job is not self.current_job or self.editing_queued] and QMessageBox.question(
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
            self._stop_lossless_checks()
            if (not self.thumb_worker.wait(3000) or not self.review_worker.wait(3000)
                    or not self.size_worker.wait(10000)
                    or not all(worker.wait(10000) for worker in list(self.lossless_checks))):
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
    startup_id = take_startup_id()  # The app menu's busy cursor, ended once the window shows.
    app = QApplication(sys.argv)
    if sys.platform.startswith("linux"):
        # Matches windows to the app-menu entry linux/install.sh creates (Wayland app id).
        QApplication.setDesktopFileName("comic-stitcher")
    ui_app_style(app)  # Fusion, with a few app-wide adjustments.
    broker = LaunchBroker()
    try:
        if not broker.start([Path(argument) for argument in sys.argv[1:]]):
            # Handed to the copy already running: this launch is complete too.
            finish_startup(startup_id, app.platformName())
            return 0
        window = ComicWindow()
        broker.paths_received.connect(window._external_import)
        window.show()
        QTimer.singleShot(0, lambda: finish_startup(startup_id, app.platformName()))
        return app.exec()
    finally:
        broker.close()
