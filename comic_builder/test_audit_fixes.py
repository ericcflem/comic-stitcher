"""Regression and fault-injection checks for the first release code audit."""
import errno
import os
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageOps
from .backend import BookPlan, Candidate, scan_manual_folder
from .export import ExportOptions, _encode_entry, export_book, publish_archive
from .detector import classify_page, load_edge_rgb
from .session import save_session, read_session, restore_plan, cached_folder_scan


class AuditFixTests(unittest.TestCase):
    def pages(self, root):
        for i in range(4):
            Image.new('RGB', (40, 60), (i * 50, 10, 20)).save(root / f'{i:03}.png')
        return BookPlan(scan_manual_folder(root))

    def test_manual_reorder_revisits_only_changed_ordered_neighbors_and_resumes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); plan = self.pages(root)
            for _ in range(3): plan.decide(False)
            plan.move_entry(0, 1)
            self.assertEqual((plan.current()[1].left, plan.current()[1].right), (1, 0))
            plan.decide(False)
            self.assertEqual((plan.current()[1].left, plan.current()[1].right), (0, 2))
            plan.decide(True)
            self.assertIsNone(plan.current())  # 2–3 retains its original rejection.
            self.assertEqual(plan.entries(), [(1, None), (0, 2), (3, None)])
            save_session(root, root / 'state', plan, possible_started=False, stage='preview', preview_index=1)
            data = read_session(root, root / 'state')
            # Both a cached folder scan and a fresh import restore changed candidates.
            for scan in (cached_folder_scan(root, data), scan_manual_folder(root)):
                restored = restore_plan(scan, data)
                self.assertEqual(restored.entries(), plan.entries())
                restored.validate_entries()
            result = export_book(plan, ExportOptions('Result', format='png', workers=1))
            with zipfile.ZipFile(result) as z:
                self.assertEqual(len(z.namelist()), 3)
                with Image.open(io.BytesIO(z.read(z.namelist()[1]))) as im:
                    self.assertEqual(im.size, (80, 60))
                    self.assertEqual(im.getpixel((0, 0)), (0, 10, 20))
                    self.assertEqual(im.getpixel((79, 0)), (100, 10, 20))
            # Back cannot resurrect a stale, nonadjacent source pair.
            while plan.back():
                current = plan.current()
                if current: self.assertTrue(plan.adjacent(current[1].left, current[1].right))
                plan.validate_entries()

    def test_guided_reorder_scores_only_new_pairs_and_undo_keeps_old_decisions(self):
        with tempfile.TemporaryDirectory() as d:
            plan = self.pages(Path(d)); plan.scan.manual_mode = False
            plan.scan.candidates = [Candidate(i, i + 1, .7, True) for i in range(3)]
            for _ in range(3): plan.decide(False)
            with patch('comic_builder.backend.is_candidate_pair', return_value=True), patch(
                    'comic_builder.backend.seam_score', return_value={'continuity': .6}) as score:
                plan.move_entry(0, 1)
                self.assertEqual(score.call_count, 2)
                self.assertEqual(plan.decisions, {0: False, 1: False, 2: False})
                plan.undo_preview()
                self.assertEqual(plan.page_order, [0, 1, 2, 3])
                self.assertIsNone(plan.current())
                plan.move_entry(0, 1)
                self.assertEqual(score.call_count, 2)  # Reuse the ordered-pair measurements.

    def test_new_likely_pair_precedes_unreviewed_possible_pair(self):
        with tempfile.TemporaryDirectory() as d:
            plan = self.pages(Path(d)); plan.scan.manual_mode = False
            plan.scan.candidates = [Candidate(2, 3, .45, False)]
            with patch('comic_builder.backend.is_candidate_pair', return_value=True), patch(
                    'comic_builder.backend.seam_score', return_value={'continuity': .6}):
                plan.move_entry(0, 1)
            self.assertTrue(plan.current()[1].likely)
            self.assertEqual((plan.current()[1].left, plan.current()[1].right), (1, 0))

    def test_guided_reorder_does_not_rescan_unchanged_excluded_pairs(self):
        with tempfile.TemporaryDirectory() as d:
            plan = self.pages(Path(d)); plan.scan.manual_mode = False; plan.scan.candidates = []
            with patch('comic_builder.backend.is_candidate_pair', return_value=False) as classify:
                plan.move_entry(0, 1)
                self.assertEqual(classify.call_count, 2)
                plan.undo_preview(); plan.move_entry(0, 1)
                self.assertEqual(classify.call_count, 2)

    def test_export_rejects_corrupt_overlapping_manual_join(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); plan = self.pages(root)
            plan.manual_pairs.add(0); plan.manual_rights[0] = 1
            plan.page_order = [1, 0, 2, 3]
            with self.assertRaisesRegex(ValueError, 'overlapping or nonadjacent'):
                export_book(plan, ExportOptions('Invalid', workers=1))
            self.assertFalse((root / 'Invalid.cbz').exists())

    def test_hardlink_unavailable_publishes_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root / 'source'; source.write_bytes(b'verified archive')
            target = root / 'target'
            with patch('comic_builder.export.os.link', side_effect=OSError(errno.EOPNOTSUPP, 'unsupported')):
                publish_archive(source, target)
                self.assertEqual(target.read_bytes(), b'verified archive')
                if os.name == 'nt':
                    # Windows publishes by renaming, so the staged copy has moved into place.
                    self.assertFalse(source.exists())
                    source.write_bytes(b'second archive')
                else:
                    self.assertEqual(source.read_bytes(), b'verified archive')
                with self.assertRaises(FileExistsError): publish_archive(source, target)
                self.assertEqual(target.read_bytes(), b'verified archive')
                if os.name != 'nt':
                    # Elsewhere the archive is copied; a failed copy must leave nothing behind.
                    failed = root / 'failed'
                    def partial_copy(origin, output):
                        output.write(b'partial'); raise OSError('disk full')
                    with patch('comic_builder.export.shutil.copyfileobj', side_effect=partial_copy):
                        with self.assertRaises(OSError): publish_archive(source, failed)
                    self.assertFalse(failed.exists())
                    self.assertEqual(source.read_bytes(), b'verified archive')

    def test_export_cleanup_failure_keeps_completed_archive(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); plan = self.pages(root)
            with patch('comic_builder.export.shutil.rmtree', side_effect=PermissionError('locked')), patch(
                    'comic_builder.export.logging.exception') as logged:
                archive = export_book(plan, ExportOptions('Completed', format='png', workers=1))
                logged.assert_called_once()
            with zipfile.ZipFile(archive) as z:
                self.assertIsNone(z.testzip())
                self.assertEqual(len(z.namelist()), 4)

    def test_two_size_publication_failure_rolls_back_and_preserves_verified_archives(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); plan = self.pages(root)
            real_publish = publish_archive
            def fail_second(source, destination):
                if source.name == 'reduced.cbz': raise PermissionError('denied')
                real_publish(source, destination)
            with patch('comic_builder.export.publish_archive', side_effect=fail_second):
                with self.assertRaises(RuntimeError):
                    export_book(plan, ExportOptions('Rollback', format='png', workers=1, size_output='both'))
            self.assertFalse((root / 'Rollback - Full.cbz').exists())
            self.assertFalse((root / 'Rollback - Reduced.cbz').exists())
            work = next(root.glob('__ComicStitcher_work_*'))
            with zipfile.ZipFile(work / 'reduced.cbz') as z: self.assertIsNone(z.testzip())
            with zipfile.ZipFile(work / 'recovered_Rollback - Full.cbz') as z: self.assertIsNone(z.testzip())

    def test_windows_incorrect_function_uses_no_replace_rename(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root / 'source'; target = root / 'target'
            error = OSError('Incorrect function'); error.winerror = 1
            with patch('comic_builder.export.os.link', side_effect=error), patch(
                    'comic_builder.export.os.name', 'nt'), patch('comic_builder.export.os.rename') as rename:
                publish_archive(source, target)
                rename.assert_called_once_with(source, target)
            with patch('comic_builder.export.os.link', side_effect=FileExistsError()):
                with self.assertRaises(FileExistsError): publish_archive(source, target)

    def test_all_exif_orientations_match_preview_scan_export_and_copy(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pixels = np.arange(40 * 60 * 3, dtype=np.uint8).reshape(60, 40, 3)
            for orientation in range(1, 9):
                source = root / f'{orientation}.jpg'; exif = Image.Exif(); exif[274] = orientation
                Image.fromarray(pixels).save(source, quality=95, exif=exif)
                with Image.open(source) as original:
                    expected = ImageOps.exif_transpose(original)
                plan = BookPlan(scan_manual_folder(root))
                page = next(p for p in plan.scan.pages if p.path == source)
                self.assertEqual((page.width, page.height), expected.size)
                info = classify_page(source, 0, 14, 16, 18)
                self.assertEqual((info.width, info.height), expected.size)
                edge = load_edge_rgb(source, 'left')
                np.testing.assert_array_equal(edge, np.asarray(expected.convert('RGB')))
                for rotation, reduce in ((0, False), (90, False), (0, True)):
                    target = root / 'result.png'
                    opt = ExportOptions('Unused', resize_pixels=20, resize_gamma=False)
                    _encode_entry(str(source), None, str(target), 'png', opt, reduce, rotation)
                    with Image.open(target) as result:
                        if not reduce:
                            wanted = expected.rotate(-rotation, expand=True)
                            self.assertEqual(result.tobytes(), wanted.tobytes())
                        else:
                            self.assertEqual(result.height, 20)
                        self.assertNotIn(274, result.getexif())
                    target.unlink()
                copied = root / 'copy.jpg'
                _encode_entry(str(source), None, str(copied), None, ExportOptions('Unused'))
                self.assertEqual(copied.read_bytes(), source.read_bytes()); copied.unlink()
                expected.close()
