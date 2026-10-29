"""Centering art that print margins push aside: measuring, moving and exporting."""
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .backend import BookPlan, scan_manual_folder
from .centering import centered_image, measure_page
from .export import ExportOptions, export_book
from .session import cached_folder_scan, read_session, restore_plan, save_session

W, H = 1700, 2600


def page(left=200, right=1550, background="white", frame="black", extras=(), rng_seed=0):
    """A page with two stacked panels between `left` and `right`, framed."""
    image = Image.new("RGB", (W, H), background)
    draw = ImageDraw.Draw(image)
    rng = np.random.default_rng(rng_seed)
    for top, bottom in ((150, 1250), (1330, 2450)):
        art = Image.fromarray(rng.integers(60, 200, (bottom - top, right - left, 3), dtype=np.uint8))
        image.paste(art, (left, top))
        draw.rectangle((left, top, right - 1, bottom - 1), outline=frame, width=8)
    for box, colour in extras:
        draw.rectangle(box, fill=colour)
    return image


class MeasureTests(unittest.TestCase):
    def measure(self, image, name="page.png"):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / name
            image.save(path)
            return measure_page(str(path))

    def test_off_center_art_is_measured_from_its_panels(self):
        shift, top, bottom = self.measure(page(200, 1550))  # 200 px left, 150 right.
        self.assertAlmostEqual(shift, -25, delta=4)
        self.assertEqual((top, bottom), (0, H))
        self.assertAlmostEqual(self.measure(page(150, 1500))[0], 25, delta=4)

    def test_centered_full_bleed_spread_and_coloured_pages_are_left_alone(self):
        self.assertIsNone(self.measure(page(175, 1525)))
        self.assertIsNone(self.measure(page(0, W)))  # Art to the edges.
        self.assertIsNone(self.measure(page(200, 1550, background=(200, 120, 90))))
        spread = Image.new("RGB", (2 * W, H), "white")
        spread.paste(page(), (0, 0))
        self.assertIsNone(self.measure(spread))

    def test_small_margins_and_off_white_paper(self):
        # 190 px against 160: 15 px off, under 1% of the page, still worth moving (Peanuts).
        self.assertAlmostEqual(self.measure(page(190, 1540))[0], -15, delta=4)
        self.assertIsNone(self.measure(page(178, 1528)))  # 3 px: left alone.
        cream = page(200, 1550, background=(238, 228, 205))  # Yellowed paper.
        self.assertAlmostEqual(self.measure(cream)[0], -25, delta=4)

    def test_why_a_page_is_left_alone(self):
        from .centering import check_page
        with tempfile.TemporaryDirectory() as folder:
            for image, why in ((page(175, 1525), "already centered"), (page(0, W), "art to the edges"),
                               (page(200, 1550, background=(200, 120, 90)), "colored background")):
                path = Path(folder) / "page.png"
                image.save(path)
                self.assertEqual(check_page(str(path)), why)

    def test_landscape_books_are_measured_and_their_spreads_left_alone(self):
        from .centering import check_page, spread_width
        # A strip collection: every page wider than tall (Peanuts), 20 px off-center.
        wide = page(200, 1550).resize((2600, 1700))
        sizes = [(2600, 1700)] * 5 + [(5200, 1700)]
        self.assertEqual(spread_width(sizes), 1.6 * 2600)
        self.assertIsNone(spread_width(sizes[:2]))  # Too few pages to tell.
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "page.png"
            wide.save(path)
            self.assertIsInstance(check_page(str(path), spread_width(sizes)), tuple)
            self.assertEqual(check_page(str(path)), "spread")  # Alone, wider than tall looks like one.
            double = Image.new("RGB", (5200, 1700), "white")
            double.paste(wide, (0, 0))
            double.save(path)
            self.assertEqual(check_page(str(path), spread_width(sizes)), "spread")

    def test_lettering_past_a_panel_moves_but_is_not_measured(self):
        # A sound effect 90 px past the left panel side: the panels still decide.
        lettering = [((115, 400 + y, 260, 430 + y), (220, 30, 30)) for y in range(0, 300, 60)]
        self.assertAlmostEqual(self.measure(page(200, 1550, extras=lettering))[0], -25, delta=4)

    def test_specks_in_the_margin_are_ignored(self):
        specks = [((1640, y, 1642, y + 2), (90, 90, 90)) for y in range(200, 2400, 37)]
        self.assertAlmostEqual(self.measure(page(200, 1550, extras=specks))[0], -25, delta=4)

    def test_black_pages_and_art_kept_on_the_page(self):
        black = page(200, 1550, background="black", frame=(250, 250, 250))
        self.assertAlmostEqual(self.measure(black)[0], -25, delta=4)
        # Panels 20 px from the left and 200 from the right would move 90 px right, but
        # lettering past the right panel reaches 64 px from the edge: it goes no further.
        lettering = [((1495, 500, 1635, 560), (220, 30, 30))]
        shift = self.measure(page(20, 1500, background="black", frame=(250, 250, 250), extras=lettering))[0]
        self.assertTrue(50 < shift <= 64, shift)

    def test_moved_art_and_the_uncovered_strip(self):
        original = page(200, 1550)
        moved = centered_image(original, (-25, 0, H))
        self.assertEqual(moved.size, original.size)
        self.assertEqual(moved.getpixel((180, 1000)), original.getpixel((205, 1000)))
        self.assertEqual(moved.getpixel((W - 10, 1000)), (255, 255, 255))  # Filled with the page colour.


