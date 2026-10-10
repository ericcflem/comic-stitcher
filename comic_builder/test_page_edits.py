"""Pixel-level checks of reordering, component rotations and resumed edits."""
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from PIL import Image
from .backend import BookPlan, ExportOptions, export_book, scan_manual_folder
from .session import save_session, read_session, restore_plan, cached_folder_scan


class PageEditTests(unittest.TestCase):
    def make_pages(self, folder, count=4):
        for i in range(count):
            image = Image.new('RGB', (12, 18), (30 + i * 40, 10, 20))
            image.putpixel((0, 0), (250, i, 3))
            image.save(folder / f'{i:03d}.png')
        return BookPlan(scan_manual_folder(folder))

    def pixels(self, archive, index):
        with zipfile.ZipFile(archive) as z:
            with Image.open(io.BytesIO(z.read(z.namelist()[index]))) as image:
                return image.copy()

    def test_rotate_halves_join_rotate_spread_matches_reversed_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory); plan = self.make_pages(folder, 2)
            plan.set_rotation(0, 180); plan.set_rotation(1, 180)
            plan.join(0, override_boundary=True)
            self.assertEqual(plan.joined_rotations[0], (180, 180))
            plan.set_rotation(0, 180)
            archive = export_book(plan, ExportOptions('Rotated', format='png', workers=1))
            result = self.pixels(archive, 0)
            with Image.open(folder / '001.png') as right, Image.open(folder / '000.png') as left:
                expected = Image.new('RGB', (24, 18)); expected.paste(right, (0, 0)); expected.paste(left, (12, 0))
            self.assertEqual(result.tobytes(), expected.tobytes())
            plan.split(0)
            self.assertEqual(plan.rotations, {})
            self.assertEqual(plan.entries(), [(1, None), (0, None)])
            split_archive = export_book(plan, ExportOptions('Split', format='png', workers=1))
            self.assertEqual(self.pixels(split_archive, 0).tobytes(), expected.crop((0, 0, 12, 18)).tobytes())
            self.assertEqual(self.pixels(split_archive, 1).tobytes(), expected.crop((12, 0, 24, 18)).tobytes())
            plan.undo_preview()
            self.assertEqual(plan.entries(), [(0, 1)])
            self.assertEqual(plan.rotations, {0: 180})

    def test_move_and_swap_undo_survive_resume_without_losing_removed_position(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); folder = root / 'pages'; folder.mkdir()
            plan = self.make_pages(folder)
            original = plan.page_order.copy()
            plan.remove_page(1)
            plan.move_entry(0, 2)
            plan.swap_entries(0, 1)
            save_session(folder, root / 'state', plan, possible_started=False, stage='preview', preview_index=2)
            data = read_session(folder, root / 'state')
            resumed = restore_plan(cached_folder_scan(folder, data), data)
            self.assertEqual(resumed.undo_preview()['_action'], 'page swap')
            self.assertEqual(resumed.entries(), [(2, None), (3, None), (0, None)])
            self.assertEqual(resumed.undo_preview()['_action'], 'page move')
            self.assertEqual(resumed.page_order, original)
            self.assertEqual(resumed.removed_pages, {1})
            self.assertEqual(resumed.undo_preview()['_action'], 'page removal')
            self.assertEqual(resumed.entries(), [(i, None) for i in range(4)])
            self.assertEqual(resumed.history, [])
            self.assertIsNone(resumed.undo_preview())
            plan.move_entry(0, 0)
            self.assertEqual(plan.preview_history[-1]['_action'], 'page swap')

    def test_split_all_quarter_turns_combines_rotations_and_visual_reading_order(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for rtl in (False, True):
                for angle in (0, 90, 180, 270):
                    plan = self.make_pages(folder, 2); plan.scan.right_to_left = rtl
                    plan.set_rotation(0, 180); plan.set_rotation(1, 90)
                    plan.join(0, override_boundary=True); plan.set_rotation(0, angle)
                    plan.split(0)
                    self.assertEqual(plan.rotations.get(0, 0), (180 + angle) % 360)
                    self.assertEqual(plan.rotations.get(1, 0), (90 + angle) % 360)
                    reverse = angle == 180 or angle == (90 if rtl else 270)
                    self.assertEqual(plan.entries(), [(1, None), (0, None)] if reverse else [(0, None), (1, None)])

    def test_swap_join_move_and_resume_keep_source_ids_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); folder = root / 'pages'; folder.mkdir()
            plan = self.make_pages(folder)
            plan.comicinfo_xml = '<ComicInfo><Pages><Page Image="0" Bookmark="First"/><Page Image="1" Bookmark="Second"/></Pages></ComicInfo>'
            plan.swap_entries(0, 1)
            self.assertEqual(plan.entries()[:2], [(1, None), (0, None)])
            plan.join(1, right=0, override_boundary=True)
            plan.move_entry(0, 2)
            self.assertEqual(plan.entries(), [(2, None), (3, None), (1, 0)])
            save_session(folder, root / 'state', plan, possible_started=False, stage='preview', preview_index=2)
            data = read_session(folder, root / 'state')
            restored = restore_plan(cached_folder_scan(folder, data), data)
            self.assertEqual(restored.entries(), plan.entries())
            archive = export_book(restored, ExportOptions('Moved', format='png', workers=2, comicinfo_xml=plan.comicinfo_xml))
            joined = self.pixels(archive, 2)
            self.assertEqual(joined.getpixel((0, 0)), (250, 1, 3))
            self.assertEqual(joined.getpixel((12, 0)), (250, 0, 3))
            restored.split(1)
            self.assertEqual(restored.entries(), [(2, None), (3, None), (1, None), (0, None)])
            with zipfile.ZipFile(archive) as z:
                xml = z.read('ComicInfo.xml').decode()
                self.assertIn('Image="2"', xml)
            self.assertTrue(all((folder / f'{i:03d}.png').exists() for i in range(4)))

    def test_rotations_survive_resume_and_right_to_left_spatial_order(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory); plan = self.make_pages(folder, 2)
            plan.scan.right_to_left = True
            plan.set_rotation(0, 90); plan.set_rotation(1, 270)
            plan.join(0, override_boundary=True)
            save_session(folder, folder / 'state', plan, possible_started=False, stage='preview', preview_index=0)
            data = read_session(folder, folder / 'state')
            resumed = restore_plan(plan.scan, data)
            self.assertEqual(resumed.joined_rotations, {0: (90, 270)})
            archive = export_book(resumed, ExportOptions('RTL', format='png', workers=1))
            result = self.pixels(archive, 0)
            self.assertEqual(result.size, (36, 12))
            with Image.open(folder / '001.png') as a, Image.open(folder / '000.png') as b:
                expected = Image.new('RGB', (36, 12)); expected.paste(a.transpose(Image.Transpose.ROTATE_90), (0, 0)); expected.paste(b.transpose(Image.Transpose.ROTATE_270), (18, 0))
            self.assertEqual(result.tobytes(), expected.tobytes())
