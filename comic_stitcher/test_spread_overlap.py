"""Spreads sliced from a page narrower than two pages: both halves repeat a strip
of art at the seam. Measuring that strip, and joining the halves over it."""
import io
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .backend import (BookPlan, join_layout, measure_overlap, offered, scan_folder, stitched_image,
    thin_border, thin_border_between)
from .export import ExportOptions, _entry_size, export_book
from .review_report import report_data
from .session import read_session, restore_plan, save_session


def artwork(width=1000, height=700, seed=3) -> Image.Image:
    """Flat-coloured shapes with dark outlines, like digitally coloured comic art."""
    rng = np.random.default_rng(seed)
    image = Image.new("RGB", (width, height), tuple(int(v) for v in rng.integers(60, 200, 3)))
    draw = ImageDraw.Draw(image)
    for _ in range(90):
        x, y = int(rng.integers(-100, width)), int(rng.integers(-100, height))
        w, h = int(rng.integers(30, 260)), int(rng.integers(30, 260))
        colour = tuple(int(v) for v in rng.integers(0, 255, 3))
        if rng.random() < 0.5:
            draw.ellipse((x, y, x + w, y + h), fill=colour, outline=(20, 20, 20), width=3)
        else:
            draw.polygon([(x, y), (x + w, y + int(rng.integers(0, h))), (x + int(rng.integers(0, w)), y + h)],
                         fill=colour, outline=(20, 20, 20), width=3)
    return image.filter(ImageFilter.SMOOTH)


def halves(image: Image.Image, overlap: int) -> tuple[Image.Image, Image.Image]:
    """Cut as the publishers' tools do: half a page's width from each edge."""
    left_width = (image.width + overlap) // 2
    right_width = image.width + overlap - left_width
    return (image.crop((0, 0, left_width, image.height)),
            image.crop((image.width - right_width, 0, image.width, image.height)))


def jpeg(image: Image.Image) -> Image.Image:
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=85)
    buffer.seek(0)
    return Image.open(buffer).convert("RGB")


class MeasureTests(unittest.TestCase):
    def test_finds_the_repeated_strip(self):
        for overlap in (20, 30, 47, 80):  # Up to a sixth of the page width is searched.
            left, right = halves(artwork(), overlap)
            self.assertEqual(measure_overlap(left, right), overlap)
        # A few pixels look like art carrying on across a clean seam; those are left to nudging.
        self.assertIsNone(measure_overlap(*halves(artwork(), 8)))

    def test_survives_each_half_being_compressed_separately(self):
        left, right = halves(artwork(seed=5), 36)
        self.assertEqual(measure_overlap(jpeg(left), jpeg(right)), 36)

    def test_looks_past_a_thin_white_border(self):
        left, right = halves(artwork(seed=7), 30)
        bordered = np.asarray(right).copy()
        bordered[:, :4] = 255
        self.assertEqual(measure_overlap(left, Image.fromarray(bordered)), 30)

    def test_clean_spreads_and_plain_edges_are_left_alone(self):
        art = artwork(seed=9)
        left, right = art.crop((0, 0, 500, 700)), art.crop((500, 0, 1000, 700))
        self.assertIsNone(measure_overlap(left, right))
        plain = Image.new("RGB", (500, 700), (240, 240, 235))
        self.assertIsNone(measure_overlap(plain, plain.copy()))
        self.assertIsNone(measure_overlap(artwork(seed=1).crop((0, 0, 500, 700)),
                                          artwork(seed=2).crop((0, 0, 500, 700))))


class JoinTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name) / "pages"
        self.folder.mkdir()
        self.art = artwork()
        left, right = halves(self.art, 30)
        Image.new("RGB", (515, 700), "white").save(self.folder / "001.png")  # Each half is 515 px wide.
        left.save(self.folder / "002.png")
        right.save(self.folder / "003.png")
        self.plan = BookPlan(scan_folder(self.folder, manual=True))
        self.plan.join(1, override_boundary=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_overlapping_halves_rebuild_the_original_spread(self):
        self.plan.set_overlap(1, 30, "right")
        spread = stitched_image(self.folder / "002.png", self.folder / "003.png", overlap=(30, "right"))
        self.assertEqual(spread.size, self.art.size)
        self.assertTrue(np.array_equal(np.asarray(spread), np.asarray(self.art)))
        self.assertEqual(_entry_size(self.plan, ExportOptions("Book"), 1, 2, False), self.art.size)

    def test_the_page_on_top_covers_the_other(self):
        left = np.asarray(Image.open(self.folder / "002.png").convert("RGB")).copy()
        left[:, -30:] = (255, 0, 0)  # A border on the left page's inner edge.
        Image.fromarray(left).save(self.folder / "002.png")
        spread = np.asarray(stitched_image(self.folder / "002.png", self.folder / "003.png", overlap=(30, "right")))
        self.assertFalse((spread[:, 485:515] == (255, 0, 0)).all(axis=2).any())  # Covered.
        spread = np.asarray(stitched_image(self.folder / "002.png", self.folder / "003.png", overlap=(30, "left")))
        self.assertTrue((spread[:, 485:515] == (255, 0, 0)).all())

    def test_export_uses_the_overlap(self):
        self.plan.set_overlap(1, 30, "right")
        with tempfile.TemporaryDirectory() as output:
            self.plan.scan.output_folder = Path(output)
            archive = export_book(self.plan, ExportOptions("Book", format="png", workers=1))
            import zipfile
            with zipfile.ZipFile(archive) as book:
                names = sorted(name for name in book.namelist() if name.endswith(".png"))
                with Image.open(io.BytesIO(book.read(names[1]))) as page:
                    self.assertEqual(page.size, self.art.size)

    def test_checked_undone_and_cleared_with_the_spread(self):
        with self.assertRaises(ValueError):
            self.plan.set_overlap(0, 10)  # Not a spread.
        with self.assertRaises(ValueError):
            self.plan.set_overlap(1, 515)  # As wide as a page.
        self.plan.set_overlap(1, 30, "left")
        self.assertEqual(self.plan.join_overlaps, {1: (30, "left")})
        self.plan.undo_preview()
        self.assertEqual(self.plan.join_overlaps, {})
        self.plan.set_overlap(1, 30)
        self.plan.split(1)
        self.assertEqual(self.plan.join_overlaps, {})

    def test_saved_and_restored_with_the_review(self):
        self.plan.set_overlap(1, 30, "left")
        with tempfile.TemporaryDirectory() as app:
            save_session(self.folder, Path(app), self.plan, possible_started=False, stage="preview",
                         preview_index=1)
            data = read_session(self.folder, Path(app))
        restored = restore_plan(self.plan.scan, data)
        self.assertEqual(restored.join_overlaps, {1: (30, "left")})
        restored.undo_preview()
        self.assertEqual(restored.join_overlaps, {})
        # Reviews saved before overlaps existed still open.
        for key in ("join_overlaps",):
            data.pop(key)
        for snapshot in data["preview_history"]:
            snapshot.pop("join_overlaps", None)
        self.assertEqual(restore_plan(self.plan.scan, data).join_overlaps, {})



class SuggestionTests(unittest.TestCase):
    """The import scan offers overlapped halves for review, lined up."""

    def book(self, folder: Path, right_to_left=False):
        spread = artwork(seed=11)
        left, right = halves(spread, 34)
        pages = [artwork(seed=20).crop((0, 0, left.width, 700)), jpeg(left), jpeg(right),
                 artwork(seed=21).crop((0, 0, right.width, 700))]
        # Back-to-front files keep the spread's left half before its right half on
        # disk; reading right to left, the scan meets the right half first.
        for number, page in enumerate(pages, 1):
            page.save(folder / f"{number:03d}.jpg", quality=80)
        return scan_folder(folder, right_to_left=right_to_left)

    def test_overlapped_halves_are_offered_and_joined_lined_up(self):
        with tempfile.TemporaryDirectory() as directory:
            scan = self.book(Path(directory))
            found = {(c.left, c.right): c.metrics.get("overlap") for c in scan.candidates}
            self.assertEqual(found.get((1, 2)), 34)
            self.assertFalse([pair for pair, overlap in found.items() if overlap and pair != (1, 2)])
            plan = BookPlan(scan)
            index, candidate = next((i, c) for i, c in enumerate(scan.candidates) if (c.left, c.right) == (1, 2))
            self.assertTrue(offered(candidate))
            while plan.current() and plan.current()[0] != index:
                plan.decide(False)
            plan.decide(True)
            self.assertEqual(plan.join_overlaps, {1: (34, "left")})
            report = report_data(plan)
            self.assertEqual(report["overlapping_halves"],
                             [{"left_page": 2, "right_page": 3, "repeated_px": 34, "status": "approved",
                               "final_overlap_px": 34}])
            plan.back()  # Undoing the decision drops the overlap with the join.
            self.assertEqual(plan.join_overlaps, {})

    def test_found_where_a_white_border_rules_the_edges_out(self):
        for side, width in (("right page", 6), ("left page", 3)):
            with self.subTest(side), tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                left, right = halves(artwork(seed=13), 40)
                if side == "right page":
                    right = np.asarray(right).copy()
                    right[:, :width] = 255
                    right = Image.fromarray(right)
                else:
                    left = np.asarray(left).copy()
                    left[:, -width:] = 255
                    left = Image.fromarray(left)
                pages = [artwork(seed=30).crop((0, 0, left.width, 700)), left, right,
                         artwork(seed=31).crop((0, 0, right.width, 700))]
                for number, page in enumerate(pages, 1):
                    page.save(folder / f"{number:03d}.jpg", quality=80)
                scan = scan_folder(folder)
                found = [(c.left, c.right, c.metrics.get("overlap")) for c in scan.candidates if c.metrics.get("overlap")]
                self.assertEqual(found, [(1, 2, 40)])
                self.assertTrue(offered(next(c for c in scan.candidates if c.metrics.get("overlap"))))

    def test_found_between_halves_of_different_widths(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            art = artwork(seed=17)
            left, right = art.crop((0, 0, 560, 700)), art.crop((520, 0, 1000, 700))  # 40 px repeated.
            pages = [artwork(seed=40).crop((0, 0, 560, 700)), left, right, artwork(seed=41).crop((0, 0, 480, 700))]
            for number, page in enumerate(pages, 1):
                page.save(folder / f"{number:03d}.jpg", quality=80)
            scan = scan_folder(folder)
            self.assertEqual([(c.left, c.right, c.metrics.get("overlap")) for c in scan.candidates
                              if c.metrics.get("overlap")], [(1, 2, 40)])

    def test_a_thin_border_on_one_half_is_covered_when_joined(self):
        self.assertEqual(thin_border(3, 0), (3, "right"))  # The right page covers the left's border.
        self.assertEqual(thin_border(0, 6), (6, "left"))
        self.assertIsNone(thin_border(2, 2))  # Both edges plain: leave it.
        self.assertIsNone(thin_border(12, 0))  # Wider than a thin border: a frame or margin.
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            art = artwork(width=2000, seed=19)
            left = np.full((700, 1003, 3), 255, np.uint8)  # A clean split, plus a 3 px white border.
            left[:, :1000] = np.asarray(art)[:, :1000]
            right = art.crop((1000, 0, 2000, 700))
            pages = [artwork(width=1003, seed=50), Image.fromarray(left), right, artwork(width=1000, seed=51)]
            for number, page in enumerate(pages, 1):
                page.save(folder / f"{number:03d}.png")
            scan = scan_folder(folder)
            index, candidate = next((i, c) for i, c in enumerate(scan.candidates) if (c.left, c.right) == (1, 2))
            self.assertIsNone(candidate.metrics.get("overlap"))
            self.assertEqual(join_layout(candidate), (3, "right"))
            self.assertEqual(thin_border_between(Image.fromarray(left), right), (3, "right"))
            plan = BookPlan(scan)
            while plan.current() and plan.current()[0] != index:
                plan.decide(False)
            plan.decide(True)
            self.assertEqual(plan.join_overlaps, {1: (3, "right")})
            spread = stitched_image(folder / "002.png", folder / "003.png", overlap=plan.join_overlaps[1])
            self.assertTrue(np.array_equal(np.asarray(spread), np.asarray(art)))  # No white line.
            self.assertEqual(report_data(plan)["covered_borders"][0]["border_px"], 3)

    def test_a_wider_border_the_edges_rule_out_is_set_aside(self):
        """A 6 px border makes the right page's edge look solid, so the edge test
        rules the pair out; with the border set aside the art carries on."""
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            art = artwork(width=2000, seed=23)
            right = np.full((700, 1006, 3), 255, np.uint8)
            right[:, 6:] = np.asarray(art)[:, 1000:]
            pages = [artwork(width=1000, seed=60), art.crop((0, 0, 1000, 700)), Image.fromarray(right),
                     artwork(width=1006, seed=61)]
            for number, page in enumerate(pages, 1):
                page.save(folder / f"{number:03d}.png")
            scan = scan_folder(folder)
            index, candidate = next((i, c) for i, c in enumerate(scan.candidates) if (c.left, c.right) == (1, 2))
            self.assertEqual(candidate.metrics.get("border_set_aside"), 6)
            self.assertTrue(offered(candidate))
            self.assertEqual(join_layout(candidate), (6, "left"))
            self.assertFalse([c for c in scan.candidates if (c.left, c.right) != (1, 2) and c.metrics.get("border_set_aside")])
            plan = BookPlan(scan)
            while plan.current() and plan.current()[0] != index:
                plan.decide(False)
            plan.decide(True)
            spread = stitched_image(folder / "002.png", folder / "003.png", overlap=plan.join_overlaps[1])
            self.assertTrue(np.array_equal(np.asarray(spread), np.asarray(art)))

    def test_found_in_back_to_front_books_too(self):
        with tempfile.TemporaryDirectory() as directory:
            scan = self.book(Path(directory), right_to_left=True)
            self.assertEqual([c.metrics["overlap"] for c in scan.candidates if c.metrics.get("overlap")], [34])

if __name__ == "__main__":
    unittest.main()
