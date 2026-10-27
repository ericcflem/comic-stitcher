"""When lossless WebP is suggested: clean line art, never scans or other formats."""
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .backend import BookPlan, scan_folder
from .export import (ExportOptions, LOSSLESS_CHECK_SKIP_SHARE, LOSSLESS_HINT_SHARE,
    estimate_export_size, lossless_alternative, lossless_check)


def line_art(path: Path, seed: int):
    """A strip-like page: black ink lines and lettering on flat white."""
    rng = np.random.default_rng(seed)
    image = Image.new("RGB", (1200, 900), "white")
    draw = ImageDraw.Draw(image)
    for _ in range(140):
        x, y = rng.integers(0, 1200), rng.integers(0, 900)
        draw.line((x, y, x + rng.integers(-200, 200), y + rng.integers(-200, 200)), fill=(35, 31, 32),
                  width=int(rng.integers(2, 6)))
    for frame in range(4):
        draw.rectangle((20 + frame * 295, 20, 290 + frame * 295, 880), outline=(35, 31, 32), width=4)
    image.save(path)


def scan(path: Path, seed: int):
    """A noisy colour scan: paper grain and shading everywhere."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:900, 0:1200]
    base = np.stack([(x * 0.2 + y * 0.1) % 255, (y * 0.25) % 255, (x * 0.15) % 255], axis=2)
    noisy = np.clip(base + rng.normal(0, 18, base.shape), 0, 255).astype(np.uint8)
    Image.fromarray(noisy, "RGB").save(path)


class AlternativeTests(unittest.TestCase):
    def test_compared_only_for_lossy_full_size_archives(self):
        alternative = lossless_alternative(ExportOptions("Book", format="jpeg"))
        self.assertEqual((alternative.format, alternative.webp_lossless, alternative.size_output),
                         ("webp", True, "full"))
        self.assertIsNotNone(lossless_alternative(ExportOptions("Book", format="webp")))
        self.assertIsNotNone(lossless_alternative(ExportOptions("Book", size_output="both", archive_format="cb7")))
        # No archive saves pages in the chosen format (WebP at 95, say), so they compare too.
        self.assertIsNotNone(lossless_alternative(ExportOptions("Book", format="webp", webp_quality=95, cbz=False)))
        # PNG is lossless already, but lossless WebP is often smaller with the same pixels.
        self.assertEqual(lossless_alternative(ExportOptions("Book", format="png")).format, "webp")
        self.assertIsNotNone(lossless_alternative(ExportOptions("Book", format="png", cbz=False)))
        for already in (ExportOptions("Book", format="webp", webp_lossless=True),
                        ExportOptions("Book", archive_format="pdf"),
                        ExportOptions("Book", size_output="reduced")):
            self.assertIsNone(lossless_alternative(already), already)

    def test_copied_pages_compare_their_joined_spreads_format(self):
        alternative = lossless_alternative(ExportOptions("Book", keep_originals=True, spread_format="jpeg"))
        self.assertEqual((alternative.spread_format, alternative.webp_lossless), ("webp", True))
        self.assertIsNotNone(lossless_alternative(ExportOptions("Book", keep_originals=True, spread_format="png")))
        self.assertIsNone(lossless_alternative(ExportOptions("Book", keep_originals=True, spread_format="webp",
                                                             webp_lossless=True)))


class EstimateTests(unittest.TestCase):
    def estimates(self, make):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for page in range(6):
                make(folder / f"{page:02d}.png", page)
            plan = BookPlan(scan_folder(folder, manual=True))
            opt = ExportOptions("Book", format="jpeg", jpeg_quality=90, workers=1)
            jpeg = estimate_export_size(plan, opt)["full"]
            lossless = estimate_export_size(plan, lossless_alternative(opt))["full"]
            return jpeg, lossless

    def test_line_art_is_suggested(self):
        jpeg, lossless = self.estimates(line_art)
        self.assertLess(lossless, LOSSLESS_HINT_SHARE * jpeg, (jpeg, lossless))

    def test_noisy_scans_are_not(self):
        jpeg, lossless = self.estimates(scan)
        self.assertGreater(lossless, LOSSLESS_HINT_SHARE * jpeg, (jpeg, lossless))


class QuickCheckTests(unittest.TestCase):
    def share(self, make):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for page in range(8):
                make(folder / f"{page:02d}.png", page)
            plan = BookPlan(scan_folder(folder, manual=True))
            return lossless_check(plan, ExportOptions("Book", format="jpeg", jpeg_quality=90))

    def test_line_art_passes_the_quick_check(self):
        self.assertLess(self.share(line_art), LOSSLESS_HINT_SHARE)

    def test_scans_are_ruled_out_early(self):
        self.assertGreater(self.share(scan), LOSSLESS_CHECK_SKIP_SHARE)

    def test_nothing_to_check_when_already_lossless_or_cancelled(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            line_art(folder / "01.png", 1)
            plan = BookPlan(scan_folder(folder, manual=True))
            self.assertIsNone(lossless_check(plan, ExportOptions("Book", format="webp", webp_lossless=True)))
            # PNG is compared too: lossless WebP keeps the same pixels in fewer bytes.
            self.assertLess(lossless_check(plan, ExportOptions("Book", format="png")), 1)
            self.assertIsNone(lossless_check(plan, ExportOptions("Book"), cancelled=lambda: True))

if __name__ == "__main__":
    unittest.main()
