"""PySide6 desktop interface for Comic Stitcher."""
from __future__ import annotations

import sys
import os
import shutil
import tempfile
import json
from collections import OrderedDict, deque
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QEvent, QSettings, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QFontMetrics, QDesktopServices, QPixmap, QShortcut, QKeySequence, QIcon
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QSizePolicy, QSpinBox, QVBoxLayout, QWidget, QGroupBox)

from .backend import BookPlan, encoding_workers
from .export import ExportOptions
from . import __version__, diagnostics
from .startup_notice import finish_startup, take_startup_id
from .single_instance import LaunchBroker
from .pdf_import import pdf_workers
from .comicvine import ComicVineClient
from .key_store import can_remember_key, forget_key, protect_key, retrieve_key
from .window_outline import OutlinedDialog, OutlinedWindow, prefer_xwayland_on_gnome
from .ui.common import (START_SIZE, PROGRESS_WIDTH, fit_height, ui_heading, ui_app_style,
    ui_primary, ProgressLabel, CurrentPageStack, tooltip_text, form_hint, interface_icon,
    review_control)
from .ui.workers import Worker, ReviewImageWorker, ThumbnailWorker
from .ui.overlap import OverlapDialog
from .ui.output_options import ARCHIVE_CHOICES, OutputOptionsDialog
from .ui.pdf_dialog import PdfImportDialog
from .ui.theme import tone
from .rar_export import find_rar
from .ui.crop_review import ScreenshotCropDialog
from .ui.import_dialogs import ImportDropZone, REVIEW_MODES, ImportChoiceDialog
from .ui.thumbnails import QueueList
from .ui.queue import QueuedComic, export_queued_comic, queued_outputs
from .ui.import_flow import ImportScreen
from .ui.screenshot_flow import ScreenshotImport
from .ui.pair_review import PairReview
from .ui.final_review import FinalReview
from .ui.export_screen import ExportScreen
from .ui.queue_flow import QueueFlow


class ComicWindow(ImportScreen, ScreenshotImport, PairReview, FinalReview, ExportScreen, QueueFlow,
                  OutlinedWindow, QMainWindow):
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
        centering = QCheckBox("Check whether each page's art is centered")
        centering.setObjectName("check_centering")
        centering.setChecked(self.settings.value("check_centering", True, type=bool))
        centering.setToolTip(tooltip_text(
            "Printed comics often sit off-center on the page, pushed aside by the margin for the binding. "
            "Checked: while you review pairs, each page is measured in the background, and the final "
            "review offers to center the art when pages need it. It takes a few seconds of computer "
            "time for each comic; uncheck it if you never want it."))
        layout.addWidget(centering)
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
        rar_group, rar_choice = self._rar_settings(dialog)
        layout.addWidget(rar_group)
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
            self.settings.setValue("check_centering", centering.isChecked())
            self.settings.setValue("rar_program", rar_choice["path"])
            self._update_rar_choice(recheck=True)
            self.refresh_export()

    def _rar_settings(self, dialog):
        """Settings' CBR group: the RAR program found, or one chosen with Browse….
        Returns the group and a dict whose "path" is the choice ("" = find it)."""
        group = QGroupBox("Optional CBR export")
        box = QVBoxLayout(group)
        status = QLabel()
        status.setWordWrap(True)
        status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.addWidget(status)
        choice = {"path": self.settings.value("rar_program", "", type=str)}

        def show():
            program = find_rar(choice["path"])
            if program:
                how = "Chosen" if choice["path"] else "Found"
                status.setText(f"{how}: {program.label}, {program.path}")
            elif choice["path"]:
                status.setText(f"{choice['path']} isn't RARLAB's RAR program (5.0 or newer), "
                               "so CBR can't be made with it.")
            else:
                status.setText("No RAR program was found, so CBR isn't offered. "
                               + ("Install WinRAR, or choose its Rar.exe." if os.name == "nt" else
                                  "Install RARLAB's rar ('sudo apt install rar' on Ubuntu), or choose it."))
            find.setEnabled(bool(choice["path"]))

        def browse():
            start = str(Path(choice["path"]).parent) if choice["path"] else (
                os.environ.get("ProgramFiles", "") if os.name == "nt" else "/usr/bin")
            path, _ = QFileDialog.getOpenFileName(
                dialog, "Choose RAR's console program", start,
                "Rar.exe (Rar.exe rar.exe);;All files (*)" if os.name == "nt" else "")
            if path:
                if find_rar(path) is None:
                    QMessageBox.warning(dialog, "Not RAR",
                                        "That isn't RARLAB's RAR program, version 5.0 or newer. On Windows it's "
                                        "Rar.exe in WinRAR's folder (not WinRAR.exe, and not UnRAR.exe, which "
                                        "can only extract).")
                    return
                choice["path"] = path
                show()

        row = QHBoxLayout()
        choose = QPushButton("Browse…")
        choose.clicked.connect(browse)
        find = QPushButton("Find automatically")
        find.clicked.connect(lambda: (choice.update(path=""), show()))
        row.addWidget(choose)
        row.addWidget(find)
        row.addStretch()
        box.addLayout(row)
        group.setToolTip(tooltip_text(
            "Only RARLAB's own RAR can make RAR archives, so Comic Stitcher uses yours, if you have it: "
            "Rar.exe, which comes with WinRAR, or rar on Linux. WinRAR is a trial, then paid, from RARLAB."))
        show()
        return group, choice

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
                            ("M", lambda: self._review_key("marked")),
                            ("C", lambda: self._review_key("centered"))):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.activated.connect(action)
            self.shortcuts[key] = shortcut

    def _update_shortcuts(self, *_):
        current = self.pages.currentWidget()
        for key, shortcut in self.shortcuts.items():
            shortcut.setEnabled(current == self.review or
                                (current == self.preview and key in ("Left", "Right", "Space", "Escape", "M", "C")) or
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
            if action == "centered":
                self.next_centered_page()
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

    def _build_progress(self):
        self.progress_page = QWidget()
        box = QVBoxLayout(self.progress_page)
        box.addStretch()
        self.progress_source = ProgressLabel()
        self.progress_source.setAlignment(Qt.AlignCenter)
        self.progress_source.setWordWrap(True)
        self.progress_source.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.progress_source.setStyleSheet(f"color:{tone('muted')}; font-size:13px;")
        box.addWidget(self.progress_source)
        self.progress_label = ProgressLabel()
        self.progress_label.setAlignment(Qt.AlignCenter)
        self.progress_label.setWordWrap(True)
        self.progress_label.setStyleSheet(f"font-size:19px; font-weight:700; color:{tone('heading')};")
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
        self.progress_caption.setStyleSheet(f"color:{tone('muted')};")
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
        self._stop_centering_checks()
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

    def open_diagnostic_logs(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(diagnostics.log_path().parent)))

    def open_review_reports(self):
        folder = diagnostics.log_path().parent.parent / "review-reports"
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

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
            self._stop_centering_checks()
            if (not self.thumb_worker.wait(3000) or not self.review_worker.wait(3000)
                    or not self.size_worker.wait(10000)
                    or not all(worker.wait(10000) for worker in list(self.lossless_checks))
                    or not all(worker.wait(10000) for worker in list(self.centering_checks))):
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
