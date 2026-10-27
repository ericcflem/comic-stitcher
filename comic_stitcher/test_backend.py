"""Focused checks of review overlap and original-byte-preserving export."""
import tempfile
import unittest
import zipfile
import json
import os
import io
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import py7zr
from PIL import Image, ImageDraw, ImageFile
import numpy as np

from .backend import BoundaryOverrideRequired, BookPlan, Candidate, encoding_workers, scan_folder
from .export import ExportOptions, _save_image, export_book, resized_image
from .detector import classify_page, load_edge_rgb, load_rgb, seam_score, strip_stats
from .review_report import report_data, save_report
from .archive_import import import_archive
from .pdf_import import import_pdf
from .session import cached_folder_scan, read_session, restore_plan, save_session, session_path


def make_page(path: Path, offset: int):
    image = Image.new("RGB", (120, 150), "#ededed")
    drawing = ImageDraw.Draw(image)
    for y in range(4, 150, 13):
        drawing.line((0, y + offset % 5, 119, y + offset % 5), fill="#151515", width=3)
    drawing.ellipse((20 + offset % 20, 20, 75 + offset % 20, 85), fill="#5580a0")
    image.save(path, "JPEG", quality=83)


class BookFlowTest(unittest.TestCase):
    def test_custom_page_prefix_is_independent_in_both_archives_and_png_masters(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for i in range(2):
                Image.new("RGB", (40, 60), (i * 40, 70, 100)).save(folder / f"{i}.png")
            plan = BookPlan(scan_folder(folder, manual=True))
            opt = ExportOptions("Superhero v01", format="webp", size_output="both",
                                resize_pixels=30, workers=1, keep_images=True, start=0, padding=3,
                                custom_prefix="Superhero v01 - 300dpi - 2025 - digital",
                                reduced_custom=True, reduced_settings={"format": "jpeg"})
            outputs = export_book(plan, opt)
            for copy, extension in (("Full", ".webp"), ("Reduced", ".jpg")):
                archive_path = folder / f"Superhero v01 - {copy}.cbz"
                self.assertIn(archive_path, outputs)
                with zipfile.ZipFile(archive_path) as archive:
                    self.assertEqual(archive.namelist(), [f"{opt.custom_prefix} - {i:03d}{extension}" for i in range(2)])
                self.assertEqual(sorted(p.name for p in (folder / f"Superhero v01 - {copy} - PNG pages").iterdir()),
                                 [f"{opt.custom_prefix} - {i:03d}.png" for i in range(2)])
            from .export import planned_names
            opt.custom_prefix = "   "
            with self.assertRaisesRegex(ValueError, "custom page prefix"):
                planned_names(plan, opt)
            opt.custom_prefix = "Digital/Edition"
            self.assertEqual(planned_names(plan, opt)[0], "Digital_Edition - 000.webp")
            opt.custom_prefix = None
            self.assertEqual(planned_names(plan, opt)[0], "000.webp")
            opt.prefixed = True
            self.assertEqual(planned_names(plan, opt)[0], "Superhero v01 - 000.webp")

    def test_pair_geometry_excludes_other_family_but_allows_odd_halves(self):
        from .detector import PageInfo, is_candidate_pair
        def page(width, height):
            return PageInfo("test", 0, width, height, False, False, {}, {}, "borderless")
        self.assertFalse(is_candidate_pair(page(1988, 3056), page(2175, 3263)))
        self.assertFalse(is_candidate_pair(page(1988, 3056), page(2037, 3056)))
        self.assertTrue(is_candidate_pair(page(1987, 3056), page(1988, 3056)))

    def test_reduced_copy_uses_its_own_format_and_quality(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for i in range(2):
                Image.new("RGB", (80, 120), (i * 50, 80, 110)).save(folder / f"{i}.png")
            plan = BookPlan(scan_folder(folder, manual=True))
            opt = ExportOptions("Separate copies", format="png", size_output="both",
                                resize_pixels=60, workers=2, reduced_custom=True,
                                reduced_settings={"format": "jpeg", "jpeg_quality": 37})
            full, small = export_book(plan, opt)
            with zipfile.ZipFile(full) as archive:
                self.assertTrue(all(name.endswith(".png") for name in archive.namelist()))
            with zipfile.ZipFile(small) as archive:
                self.assertTrue(all(name.endswith(".jpg") for name in archive.namelist()))
                with Image.open(io.BytesIO(archive.read(archive.namelist()[0]))) as image:
                    self.assertEqual(image.size, (40, 60))
                    self.assertEqual(image.format, "JPEG")
                    reference = io.BytesIO()
                    Image.new("RGB", (40, 60), "white").save(reference, "JPEG", quality=37)
                    reference.seek(0)
                    with Image.open(reference) as expected:
                        self.assertEqual(image.quantization, expected.quantization)
            opt.reduced_custom = False
            opt.book_name = "Shared copies"
            _, small = export_book(plan, opt)
            with zipfile.ZipFile(small) as archive:
                self.assertTrue(all(name.endswith(".png") for name in archive.namelist()))

    def test_webp_edits_copy_unmodified_webp_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for i in range(4):
                Image.new("RGB", (40, 60), (i * 40, 70, 100)).save(folder / f"{i:03d}.webp")
            original = (folder / "003.webp").read_bytes()
            plan = BookPlan(scan_folder(folder, manual=True))
            plan.join(0, override_boundary=True)
            plan.set_rotation(2, 90)
            output = export_book(plan, ExportOptions("WebP edits", keep_originals=True,
                                                     spread_format="webp", workers=1))
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.namelist(), ["01.webp", "02.webp", "03.webp"])
                self.assertEqual(archive.read("03.webp"), original)
                for name, size in (("01.webp", (80, 60)), ("02.webp", (60, 40))):
                    with Image.open(io.BytesIO(archive.read(name))) as image:
                        self.assertEqual(image.format, "WEBP")
                        self.assertEqual(image.size, size)

    def test_manual_review_skips_detector_and_advances_by_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "pages"
            folder.mkdir()
            for name in ("001", "002", "004", "005", "006"):
                Image.new("RGB", (60, 80), "black").save(folder / f"{name}.png")
            with patch("comic_stitcher.backend.classify_page", side_effect=AssertionError("edge scan")), \
                 patch("comic_stitcher.backend.seam_score", side_effect=AssertionError("seam score")):
                scan = scan_folder(folder, manual=True)
            self.assertTrue(scan.manual_mode)
            self.assertEqual([p.group for p in scan.pages], [0] * 5)
            plan = BookPlan(scan)
            self.assertEqual((plan.current()[1].left, plan.current()[1].right), (0, 1))
            plan.decide(False)
            self.assertEqual((plan.current()[1].left, plan.current()[1].right), (1, 2))
            plan.decide(True)
            self.assertEqual((plan.current()[1].left, plan.current()[1].right), (3, 4))
            self.assertEqual(plan.entries(), [(0, None), (1, 2), (3, None), (4, None)])
            self.assertTrue(plan.back())
            self.assertEqual((plan.current()[1].left, plan.current()[1].right), (1, 2))
            self.assertTrue(plan.back())
            self.assertEqual((plan.current()[1].left, plan.current()[1].right), (0, 1))
            plan.decide(True)
            self.assertEqual((plan.current()[1].left, plan.current()[1].right), (2, 3))
            save_session(folder, root, plan, possible_started=False, stage="review", preview_index=0)
            data = read_session(folder, root)
            self.assertTrue(data["manual_mode"])
            self.assertTrue(all(isinstance(item, int) for item in data["history"]))
            resumed = restore_plan(cached_folder_scan(folder, data), data)
            self.assertEqual((resumed.current()[1].left, resumed.current()[1].right), (2, 3))
            resumed.decide(False)
            self.assertEqual((resumed.current()[1].left, resumed.current()[1].right), (3, 4))
            resumed.decide(False)
            self.assertIsNone(resumed.current())
            output = export_book(resumed, ExportOptions("Manual comic", keep_originals=True, workers=1))
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(len(archive.namelist()), 4)
            report = report_data(resumed)
            self.assertEqual(report["review_mode"], "manual")
            self.assertIsNone(report["candidates"][0]["continuity"])

    def test_removed_spacer_is_excluded_from_export_and_restored_after_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "pages"
            folder.mkdir()
            for i in range(4):
                Image.new("RGB", (60, 80), (i * 50, 20, 40)).save(folder / f"{i + 1:03d}.png")
            original = [(folder / f"{i + 1:03d}.png").read_bytes() for i in range(4)]
            plan = BookPlan(scan_folder(folder, manual=True))
            plan.set_rotation(1, 90)
            plan.remove_page(1)
            self.assertNotIn(1, plan.rotations)
            self.assertEqual(plan.entries(), [(0, None), (2, None), (3, None)])
            self.assertEqual((plan.current()[1].left, plan.current()[1].right), (2, 3))
            with self.assertRaises(ValueError):
                plan.remove_page(1)
            save_session(folder, root, plan, possible_started=False, stage="review", preview_index=0)
            checkpoint = read_session(folder, root)
            resumed = restore_plan(cached_folder_scan(folder, checkpoint), checkpoint)
            self.assertEqual(resumed.removed_pages, {1})
            self.assertEqual(report_data(resumed)["removed_files"], ["002.png"])
            self.assertTrue(resumed.back())
            self.assertEqual(resumed.removed_pages, set())
            self.assertEqual(resumed.rotations[1], 90)
            resumed.remove_page(1)
            output = export_book(resumed, ExportOptions("Without spacer", keep_originals=True, workers=1))
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.namelist(), ["01.png", "02.png", "03.png"])
                self.assertEqual(archive.read("02.png"), original[2])
            self.assertEqual([(folder / f"{i + 1:03d}.png").read_bytes() for i in range(4)],
                             original)

    def test_manual_pdf_and_archive_import_skip_pair_scoring(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / "p.png"
            Image.new("RGB", (72, 100), "black").save(original)
            source_pdf = root / "short.pdf"
            with Image.open(original) as first, Image.open(original) as second:
                first.save(source_pdf, "PDF", save_all=True, append_images=[second], resolution=72)
            archive = root / "short.cbz"
            with zipfile.ZipFile(archive, "w") as writer:
                writer.write(original, "001.png")
                writer.write(original, "003.png")
            with patch("comic_stitcher.backend.classify_page", side_effect=AssertionError("edge scan")):
                pdf_progress, archive_progress = [], []
                pdf_scan = import_pdf(source_pdf, root / "pdf-pages", dpi=72, workers=1,
                                      manual=True, progress=lambda n, total: pdf_progress.append((n, total)))
                cbz_scan = import_archive(archive, root / "cbz-pages", manual=True,
                                          progress=lambda n, total: archive_progress.append((n, total)))
            self.assertTrue(pdf_scan.manual_mode)
            self.assertTrue(cbz_scan.manual_mode)
            self.assertEqual(len(pdf_scan.candidates), 1)
            self.assertEqual(len(cbz_scan.candidates), 1)
            self.assertEqual(pdf_progress[-1], (4, 4))
            self.assertEqual(archive_progress[-1], (4, 4))

    def test_rotated_single_reencodes_while_other_original_remains_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "book"
            folder.mkdir()
            first, second = folder / "01.jpg", folder / "02.jpg"
            page = Image.new("RGB", (20, 30), "red")
            ImageDraw.Draw(page).rectangle((0, 15, 19, 29), fill="blue")
            page.save(first, "JPEG", quality=95)
            Image.new("RGB", (20, 30), "green").save(second, "JPEG")
            plan = BookPlan(scan_folder(folder))
            plan.set_rotation(0, 90)
            save_session(folder, root, plan, possible_started=False, stage="preview", preview_index=0)
            data = read_session(folder, root)
            restored = restore_plan(cached_folder_scan(folder, data), data)
            self.assertEqual(restored.rotations, {0: 90})
            output = export_book(restored, ExportOptions("Rotated single", keep_originals=True,
                                                         spread_format="png", workers=1))
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.namelist(), ["01.png", "02.jpg"])
                self.assertEqual(archive.read("02.jpg"), second.read_bytes())
                with Image.open(io.BytesIO(archive.read("01.png"))) as turned:
                    self.assertEqual(turned.size, (30, 20))
                    self.assertGreater(turned.getpixel((3, 10))[2], 180)
                    self.assertGreater(turned.getpixel((26, 10))[0], 180)
            restored.join(0, override_boundary=True)
            self.assertEqual(restored.rotations, {})

    def test_rotated_join_survives_resume_and_exports_both_sizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "book"
            folder.mkdir()
            Image.new("RGB", (20, 30), "red").save(folder / "01.png")
            Image.new("RGB", (20, 30), "blue").save(folder / "02.png")
            plan = BookPlan(scan_folder(folder))
            plan.join(0, override_boundary=True)
            plan.set_rotation(0, 90)
            save_session(folder, root, plan, possible_started=False, stage="preview", preview_index=0)
            data = read_session(folder, root)
            resumed = restore_plan(cached_folder_scan(folder, data), data)
            self.assertEqual(resumed.rotations, {0: 90})
            outputs = export_book(resumed, ExportOptions("Rotated", format="png", workers=1,
                                                         size_output="both", resize_pixels=20,
                                                         resize_gamma=False))
            with zipfile.ZipFile(outputs[0]) as archive:
                with Image.open(io.BytesIO(archive.read("01.png"))) as image:
                    self.assertEqual(image.size, (30, 40))
                    self.assertEqual(image.getpixel((5, 5)), (255, 0, 0))
                    self.assertEqual(image.getpixel((5, 35)), (0, 0, 255))
            with zipfile.ZipFile(outputs[1]) as archive:
                with Image.open(io.BytesIO(archive.read("01.png"))) as image:
                    self.assertEqual(image.size, (15, 20))
            resumed.split(0)
            self.assertEqual(resumed.rotations, {0: 90, 1: 90})

    def test_resume_folder_restores_decisions_undo_and_manual_joins_without_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pages = root / "book"
            pages.mkdir()
            for i in range(4):
                Image.new("RGB", (120, 150), (i * 45, 40, 60)).save(pages / f"{i + 1:03d}.png")
            scan = scan_folder(pages)
            plan = BookPlan(scan)
            plan.decisions = {0: False} if scan.candidates else {}
            plan.history = [{}] if scan.candidates else []
            plan.join(2, override_boundary=True)
            plan.deferred_possible = True
            save_session(pages, root, plan, possible_started=True,
                         stage="preview", preview_index=1)
            checkpoint = session_path(pages, root)
            self.assertTrue(checkpoint.with_suffix(".scan.json").exists())
            self.assertNotIn("folder_scan", json.loads(checkpoint.read_text()))
            saved = read_session(pages, root)
            self.assertIsNotNone(saved)
            restored = restore_plan(cached_folder_scan(pages, saved), saved)
            self.assertEqual(restored.decisions, plan.decisions)
            self.assertEqual(restored.manual_pairs, {2})
            self.assertTrue(restored.deferred_possible)
            if scan.candidates:
                self.assertTrue(restored.back())
                self.assertEqual(restored.decisions, {})
            self.assertEqual(saved["stage"], "preview")
            self.assertEqual(saved["preview_index"], 1)
            (pages / "003.png").write_bytes(b"changed")
            self.assertIsNone(read_session(pages, root))

    def test_pdf_import_renders_every_page_in_order_with_parallel_processes(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / "comic.pdf"
            originals = [Image.new("RGB", (72, 100), (i * 13, 90, 25)) for i in range(17)]
            try:
                originals[0].save(source, "PDF", save_all=True, append_images=originals[1:], resolution=72)
            finally:
                for image in originals:
                    image.close()
            values = []
            scan = import_pdf(source, folder / "rendered", dpi=72, workers=2,
                              progress=lambda done, total: values.append((done, total)))
            self.assertEqual(len(scan.pages), 17)
            self.assertEqual(scan.source_archive, source)
            self.assertEqual(scan.output_folder, folder)
            self.assertEqual([p.path.name for p in scan.pages],
                             [f"{i:04d}.png" for i in range(1, 18)])
            self.assertEqual(values[-1], (51, 51))
            with Image.open(scan.pages[0].path) as first, Image.open(scan.pages[-1].path) as last:
                self.assertNotEqual(first.getpixel((30, 40)), last.getpixel((30, 40)))
            self.assertTrue(source.is_file())

    def test_import_zip_and_7z_archive_then_export_next_to_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            original = folder / "01.png"
            Image.new("RGB", (100, 120), "red").save(original)
            pages = folder / "pages"
            pages.mkdir()
            for extension in (".cbz", ".cb7"):
                archive = folder / f"Archive{extension}"
                if extension == ".cbz":
                    with zipfile.ZipFile(archive, "w") as writer:
                        writer.write(original, "chapter/1.png")
                else:
                    with py7zr.SevenZipFile(archive, "w") as writer:
                        writer.write(original, "chapter/1.png")
                stage = pages / extension[1:]
                scan = import_archive(archive, stage)
                self.assertEqual(scan.source_archive, archive)
                self.assertEqual(scan.output_folder, folder)
                self.assertEqual(len(scan.pages), 1)
                self.assertEqual(scan.pages[0].path.read_bytes(), original.read_bytes())
                result = export_book(BookPlan(scan), ExportOptions(f"Export {extension}", keep_originals=True,
                                                                 spread_format="png", workers=1))
                self.assertEqual(result.parent, folder)
                self.assertEqual(original.read_bytes(), scan.pages[0].path.read_bytes())

    def test_import_rejects_traversal_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            archive = folder / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as writer:
                writer.writestr("../escaped.png", b"not an image")
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                import_archive(archive, folder / "scan")
            self.assertFalse((folder / "escaped.png").exists())

    def test_rar_import_orders_pages_and_rejects_unsafe_members(self):
        class Member:
            def __init__(self, name, data):
                self.filename, self.data, self.file_size = name, data, len(data)

            def is_dir(self):
                return False

            def is_symlink(self):
                return False

            def needs_password(self):
                return False

        class Archive:
            def __init__(self, members):
                self.members = members

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def volumelist(self):
                return ["single.cbr"]

            def infolist(self):
                return self.members

            def open(self, name):
                return io.BytesIO(next(item.data for item in self.members if item.filename == name))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "single.cbr"
            source.write_bytes(b"mock RAR header")
            page1 = root / "01.png"
            page2 = root / "02.png"
            Image.new("RGB", (120, 150), "red").save(page1)
            Image.new("RGB", (120, 150), "blue").save(page2)
            members = [Member("part/10.png", page2.read_bytes()),
                       Member("part/2.png", page1.read_bytes())]
            with patch("comic_stitcher.archive_import._unrar_tool", return_value="unrar"), \
                 patch("comic_stitcher.archive_import.rarfile.tool_setup"), \
                 patch("comic_stitcher.archive_import.rarfile.RarFile", return_value=Archive(members)):
                scan = import_archive(source, root / "staged")
                self.assertEqual([p.path.read_bytes() for p in scan.pages],
                                 [page1.read_bytes(), page2.read_bytes()])
                self.assertEqual(scan.source_archive, source)
                self.assertEqual(scan.output_folder, root)
                members.append(Member("../escape.png", page1.read_bytes()))
                with self.assertRaisesRegex(ValueError, "Unsafe"):
                    import_archive(source, root / "unsafe")
                self.assertFalse((root / "escape.png").exists())

    def test_rar_extractor_choice_per_platform(self):
        from comic_stitcher import archive_import
        with patch("comic_stitcher.archive_import._unrar_tool", return_value="C:/App/unrar.exe"):
            choices = archive_import._rar_extractors(windows=True)
            self.assertEqual(archive_import.rarfile.UNRAR_TOOL, "C:/App/unrar.exe")
        self.assertEqual(choices, [("UnRAR", dict(unrar=True, unar=False, bsdtar=False,
                                                  sevenzip=False, sevenzip2=False))])
        with patch("comic_stitcher.archive_import._unrar_tool") as windows_lookup:
            choices = archive_import._rar_extractors(windows=False)
            windows_lookup.assert_not_called()
        # Elsewhere each installed extractor is tried on its own, UnRAR first.
        self.assertEqual([label for label, _ in choices],
                         ["UnRAR", "unar", "bsdtar", "7-Zip (7z)", "7-Zip (7zz)"])
        for _, backends in choices:
            self.assertEqual(sum(backends.values()), 1)

    def test_missing_rar_extractor_names_packages_outside_windows(self):
        from comic_stitcher import archive_import
        with tempfile.TemporaryDirectory() as tmp, \
                patch("comic_stitcher.archive_import.rarfile.tool_setup",
                      side_effect=archive_import.rarfile.RarCannotExec("none")):
            with self.assertRaisesRegex(ValueError, "sudo apt install unrar"):
                archive_import._import_rar(Path(tmp) / "book.cbr", Path(tmp), None, windows=False)
            with self.assertRaisesRegex(ValueError, "UnRAR could not start"):
                with patch("comic_stitcher.archive_import._unrar_tool", return_value="unrar.exe"):
                    archive_import._import_rar(Path(tmp) / "book.cbr", Path(tmp), None, windows=True)

    def _rar_by_extractor(self, behaviour, pages):
        """Patches rarfile so each extractor behaves as given: 'missing', 'short' or 'whole'."""
        from comic_stitcher import archive_import
        current = {}

        def setup(**backends):
            backends.pop("force")
            name = next(key for key, used in backends.items() if used)
            if behaviour.get(name, "missing") == "missing":
                raise archive_import.rarfile.RarCannotExec(name)
            current["name"] = name

        class Member:
            def __init__(self, name, data):
                self.filename, self.data, self.file_size = name, data, len(data)
            def is_dir(self):
                return False
            def is_symlink(self):
                return False
            def needs_password(self):
                return False

        class Archive:
            def __init__(self, *_):
                self.members = [Member(f"{i:02d}.png", data) for i, data in enumerate(pages, 1)]
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass
            def volumelist(self):
                return ["book.cbr"]
            def infolist(self):
                return self.members
            def open(self, name):
                data = next(m.data for m in self.members if m.filename == name)
                if behaviour[current["name"]] == "short":
                    return io.BytesIO(data[:len(data) // 2])  # As unar does with RAR5.
                return io.BytesIO(data)

        return (patch("comic_stitcher.archive_import.rarfile.tool_setup", side_effect=setup),
                patch("comic_stitcher.archive_import.rarfile.RarFile", Archive))

    def test_rar_import_moves_on_when_an_extractor_cuts_pages_short(self):
        from comic_stitcher import archive_import
        pages = []
        for colour in ("red", "blue"):
            buffer = io.BytesIO()
            Image.new("RGB", (60, 80), colour).save(buffer, "PNG")
            pages.append(buffer.getvalue())
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "pages"
            destination.mkdir()
            setup, archive = self._rar_by_extractor({"unar": "short", "sevenzip": "whole"}, pages)
            with setup, archive:
                found = archive_import._import_rar(Path(tmp) / "book.cbr", destination, None, windows=False)
            self.assertEqual(len(found), 2)
            self.assertEqual(sorted(p.read_bytes() for p in destination.iterdir()), sorted(pages))

            for item in destination.iterdir():
                item.unlink()
            setup, archive = self._rar_by_extractor({"unar": "short", "sevenzip": "short"}, pages)
            with setup, archive, self.assertRaises(ValueError) as caught:
                archive_import._import_rar(Path(tmp) / "book.cbr", destination, None, windows=False)
            message = str(caught.exception)
            self.assertIn("unar and 7-Zip (7z) couldn't fully read this archive", message)
            self.assertIn("sudo apt install unrar", message)
            self.assertIn("7zip-rar", message)
            self.assertFalse(any(destination.iterdir()))  # Nothing half-extracted is left.

    def test_cb7_both_sizes_preserves_order_and_source_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for index in range(3):
                Image.new("RGB", (120, 150), (index * 70, 40, 90)).save(folder / f"p{index:02d}.png")
            originals = {p.name: p.read_bytes() for p in folder.glob("*.png")}
            plan = BookPlan(scan_folder(folder))
            plan.join(1, override_boundary=True)
            full, reduced = export_book(plan, ExportOptions("CB7 Check", keep_originals=True,
                                                            spread_format="png",
                                                            archive_format="cb7", size_output="both",
                                                            resize_pixels=75, format="jpeg", workers=1))
            self.assertEqual([full.name, reduced.name], ["CB7 Check - Full.cb7", "CB7 Check - Reduced.cb7"])
            for path, expected in ((full, ["01.png", "02.png"]),
                                   (reduced, ["01.jpg", "02.jpg"])):
                with py7zr.SevenZipFile(path, "r") as archive:
                    self.assertEqual(archive.getnames(), expected)
                    self.assertIsNone(archive.testzip())
                output = folder / f"extracted_{path.stem}"
                with py7zr.SevenZipFile(path, "r") as archive:
                    archive.extractall(path=output)
                if path == full:
                    self.assertEqual((output / "01.png").read_bytes(), originals["p00.png"])
                else:
                    with Image.open(output / "02.jpg") as page:
                        self.assertEqual(page.size, (120, 75))
            self.assertEqual(originals, {p.name: p.read_bytes() for p in folder.glob("*.png")})

    def test_gamma_lanczos_high_detail_jpeg_encodes_with_optimization(self):
        # Without the larger JPEG encoder buffer, this reproducibly raises
        # "broken data stream when writing image file" on Pillow 12.
        with tempfile.TemporaryDirectory() as tmp:
            pixels = np.random.default_rng(42).integers(0, 256, (3072, 2048, 3), dtype=np.uint8)
            source = Image.fromarray(pixels)
            opt = ExportOptions("Detail", resize_filter="lanczos", resize_gamma=True,
                                resize_pixels=1920, jpeg_quality=90, jpeg_optimize=True,
                                jpeg_subsampling=0)
            output = resized_image(source, opt)
            before = ImageFile.MAXBLOCK
            path = Path(tmp) / "resized.jpg"
            _save_image(output, path, "jpeg", opt)
            self.assertEqual(ImageFile.MAXBLOCK, before)
            with Image.open(path) as decoded:
                decoded.load()
                self.assertEqual(decoded.size, (1280, 1920))

    def test_gamma_resize_is_bright_and_reduce_only_leaves_small_pages_alone(self):
        image = Image.new("RGB", (2, 2), "black")
        for y in range(2):
            image.putpixel((1, y), (255, 255, 255))
        corrected = resized_image(image, ExportOptions("Light", resize_pixels=1))
        ordinary = resized_image(image, ExportOptions("Dark", resize_pixels=1, resize_gamma=False))
        self.assertEqual(corrected.size, (1, 1))
        self.assertGreater(corrected.getpixel((0, 0))[0], ordinary.getpixel((0, 0))[0] + 30)
        self.assertIs(image, resized_image(image, ExportOptions("Small", resize_pixels=1920)))

    def test_both_sizes_join_and_preserve_full_originals(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for i in range(3):
                Image.new("RGB", (120, 150), (i * 70, 60, 90)).save(folder / f"p{i:02d}.png")
            originals = {p.name: p.read_bytes() for p in folder.glob("*.png")}
            plan = BookPlan(scan_folder(folder))
            plan.join(1, override_boundary=True)
            opt = ExportOptions("Two Copies", keep_originals=True, format="jpeg", spread_format="png",
                                size_output="both", resize_pixels=75, workers=2)
            full, reduced = export_book(plan, opt)
            self.assertEqual((full.name, reduced.name),
                             ("Two Copies - Full.cbz", "Two Copies - Reduced.cbz"))
            with zipfile.ZipFile(full) as archive:
                self.assertEqual(archive.namelist(), ["01.png", "02.png"])
                self.assertEqual(archive.read("01.png"), originals["p00.png"])
                with archive.open("02.png") as member, Image.open(member) as spread:
                    self.assertEqual(spread.size, (240, 150))
            with zipfile.ZipFile(reduced) as archive:
                self.assertEqual(archive.namelist(), ["01.jpg", "02.jpg"])
                with archive.open("01.jpg") as member, Image.open(member) as page:
                    self.assertEqual(page.size, (60, 75))
                with archive.open("02.jpg") as member, Image.open(member) as spread:
                    self.assertEqual(spread.size, (120, 75))
            self.assertEqual(originals, {p.name: p.read_bytes() for p in folder.glob("*.png")})
            self.assertFalse(any(p.name.startswith("__ComicStitcher_work") for p in folder.iterdir()))
            with self.assertRaises(FileExistsError):
                export_book(plan, opt)

    def test_reduced_only_preserves_aspect_ratio_and_avoids_upscaling(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            Image.new("RGB", (50, 100), "red").save(folder / "page01.png")
            plan = BookPlan(scan_folder(folder))
            opt = ExportOptions("Tablet", keep_originals=True, format="png",
                                size_output="reduced", resize_mode="width", resize_pixels=120,
                                workers=1)
            archive = export_book(plan, opt)
            self.assertEqual(archive.name, "Tablet.cbz")
            with zipfile.ZipFile(archive) as contents:
                self.assertEqual(contents.namelist(), ["01.png"])
                with contents.open("01.png") as member, Image.open(member) as image:
                    self.assertEqual(image.size, (50, 100))

    def test_parallel_webp_export_keeps_order_and_originals(self):
        self.assertEqual(encoding_workers(), min(6, max(1, (os.cpu_count() or 1) // 2)))
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for i in range(4):
                image = Image.new("RGB", (256, 192), (30 * i, 30 + i, 220 - 20 * i))
                image.save(folder / f"p{i:02d}.png")
            sources = {p.name: p.read_bytes() for p in folder.glob("*.png")}
            plan = BookPlan(scan_folder(folder))
            progress = []
            result = export_book(plan, ExportOptions("Parallel WebP", format="webp", workers=2),
                                 lambda n, total: progress.append((n, total)))
            with zipfile.ZipFile(result) as archive:
                self.assertEqual(archive.namelist(), ["01.webp", "02.webp", "03.webp", "04.webp"])
                for filename in archive.namelist():
                    with archive.open(filename) as member, Image.open(member) as decoded:
                        self.assertEqual(decoded.size, (256, 192))
            self.assertEqual(progress[-1], (5, 5))
            self.assertEqual([n for n, _ in progress], sorted(n for n, _ in progress))
            self.assertEqual(sources, {p.name: p.read_bytes() for p in folder.glob("*.png")})

    def test_review_report_tracks_decisions_overlaps_undo_and_manual_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for i in range(3):
                make_page(folder / f"page{i:03d}.jpg", i)
            plan = BookPlan(scan_folder(folder))
            path = folder / "separate-report.json"

            def report():
                save_report(plan, path)
                return json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(report()["counts"]["pending"], 2)
            plan.decide(True)
            after_yes = report()
            self.assertEqual(after_yes["counts"]["approved"], 1)
            self.assertEqual(after_yes["counts"]["overlapped"], 1)
            self.assertTrue(after_yes["review_complete"])
            self.assertIn("edge_mad", after_yes["candidates"][0]["metrics"]["seam"])
            self.assertFalse((folder / "separate-report.json.tmp").exists())
            plan.back()
            self.assertEqual(report()["counts"]["pending"], 2)
            plan.decide(False)
            self.assertEqual(report()["counts"],
                             {"approved": 0, "rejected": 1, "overlapped": 0, "below_cutoff": 0,
                              "deferred": 0, "pending": 1})
            plan.deferred_possible = True
            deferred = report()
            self.assertEqual(deferred["counts"]["deferred"], 1)
            self.assertFalse(deferred["review_complete"])
            self.assertEqual(plan.remaining_possible(), 1)
            plan.deferred_possible = False
            plan.join(0)
            joined = report()
            self.assertEqual(joined["counts"],
                             {"approved": 0, "rejected": 1, "overlapped": 1, "below_cutoff": 0,
                              "deferred": 0, "pending": 0})
            self.assertEqual(len(joined["manual_joins"]), 1)
            plan.split(0)
            self.assertEqual(report()["manual_joins"], [])

    def test_low_scoring_candidate_is_reported_but_not_queued(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for i in range(2):
                make_page(folder / f"p{i:02d}.jpg", i)
            scan = scan_folder(folder)
            scan.candidates = [Candidate(0, 1, .399, False)]
            plan = BookPlan(scan)
            self.assertIsNone(plan.current())
            self.assertEqual(plan.remaining_possible(), 0)
            self.assertEqual(report_data(plan)["candidates"][0]["status"], "below_cutoff")
            scan.candidates = [Candidate(0, 1, .4, False)]
            self.assertIsNotNone(plan.current())

    def test_edge_only_scan_matches_full_image_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for i in range(2):
                pixels = np.random.default_rng(i).integers(0, 256, (205, 151 + i, 3), dtype=np.uint8)
                pixels[:, :5] = 0
                Image.fromarray(pixels).save(folder / f"page{i:03d}.png")
            first, second = sorted(folder.glob("*.png"))
            page = classify_page(first, 0, 14, 16, 18)
            original = load_rgb(first)
            self.assertEqual(page.left_stats, strip_stats(original, "left", 14))
            self.assertEqual(page.right_stats, strip_stats(original, "right", 14))
            self.assertEqual(seam_score(load_edge_rgb(first, "right"), load_edge_rgb(second, "left")),
                             seam_score(original, load_rgb(second)))
            progress = []
            scan_folder(folder, lambda n, total: progress.append((n, total)))
            self.assertEqual(progress[-1], (4, 4))

    def test_manual_override_of_border_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for i in (46, 47):
                Image.new("RGB", (80, 100), "black").save(folder / f"page{i:03d}.png")
            plan = BookPlan(scan_folder(folder))
            self.assertEqual(plan.scan.candidates, [])
            with self.assertRaises(BoundaryOverrideRequired):
                plan.join(0)
            self.assertEqual(plan.entries(), [(0, None), (1, None)])
            plan.join(0, override_boundary=True)
            self.assertEqual(plan.entries(), [(0, 1)])
            plan.split(0)
            self.assertEqual(plan.entries(), [(0, None), (1, None)])
            with self.assertRaises(ValueError):
                plan.join(1, override_boundary=True)  # adjacency is never overridable

    def test_numbering_gap_and_changed_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            make_page(folder / "page001.jpg", 1)
            make_page(folder / "page003.jpg", 3)
            scan = scan_folder(folder)
            self.assertTrue(scan.number_gaps_detected)
            self.assertEqual(scan.candidates, [])
            plan = BookPlan(scan)
            opt = ExportOptions("Gap check", keep_originals=True)
            (folder / "page001.jpg").write_bytes(b"changed")
            with self.assertRaises(ValueError):
                export_book(plan, opt)
            self.assertFalse((folder / "Gap check.cbz").exists())

    def test_retained_png_pages_precede_lossy_encoding_and_include_both_sizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            pixels = np.random.default_rng(42).integers(0, 256, (80, 60, 3), dtype=np.uint8)
            Image.fromarray(pixels).save(folder / "001.png")
            plan = BookPlan(scan_folder(folder))
            opt = ExportOptions("Masters", format="jpeg", jpeg_quality=10, keep_images=True,
                                size_output="both", resize_pixels=40, workers=1)
            export_book(plan, opt)
            with Image.open(folder / "Masters - Full - PNG pages" / "01.png") as image:
                np.testing.assert_array_equal(np.asarray(image), pixels)
            with Image.open(folder / "Masters - Reduced - PNG pages" / "01.png") as image:
                self.assertEqual(image.size, (30, 40))
            with zipfile.ZipFile(folder / "Masters - Full.cbz") as archive:
                self.assertEqual(archive.namelist(), ["01.jpg"])
            opt.resize_pixels = 200
            opt.resize_reduce_only = False  # Older saved options cannot enable enlargement.
            with Image.fromarray(pixels) as image:
                self.assertIs(resized_image(image, opt), image)

    def test_png_conversion_without_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for i in range(2):
                make_page(folder / f"p{i}.jpg", i)
            plan = BookPlan(scan_folder(folder))
            options = ExportOptions("Export PNG", format="png", cbz=False, keep_images=True)
            result = export_book(plan, options)
            self.assertTrue(result.is_dir())
            self.assertEqual(sorted(p.suffix for p in result.iterdir()), [".png", ".png"])
            self.assertEqual(len(list(folder.glob("*.jpg"))), 2)

    def test_no_archive_saves_pages_in_the_chosen_formats_and_png_is_extra(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for i in range(2):
                make_page(folder / f"p{i}.jpg", i)
            plan = BookPlan(scan_folder(folder))
            # Full size JPEG, reduced WebP, no archive: a folder of each.
            options = ExportOptions("Book", format="jpeg", cbz=False, size_output="both", resize_pixels=60,
                                    reduced_custom=True, reduced_settings={"format": "webp"}, workers=1,
                                    comicinfo_xml="<ComicInfo/>")
            result = export_book(plan, options)
            self.assertEqual([p.name for p in result], ["Book - Full - JPEG pages", "Book - Reduced - WebP pages"])
            self.assertEqual(sorted(p.name for p in result[0].iterdir()), ["01.jpg", "02.jpg", "ComicInfo.xml"])
            self.assertEqual(sorted(p.name for p in result[1].iterdir()), ["01.webp", "02.webp", "ComicInfo.xml"])
            self.assertFalse(any("PNG" in p.name for p in folder.iterdir()))
            # Also keeping PNG pages adds a PNG folder for each size.
            export_book(plan, replace(options, book_name="Book 2", keep_images=True))
            self.assertEqual(sorted(p.name for p in folder.iterdir() if p.name.startswith("Book 2")),
                             ["Book 2 - Full - JPEG pages", "Book 2 - Full - PNG pages",
                              "Book 2 - Reduced - PNG pages", "Book 2 - Reduced - WebP pages"])
            # PNG pages with no archive are already lossless: no second PNG folder.
            result = export_book(plan, ExportOptions("Book 3", format="png", cbz=False, keep_images=True, workers=1))
            self.assertEqual([p.name for p in folder.iterdir() if p.name.startswith("Book 3")], ["Book 3 - PNG pages"])
            self.assertEqual(result.name, "Book 3 - PNG pages")
            # With an archive, PNG pages are still extra.
            export_book(plan, ExportOptions("Book 4", format="webp", keep_images=True, workers=1))
            self.assertEqual(sorted(p.name for p in folder.iterdir() if p.name.startswith("Book 4")),
                             ["Book 4 - PNG pages", "Book 4.cbz"])

    def test_cover_image_and_comicinfo_saved_beside_the_comic(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for i in range(3):
                make_page(folder / f"p{i}.jpg", i)
            plan = BookPlan(scan_folder(folder))
            options = ExportOptions("Book", format="webp", size_output="both", resize_pixels=60, workers=1,
                                    comicinfo_xml="<ComicInfo/>", save_cover=True, save_comicinfo=True)
            export_book(plan, options)
            for size in ("Full", "Reduced"):
                with zipfile.ZipFile(folder / f"Book - {size}.cbz") as archive:
                    self.assertEqual((folder / f"Book - {size} - Cover.webp").read_bytes(), archive.read("01.webp"))
                    self.assertEqual((folder / f"Book - {size} - ComicInfo.xml").read_bytes(),
                                     archive.read("ComicInfo.xml"))
            # Back to front (manga saved with the back cover first): the cover is the front cover.
            plan.scan.right_to_left = True
            export_book(plan, ExportOptions("Manga", format="jpeg", workers=1, save_cover=True))
            with zipfile.ZipFile(folder / "Manga.cbz") as archive:
                self.assertEqual((folder / "Manga - Cover.jpg").read_bytes(), archive.read("03.jpg"))
            self.assertFalse((folder / "Manga - ComicInfo.xml").exists())  # Not asked for.
            # A PDF has no place for ComicInfo.xml: beside it, it's the only copy.
            export_book(plan, ExportOptions("Paper", format="jpeg", archive_format="pdf", workers=1,
                                            comicinfo_xml="<ComicInfo/>", save_comicinfo=True))
            self.assertIn(b"<PageCount>3</PageCount>", (folder / "Paper - ComicInfo.xml").read_bytes())
            # No archive: the page folders hold ComicInfo.xml already, so none is saved beside them.
            export_book(plan, ExportOptions("Pages", format="jpeg", cbz=False, workers=1,
                                            comicinfo_xml="<ComicInfo/>", save_comicinfo=True, save_cover=True))
            self.assertEqual(sorted(p.name for p in folder.iterdir() if p.name.startswith("Pages")),
                             ["Pages - Cover.jpg", "Pages - JPEG pages"])

    def test_overlaps_and_keep_originals(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for i in range(1, 4):
                make_page(folder / f"page{i:03d}.jpg", i)
            originals = {p.name: p.read_bytes() for p in folder.glob("*.jpg")}
            scan = scan_folder(folder)
            self.assertEqual(len(scan.pages), 3)
            self.assertEqual({(p.left, p.right) for p in scan.candidates}, {(0, 1), (1, 2)})
            plan = BookPlan(scan)
            first = plan.current()
            plan.decide(True)
            self.assertIsNone(plan.current())  # overlapping candidate disappeared
            self.assertTrue(plan.back())
            self.assertEqual(plan.current(), first)
            plan.decide(False)
            self.assertIsNotNone(plan.current())
            plan.decide(True)
            self.assertEqual(len(plan.entries()), 2)

            opt = ExportOptions("A: sample", keep_originals=True, start=0, spread_format="png")
            archive = export_book(plan, opt)
            with zipfile.ZipFile(archive) as zf:
                self.assertEqual(zf.namelist(), ["00.jpg", "01.png"])
                self.assertEqual(zf.read("00.jpg"), originals["page001.jpg"])
                with zf.open("01.png") as member, Image.open(member) as spread:
                    self.assertEqual(spread.size, (240, 150))
            self.assertEqual({p.name: p.read_bytes() for p in folder.glob("*.jpg")}, originals)
            self.assertFalse(any(p.name.startswith("__ComicStitcher_work") for p in folder.iterdir()))
            with self.assertRaises(FileExistsError):
                export_book(plan, opt)  # collision cannot overwrite archive


if __name__ == "__main__":
    unittest.main()
