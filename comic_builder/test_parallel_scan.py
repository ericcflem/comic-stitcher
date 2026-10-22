"""Scanning spreads pages across worker processes, with the same results."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from . import backend
from .backend import scan_folder
from .test_detector_seams import line_art, split


class ParallelScanTests(unittest.TestCase):
    def test_parallel_scan_matches_a_single_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            number = 0
            for seed in range(8):  # Eight split spreads: sixteen borderless pages.
                for half in split(line_art(seed, size=(600, 400))):
                    number += 1
                    Image.fromarray(half).save(folder / f"{number:03d}.png")
            with patch.object(backend, "SCAN_PARALLEL_MIN_PAGES", 4), patch.object(backend, "SCAN_PARALLEL_MIN_BYTES", 0):
                steps = []
                parallel = scan_folder(folder, workers=2, progress=lambda done, total: steps.append((done, total)))
            single = scan_folder(folder, workers=1)
            key = lambda scan: [(c.left, c.right, c.continuity, c.likely, c.metrics) for c in scan.candidates]
            self.assertEqual(key(parallel), key(single))
            self.assertEqual([(p.path, p.group, p.width) for p in parallel.pages],
                             [(p.path, p.group, p.width) for p in single.pages])
            self.assertTrue(single.candidates)
            self.assertEqual(steps[-1], (32, 32))
            self.assertEqual([done for done, _ in steps], sorted(done for done, _ in steps))

    def test_unreadable_page_names_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for number in range(1, 6):
                Image.fromarray(np.full((60, 40, 3), 128, np.uint8)).save(folder / f"{number:03d}.png")
            (folder / "006.png").write_bytes(b"not an image")
            with patch.object(backend, "SCAN_PARALLEL_MIN_PAGES", 4), \
                    patch.object(backend, "SCAN_PARALLEL_MIN_BYTES", 0):
                with self.assertRaisesRegex(ValueError, "006.png"):
                    scan_folder(folder, workers=2)


if __name__ == "__main__":
    unittest.main()
