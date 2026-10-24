"""Crop proposals for emulator screenshots, including black artwork."""
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from PIL import Image, ImageDraw

if __package__:
    from .screenshot_geometry import _book_widths, main, propose_crops
else:
    from screenshot_geometry import _book_widths, main, propose_crops


class ScreenshotGeometryTest(unittest.TestCase):
    def test_multiple_families_keep_exact_sizes_and_use_smallest_fitting_crop(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            expected = []
            for width in (650, 650, 666, 666, 1300, 1300, 1332, 1332):
                path = Path(directory) / f"{len(paths)}.png"
                image = Image.new("RGB", (1400, 1000), "black")
                left = (1400 - width) // 2
                image.paste("white", (left, 0, left + width, 1000))
                image.save(path)
                paths.append(path)
                expected.append(width)
            dark = Path(directory) / "dark.png"
            image = Image.new("RGB", (1400, 1000), "black")
            image.paste("red", (100, 100, 1300, 900))
            image.save(dark)
            # The dark last page looks like a clean page 8% narrower; too narrow to
            # be a cover of its own size, so the page sizes decide its crop.
            proposals = propose_crops(paths + [dark])
            self.assertEqual([p.page_width for p in proposals[:-1]], expected)
            self.assertTrue(all(p.source == "measured" for p in proposals[:-1]))
            self.assertTrue(all(p.family_widths == (650, 666) for p in proposals))
            self.assertEqual(proposals[-1].page_width, 1300)
            self.assertEqual(proposals[-1].family_width, 650)

    def covers_book(self, directory, cover_width):
        """A book whose covers differ from its 650 px pages, with black-bordered
        spreads (16-18) and singles (19-20)."""
        def shot(name, width, black=False):
            image = Image.new("RGB", (1400, 1000), "black")
            left = (1400 - width) // 2
            draw = ImageDraw.Draw(image)
            if black:  # Ragged panels inside a black border that blends into the screenshot.
                for row, (top, inset) in enumerate(((60, 70), (330, 95), (610, 80))):
                    draw.rectangle((left + inset + 7 * row, top, left + width - inset - 11 * row, top + 240),
                                   fill=(200, 90 + 40 * row, 40))
            else:
                image.paste("white", (left, 0, left + width, 1000))
                draw.rectangle((left + 30, 40, left + width - 30, 960), fill=(180, 60, 60))
            path = Path(directory) / f"{name}.png"
            image.save(path)
            return path
        paths = [shot("000", cover_width)]
        paths += [shot(f"{i:03d}", 1300 if i % 5 == 0 else 650) for i in range(1, 16)]
        paths += [shot(f"{i:03d}", 1300, black=True) for i in range(16, 19)]
        paths += [shot(f"{i:03d}", 650, black=True) for i in range(19, 21)]
        paths += [shot("021", cover_width)]
        return paths

    def test_covers_of_their_own_size_dont_set_the_page_sizes(self):
        # 644 against 650 is 1969 against 1988 px; DC covers can be wider (2063).
        for cover in (644, 674):
            with self.subTest(cover=cover), tempfile.TemporaryDirectory() as directory:
                proposals = propose_crops(self.covers_book(directory, cover))
                self.assertEqual([(p.page_width, p.source) for p in (proposals[0], proposals[-1])],
                                 [(cover, "measured")] * 2)
                self.assertTrue(all(p.page_width in (650, 1300) for p in proposals[1:16]))
                self.assertFalse(any(p.family_widths for p in proposals))  # One page size, not two.
                # The black spreads take the pages' double crop, not twice the cover.
                self.assertEqual({(p.kind, p.page_width) for p in proposals[16:19]}, {("double", 1300)})
                # Black singles: everything visible fits the one page size, so no review.
                self.assertEqual({(p.kind, p.box, p.source) for p in proposals[19:21]},
                                 {("single", (375, 0, 1025, 1000), "suggested")})

    def test_straight_panel_edges_inside_a_black_border_dont_make_a_page_size(self):
        # Fire Power: black-bordered spreads whose panels end in straight vertical
        # lines measured as clean pages 3810-3826 wide and made two page sizes
        # beside the real 1988. Their top and bottom rows are black: the border.
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index in range(6):
                image = Image.new("RGB", (1400, 1000), "black")
                image.paste("white", (50, 0, 1350, 1000))
                draw = ImageDraw.Draw(image)
                draw.rectangle((90, 40, 1310, 960), fill=(180, 60, 60))
                paths.append(Path(directory) / f"{index}.png")
                image.save(paths[-1])
            for index, inset in enumerate((30, 31, 32), 6):
                image = Image.new("RGB", (1400, 1000), "black")
                draw = ImageDraw.Draw(image)
                for top in (25, 345, 665):  # Full-width panels, straight sides, black gutters.
                    draw.rectangle((50 + inset, top, 1349 - inset, top + 300), fill=(60, 120, 200))
                paths.append(Path(directory) / f"{index}.png")
                image.save(paths[-1])
            proposals = propose_crops(paths)
            self.assertFalse(any(p.family_widths for p in proposals))
            self.assertEqual({(p.kind, p.box) for p in proposals[6:]}, {("double", (50, 0, 1350, 1000))})

    def test_hidden_edge_single_with_two_page_sizes_still_goes_to_review(self):
        # Two real page sizes (Marvel 1988 and 2063, say): which one a black page
        # belongs to can't be told from its artwork, so it's left for review.
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index, width in enumerate((650, 650, 650, 690, 690, 690)):
                image = Image.new("RGB", (1400, 1000), "black")
                left = (1400 - width) // 2
                image.paste("white", (left, 0, left + width, 1000))
                paths.append(Path(directory) / f"{index}.png")
                image.save(paths[-1])
            image = Image.new("RGB", (1400, 1000), "black")
            ImageDraw.Draw(image).rectangle((460, 100, 930, 900), fill=(200, 90, 40))
            paths.append(Path(directory) / "black.png")
            image.save(paths[-1])
            black = propose_crops(paths)[-1]
            self.assertEqual(black.source, "review")
            self.assertIsNone(black.box)

    def test_different_capture_heights_keep_separate_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for width, height in ((650, 1000), (1300, 1000), (700, 1050), (1400, 1050)):
                path = Path(directory) / f"{len(paths)}.png"
                image = Image.new("RGB", (1500, height), "black")
                left = (1500 - width) // 2
                image.paste("white", (left, 0, left + width, height))
                image.save(path)
                paths.append(path)
            proposals = propose_crops(paths)
            self.assertEqual([p.page_width for p in proposals], [650, 1300, 700, 1400])
            self.assertFalse(any(p.family_widths for p in proposals))

    def test_full_resolution_mixed_family_clear_boundaries_keep_all_crops(self):
        from unittest.mock import patch
        if __package__:
            from . import screenshot_geometry as geometry
        else:
            import screenshot_geometry as geometry
        observations = []
        widths = [1986, 1987, 1988, 3974, 3975, 3976, 2037, 2038, 4074] * 4
        for index, width in enumerate(widths):
            left = (4200 - width) // 2
            observations.append((Path(f"{index}.png"), (4200, 3056),
                                 (left, left + width), geometry._kind(width, 3056),
                                 False, True, None))
        lookup = {o[0]: o for o in observations}
        with patch.object(geometry, "_observe", side_effect=lookup.get):
            proposals = propose_crops(lookup, workers=1)
        self.assertEqual([p.page_width for p in proposals], widths)
        self.assertTrue(all(p.source == "measured" for p in proposals))

    def test_sparse_edge_noise_does_not_hide_white_page_families(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            expected = [650, 650, 666, 666]
            for index, width in enumerate(expected):
                image = Image.new("RGB", (1400, 1000), "black")
                left = (1400 - width) // 2
                image.paste("white", (left, 0, left + width, 1000))
                draw = ImageDraw.Draw(image)
                # Outlying marks make the first nonblack edge sparse; the
                # full-height page boundary remains strong and measurable.
                draw.rectangle((left - 5, 300, left - 4, 310), fill=(12, 12, 12))
                draw.rectangle((left + width + 3, 300, left + width + 4, 310), fill=(12, 12, 12))
                path = Path(directory) / f"{index}.png"
                image.save(path)
                paths.append(path)
            proposals = propose_crops(paths)
            self.assertEqual([p.page_width for p in proposals], expected)
            self.assertTrue(all(p.source == "measured" for p in proposals))
            self.assertTrue(all(p.family_widths == (650, 666) for p in proposals))

    def test_middle_width_with_one_double_edge_is_still_reviewed(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            with Image.new("RGB", (420, 306), "black") as image:
                image.paste("white", (11, 0, 408, 306))
                clear = folder / "clear.png"
                image.save(clear)
            with Image.new("RGB", (420, 306), "black") as image:
                image.paste("red", (11, 0, 250, 306))
                middle = folder / "middle.png"
                image.save(middle)
            item = {p.path: p for p in propose_crops([clear, middle])}[middle]
            self.assertIsNone(item.box)
            self.assertEqual(item.source, "review")
            self.assertIn("middle width", item.review_reason)

    def test_parallel_measurement_preserves_order_and_proposals(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            paths = []
            for index, bounds in enumerate(((11, 408), (111, 309), (11, 408), (111, 309))):
                with Image.new("RGB", (420, 306), "black") as image:
                    image.paste("white", (bounds[0], 0, bounds[1], 306))
                    path = folder / f"{index}.png"
                    image.save(path)
                    paths.append(path)
            sequential = propose_crops(paths, workers=1)
            progress = []
            parallel = propose_crops(paths, workers=4,
                                     progress=lambda done, total: progress.append((done, total)))
            self.assertEqual(parallel, sequential)
            self.assertEqual(progress, [(i, 4) for i in range(1, 5)])

    def test_sparse_marks_across_calibrated_double_are_batch_suggestions(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            canvas = (4200, 3056)
            with Image.new("RGB", canvas, "black") as image:
                image.paste("white", (112, 0, 4088, 3056))
                anchor = folder / "clear-double.png"
                image.save(anchor)
            dark_pages = []
            for number, left, right in ((94, 112, 4064),
                                        (95, 136, 4064),
                                        (96, 136, 4088)):
                with Image.new("RGB", canvas, "black") as image:
                    draw = ImageDraw.Draw(image)
                    draw.rectangle((left, 1200, left + 17, 1229), fill="white")
                    draw.rectangle((right - 18, 1200, right - 1, 1229), fill="white")
                    path = folder / f"dark-{number}.png"
                    image.save(path)
                    dark_pages.append(path)
            items = {p.path: p for p in propose_crops([anchor, *dark_pages])}
            for path in dark_pages:
                self.assertEqual(items[path].source, "suggested")
                self.assertEqual(items[path].box, (112, 0, 4088, 3056))
                self.assertEqual(items[path].kind, "double")

    def test_sparse_edge_text_on_unusual_wide_page_is_not_cut(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            canvas = (4200, 3056)
            with Image.new("RGB", canvas, "black") as image:
                image.paste("white", (1106, 0, 3094, 3056))
                single = folder / "single.png"
                image.save(single)
            with Image.new("RGB", canvas, "black") as image:
                image.paste("white", (112, 0, 4088, 3056))
                double = folder / "double.png"
                image.save(double)
            with Image.new("RGB", canvas, "black") as image:
                draw = ImageDraw.Draw(image)
                draw.text((900, 1200), "A THOUGHT AT THE LEFT EDGE", fill="white")
                draw.text((3140, 1200), "A THOUGHT AT THE RIGHT EDGE", fill="white")
                ink = image.getbbox()
                unusual = folder / "black-spread.png"
                image.save(unusual)
            with Image.new("RGB", canvas, "black") as image:
                image.paste("orange", (1600, 1000, 2600, 2000))
                ambiguous = folder / "small-image-on-black.png"
                image.save(ambiguous)
            items = {p.path: p for p in propose_crops([single, double, unusual, ambiguous])}
            self.assertEqual(items[single].page_width, 1988)
            self.assertEqual(items[double].page_width, 3976)
            suggestion = items[unusual]
            self.assertIsNone(suggestion.box)
            self.assertEqual(suggestion.kind, "uncertain")
            self.assertEqual(suggestion.source, "review")
            self.assertIn("sideways", suggestion.review_reason)
            self.assertIsNotNone(suggestion.measured_box)
            self.assertEqual(suggestion.suggested_box, (112, 0, 4088, 3056))
            self.assertLessEqual(suggestion.measured_box[0], ink[0])
            self.assertGreaterEqual(suggestion.measured_box[2], ink[2])
            # A small image on black fits inside a single page: the single crop.
            self.assertEqual((items[ambiguous].kind, items[ambiguous].box, items[ambiguous].source),
                             ("single", (1106, 0, 3094, 3056), "suggested"))

    def test_book_derived_width_buckets_and_sideways_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            canvas = (4200, 3056)
            with Image.new("RGB", canvas, "black") as image:
                image.paste("white", (112, 0, 4088, 3056))
                anchor = folder / "clear-double.png"
                image.save(anchor)
            sideways, cutoff = _book_widths(canvas, {"single": 1988, "double": 3976})
            self.assertEqual(sideways, 2349)
            self.assertEqual(cutoff, 2501)
            cases = []
            for name, left, right, expected in (
                    ("2500.png", 850, 3350, "review"),
                    ("2501.png", 849, 3350, "review"),
                    ("2502.png", 849, 3351, "suggested"),
                    ("3000.png", 600, 3600, "suggested")):
                with Image.new("RGB", canvas, "black") as image:
                    image.paste("red", (left, 1200, right, 1300))
                    path = folder / name
                    image.save(path)
                    cases.append((path, expected))
            items = {p.path: p for p in propose_crops([anchor, *(p for p, _ in cases)])}
            for path, expected in cases:
                self.assertEqual(items[path].source, expected)
                self.assertEqual(items[path].box,
                                 (112, 0, 4088, 3056) if expected == "suggested" else None)
                if expected == "review":
                    self.assertIn("sideways", items[path].review_reason)
            with Image.new("RGB", canvas, "black") as image:
                image.paste("white", ((canvas[0] - sideways) // 2, 0,
                                      (canvas[0] - sideways) // 2 + sideways, canvas[1]))
                rotated = folder / "sideways.png"
                image.save(rotated)
            rotated_result = {p.path: p for p in propose_crops([anchor, rotated])}[rotated]
            self.assertEqual(rotated_result.source, "review")
            self.assertEqual(rotated_result.kind, "sideways")
            self.assertIn("taller screenshot", rotated_result.review_reason)

    def test_single_only_calibrates_double_and_1986_pixel_variant(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            canvas = (4200, 3056)
            paths = []
            for name, x0, x1 in (("cover.png", 1106, 3094),
                                 ("single-1986.png", 1107, 3093),
                                 ("dark-double.png", 600, 3600)):
                with Image.new("RGB", canvas, "black") as image:
                    image.paste("white", (x0, 0, x1, canvas[1]))
                    path = folder / name
                    image.save(path)
                    paths.append(path)
            items = {p.path: p for p in propose_crops(paths)}
            self.assertEqual(items[paths[1]].box, (1107, 0, 3093, 3056))
            self.assertEqual(items[paths[2]].box, (112, 0, 4088, 3056))
            self.assertEqual(items[paths[2]].source, "suggested")

    def test_folder_report_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "page01.png"
            with Image.new("RGB", (420, 306), "black") as image:
                image.paste("white", (111, 0, 309, 306))
                image.save(source)
            (folder / "page02.png").write_bytes(b"not a PNG")
            original = source.read_bytes()
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main([str(folder)]), 0)
            self.assertIn("SINGLE", output.getvalue())
            self.assertIn("198x306", output.getvalue())
            self.assertIn("ERROR", output.getvalue())
            self.assertIn("No images were modified", output.getvalue())
            self.assertEqual(source.read_bytes(), original)

    def test_mixed_widths_and_black_artwork_keep_comic_edges(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)

            def screenshot(name, canvas, box, color="white", black_left=0,
                           black_right=0, noise=False):
                with Image.new("RGB", canvas, "black") as im:
                    x0, x1 = box
                    im.paste(color, (x0 + black_left, 0, x1 - black_right, canvas[1]))
                    if noise:
                        im.putpixel((0, 0), (255, 255, 255))
                    path = folder / name
                    im.save(path)
                return path

            single = screenshot("01.png", (420, 306), (111, 309), noise=True)
            double = screenshot("02.png", (420, 306), (11, 408))
            odd_single = screenshot("03.png", (420, 306), (111, 310))
            black_art = screenshot("04.png", (420, 306), (111, 309), black_left=20)
            black_spread = screenshot("05.png", (420, 306), (11, 408), black_right=80)
            near_full_spread = screenshot("05b.png", (420, 306), (11, 408),
                                          black_left=9, black_right=9)
            hidden_both = screenshot("06.png", (420, 306), (11, 408),
                                     black_left=145, black_right=145)
            taller = screenshot("07.png", (440, 326), (3, 437))
            items = {p.path: p for p in propose_crops(
                [single, double, odd_single, black_art, black_spread,
                 near_full_spread, hidden_both, taller])}

            self.assertEqual(items[single].box, (111, 0, 309, 306))
            self.assertEqual(items[double].box, (11, 0, 408, 306))
            self.assertEqual(items[odd_single].page_width, 199)
            self.assertEqual(items[black_art].box, (111, 0, 309, 306))
            self.assertEqual(items[black_art].source, "profile")
            self.assertEqual(items[black_spread].box, (11, 0, 408, 306))
            self.assertEqual(items[black_spread].source, "profile")
            self.assertEqual(items[near_full_spread].box, (11, 0, 408, 306))
            self.assertEqual(items[near_full_spread].source, "suggested")
            # Both edges hidden and everything visible fits a single page: it's
            # taken as a single at the book's page size (Eric's rule), not reviewed.
            self.assertEqual(items[hidden_both].box, (110, 0, 309, 306))
            self.assertEqual(items[hidden_both].source, "suggested")
            self.assertEqual(items[taller].box, (3, 0, 437, 326))


if __name__ == "__main__":
    unittest.main()
