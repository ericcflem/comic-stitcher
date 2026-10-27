"""The final review's thumbnail strip, its drag and drop, and the queue list."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QMimeData
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QApplication, QListWidget, QPushButton, QWidget
from .common import DropMarker


class QueueList(QListWidget):
    """The batch queue: drag a comic, or press Ctrl with the arrow keys, Home or
    End, to change the order comics are exported in."""
    reordered = Signal()

    def __init__(self):
        super().__init__()
        self.setDragDropMode(QListWidget.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QListWidget.SingleSelection)
        # Qt's own drop line differs by system and isn't drawn above the first
        # comic on Windows; the pink line shows every position the same way.
        self.setDropIndicatorShown(False)
        self.drop_marker = DropMarker(self.viewport(), horizontal=True)
        self.drop_boundary = None

    def boundary_at(self, y):
        """Where a comic dropped at this height goes: before row n, or after the last (count)."""
        for row in range(self.count()):
            if y < self.visualItemRect(self.item(row)).center().y():
                return row
        return self.count()

    def show_drop(self, boundary):
        self.drop_boundary = boundary
        if not self.count():
            self.drop_marker.hide()
            return
        if boundary == 0:
            y = self.visualItemRect(self.item(0)).top()
        elif boundary >= self.count():
            y = self.visualItemRect(self.item(self.count() - 1)).bottom() + 1
        else:
            above = self.visualItemRect(self.item(boundary - 1))
            below = self.visualItemRect(self.item(boundary))
            y = (above.bottom() + 1 + below.top()) // 2
        y = max(3, min(self.viewport().height() - 3, y))  # Fully visible at the very top and bottom.
        self.drop_marker.setGeometry(0, y - 3, self.viewport().width(), 6)
        self.drop_marker.show()
        self.drop_marker.raise_()

    def clear_drop(self):
        self.drop_boundary = None
        self.drop_marker.hide()

    def move_to_boundary(self, row, boundary):
        """Move the comic in this row to the boundary the line showed."""
        target = boundary - (1 if row < boundary else 0)
        if 0 <= row < self.count() and target != row:
            item = self.takeItem(row)
            self.insertItem(target, item)
            self.reordered.emit()
        self.setCurrentRow(min(max(target, 0), self.count() - 1))

    def dragEnterEvent(self, event):
        super().dragEnterEvent(event)
        if event.source() is self:
            event.setDropAction(Qt.MoveAction)
            event.accept()

    def dragMoveEvent(self, event):
        super().dragMoveEvent(event)  # Keeps Qt's scrolling near the list's ends.
        if event.source() is not self:
            self.clear_drop()
            event.ignore()
            return
        self.show_drop(self.boundary_at(event.position().toPoint().y()))
        event.setDropAction(Qt.MoveAction)
        event.accept()

    def dragLeaveEvent(self, event):
        self.clear_drop()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        boundary = self.boundary_at(event.position().toPoint().y())
        self.clear_drop()
        if event.source() is not self:
            event.ignore()
            return
        self.move_to_boundary(self.currentRow(), boundary)
        # The move is done; tell Qt not to remove the dragged row afterwards.
        event.setDropAction(Qt.IgnoreAction)
        event.accept()

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
