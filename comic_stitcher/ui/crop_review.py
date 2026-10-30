"""Review screenshot crops: page sizes, crop types and outlines."""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from PIL import Image
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QPixmap, QShortcut, QKeySequence, QPainter, QColor, QPen
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
    QRadioButton, QButtonGroup)
from ..window_outline import OutlinedWindow
from ..screenshot_import import CropChoice
from ..screenshot_geometry import holds_art, kind_for_extent, own_size, remeasure
from .common import FitImage, qimage_from_pixels, review_control
from .workers import CropPreviewWorker, ThumbnailWorker


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
        # Page sizes per screenshot size: the measured families, or the one size.
        self.canvas_families: dict[tuple[int, int], set[int]] = {}
        everything = calibration or proposals
        for proposal in everything:
            if proposal.family_widths:
                self.canvas_families.setdefault(proposal.canvas, set()).update(proposal.family_widths)
        for canvas, known in self.sizes.items():
            if canvas not in self.canvas_families and "single" in known:
                self.canvas_families[canvas] = {known["single"]}
        self.family_counts = {}
        for proposal in everything:
            family = self._family_of(proposal, sorted(self.canvas_families.get(proposal.canvas, ())))
            if isinstance(family, int) and proposal.box is not None:
                key = (proposal.canvas, family)
                self.family_counts[key] = self.family_counts.get(key, 0) + 1
        self.family_selection = {index: self._initial_family(index) for index in range(len(proposals))}
        self.book_family = None  # (canvas, width) once "use for all pages" is on
        family_row = QHBoxLayout()
        family_row.addWidget(QLabel("Page size:"))
        self.family_buttons = QButtonGroup(self)
        self.family_row = family_row
        family_row.addStretch()
        self.all_pages = QCheckBox("Use this size for all pages")
        self.all_pages.setToolTip("Crop every page at the selected size. Pages measured at a size of "
                                  "their own (covers, say) and crops you adjusted by hand keep theirs.")
        self.all_pages.toggled.connect(self._use_for_all)
        family_row.addWidget(self.all_pages)
        self.family_widget = QWidget()
        self.family_widget.setLayout(family_row)
        outer.addWidget(self.family_widget)
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

    def _families(self, index):
        return sorted(self.canvas_families.get(self.canvases[index], ()))

    def _initial_family(self, index):
        return self._family_of(self.proposals[index], self._families(index))

    @staticmethod
    def _family_of(proposal, families):
        """The page size a proposal was cropped at, "own" for a page measured at
        a size of its own, or None when it's still to be chosen."""
        if proposal.family_width is not None:
            return proposal.family_width
        if own_size(proposal, families):
            return "own"
        box = proposal.box
        if box is not None and proposal.kind in ("single", "double"):
            width = (box[2] - box[0]) / (2 if proposal.kind == "double" else 1)
            near = [f for f in families if abs(f - width) <= max(3, round(f * .002))]
            if near:
                return min(near, key=lambda f: abs(f - width))
        return families[0] if len(families) == 1 else None

    def _show_families(self, index):
        for button in self.family_buttons.buttons():
            self.family_buttons.removeButton(button)
            self.family_row.removeWidget(button)
            button.deleteLater()
        canvas = self.canvases[index]
        selected = self.family_selection.get(index)
        options = [(width, f"{width} × {canvas[1]}" +
                    (f" ({count} {'page' if count == 1 else 'pages'})"
                     if (count := self.family_counts.get((canvas, width))) else ""))
                   for width in self._families(index)]
        own = "This page's own size"
        choice = self.choices[index]
        if selected == "own" and choice is not None:
            own += f" ({choice.box[2] - choice.box[0]} × {canvas[1]})"
        options.append(("own", own))
        for position, (value, caption) in enumerate(options, 1):
            button = QRadioButton(caption)
            button.setChecked(value == selected)
            if value == "own":
                button.setToolTip("Measure this screenshot again on its own and crop it at the size "
                                  "it measures (for a cover of a different size, say).")
            else:
                button.setToolTip("Crop this screenshot at this page size.")
            button.clicked.connect(lambda _=False, v=value: self._select_family(v))
            self.family_buttons.addButton(button)
            self.family_row.insertWidget(position, button)
        self.all_pages.blockSignals(True)
        self.all_pages.setChecked(self.book_family is not None and self.book_family[0] == canvas)
        self.all_pages.setEnabled(isinstance(selected, int) or self.all_pages.isChecked())
        self.all_pages.blockSignals(False)

    def _flagged(self, index, kind):
        reason = getattr(self.proposals[index], "review_reason", None)
        return (index not in self.replaced and
                (kind == "sideways" or reason is not None and "middle width" in reason))

    def _extent(self, index):
        """What's visible on the screenshot, as measured (None once replaced)."""
        return None if index in self.replaced else getattr(self.proposals[index], "measured_box", None)

    def _refit(self, index, width):
        """Crop this page at a page size, keeping single or double; a page still
        to review gets the kind its content suggests. Returns False, changing
        nothing, when that size would cut off artwork the screenshot shows."""
        choice = self.choices[index]
        if choice is not None and choice.custom:
            return True
        kind = choice.kind if choice is not None else kind_for_extent(self._extent(index), width)
        previous = self.family_selection.get(index)
        self.family_selection[index] = width
        box = self._guide(kind, index) if kind is not None else None
        if box is not None and not holds_art(box, self._extent(index)):
            self.family_selection[index] = previous
            return False
        if box is not None:
            self.choices[index] = CropChoice(self.sources[index], kind, box,
                                             choice.inferred if choice and choice.box == box else True,
                                             flagged=choice.flagged if choice else self._flagged(index, kind))
            self._label_item(index)
        return True

    def _art_kept(self, indexes, width, canvas):
        if indexes:
            QMessageBox.information(
                self, "Artwork kept",
                f"{'Screenshot' if len(indexes) == 1 else 'Screenshots'} "
                f"{', '.join(str(i + 1) for i in indexes[:15])}{', …' if len(indexes) > 15 else ''} "
                f"kept {'its' if len(indexes) == 1 else 'their'} crop: the artwork is wider than a "
                f"{width} × {canvas[1]} page, so that size would cut it off.")

    def _select_family(self, value):
        index = self.list.currentRow()
        if index < 0:
            return
        if value == "own":
            found = remeasure(self.sources[index])
            if found is None:
                QMessageBox.information(self, "Measure this page",
                                        "This page's edges blend into the background, so it can't be "
                                        "measured on its own. Choose a page size instead.")
                self.show_page(index)
                return
            self.family_selection[index] = "own"
            self.choices[index] = CropChoice(self.sources[index], found.kind, found.box)
            self._label_item(index)
        else:
            if not self._refit(index, value):
                self._art_kept([index], value, self.canvases[index])
            elif self.all_pages.isChecked():
                self._apply_family(self.canvases[index], value)
        self.show_page(index)

    def _apply_family(self, canvas, width):
        self.book_family = (canvas, width)
        kept = []
        for index in range(len(self.sources)):
            choice = self.choices[index]
            if (self.canvases[index] != canvas or self.family_selection.get(index) == "own" or
                    choice is not None and choice.custom):
                continue
            if not self._refit(index, width):
                kept.append(index)
        self._art_kept(kept, width, canvas)

    def _use_for_all(self, checked):
        index = self.list.currentRow()
        if index < 0:
            return
        selected = self.family_selection.get(index)
        if checked and isinstance(selected, int):
            self._apply_family(self.canvases[index], selected)
        elif not checked:
            self.book_family = None
        self.show_page(index)

    def _dimensions(self, index):
        family = self.family_selection.get(index)
        if isinstance(family, int):
            known = self.sizes.get(self.canvases[index], {})
            if (len(self.canvas_families.get(self.canvases[index], ())) == 1 and
                    known.get("single") == family and "double" in known):
                return dict(known)  # Keep a measured double's odd pixel (397 = 2 × 198 + 1).
            return {"single": family, "double": family * 2}
        choice = self.choices[index]
        if family == "own" and choice is not None and choice.kind in ("single", "double"):
            single = (choice.box[2] - choice.box[0]) // (2 if choice.kind == "double" else 1)
            return {"single": single, "double": choice.box[2] - choice.box[0]}
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
        self._show_families(index)
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
                self.canvas_families.setdefault(canvas, set()).add(self.base_size[0])
                for index, page in enumerate(self.canvases):
                    if page == canvas and self.family_selection.get(index) != "own":
                        self.family_selection[index] = self.base_size[0]
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
                                         flagged=self._flagged(index, kind))
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
                                              "Images (*.png *.PNG *.jpg *.JPG *.jpeg *.JPEG *.webp *.WEBP *.tif *.TIF *.tiff *.TIFF *.bmp *.BMP)")
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
        if self.family_selection.get(index) == "own":
            self.family_selection[index] = self._initial_family(index) if canvas == self.proposals[index].canvas else None
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
