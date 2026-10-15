"""Suggesting a PDF import resolution from the images on its pages."""
import tempfile
import unittest
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from .pdf_import import suggest_pdf_dpi


def make_pdf(path, pages, page_size=(480, 720)):
    """pages: for each page, a list of (pixel size, (x, y, width, height) in points)."""
    folder = path.parent
    document = pdfium.PdfDocument.new()
    for number, images in enumerate(pages):
        page = document.new_page(*page_size)
        for index, ((width_px, height_px), (x, y, width, height)) in enumerate(images):
            jpeg = folder / f"p{number}-{index}.jpg"
            Image.new("RGB", (width_px, height_px), "gray").save(jpeg, quality=50)
            image = pdfium.PdfImage.new(document)
            image.load_jpeg(str(jpeg), inline=True)
            image.set_matrix(pdfium.PdfMatrix().scale(width, height).translate(x, y))
            page.insert_obj(image)
        page.gen_content()
        page.close()
    document.save(str(path))
    document.close()
    return path


def full_page(dpi, page_size=(480, 720)):
    width, height = page_size
    return ((round(width * dpi / 72), round(height * dpi / 72)), (0, 0, width, height))


class SuggestResolutionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def suggest(self, pages, **kwargs):
        return suggest_pdf_dpi(make_pdf(self.folder / "book.pdf", pages, **kwargs))

    def test_one_scan_per_page(self):
        self.assertEqual(self.suggest([[full_page(400)]] * 3), 400)
        self.assertEqual(self.suggest([[full_page(150)]] * 3), 150)

    def test_nearby_values_show_as_the_common_resolution(self):
        # 2,010 pixels across 480 points is 301.5 dpi.
        self.assertEqual(self.suggest([[((2010, 3000), (0, 0, 480, 720))]]), 300)

    def test_logos_and_decorations_do_not_decide(self):
        logo = ((900, 300), (40, 40, 100, 33))  # About 650 dpi, but tiny.
        self.assertEqual(self.suggest([[full_page(300), logo, logo]] * 2), 300)

    def test_pages_cut_into_thin_strips_still_count(self):
        strips = [((1000, 75), (0, y, 480, 36)) for y in range(0, 720, 36)]  # 150 dpi strips
        self.assertEqual(self.suggest([strips] * 2), 150)

    def test_images_beside_the_page_are_ignored(self):
        off_page = ((4000, 4000), (500, 0, 200, 200))
        self.assertEqual(self.suggest([[full_page(200), off_page]]), 200)

    def test_pages_without_much_image_get_no_suggestion(self):
        self.assertIsNone(self.suggest([[((300, 300), (40, 40, 100, 100))]] * 2))
        self.assertIsNone(self.suggest([[]]))

    def test_a_minority_of_larger_scans_does_not_raise_the_suggestion(self):
        # Most pages at 400 dpi; one in six scanned at 600 or more.
        pages = [[full_page(400)]] * 10 + [[full_page(600)], [full_page(800)]]
        self.assertEqual(self.suggest(pages), 400)

    def test_artwork_laid_over_a_full_page_does_not_decide(self):
        # A 300 dpi page with two high-resolution pieces of art covering most of it.
        pieces = [((2400, 2400), (0, 0, 480, 480)), ((2400, 1200), (0, 480, 480, 240))]
        self.assertEqual(self.suggest([[full_page(300), *pieces]] * 3), 300)

    def test_suggestion_stays_within_the_import_range(self):
        self.assertEqual(self.suggest([[full_page(900)]]), 600)
        self.assertEqual(self.suggest([[full_page(50)]]), 72)


if __name__ == "__main__":
    unittest.main()
