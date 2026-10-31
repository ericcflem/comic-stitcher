"""Help, offline: the User Guide and the keyboard shortcuts, in windows of their own.

The guide is the same docs/USER_GUIDE.md installed with the app (and shown on
GitHub), read with Qt's own Markdown support, so there's nothing to convert
when building and nothing to keep in step. Its contents links work inside the
window; web links open in the browser. The shortcuts window shows the guide's
own "Keyboard shortcuts" table, so the two can't disagree.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut, QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtWidgets import (QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser,
                               QVBoxLayout)

from ..window_outline import OutlinedDialog

GUIDE = "USER_GUIDE.md"
SHORTCUTS_HEADING = "## Keyboard shortcuts"


def app_root() -> Path:
    """Where the app's documents are: beside ComicStitcher.exe when installed,
    else the source folder (which a Linux install copies as it is)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


def guide_path() -> Path:
    return app_root() / "docs" / GUIDE


def slug(heading: str) -> str:
    """A heading's anchor, as GitHub makes it: "Batches and the queue" -> batches-and-the-queue."""
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


def shortcuts_markdown(guide: str) -> str:
    """The guide's keyboard shortcuts section, heading and all."""
    start = guide.find(SHORTCUTS_HEADING)
    if start < 0:
        return ""
    end = guide.find("\n## ", start + len(SHORTCUTS_HEADING))
    return guide[start:] if end < 0 else guide[start:end]


def _name_headings(document: QTextDocument) -> None:
    """Give each heading the anchor GitHub gives it, so the contents links work."""
    block = document.begin()
    while block.isValid():
        if block.blockFormat().headingLevel():
            cursor = QTextCursor(block)
            cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
            mark = QTextCharFormat()
            mark.setAnchor(True)
            mark.setAnchorNames([slug(block.text())])
            cursor.mergeCharFormat(mark)
        block = block.next()


class HelpWindow(OutlinedDialog):
    """Markdown in a reading window, with Find (Ctrl+F)."""

    def __init__(self, title: str, markdown: str, parent=None, base: Path | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.base = base
        self.resize(820, 760)
        layout = QVBoxLayout(self)
        self.browser = QTextBrowser()
        self.browser.setOpenLinks(False)
        self.browser.anchorClicked.connect(self._follow)
        layout.addWidget(self.browser, 1)
        find_row = QHBoxLayout()
        find_row.addWidget(QLabel("Find:"))
        self.find_box = QLineEdit()
        self.find_box.setPlaceholderText("Words in the guide (Enter for the next one)")
        self.find_box.returnPressed.connect(self.find_next)
        find_row.addWidget(self.find_box, 1)
        layout.addLayout(find_row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        QShortcut(QKeySequence.StandardKey.Find, self, activated=self.find_box.setFocus)
        self.show_markdown(markdown)

    def show_markdown(self, markdown: str) -> None:
        document = QTextDocument(self.browser)
        document.setMarkdown(markdown)
        _name_headings(document)
        self.browser.setDocument(document)

    def _follow(self, url: QUrl) -> None:
        if url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)
        elif not url.path() and url.fragment():
            self.browser.scrollToAnchor(url.fragment())
        elif self.base is not None and url.path().lower().endswith(".md"):
            target = (self.base / url.path()).resolve()
            if target.is_file():  # Another of the installed documents (the README, say).
                self.base = target.parent
                self.show_markdown(target.read_text(encoding="utf-8"))
                if url.fragment():
                    self.browser.scrollToAnchor(url.fragment())

    def find_next(self) -> None:
        text = self.find_box.text()
        if text and not self.browser.find(text):
            self.browser.moveCursor(QTextCursor.MoveOperation.Start)  # Round to the first.
            self.browser.find(text)


def _missing(parent) -> HelpWindow:
    return HelpWindow("User Guide", f"The User Guide wasn't found. It should be at:\n\n`{guide_path()}`", parent)


def show_guide(parent) -> HelpWindow:
    path = guide_path()
    window = (HelpWindow("Comic Stitcher User Guide", path.read_text(encoding="utf-8"), parent, path.parent)
              if path.is_file() else _missing(parent))
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    window.show()  # Beside the main window, so the two can be used together.
    return window


def show_shortcuts(parent) -> HelpWindow:
    path = guide_path()
    text = shortcuts_markdown(path.read_text(encoding="utf-8")) if path.is_file() else ""
    window = HelpWindow("Keyboard shortcuts", text, parent, path.parent) if text else _missing(parent)
    window.resize(620, 520)
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    window.show()
    return window


class NoticesWindow(OutlinedDialog):
    """The third-party notices (licenses/index.html and the files it lists),
    read inside Comic Stitcher. The AppImage uses this: its files are in a
    folder only it can see while running, which a browser in a sandbox (Ubuntu's
    Firefox, say) can't open."""

    def __init__(self, index: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Third-party notices")
        self.resize(900, 760)
        self.root = index.parent.resolve()
        self.history: list[Path] = []
        layout = QVBoxLayout(self)
        self.browser = QTextBrowser()
        self.browser.setOpenLinks(False)
        self.browser.anchorClicked.connect(self._follow)
        layout.addWidget(self.browser, 1)
        row = QHBoxLayout()
        self.back = QPushButton("Back")
        self.back.clicked.connect(self.go_back)
        row.addWidget(self.back)
        row.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        row.addWidget(buttons)
        layout.addLayout(row)
        self.current = index.resolve()
        self.show_file(self.current)

    def show_file(self, path: Path) -> None:
        self.current = path
        text = path.read_text(encoding="utf-8", errors="replace")
        suffix = path.suffix.lower()
        if suffix in (".html", ".htm"):
            self.browser.setHtml(text)
        elif suffix == ".md":
            self.browser.setMarkdown(text)
        else:
            self.browser.setPlainText(text)  # Licence texts, whatever angle brackets they hold.
        self.back.setEnabled(bool(self.history))

    def _follow(self, url: QUrl) -> None:
        if url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)
            return
        target = (self.current.parent / url.path()).resolve()
        if target.is_file() and target.is_relative_to(self.root):  # Only the notices themselves.
            self.history.append(self.current)
            self.show_file(target)

    def go_back(self) -> None:
        if self.history:
            self.show_file(self.history.pop())


def show_notices(index: Path, parent) -> NoticesWindow:
    window = NoticesWindow(index, parent)
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    window.show()
    return window
