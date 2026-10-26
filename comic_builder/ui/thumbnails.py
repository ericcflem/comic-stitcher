"""The final review's thumbnail strip, its drag and drop, and the queue list."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QMimeData
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QApplication, QListWidget, QPushButton, QWidget


class QueueList(QListWidget):
    """The batch queue: drag a comic, or press Ctrl with the arrow keys, Home or
    End, to change the order comics are exported in."""
    reordered = Signal()

    def __init__(self):
        super().__init__()
        self.setDragDropMode(QListWidget.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QListWidget.SingleSelection)

    def dropEvent(self, event):
        super().dropEvent(event)
        self.reordered.emit()

    def move_current(self, target: int):
        row = self.currentRow()
        target = max(0, min(self.count() - 1, target))
        if row < 0 or target == row:
            return
        item = self.takeItem(row)
        self.insertItem(target, item)
        self.setCurrentRow(target)
        self.reordered.emit()

    def keyPressEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            row = self.currentRow()
            moves = {Qt.Key_Up: row - 1, Qt.Key_Down: row + 1, Qt.Key_Home: 0, Qt.Key_End: self.count() - 1}
            if event.key() in moves:
                self.move_current(moves[event.key()])
                return
        super().keyPressEvent(event)


# Dragging a page within this many pixels of either end of the thumbnail strip
# scrolls it, from DRAG_SCROLL_MIN up to DRAG_SCROLL_MAX pixels every
# DRAG_SCROLL_INTERVAL milliseconds, faster the closer to the end.
DRAG_SCROLL_ZONE = 64


DRAG_SCROLL_MIN = 6


DRAG_SCROLL_MAX = 48


DRAG_SCROLL_INTERVAL = 30


class ThumbnailStrip(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        self.dragMoveEvent(event)

    def dragMoveEvent(self, event):
        if self.owner.update_page_drop(event.source(), event.position().x()):
            event.setDropAction(Qt.MoveAction); event.accept()
        else: event.ignore()

    def dragLeaveEvent(self, event):
        self.owner.page_drag_left()
        event.accept()

    def dropEvent(self, event):
        if self.owner.finish_page_drop(event.source(), event.position().x()):
            event.setDropAction(Qt.MoveAction); event.accept()
        else: event.ignore()


class PageThumbnail(QPushButton):
    """Drag to an insertion boundary, keeping joined spreads intact."""
    def __init__(self, index, owner):
        super().__init__(str(index + 1))
        self.index = index
        self.owner = owner
        self.setAcceptDrops(True)
        self.press_position = None

    def mousePressEvent(self, event):
        self.press_position = event.position().toPoint() if event.button() == Qt.LeftButton else None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (self.press_position is not None and event.buttons() & Qt.LeftButton and
                (event.position().toPoint() - self.press_position).manhattanLength() >= QApplication.startDragDistance()):
            data = QMimeData()
            data.setData('application/x-comic-stitcher-page', str(self.index).encode())
            drag = QDrag(self); drag.setMimeData(data)
            if not self.icon().isNull(): drag.setPixmap(self.icon().pixmap(80, 80))
            self.setDown(False); self.press_position = None
            try: drag.exec(Qt.MoveAction)
            finally: self.owner.clear_page_drop()
        else:
            super().mouseMoveEvent(event)

    def dragEnterEvent(self, event):
        self.dragMoveEvent(event)

    def dragMoveEvent(self, event):
        if self.owner.update_page_drop(event.source(), self.x() + event.position().x()):
            event.setDropAction(Qt.MoveAction); event.accept()
        else: event.ignore()

    def dragLeaveEvent(self, event):
        self.owner.page_drag_left()
        event.accept()

    def dropEvent(self, event):
        if self.owner.finish_page_drop(event.source(), self.x() + event.position().x()):
            event.setDropAction(Qt.MoveAction); event.accept()
        else: event.ignore()
