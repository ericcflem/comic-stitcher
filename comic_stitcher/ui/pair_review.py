"""Pair review: one suggested pair at a time, Yes or No, Seam and the possible-pair choices."""
from __future__ import annotations

from PIL import Image, ImageOps
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget)
from ..backend import rotated_image
from .. import diagnostics
from ..colour import standard_mode
from ..review_report import save_report
from ..session import save_session
from .common import set_review_icon, review_control, qimage_from_pixels
from .theme import tone
from .overlap import OverlapDialog, JoinedPairView


class PairReview:
    """Mixed into ComicWindow (gui.py), which builds the window and holds its state."""

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
        self.yes_button.setStyleSheet(self.yes_button.styleSheet() +
                                      f"QPushButton:hover {{ background:{tone('yes_hover')}; }}")
        self.no_button.setStyleSheet(self.no_button.styleSheet() +
                                     f"QPushButton:hover {{ background:{tone('no_hover')}; }}")
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

    def _confirm_page_removal(self, index: int, also: int | None = None) -> bool:
        pages = self.plan.scan.pages
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Warning)
        if also is None:
            dialog.setWindowTitle("Remove page from finished comic?")
            dialog.setText(f"Remove page {index + 1}: {pages[index].path.name}?")
            dialog.setInformativeText(
                "This page will disappear from Final Comic Preview and the exported comic. "
                "Are you sure?")
        else:
            dialog.setWindowTitle("Remove both pages from finished comic?")
            dialog.setText(f"Remove pages {index + 1} and {also + 1}: "
                           f"{pages[index].path.name} and {pages[also].path.name}?")
            dialog.setInformativeText(
                "Both pages will disappear from Final Comic Preview and the exported comic. "
                "Are you sure?")
        remove = dialog.addButton("Remove from comic", QMessageBox.DestructiveRole)
        keep = dialog.addButton("Keep pages" if also is not None else "Keep page", QMessageBox.RejectRole)
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
