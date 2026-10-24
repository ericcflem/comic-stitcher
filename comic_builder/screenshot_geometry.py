"""Detect centered comic pages within black emulator screenshots.

This module proposes crop rectangles only. The source images are never modified.
The book-level profile lets a page with black artwork at an outer edge borrow
the geometry of clear pages captured with the same virtual-device dimensions.
"""
from __future__ import annotations

import argparse
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
from PIL import Image


@dataclass(frozen=True)
class CropProposal:
    path: Path
    canvas: tuple[int, int]
    kind: str  # "single", "double", "sideways", or "uncertain"
    box: tuple[int, int, int, int] | None  # PIL's exclusive right/bottom
    measured_box: tuple[int, int, int, int] | None
    source: str  # "measured", "profile", "suggested", or "review"
    error: str | None = None
    suggested_box: tuple[int, int, int, int] | None = None  # Unapproved book-calibrated option.
    review_reason: str | None = None

    family_widths: tuple[int, ...] = ()
    family_width: int | None = None

    @property
    def page_width(self) -> int | None:
        return None if self.box is None else self.box[2] - self.box[0]


def _horizontal_edges(image: Image.Image, black_limit: int = 6) -> tuple[int, int, bool, bool] | None:
    """Find outer ink, including short text, while ignoring isolated noise.

    Count every source row in small strips to avoid skipping text between
    sampled rows. A supported horizontal neighborhood locates short marks;
    sparse outer columns are then flagged for review rather than auto-cropped.
    """
    width, height = image.size
    count = np.zeros(width, dtype=np.int32)
    white_count = np.zeros(width, dtype=np.int32)
    brightness_sum = np.zeros(width, dtype=np.float64)
    for top in range(0, height, 256):
        rgb = np.asarray(image.crop((0, top, width, min(height, top + 256))).convert("RGB"))
        brightest = np.max(rgb, axis=2)
        brightness_sum += brightest.sum(axis=0, dtype=np.float64)
        count += np.count_nonzero(brightest > black_limit, axis=0)
        white_count += np.count_nonzero(np.min(rgb, axis=2) >= 245, axis=0)
    neighborhood = min(16, width)
    supported = np.convolve(count, np.ones(neighborhood, dtype=np.int32), mode="same")
    active = np.flatnonzero(supported >= max(12, round(height * .004)))
    if len(active) == 0:
        return None
    left_region = count[max(0, active[0] - neighborhood):min(width, active[0] + neighborhood)]
    right_region = count[max(0, active[-1] - neighborhood):min(width, active[-1] + neighborhood)]
    left = int(max(0, active[0] - neighborhood) + np.flatnonzero(left_region)[0])
    right = int(max(0, active[-1] - neighborhood) + np.flatnonzero(right_region)[-1] + 1)
    # Sparse pixels and compression ringing may extend into emulator black.
    # Locate a sustained brightness cliff near each observed outer boundary,
    # instead of demanding a perfectly clean first nonblack column. Short
    # isolated text cannot supply this much average brightness across rows.
    gradient = np.diff(brightness_sum / height)
    left_search = range(max(0, left - 1), min(len(gradient), left + 32))
    right_search = range(max(0, right - 33), min(len(gradient), right))
    if left_search and right_search:
        rise = max(left_search, key=lambda x: gradient[x])
        fall = min(right_search, key=lambda x: gradient[x])
        cliff_left, cliff_right = rise + 1, fall + 1
        if (gradient[rise] >= 10 and gradient[fall] <= -10 and
                abs(cliff_left - (width - cliff_right)) <= 4 and
                _kind(cliff_right - cliff_left, height) in ("single", "double")):
            return cliff_left, cliff_right, False, True
    sparse = min(count[left], count[right - 1]) < max(3, round(height * .01))
    white_inside = (right - left > 2 and
                    min(white_count[left + 1], white_count[right - 2]) >= height * .95)
    hard_edges = (left > 0 and right < width and
                  count[left - 1] <= 1 and count[right] <= 1 and
                  (white_inside or min(count[left], count[right - 1]) >= height * .95))
    return left, right, bool(sparse), bool(hard_edges)