class PlanTests(unittest.TestCase):
    def book(self, folder):
        for i, (left, right) in enumerate(((200, 1550), (150, 1500), (175, 1525))):
            page(left, right, rng_seed=i).save(folder / f"{i + 1:03d}.png")
        plan = BookPlan(scan_manual_folder(folder))
        plan.decisions = {index: False for index in range(len(plan.scan.candidates))}
        plan.centering = {i: value for i, p in enumerate(plan.scan.pages)
                          if (value := measure_page(str(p.path))) is not None}
        plan.centering_measured = True
        return plan

    def test_only_when_chosen_and_not_on_pages_left_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            plan = self.book(folder)
            self.assertEqual(sorted(plan.centering), [0, 1])
            self.assertEqual(plan.centered_pages(), [0, 1])
            self.assertIsNone(plan.page_centering(0))  # Off until chosen.
            plan.set_center_art(True)
            self.assertIsNotNone(plan.page_centering(0))
            plan.set_page_centered(1, False)
            self.assertIsNone(plan.page_centering(1))
            self.assertIsNone(plan.page_centering(0, 1))  # A spread is never centered.
            self.assertEqual(plan.undo_preview()["_action"], "centering")
            self.assertIsNotNone(plan.page_centering(1))
            plan.undo_preview()
            self.assertFalse(plan.center_art)

    def test_saved_with_the_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "pages"
            folder.mkdir()
            plan = self.book(folder)
            plan.set_center_art(True)
            plan.set_page_centered(1, False)
            save_session(folder, root / "state", plan, possible_started=False, stage="preview", preview_index=0)
            data = read_session(folder, root / "state")
            resumed = restore_plan(cached_folder_scan(folder, data), data)
            self.assertEqual((resumed.centering, resumed.centering_measured, resumed.center_art,
                              resumed.center_skipped), (plan.centering, True, True, {1}))
            self.assertEqual(resumed.undo_preview()["_action"], "centering")
            self.assertEqual(resumed.center_skipped, set())
            self.assertEqual(resumed.undo_preview()["_action"], "centering")
            self.assertFalse(resumed.center_art)

    def test_export_moves_centered_pages_and_copies_the_rest(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            plan = self.book(folder)
            plan.set_center_art(True)
            plan.set_page_centered(1, False)
            output = export_book(plan, ExportOptions("Book", keep_originals=True, spread_format="png", workers=1))
            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()
                self.assertEqual(archive.read(names[1]), (folder / "002.png").read_bytes())  # Left as it was.
                self.assertEqual(archive.read(names[2]), (folder / "003.png").read_bytes())  # Centered already.
                with archive.open(names[0]) as moved, Image.open(folder / "001.png") as source:
                    shift = plan.centering[0][0]
                    self.assertEqual(Image.open(moved).convert("RGB").getpixel((205 + shift, 1000)),
                                     source.convert("RGB").getpixel((205, 1000)))
