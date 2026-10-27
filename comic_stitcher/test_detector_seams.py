"""Seam scoring: line art that crosses the seam, slicing debris, and false pairs."""
import unittest

import numpy as np
from PIL import Image, ImageDraw

from .detector import NO_EVIDENCE, classify_page, roughness_ratio, seam_score

LIKELY, POSSIBLE = 0.55, 0.40  # backend.LIKELY_MIN_SCORE and POSSIBLE_MIN_SCORE


def line_art(seed: int, size=(900, 1200)) -> np.ndarray:
    """Black diagonal ink lines on white, like uncoloured comic art."""
    rng = np.random.default_rng(seed)
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    w, h = size
    for _ in range(60):
        x, y = rng.integers(0, w), rng.integers(0, h)
        angle = rng.uniform(np.radians(25), np.radians(75)) * rng.choice((-1, 1))
        length = rng.integers(150, 500)
        draw.line((x, y, x + length * np.cos(angle), y + length * np.sin(angle)),
                  fill="black", width=int(rng.integers(2, 6)))
    return np.asarray(image)


def split(spread: np.ndarray, lost: int = 0):
    """Slice a spread in half, optionally losing a few columns at the cut."""
    mid = spread.shape[1] // 2
    return spread[:, :mid].copy(), spread[:, mid + lost:].copy()


class SeamScoreTests(unittest.TestCase):
    def test_diagonal_line_art_across_the_seam_is_likely(self):
        for seed in range(4):
            left, right = split(line_art(seed), lost=2)
            self.assertGreaterEqual(seam_score(left, right)["continuity"], LIKELY, seed)

    def test_slicing_debris_column_does_not_hide_a_real_spread(self):
        # Publishers' slicing often copies the left page's last column onto
        # the right page, or leaves a blank strip; both must be skipped.
        for seed in range(4):
            left, right = split(line_art(seed + 10))
            copied = np.concatenate([left[:, -1:], right], axis=1)
            blank = right.copy()
            blank[:, :2] = 255
            self.assertGreaterEqual(seam_score(left, copied)["continuity"], LIKELY, seed)
            self.assertGreaterEqual(seam_score(left, blank)["continuity"], LIKELY, seed)

    def test_unrelated_line_art_pages_are_not_suggested(self):
        for seed in range(4):
            left, _ = split(line_art(seed + 20))
            _, right = split(line_art(seed + 40))
            self.assertLess(seam_score(left, right)["continuity"], POSSIBLE, seed)

    def test_copied_edge_column_does_not_make_a_false_pair_likely(self):
        # The debris column matches the neighbour exactly, which fooled the
        # old pixel comparison into a near-perfect score.
        left, _ = split(line_art(60))
        _, right = split(line_art(61))
        right = np.concatenate([left[:, -1:], right], axis=1)
        self.assertLess(seam_score(left, right)["continuity"], POSSIBLE)

    def test_textured_paper_margins_in_different_tints_are_not_suggested(self):
        rng = np.random.default_rng(7)
        paper = lambda tint: np.clip(rng.normal(tint, 6, (1200, 450, 3)), 0, 255).astype(np.uint8)
        score = seam_score(paper((230, 220, 200)), paper((245, 238, 220)))
        self.assertLess(score["continuity"], POSSIBLE)

    def test_blank_seam_offers_no_evidence(self):
        white = np.full((1200, 450, 3), 255, np.uint8)
        self.assertEqual(roughness_ratio(white, white), NO_EVIDENCE)

    def test_a_few_agreeing_rows_are_not_enough_evidence(self):
        # Seen in a real book: a plain white edge strip on one page and a
        # panel line parallel to the seam on the other left only 3 small
        # stretches to compare, which happened to agree and scored 0.96.
        left = np.full((3600, 450, 3), 255, np.uint8)
        right = left.copy()
        for y in (504, 1704, 2904):           # Three short marks that do line up,
            left[y:y + 4, -20:] = 0           # on an otherwise featureless seam:
            right[y:y + 4, :20] = 0           # about 4% of the height has detail.
        self.assertEqual(roughness_ratio(left, right), NO_EVIDENCE)

    def test_small_images_fall_back_to_the_pixel_match(self):
        left, right = split(line_art(3, size=(200, 120)))
        score = seam_score(left, right)
        self.assertIsNone(score["roughness_ratio"])
        self.assertEqual(score["continuity"], score["pixel_match"])

    def test_score_keeps_previous_diagnostic_fields(self):
        left, right = split(line_art(5))
        score = seam_score(left, right)
        for key in ("edge_mad", "inner_mad", "hist_corr", "grad_corr", "continuity",
                    "pixel_match", "roughness_ratio"):
            self.assertIn(key, score)
        self.assertTrue(0 <= score["continuity"] <= 1)


class NarrowBorderTests(unittest.TestCase):
    def classify(self, page: np.ndarray):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "page.png"
            Image.fromarray(page).save(path)
            return classify_page(path, 0, 14, 16.0, 18.0)

    @staticmethod
    def full_bleed_art(seed: int) -> np.ndarray:
        """Artwork that reaches every edge of the page (hatching on grey)."""
        rng = np.random.default_rng(seed)
        return rng.integers(0, 256, (900, 600, 3), dtype=np.uint8)

    def test_white_frame_narrower_than_the_strip_is_a_border(self):
        page = self.full_bleed_art(70)
        page[:, -9:] = 255   # 9 px white frame, artwork right up to it.
        page[:, :6] = 255    # 6 px on the other side.
        info = self.classify(page)
        self.assertTrue(info.right_solid)
        self.assertTrue(info.left_solid)
        self.assertEqual((info.left_stats["plain_px"], info.right_stats["plain_px"]), (6, 9))

    def test_full_bleed_art_is_not_a_border(self):
        info = self.classify(self.full_bleed_art(72))
        self.assertFalse(info.left_solid)
        self.assertFalse(info.right_solid)

    def test_slicing_debris_is_not_mistaken_for_a_border(self):
        page = self.full_bleed_art(71)
        page[:, :2] = 255    # 2 px blank strip left by slicing.
        page[:, -1:] = 0     # 1 px dark line.
        info = self.classify(page)
        self.assertFalse(info.left_solid)
        self.assertFalse(info.right_solid)


if __name__ == "__main__":
    unittest.main()
