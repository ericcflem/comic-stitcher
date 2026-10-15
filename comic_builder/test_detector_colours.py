"""Plain coloured page edges are borders, whatever the colour."""
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from .backend import scan_folder
from .detector import _strip_metrics, is_solid_border

THRESHOLDS = (16.0, 18.0)


class SolidColourTests(unittest.TestCase):
    def test_flat_colours_are_solid_borders(self):
        rng = np.random.default_rng(1)
        for colour in ((0, 0, 0), (255, 255, 255), (0, 128, 128), (120, 80, 40), (100, 110, 135)):
            for grain in (0, 5):
                strip = np.clip(np.full((800, 14, 3), colour, float) + rng.normal(0, grain, (800, 14, 3)),
                                0, 255).astype(np.uint8)
                self.assertTrue(is_solid_border(_strip_metrics(strip, 14), *THRESHOLDS), (colour, grain))

    def test_artwork_is_not_a_border(self):
        rng = np.random.default_rng(2)
        art = rng.integers(0, 255, (80, 2, 3)).repeat(10, 0).repeat(7, 1).astype(np.uint8)
        self.assertFalse(is_solid_border(_strip_metrics(art, 14), *THRESHOLDS))

    def test_pages_on_matching_colour_backgrounds_are_not_offered_as_spreads(self):
        # Eric's case: pages with solid teal, brown or grey backgrounds and nothing
        # crossing the edge were all suggested as spreads.
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            colours = [(0, 128, 128), (0, 128, 128), (120, 80, 40), (120, 80, 40), (100, 110, 135), (100, 110, 135)]
            for number, colour in enumerate(colours):
                page = Image.new("RGB", (300, 450), colour)
                page.paste((240, 220, 30), (60, 60, 240, 390))  # Artwork away from the edges.
                page.save(folder / f"{number:03}.png")
            scan = scan_folder(folder)
            self.assertEqual(scan.candidates, [])


if __name__ == "__main__":
    unittest.main()
