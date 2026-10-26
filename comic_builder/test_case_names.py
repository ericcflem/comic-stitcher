"""File names that differ in capitalisation: mixed-case extensions, ComicInfo.XML,
and pages whose names are the same apart from capitalisation (possible on Linux
and inside archives)."""
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

from PIL import Image

from .archive_import import import_archive
from .backend import BookPlan, scan_manual_folder
from .export import ExportOptions, planned_names
from .detector import name_sort_key

TWINS = [f"comic - {n:03d}.{ext}" for n in range(1, 6) for ext in ("jpg", "JPG")]  # Eric's test set.


def case_sensitive(folder: Path) -> bool:
    probe = folder / "case-probe"
    probe.touch()
    try:
        return not (folder / "CASE-PROBE").exists()
    finally:
        probe.unlink()


def page(path: Path, shade: int):
    Image.new("RGB", (40, 60), (shade, 0, 0)).save(path, "JPEG")


class SortOrderTests(unittest.TestCase):
    def test_twins_sort_lowercase_first_as_linux_file_managers_do(self):
        shuffled = TWINS[::-1]
        self.assertEqual(sorted(shuffled, key=name_sort_key), TWINS)

    def test_numbers_by_value_and_capitalisation_ignored_otherwise(self):
        names = ["Page 10.png", "page 2.JPG", "PAGE 1.jpg", "page 3.Png"]
        self.assertEqual(sorted(names, key=name_sort_key),
                         ["PAGE 1.jpg", "page 2.JPG", "page 3.Png", "Page 10.png"])


class FolderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_mixed_case_extensions_and_comicinfo(self):
        for n, name in enumerate(("001.JPG", "002.Jpeg", "003.jpg"), 1):
            page(self.folder / name, n * 40)
        (self.folder / "ComicInfo.XML").write_text("<ComicInfo><Title>Mixed</Title></ComicInfo>", encoding="utf-8")
        scan = scan_manual_folder(self.folder)
        self.assertEqual([p.path.name for p in scan.pages], ["001.JPG", "002.Jpeg", "003.jpg"])
        plan = BookPlan(scan)
        self.assertIn("Mixed", plan.comicinfo_xml)
        # Copied pages keep their format, with the usual lowercase extension.
        names = planned_names(plan, ExportOptions("Book", keep_originals=True))
        self.assertEqual(names, ["01.jpg", "02.jpeg", "03.jpg"])
        self.assertEqual(scan.case_twins, [])

    def test_names_differing_only_in_capitalisation_are_kept_in_order(self):
        if not case_sensitive(self.folder):
            self.skipTest("this file system ignores capitalisation")
        for shade, name in enumerate(TWINS):
            page(self.folder / name, shade * 20)
        scan = scan_manual_folder(self.folder)
        self.assertEqual([p.path.name for p in scan.pages], TWINS)
        self.assertEqual(len(scan.case_twins), 5)
        self.assertEqual(scan.case_twins[0], (("comic - 001.jpg", "comic - 001.JPG"), False))

    def test_identical_twins_are_reported_as_copies(self):
        if not case_sensitive(self.folder):
            self.skipTest("this file system ignores capitalisation")
        page(self.folder / "comic - 001.jpg", 10)
        (self.folder / "comic - 001.JPG").write_bytes((self.folder / "comic - 001.jpg").read_bytes())
        page(self.folder / "comic - 002.jpg", 30)
        scan = scan_manual_folder(self.folder)
        self.assertEqual(len(scan.pages), 3)
        self.assertEqual(scan.case_twins, [(("comic - 001.jpg", "comic - 001.JPG"), True)])


