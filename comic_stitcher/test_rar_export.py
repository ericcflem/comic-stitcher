"""CBR export through RARLAB's RAR, found or chosen by the user."""
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from .backend import BookPlan, scan_manual_folder
from .export import ExportOptions, export_book
from . import rar_export
from .rar_export import RarProgram, find_rar, identify, write_cbr

RAR5_SIGNATURE = b"Rar!\x1a\x07\x01\x00"
RAR4_SIGNATURE = b"Rar!\x1a\x07\x00"


class IdentifyTests(unittest.TestCase):
    def fake(self, folder, banner):
        """A program that prints `banner`, as RAR prints its own when run alone."""
        path = Path(folder) / "rar"
        path.write_text("#!/bin/sh\ncat <<'EOF'\n" + banner + "\nEOF\n")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return path

    @unittest.skipIf(os.name == "nt", "uses a shell script as a stand-in program")
    def test_only_rarlabs_rar_5_or_newer_is_used(self):
        with tempfile.TemporaryDirectory() as folder:
            for banner, expected in (
                    ("\nRAR 7.13     Copyright (c) 1993-2025 Alexander Roshal   1 Aug 2025\nTrial version", (7, 13)),
                    ("RAR 6.24 x64   Copyright (c) 1993-2023 Alexander Roshal", (6, 24)),
                    ("\nUNRAR 7.13 freeware      Copyright (c) 1993-2025 Alexander Roshal", None),
                    ("RAR 4.20   Copyright (c) 1993-2012 Alexander Roshal", None),
                    ("7-Zip 24.09 (x64)", None)):
                with self.subTest(banner=banner):
                    program = identify(self.fake(folder, banner))
                    self.assertEqual(program and program.version, expected)
            self.assertIsNone(identify(Path(folder) / "missing"))
            self.assertIsNone(identify(""))

    @unittest.skipIf(os.name == "nt", "uses a shell script as a stand-in program")
    def test_a_chosen_program_is_used_instead_of_searching(self):
        with tempfile.TemporaryDirectory() as folder:
            chosen = self.fake(folder, "RAR 6.02   Copyright (c) 1993-2021 Alexander Roshal")
            with patch.object(rar_export, "candidates", side_effect=AssertionError("searched")):
                self.assertEqual(find_rar(str(chosen)), RarProgram(str(chosen), (6, 2)))
            with patch.object(rar_export, "candidates", return_value=[str(Path(folder) / "none"), str(chosen)]):
                self.assertEqual(find_rar(), RarProgram(str(chosen), (6, 2)))

    def test_rar4_only_up_to_rar_6(self):
        self.assertTrue(RarProgram("rar", (6, 24)).can_make_rar4)
        self.assertFalse(RarProgram("rar", (7, 0)).can_make_rar4)
        self.assertEqual(RarProgram("rar", (7, 1)).label, "RAR 7.01")

    def test_cbr_without_rar_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            Image.new("RGB", (40, 60), (10, 20, 30)).save(Path(folder) / "001.png")
            plan = BookPlan(scan_manual_folder(Path(folder)))
            with self.assertRaisesRegex(ValueError, "RAR program"):
                export_book(plan, ExportOptions("Book", archive_format="cbr", workers=1))


RAR = find_rar()


@unittest.skipUnless(RAR, "RARLAB's RAR is not installed")
class RealRarTests(unittest.TestCase):
    def listing(self, archive):
        output = subprocess.run([RAR.path, "lb", "-cfg-", "--", str(archive)], capture_output=True, check=True)
        return [line.strip() for line in output.stdout.decode(errors="replace").splitlines() if line.strip()]

    def test_pages_stored_in_a_tested_rar5_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "pages"
            folder.mkdir()
            names = [f"Book - {i:03d}.png" for i in range(1, 4)] + ["ComicInfo.xml"]
            for i, name in enumerate(names[:3]):
                Image.new("RGB", (40, 60), (i * 60, 20, 30)).save(folder / name)
            (folder / "ComicInfo.xml").write_text("<ComicInfo/>")
            archive = Path(directory) / "Book.cbr"
            write_cbr(RAR.path, folder, names, archive)
            self.assertTrue(archive.read_bytes().startswith(RAR5_SIGNATURE))
            self.assertEqual(sorted(self.listing(archive)), sorted(names))
            if RAR.can_make_rar4:
                older = Path(directory) / "Older.cbr"
                write_cbr(RAR.path, folder, names, older, rar4=True)
                self.assertTrue(older.read_bytes().startswith(RAR4_SIGNATURE))

    def test_export_makes_a_cbr_beside_the_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "Book"
            folder.mkdir()
            for i in range(3):
                Image.new("RGB", (40, 60), (i * 60, 20, 30)).save(folder / f"{i + 1:03d}.png")
            plan = BookPlan(scan_manual_folder(folder))
            output = export_book(plan, ExportOptions("Book", archive_format="cbr", rar_program=RAR.path,
                                                     format="png", workers=1))
            self.assertEqual(output.suffix, ".cbr")
            self.assertTrue(output.read_bytes().startswith(RAR5_SIGNATURE))
            self.assertEqual(len(self.listing(output)), 3)
            # Nothing left behind but the comic.
            self.assertEqual(sorted(p.name for p in output.parent.iterdir()), ["Book", "Book.cbr"])

    def test_a_failure_is_reported_and_no_archive_published(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            with self.assertRaisesRegex(ValueError, "RAR couldn't make the CBR"):
                write_cbr(RAR.path, folder, ["missing.png"], folder / "Book.cbr")
