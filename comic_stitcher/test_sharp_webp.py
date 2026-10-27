"""WebP with sharp RGB->YUV conversion, encoded by libwebp's cwebp."""
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import subprocess

import numpy as np
from PIL import Image, ImageCms, features

from . import sharp_webp
from .export import ExportOptions, _save_image
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
        with patch("comic_stitcher.export.save_sharp_webp", side_effect=AssertionError("cwebp used")):
            _save_image(image, target, "webp", ExportOptions("t", webp_sharp_yuv=True, webp_lossless=True))

    def test_png_input_as_windows_uses_gives_the_same_file(self):
        # Google's Windows cwebp can't read PAM, so Windows sends a PNG instead.
        rng = np.random.default_rng(4)
        for mode, depth in (("RGB", 3), ("RGBA", 4)):
            image = Image.fromarray(rng.integers(0, 255, (64, 48, depth), dtype=np.uint8), mode)
            image.info["icc_profile"] = PROFILE
            png = sharp_webp._input(image, windows=True)
            with Image.open(io.BytesIO(png)) as decoded:
                self.assertEqual(decoded.tobytes(), image.tobytes())
                self.assertNotIn("icc_profile", decoded.info)
            outputs = []
            # Encode both inputs with the installed cwebp, when it reads PNG.
            for data in (sharp_webp._pam(image), png):
                result = subprocess.run([sharp_webp.cwebp_tool(), "-quiet", "-q", "90", "-sharp_yuv",
                                         "-o", "-", "--", "-"], input=data, capture_output=True)
                outputs.append(result.stdout if result.returncode == 0 else None)
            if os.name == "nt":
                self.assertIsNotNone(outputs[1])  # The Windows build must read the PNG.
            elif outputs[1] is None:
                self.skipTest("this cwebp was built without PNG input")
            if outputs[0] is not None:  # Google's Windows build can't read PAM at all.
                self.assertEqual(outputs[0], outputs[1])

    def test_writes_to_a_file_object_for_the_size_estimate(self):
        image = red_and_blue_lettering()
        buffer = io.BytesIO()
        _save_image(image, buffer, "webp", ExportOptions("t", webp_sharp_yuv=True))
        target = self.folder / "page.webp"
        _save_image(image, target, "webp", ExportOptions("t", webp_sharp_yuv=True))
        self.assertEqual(buffer.getvalue(), target.read_bytes())

    def test_greyscale_and_palette_pages_are_converted_first(self):
        for image in (Image.new("L", (40, 30), 128), Image.new("P", (40, 30), 3)):
            target = self.folder / "page.webp"
            save_sharp_webp(image, target, 90, 4)
            with Image.open(target) as saved:
                self.assertEqual(saved.size, (40, 30))


OVERFLOW = (b"Error! Cannot encode picture as WebP\nError code: 6 (PARTITION0_OVERFLOW: Partition #0 is "
            b"too big to fit 512k.)")


