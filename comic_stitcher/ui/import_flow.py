"""Importing: the start screen's drops and choices, scanning and opening a comic."""
from __future__ import annotations

import tempfile
from functools import partial
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from PySide6.QtCore import Qt, QTimer, QSignalBlocker
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox
from ..backend import BookPlan, scan_folder, scan_manual_folder, POSSIBLE_MIN_SCORE, offered
from .. import diagnostics
from ..import_sources import expand_sources
from ..detector import collect_images
from ..archive_import import ARCHIVE_EXTS, import_archive
from ..pdf_import import import_pdf
from ..pdf_jpegs import check_pdf_jpegs
from ..session import cached_folder_scan, clear_session, read_session, restore_plan, session_path
from ..screenshot_import import looks_like_screenshot_folder
from .pdf_dialog import PdfImportDialog
from .import_dialogs import NO_PAIR_REVIEW, ImportChoiceDialog


class ImportScreen:
    """Mixed into ComicWindow (gui.py), which builds the window and holds its state."""

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
        self._start_centering_check()  # Measured while the pairs are reviewed.
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
