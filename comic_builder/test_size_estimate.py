"""Estimating the size of the finished comic before export."""
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from .backend import BookPlan, ExportOptions, estimate_export_size, export_book, scan_folder


def make_pages(folder, count, seed=1):
    """Pages with varying detail, so they compress differently, as real pages do."""
    rng = np.random.default_rng(seed)
    for number in range(count):
        step = int(rng.integers(2, 12))
        noise = rng.integers(0, 255, (120 // step + 1, 80 // step + 1, 3), dtype=np.uint8)
        page = Image.fromarray(noise).resize((400, 600), Image.Resampling.BICUBIC)
        page.filter(ImageFilter.GaussianBlur(1)).save(folder / f"{number:03}.jpg", quality=92)


class SizeEstimateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name) / "pages"
        self.folder.mkdir()
        make_pages(self.folder, 30)
        self.plan = BookPlan(scan_folder(self.folder, manual=True))
        for page in (4, 15, 24):
            self.plan.join(page, override_boundary=True)

    def tearDown(self):
        self._tmp.cleanup()

    def assertClose(self, estimate, actual, tolerance=0.15):
        self.assertLess(abs(estimate - actual) / actual, tolerance, f"estimate {estimate:,} vs actual {actual:,}")

    def check(self, name, **settings):
        opt = ExportOptions(name, workers=1, **settings)
        estimate = estimate_export_size(self.plan, opt)
        outputs = export_book(self.plan, opt)
        outputs = outputs if isinstance(outputs, tuple) else (outputs,)
        variants = ("full", "reduced") if opt.size_output == "both" else (opt.size_output,)
        self.assertEqual(set(estimate), set(variants))
        for variant, output in zip(variants, outputs, strict=True):
            self.assertClose(estimate[variant], output.stat().st_size)
        return estimate

    def test_reencoded_formats(self):
        self.check("jpeg", format="jpeg", keep_originals=False)
        self.check("webp", format="webp", keep_originals=False)
        self.check("cb7", format="jpeg", keep_originals=False, archive_format="cb7")

    def test_copied_pages_are_counted_exactly(self):
        # Only the three joined spreads are encoded; the rest are copied as they are.
        opt = ExportOptions("copied", workers=1, format="jpeg", keep_originals=True, spread_format="jpeg")
        estimate = estimate_export_size(self.plan, opt)["full"]
        copied = sum(page.path.stat().st_size for index, page in enumerate(self.plan.scan.pages)
                     if index not in (4, 5, 15, 16, 24, 25))
        self.assertGreater(estimate, copied)
        self.check("copied", format="jpeg", keep_originals=True, spread_format="jpeg")

    def test_full_and_reduced_copies_are_estimated_separately(self):
        estimate = self.check("both", format="jpeg", size_output="both", resize_pixels=300)
        self.assertLess(estimate["reduced"], estimate["full"])

    def test_cancelling_stops_early(self):
        calls = []
        def cancelled():
            calls.append(1)
            return True
        opt = ExportOptions("cancel", workers=1, format="jpeg", keep_originals=False)
        self.assertIsNone(estimate_export_size(self.plan, opt, cancelled=cancelled))
        self.assertEqual(len(calls), 1)

    def test_the_same_settings_give_the_same_estimate(self):
        opt = ExportOptions("again", workers=1, format="jpeg", keep_originals=False)
        self.assertEqual(estimate_export_size(self.plan, opt), estimate_export_size(self.plan, opt))


if __name__ == "__main__":
    unittest.main()
