"""Plain-language file problems, and checking for disk space first."""
import errno
import os
import tempfile
import unittest
import zipfile
from collections import namedtuple
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from .backend import BookPlan, scan_manual_folder
from .export import ExportOptions
from .problems import cannot_write, explain, message_for
from .space import export_need, import_need, shortfall


def wrapped(error):
    """As export reports it: the OS error inside another."""
    try:
        try:
            raise error
        except OSError as inner:
            raise RuntimeError("Export stopped: " + str(inner)) from inner
    except RuntimeError as outer:
        return outer


class ExplainTests(unittest.TestCase):
    def test_common_problems_in_plain_words(self):
        for error, kind, words in (
                (OSError(errno.ENOSPC, "No space left on device", "/comics/Book/007.png"), "disk full",
                 "isn't enough free space"),
                (PermissionError(errno.EACCES, "Permission denied", "/media/disc/Book"), "access",
                 "isn't allowed to change this location"),
                (FileNotFoundError(errno.ENOENT, "No such file or directory", "/comics/gone.png"), "missing",
                 "is gone"),
                (OSError(errno.ENAMETOOLONG, "File name too long", "/x"), "long path", "260 characters")):
            with self.subTest(kind):
                problem = explain(wrapped(error))
                self.assertEqual(problem.kind, kind)
                self.assertIn(words, problem.text)
                self.assertIn(error.filename, problem.text)
        self.assertIsNone(explain(ValueError("Choose a PDF file.")))
        self.assertEqual(message_for(ValueError("Choose a PDF file.")), ("Choose a PDF file.", "other"))

    @unittest.skipUnless(os.name == "nt", "Windows error codes")
    def test_windows_error_codes(self):
        for winerror, kind in ((32, "in use"), (112, "disk full"), (5, "access"), (53, "network"),
                               (362, "cloud"), (206, "long path")):
            with self.subTest(winerror):
                self.assertEqual(explain(OSError(None, "message", "C:\\Comics\\Book.cbz", winerror)).kind, kind)


class WritableTests(unittest.TestCase):
    def test_a_folder_that_takes_files_and_ones_that_dont(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(cannot_write(Path(folder)))
            self.assertEqual(list(Path(folder).iterdir()), [])  # The check leaves nothing behind.
            self.assertEqual(cannot_write(Path(folder) / "gone").kind, "missing")

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                     "needs a folder this user can't write to")
    def test_read_only_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            locked = Path(folder) / "locked"
            locked.mkdir()
            locked.chmod(0o555)
            try:
                self.assertEqual(cannot_write(locked).kind, "access")
            finally:
                locked.chmod(0o755)


class SpaceTests(unittest.TestCase):
    def test_import_needs(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "Book.cbz"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("001.jpg", b"x" * 5000)
                z.writestr("002.jpg", b"x" * 7000)
            self.assertEqual(import_need(archive), 12000)
            other = Path(folder) / "Book.cb7"
            other.write_bytes(b"x" * 1000)
            self.assertEqual(import_need(other), 1050)

    def test_export_needs_pages_and_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            for i in range(2):
                Image.new("RGB", (100, 150), "white").save(Path(folder) / f"{i:03d}.png")
            plan = BookPlan(scan_manual_folder(Path(folder)))
            copied = sum(page.size for page in plan.scan.pages)
            opt = ExportOptions("Book", keep_originals=True, cbz=True)
            self.assertEqual(export_need(plan, opt), 2 * copied)  # Pages, then the archive of them.
            self.assertEqual(export_need(plan, ExportOptions("Book", keep_originals=True, cbz=False)), copied)
            self.assertEqual(export_need(plan, ExportOptions("Book", format="png", cbz=False)), 2 * 100 * 150 * 2)

    def test_shortfall(self):
        usage = namedtuple("usage", "total used free")
        with tempfile.TemporaryDirectory() as folder:
            with patch("comic_stitcher.space.shutil.disk_usage", return_value=usage(0, 0, 500 * 2 ** 20)):
                self.assertEqual(shortfall(Path(folder) / "not yet", 300 * 2 ** 20), 500 * 2 ** 20)
                self.assertIsNone(shortfall(Path(folder), 100 * 2 ** 20))
