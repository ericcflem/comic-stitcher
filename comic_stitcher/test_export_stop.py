"""Stopping an export part-way (Stop now in the queue)."""
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from .backend import BookPlan, scan_folder
from .export import ExportOptions, ExportStopped, export_book


class ExportStopTests(unittest.TestCase):
    def make_plan(self, folder: Path) -> BookPlan:
        for i in range(6):
            Image.new("RGB", (40, 60), (i * 30, 70, 100)).save(folder / f"{i}.png")
        return BookPlan(scan_folder(folder, manual=True))

    def assert_nothing_left(self, folder: Path):
        made = sorted(path.name for path in folder.iterdir() if not path.name.endswith(".png"))
        self.assertEqual(made, [], "the stopped export left files behind")

    def test_stopped_part_way_leaves_nothing(self):
        for workers in (1, 2):  # One page at a time, and the worker processes.
            with self.subTest(workers=workers), tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                plan = self.make_plan(folder)
                pages = []
                opt = ExportOptions("Stopped", format="jpeg", size_output="both", resize_pixels=30,
                                    workers=workers)
                with self.assertRaises(ExportStopped):
                    export_book(plan, opt, progress=lambda n, total, pages=pages: pages.append(n),
                                stop=lambda pages=pages: len(pages) >= 2)
                self.assertLess(max(pages), 14)  # It stopped well before the end.
                self.assert_nothing_left(folder)

    def test_a_stop_after_the_last_page_still_saves_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            plan = self.make_plan(folder)
            asked = []
            with self.assertRaises(ExportStopped):
                export_book(plan, ExportOptions("Late", format="jpeg", workers=1),
                            stop=lambda: asked.append(1) or len(asked) > 7)
            self.assert_nothing_left(folder)

    def test_without_a_stop_request_it_finishes(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            plan = self.make_plan(folder)
            output = export_book(plan, ExportOptions("Whole", format="jpeg", workers=1), stop=lambda: False)
            self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
