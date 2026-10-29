"""The import drop zone and the Import this file? dialog."""
from __future__ import annotations

from pathlib import Path
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QPushButton, QVBoxLayout)
from ..window_outline import OutlinedWindow
from .common import tooltip_text, ui_heading, ui_icon, ui_primary
from .theme import tone


class ImportDropZone(QFrame):
    """Clickable target for a file or folder, with a matching browse button."""

    chosen = Signal()
    folder_chosen = Signal()
    dropped = Signal(object)

    def __init__(self, heading: str, detail: str, browse: str, parent=None, *, folder_browse=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            f"ImportDropZone {{ background: {tone('drop')}; border: 2px dashed {tone('drop_border')}; "
            "border-radius: 14px; } "
            f"ImportDropZone:hover {{ background: {tone('drop_hover')}; border-color: {tone('accent')}; }} "
            "ImportDropZone QLabel { border: none; background: transparent; } ")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 20, 18, 20)
        layout.setSpacing(12)
        layout.addStretch()
        icon = QLabel()
        icon.setPixmap(ui_icon(self, "folder" if heading == "Folder of images" else "COMIC", 84))
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet(f"font-size: 84px; font-weight: 700; color: {tone('accent')};")
        icon.setMinimumHeight(100)
        icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(icon)
        title = QLabel(heading)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"font-size: 19px; font-weight: 600; color: {tone('title')};")
        title.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(title)
        subtitle = QLabel(detail)
        subtitle.setWordWrap(True)
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet(f"color: {tone('muted')};")
        subtitle.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(subtitle)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.files_button = QPushButton(browse)
        self.files_button.clicked.connect(self.chosen)
        buttons.addWidget(self.files_button)
        if folder_browse:
            self.folder_button = QPushButton(folder_browse)
            self.folder_button.clicked.connect(self.folder_chosen)
            buttons.addWidget(self.folder_button)
        buttons.addStretch()
        layout.addLayout(buttons)
        layout.addStretch()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.chosen.emit()
            event.accept()
        else:
            super().mousePressEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls()
                 if url.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            # Return control to the drag source before opening modal import dialogs.
            QTimer.singleShot(0, lambda: self.dropped.emit(paths))


# Pair review choices; the index is the setting. 1 and 2 read pages without
# scoring their edges; 2 then leaves every pair unjoined and skips to the final review.
REVIEW_MODES = ["Suggested pairs", "Manual — every neighboring pair", "None — the pages are already complete"]


NO_PAIR_REVIEW = 2


class ImportChoiceDialog(OutlinedWindow, QDialog):
    """Confirm the detected folder workflow and the pair review style."""

    def __init__(self, source: Path, *, folder: bool, screenshot_hint: bool = False,
                 sampled: int = 0, pdf_smooth_images: bool = True, parent=None):
        super().__init__(parent)
        self._setup_outline()  # GNOME on Wayland: a visible window edge
        self.setWindowTitle("Start comic import")
        self.resize(540, 310)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)
        layout.addWidget(ui_heading("Import this folder?" if folder else "Import this file?"))
        source_row = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(ui_icon(self, "folder" if folder else source.suffix[1:].upper(), 56))
        source_row.addWidget(icon)
        filename = ui_heading(source.name, 18)
        source_row.addWidget(filename, 1)
        layout.addLayout(source_row)
        if folder:
            detail = (f"Checked {sampled} sample images. "
                      + ("They look like emulator screenshots with black surroundings."
                         if screenshot_hint else "They look like ordinary comic pages."))
            if sampled == 0:
                detail = "No readable images could be sampled. The folder type could not be determined."
            hint = QLabel(detail)
            hint.setWordWrap(True)
            layout.addWidget(hint)
            row = QFormLayout()
            self.folder_type = QComboBox()
            self.folder_type.addItems(["Comic page images", "Emulator screenshots — crop and split"])
            self.folder_type.setCurrentIndex(int(screenshot_hint))
            row.addRow("Folder contains", self.folder_type)
            layout.addLayout(row)
        else:
            self.folder_type = None
            file_types = {
                ".pdf": "Comic Stitcher will check how to import this PDF before pair review.",
                ".cbz": "Pages will be extracted from this CBZ comic archive before pair review.",
                ".cb7": "Pages will be extracted from this CB7 comic archive before pair review.",
                ".cbr": "Pages will be extracted from this CBR comic archive before pair review.",
                ".zip": "Pages will be extracted from this ZIP archive before pair review.",
                ".7z": "Pages will be extracted from this 7z archive before pair review.",
                ".rar": "Pages will be extracted from this RAR archive before pair review.",
            }
            self.file_description = QLabel(file_types.get(source.suffix.lower(),
                                                         "Comic Stitcher will import this file."))
            self.file_description.setWordWrap(True)
            layout.addWidget(self.file_description)
        self.pdf_smooth_images = None
        row = QFormLayout()
        self.review_mode = QComboBox()
        self.review_mode.addItems(REVIEW_MODES)
        self.review_mode.setToolTip(tooltip_text(
            "Suggested pairs analyzes page edges and shows Likely and Possible matches. "
            "Manual shows every neighboring pair without scoring their edges. "
            "None skips pair review and goes straight to the final review, for comics whose "
            "spreads are already joined (converting a CBR to CBZ, for example); pages can "
            "still be joined, removed or rotated there. This choice resets to Suggested "
            "pairs for each comic."))
        row.addRow("Pair review", self.review_mode)
        self.right_to_left = QCheckBox("Back-to-front page order (manga)")
        self.right_to_left.setToolTip(tooltip_text(
            "Use when the front cover is the last page or image in the source. Reverse the source page "
            "sequence before pair detection, then check and stitch spreads in right-to-left order. "
            "The finished comic is numbered from the front cover onward."))
        row.addRow(self.right_to_left)
        layout.addLayout(row)
        controls = QHBoxLayout()
        controls.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        start = QPushButton("Continue")
        ui_primary(start)
        start.setDefault(True)
        start.clicked.connect(self.accept)
        controls.addWidget(cancel)
        controls.addWidget(start)
        layout.addLayout(controls)

    def screenshots(self) -> bool:
        return self.folder_type is not None and self.folder_type.currentIndex() == 1
