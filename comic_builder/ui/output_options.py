"""Output options: formats, quality, encoder settings and the reduced copy."""
from __future__ import annotations

from dataclasses import replace
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QPushButton, QScrollArea, QSpinBox, QStackedWidget, QTabWidget, QVBoxLayout, QWidget)
from ..export import ExportOptions, copy_encoding_options, ENCODER_FIELDS
from ..sharp_webp import sharp_yuv_available
from ..window_outline import OutlinedWindow
from .common import form_hint, tooltip_text


ARCHIVE_CHOICES = ("cbz", "cb7", "pdf", "none")  # Order of the Archive format list.


PDF_PAGES_NOTE = ("A PDF holds JPEG and PNG pages, so choose one of those for this comic's pages. "
                  "These PDF settings are kept until Comic Stitcher closes; your usual output options "
                  "don't change.")


def pdf_page_options(opt: ExportOptions) -> ExportOptions:
    """A copy of the output options a PDF can use: WebP pages become JPEG."""
    reduced = copy_encoding_options(opt, True)
    settings = {name: getattr(reduced, name) for name in ENCODER_FIELDS}
    if settings["format"] == "webp":
        settings["format"] = "jpeg"
    return replace(opt, format="jpeg" if opt.format == "webp" else opt.format,
                   spread_format="jpeg" if opt.spread_format == "webp" else opt.spread_format,
                   reduced_custom=True, reduced_settings=settings, archive_format="pdf")


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
        self.sharp_yuv = QCheckBox("Sharp color conversion (sharp YUV)")
        self.sharp_yuv.setChecked(opt.webp_sharp_yuv)
        wf.addRow(self.lossless)
        wf.addRow("Compression effort", self.method)
        wf.addRow(self.sharp_yuv)
        form_hint(wf, self.lossless,
                  "Preserves decoded pixel values instead of using lossy WebP compression. Files may be larger. "
                  "In lossless mode, the main WebP quality value controls compression effort, not image quality.")
        form_hint(wf, self.method,
                  "WebP encoder method, 0–6. Higher values generally spend more CPU time seeking "
                  "a smaller or better-encoded file; they do not change the selected quality setting.")
        self.sharp_yuv_hint = ("Lossy WebP keeps color at half resolution. This chooses that color more "
                               "carefully, so edges between strong colors, such as red lettering or blue sky "
                               "against black ink, stay clean instead of bleeding or darkening. Encoding takes "
                               "about twice as long and files are a few percent larger. Lossless WebP doesn't "
                               "need it. It works alongside gamma-corrected resizing: resizing happens "
                               "first, then this conversion, both treating colors as sRGB.")
        form_hint(wf, self.sharp_yuv, self.sharp_yuv_hint)
        self.lossless.toggled.connect(self._update_sharp_yuv)
        self._update_sharp_yuv()
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

    def _update_sharp_yuv(self):
        available = sharp_yuv_available()
        self.sharp_yuv.setEnabled(available and not self.lossless.isChecked())
        self.sharp_yuv.setToolTip(tooltip_text(
            self.sharp_yuv_hint if available else
            "Needs libwebp's cwebp tool, which wasn't found. Windows builds include it; "
            "on Linux, install the \"webp\" package."))

    def reset_defaults(self):
        self.progressive.setChecked(False)
        self.optimize.setChecked(True)
        self.subsampling.setCurrentIndex(0)
        self.smooth.setValue(0)
        self.lossless.setChecked(False)
        self.method.setValue(4)
        self.sharp_yuv.setChecked(False)
        self.compress.setValue(6)
        self.reset_requested.emit()

    def apply(self):
        self.opt.jpeg_progressive = self.progressive.isChecked()
        self.opt.jpeg_optimize = self.optimize.isChecked()
        self.opt.jpeg_subsampling = self.subsampling.currentIndex()
        self.opt.jpeg_smooth = self.smooth.value()
        self.opt.webp_lossless = self.lossless.isChecked()
        self.opt.webp_method = self.method.value()
        self.opt.webp_sharp_yuv = self.sharp_yuv.isChecked() and sharp_yuv_available()
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
                  "more natural. It uses more processing time. It treats colors as sRGB, the usual "
                  "color space for comics; pages tagged with another color profile are converted to sRGB first.")
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
    """Edit a copy of the output settings; apply nothing until OK.

    One tab per copy: Full size and Reduced size. Each has a checkbox to make
    that copy and its own format settings, every setting directly under the
    choice it belongs to. At least one copy is always made.
    """

    def __init__(self, opt: ExportOptions, resize_values: dict, parent=None, *, size_limits=None,
                 pdf: bool = False):
        super().__init__(parent)
        self._setup_outline()  # GNOME on Wayland: a visible window edge
        self.for_pdf = pdf
        self.setWindowTitle("PDF page format" if pdf else "Output options")
        self.resize(650, 520)
        self.setStyleSheet("QTabWidget::pane { border:1px solid #ccd5dd; } "
                           "QFormLayout { spacing:12px; }")
        self.draft = replace(opt)
        self.keep_originals = opt.keep_originals
        box = QVBoxLayout(self)
        if pdf:
            self.pdf_note = QLabel(PDF_PAGES_NOTE)
            self.pdf_note.setWordWrap(True)
            box.addWidget(self.pdf_note)
        self.tabs = QTabWidget()
        box.addWidget(self.tabs)

        # Full size: re-encoded pages, or (when unchanged pages are copied) the
        # format for joined and rotated pages.
        full_tab = QWidget()
        full_box = QVBoxLayout(full_tab)
        self.make_full = QCheckBox("Create a full-size copy")
        self.make_full.setChecked(opt.size_output != "reduced")
        self.make_full.setToolTip(tooltip_text("Keeps the final pages at their source dimensions."))
        full_box.addWidget(self.make_full)
        self.full_settings = QWidget()
        full_settings_box = QVBoxLayout(self.full_settings)
        full_settings_box.setContentsMargins(0, 6, 0, 0)
        choice = QFormLayout()
        self.format = QComboBox()
        self.format.addItems(["JPEG", "WebP", "PNG"])
        self.format.setCurrentText({"jpeg": "JPEG", "webp": "WebP", "png": "PNG"}.get(opt.format, "JPEG"))
        choice.addRow("Output format", self.format)
        form_hint(choice, self.format, "The format every final page of the full-size copy is saved in.")
        self.spread_format = QComboBox()
        self.spread_format.addItems(["PNG (lossless)", "JPEG", "WebP"])
        self.spread_format.setCurrentIndex({"png": 0, "jpeg": 1, "webp": 2}[opt.spread_format])
        choice.addRow("Format for joined/rotated pages", self.spread_format)
        form_hint(choice, self.spread_format,
                  "Pages unchanged during review are copied as they are. Newly joined spreads and "
                  "rotated single pages must be encoded: PNG avoids another lossy step; JPEG or WebP "
                  "can be smaller but re-encode the edited image.")
        choice.setRowVisible(self.format, not self.keep_originals)
        choice.setRowVisible(self.spread_format, self.keep_originals)
        full_settings_box.addLayout(choice)
        if self.keep_originals:
            copied = QLabel("Other pages are copied without re-encoding (chosen under Image handling)."
                            + (" In a PDF, copied pages that aren't JPEG or PNG are converted to this "
                               "format." if pdf else ""))
            copied.setWordWrap(True)
            copied.setStyleSheet("color:#617080;")
            full_settings_box.addWidget(copied)
        self.jpeg_quality = QSpinBox()
        self.jpeg_quality.setRange(1, 100)
        self.jpeg_quality.setValue(opt.jpeg_quality)
        self.webp_quality = QSpinBox()
        self.webp_quality.setRange(1, 100)
        self.webp_quality.setValue(opt.webp_quality)
        self.full_quality = QFormLayout()
        self.full_quality.addRow("JPEG quality", self.jpeg_quality)
        form_hint(self.full_quality, self.jpeg_quality,
                  "JPEG quality, 1–100. Higher values preserve more detail but usually make larger "
                  "files. This setting is remembered next time you open the app. Default: 90.")
        self.full_quality.addRow("WebP quality", self.webp_quality)
        form_hint(self.full_quality, self.webp_quality,
                  "For lossy WebP, 1–100 controls image quality and file size. In lossless WebP mode, "
                  "it controls compression effort: higher is slower, not higher image quality. "
                  "The value is remembered next time you open the app. Default: 90.")
        self.webp_quality_label = self.full_quality.labelForField(self.webp_quality)
        self.advanced = AdvancedSettingsWidget(self.draft, opt.format)
        self.advanced.reset_requested.connect(lambda: (self.jpeg_quality.setValue(90),
                                                       self.webp_quality.setValue(90)))
        full_settings_box.addWidget(self._indented(self.full_quality, self.advanced))
        full_box.addWidget(self.full_settings)
        full_box.addStretch()
        self.tabs.addTab(full_tab, "Full size")

        # Reduced size: its own resize, format and quality settings.
        reduced_tab = QWidget()
        reduced_box = QVBoxLayout(reduced_tab)
        self.make_reduced = QCheckBox("Create a reduced-size copy")
        self.make_reduced.setChecked(opt.size_output != "full")
        self.make_reduced.setToolTip(tooltip_text(
            "Creates a resized comic. With a full-size copy as well, the two are saved separately "
            "with Full and Reduced added to their names."))
        reduced_box.addWidget(self.make_reduced)
        self.reduced_settings = QWidget()
        reduced_settings_box = QVBoxLayout(self.reduced_settings)
        reduced_settings_box.setContentsMargins(0, 6, 0, 0)
        self.resize_settings = ResizeSettingsWidget(resize_values)
        reduced_settings_box.addWidget(self.resize_settings)
        if size_limits:
            def limit_target():
                axis = self.resize_settings.mode.currentText().lower()
                self.resize_settings.pixels.setMaximum(min(20000, size_limits[axis]))
            self.resize_settings.mode.currentIndexChanged.connect(limit_target)
            limit_target()
        # Until now the reduced copy could follow the full-size settings; start
        # from whatever it actually used, so nothing changes unexpectedly.
        self.reduced_draft = replace(copy_encoding_options(opt, True))
        reduced_choice = QFormLayout()
        self.reduced_format = QComboBox()
        self.reduced_format.addItems(["JPEG", "WebP", "PNG"])
        self.reduced_format.setCurrentText({"jpeg": "JPEG", "webp": "WebP", "png": "PNG"}.get(
            self.reduced_draft.format, "JPEG"))
        reduced_choice.addRow("Output format", self.reduced_format)
        form_hint(reduced_choice, self.reduced_format, "The format the reduced copy's pages are saved in.")
        reduced_settings_box.addLayout(reduced_choice)
        self.reduced_quality = QSpinBox()
        self.reduced_quality.setRange(1, 100)
        self.reduced_quality.setValue(self.reduced_draft.jpeg_quality if self.reduced_draft.format == "jpeg"
                                      else self.reduced_draft.webp_quality)
        self.reduced_quality_form = QFormLayout()
        self.reduced_quality_form.addRow("Quality", self.reduced_quality)
        self.reduced_quality_label = self.reduced_quality_form.labelForField(self.reduced_quality)
        self.reduced_advanced = AdvancedSettingsWidget(self.reduced_draft, self.reduced_draft.format)
        self.reduced_advanced.reset_requested.connect(lambda: self.reduced_quality.setValue(90))
        reduced_settings_box.addWidget(self._indented(self.reduced_quality_form, self.reduced_advanced))
        reduced_box.addWidget(self.reduced_settings)
        reduced_box.addStretch()
        reduced_scroll = QScrollArea()
        reduced_scroll.setWidgetResizable(True)
        # No frame or background of its own, so it looks like the Full size tab.
        reduced_scroll.setFrameShape(QFrame.NoFrame)
        reduced_scroll.setObjectName("resizeTabScroll")
        reduced_tab.setObjectName("resizeTabContent")
        reduced_scroll.viewport().setObjectName("resizeTabViewport")
        reduced_scroll.setStyleSheet("QScrollArea#resizeTabScroll, QWidget#resizeTabViewport, "
                                     "QWidget#resizeTabContent { background:transparent; }")
        reduced_scroll.setWidget(reduced_tab)
        self.tabs.addTab(reduced_scroll, "Reduced size")

        if pdf:
            # A PDF can't hold WebP: it isn't offered.
            for combo in (self.format, self.spread_format, self.reduced_format):
                combo.removeItem(next(i for i in range(combo.count()) if combo.itemText(i).startswith("WebP")))
        for control in (self.make_full, self.make_reduced, self.advanced.lossless,
                        self.reduced_advanced.lossless):
            control.toggled.connect(self._refresh)
        for control in (self.format, self.spread_format, self.reduced_format):
            control.currentIndexChanged.connect(self._refresh)
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

    @staticmethod
    def _indented(quality_form, advanced):
        """A format's settings, indented under the format choice they belong to."""
        panel = QWidget()
        panel_box = QVBoxLayout(panel)
        panel_box.setContentsMargins(20, 0, 0, 0)
        panel_box.addLayout(quality_form)
        advanced.layout().setContentsMargins(0, 0, 0, 0)
        panel_box.addWidget(advanced)
        return panel

    def full_format(self):
        """The format the full-size copy encodes pages in."""
        if self.keep_originals:
            return ("PNG", "JPEG", "WebP")[self.spread_format.currentIndex()]
        return self.format.currentText()

    def _refresh(self, *_):
        full, reduced = self.make_full.isChecked(), self.make_reduced.isChecked()
        # At least one copy is made: the last one ticked can't be unticked.
        for box, other in ((self.make_full, reduced), (self.make_reduced, full)):
            box.setEnabled(other)
            box.setToolTip(tooltip_text(
                "At least one copy must be made. Tick the other copy to turn this one off." if not other else
                "Keeps the final pages at their source dimensions." if box is self.make_full else
                "Creates a resized comic. With a full-size copy as well, the two are saved separately "
                "with Full and Reduced added to their names."))
        self.full_settings.setEnabled(full)
        self.reduced_settings.setEnabled(reduced)
        name = self.full_format()
        self.full_quality.setRowVisible(self.jpeg_quality, name == "JPEG")
        self.full_quality.setRowVisible(self.webp_quality, name == "WebP")
        self.webp_quality_label.setText("WebP lossless effort" if self.advanced.lossless.isChecked()
                                        else "WebP quality")
        self.advanced.format.setCurrentIndex({"JPEG": 0, "WebP": 1}.get(name, 2))
        reduced_name = self.reduced_format.currentText()
        self.reduced_advanced.format.setCurrentIndex({"JPEG": 0, "WebP": 1}.get(reduced_name, 2))
        self.reduced_quality_form.setRowVisible(self.reduced_quality, reduced_name != "PNG")
        self.reduced_quality_label.setText(
            "WebP lossless effort" if reduced_name == "WebP" and self.reduced_advanced.lossless.isChecked()
            else f"{reduced_name} quality")

    def apply_to(self, opt: ExportOptions) -> dict:
        full, reduced = self.make_full.isChecked(), self.make_reduced.isChecked()
        opt.size_output = "both" if full and reduced else "reduced" if reduced else "full"
        opt.format = self.format.currentText().lower()
        opt.spread_format = ("png", "jpeg", "webp")[self.spread_format.currentIndex()]
        opt.jpeg_quality = self.jpeg_quality.value()
        opt.webp_quality = self.webp_quality.value()
        self.advanced.apply()
        for name in ("jpeg_progressive", "jpeg_optimize", "jpeg_subsampling", "jpeg_smooth",
                     "webp_lossless", "webp_method", "webp_sharp_yuv", "png_compress"):
            setattr(opt, name, getattr(self.draft, name))
        self.reduced_advanced.apply()
        opt.reduced_custom = True  # The reduced copy always has its own settings now.
        self.reduced_draft.format = self.reduced_format.currentText().lower()
        self.reduced_draft.jpeg_quality = self.reduced_quality.value()
        self.reduced_draft.webp_quality = self.reduced_quality.value()
        opt.reduced_settings = {name: getattr(self.reduced_draft, name) for name in ENCODER_FIELDS}
        return self.resize_settings.values()