def _kind(width: int, height: int) -> str:
    ratio = width / height
    if .42 <= ratio <= .73:
        return "single"
    if 1.08 <= ratio <= 1.75:
        return "double"
    return "uncertain"


def _book_widths(canvas: tuple[int, int], known: dict[str, int]) -> tuple[int, int] | None:
    """Return the expected sideways width and the dark-double cutoff.

    A rotated 2W-by-H spread, fitted to screenshot height H, is H²/(2W).
    Put the cutoff a modest cushion beyond the sideways width,
    about 2500 for a 1988-by-3056 book, with no fixed pixel dimensions.
    """
    if "single" not in known or "double" not in known:
        return None
    single, double = known["single"], known["double"]
    sideways = round(canvas[1] ** 2 / double)
    return sideways, max(single + max(3, round(single * .002)),
                         sideways + round(max(0, sideways - single) * .42))


def _wide_double_evidence(raw_width: int, canvas: tuple[int, int],
                          known: dict[str, int]) -> bool:
    widths = _book_widths(canvas, known)
    return widths is not None and raw_width > widths[1]


def _middle_reason(raw_width: int, canvas: tuple[int, int],
                   known: dict[str, int]) -> str | None:
    widths = _book_widths(canvas, known)
    if widths is None:
        return None
    sideways, cutoff = widths
    if known["single"] + max(3, round(known["single"] * .002)) < raw_width <= cutoff:
        return (f"middle width; possible sideways spread or dark double "
                f"(expected sideways about {sideways} px wide)")
    return None