class ArchiveTests(unittest.TestCase):
    """Archives can hold twins on any system; staged pages are numbered, so Windows copes too."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        self.images = {}
        for shade, name in enumerate(TWINS):
            source = self.folder / f"source-{shade}.jpg"
            page(source, shade * 20)
            self.images[name] = source.read_bytes()

    def tearDown(self):
        self._tmp.cleanup()

    def check(self, archive: Path):
        scan = import_archive(archive, self.folder / f"stage-{archive.suffix[1:]}", manual=True)
        self.assertEqual([p.path.read_bytes() for p in scan.pages], [self.images[name] for name in TWINS])
        self.assertEqual(len(scan.case_twins), 5)
        self.assertEqual(scan.case_twins[-1], (("comic - 005.jpg", "comic - 005.JPG"), False))

    def test_zip(self):
        archive = self.folder / "twins.cbz"
        with zipfile.ZipFile(archive, "w") as writer:
            for name in reversed(TWINS):  # The archive's own order doesn't decide.
                writer.writestr(name, self.images[name])
        self.check(archive)

    def test_7z_extracts_twins_without_overwriting(self):
        import py7zr
        archive = self.folder / "twins.cb7"
        with py7zr.SevenZipFile(archive, "w") as writer:
            for name in TWINS:
                writer.writestr(self.images[name], name)
        self.check(archive)

    def test_the_same_name_twice_is_still_refused(self):
        archive = self.folder / "repeated.cbz"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # zipfile warns about the duplicate name.
            with zipfile.ZipFile(archive, "w") as writer:
                writer.writestr("001.jpg", self.images[TWINS[0]])
                writer.writestr("001.jpg", self.images[TWINS[2]])
        with self.assertRaisesRegex(ValueError, "same image path twice"):
            import_archive(archive, self.folder / "stage")


if __name__ == "__main__":
    unittest.main()


class PageNoteTests(unittest.TestCase):
    """Notes from page names: a gap in the numbering (a page may be missing) and
    names differing only in capitalisation, by their page in reading order."""

    def test_numbering_gaps_are_found_only_in_plainly_numbered_books(self):
        from .backend import missing_numbers
        self.assertEqual(missing_numbers(["014.jpg", "015.jpg", "017.jpg", "018.jpg"]), [(2, 1)])
        self.assertEqual(missing_numbers(["Book 01.jpg", "Book 02-03.jpg", "Book 04.jpg", "Book 07.jpg"]), [(3, 2)])
        for names in (["001.jpg", "002.jpg", "003.jpg"],          # Complete.
                      ["cover.jpg", "001.jpg", "003.jpg"],       # Not every name numbered.
                      ["a01.jpg", "b03.jpg"],                    # Different prefixes.
                      ["001.jpg", "003.jpg", "005.jpg", "007.jpg", "009.jpg"]):  # Numbered by twos.
            self.assertEqual(missing_numbers(names), [], names)

    def test_notes_name_pages_in_reading_order(self):
        from .backend import describe_page_notes
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            names = ["001.jpg", "002.jpg", "004.jpg", "005.jpg"]
            files = [folder / name for name in names]
            for index, path in enumerate(files):
                page(path, index * 40)
            self.assertEqual(describe_page_notes(names, files),
                             [((1, 2), "File numbers jump from 002.jpg to 004.jpg: a page may be missing between them")])
            # Back to front: the same files, read from the last.
            self.assertEqual(describe_page_notes(names, files, right_to_left=True)[0][0], (1, 2))
            self.assertEqual(describe_page_notes(names[:3] + ["007.jpg"], files)[1][1],
                             "File numbers jump from 004.jpg to 007.jpg: 2 pages may be missing between them")

    def test_folder_and_archive_imports_note_a_missing_page(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "pages"
            folder.mkdir()
            names = ["Book 01.jpg", "Book 02.jpg", "Book 04.jpg"]
            for index, name in enumerate(names):
                page(folder / name, index * 60)
            scan = scan_manual_folder(folder)
            self.assertEqual(scan.page_notes,
                             [((1, 2), "File numbers jump from Book 02.jpg to Book 04.jpg: a page may be missing between them")])
            archive = Path(directory) / "Book.cbz"
            with zipfile.ZipFile(archive, "w") as zf:
                for name in names:
                    zf.write(folder / name, f"Book/{name}")
            staged = import_archive(archive, Path(directory) / "stage", manual=True)
            self.assertEqual(staged.page_notes, scan.page_notes)  # By the names in the archive.
