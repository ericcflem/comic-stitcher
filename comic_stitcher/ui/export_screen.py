"""Create your comic: names, output settings, size estimates, metadata and exporting."""
from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from PySide6.QtCore import Qt, QThread, QTimer, QSignalBlocker
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget, QGroupBox)
from ..backend import check_sources, safe_book_name
from ..export import (lossless_check_key, keeps_png, ExportOptions, export_book, planned_names, copy_encoding_options,
    lossless_alternative, LOSSLESS_HINT_SHARE, LOSSLESS_CHECK_SKIP_SHARE)
from .. import diagnostics
from ..metadata_dialog import MetadataDialog
from ..comicinfo import parse_xml
from .common import EXPORT_HINT, ui_heading, ui_primary, tooltip_text, form_hint, readable_size
from .workers import SizeEstimateWorker, LosslessCheckWorker
from .output_options import ARCHIVE_CHOICES, pdf_page_options, OutputOptionsDialog
from .queue import QueuedComic, queued_outputs
from ..rar_export import find_rar
from ..space import export_need
from .theme import tone


class ExportScreen:
    """Mixed into ComicWindow (gui.py), which builds the window and holds its state."""

    def _build_export(self):
        self.export_page = QWidget()
        self.export_page.setStyleSheet(
            f"QGroupBox {{ font-weight:600; font-size:15px; border:1px solid {tone('panel_border')}; "
            f"border-radius:8px; margin-top:26px; background:{tone('panel')}; }} "
            "QGroupBox::title { subcontrol-origin:margin; subcontrol-position:top left; top:0px; left:12px; padding:0 4px; } "
            f"QWidget#exportBookContent, QWidget#exportBookViewport {{ background:{tone('panel')}; }} "
            f"QGroupBox QLabel, QGroupBox QCheckBox {{ font-weight:400; color:{tone('text')}; }} "
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
                  "Sets the finished archive's name (plus .cbz, .cb7 or .cbr) and the processed-image folder name. "
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
        self.archive_format.addItems(["CBZ (ZIP)", "CB7 (7-Zip)", "CBR (RAR)", "PDF", "No archive"])
        self.rar = find_rar(self.settings.value("rar_program", "", type=str))
        saved_archive = str(self.settings.value("archive_format", "cbz"))
        if (saved_archive not in ARCHIVE_CHOICES or saved_archive == "pdf"
                or (saved_archive == "cbr" and self.rar is None)):
            saved_archive = "cbz"  # PDF is never the remembered choice; CBR needs RAR.
        self.archive_format.setCurrentIndex(ARCHIVE_CHOICES.index(saved_archive))
        self.archive_previous = saved_archive
        self.archive_format.currentIndexChanged.connect(self._archive_changed)
        form.addRow("Archive format", self.archive_format)
        form_hint(form, self.archive_format,
                  "CBZ packages images as a ZIP archive; CB7 uses a 7z container; CBR is a RAR archive, "
                  "made with your own RAR program (WinRAR on Windows), and offered when it's found; PDF "
                  "puts one image on each page. All four store the already-encoded images in reading "
                  "order without changing their quality. A PDF holds JPEG and PNG pages, so choosing it asks which to "
                  "use; it is kept until Comic Stitcher closes, then your usual archive format returns. "
                  "Choose No archive to save the finished pages in a folder instead, in the "
                  "format you chose (one folder for each size).")
        for index, description in enumerate(("Most widely supported comic archive; images stored inside ZIP.",
                                              "7z comic archive; verify that your comic reader supports CB7.",
                                              "RAR comic archive, made with your RAR program.",
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
        self.rar4 = QCheckBox("Use the older RAR4 format (if your comic reader can't open the CBR)")
        self.rar4.setChecked(self.settings.value("rar4", False, type=bool))
        self.rar4.hide()
        self.rar4.toggled.connect(self.refresh_export)
        form.addRow("", self.rar4)
        self._update_rar_choice()
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
        self.output_summary.setStyleSheet(f"color:{tone('heading')}; padding:6px; font-size:13px;")
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
        self.size_estimate.setStyleSheet(f"color:{tone('heading')}; padding-left:6px;")
        estimate_row.addWidget(self.size_estimate, 1)
        output_box.addLayout(estimate_row)
        # Shown when lossless WebP would make the comic far smaller (clean line art).
        self.lossless_hint_row = QWidget()
        lossless_row = QHBoxLayout(self.lossless_hint_row)
        lossless_row.setContentsMargins(0, 0, 0, 0)
        self.lossless_hint = QLabel("")
        self.lossless_hint.setWordWrap(True)
        self.lossless_hint.setStyleSheet(f"color:{tone('good')}; padding-left:6px;")
        lossless_row.addWidget(self.lossless_hint, 1)
        self.lossless_button = QPushButton("Use lossless WebP")
        self.lossless_button.setToolTip(tooltip_text(
            "Saves the pages Comic Stitcher encodes as lossless WebP: every pixel is kept exactly, "
            "and clean line art such as black-and-white strips is often several times smaller "
            "than JPEG, and usually smaller than PNG. Check that your comic reader opens WebP. "
            "For this comic only: your usual output settings aren't changed, and the "
            "next comic starts with them again."))
        self.lossless_button.clicked.connect(self._use_lossless_webp)
        lossless_row.addWidget(self.lossless_button)
        self.lossless_hint_row.hide()
        output_box.addWidget(self.lossless_hint_row)
        self.estimate_full_size = None
        self.lossless_for_this_comic = False  # Lossless WebP from the suggestion: never saved.
        self.lossless_field = "format"
        self.lossless_checks = []  # Running quick checks (kept until finished).
        self.lossless_checked = (None, None, None)  # (plan, settings, share) from the latest quick check.
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
        if not getattr(self, "preview_ready", False):
            # A resumed review that opened on this screen went straight past the
            # final review before it was drawn: draw it now, thumbnails and all.
            QTimer.singleShot(0, self._populate_preview)

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

    def _update_rar_choice(self, recheck: bool = False):
        """CBR is listed only when RAR is found (or chosen in Settings); RAR4 only
        when that RAR can still make it (RAR 6 and older)."""
        if recheck:
            self.rar = find_rar(self.settings.value("rar_program", "", type=str))
        row = ARCHIVE_CHOICES.index("cbr")
        self.archive_format.view().setRowHidden(row, self.rar is None)
        item = self.archive_format.model().item(row)
        if item is not None:
            item.setEnabled(self.rar is not None)
        if self.rar is None and self._archive_choice() == "cbr":
            self.archive_format.setCurrentIndex(ARCHIVE_CHOICES.index("cbz"))
        older = self.rar is not None and self.rar.can_make_rar4
        self.rar4.setEnabled(older)
        self.rar4.setToolTip(tooltip_text(
            "CBRs are made in the RAR5 format, which current comic readers open. Some older readers "
            "only open RAR4: check this if yours can't open the CBR." if older else
            f"Your RAR program ({self.rar.label if self.rar else 'not found'}) makes only the RAR5 format: "
            "RAR 7 dropped RAR4. RAR or WinRAR 6 or older can make RAR4."))
        self.rar4.setVisible(self._archive_choice() == "cbr")

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
        if hasattr(self, "rar4"):
            self.rar4.setVisible(choice == "cbr")
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
        cbr = self.opt.cbz and self.opt.archive_format == "cbr"
        self.opt.rar_program = self.rar.path if cbr and self.rar else ""
        self.opt.rar4 = cbr and self.rar4.isEnabled() and self.rar4.isChecked()
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
        key = lossless_check_key(opt)
        worker.done.connect(lambda plan, share, key=key: self._lossless_check_done(plan, share, key))
        worker.finished.connect(lambda worker=worker: self.lossless_checks.remove(worker)
                                if worker in self.lossless_checks else None)
        self.lossless_checks.append(worker)
        worker.start(QThread.Priority.LowPriority)

    def _stop_lossless_checks(self):
        for worker in self.lossless_checks:
            worker.requestInterruption()
        self.lossless_checked = (None, None, None)

    def _lossless_check_done(self, plan, share, key=None):
        if plan is not self.plan:
            return
        self.lossless_checked = (plan, key, share)
        if self.lossless_hint_row.isHidden():
            self._show_early_lossless_hint()

    def _lossless_share(self):
        """The quick check's answer, while this comic's pages would be encoded as
        it measured them. Change the format or quality and it no longer applies:
        lossless might lose to JPEG at 80 yet win easily against WebP at 95."""
        plan, key, share = self.lossless_checked
        if plan is None or plan is not self.plan:
            return None
        return share if key == lossless_check_key(self.opt) else None

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
        if opt.cbz and opt.archive_format == "cbr" and self.rar4.isEnabled():
            self.settings.setValue("rar4", opt.rar4)
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
        if opt.archive_format == "cbr":
            self.rar4.setChecked(opt.rar4)
        self.rar4.setVisible(choice == "cbr")
        with QSignalBlocker(self.include_comicinfo):
            self.include_comicinfo.setChecked(opt.comicinfo_xml is not None)
        self.metadata_button.setEnabled(opt.comicinfo_xml is not None)

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
        folder = self.plan.scan.output_folder or self.plan.scan.folder
        if not self._space_ok(folder, export_need(self.plan, opt), "Creating this comic"):
            return
        self._cancel_size_estimate("")
        self.size_worker.release()  # Export needs the processor.
        self.progress_label.setText("Creating comic…")
        self.progress_bar.setValue(0)
        self._show_progress_page()
        self._compact_window()
        self._run_worker(export_book, (self.plan, opt), self.export_done)

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
                     for ext in (".cbz", ".cb7", ".cbr", ".pdf", " - PNG pages", " - JPEG pages", " - WebP pages", " - Pages",
                                 " - Cover.jpg", " - Cover.webp", " - Cover.png", " - ComicInfo.xml"))
            if not any((folder / name).exists() for name in names):
                break
            index += 1
        self.book_name.setText(candidate)
        self.show_export()
