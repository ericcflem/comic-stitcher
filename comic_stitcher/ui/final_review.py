"""Final review: the finished comic page by page, the thumbnail strip and page edits."""
from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, QSize, Qt, QThread, QTimer
from PySide6.QtGui import QCursor, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QHBoxLayout, QLabel, QMessageBox,
    QProgressDialog, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)
from ..backend import BoundaryOverrideRequired, encoding_workers
from .. import diagnostics
from ..session import save_session
from .common import (tooltip_text, review_control, qimage_from_pixels, page_pixmap, spread_pixmap,
    FitImage, DropMarker)
from .overlap import OverlapDialog, JoinedPairView
from .workers import CenteringWorker
from .thumbnails import (DRAG_SCROLL_ZONE, DRAG_SCROLL_MIN, DRAG_SCROLL_MAX, DRAG_SCROLL_INTERVAL,
    ThumbnailStrip, PageThumbnail)


class FinalReview:
    """Mixed into ComicWindow (gui.py), which builds the window and holds its state."""

    def _build_preview(self):
        self.preview = QWidget()
        box = QVBoxLayout(self.preview)
        self.preview_heading = QLabel()
        box.addWidget(self.preview_heading)
        self._build_centering_row(box)
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
                                  ("Continue to Export", self.continue_to_export)):
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
        review_control(self.remove_preview_button, "⌫", "Remove", "Remove the current page, or both pages of a spread, after confirmation")
        review_control(self.undo_remove_button, "↶", "Undo", "Undo the last page move, swap, rotation, join, split, or removal")
        remove_row.removeWidget(self.remove_preview_button)
        remove_row.removeWidget(self.undo_remove_button)
        controls.insertWidget(controls.count() - 2, self.remove_preview_button)
        controls.insertWidget(controls.count() - 2, self.undo_remove_button)
        self.pages.addWidget(self.preview)

    def _save_preview_position(self):
        if self.plan and self.session_source and self.pages.currentWidget() == self.preview and self.replay_count == 0:
            try:
                save_session(self.session_source, diagnostics.log_path().parent.parent,
                             self.plan, possible_started=self.possible_review_started,
                             stage="preview", preview_index=self.preview_index,
                             pdf_options=self.pdf_session_options)
            except OSError:
                diagnostics.exception("Could not save preview position")

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
        self.preview_ready = False  # Drawn by _populate_preview, once this screen is showing.
        if self.review_report_path:
            self.statusBar().showMessage("Pair review report saved. Open it from Settings → Open pair review reports.")

        QTimer.singleShot(0, self._populate_preview)

    def _populate_preview(self):
        if not self.isVisible() or self.plan is None or self.pages.currentWidget() != self.preview:
            return
        self.rebuild_thumbnails()
        self.show_preview()
        self.preview_ready = True
        if self.plan.centering_measured:
            QTimer.singleShot(0, self._offer_centering)
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
                                                            rotation=self.plan.rotations.get(first[0], 0),
                                                            centering=self.plan.page_centering(first[0]))
                                                if first[1] is None else spread_pixmap(
                                                    self.plan.scan.pages[first[0]].path,
                                                    self.plan.scan.pages[first[1]].path,
                                                    rotation=self.plan.rotations.get(first[0], 0),
                                                    right_to_left=self.plan.scan.right_to_left,
                                                               page_rotations=self.plan.joined_rotations.get(first[0], (0, 0)),
                                                               overlap=self.plan.join_overlaps.get(first[0], (0, "right"))))
                    self.compare_image.show()
                    self.compare_image.show_pixmap(page_pixmap(self.plan.scan.pages[second[0]].path,
                                                               rotation=self.plan.rotations.get(second[0], 0),
                                                               centering=self.plan.page_centering(second[0]))
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
                                                        rotation=self.plan.rotations.get(a, 0),
                                                        centering=self.plan.page_centering(a)) if b is None
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
        self._update_centering_row()
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
        self.remove_preview_button.setEnabled(self.comparing is None)
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

    # Centering the art -------------------------------------------------------
    CENTERING_NOTE = ("Most pages will be better centered, but a few may not come out exactly right, "
                      "such as pages where art or lettering spills past the panels. Press C to go from "
                      "one moved page to the next and check them; any page can be left as it was.")

    def _build_centering_row(self, box):
        """The offer to center art that print margins push aside, shown once
        the background check has found pages that need it."""
        self.centering_row = QWidget()
        row = QHBoxLayout(self.centering_row)
        self.centering_row.setObjectName("centeringRow")
        self.centering_row.setStyleSheet("QWidget#centeringRow { background:#fff4d6; border:1px solid #e0b44a; "
                                         "border-radius:6px; }")
        row.setContentsMargins(10, 6, 10, 6)
        self.centering_label = QLabel()
        self.centering_label.setStyleSheet("font-weight:600; color:#5c4300;")
        self.centering_label.setToolTip(tooltip_text(self.CENTERING_NOTE))
        self.center_art_box = QCheckBox("Center the art")
        self.center_art_box.setToolTip(tooltip_text(
            "Moves the art on these pages sideways to the middle of the page, filling the strip it "
            "uncovers with the page's own color. Only single pages; spreads are left as they are. "
            "Moved pages are saved again at export, in the format for joined and rotated pages. "
            + self.CENTERING_NOTE))
        self.center_art_box.toggled.connect(self._center_art_toggled)
        self.center_page_box = QCheckBox("Center this page")
        self.center_page_box.setToolTip(tooltip_text("Uncheck to leave this page's art where it was."))
        self.center_page_box.toggled.connect(self._center_page_toggled)
        for box_ in (self.center_art_box, self.center_page_box):
            box_.setFocusPolicy(Qt.NoFocus)  # Space stays the seam key.
        row.addWidget(self.centering_label)
        row.addWidget(self.center_art_box)
        row.addWidget(self.center_page_box)
        row.addStretch()
        self.centering_row.hide()
        box.addWidget(self.centering_row)
        self.centering_checks = []

    def _start_centering_check(self):
        """Measure every page in the background while the comic is reviewed."""
        self._stop_centering_checks()
        plan = self.plan
        if (plan is None or plan.centering_measured or plan.scan.screenshot_source is not None
                or not self.settings.value("check_centering", True, type=bool)):
            return
        worker = CenteringWorker(plan, max(1, encoding_workers(self.worker_count()) // 2))
        worker.done.connect(self._centering_done)
        worker.progress.connect(self._centering_progress)
        self.centering_measured_count = (0, len(plan.scan.pages))
        worker.finished.connect(lambda worker=worker: self.centering_checks.remove(worker)
                                if worker in self.centering_checks else None)
        self.centering_checks.append(worker)
        worker.start(QThread.Priority.LowPriority)

    def _stop_centering_checks(self):
        for worker in self.centering_checks:
            worker.requestInterruption()

    def _centering_progress(self, done, total):
        self.centering_measured_count = (done, total)
        waiting = getattr(self, "centering_wait", None)
        if waiting is not None:
            waiting.setMaximum(total)
            waiting.setValue(done)

    def _centering_done(self, plan, found):
        if plan is not self.plan:
            return
        plan.centering = found
        plan.centering_measured = True
        self._save_review_report()
        waiting = getattr(self, "centering_wait", None)
        if waiting is not None:
            waiting.accept()  # Continue to Export was waiting for this; it carries on.
        elif self.pages.currentWidget() == self.preview and self.preview_ready:
            self._update_centering_row()
            self._offer_centering()

    def _centering_checking(self) -> bool:
        return (self.plan is not None and not self.plan.centering_measured
                and any(worker.isRunning() for worker in self.centering_checks))

    def _offer_centering(self) -> None:
        """Ask once per comic, when pages are off-center: the line in the final
        review is easy to miss."""
        plan = self.plan
        if plan is None or plan.centering_offered or plan.center_art:
            return
        pages = plan.centered_pages()
        if not pages:
            return
        plan.centering_offered = True
        total = sum(right is None for _, right in plan.entries())
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Question)
        dialog.setWindowTitle("Center the art?")
        dialog.setText(f"The art on {len(pages)} of {total} pages sits off-center, "
                       "probably because of print margins. Center it?")
        dialog.setInformativeText(self.CENTERING_NOTE + " You can change this in the final review "
                                  "until you export.")
        center = dialog.addButton("Center the art", QMessageBox.AcceptRole)
        dialog.addButton("Leave it as it is", QMessageBox.RejectRole)
        dialog.setDefaultButton(center)
        dialog.exec()
        if dialog.clickedButton() == center:
            self.center_art_box.setChecked(True)  # As if checked by hand: undoable, saved.
        self._update_centering_row()

    def continue_to_export(self):
        """Continue to Export: first finish the centering check, if it's still
        running, and offer centering, so the offer isn't missed."""
        if self._centering_checking():
            done, total = getattr(self, "centering_measured_count", (0, 0))
            waiting = QProgressDialog("Checking whether the art is centered on each page…",
                                      "Skip the check", 0, max(1, total), self)
            waiting.setWindowTitle("One moment")
            waiting.setWindowModality(Qt.WindowModal)
            waiting.setMinimumDuration(0)
            waiting.setAutoClose(False)  # Closed when the result is in, not at the last page.
            waiting.setAutoReset(False)
            waiting.setValue(done)
            self.centering_wait = waiting
            try:
                finished = waiting.exec() == QDialog.Accepted or self.plan.centering_measured
            finally:
                self.centering_wait = None
            if not finished:
                self._stop_centering_checks()  # Skipped: export as it is.
            self._update_centering_row()
        if self.plan is not None and self.plan.centering_measured:
            before = self.plan.center_art
            self._offer_centering()
            if self.plan.center_art != before:
                self.show_preview()
                return  # Centered just now: see the pages before exporting.
        self.show_export()

    def _update_centering_row(self):
        plan = self.plan
        checking = bool(self.centering_checks) and plan is not None and not plan.centering_measured
        pages = plan.centered_pages() if plan is not None else []
        self.centering_row.setVisible(checking or bool(pages))
        if checking:
            self.centering_label.setText("Checking whether the art is centered on each page…")
        elif pages:
            total = sum(right is None for _, right in plan.entries())
            self.centering_label.setText(
                f"The art on {len(pages)} of {total} pages sits off-center, probably because of print margins.")
        self.center_art_box.setVisible(bool(pages))
        with QSignalBlocker(self.center_art_box):
            self.center_art_box.setChecked(plan is not None and plan.center_art)
        entries = plan.entries() if plan is not None else []
        current = entries[self.preview_index] if 0 <= self.preview_index < len(entries) else (None, None)
        on_page = bool(pages) and plan.center_art and current[1] is None and current[0] in plan.centering
        self.center_page_box.setVisible(on_page and not self.comparing)
        if on_page:
            with QSignalBlocker(self.center_page_box):
                self.center_page_box.setChecked(current[0] not in plan.center_skipped)

    def _center_art_toggled(self, on):
        entries = self.plan.entries()
        self.plan.set_center_art(on, entries[self.preview_index][0] if entries else 0)
        self._save_review_report()
        self.show_preview()

    def _center_page_toggled(self, on):
        left, right = self.plan.entries()[self.preview_index]
        if right is None:
            self.plan.set_page_centered(left, on)
            self._save_review_report()
            self.show_preview()

    def next_centered_page(self):
        """C in the final review: the next page whose art is centered (or would be)."""
        if self.plan is None:
            return
        pages = set(self.plan.centered_pages())
        moved = [index for index, (a, b) in enumerate(self.plan.entries()) if b is None and a in pages]
        if not moved:
            self.statusBar().showMessage("No pages are off-center.", 4000)
            return
        target = next((i for i in moved if i > self.preview_index), moved[0])
        self.comparing = None
        self.preview_index = self.selected_first = target
        self.show_preview(follow=True)
        left = self.plan.entries()[target][0]
        shift = self.plan.centering[left][0]
        state = ("left as it was" if left in self.plan.center_skipped or not self.plan.center_art
                 else "centered")
        self.statusBar().showMessage(f"Off-center page {moved.index(target) + 1} of {len(moved)}: "
                                     f"art {abs(shift)} px {'left' if shift > 0 else 'right'} of the middle, "
                                     f"{state}", 8000)

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
        if not self._confirm_page_removal(index, right):
            return
        try:
            self.plan.remove_page(index, right)
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot remove page", str(exc))
            return
        for page in (index, right):
            if page is not None:
                diagnostics.note("Page excluded from comic: %s", self.plan.scan.pages[page].path.name)
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
