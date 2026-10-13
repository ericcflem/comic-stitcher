"""PDF export: images go into the PDF as they are, and look right there."""
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageOps
from pypdf import PdfReader

from .backend import BookPlan, ExportOptions, estimate_export_size, export_book, scan_folder
from .pdf_export import PNG_SIGNATURE, _png_chunks, verify_pdf, write_pdf


def noise(size=(200, 300), seed=0):
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 255, (size[1] // 10, size[0] // 10, 3), dtype=np.uint8)
    return Image.fromarray(small).resize(size, Image.Resampling.NEAREST)


def rendered(pdf, index, size):
    page = pdfium.PdfDocument(pdf)[index]
    return page.render(scale=300 / 72).to_pil().convert("RGB").resize(size)


def difference(a, b):
    return float(np.abs(np.asarray(a, dtype=int) - np.asarray(b, dtype=int)).mean())


class WritePdfTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, *images, **options):
        pdf = self.folder / "out.pdf"
        write_pdf(list(images), pdf, **options)
        self.assertTrue(verify_pdf(pdf, len(images)))
        return pdf

    def image_stream(self, pdf, index=0):
        page = PdfReader(pdf).pages[index]
        return next(iter(page["/Resources"]["/XObject"].values())).get_object()

    def test_jpeg_is_stored_byte_for_byte_at_300_dpi(self):
        path = self.folder / "page.jpg"
        noise().save(path, quality=90)
        pdf = self.write(path, title="Tëst ★")
        self.assertEqual(self.image_stream(pdf)._data, path.read_bytes())
        page = PdfReader(pdf).pages[0]
        self.assertEqual([float(v) for v in page.mediabox], [0, 0, 48, 72])  # 200 x 300 px at 300 dpi
        self.assertEqual(PdfReader(pdf).metadata.title, "Tëst ★")

    def test_png_keeps_its_compressed_data(self):
        path = self.folder / "page.png"
        noise().save(path)
        pdf = self.write(path)
        data = path.read_bytes()
        idat = b"".join(body for kind, body in _png_chunks(data) if kind == b"IDAT")
        self.assertTrue(data.startswith(PNG_SIGNATURE))
        self.assertEqual(self.image_stream(pdf)._data, idat)
        self.assertLess(difference(rendered(pdf, 0, (200, 300)), noise()), 1)

    def test_images_pdf_cannot_take_as_they_are_still_look_right(self):
        rgba = noise().convert("RGBA")
        rgba.putalpha(128)
        paths = []
        for name, image, options in (("alpha.png", rgba, {}), ("interlaced.png", noise(), {"interlace": 1}),
                                     ("palette.png", noise().convert("P", palette=Image.Palette.ADAPTIVE), {}),
                                     ("gray.jpg", noise().convert("L"), {"quality": 95})):
            path = self.folder / name
            image.save(path, **options)
            paths.append(path)
        pdf = self.write(*paths)
        for index, path in enumerate(paths):
            with Image.open(path) as image:
                expected = Image.new("RGB", image.size, "white")
                converted = image.convert("RGBA")
                expected.paste(converted, mask=converted.getchannel("A"))
            self.assertLess(difference(rendered(pdf, index, expected.size), expected), 2, path.name)

    def test_exif_orientation_is_followed_without_changing_the_image(self):
        for orientation in range(1, 9):
            exif = Image.Exif()
            exif[274] = orientation
            path = self.folder / f"turned-{orientation}.jpg"
            noise().save(path, quality=95, exif=exif.tobytes())
            pdf = self.write(path)
            expected = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
            self.assertLess(difference(rendered(pdf, 0, expected.size), expected), 1, orientation)
            self.assertEqual(self.image_stream(pdf)._data, path.read_bytes())

    def test_cmyk_jpeg_colours(self):
        path = self.folder / "cmyk.jpg"
        Image.new("CMYK", (60, 60), (0, 0, 0, 0)).save(path, quality=95)  # white
        pdf = self.write(path)
        self.assertEqual(rendered(pdf, 0, (60, 60)).getpixel((30, 30)), (255, 255, 255))

    def test_right_to_left_books_open_right_to_left(self):
        path = self.folder / "page.jpg"
        noise().save(path)
        catalog = PdfReader(self.write(path, right_to_left=True)).trailer["/Root"]
        self.assertEqual(catalog["/ViewerPreferences"]["/Direction"], "/R2L")


class ExportPdfTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name) / "pages"
        self.folder.mkdir()
        for number in range(5):
            image = noise(seed=number)
            image.save(self.folder / f"{number:03}.{'webp' if number == 3 else 'jpg'}", quality=90)
        self.plan = BookPlan(scan_folder(self.folder, manual=True))
        self.plan.join(1, override_boundary=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_unchanged_jpegs_go_in_as_they_are_and_webp_pages_are_converted(self):
        opt = ExportOptions("Book", workers=1, archive_format="pdf", keep_originals=True,
                            spread_format="jpeg", comicinfo_xml="<ComicInfo><Title>Book</Title></ComicInfo>")
        estimate = estimate_export_size(self.plan, opt)["full"]
        pdf = export_book(self.plan, opt)
        self.assertEqual(pdf.suffix, ".pdf")
        reader = PdfReader(pdf)
        self.assertEqual(len(reader.pages), 4)  # Pages 1 and 2 were joined.
        first = next(iter(reader.pages[0]["/Resources"]["/XObject"].values())).get_object()
        self.assertEqual(first._data, (self.folder / "000.jpg").read_bytes())
        webp_page = next(iter(reader.pages[2]["/Resources"]["/XObject"].values())).get_object()
        self.assertEqual(webp_page["/Filter"], "/DCTDecode")
        self.assertEqual(reader.pages[1].mediabox.width, 96)  # The spread is two pages wide.
        self.assertLess(abs(estimate - pdf.stat().st_size) / pdf.stat().st_size, 0.15)

    def test_png_output_and_both_sizes(self):
        opt = ExportOptions("Both", workers=1, archive_format="pdf", format="png", size_output="both",
                            resize_pixels=150)
        full, reduced = export_book(self.plan, opt)
        self.assertEqual(len(PdfReader(full).pages), 4)
        self.assertEqual(PdfReader(reduced).pages[0].mediabox.height, 36)  # 150 px at 300 dpi

    def test_webp_settings_are_refused(self):
        for settings in ({"format": "webp"}, {"keep_originals": True, "spread_format": "webp"},
                         {"size_output": "both", "reduced_custom": True, "reduced_settings": {"format": "webp"}}):
            with self.assertRaisesRegex(ValueError, "not WebP"):
                export_book(self.plan, ExportOptions("Refused", workers=1, archive_format="pdf", **settings))
        self.assertFalse(list(self.folder.glob("Refused*")))


if __name__ == "__main__":
    unittest.main()
