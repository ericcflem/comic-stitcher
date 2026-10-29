"""Read-only comic analysis and virtual book decisions: scanning pages, suggesting spreads,
the reviewed book plan, and the page images it is made from. Exporting is in export.py."""
from __future__ import annotations

import os
import copy
import re
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from multiprocessing import get_context
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageOps, ImageCms
from io import BytesIO


from .colour import standard_mode
from .detector import (
    case_twin_groups,
    classify_page, collect_images, is_candidate_pair, load_edge_rgb,
    seam_score, strip_stats,
)

# Spread-detection tuning. Only a thin strip at each page edge is examined.
BORDER_STRIP_PX = 14            # Width of the edge strip tested for a solid border.
BORDER_MAX_STD = 16.0           # Colour variation allowed in a solid border strip.
BORDER_MAX_VERTICAL_STD = 18.0  # Top-to-bottom variation allowed in a solid border strip.
SEAM_BAND_PX = 24               # Width of the edge band compared across a possible seam.
LIKELY_MIN_SCORE = 0.55         # Seam continuity at or above this is a likely spread.
POSSIBLE_MIN_SCORE = 0.40       # Below this, an unlikely pair is not offered for review.


@dataclass(frozen=True)
class Page:
    path: Path
    index: int
    group: int
    width: int
    height: int
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class Candidate:
    left: int
    right: int
    continuity: float
    likely: bool
    metrics: dict = field(default_factory=dict)


@dataclass
class Scan:
    folder: Path
    pages: list[Page]
    candidates: list[Candidate]
    number_gaps_detected: bool = False
    numbering_assumed: bool = False
    output_folder: Path | None = None
    source_archive: Path | None = None
    manual_mode: bool = False
    screenshot_source: Path | None = None
    flagged_pages: set[int] = field(default_factory=set)
    right_to_left: bool = False
    # Pages whose names differ only in capitalisation: (their names, whether
    # the files are identical). Possible on Linux and inside archives.
    case_twins: list[tuple[tuple[str, ...], bool]] = field(default_factory=list)
    # Pages worth a look, from their file names: (page indexes, what to check).
    page_notes: list[tuple[tuple[int, ...], str]] = field(default_factory=list)


def describe_case_twins(names: list[str], files: list[Path]) -> list[tuple[tuple[str, ...], bool]]:
    """Groups of names differing only in capitalisation, and whether each group's files match."""
    import filecmp
    return [(tuple(names[i] for i in group),
             all(filecmp.cmp(files[group[0]], files[i], shallow=False) for i in group[1:]))
            for group in case_twin_groups(names)]


# A page name's number, or a range for a two-page file ("Book 012-013.jpg").
_NUMBERED_NAME = re.compile(r"(.*?)(\d+)(?:\s*[-_]\s*(\d+))?")


