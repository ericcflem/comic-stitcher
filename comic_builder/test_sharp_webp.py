"""WebP with sharp RGB->YUV conversion, encoded by libwebp's cwebp."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import subprocess

import numpy as np
from PIL import Image, ImageCms, features

from . import sharp_webp
from .backend import ExportOptions, _save_image
from .sharp_webp import save_sharp_webp, with_icc

PROFILE = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def pillow_webp(image, **options):
    buffer = io.BytesIO()
    image.save(buffer, "WEBP", **options)
    return buffer.getvalue()


def red_and_blue_lettering():
    """Saturated red strokes on blue, the edges sharp YUV is for."""
    pixels = np.zeros((240, 320, 3), np.uint8)
    pixels[:] = (20, 40, 200)
    for x in range(10, 310, 9):
        pixels[30:210, x:x + 3] = (220, 20, 30)
    return Image.fromarray(pixels)


class ColourProfileTests(unittest.TestCase):
    def test_profile_is_laid_out_as_pillow_does(self):
        rng = np.random.default_rng(1)
        for mode, depth in (("RGB", 3), ("RGBA", 4)):
            image = Image.fromarray(rng.integers(0, 255, (61, 37, depth), dtype=np.uint8), mode)
            for profile in (PROFILE, PROFILE + b"x"):  # Even and odd lengths.
                plain = pillow_webp(image, quality=80)
                self.assertEqual(with_icc(plain, profile, image.size),
                                 pillow_webp(image, quality=80, icc_profile=profile))


@unittest.skipUnless(sharp_webp.sharp_yuv_available(), "cwebp is not installed")
class SharpWebpTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_without_sharp_yuv_cwebp_matches_pillow_byte_for_byte(self):
        # So the option changes the colour conversion and nothing else.
        version = subprocess.run([sharp_webp.cwebp_tool(), "-version"], capture_output=True,
                                 text=True).stdout.split()[0]
        if version != features.version("webp"):
            self.skipTest(f"cwebp {version} and Pillow's libwebp {features.version('webp')} differ")
        rng = np.random.default_rng(2)
        for mode, depth in (("RGB", 3), ("RGBA", 4)):
            image = Image.fromarray(rng.integers(0, 255, (75, 50, depth), dtype=np.uint8), mode)
            for profile in (None, PROFILE):
                target = self.folder / "out.webp"
                save_sharp_webp(image, target, 85, 5, profile, sharp_yuv=False)
                expected = pillow_webp(image, quality=85, method=5,
                                       **({"icc_profile": profile} if profile else {}))
                self.assertEqual(target.read_bytes(), expected)

    def test_sharp_yuv_keeps_coloured_edges_closer_to_the_original(self):
        image = red_and_blue_lettering()
        original = np.asarray(image, np.float64)
        errors = {}
        for sharp in (False, True):
            target = self.folder / f"{sharp}.webp"
            save_sharp_webp(image, target, 90, 4, sharp_yuv=sharp)
            with Image.open(target) as decoded:
                errors[sharp] = np.abs(np.asarray(decoded.convert("RGB"), np.float64) - original).mean()
        self.assertLess(errors[True], errors[False])

    def test_export_option_uses_cwebp_for_lossy_webp_only(self):
        image = red_and_blue_lettering()
        image.info["icc_profile"] = PROFILE
        target = self.folder / "page.webp"
        _save_image(image, target, "webp", ExportOptions("t", webp_sharp_yuv=True))
        self.assertNotEqual(target.read_bytes(), pillow_webp(image, quality=90, method=4, icc_profile=PROFILE))
        with Image.open(target) as saved:
            self.assertEqual(saved.info["icc_profile"], PROFILE)
        # Lossless WebP has no colour conversion to sharpen: Pillow encodes it as before.
        with patch("comic_builder.backend.save_sharp_webp", side_effect=AssertionError("cwebp used")):
            _save_image(image, target, "webp", ExportOptions("t", webp_sharp_yuv=True, webp_lossless=True))

    def test_greyscale_and_palette_pages_are_converted_first(self):
        for image in (Image.new("L", (40, 30), 128), Image.new("P", (40, 30), 3)):
            target = self.folder / "page.webp"
            save_sharp_webp(image, target, 90, 4)
            with Image.open(target) as saved:
                self.assertEqual(saved.size, (40, 30))


class MissingToolTests(unittest.TestCase):
    def test_missing_cwebp_gives_a_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(sharp_webp, "cwebp_tool", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "cwebp"):
                save_sharp_webp(Image.new("RGB", (8, 8)), Path(tmp) / "x.webp", 90, 4)


if __name__ == "__main__":
    unittest.main()