class PartitionOverflowTests(unittest.TestCase):
    """Huge, finely detailed pages can overflow lossy WebP's first partition (error 6)."""

    def fake_cwebp(self, works_with):
        calls = []
        def run(command, **_):
            calls.append(command)
            ok = works_with is not None and all(option in command for option in works_with)
            return subprocess.CompletedProcess(command, 0 if ok else 255,
                                               stdout=pillow_webp(Image.new("RGB", (8, 8))) if ok else b"",
                                               stderr=b"" if ok else OVERFLOW)
        return calls, patch.object(sharp_webp.subprocess, "run", run)

    def test_overflow_is_retried_with_relaxed_limits(self):
        calls, fake = self.fake_cwebp(works_with=("-partition_limit", "100"))
        with tempfile.TemporaryDirectory() as tmp, fake, \
                patch.object(sharp_webp, "cwebp_tool", return_value="cwebp"):
            target = Path(tmp) / "page.webp"
            save_sharp_webp(Image.new("RGB", (8, 8)), target, 90, 4)
            self.assertTrue(target.read_bytes().startswith(b"RIFF"))
        self.assertEqual([c[c.index("-sharp_yuv") + 1:c.index("-o")] for c in calls],
                         [[], ["-partition_limit", "50"], ["-partition_limit", "100"]])

    def test_fast_effort_levels_retry_at_effort_3_first(self):
        calls, fake = self.fake_cwebp(works_with=("-m", "3"))
        with tempfile.TemporaryDirectory() as tmp, fake, \
                patch.object(sharp_webp, "cwebp_tool", return_value="cwebp"):
            save_sharp_webp(Image.new("RGB", (8, 8)), Path(tmp) / "page.webp", 90, 0)
        self.assertEqual([c[c.index("-m") + 1] for c in calls], ["0", "3"])
        self.assertNotIn("-partition_limit", calls[1])

    def test_a_page_that_never_fits_says_what_to_do(self):
        _, fake = self.fake_cwebp(works_with=None)
        with tempfile.TemporaryDirectory() as tmp, fake, \
                patch.object(sharp_webp, "cwebp_tool", return_value="cwebp"):
            with self.assertRaisesRegex(sharp_webp.PartitionOverflow, "JPEG or PNG"):
                save_sharp_webp(Image.new("RGB", (8, 8)), Path(tmp) / "page.webp", 90, 4)

    def test_other_cwebp_errors_are_not_retried(self):
        calls = []
        def run(command, **_):
            calls.append(command)
            return subprocess.CompletedProcess(command, 255, stdout=b"", stderr=b"Error! Out of memory")
        with tempfile.TemporaryDirectory() as tmp, patch.object(sharp_webp.subprocess, "run", run), \
                patch.object(sharp_webp, "cwebp_tool", return_value="cwebp"):
            with self.assertRaisesRegex(RuntimeError, "Out of memory"):
                save_sharp_webp(Image.new("RGB", (8, 8)), Path(tmp) / "page.webp", 90, 4)
        self.assertEqual(len(calls), 1)

    def test_pillow_overflow_falls_back_to_cwebp_or_explains(self):
        image = Image.new("RGB", (8, 8))
        original_save = Image.Image.save
        def overflowing_save(self, fp, format=None, **params):
            if format == "WEBP" and not params.get("lossless"):
                raise ValueError("encoding error 6")
            return original_save(self, fp, format, **params)
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(Image.Image, "save", overflowing_save):
            target = Path(tmp) / "page.webp"
            with patch("comic_stitcher.export.sharp_yuv_available", return_value=True), \
                    patch("comic_stitcher.export.save_sharp_webp") as sharp:
                _save_image(image, target, "webp", ExportOptions("t", webp_quality=80, webp_method=5))
            sharp.assert_called_once()
            self.assertEqual(sharp.call_args.args[2:4], (80, 5))
            self.assertFalse(sharp.call_args.kwargs["sharp_yuv"])  # Only the limits change.
            with patch("comic_stitcher.export.sharp_yuv_available", return_value=False):
                with self.assertRaisesRegex(RuntimeError, "too large and detailed"):
                    _save_image(image, target, "webp", ExportOptions("t"))


def dithered_page(size=4000, seed=0):
    """A finely dithered page, like a halftone scan, that overflows at effort 0."""
    rng = np.random.default_rng(seed)
    field = Image.fromarray((rng.random((size // 40, size // 40)) * 255).astype(np.uint8)).resize(
        (size, size), Image.BICUBIC)
    dots = ((rng.random((size, size)) < np.asarray(field, np.float32) / 255) * 255).astype(np.uint8)
    return Image.fromarray(np.stack([dots // 3, dots // 2 + 40, dots], -1))


class RealOverflowTests(unittest.TestCase):
    """The real libwebp limit, on a page like the halftone scan that stopped an export."""

    @classmethod
    def setUpClass(cls):
        cls.page = dithered_page()
        try:
            pillow_webp(cls.page, quality=90, method=0)
        except ValueError as exc:
            if "encoding error 6" not in str(exc):
                raise
        else:
            raise unittest.SkipTest("this libwebp doesn't overflow on the test page")

    def test_pillow_encode_retries_at_effort_3(self):
        buffer = io.BytesIO()
        with patch("comic_stitcher.export.sharp_yuv_available", return_value=False):
            _save_image(self.page, buffer, "webp", ExportOptions("t", webp_quality=90, webp_method=0))
        self.assertEqual(buffer.getvalue(), pillow_webp(self.page, quality=90, method=3))

    @unittest.skipUnless(sharp_webp.sharp_yuv_available(), "cwebp is not installed")
    def test_sharp_yuv_encode_retries_at_effort_3(self):
        buffer = io.BytesIO()
        _save_image(self.page, buffer, "webp",
                    ExportOptions("t", webp_quality=90, webp_method=0, webp_sharp_yuv=True))
        with Image.open(io.BytesIO(buffer.getvalue())) as saved:
            self.assertEqual(saved.size, self.page.size)


class MissingToolTests(unittest.TestCase):
    def test_missing_cwebp_gives_a_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(sharp_webp, "cwebp_tool", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "cwebp"):
                save_sharp_webp(Image.new("RGB", (8, 8)), Path(tmp) / "x.webp", 90, 4)


if __name__ == "__main__":
    unittest.main()
