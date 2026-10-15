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


def add_stencils(source, target, dpi, count, page_size=(480, 720)):
    """Lay full-page 1-bit stencil masks (as lettering layers are often made) over every page."""
    import zlib
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import (ArrayObject, BooleanObject, DecodedStreamObject, DictionaryObject,
                               NameObject, NumberObject, StreamObject)
    width, height = page_size
    px_w, px_h = round(width * dpi / 72), round(height * dpi / 72)
    writer = PdfWriter(clone_from=PdfReader(source))
    for page in writer.pages:
        resources = page.setdefault(NameObject("/Resources"), DictionaryObject())
        objects = resources.setdefault(NameObject("/XObject"), DictionaryObject())
        drawing = b""
        for number in range(count):
            mask = StreamObject()
            mask._data = zlib.compress(b"\x55" * (((px_w + 7) // 8) * px_h))
            mask.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                         NameObject("/Width"): NumberObject(px_w), NameObject("/Height"): NumberObject(px_h),
                         NameObject("/ImageMask"): BooleanObject(True),
                         NameObject("/BitsPerComponent"): NumberObject(1),
                         NameObject("/Decode"): ArrayObject([NumberObject(1), NumberObject(0)]),
                         NameObject("/Filter"): NameObject("/FlateDecode")})
            objects[NameObject(f"/Mask{number}")] = writer._add_object(mask)
            drawing += f"q 1 g {width} 0 0 {height} 0 0 cm /Mask{number} Do Q\n".encode()
        contents = page.get_contents()
        stream = DecodedStreamObject()
        stream.set_data((contents.get_data() if contents is not None else b"") + b"\n" + drawing)
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(target)
    return target


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

    def test_a_higher_resolution_layer_over_the_page_does_not_decide(self):
        # 300 dpi artwork with a full-page 600 dpi layer (lettering, say) and more pieces over it.
        pieces = [full_page(600), ((2400, 2400), (0, 0, 480, 480)), ((2400, 1200), (0, 480, 480, 240))]
        self.assertEqual(self.suggest([[full_page(300), *pieces]] * 3), 300)

    def test_stencil_masks_do_not_decide(self):
        # Eric's book: 451 dpi colour art under two full-page 600 dpi lettering masks.
        path = make_pdf(self.folder / "book.pdf", [[full_page(451)]] * 3)
        add_stencils(path, self.folder / "lettered.pdf", 600, count=2)
        self.assertEqual(suggest_pdf_dpi(self.folder / "lettered.pdf"), 450)
        # A book made only of masks is still measured by them.
        blank = make_pdf(self.folder / "blank.pdf", [[]] * 3)
        add_stencils(blank, self.folder / "masks.pdf", 600, count=1)
        self.assertEqual(suggest_pdf_dpi(self.folder / "masks.pdf"), 600)

    def test_suggestion_stays_within_the_import_range(self):
        self.assertEqual(self.suggest([[full_page(900)]]), 600)
        self.assertEqual(self.suggest([[full_page(50)]]), 72)


if __name__ == "__main__":
    unittest.main()
