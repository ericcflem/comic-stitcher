"""16-bit greyscale and CMYK pages: shown, scanned and encoded with correct colour."""
import hashlib
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageCms

from .backend import ExportOptions, _encode_entry
from .cmyk_profile import SWOP_PROFILE, SWOP_PROFILE_SHA256
from .colour import standard_mode
from .detector import classify_page

CMYK_PROFILE = (Path(__file__).parent / "test_data" / "CGATS001Compat-v2-micro.icc").read_bytes()


def grey16(value=30000, size=(40, 60)):
    return Image.fromarray(np.full(size[::-1], value, np.uint16))


def cmyk(seed=0, size=(40, 60)):
    rng = np.random.default_rng(seed)
    return Image.fromarray(rng.integers(0, 255, (size[1], size[0], 4), dtype=np.uint8), "CMYK")


class StandardModeTests(unittest.TestCase):
    def test_sixteen_bit_grey_is_scaled_not_clipped(self):
        for value in (0, 257 * 117, 65535):
            converted = standard_mode(grey16(value))
            self.assertEqual(converted.mode, "L")
            self.assertEqual(converted.getpixel((0, 0)), value // 257)
        # The plain conversion this replaces turned mid-grey white.
        self.assertEqual(grey16(30000).convert("L").getpixel((0, 0)), 255)

    def test_32_bit_integers_holding_8_bit_values_are_kept(self):
        image = Image.fromarray(np.full((5, 5), 117, np.int32), "I")
        self.assertEqual(standard_mode(image).getpixel((0, 0)), 117)

    def test_cmyk_uses_its_embedded_profile(self):
        page = cmyk()
        page.info["icc_profile"] = CMYK_PROFILE
        srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
        expected = ImageCms.profileToProfile(page, ImageCms.ImageCmsProfile(BytesIO(CMYK_PROFILE)), srgb,
                                             outputMode="RGB")
        with patch("comic_builder.colour.SWOP_PROFILE", b"not used"):  # Only the page's own profile counts.
            converted = standard_mode(page)
        self.assertEqual(converted.mode, "RGB")
        self.assertEqual(converted.tobytes(), expected.tobytes())
        # The new profile is sRGB (its header records when it was made, so compare the rest).
        profile = converted.info["icc_profile"]
        self.assertEqual(profile[:24] + profile[36:], srgb.tobytes()[:24] + srgb.tobytes()[36:])
        self.assertNotEqual(converted.tobytes(), page.convert("RGB").tobytes())

    def test_cmyk_without_a_usable_profile_is_taken_as_printed_for_swop(self):
        srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
        for info in ({}, {"icc_profile": b"not a profile"}):
            page = cmyk(1)
            page.info.update(info)
            converted = standard_mode(page)
            expected = ImageCms.profileToProfile(page, ImageCms.ImageCmsProfile(BytesIO(SWOP_PROFILE)), srgb,
                                                 outputMode="RGB")
            self.assertEqual(converted.tobytes(), expected.tobytes())
            self.assertNotEqual(converted.tobytes(), page.convert("RGB").tobytes())
            self.assertIn("icc_profile", converted.info)  # Tagged sRGB, like other converted pages.

    def test_the_plain_conversion_is_the_last_resort(self):
        page = cmyk(2)
        with patch("comic_builder.colour.SWOP_PROFILE", b"broken"):
            converted = standard_mode(page)
        self.assertEqual(converted.tobytes(), page.convert("RGB").tobytes())
        self.assertNotIn("icc_profile", converted.info)

    def test_the_bundled_profile_is_the_recorded_one(self):
        self.assertEqual(hashlib.sha256(SWOP_PROFILE).hexdigest(), SWOP_PROFILE_SHA256)
        self.assertEqual(SWOP_PROFILE, CMYK_PROFILE)  # The CC0 file kept in test_data.
        self.assertEqual(ImageCms.ImageCmsProfile(BytesIO(SWOP_PROFILE)).profile.xcolor_space, "CMYK")

    def test_white_paper_stays_white(self):
        converted = standard_mode(Image.new("CMYK", (4, 4), (0, 0, 0, 0)))
        self.assertTrue(all(channel >= 250 for channel in converted.getpixel((0, 0))), converted.getpixel((0, 0)))

    def test_ordinary_pages_are_untouched(self):
        for mode in ("RGB", "RGBA", "L", "LA", "P", "1"):
            image = Image.new(mode, (4, 4))
            self.assertIs(standard_mode(image), image)


class EncodingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_every_format_and_size_encodes_these_pages(self):
        grey16(257 * 117).save(self.folder / "grey16.png")
        cmyk().save(self.folder / "cmyk.jpg")
        page = cmyk()
        page.save(self.folder / "cmyk-profiled.jpg", icc_profile=CMYK_PROFILE)
        Image.new("RGB", (40, 60), "white").save(self.folder / "white.png")
        for name in ("grey16.png", "cmyk.jpg", "cmyk-profiled.jpg"):
            for fmt in ("png", "jpeg", "webp"):
                for reduce in (False, True):
                    with self.subTest(name=name, fmt=fmt, reduce=reduce):
                        target = self.folder / f"out.{fmt}"
                        # PNG, the default for joined pages, couldn't hold CMYK before.
                        _encode_entry(str(self.folder / name), None, str(target), fmt,
                                      ExportOptions("t", resize_pixels=30), reduce=reduce)
                        with Image.open(target) as out:
                            self.assertIn(out.mode, ("L", "RGB"))
                            if name == "grey16.png":
                                mean = np.asarray(out.convert("L"), float).mean()
                                self.assertAlmostEqual(mean, 117, delta=2)  # Not white.
            _encode_entry(str(self.folder / name), str(self.folder / "white.png"),
                          str(self.folder / "joined.png"), "png", ExportOptions("t"))

    def test_scanner_sees_sixteen_bit_grey_as_grey(self):
        grey16(257 * 117, (200, 300)).save(self.folder / "page.png")
        info = classify_page(self.folder / "page.png", 0, 14, 16.0, 18.0)
        self.assertAlmostEqual(info.left_stats["mean_color"][0], 117, delta=1)

    def test_thumbnails_and_previews_show_sixteen_bit_grey(self):
        # The thumbnail strip showed "Image error": Pillow can't shrink 16-bit grey.
        try:
            from .ui.common import pixels_from_pil
        except ImportError:
            self.skipTest("Qt is not installed")
        data, width, height = pixels_from_pil(grey16(257 * 117, (400, 600)), (100, 84))
        self.assertEqual(data[:4], bytes((117, 117, 117, 255)))

    def test_pdf_holds_sixteen_bit_grey_as_grey(self):
        from pypdf import PdfReader
        from .pdf_export import write_pdf
        grey16(257 * 117).save(self.folder / "grey16.png")
        write_pdf([self.folder / "grey16.png"], self.folder / "book.pdf")
        image = next(iter(PdfReader(self.folder / "book.pdf").pages[0].images)).image
        self.assertAlmostEqual(np.asarray(image.convert("L"), float).mean(), 117, delta=1)


if __name__ == "__main__":
    unittest.main()
