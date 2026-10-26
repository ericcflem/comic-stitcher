"""PDF import settings, with a rendered preview."""
from __future__ import annotations

from pathlib import Path
from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QVBoxLayout)
from ..pdf_import import rendered_dimensions
from ..window_outline import OutlinedWindow
from .common import form_hint, qimage_from_pixels, ui_heading, ui_primary
from .workers import PdfPreviewWorker


class PdfImportDialog(OutlinedWindow, QDialog):
    def __init__(self, source: Path, dpi: int,
                 smooth_images: bool = True, parent=None, *, manual: bool = False, right_to_left: bool = False,
                 jpeg_reason: str | None = None):
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
        # Why the original JPEGs weren't offered, when the PDF's pages are images.
        self.jpeg_reason = QLabel(f"Original JPEGs can't be extracted:\n{jpeg_reason}" if jpeg_reason else "")
        self.jpeg_reason.setWordWrap(True)
        self.jpeg_reason.setVisible(bool(jpeg_reason))
        settings.addWidget(self.jpeg_reason)
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
