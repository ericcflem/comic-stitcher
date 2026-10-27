"""Screenshot import: measuring, the crop summary and review, and staging the crops."""
from __future__ import annotations

import shutil
from functools import partial
from dataclasses import replace
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QMessageBox
from .. import diagnostics
from ..session import cached_folder_scan, clear_session, read_session, session_path
from ..screenshot_import import CropChoice, StagedScreenshots, analyze_screenshots, stage_screenshots
from ..crop_summary import crop_summary
from ..screenshot_geometry import family_box, holds_art, own_size
from .crop_review import ScreenshotCropDialog


class ScreenshotImport:
    """Mixed into ComicWindow (gui.py), which builds the window and holds its state."""

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