def screenshot_workers(requested: int = 0) -> int:
    """Bound automatic screenshot work to half the logical processors and six images."""
    available = os.cpu_count() or 1
    return max(1, min(requested if requested > 0 else min(6, max(1, available // 2)),
                      available))


def _observe(path: Path):
    try:
        with Image.open(path) as image:
            canvas = image.size
            found = _horizontal_edges(image)
    except (OSError, ValueError) as exc:
        return (path, (0, 0), None, "uncertain", False, False, str(exc))
    if found is None:
        return (path, canvas, None, "uncertain", False, False, None)
    left, right, sparse, hard_edges = found
    return (path, canvas, (left, right), _kind(right - left, canvas[1]),
            sparse, hard_edges, None)


def propose_crops(paths: Iterable[Path], single_size: tuple[int, int] | None = None,
                  progress: Callable[[int, int], None] | None = None,
                  workers: int = 0) -> list[CropProposal]:
    """Calibrate per screenshot size and propose safe, reviewable crop boxes.

    A one-pixel difference between 2*single and double is normal. If a page
    appears narrower than the profile, use the wider known page boundary so
    black artwork inside the page is retained. Ambiguous geometries stay
    uncertain for a human to adjust; no pixels are cut automatically.
    """
    paths = list(paths)
    observations = [None] * len(paths)
    count = min(screenshot_workers(workers), len(paths))
    if count <= 1:
        for index, path in enumerate(paths):
            observations[index] = _observe(path)
            if progress:
                progress(index + 1, len(paths))
    else:
        with ThreadPoolExecutor(max_workers=count) as executor:
            futures = {executor.submit(_observe, path): index
                       for index, path in enumerate(paths)}
            for completed, future in enumerate(as_completed(futures), 1):
                observations[futures[future]] = future.result()
                if progress:
                    progress(completed, len(paths))

    # The front and back covers are often a different width from the pages
    # (narrower, or wider in some DC books), so they don't calibrate the page
    # sizes. A cover whose edges are clear is cropped at its own size.
    covers = _cover_indexes(observations)
    pages = [o for index, o in enumerate(observations) if index not in covers]

    # Cluster clear, centered boundaries by their equivalent single width.
    # Two examples establish a recurring family; tiny rounding changes stay together.
    groups = {}
    for observation in pages:
        _path, canvas, edges, kind, sparse, hard, _error = observation
        if edges is None or kind not in ("single", "double") or sparse or not hard:
            continue
        left, right = edges
        if abs(left - (canvas[0] - right)) > max(4, canvas[0] // 150):
            continue
        width = (right - left) / (2 if kind == "double" else 1)
        groups.setdefault(canvas, []).append((width, observation))
    families = {}
    for canvas, samples in groups.items():
        clusters = []
        for width, observation in sorted(samples, key=lambda item: item[0]):
            if not clusters or width - clusters[-1][0][0] > max(3, round(width * .002)):
                clusters.append([])
            clusters[-1].append((width, observation))
        recurring = [cluster for cluster in clusters if len(cluster) >= 2]
        if len(recurring) > 1:
            families[canvas] = [(round(max(w for w, _ in cluster)),
                                 [obs for _, obs in cluster]) for cluster in recurring]
    if not families:
        proposals = _propose_observations(observations, single_size, pages)
        return [_cover_proposal(o) or p if index in covers else p
                for index, (o, p) in enumerate(zip(observations, proposals, strict=True))]
    result = []
    for index, observation in enumerate(observations):
        path, canvas, edges, kind, sparse, hard, error = observation
        if index in covers and (cover := _cover_proposal(observation)) is not None:
            result.append(cover)
            continue
        if canvas not in families:
            same_canvas = [o for o in observations if o[1] == canvas]
            calibration = [o for o in pages if o[1] == canvas]
            result.append(next(p for p in _propose_observations(same_canvas, single_size, calibration)
                               if p.path == path))
            continue
        entries = families[canvas]
        widths = tuple(width for width, _ in entries)
        if single_size is not None and abs(single_size[1] - canvas[1]) <= 2:
            if not any(abs(width - single_size[0]) <= max(3, round(width * .002)) for width in widths):
                raise ValueError("Entered dimensions do not match any detected page-size family.")
        candidates = []
        for width, anchors in entries:
            subset = list(anchors)
            if observation not in subset:
                subset.append(observation)
            candidate = next(p for p in _propose_observations(subset, None, anchors, hidden_singles=False)
                             if p.path == path)
            candidates.append(replace(candidate, family_widths=widths, family_width=width))
        measured = [p for p in candidates if p.source == "measured"]
        if measured:
            # A clearly exposed boundary wins over containment in another
            # family's larger rectangle. Resolve rounding overlap by distance.
            raw = edges[1] - edges[0]
            usable = [min(measured, key=lambda p: abs(raw - p.family_width *
                                                     (2 if p.kind == "double" else 1)))]
        else:
            viable = [p for p in candidates if p.box is not None]
            anchored = [p for p in viable if p.source == "profile"]
            if len(anchored) == 1:
                usable = anchored
            elif viable and len({p.kind for p in viable}) == 1:
                # Hidden black borders do not justify adding the larger
                # family. Prefer the narrowest established rectangle that
                # contains all measured content; optional previews remain.
                usable = [min(viable, key=lambda p: p.page_width)]
            else:
                usable = []
        if len(usable) == 1:
            result.append(usable[0])
        else:
            extent = (edges[0], 0, edges[1], canvas[1]) if edges else None
            result.append(CropProposal(path, canvas, "uncertain", None, extent, "review", error,
                                      review_reason="Multiple page-size families fit; choose the family and crop type.",
                                      family_widths=widths))
    return result


def _clear_width(observation) -> float | None:
    """A screenshot's page width per single page, when both edges are clear and centred."""
    _path, canvas, edges, kind, sparse, hard, _error = observation
    if edges is None or kind not in ("single", "double") or sparse or not hard:
        return None
    left, right = edges
    if abs(left - (canvas[0] - right)) > max(4, canvas[0] // 150):
        return None
    return (right - left) / (2 if kind == "double" else 1)


# A cover this much narrower than the pages, or less, is taken as its own size.
# Much narrower usually means artwork inside a black border that blends into
# the screenshot, so the page sizes decide instead.
COVER_NARROWER_MAX = 0.03


def _cover_indexes(observations) -> set[int]:
    """Covers that stand apart from the page sizes: the first and last screenshots
    of a book of several, when their clear width matches none of the other pages'
    and is either wider than the nearest page size (some DC covers) or only a
    little narrower (1969 against 1988, say). A cover the same size as the
    pages still counts towards them."""
    count = len(observations)
    if count < 3:
        return set()
    apart = set()
    for index in {0, count - 1}:
        width = _clear_width(observations[index])
        if width is None:
            continue
        canvas = observations[index][1]
        others = [w for i, o in enumerate(observations)
                  if i not in (0, count - 1) and o[1] == canvas and (w := _clear_width(o)) is not None]
        if not others or any(abs(width - w) <= max(3, round(w * .002)) for w in others):
            continue
        nearest = min(others, key=lambda w: abs(width - w))
        if width > nearest or width >= nearest * (1 - COVER_NARROWER_MAX):
            apart.add(index)
    return apart


def _cover_proposal(observation) -> CropProposal | None:
    """A cover with clear, centred edges, cropped at its own measured size; None
    when its edges are hidden (black artwork), so the page sizes decide."""
    path, canvas, edges, kind, sparse, hard, _error = observation
    if edges is None or kind not in ("single", "double") or sparse or not hard:
        return None
    left, right = edges
    if abs(left - (canvas[0] - right)) > max(4, canvas[0] // 150):
        return None
    box = (left, 0, right, canvas[1])
    return CropProposal(path, canvas, kind, box, box, "measured")


def _hidden_single(left: int, right: int, canvas: tuple[int, int],
                   known: dict[str, int]) -> tuple[int, int, int, int] | None:
    """The book's single-page rectangle, centred, when a page whose edges are
    both hidden (black borders running into the screenshot's black) has all its
    visible content inside it: nothing on it is wider than a single page."""
    if "single" not in known:
        return None
    single = known["single"]
    x = (canvas[0] - single) // 2
    tolerance = max(3, round(single * .002))
    if x - tolerance <= left and right <= x + single + tolerance:
        return (x, 0, x + single, canvas[1])
    return None


def _propose_observations(observations, single_size=None, calibration=None,
                          hidden_singles=True):
    profiles: dict[tuple[int, int], dict[str, int]] = {}
    templates: dict[tuple[int, int], dict[str, list[tuple[int, int]]]] = {}
    for _path, canvas, edges, kind, sparse, hard_edges, _error in (observations if calibration is None else calibration):
        if edges is None or kind == "uncertain" or sparse or not hard_edges:
            continue
        left, right = edges
        # A page is horizontally centered in the virtual device. A shifted
        # measurement usually means black comic art hid one of the edges.
        if abs(left - (canvas[0] - right)) > max(4, canvas[0] // 150):
            continue
        widths = profiles.setdefault(canvas, {})
        widths[kind] = max(widths.get(kind, 0), right - left)
        templates.setdefault(canvas, {}).setdefault(kind, []).append((left, right))

    if single_size is not None:
        standard_width, standard_height = single_size
        if standard_width <= 0 or standard_height <= 0:
            raise ValueError("Single-page dimensions must be positive.")
        for canvas in {item[1] for item in observations if item[1] != (0, 0)}:
            if abs(canvas[1] - standard_height) > max(2, round(standard_height * .002)):
                # A book may also contain a taller capture of a sideways
                # spread. Treat it as its own group for individual review.
                continue
            widths = profiles.setdefault(canvas, {})
            templates.setdefault(canvas, {})
            for kind, expected in (("single", standard_width), ("double", 2 * standard_width)):
                if kind in widths and abs(widths[kind] - expected) > max(4, round(expected * .01)):
                    raise ValueError(f"Measured {kind} width {widths[kind]} disagrees with your "
                                     f"entered single width {standard_width}. Check the dimensions.")
                widths[kind] = expected

    # One clean size can calibrate the other, with a little rounding slack.
    for widths in profiles.values():
        if "double" in widths and "single" not in widths:
            widths["single"] = round(widths["double"] / 2)
        elif "single" in widths and "double" not in widths:
            widths["double"] = widths["single"] * 2
    for canvas, widths in profiles.items():
        for kind, width in widths.items():
            if kind not in templates[canvas]:
                x = (canvas[0] - width) // 2
                templates[canvas][kind] = [(x, x + width)]
            else:
                # A solid colored region *inside* black comic artwork can
                # look like a second hard page edge. Keep only rectangles
                # consistent with the widest clear page in this size group,
                # allowing the usual one-pixel variations.
                slack = max(3, round(width * .002))
                templates[canvas][kind] = [bounds for bounds in templates[canvas][kind]
                                           if width - (bounds[1] - bounds[0]) <= slack]

    result = []
    for path, canvas, edges, kind, sparse, hard_edges, error in observations:
        known = profiles.get(canvas, {})
        if edges is None:
            result.append(CropProposal(path, canvas, "uncertain", None, None, "review", error))
            continue
        left, right = edges
        raw_width = right - left
        if sparse:
            # A repeated calibrated rectangle can explain tiny edge noise
            # on a textured page even when the very outer pixels are sparse.
            matches = []
            for candidate, bounds in templates.get(canvas, {}).items():
                for x, end in bounds:
                    tolerance = max(3, round((end - x) * .002))
                    if abs(left - x) <= tolerance and abs(right - end) <= tolerance:
                        matches.append((candidate, x, end))
            if len({candidate for candidate, _, _ in matches}) == 1:
                candidate, x, end = matches[0]
                result.append(CropProposal(path, canvas, candidate,
                                           (x, 0, end, canvas[1]),
                                           (left, 0, right, canvas[1]), "profile"))
                continue
            # Sparse text on a black page cannot establish the actual page
            # boundary. Show a generous visible-content region for inspection.
            pad = max(16, round(canvas[0] * .004))
            visible = (max(0, left - pad), 0, min(canvas[0], right + pad), canvas[1])
            likely_double = "single" in known and raw_width > known["single"] * 1.05
            suggestion = None
            if likely_double and "double" in known:
                double = known["double"]
                x = (canvas[0] - double) // 2
                if x <= left and right <= x + double:
                    suggestion = (x, 0, x + double, canvas[1])
            if suggestion is not None and _wide_double_evidence(raw_width, canvas, known):
                # Even sparse marks spanning beyond the gray zone cannot fit
                # on a single page. The crop itself comes from clear pages.
                result.append(CropProposal(path, canvas, "double", suggestion,
                                           visible, "suggested"))
                continue
            if hidden_singles and (single := _hidden_single(left, right, canvas, known)):
                result.append(CropProposal(path, canvas, "single", single,
                                           visible, "suggested"))
                continue
            reason = _middle_reason(raw_width, canvas, known)
            result.append(CropProposal(path, canvas, "uncertain",
                                       None, visible, "review", suggested_box=suggestion,
                                       review_reason=reason))
            continue
        # If black art concealed both edges of a wide image, its visible
        # width might resemble one page. Do not silently choose either size.
        alternatives = [other for other in ("single", "double") if other in known]
        closest = min(alternatives, key=lambda other: abs(raw_width - known[other])) if alternatives else None
        measured = (left, 0, right, canvas[1])
        if closest:
            target = known[closest]
            tolerance = max(3, round(target * .002))
            centered = abs(left - (canvas[0] - right)) <= max(4, canvas[0] // 150)
            # Small differences are real (e.g. 1987 vs 1988). Preserve them.
            if abs(raw_width - target) <= tolerance and centered:
                result.append(CropProposal(path, canvas, closest, measured, measured, "measured"))
                continue
        widths = _book_widths(canvas, known)
        centered = abs(left - (canvas[0] - right)) <= max(4, canvas[0] // 150)
        if widths is not None and hard_edges and centered:
            sideways, _cutoff = widths
            if abs(raw_width - sideways) <= max(4, round(sideways * .02)):
                result.append(CropProposal(
                    path, canvas, "sideways", None, measured, "review",
                    review_reason=f"measured near sideways width {sideways} px; "
                                  "check whether a taller screenshot is needed"))
                continue
        middle = _middle_reason(raw_width, canvas, known)
        if middle:
            # A visible span in the sideways range is ambiguous even when
            # one edge happens to align with the usual double-page crop.
            double = known["double"]
            x = (canvas[0] - double) // 2
            option = (x, 0, x + double, canvas[1]) if x <= left and right <= x + double else None
            result.append(CropProposal(path, canvas, "uncertain", None, measured,
                                       "review", suggested_box=option,
                                       review_reason=middle))
            continue
        anchored = []
        for candidate, bounds in templates.get(canvas, {}).items():
            for x, end in bounds:
                tolerance = max(3, round((end - x) * .002))
                # One edge still exposes the actual page boundary; the other
                # disappears into black artwork. If neither is visible, ask.
                edge_distance = min(abs(left - x), abs(right - end))
                if (left >= x - tolerance and right <= end + tolerance and
                        edge_distance <= tolerance):
                    anchored.append((candidate, x, end, edge_distance))
        if anchored and len({candidate for candidate, *_ in anchored}) == 1:
            candidate, x, end, _ = min(anchored, key=lambda item:
                                        (item[3], item[2] - item[1] - raw_width))
            result.append(CropProposal(path, canvas, candidate, (x, 0, end, canvas[1]),
                                       measured, "profile"))
        else:
            # Black comic borders can hide *both* real edges. If the still
            # visible region exceeds the gray zone, it cannot be a normal
            # single. Suggest the calibrated double for a batch check.
            if "single" in known and "double" in known:
                double = known["double"]
                x = (canvas[0] - double) // 2
                if (_wide_double_evidence(raw_width, canvas, known) and
                        x <= left and right <= x + double):
                    result.append(CropProposal(path, canvas, "double",
                                               (x, 0, x + double, canvas[1]),
                                               measured, "suggested"))
                    continue
            # Everything visible fits inside the book's one single-page size,
            # so it's a single page at that size. (With two page sizes in the
            # book, the family code decides instead; see hidden_singles.)
            if hidden_singles and (single := _hidden_single(left, right, canvas, known)):
                result.append(CropProposal(path, canvas, "single", single,
                                           measured, "suggested"))
                continue
            # Wider than a calibrated single but unlike an ordinary double:
            # likely a special spread. Leave the proposed extent for review.
            unusual_wide = (kind == "uncertain" and "single" in known and
                            raw_width > known["single"] * 1.05)
            suggestion = None
            if unusual_wide and "double" in known:
                double = known["double"]
                x = (canvas[0] - double) // 2
                if x <= left and right <= x + double:
                    suggestion = (x, 0, x + double, canvas[1])
            result.append(CropProposal(path, canvas, "uncertain",
                                       None, measured, "review", suggested_box=suggestion,
                                       review_reason=_middle_reason(raw_width, canvas, known)))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report proposed crops for emulator screenshots without changing files.")
    parser.add_argument("folder", type=Path, help="Folder containing the original screenshots")
    args = parser.parse_args(argv)
    if not args.folder.is_dir():
        parser.error(f"Not a folder: {args.folder}")

    extensions = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
    def natural_key(path: Path):
        return [int(part) if part.isdigit() else part.casefold()
                for part in re.split(r"(\d+)", path.name)]
    paths = sorted((p for p in args.folder.iterdir()
                    if p.is_file() and p.suffix.lower() in extensions), key=natural_key)
    if not paths:
        parser.error("No supported screenshots found in that folder")

    proposals = propose_crops(paths)
    print(f"Analyzed {len(proposals)} screenshot{'s' if len(proposals) != 1 else ''} (read only):")
    for item in proposals:
        if item.box is None:
            if item.error:
                print(f"  ERROR   {item.path.name}  unreadable: {item.error}")
            else:
                suggestion = (f"visible extent {item.measured_box} "
                              "(not an approved crop)" if item.measured_box else "crop uncertain")
                hint = f"{item.review_reason}; " if item.review_reason else ""
                calibrated = (f"; book-calibrated double option {item.suggested_box} "
                              "(confirm before cropping)" if item.suggested_box else "")
                print(f"  REVIEW  {item.path.name}  {item.canvas[0]}x{item.canvas[1]}  "
                      f"{hint}"
                      f"{suggestion}{calibrated}; inspect by eye")
            continue
        left, top, right, bottom = item.box
        label = "SUGGEST" if item.source in ("profile", "suggested") else item.kind.upper()
        print(f"  {label:7}  {item.path.name}  {item.kind} "
              f"{right-left}x{bottom-top}  crop ({left}, {top}, {right}, {bottom})  "
              f"[{item.source}]")
    uncertain = sum(item.box is None for item in proposals)
    suggested = sum(item.source in ("profile", "suggested") for item in proposals)
    print(f"Summary: {len(proposals) - uncertain - suggested} measured, "
          f"{suggested} suggested for batch check, {uncertain} need individual review. "
          "No images were modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
