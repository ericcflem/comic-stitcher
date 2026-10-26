"""Line up spread: the Seam tools for overlapping or bordered halves."""
from __future__ import annotations

from PIL import Image
from PySide6.QtCore import QEvent, Qt, Signal, QRectF
from PySide6.QtGui import QPixmap, QShortcut, QKeySequence, QPainter, QColor, QPen
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QVBoxLayout, QWidget, QDialogButtonBox)
from ..backend import measure_overlap, thin_border_between
from ..window_outline import OutlinedDialog
from .common import interface_icon, pixmap_from_pil, tooltip_text, ui_heading, ui_primary


class SideSwitch(QWidget):
    """A sliding switch between two sides, both equally "on": the knob shows
    which side is chosen, and a click moves it to the other."""
    toggled = Signal(str)

    def __init__(self, side: str = "left", parent=None):
        super().__init__(parent)
        self.side = side
        self.setFixedSize(46, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)

    def set_side(self, side: str):
        if side != self.side:
            self.side = side
            self.update()

    def mousePressEvent(self, event):
        self.set_side("right" if self.side == "left" else "left")
        self.toggled.emit(self.side)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Left, Qt.Key_Right):
            wanted = "left" if event.key() == Qt.Key_Left else "right"
            if wanted != self.side:
                self.set_side(wanted)
                self.toggled.emit(self.side)
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        track = QRectF(1, 1, self.width() - 2, self.height() - 2)
        painter.setPen(QPen(QColor("#1266b6"), 1))
        painter.setBrush(QColor("#1976cf"))
        painter.drawRoundedRect(track, track.height() / 2, track.height() / 2)
        knob = track.height() - 4
        x = track.left() + 2 if self.side == "left" else track.right() - 2 - knob
        painter.setPen(QPen(QColor("#d0d7de"), 1))
        painter.setBrush(QColor("white"))
        painter.drawEllipse(QRectF(x, track.top() + 2, knob, knob))
        if self.hasFocus():
            painter.setPen(QPen(QColor("#ffb300"), 1.5))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(track, track.height() / 2, track.height() / 2)