def missing_numbers(names: list[str]) -> list[tuple[int, int]]:
    """Places where the file numbering skips: (index of the name after the
    gap, how many numbers are missing). Only when every name is the same prefix
    and a number (or range) going up, and the skips are few; books numbered some
    other way say nothing."""
    parts = []
    for name in names:
        match = _NUMBERED_NAME.fullmatch(Path(name.replace("\\", "/").rsplit("/", 1)[-1]).stem)
        if match is None:
            return []
        start = int(match.group(2))
        end = int(match.group(3) or start)
        if end < start:
            return []
        parts.append((match.group(1).casefold(), start, end))
    if len({prefix for prefix, _, _ in parts}) != 1:
        return []
    steps = [after[1] - before[2] for before, after in zip(parts, parts[1:])]
    if any(step < 1 for step in steps):
        return []
    gaps = [(index + 1, step - 1) for index, step in enumerate(steps) if step > 1]
    return gaps if len(gaps) <= max(2, len(names) // 10) else []


def describe_page_notes(names: list[str], files: list[Path], right_to_left: bool = False
                        ) -> list[tuple[tuple[int, ...], str]]:
    """Notes on pages worth a look, from their names in file order: names that
    differ only in capitalisation, and numbering that skips (a page may be
    missing). Pages are given by their index in reading order."""
    import filecmp
    count = len(names)

    def page(index):
        return count - 1 - index if right_to_left else index
    short = [name.replace("\\", "/").rsplit("/", 1)[-1] for name in names]
    if len(set(short)) != len(short):
        short = [name.replace("\\", "/") for name in names]
    notes = []
    for group in case_twin_groups(names):
        identical = all(filecmp.cmp(files[group[0]], files[i], shallow=False) for i in group[1:])
        notes.append((tuple(sorted(page(i) for i in group)),
                      "Names differ only in capitalisation: " + ", ".join(short[i] for i in group) +
                      (" (identical copies)" if identical else "")))
    for after, missing in missing_numbers(names):
        notes.append((tuple(sorted((page(after - 1), page(after)))),
                      f"File numbers jump from {short[after - 1]} to {short[after]}: "
                      f"{'a page' if missing == 1 else f'{missing} pages'} may be missing between them"))
    return sorted(notes)


class BoundaryOverrideRequired(ValueError):
    """The scanner excluded this pair; a human may deliberately join it."""


def _classify(path: Path, index: int):
    return classify_page(path, index, BORDER_STRIP_PX, BORDER_MAX_STD, BORDER_MAX_VERTICAL_STD)


# Worker processes take a moment to start, so small or light books scan in one.
SCAN_PARALLEL_MIN_PAGES = 12
SCAN_PARALLEL_MIN_BYTES = 40 * 1024 * 1024


def _classify_job(path: str, index: int):
    """Top level, so Windows spawn and PyInstaller can run it in a worker process."""
    try:
        return _classify(Path(path), index)
    except Exception as exc:
        raise ValueError(f"Cannot read image {Path(path).name}: {exc}") from exc


def _scan_jobs(function, jobs: list[tuple], workers: int, done: Callable[[], None]) -> list:
    """function(*job) for every job, in order; in parallel processes when worthwhile.

    Each page is decoded on its own, so pages spread across processes like
    export does. The results are the same either way.
    """
    if workers <= 1 or len(jobs) < SCAN_PARALLEL_MIN_PAGES:
        results = []
        for job in jobs:
            results.append(function(*job))
            done()
        return results
    results = [None] * len(jobs)
    with ProcessPoolExecutor(max_workers=min(workers, len(jobs)), mp_context=get_context("spawn")) as pool:
        pending = {pool.submit(function, *job): index for index, job in enumerate(jobs)}
        try:
            while pending:
                finished, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in finished:
                    results[pending.pop(future)] = future.result()
                    done()
        except BaseException:
            for future in pending:
                future.cancel()
            raise
    return results


def _measured_candidate(left: int, right: int, visual_left: Path, visual_right: Path,
                        left_info, right_info) -> Candidate:
    """Score the seam between two neighbouring pages, as they appear side by side,
    and look for a strip of art both repeat (a spread sliced with an overlap)."""
    seam_columns = 8 * SEAM_BAND_PX  # As load_edge_rgb reads for the seam score.
    limit = overlap_limit(left_info.width, right_info.width)
    left_edge = load_edge_rgb(visual_left, "right", width=max(seam_columns, limit + OVERLAP_RIGHT_COLUMNS))
    right_edge = load_edge_rgb(visual_right, "left", width=max(seam_columns, OVERLAP_RIGHT_COLUMNS))
    score = seam_score(left_edge[:, -seam_columns:], right_edge[:, :seam_columns], band=SEAM_BAND_PX)
    metrics = {"seam": score, "left_page_right_edge": left_info.right_stats,
               "right_page_left_edge": right_info.left_stats}
    overlap = overlap_in(left_edge, right_edge, limit)
    if overlap:
        metrics["overlap"] = overlap
    # A strip of art both halves repeat is as good as proof of a spread, so it is
    # Likely, whatever the seam scored (a repeated strip doesn't match at the seam).
    return Candidate(left, right, score["continuity"], score["continuity"] >= LIKELY_MIN_SCORE or bool(overlap),
                     metrics)


def _overlap_candidate(left: int, right: int, visual_left: Path, visual_right: Path,
                       left_info, right_info) -> Candidate | None:
    """For neighbouring pages the edge test rules out (a border on an inner edge,
    for example). Offered as Likely when both repeat a strip of art, which is
    strict enough to trust on its own; or when one inner edge has a thin plain border
    and, with it set aside, the art carries on across the seam."""
    seam_columns = 8 * SEAM_BAND_PX
    limit = overlap_limit(left_info.width, right_info.width)
    left_edge = load_edge_rgb(visual_left, "right",
                              width=max(limit + OVERLAP_RIGHT_COLUMNS, seam_columns + BORDER_COVER_MAX))
    right_edge = load_edge_rgb(visual_right, "left",
                               width=max(OVERLAP_RIGHT_COLUMNS, seam_columns + BORDER_COVER_MAX))
    stats = {"left_page_right_edge": left_info.right_stats, "right_page_left_edge": right_info.left_stats}
    overlap = overlap_in(left_edge, right_edge, limit)
    if overlap:
        return Candidate(left, right, 0.0, True, {"overlap": overlap, "edges_not_scored": True, **stats})
    border = thin_border(left_info.right_stats["plain_px"], right_info.left_stats["plain_px"])
    width_gap = abs(left_info.width - right_info.width)
    if border is None or width_gap > max(2, round(min(left_info.width, right_info.width) * .005)) + BORDER_COVER_MAX:
        return None
    pixels, top = border
    if top == "right":  # The border is on the left page: set it aside.
        left_edge = left_edge[:, :left_edge.shape[1] - pixels]
    else:
        right_edge = right_edge[:, pixels:]
    if min(left_edge.shape[1], right_edge.shape[1]) < seam_columns:
        return None
    score = seam_score(left_edge[:, -seam_columns:], right_edge[:, :seam_columns], band=SEAM_BAND_PX)
    if score["continuity"] < POSSIBLE_MIN_SCORE:
        return None
    return Candidate(left, right, score["continuity"], score["continuity"] >= LIKELY_MIN_SCORE,
                     {"seam": score, "border_set_aside": pixels, **stats})


def _pair_job(scored: bool, *job) -> Candidate | None:
    """One neighbouring pair in the scan: scored as a seam, or only checked for a repeated strip."""
    return _measured_candidate(*job) if scored else _overlap_candidate(*job)


def _same_size(a, b) -> bool:
    """Pages a sliced spread could have been cut into: the same height (as
    is_candidate_pair measures it), and widths no more than half again apart,
    since a slice with an overlap, or a cropped one, needn't be exactly half."""
    return (abs(a.height - b.height) <= max(2, round(min(a.height, b.height) * .002)) and
            max(a.width, b.width) <= 1.5 * min(a.width, b.width))


# A plain strip this narrow on one inner edge only (a border added to one half,
# or blank slicing debris) is covered by the other page when the pair is joined.
BORDER_COVER_MAX = 8


def thin_border(left_plain: int, right_plain: int) -> tuple[int, str] | None:
    """(pixels, page on top) covering a thin plain strip on one inner edge, or None."""
    if 0 < left_plain <= BORDER_COVER_MAX and right_plain == 0:
        return left_plain, "right"
    if 0 < right_plain <= BORDER_COVER_MAX and left_plain == 0:
        return right_plain, "left"
    return None


def thin_border_between(left: Image.Image, right: Image.Image) -> tuple[int, str] | None:
    """thin_border for two pages, as displayed side by side."""
    height = min(left.height, right.height)
    columns = 14  # As the scan's edge statistics.
    left_edge = np.asarray(left.convert("RGB").crop((left.width - columns, 0, left.width, height)))
    right_edge = np.asarray(right.convert("RGB").crop((0, 0, columns, height)))
    return thin_border(strip_stats(left_edge, "right", columns)["plain_px"],
                       strip_stats(right_edge, "left", columns)["plain_px"])


def join_layout(candidate: Candidate) -> tuple[int, str] | None:
    """How a pair is joined when approved: over the strip both halves repeat,
    and/or covering a thin border on one inner edge. (pixels, page on top) or None."""
    metrics = candidate.metrics
    border = thin_border(metrics.get("left_page_right_edge", {}).get("plain_px", 0),
                         metrics.get("right_page_left_edge", {}).get("plain_px", 0))
    if metrics.get("overlap"):
        # The page without a border goes on top, hiding the other's.
        return metrics["overlap"], border[1] if border else "left"
    return border


def offered(candidate: Candidate) -> bool:
    """Whether pair review shows this pair: a likely or possible seam, or a repeated strip."""
    return candidate.likely or candidate.continuity >= POSSIBLE_MIN_SCORE or bool(candidate.metrics.get("overlap"))


def scan_folder(folder: Path, progress: Callable[[int, int], None] | None = None,
                *, manual: bool = False, right_to_left: bool = False, workers: int = 0) -> Scan:
    folder = folder.expanduser().resolve(strict=True)
    if not folder.is_dir():
        raise ValueError("Choose a folder of images.")
    paths = collect_images(folder)
    if right_to_left:
        paths.reverse()
    if not paths:
        raise ValueError("No supported page images were found in this folder.")
    # Names differing only in capitalisation are kept, in the order Linux file
    # managers show them (see name_sort_key); the import notes them for review.
    case_twins = describe_case_twins([p.name for p in paths], paths)
    in_file_order = paths[::-1] if right_to_left else paths
    page_notes = describe_page_notes([p.name for p in in_file_order], in_file_order, right_to_left)

    if manual:
        pages = []
        for i, path in enumerate(paths):
            try:
                with Image.open(path) as image:
                    width, height = image.size
                    if image.getexif().get(274) in (5, 6, 7, 8):
                        width, height = height, width
                stat = path.stat()
            except Exception as exc:
                raise ValueError(f"Cannot read image {path.name}: {exc}") from exc
            pages.append(Page(path, i, 0, width, height, stat.st_size, stat.st_mtime_ns))
            if progress:
                progress(i + 1, len(paths))
        # Every neighbor is reviewable, regardless of the edge art or page numbers.
        candidates = [Candidate(i, i + 1, 0.0, True) for i in range(len(pages) - 1)]
        return Scan(folder, pages, candidates, manual_mode=True, right_to_left=right_to_left,
                    case_twins=case_twins, page_notes=page_notes)

    # Only infer numbering gaps if every name follows the same prefix + digits pattern.
    matches = [re.fullmatch(r"(.*?)(\d+)", p.stem) for p in paths]
    numbers = None
    if all(matches) and len({m.group(1).casefold() for m in matches}) == 1:
        proposed = [int(m.group(2)) for m in matches]
        if all((b < a if right_to_left else b > a) for a, b in zip(proposed, proposed[1:], strict=False)):
            numbers = proposed

    workers = encoding_workers(workers)
    if sum(path.stat().st_size for path in paths) < SCAN_PARALLEL_MIN_BYTES:
        workers = 1
    steps = {"done": 0}
    def step():
        steps["done"] += 1
        if progress:
            progress(steps["done"], len(paths) * 2)
    info = _scan_jobs(_classify_job, [(str(path), i) for i, path in enumerate(paths)], workers, step)

    pages: list[Page] = []
    group = -1
    previous_eligible = False
    gaps = False
    for i, (path, p) in enumerate(zip(paths, info, strict=True)):
        bordered = p.left_solid and p.right_solid
        gap = i > 0 and numbers is not None and numbers[i] != numbers[i - 1] + (-1 if right_to_left else 1)
        gaps |= gap
        if bordered:
            previous_eligible = False
            assigned_group = -1
        else:
            if not previous_eligible or gap:
                group += 1
            assigned_group = group
            previous_eligible = True
        stat = path.stat()
        pages.append(Page(path, i, assigned_group, p.width, p.height,
                          stat.st_size, stat.st_mtime_ns))

    pairs, overlap_only = [], []
    for i in range(len(pages) - 1):
        a, b = pages[i:i + 2]
        visual_left, visual_right = (b, a) if right_to_left else (a, b)
        left_info, right_info = (info[i + 1], info[i]) if right_to_left else (info[i], info[i + 1])
        job = (i, i + 1, visual_left.path, visual_right.path, left_info, right_info)
        if a.group >= 0 and a.group == b.group and is_candidate_pair(left_info, right_info):
            pairs.append(job)
        elif _same_size(left_info, right_info):
            overlap_only.append(job)  # Ruled out by its edges; a repeated strip can still show a spread.
    steps["done"] = len(paths) + (len(pages) - 1 - len(pairs) - len(overlap_only))  # Pairs that need no measuring.
    jobs = [(True, *job) for job in pairs] + [(False, *job) for job in overlap_only]
    candidates = [c for c in _scan_jobs(_pair_job, jobs, workers, step) if c is not None]
    candidates.sort(key=lambda c: (not c.likely, c.left))
    if progress:
        progress(2 * len(paths), 2 * len(paths))
    return Scan(folder, pages, candidates, gaps, numbers is None, right_to_left=right_to_left,
                case_twins=case_twins, page_notes=page_notes)


def scan_manual_folder(folder: Path, progress: Callable[[int, int], None] | None = None, *, right_to_left=False,
                       workers: int = 0) -> Scan:
    return scan_folder(folder, progress=progress, manual=True, right_to_left=right_to_left, workers=workers)


class BookPlan:
    """All edits are memory-only. Candidate history supports real undo."""

    def __init__(self, scan: Scan):
        self.scan = scan
        self.base_candidate_count = len(scan.candidates)
        from .comicinfo import read_folder_metadata
        self.comicinfo_xml = read_folder_metadata(scan.folder)
        self.include_comicinfo = False
        self.decisions: dict[int, bool] = {}
        self.history: list[dict[int, bool] | dict[str, int] | int] = []
        self.manual_pairs: set[int] = set()
        self.removed_pages: set[int] = set()  # Excluded from output; source files remain untouched.
        self.rotations: dict[int, int] = {}  # Final entry's source index -> clockwise degrees.
        self.page_order = list(range(len(scan.pages)))
        self.manual_rights: dict[int, int] = {}
        self.joined_rotations: dict[int, tuple[int, int]] = {}
        # Spreads whose halves repeat a strip of art at the seam (sliced from a page
        # narrower than two): first page -> (pixels the halves overlap, "left" or
        # "right" for the half drawn on top, as the pages appear side by side).
        self.join_overlaps: dict[int, tuple[int, str]] = {}
        # Alignments chosen in pair review's Seam before deciding: candidate -> (pixels, top).
        self.proposed_layouts: dict[int, tuple[int, str]] = {}
        self.preview_history: list[dict] = []
        self.deferred_possible = False
        # Centering art that print margins push aside (centering.py): each measured
        # page's (shift, first row, end row), found in the background and not saved;
        # whether this comic's art is centered; and pages left as they were.
        self.centering: dict[int, tuple[int, int, int]] = {}
        self.center_art = False
        self.center_skipped: set[int] = set()
        self.centering_measured = False  # Every page measured (none of them may need it).
        self._measured_neighbors = set(zip(range(len(scan.pages) - 1), range(1, len(scan.pages)), strict=True))
        self._measured_neighbors.update((c.left, c.right) for c in scan.candidates)

    def preview_snapshot(self, action: str, focus: int) -> dict:
        return {"decisions": self.decisions.copy(), "manual_pairs": sorted(self.manual_pairs),
                "removed_pages": sorted(self.removed_pages), "rotations": self.rotations.copy(),
                "page_order": self.page_order.copy(), "manual_rights": self.manual_rights.copy(),
                "joined_rotations": copy.deepcopy(self.joined_rotations),
                "join_overlaps": copy.deepcopy(self.join_overlaps),
                "center_art": self.center_art, "center_skipped": sorted(self.center_skipped),
                "_action": action, "_focus": focus, "_history_length": len(self.history)}

    def checkpoint_preview(self, action: str, focus: int) -> None:
        self.preview_history.append(self.preview_snapshot(action, focus))
        del self.preview_history[:-100]

    def undo_preview(self) -> dict | None:
        if not self.preview_history:
            return None
        saved = self.preview_history.pop()
        self.decisions = saved["decisions"].copy()
        self.manual_pairs = set(saved["manual_pairs"])
        self.removed_pages = set(saved["removed_pages"])
        for name in ("rotations", "manual_rights", "joined_rotations"):
            setattr(self, name, copy.deepcopy(saved[name]))
        self.join_overlaps = copy.deepcopy(saved.get("join_overlaps", {}))
        self.center_art = bool(saved.get("center_art", False))
        self.center_skipped = set(saved.get("center_skipped", []))
        self.page_order = saved["page_order"].copy()
        del self.history[saved["_history_length"]:]
        return saved

    def page_centering(self, left: int, right: int | None = None) -> tuple[int, int, int] | None:
        """How a final page is centered: only a single page, with its art centered."""
        if not self.center_art or right is not None or left in self.center_skipped:
            return None
        return self.centering.get(left)

    def centered_pages(self) -> list[int]:
        """Single pages in the comic whose art was measured off-center."""
        return [left for left, right in self.entries() if right is None and left in self.centering]

    def set_center_art(self, on: bool, focus: int = 0) -> None:
        if on == self.center_art:
            return
        self.checkpoint_preview("centering", focus)
        self.center_art = on

    def set_page_centered(self, index: int, on: bool) -> None:
        """Center one page's art, or leave it as it was."""
        if on == (index not in self.center_skipped):
            return
        self.checkpoint_preview("centering", index)
        if on:
            self.center_skipped.discard(index)
        else:
            self.center_skipped.add(index)

    def neighbor_pairs(self) -> set[tuple[int, int]]:
        order = [i for i in self.page_order if i not in self.removed_pages]
        return set(zip(order, order[1:], strict=False))

    def approved(self) -> set[int]:
        neighbors = self.neighbor_pairs()
        return {self.scan.candidates[i].left for i, yes in self.decisions.items()
                if yes and (self.scan.candidates[i].left, self.scan.candidates[i].right) in neighbors} | self.manual_pairs

    def adjacent(self, left: int, right: int) -> bool:
        order = [i for i in self.page_order if i not in self.removed_pages]
        return left in order and right in order and order.index(right) == order.index(left) + 1

    def reconcile_neighbors(self) -> None:
        """Keep ordered-pair identities stable; score only previously unseen neighbors."""
        known = self._measured_neighbors
        order = [i for i in self.page_order if i not in self.removed_pages]
        for left, right in zip(order, order[1:], strict=False):
            if (left, right) in known:
                continue
            known.add((left, right))
            if self.scan.manual_mode:
                candidate = Candidate(left, right, 0.0, True)
            else:
                a, b = self.scan.pages[left], self.scan.pages[right]
                if a.group < 0 or a.group != b.group:
                    continue
                visual_left, visual_right = (b, a) if self.scan.right_to_left else (a, b)
                left_info = _classify(visual_left.path, left)
                right_info = _classify(visual_right.path, right)
                if not is_candidate_pair(left_info, right_info):
                    continue
                candidate = _measured_candidate(left, right, visual_left.path, visual_right.path,
                                                left_info, right_info)
            self.scan.candidates.append(candidate)

    def validate_entries(self) -> None:
        entries = self.entries()
        indices = [i for a, b in entries for i in ((a,) if b is None else (a, b))]
        expected = set(range(len(self.scan.pages))) - self.removed_pages
        if len(indices) != len(set(indices)) or set(indices) != expected or any(
                b is not None and not self.adjacent(a, b) for a, b in entries):
            raise ValueError("The final page order contains an overlapping or nonadjacent spread. Review it again.")

    def current(self) -> tuple[int, Candidate] | None:
        neighbors = self.neighbor_pairs()
        used = {j for i in self.approved() for j in (i, self.partner(i))}
        for index, candidate in sorted(enumerate(self.scan.candidates), key=lambda item: (not item[1].likely, item[0])):
            if not offered(candidate):
                continue
            if ((candidate.left, candidate.right) in neighbors and index not in self.decisions and candidate.left not in used and candidate.right not in used
                    and candidate.left not in self.removed_pages
                    and candidate.right not in self.removed_pages):
                return index, candidate
        return None

    def remaining_possible(self) -> int:
        neighbors = self.neighbor_pairs()
        used = {j for i in self.approved() for j in (i, self.partner(i))}
        return sum(not candidate.likely and offered(candidate) and
                   (candidate.left, candidate.right) in neighbors and index not in self.decisions and
                   candidate.left not in used and candidate.right not in used and
                   candidate.left not in self.removed_pages and
                   candidate.right not in self.removed_pages
                   for index, candidate in enumerate(self.scan.candidates))

    def remove_page(self, index: int, also: int | None = None) -> None:
        """Leave a page out of the comic; with `also`, two at once: both pages
        of a joined spread, or both pages of a pair in review. One step undoes it."""
        pages = (index,) if also is None else (index, also)
        if (any(i < 0 or i >= len(self.scan.pages) or i in self.removed_pages for i in pages)
                or len(set(pages)) != len(pages)):
            raise ValueError("Select a page that is still in the comic.")
        if len(self.removed_pages) + len(pages) >= len(self.scan.pages):
            raise ValueError("The finished comic needs at least one page.")
        spread = None
        for left in self.approved():
            right = self.partner(left)
            if left in pages or right in pages:
                if also is None or {left, right} != set(pages):
                    raise ValueError("Split this spread before removing one of its pages.")
                spread = (left, right)
        if spread:
            index, also = spread
        self.checkpoint_preview("page removal", index)
        removal = {"removed": index}
        if also is not None:
            removal["also"] = also
        for key, page in (("rotation", index), ("also_rotation", also)):
            rotation = self.rotations.pop(page, None) if page is not None else None
            if rotation is not None:
                removal[key] = rotation
        if spread:
            overlap = self.join_overlaps.pop(index, None)
            if overlap is not None:
                removal["overlap"] = list(overlap)
            if index in self.manual_pairs:  # Joined by hand: keep how, for going back.
                self.manual_pairs.discard(index)
                self.manual_rights.pop(index, None)
                removal["turns"] = list(self.joined_rotations.pop(index, (0, 0)))
        self.history.append(removal)
        self.removed_pages.update(i for i in (index, also) if i is not None)
        self.reconcile_neighbors()

    def decide(self, yes: bool) -> None:
        item = self.current()
        if item is None:
            raise ValueError("All suggested pairs have been reviewed.")
        self.preview_history.clear()
        self.history.append(item[0] if self.scan.manual_mode else self.decisions.copy())
        self.decisions[item[0]] = yes
        layout = self.pair_layout(item[0])
        if yes and layout and layout[0]:  # Joined over a repeated strip, or covering a thin border.
            self.join_overlaps[item[1].left] = layout
        else:
            self.join_overlaps.pop(item[1].left, None)
        if yes:
            self.rotations = {left: degrees for left, degrees in self.rotations.items()
                              if left == item[1].left or
                              (left not in (item[1].left - 1, item[1].left + 1))}
        else:
            self.rotations.pop(item[1].left, None)

    def pair_layout(self, index: int) -> tuple[int, str] | None:
        """How a candidate pair would be joined: as lined up in Seam, else as suggested."""
        if index in self.proposed_layouts:
            return self.proposed_layouts[index]
        return join_layout(self.scan.candidates[index])

    def set_rotation(self, left: int, degrees: int) -> None:
        if left not in {entry_left for entry_left, _ in self.entries()} or degrees not in (0, 90, 180, 270):
            raise ValueError("Select a final page and a quarter-turn rotation.")
        if self.rotations.get(left, 0) == degrees:
            return
        self.checkpoint_preview("rotation", left)
        if degrees:
            self.rotations[left] = degrees
        else:
            self.rotations.pop(left, None)

    def back(self) -> bool:
        if not self.history:
            return False
        self.preview_history.clear()
        previous = self.history.pop()
        if isinstance(previous, int):
            self.decisions.pop(previous, None)
        elif "removed" in previous:
            left, also = previous["removed"], previous.get("also")
            self.removed_pages.difference_update((left, also))
            if "rotation" in previous:
                self.rotations[left] = previous["rotation"]
            if "also_rotation" in previous:
                self.rotations[also] = previous["also_rotation"]
            if "turns" in previous:
                self.manual_pairs.add(left)
                self.manual_rights[left] = also
                self.joined_rotations[left] = tuple(previous["turns"])
            if "overlap" in previous:
                self.join_overlaps[left] = tuple(previous["overlap"])
        else:
            self.decisions = previous
        approved = self.approved()
        self.join_overlaps = {left: value for left, value in self.join_overlaps.items() if left in approved}
        return True

    def partner(self, left: int) -> int:
        if left in self.manual_rights:
            return self.manual_rights[left]
        for index, candidate in enumerate(self.scan.candidates):
            if candidate.left == left and self.decisions.get(index) and self.adjacent(left, candidate.right):
                return candidate.right
        return left + 1

    def entries(self) -> list[tuple[int, int | None]]:
        pairs = self.approved()
        used = set()
        result = []
        for i in self.page_order:
            if i in self.removed_pages or i in used:
                continue
            right = self.partner(i) if i in pairs else None
            if right is not None:
                used.add(right)
            result.append((i, right))
        return result

    def move_entry(self, source: int, target: int, action: str = "page move") -> None:
        entries = self.entries()
        if not 0 <= source < len(entries) or not 0 <= target < len(entries):
            raise ValueError("Select pages in the final comic.")
        if source == target:
            return
        self.checkpoint_preview(action, entries[source][0])
        entry = entries.pop(source)
        entries.insert(target, entry)
        self.page_order = [i for a, b in entries for i in ((a,) if b is None else (a, b))]
        self.page_order.extend(sorted(self.removed_pages))
        self.reconcile_neighbors()

    def swap_entries(self, first: int, second: int) -> None:
        entries = self.entries()
        if second != first + 1 or not 0 <= first < second < len(entries):
            raise ValueError("Select two neighboring pages.")
        self.move_entry(first, second, "page swap")

    def join(self, left: int, override_boundary: bool = False, right: int | None = None) -> None:
        pages = self.scan.pages
        right = left + 1 if right is None else right
        if left < 0 or right < 0 or left >= len(pages) or right >= len(pages) or left == right:
            raise ValueError("Select two neighboring individual pages.")
        if left in self.removed_pages or right in self.removed_pages:
            raise ValueError("A removed page cannot be joined. Restore it first.")
        entries = self.entries()
        if (left, None) not in entries or (right, None) not in entries or entries.index((right, None)) != entries.index((left, None)) + 1:
            raise ValueError("Select two neighboring individual pages.")
        a, b = pages[left], pages[right]
        if any(left in (i, self.partner(i)) or right in (i, self.partner(i)) for i in self.approved()):
            raise ValueError("Split the existing spread first.")
        if a.group < 0 or a.group != b.group:
            if not override_boundary:
                reason = ("one of the pages has solid borders on both sides" if a.group < 0 or b.group < 0
                          else "there is a gap in the source page numbers")
                raise BoundaryOverrideRequired(f"The scanner excluded this pair because {reason}.")
        self.checkpoint_preview("join", left)
        self.join_overlaps.pop(left, None)  # A new join starts without an overlap.
        self.manual_pairs.add(left)
        self.manual_rights[left] = right
        self.joined_rotations[left] = (self.rotations.pop(left, 0), self.rotations.pop(right, 0))

    def set_overlap(self, left: int, pixels: int, top: str = "left", checkpoint: bool = True) -> None:
        """Line up a spread whose halves repeat art at the seam: they overlap by
        `pixels`, with the `top` half ("left" or "right", as displayed) covering the other."""
        if left not in self.approved():
            raise ValueError("This is not a stitched spread.")
        if top not in ("left", "right"):
            raise ValueError("Choose the left or right page to go on top.")
        right = self.partner(left)
        narrowest = min(self.scan.pages[i].width for i in (left, right))
        if not 0 <= pixels < narrowest:
            raise ValueError("The overlap must be smaller than the pages.")
        if (pixels, top) == self.join_overlaps.get(left, (0, "left")):
            return
        if checkpoint:
            self.checkpoint_preview("spread overlap", left)
        if pixels:
            self.join_overlaps[left] = (pixels, top)
        else:
            self.join_overlaps.pop(left, None)

    def split(self, left: int) -> None:
        if left not in self.approved():
            raise ValueError("This is not a stitched spread.")
        self.checkpoint_preview("split", left)
        right = self.partner(left)
        spread_rotation = self.rotations.pop(left, 0)
        turns = self.joined_rotations.pop(left, (0, 0))
        self.join_overlaps.pop(left, None)
        self.manual_pairs.discard(left)
        self.manual_rights.pop(left, None)
        for index, degrees in zip((left, right), turns, strict=True):
            degrees = (degrees + spread_rotation) % 360
            if degrees:
                self.rotations[index] = degrees
            else:
                self.rotations.pop(index, None)
        # Split the current visual spread, rather than restoring the original halves.
        # Horizontal spreads follow reading direction; vertical spreads read top to bottom.
        reverse = spread_rotation == 180 or spread_rotation == (90 if self.scan.right_to_left else 270)
        if reverse:
            a, b = self.page_order.index(left), self.page_order.index(right)
            self.page_order[a], self.page_order[b] = self.page_order[b], self.page_order[a]
        for i, candidate in enumerate(self.scan.candidates):
            if candidate.left == left and self.decisions.get(i):
                self.decisions[i] = False
        self.reconcile_neighbors()


def check_sources(scan: Scan) -> None:
    for page in scan.pages:
        try:
            s = page.path.stat()
        except FileNotFoundError as exc:
            raise ValueError(f"Source page disappeared: {page.path.name}. Scan again.") from exc
        if s.st_size != page.size or s.st_mtime_ns != page.mtime_ns:
            raise ValueError(f"Source page changed: {page.path.name}. Scan again.")


def safe_book_name(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip().rstrip(". ")
    if not value or value.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        raise ValueError("Enter a valid book name.")
    return value


def encoding_workers(requested: int = 0) -> int:
    logical = os.cpu_count() or 1
    if requested < 0:
        raise ValueError("Encoding workers cannot be negative.")
    return max(1, min(requested or min(6, max(1, logical // 2)), logical))


def _working_colour(image: Image.Image) -> Image.Image:
    """Bring a page to ordinary 8-bit colour, and tagged RGB artwork to sRGB,
    before combining, resampling or encoding it."""
    image = standard_mode(image)  # 16-bit grey and CMYK pages; see colour.py.
    profile = image.info.get("icc_profile")
    if not profile or image.mode not in ("RGB", "RGBA"):
        return image
    target = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
    converted = ImageCms.profileToProfile(image, ImageCms.ImageCmsProfile(BytesIO(profile)),
                                         target, outputMode=image.mode)
    converted.info["icc_profile"] = target.tobytes()
    return converted


def stitched_image(left: Path, right: Path, rotation: int = 0, page_rotations: tuple[int, int] = (0, 0),
                   overlap: tuple[int, str] = (0, "right")) -> Image.Image:
    """The two pages side by side (left and right as displayed), top-aligned and
    cut to the shorter height. With an overlap the right page starts that many
    pixels inside the left one, and the `top` half covers the other there."""
    if rotation not in (0, 90, 180, 270):
        raise ValueError("Spread rotation must be a quarter turn.")
    with Image.open(left) as a, Image.open(right) as b:
        # The preview uses the same top-aligned shorter-height crop as export.
        mode = "RGBA" if "A" in (a.getbands() + b.getbands()) else "RGB"
        a, b = _working_colour(ImageOps.exif_transpose(a)).convert(mode), _working_colour(ImageOps.exif_transpose(b)).convert(mode)
        a, b = rotated_image(a, page_rotations[0]), rotated_image(b, page_rotations[1])
        height = min(a.height, b.height)
        pixels, top = overlap
        pixels = max(0, min(pixels, a.width - 1, b.width - 1))
        out = Image.new(mode, (a.width + b.width - pixels, height))
        if a.info.get("icc_profile") or b.info.get("icc_profile"):
            out.info["icc_profile"] = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        left_part, right_part = a.crop((0, 0, a.width, height)), b.crop((0, 0, b.width, height))
        if top == "left":
            out.paste(right_part, (a.width - pixels, 0))
            out.paste(left_part, (0, 0))
        else:
            out.paste(left_part, (0, 0))
            out.paste(right_part, (a.width - pixels, 0))
        if rotation:
            turned = out.transpose({90: Image.Transpose.ROTATE_270,
                                    180: Image.Transpose.ROTATE_180,
                                    270: Image.Transpose.ROTATE_90}[rotation])
            out.close()
            return turned
        return out


# Overlap search: at most this share of the narrower page, and never more than OVERLAP_MAX_PIXELS.
OVERLAP_MAX_SHARE = 1 / 6
OVERLAP_MAX_PIXELS = 400
OVERLAP_STRIP = 12  # Columns of the right page compared at each position.
# A match within this many pixels of the shortest overlap tried is the art simply
# continuing across a clean seam, not a repeat, so it isn't taken.
OVERLAP_EDGE = 4


def overlap_limit(left_width: int, right_width: int) -> int:
    """The widest overlap searched for between pages this wide."""
    return int(min(OVERLAP_MAX_PIXELS, min(left_width, right_width) * OVERLAP_MAX_SHARE))


def _luma(pixels: np.ndarray) -> np.ndarray:
    pixels = pixels.astype(np.float32)
    return pixels[..., 0] * 0.299 + pixels[..., 1] * 0.587 + pixels[..., 2] * 0.114


def overlap_in(left_edge: np.ndarray, right_edge: np.ndarray, limit: int) -> int | None:
    """The repeated strip between the end of one page and the start of the next.

    left_edge: RGB pixels of the left page's right-hand end (at least `limit`
    plus a few dozen columns); right_edge: the right page's first columns. Both
    top-aligned. See measure_overlap.
    """
    height = min(left_edge.shape[0], right_edge.shape[0])
    step = max(1, height // 1500)  # Every row isn't needed to recognise the art.
    a = _luma(left_edge[:height:step])
    b = _luma(right_edge[:height:step])
    limit = min(limit, a.shape[1] - OVERLAP_STRIP)
    # Skip a thin border on the right page if the first columns don't line up.
    for skip in (0, 8, 16, 24):
        template = b[:, skip:skip + OVERLAP_STRIP]
        if template.shape[1] < OVERLAP_STRIP or template.std() < 12:
            continue
        shortest = skip + OVERLAP_STRIP
        scores = []
        for pixels in range(shortest, limit + 1):
            start = a.shape[1] - pixels + skip
            if start < 0:
                break
            scores.append((float(np.abs(a[:, start:start + OVERLAP_STRIP] - template).mean()), pixels))
        if len(scores) < 8:
            continue
        ranked = sorted(scores)
        typical = ranked[len(ranked) // 2][0]
        match, pixels = ranked[0]
        runner_up = next((score for score, other in ranked[1:] if abs(other - pixels) > 3), typical)
        if (pixels > shortest + OVERLAP_EDGE and match <= 6
                and match <= 0.25 * typical and match <= 0.5 * runner_up):
            return pixels
    return None


# Columns read from each page edge so the scan can look for a repeated strip.
OVERLAP_RIGHT_COLUMNS = 24 + OVERLAP_STRIP + 4


def measure_overlap(left: Image.Image, right: Image.Image) -> int | None:
    """How many pixels of art the halves of a spread repeat at the seam, or None.

    A spread sliced from a page narrower than two pages gives each half a copy of
    the strip around the middle. A few columns from the start of the right page
    are compared with the left page at every position near its edge; a repeat
    is accepted only where they match far better than anywhere else, and only
    when those columns have detail (flat colour would match anywhere).
    """
    height = min(left.height, right.height)
    limit = overlap_limit(left.width, right.width)
    width = min(left.width, limit + OVERLAP_RIGHT_COLUMNS)
    left_edge = np.asarray(left.convert("RGB").crop((left.width - width, 0, left.width, height)))
    right_edge = np.asarray(right.convert("RGB").crop((0, 0, min(right.width, OVERLAP_RIGHT_COLUMNS), height)))
    return overlap_in(left_edge, right_edge, limit)


def rotated_image(image: Image.Image, rotation: int) -> Image.Image:
    if rotation not in (0, 90, 180, 270):
        raise ValueError("Page rotation must be a quarter turn.")
    if not rotation:
        return image
    return image.transpose({90: Image.Transpose.ROTATE_270,
                            180: Image.Transpose.ROTATE_180,
                            270: Image.Transpose.ROTATE_90}[rotation])


