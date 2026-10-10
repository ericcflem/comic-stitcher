"""Screenshot staging preserves pixels and uses ordinary pair scoring."""
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from .backend import BookPlan, BoundaryOverrideRequired
from .screenshot_import import CropChoice, looks_like_screenshot_folder, stage_screenshots
from .session import cached_folder_scan, read_session, restore_plan, save_session


class ScreenshotImportTest(unittest.TestCase):
    def test_folder_hint_samples_black_emulator_surroundings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            captures = root / "captures"
            pages = root / "pages"
            captures.mkdir()
            pages.mkdir()
            for i in range(5):
                with Image.new("RGB", (420, 306), "black") as image:
                    image.paste("white", (11 if i % 2 else 111, 0,
                                          408 if i % 2 else 309, 306))
                    image.save(captures / f"{i}.png")
                Image.new("RGB", (198, 306), "white").save(pages / f"{i}.png")
            self.assertEqual(looks_like_screenshot_folder(captures), (True, 5))
            self.assertEqual(looks_like_screenshot_folder(pages), (False, 5))

    def test_odd_width_split_preserves_pixels_without_forcing_black_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = root / "screenshots"
            sources.mkdir()
            single = sources / "001.png"
            double = sources / "002.png"
            with Image.new("RGB", (420, 306), "black") as image:
                image.paste("white", (111, 0, 309, 306))
                image.save(single)
            with Image.new("RGB", (420, 306), "black") as image:
                image.putpixel((11, 100), (10, 20, 30))
                image.putpixel((407, 100), (40, 50, 60))
                image.save(double)
            original = [p.read_bytes() for p in (single, double)]
            choices = [CropChoice(single, "single", (111, 0, 309, 306)),
                       CropChoice(double, "double", (11, 0, 408, 306), inferred=True)]
            staged = stage_screenshots(choices, root / "temporary", sources)
            self.assertEqual([p.width for p in staged.scan.pages], [198, 198, 199])
            self.assertEqual(staged.flagged_pages, set())
            self.assertEqual(staged.scan.output_folder, sources)
            self.assertFalse(any(c.left == 1 for c in staged.scan.candidates))
            plan = BookPlan(staged.scan)
            with self.assertRaises(BoundaryOverrideRequired):
                plan.join(1)
            plan.join(1, override_boundary=True)
            self.assertIn(1, plan.approved())
            with Image.open(staged.scan.pages[1].path) as left, \
                 Image.open(staged.scan.pages[2].path) as right:
                self.assertEqual(left.getpixel((0, 100)), (10, 20, 30))
                self.assertEqual(right.getpixel((198, 100)), (40, 50, 60))
            self.assertEqual([p.read_bytes() for p in (single, double)], original)

    def test_parallel_staging_preserves_numbering_and_pixels(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "sources"
            source.mkdir()
            choices = []
            for i in range(4):
                path = source / f"{i}.png"
                with Image.new("RGB", (41, 30), (i * 40, 20, 30)) as image:
                    image.putpixel((40, 5), (i, 100, 200))
                    image.save(path)
                choices.append(CropChoice(path, "double", (1, 0, 40, 30),
                                          flagged=i == 2))
            sequential = stage_screenshots(choices, root / "one", source, workers=1)
            progress = []
            parallel = stage_screenshots(choices, root / "four", source, workers=4,
                                         progress=lambda done, total: progress.append((done, total)))
            self.assertEqual(parallel.flagged_pages, sequential.flagged_pages)
            self.assertEqual(parallel.flagged_pages, {4, 5})
            self.assertEqual([p.path.name for p in parallel.scan.pages],
                             [f"{i:06d}.png" for i in range(1, 9)])
            self.assertEqual([(root / "one" / f"{i:06d}.png").read_bytes()
                              for i in range(1, 9)],
                             [(root / "four" / f"{i:06d}.png").read_bytes()
                              for i in range(1, 9)])
            self.assertEqual(progress[-1], (8, 8))
            self.assertEqual(progress[:4], [(i, 8) for i in range(1, 5)])

    def test_screenshot_review_resumes_from_persistent_staged_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "screenshots"
            source.mkdir()
            capture = source / "001.png"
            Image.new("RGB", (420, 306), "black").save(capture)
            staged = stage_screenshots(
                [CropChoice(capture, "double", (11, 0, 408, 306), True, flagged=True)],
                root / "app" / "screenshot-staging" / "book" / "pages", source,
                manual_mode=True)
            plan = BookPlan(staged.scan)
            self.assertEqual(plan.current()[1].left, 0)
            plan.decide(True)
            save_session(source, root / "app", plan, possible_started=False,
                         stage="review", preview_index=0)
            checkpoint = read_session(source, root / "app")
            self.assertIsNotNone(checkpoint)
            restored_scan = cached_folder_scan(source, checkpoint)
            self.assertEqual(restored_scan.flagged_pages, {0, 1})
            self.assertEqual(restore_plan(restored_scan, checkpoint).approved(), {0})
            Image.new("RGB", (420, 306), "white").save(capture)
            self.assertIsNone(read_session(source, root / "app"))


if __name__ == "__main__":
    unittest.main()
