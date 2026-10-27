"""Reading order stays stable while physical spread order reverses."""
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image
from .backend import BookPlan, scan_folder
from .export import ExportOptions, export_book
from .session import save_session, read_session, cached_folder_scan, restore_plan, session_path
from .screenshot_import import CropChoice, stage_screenshots
from .archive_import import import_archive


class ReadingDirectionTests(unittest.TestCase):
    def make_pages(self, root):
        for name, colour in [('001.png', 'red'), ('002.png', 'blue')]:
            page = Image.new('RGB', (80, 120), colour)
            # Varied stripes lower down, as artwork has, so the edges aren't plain
            # borders (a plain red or blue edge counts as a border, like black or white).
            for row, stripe in enumerate(range(40, 120, 8)):
                page.paste(((row * 70) % 256, (row * 130) % 256, (row * 190) % 256), (0, stripe, 80, stripe + 4))
            page.save(root / name)

    def test_seam_detection_checks_opposite_edges(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_pages(root)
            with patch('comic_builder.backend.is_candidate_pair', return_value=True), \
                 patch('comic_builder.backend.load_edge_rgb',
                       return_value=np.zeros((120, 200, 3), np.uint8)) as edge, \
                 patch('comic_builder.backend.seam_score', return_value={'continuity': .8}):
                scan = scan_folder(root, right_to_left=True)
            self.assertTrue(scan.right_to_left)
            self.assertEqual(scan.candidates[0].left, 0)
            self.assertEqual(edge.call_args_list[0].args, (root / '001.png', 'right'))
            self.assertEqual(edge.call_args_list[1].args, (root / '002.png', 'left'))

    def test_export_places_earlier_page_on_right(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_pages(root)
            plan = BookPlan(scan_folder(root, manual=True, right_to_left=True))
            plan.decide(True)
            path = export_book(plan, ExportOptions('Manga', format='png', workers=1, front_to_back=True))
            with zipfile.ZipFile(path) as archive:
                with Image.open(io.BytesIO(archive.read(archive.namelist()[0]))) as image:
                    self.assertEqual(image.getpixel((10, 10)), (255, 0, 0))
                    self.assertEqual(image.getpixel((100, 10)), (0, 0, 255))
            self.assertEqual(plan.entries(), [(0, 1)])

    def test_resume_preserves_direction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pages = root / 'pages'
            pages.mkdir()
            self.make_pages(pages)
            plan = BookPlan(scan_folder(pages, manual=True, right_to_left=True))
            plan.decide(True)
            save_session(pages, root / 'app', plan, possible_started=False,
                         stage='preview', preview_index=0)
            data = read_session(pages, root / 'app')
            restored = restore_plan(cached_folder_scan(pages, data), data)
            self.assertTrue(restored.scan.right_to_left)
            self.assertEqual(restored.approved(), {0})

    def test_screenshot_right_half_is_numbered_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'screenshot.png'
            image = Image.new('RGB', (161, 120), 'red')
            image.paste('blue', (80, 0, 161, 120))
            image.save(source)
            staged = stage_screenshots([CropChoice(source, 'double', (0, 0, 161, 120))],
                                      root / 'stage', root, manual_mode=True,
                                      workers=1, right_to_left=True)
            first, second = staged.scan.pages
            self.assertEqual((first.width, second.width), (81, 80))
            with Image.open(first.path) as page:
                self.assertEqual(page.getpixel((0, 0)), (0, 0, 255))
            self.assertTrue(staged.scan.right_to_left)

    def test_archive_direction_reaches_scanner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_pages(root)
            source = root / 'book.cbz'
            with zipfile.ZipFile(source, 'w') as archive:
                for name in ('001.png', '002.png'):
                    archive.write(root / name, name)
            scan = import_archive(source, root / 'stage', manual=True, right_to_left=True)
            self.assertTrue(scan.right_to_left)
            self.assertEqual([p.path.name for p in scan.pages], ['000002.png', '000001.png'])

    def test_pdf_direction_reaches_scanner(self):
        from .pdf_import import import_pdf
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'book.pdf'
            Image.new('RGB', (80, 120), 'red').save(source, 'PDF')
            scan = import_pdf(source, root / 'stage', dpi=72, workers=1,
                              manual=True, right_to_left=True)
            self.assertTrue(scan.right_to_left)

    def test_back_cover_first_book_exports_front_cover_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            colours = ['black', 'red', 'blue', 'green']
            for index, colour in enumerate(colours, 1):
                Image.new('RGB', (80, 120), colour).save(root / f'{index:03d}.png')
            plan = BookPlan(scan_folder(root, manual=True, right_to_left=True))
            self.assertEqual([p.path.name for p in plan.scan.pages],
                             ['004.png', '003.png', '002.png', '001.png'])
            plan.decide(False)  # Cover stays separate.
            plan.decide(True)   # Interior spread joins.
            result = export_book(plan, ExportOptions('Cover first', format='png', workers=1, front_to_back=True))
            with zipfile.ZipFile(result) as archive:
                names = archive.namelist()
                with Image.open(io.BytesIO(archive.read(names[0]))) as front:
                    self.assertEqual(front.getpixel((0, 0)), (0, 128, 0))
                with Image.open(io.BytesIO(archive.read(names[1]))) as spread:
                    self.assertEqual(spread.getpixel((0, 0)), (255, 0, 0))
                    self.assertEqual(spread.getpixel((80, 0)), (0, 0, 255))
                with Image.open(io.BytesIO(archive.read(names[2]))) as back:
                    self.assertEqual(back.getpixel((0, 0)), (0, 0, 0))

    def test_descending_number_gaps_still_block_pairing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for number in (1, 2, 4, 5):
                Image.effect_noise((80, 120), 100).convert('RGB').save(root / f'{number:03d}.png')
            scan = scan_folder(root, right_to_left=True)
            self.assertTrue(scan.number_gaps_detected)
            self.assertFalse(scan.numbering_assumed)
            self.assertNotEqual(scan.pages[1].group, scan.pages[2].group)

    def test_old_rtl_session_cannot_restore_wrong_sequence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pages = root / 'pages'
            pages.mkdir()
            self.make_pages(pages)
            plan = BookPlan(scan_folder(pages, manual=True, right_to_left=True))
            save_session(pages, root / 'app', plan, possible_started=False,
                         stage='review', preview_index=0)
            target = session_path(pages, root / 'app')
            data = json.loads(target.read_text())
            data.pop('source_order_reversed')
            target.write_text(json.dumps(data))
            self.assertIsNone(read_session(pages, root / 'app'))

    def test_output_order_choices_keep_spreads_intact_for_cbz_and_cb7(self):
        import py7zr
        for archive_format in ('cbz', 'cb7'):
            for front_to_back in (True, False):
                with self.subTest(archive_format=archive_format, front_to_back=front_to_back), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    for index, colour in enumerate(['black', 'red', 'blue', 'green'], 1):
                        Image.new('RGB', (80, 120), colour).save(root / f'{index:03d}.png')
                    plan = BookPlan(scan_folder(root, manual=True, right_to_left=True))
                    plan.decide(False)
                    plan.decide(True)
                    approved = plan.entries()
                    options = ExportOptions('Order', format='png', workers=1,
                                            size_output='both', resize_pixels=60,
                                            archive_format=archive_format, front_to_back=front_to_back)
                    results = export_book(plan, options)
                    for variant, path in enumerate(results):
                        if archive_format == 'cbz':
                            with zipfile.ZipFile(path) as archive:
                                pages = [archive.read(name) for name in archive.namelist()]
                        else:
                            unpack = root / f'unpacked-{variant}'
                            with py7zr.SevenZipFile(path) as archive:
                                archive.extractall(unpack)
                            pages = [p.read_bytes() for p in sorted(unpack.iterdir())]
                        expected_first = (0, 128, 0) if front_to_back else (0, 0, 0)
                        expected_last = (0, 0, 0) if front_to_back else (0, 128, 0)
                        with Image.open(io.BytesIO(pages[0])) as first:
                            self.assertEqual(first.getpixel((0, 0)), expected_first)
                        with Image.open(io.BytesIO(pages[2])) as last:
                            self.assertEqual(last.getpixel((0, 0)), expected_last)
                        with Image.open(io.BytesIO(pages[1])) as spread:
                            self.assertEqual(spread.getpixel((5, 5)), (255, 0, 0))
                            self.assertEqual(spread.getpixel((spread.width - 5, 5)), (0, 0, 255))
                    self.assertEqual(plan.entries(), approved)
                    self.assertFalse(list(root.glob('*.pdf')))