class OverlapDialog(OutlinedDialog):
    """Line up a spread whose halves both repeat a strip of art at the seam.

    Shows the seam of the joined spread as it will be saved. The halves move
    together a pixel at a time; Find overlap measures the repeated strip; the
    page on top covers the other where they overlap (hiding a thin border).
    """

    def __init__(self, left: Image.Image, right: Image.Image, overlap: tuple[int, str], parent=None,
                 accept_text: str = "OK", find_on_open: bool | None = None):
        super().__init__(parent)
        self.setWindowTitle("Line up spread")
        self.show_line = False  # Enter shows a line where the page on top ends.
        self.resize(760, 760)
        self.left, self.right = left, right
        self.height_ = min(left.height, right.height)
        self.strip = max(1, min(240, left.width // 3, right.width // 3))
        layout = QVBoxLayout(self)
        layout.addWidget(ui_heading("Line up the halves", 20))
        note = QLabel("If this spread was cut from a page narrower than two pages, both halves repeat a "
                      "strip of art at the seam, or one has a thin border there. Move them together "
                      "until the art lines up, or use Find overlap. This is the seam as the halves "
                      "will be joined.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.view = QScrollArea()
        self.view.setWidgetResizable(False)
        self.view.setAlignment(Qt.AlignHCenter)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.view.setWidget(self.image)
        self.image.setCursor(Qt.PointingHandCursor)
        self.image.installEventFilter(self)  # Click a half to bring it to the front.
        layout.addWidget(self.view, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel("Overlap"))
        self.apart = QPushButton("Apart")
        self.apart.setIcon(interface_icon("split"))
        self.apart.setToolTip(tooltip_text("Move the halves one pixel apart (O, for out)."))
        self.apart.clicked.connect(lambda: self.pixels.stepBy(-1))
        row.addWidget(self.apart)
        self.pixels = QSpinBox()
        self.pixels.setRange(0, min(left.width, right.width) - 1)
        self.pixels.setSuffix(" px")
        self.pixels.setValue(overlap[0])
        self.pixels.valueChanged.connect(self.render)
        row.addWidget(self.pixels)
        self.together = QPushButton("Together")
        self.together.setIcon(interface_icon("merge"))
        self.together.setToolTip(tooltip_text("Move the halves one pixel closer together (I, for in)."))
        self.together.clicked.connect(lambda: self.pixels.stepBy(1))
        row.addWidget(self.together)
        self.find = QPushButton("Find overlap")
        self.find.setToolTip(tooltip_text(
            "Looks for the strip of art both halves repeat, by comparing the start of the right page "
            "with the end of the left one, and sets the overlap to match."))
        self.find.clicked.connect(self.find_overlap)
        row.addWidget(self.find)
        row.addSpacing(16)
        self.top = overlap[1]
        row.addWidget(QLabel("Page on top:"))
        self.top_labels = {}
        self.top_switch = SideSwitch(self.top)
        self.top_switch.toggled.connect(self.set_top)
        for side in ("left", "right"):
            label = QLabel(side.capitalize())
            label.setCursor(Qt.PointingHandCursor)
            label.installEventFilter(self)  # Clicking a side's name chooses it too.
            self.top_labels[side] = label
        row.addWidget(self.top_labels["left"])
        row.addWidget(self.top_switch)
        row.addWidget(self.top_labels["right"])
        tip = tooltip_text("Where the halves overlap, the page on top covers the other. Click the switch "
                           "or a side's name, or click a half of the seam view to bring it to the front. "
                           "Put the page with the clean edge on top, so a thin border on the other is hidden.")
        for widget in (self.top_switch, *self.top_labels.values()):
            widget.setToolTip(tip)
        row.addStretch()
        layout.addLayout(row)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        keys = QLabel("Keys: I together · O apart · ← → page on top · Enter seam line · Space done · Esc cancel")
        keys.setStyleSheet("color:#5f6b76;")
        layout.addWidget(keys)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.Ok)
        self.ok_button.setText(accept_text)
        for button in buttons.buttons():
            button.setAutoDefault(False)  # Enter toggles the seam line instead.
            button.setDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        ui_primary(self.ok_button)
        layout.addWidget(buttons)
        for key, action in ((Qt.Key_Left, lambda: self.set_top("left")),
                            (Qt.Key_Right, lambda: self.set_top("right")),
                            (Qt.Key_Return, self.toggle_line), (Qt.Key_Enter, self.toggle_line),
                            (Qt.Key_I, lambda: self.pixels.stepBy(1)),  # In: together.
                            (Qt.Key_O, lambda: self.pixels.stepBy(-1)),  # Out: apart.
                            (Qt.Key_Space, self.accept)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.activated.connect(action)
        if overlap[0] == 0 if find_on_open is None else find_on_open:
            self.find_overlap()
        self.render()
        self.view.setFocus(Qt.OtherFocusReason)  # Up and Down scroll the seam.

    def overlap(self) -> tuple[int, str]:
        return self.pixels.value(), self.top

    def set_top(self, top: str):
        self.top = top
        self.top_switch.set_side(top)
        self.render()

    def toggle_line(self):
        self.show_line = not self.show_line
        self.render()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.MouseButtonPress:
            if watched is self.image:
                self.set_top("left" if event.position().x() < self.image.width() / 2 else "right")
                return True
            for side, label in getattr(self, "top_labels", {}).items():
                if watched is label:
                    self.set_top(side)
                    return True
        return super().eventFilter(watched, event)

    def find_overlap(self):
        found = measure_overlap(self.left, self.right)
        border = thin_border_between(self.left, self.right)
        if found is not None:
            self.status.setText(f"Found a strip of {found} px repeated on both halves; the overlap is set to match.")
            self.pixels.setValue(found)
            if border:
                self.set_top(border[1])
        elif border is not None:
            pixels, top = border
            covered = "left" if top == "right" else "right"
            self.status.setText(f"Found a {pixels} px border on the {covered} page at the seam; the {top} "
                                "page now covers it.")
            self.pixels.setValue(pixels)
            self.set_top(top)
        else:
            self.status.setText("No repeated strip of art or thin border was found at the seam. If the halves "
                                "overlap, move them together by hand.")

    def seam_image(self) -> Image.Image:
        """The joined spread around the seam, as stitched_image lays it out. Centred
        on the middle of the overlap, so swapping the page on top doesn't move it."""
        pixels, top = self.overlap()
        width, height = self.left.width, self.height_
        middle = width - pixels // 2
        x0, x1 = middle - self.strip, middle + self.strip
        canvas = Image.new("RGB", (x1 - x0, height), "white")
        halves = [(self.left, 0), (self.right, width - pixels)]
        for image, offset in (halves[::-1] if top == "left" else halves):
            start, end = max(0, x0 - offset), min(image.width, x1 - offset)
            if end > start:
                canvas.paste(image.crop((start, 0, end, height)), (offset + start - x0, 0))
        return canvas

    def render(self, *_):
        scroll = self.view.verticalScrollBar().value()
        image = self.seam_image()
        pix = pixmap_from_pil(image, (900, 6000))
        if self.show_line:
            # Where the page on top ends: what is left of the seam once the halves are joined.
            pixels, top = self.overlap()
            edge = self.left.width if top == "left" else self.left.width - pixels
            x = round((edge - (self.left.width - pixels // 2 - self.strip)) * pix.width() / image.width)
            painter = QPainter(pix)
            painter.setPen(QPen(QColor("#ffe500"), 1))
            painter.drawLine(x, 0, x, pix.height() - 1)
            painter.end()
        self.image.setPixmap(pix)
        self.image.resize(pix.size())
        self.view.verticalScrollBar().setValue(scroll)
        self.apart.setEnabled(self.pixels.value() > self.pixels.minimum())
        self.together.setEnabled(self.pixels.value() < self.pixels.maximum())
        for side, label in self.top_labels.items():
            label.setStyleSheet("font-weight:600; color:#1976cf;" if side == self.top else "color:#5f6b76;")


class JoinedPairView(QWidget):
    """Draw the two source pages at one scale, with their seam touching, or with
    the right page overlapping the left by `overlap` source pixels."""

    def __init__(self):
        super().__init__()
        self.left: QPixmap | None = None
        self.right: QPixmap | None = None
        self.page_sizes: tuple[tuple[int, int], tuple[int, int]] | None = None
        self.message = "Loading pages…"
        self.rotation = 0
        self.overlap = 0
        self.top = "left"
        self.setMinimumSize(160, 120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def load_pair(self, left_size: tuple[int, int], right_size: tuple[int, int], overlap: int = 0,
                  top: str = "left"):
        self.page_sizes = (left_size, right_size)
        self.overlap, self.top = overlap, top
        self.left = self.right = None
        self.message = "Loading pages…"
        self.update()

    def show_pair(self, left: QPixmap, right: QPixmap):
        self.left, self.right = left, right
        self.update()

    def show_error(self):
        self.left = self.right = None
        self.message = "Could not load pages"
        self.update()

    def image_rects(self) -> tuple[QRectF, QRectF] | None:
        if not self.page_sizes:
            return None
        (left_width, left_height), (right_width, right_height) = self.page_sizes
        common_height = min(left_height, right_height)
        if not common_height or not self.width() or not self.height():
            return None
        total = left_width + right_width - self.overlap
        scale = min(self.width() / total, self.height() / common_height)
        x = (self.width() - total * scale) / 2
        y = (self.height() - common_height * scale) / 2
        left_rect = QRectF(x, y, left_width * scale, common_height * scale)
        right_rect = QRectF(left_rect.right() - self.overlap * scale, y, right_width * scale, common_height * scale)
        return left_rect, right_rect

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#20242a"))
        if self.rotation and self.page_sizes and self.left is not None and self.right is not None:
            (lw, lh), (rw, rh) = self.page_sizes
            common = min(lh, rh)
            width = lw + rw - self.overlap
            turned_width, turned_height = (common, width) if self.rotation % 180 else (width, common)
            scale = min(self.width() / turned_width, self.height() / turned_height)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.translate(self.width() / 2, self.height() / 2)
            painter.rotate(self.rotation)
            painter.translate(-width * scale / 2, -common * scale / 2)
            halves = ((self.right, lw - self.overlap, rw, rh), (self.left, 0, lw, lh))
            for pixmap, x, page_width, height in (halves if self.top == "left" else halves[::-1]):
                painter.drawPixmap(QRectF(x * scale, 0, page_width * scale, common * scale),
                                   pixmap, QRectF(0, 0, pixmap.width(), pixmap.height() * common / height))
            return
        rects = self.image_rects()
        if not rects or self.left is None or self.right is None:
            painter.setPen(QColor("#d6dde5"))
            painter.drawText(self.rect(), Qt.AlignCenter, self.message)
            return
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        # Draw the page on top last, as the halves will be joined.
        halves = list(zip((self.left, self.right), rects, self.page_sizes, strict=True))
        for pixmap, destination, (_, source_height) in (halves[::-1] if self.top == "left" else halves):
            # Export crops the taller page at the bottom. Show the same join.
            common_height = min(height for _, height in self.page_sizes)
            source = QRectF(0, 0, pixmap.width(), pixmap.height() * common_height / source_height)
            painter.drawPixmap(destination, pixmap, source)
