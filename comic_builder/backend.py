"""Read-only comic analysis, virtual book decisions, and transactional export."""
from __future__ import annotations

import os
import errno
import logging
import copy
import re
import shutil
import threading
import uuid
import zipfile
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field, replace
from multiprocessing import get_context
from pathlib import Path
from typing import Callable

import numpy as np
import py7zr
from PIL import Image, ImageFile, ImageOps, ImageCms
from io import BytesIO

from .sharp_webp import OVERFLOW_MIN_METHOD, PARTITION0_OVERFLOW, save_sharp_webp, sharp_yuv_available

from .colour import standard_mode
from .pdf_export import PDF_IMAGE_SUFFIXES, verify_pdf, write_pdf
from .detector import (
    case_twin_groups,
    classify_page, collect_images, is_candidate_pair, load_edge_rgb,
    seam_score,
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


def describe_case_twins(names: list[str], files: list[Path]) -> list[tuple[tuple[str, ...], bool]]:
    """Groups of names differing only in capitalisation, and whether each group's files match."""
    import filecmp
    return [(tuple(names[i] for i in group),
             all(filecmp.cmp(files[group[0]], files[i], shallow=False) for i in group[1:]))
            for group in case_twin_groups(names)]


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
    """Score the seam between two neighbouring pages, as they appear side by side."""
    score = seam_score(load_edge_rgb(visual_left, "right", band=SEAM_BAND_PX),
                       load_edge_rgb(visual_right, "left", band=SEAM_BAND_PX), band=SEAM_BAND_PX)
    return Candidate(left, right, score["continuity"], score["continuity"] >= LIKELY_MIN_SCORE,
                     {"seam": score, "left_page_right_edge": left_info.right_stats,
                      "right_page_left_edge": right_info.left_stats})


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
                    case_twins=case_twins)

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

    pairs = []
    for i in range(len(pages) - 1):
        a, b = pages[i:i + 2]
        visual_left, visual_right = (b, a) if right_to_left else (a, b)
        left_info, right_info = (info[i + 1], info[i]) if right_to_left else (info[i], info[i + 1])
        if a.group >= 0 and a.group == b.group and is_candidate_pair(left_info, right_info):
            pairs.append((i, i + 1, visual_left.path, visual_right.path, left_info, right_info))
    steps["done"] = len(paths) + (len(pages) - 1 - len(pairs))  # Pairs that need no measuring.
    candidates = _scan_jobs(_measured_candidate, pairs, workers, step)
    candidates.sort(key=lambda c: (not c.likely, c.left))
    if progress:
        progress(2 * len(paths), 2 * len(paths))
    return Scan(folder, pages, candidates, gaps, numbers is None, right_to_left=right_to_left,
                case_twins=case_twins)


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
        self.preview_history: list[dict] = []
        self.deferred_possible = False
        self._measured_neighbors = set(zip(range(len(scan.pages) - 1), range(1, len(scan.pages)), strict=True))
        self._measured_neighbors.update((c.left, c.right) for c in scan.candidates)

    def preview_snapshot(self, action: str, focus: int) -> dict:
        return {"decisions": self.decisions.copy(), "manual_pairs": sorted(self.manual_pairs),
                "removed_pages": sorted(self.removed_pages), "rotations": self.rotations.copy(),
                "page_order": self.page_order.copy(), "manual_rights": self.manual_rights.copy(),
                "joined_rotations": copy.deepcopy(self.joined_rotations),
                "join_overlaps": copy.deepcopy(self.join_overlaps),
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
        self.page_order = saved["page_order"].copy()
        del self.history[saved["_history_length"]:]
        return saved

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
            if not candidate.likely and candidate.continuity < POSSIBLE_MIN_SCORE:
                continue
            if ((candidate.left, candidate.right) in neighbors and index not in self.decisions and candidate.left not in used and candidate.right not in used
                    and candidate.left not in self.removed_pages
                    and candidate.right not in self.removed_pages):
                return index, candidate
        return None

    def remaining_possible(self) -> int:
        neighbors = self.neighbor_pairs()
        used = {j for i in self.approved() for j in (i, self.partner(i))}
        return sum(not candidate.likely and candidate.continuity >= POSSIBLE_MIN_SCORE and
                   (candidate.left, candidate.right) in neighbors and index not in self.decisions and
                   candidate.left not in used and candidate.right not in used and
                   candidate.left not in self.removed_pages and
                   candidate.right not in self.removed_pages
                   for index, candidate in enumerate(self.scan.candidates))

    def remove_page(self, index: int) -> None:
        if index < 0 or index >= len(self.scan.pages) or index in self.removed_pages:
            raise ValueError("Select a page that is still in the comic.")
        if len(self.removed_pages) + 1 >= len(self.scan.pages):
            raise ValueError("The finished comic needs at least one page.")
        if any(index in (left, self.partner(left)) for left in self.approved()):
            raise ValueError("Split this spread before removing one of its pages.")
        self.checkpoint_preview("page removal", index)
        previous_rotation = self.rotations.pop(index, None)
        removal = {"removed": index}
        if previous_rotation is not None:
            removal["rotation"] = previous_rotation
        self.history.append(removal)
        self.removed_pages.add(index)
        self.reconcile_neighbors()

    def decide(self, yes: bool) -> None:
        item = self.current()
        if item is None:
            raise ValueError("All suggested pairs have been reviewed.")
        self.preview_history.clear()
        self.history.append(item[0] if self.scan.manual_mode else self.decisions.copy())
        self.decisions[item[0]] = yes
        if yes:
            self.rotations = {left: degrees for left, degrees in self.rotations.items()
                              if left == item[1].left or
                              (left not in (item[1].left - 1, item[1].left + 1))}
        else:
            self.rotations.pop(item[1].left, None)

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
            self.removed_pages.remove(previous["removed"])
            if "rotation" in previous:
                self.rotations[previous["removed"]] = previous["rotation"]
        else:
            self.decisions = previous
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
        self.manual_pairs.add(left)
        self.manual_rights[left] = right
        self.joined_rotations[left] = (self.rotations.pop(left, 0), self.rotations.pop(right, 0))

    def set_overlap(self, left: int, pixels: int, top: str = "right", checkpoint: bool = True) -> None:
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
        if (pixels, top) == self.join_overlaps.get(left, (0, "right")):
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


@dataclass
class ExportOptions:
    book_name: str
    prefixed: bool = False
    start: int = 1
    padding: int = 0  # 0 = automatic
    keep_originals: bool = False
    format: str = "jpeg"  # convert-all: jpeg/webp/png
    spread_format: str = "jpeg"  # keep-originals: format for joins and rotated singles
    jpeg_quality: int = 90
    webp_quality: int = 90
    cbz: bool = True  # Whether to create an archive (see archive_format).
    archive_format: str = "cbz"  # cbz (ZIP), cb7 (7z) or pdf
    keep_images: bool = False
    front_to_back: bool = False
    jpeg_progressive: bool = False
    jpeg_optimize: bool = True
    jpeg_subsampling: int = 0
    jpeg_smooth: int = 0
    webp_method: int = 4
    webp_lossless: bool = False
    webp_sharp_yuv: bool = False  # Sharper colour edges in lossy WebP; about twice the encoding time.
    png_compress: int = 6
    workers: int = 0  # 0 = automatic (half of logical processors, capped at six)
    size_output: str = "full"  # full, reduced, or both
    resize_mode: str = "height"  # height or width
    resize_pixels: int = 1920
    resize_reduce_only: bool = True
    resize_gamma: bool = True
    resize_filter: str = "bilinear"
    comicinfo_xml: str | None = None
    reduced_custom: bool = False
    reduced_settings: dict = field(default_factory=dict)
    custom_prefix: str | None = None  # None uses the existing number/title styles


ENCODER_FIELDS = ("format", "jpeg_quality", "webp_quality", "jpeg_progressive", "jpeg_optimize",
                  "jpeg_subsampling", "jpeg_smooth", "webp_method", "webp_lossless", "webp_sharp_yuv",
                  "png_compress")


def copy_encoding_options(opt: ExportOptions, reduced: bool = False) -> ExportOptions:
    if not reduced or not opt.reduced_custom:
        return opt
    settings = {key: value for key, value in opt.reduced_settings.items() if key in ENCODER_FIELDS}
    return replace(opt, **settings)


def encoding_workers(requested: int = 0) -> int:
    logical = os.cpu_count() or 1
    if requested < 0:
        raise ValueError("Encoding workers cannot be negative.")
    return max(1, min(requested or min(6, max(1, logical // 2)), logical))


def export_entries(plan: BookPlan, opt: ExportOptions):
    """Choose output sequence without changing approved spread orientation."""
    entries = plan.entries()
    return entries[::-1] if plan.scan.right_to_left and not opt.front_to_back else entries


def planned_names(plan: BookPlan, opt: ExportOptions, reduced: bool = False) -> list[str]:
    opt = copy_encoding_options(opt, reduced)
    title = safe_book_name(opt.book_name)
    if opt.custom_prefix is not None:
        try:
            title = safe_book_name(opt.custom_prefix)
        except ValueError as exc:
            raise ValueError("Enter a valid custom page prefix.") from exc
    entries = export_entries(plan, opt)
    width = opt.padding or max(2, len(str(len(entries))))
    if width < len(str(opt.start + len(entries) - 1)):
        raise ValueError("Number width is too short for the final page number.")
    result = []
    for index, (left, right) in enumerate(entries, opt.start):
        if opt.keep_originals and not reduced:
            suffix = (plan.scan.pages[left].path.suffix.lower() if right is None and left not in plan.rotations
                      else {"png": ".png", "jpeg": ".jpg", "webp": ".webp"}[opt.spread_format])
        else:
            suffix = {"jpeg": ".jpg", "webp": ".webp", "png": ".png"}[opt.format]
        stem = f"{index:0{width}d}"
        result.append(f"{title} - {stem}{suffix}" if opt.prefixed or opt.custom_prefix is not None else f"{stem}{suffix}")
    if len({n.casefold() for n in result}) != len(result):
        raise ValueError("The chosen options produce duplicate image names.")
    return result


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


def measure_overlap(left: Image.Image, right: Image.Image) -> int | None:
    """How many pixels of art the halves of a spread repeat at the seam, or None.

    A spread sliced from a page narrower than two pages gives each half a copy of
    the strip around the middle. A few columns from the start of the right page
    are compared with the left page at every position near its edge; a repeat
    is accepted only where they match far better than anywhere else, and only
    when those columns have detail (flat colour would match anywhere).
    """
    height = min(left.height, right.height)
    step = max(1, height // 1500)  # Every row isn't needed to recognise the art.
    a = np.asarray(left.convert("L").crop((0, 0, left.width, height)), dtype=np.float32)[::step]
    b = np.asarray(right.convert("L").crop((0, 0, right.width, height)), dtype=np.float32)[::step]
    limit = int(min(OVERLAP_MAX_PIXELS, min(left.width, right.width) * OVERLAP_MAX_SHARE))
    # Skip a thin border on the right page if the first columns don't line up.
    for skip in (0, 8, 16, 24):
        template = b[:, skip:skip + OVERLAP_STRIP]
        if template.shape[1] < OVERLAP_STRIP or template.std() < 12:
            continue
        shortest = skip + OVERLAP_STRIP
        scores = []
        for pixels in range(shortest, limit + 1):
            start = a.shape[1] - pixels + skip
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


def rotated_image(image: Image.Image, rotation: int) -> Image.Image:
    if rotation not in (0, 90, 180, 270):
        raise ValueError("Page rotation must be a quarter turn.")
    if not rotation:
        return image
    return image.transpose({90: Image.Transpose.ROTATE_270,
                            180: Image.Transpose.ROTATE_180,
                            270: Image.Transpose.ROTATE_90}[rotation])


def resized_image(image: Image.Image, opt: ExportOptions) -> Image.Image:
    """Resize after joining, using linear-light resampling when requested."""
    axis = image.height if opt.resize_mode == "height" else image.width
    if axis <= opt.resize_pixels:
        return image
    factor = opt.resize_pixels / axis
    size = (max(1, round(image.width * factor)), max(1, round(image.height * factor)))
    resample = {"bilinear": Image.Resampling.BILINEAR,
                "bicubic": Image.Resampling.BICUBIC,
                "lanczos": Image.Resampling.LANCZOS}[opt.resize_filter]
    if not opt.resize_gamma:
        return image.resize(size, resample)

    # Pillow's usual RGB resize averages gamma-encoded values; bright lines
    # against dark artwork consequently turn darker. Work one float channel at
    # a time to bound memory even for a very wide joined spread.
    rgba = image.mode == "RGBA"
    rgb = image if image.mode in ("RGB", "RGBA") else image.convert("RGB")
    alpha = np.asarray(rgb.getchannel("A"), dtype=np.float32) / 255 if rgba else None
    alpha_small = None
    if alpha is not None:
        alpha_small = np.asarray(Image.fromarray(alpha, "F").resize(size, resample), dtype=np.float32)
    lut = np.arange(256, dtype=np.float32) / 255
    lut = np.where(lut <= .04045, lut / 12.92, ((lut + .055) / 1.055) ** 2.4)
    channels = []
    for channel in rgb.convert("RGB").split():
        linear = lut[np.asarray(channel)]
        if alpha is not None:
            linear *= alpha
        small = np.asarray(Image.fromarray(linear, "F").resize(size, resample), dtype=np.float32)
        if alpha_small is not None:
            small = np.divide(small, alpha_small, out=np.zeros_like(small), where=alpha_small > 1e-6)
        small = np.clip(small, 0, 1)
        srgb = np.where(small <= .0031308, small * 12.92, 1.055 * np.power(small, 1 / 2.4) - .055)
        channels.append(Image.fromarray(np.uint8(np.clip(np.rint(srgb * 255), 0, 255)), "L"))
    if alpha_small is not None:
        channels.append(Image.fromarray(np.uint8(np.clip(np.rint(alpha_small * 255), 0, 255)), "L"))
    out = Image.merge("RGBA" if rgba else "RGB", channels)
    out.info.update(image.info)
    return out


_JPEG_BUFFER_LOCK = threading.Lock()  # ImageFile.MAXBLOCK is shared by every thread.


def _save_image(image: Image.Image, path: Path, fmt: str, opt: ExportOptions) -> None:
    colour_metadata = {"icc_profile": image.info["icc_profile"]} if image.info.get("icc_profile") else {}
    if fmt == "png":
        image.save(path, "PNG", compress_level=opt.png_compress, **colour_metadata)
    elif fmt == "jpeg":
        if image.mode != "RGB":
            background = Image.new("RGB", image.size, "white")
            if image.mode == "RGBA":
                background.paste(image, mask=image.getchannel("A"))
            else:
                background.paste(image.convert("RGB"))
            image = background
        # Pillow guesses one byte per pixel for optimized/progressive JPEG's
        # one-shot encoder buffer at qualities below 95. High-detail 4:4:4
        # images can need more, producing libjpeg's "Suspension not allowed
        # here" / "broken data stream" error even when the image is valid.
        # MAXBLOCK is a lower bound used by ImageFile._save in this process.
        with _JPEG_BUFFER_LOCK:
            previous_block = ImageFile.MAXBLOCK
            if opt.jpeg_optimize or opt.jpeg_progressive:
                ImageFile.MAXBLOCK = max(previous_block, 4 * image.width * image.height)
            try:
                image.save(path, "JPEG", quality=opt.jpeg_quality, progressive=opt.jpeg_progressive,
                           optimize=opt.jpeg_optimize, subsampling=opt.jpeg_subsampling,
                           smooth=opt.jpeg_smooth, **colour_metadata)
            finally:
                ImageFile.MAXBLOCK = previous_block
    elif fmt == "webp" and opt.webp_sharp_yuv and not opt.webp_lossless:
        save_sharp_webp(image, path, opt.webp_quality, opt.webp_method, colour_metadata.get("icc_profile"))
    elif fmt == "webp":
        def pillow_webp(method):
            image.save(path, "WEBP", quality=opt.webp_quality, method=method,
                       lossless=opt.webp_lossless, **colour_metadata)
        def overflowed(exc):
            return (not opt.webp_lossless
                    and str(exc).split(":")[0] == f"encoding error {PARTITION0_OVERFLOW}")
        try:
            pillow_webp(opt.webp_method)
        except ValueError as exc:
            # A large, detailed page can overflow lossy WebP's first partition
            # (libwebp error 6) at the fastest effort levels; from effort 3 the
            # encoder keeps it in bounds, so try again there.
            if not overflowed(exc):
                raise
            try:
                if opt.webp_method >= OVERFLOW_MIN_METHOD:
                    raise
                pillow_webp(OVERFLOW_MIN_METHOD)
            except ValueError as again:
                if not overflowed(again):
                    raise
                # cwebp can relax libwebp's limits further; Pillow can't.
                if not sharp_yuv_available():
                    raise RuntimeError(
                        f"This page ({image.width:,} × {image.height:,} px) is too large and detailed for "
                        "lossy WebP at these settings. Export it as JPEG or PNG, or as lossless WebP; "
                        "installing libwebp's cwebp tool (the \"webp\" package) lets Comic Stitcher "
                        "work around this.") from again
                save_sharp_webp(image, path, opt.webp_quality, opt.webp_method,
                                colour_metadata.get("icc_profile"), sharp_yuv=False)
    else:
        raise ValueError(f"Unsupported format: {fmt}")


def _encode_entry(source: str, right: str | None, destination: str,
                  fmt: str | None, opt: ExportOptions, reduce: bool = False, rotation: int = 0,
                  png_destination: str | None = None, page_rotations: tuple[int, int] = (0, 0),
                  overlap: tuple[int, str] = (0, "right")) -> None:
    """Top-level job so Windows spawn and PyInstaller can run it in a process."""
    if right is None:
        with Image.open(source) as src:
            image = ImageOps.exif_transpose(src)
        if rotation:
            turned = rotated_image(image, rotation)
            image.close()
            image = turned
    else:
        image = stitched_image(Path(source), Path(right), rotation, page_rotations, overlap)
    try:
        if fmt is not None or png_destination:
            converted = _working_colour(image)
            if converted is not image:
                image.close()
                image = converted
        if reduce:
            reduced = resized_image(image, opt)
            if reduced is not image:
                image.close()
                image = reduced
        try:
            if png_destination:
                _save_image(image, Path(png_destination), "png", opt)
            if fmt is None:
                shutil.copyfile(source, destination)
            else:
                _save_image(image, Path(destination), fmt, opt)
        except Exception as exc:
            raise RuntimeError(f"Failed to encode {Path(source).name} as {Path(destination).name}: {exc}") from exc
    finally:
        image.close()


def pdf_format_problem(opt: ExportOptions) -> str | None:
    """Why these settings can't make a PDF, or None. PDF holds JPEG and PNG, not WebP."""
    formats = []
    if opt.size_output != "reduced":
        formats.append(opt.spread_format if opt.keep_originals else opt.format)
    if opt.size_output != "full":
        formats.append(copy_encoding_options(opt, True).format)
    if "webp" in formats:
        return "PDF can hold JPEG and PNG pages, but not WebP. Choose JPEG or PNG for the PDF's pages."
    return None


def _entry_format(plan: BookPlan, opt: ExportOptions, encoding_opt: ExportOptions,
                  left: int, right: int | None, reduce: bool) -> str | None:
    """The format a final page is encoded in, or None when its file is copied."""
    copy = not reduce and right is None and left not in plan.rotations and opt.keep_originals
    if (copy and opt.cbz and opt.archive_format == "pdf"
            and plan.scan.pages[left].path.suffix.lower() not in PDF_IMAGE_SUFFIXES):
        copy = False  # A PDF can't hold this page as it is (WebP, for example).
    return None if copy else encoding_opt.format if reduce or not opt.keep_originals else opt.spread_format


def _entry_sources(plan: BookPlan, left: int, right: int | None) -> tuple[Path, Path | None]:
    visual_left, visual_right = (right, left) if right is not None and plan.scan.right_to_left else (left, right)
    return (plan.scan.pages[visual_left].path,
            plan.scan.pages[visual_right].path if visual_right is not None else None)


def _entry_rotations(plan: BookPlan, left: int) -> tuple[int, tuple[int, int]]:
    joined = plan.joined_rotations.get(left, (0, 0))
    return plan.rotations.get(left, 0), joined[::-1] if plan.scan.right_to_left else joined


def _entry_overlap(plan: BookPlan, left: int) -> tuple[int, str]:
    return plan.join_overlaps.get(left, (0, "right"))


def _oriented_size(page: Page, rotation: int = 0) -> tuple[int, int]:
    """A page's pixel size as exported. The scan recorded it, already turned for
    EXIF orientation, so no file needs opening."""
    return (page.height, page.width) if rotation in (90, 270) else (page.width, page.height)


def _entry_size(plan: BookPlan, opt: ExportOptions, left: int, right: int | None, reduce: bool) -> tuple[int, int]:
    """Pixel size of a final page, matching stitched_image and resized_image."""
    visual_left, visual_right = (right, left) if right is not None and plan.scan.right_to_left else (left, right)
    rotation, page_rotations = _entry_rotations(plan, left)
    if visual_right is None:
        width, height = _oriented_size(plan.scan.pages[visual_left], rotation)
    else:
        (a_width, a_height), (b_width, b_height) = (
            _oriented_size(plan.scan.pages[visual_left], page_rotations[0]),
            _oriented_size(plan.scan.pages[visual_right], page_rotations[1]))
        width, height = a_width + b_width - _entry_overlap(plan, left)[0], min(a_height, b_height)
        if rotation in (90, 270):
            width, height = height, width
    if reduce:
        axis = height if opt.resize_mode == "height" else width
        if axis > opt.resize_pixels:
            factor = opt.resize_pixels / axis
            width, height = max(1, round(width * factor)), max(1, round(height * factor))
    return width, height


ESTIMATE_SAMPLES = 24
# Each sample encodes a band across the page this tall, at a different height
# on each page, so many pages are covered for the work of a few.
ESTIMATE_BAND = 0.25
ARCHIVE_ENTRY_OVERHEAD = 200  # Bytes of archive bookkeeping per stored file, roughly.


def _estimate_sample(source: str, right: str | None, rotation: int, page_rotations: tuple[int, int],
                     full: tuple[str, ExportOptions] | None, reduced: tuple[str, ExportOptions] | None,
                     band: tuple[float, float] = (0.0, 1.0),
                     overlap: tuple[int, str] = (0, "right")) -> tuple[tuple[int, int] | None, ...]:
    """(encoded bytes, pixels) of part of one final page at full and/or reduced size.

    The page is prepared once, the way _encode_entry prepares it; then a
    full-width band (top and height as fractions of the page) is colour-matched,
    resized and encoded in memory. Top level so it can run in another process.
    """
    if right is None:
        with Image.open(source) as src:
            image = ImageOps.exif_transpose(src)
            image.load()
        if rotation:
            turned = rotated_image(image, rotation)
            if turned is not image:
                image.close()
            image = turned
    else:
        image = stitched_image(Path(source), Path(right), rotation, page_rotations, overlap)
    try:
        full_width, full_height = image.size
        top, height = band
        if height < 1:
            rows = max(1, min(image.height, round(image.height * height)))
            first = min(image.height - rows, round(image.height * top))
            strip = image.crop((0, first, image.width, first + rows))
            strip.info.update(image.info)
            image.close()
            image = strip
        converted = _working_colour(image)
        if converted is not image:
            image.close()
            image = converted
        sizes = []
        for wanted, reduce in ((full, False), (reduced, True)):
            if wanted is None:
                sizes.append(None)
                continue
            fmt, opt = wanted
            page = image
            axis = full_height if opt.resize_mode == "height" else full_width
            if reduce and axis > opt.resize_pixels:
                # Scale the band as the whole page would be scaled. Bands are
                # full width, so matching the page's new width does that.
                width = max(1, round(full_width * opt.resize_pixels / axis))
                page = resized_image(image, replace(opt, resize_mode="width", resize_pixels=width))
            try:
                buffer = BytesIO()
                _save_image(page, buffer, fmt, opt)
                sizes.append((buffer.tell(), page.width * page.height))
            finally:
                if page is not image:
                    page.close()
        return sizes[0], sizes[1]
    finally:
        image.close()


# Offer lossless WebP when its estimate is at most this share of the chosen lossy format's.
LOSSLESS_HINT_SHARE = 0.75


def lossless_alternative(opt: ExportOptions) -> ExportOptions | None:
    """The same export with the full-size copy's encoded pages as lossless WebP,
    for comparing sizes; None when there is nothing to compare.

    Clean line art (black-and-white strips, for example) is often several times
    smaller as lossless WebP than as JPEG or lossy WebP, with no loss at all.
    Scans and full-colour art usually aren't, which the estimate shows. Only CBZ
    and CB7 archives qualify (PDF pages are JPEG or PNG), and only when the
    full-size copy's encoded pages use a lossy format.
    """
    if not opt.cbz or opt.archive_format not in ("cbz", "cb7") or opt.size_output not in ("full", "both"):
        return None
    field_name = "spread_format" if opt.keep_originals else "format"
    chosen = getattr(opt, field_name)
    if chosen == "png" or (chosen == "webp" and opt.webp_lossless):
        return None
    return replace(opt, **{field_name: "webp"}, webp_lossless=True, size_output="full",
                   reduced_custom=False, keep_images=False)


# The early check, run in the background during review: a few pages, fast lossless effort.
LOSSLESS_CHECK_SAMPLES = 6
LOSSLESS_CHECK_METHOD = 1
# At the fast effort lossless only gets smaller at the chosen one, so a check
# that finds it no smaller than the lossy format rules the suggestion out.
LOSSLESS_CHECK_SKIP_SHARE = 1.0


def lossless_check(plan: BookPlan, opt: ExportOptions,
                   cancelled: Callable[[], bool] | None = None) -> float | None:
    """Lossless WebP's estimated size as a share of the chosen lossy format's,
    from a few sample pages at full size; None when there's nothing to compare
    or when cancelled. Quick enough to run, on one process, while pages are reviewed.
    """
    alternative = lossless_alternative(opt)
    if alternative is None:
        return None
    alternative = replace(alternative, webp_method=min(alternative.webp_method, LOSSLESS_CHECK_METHOD))
    chosen = replace(opt, size_output="full", reduced_custom=False, keep_images=False)
    lossy = estimate_export_size(plan, chosen, samples=LOSSLESS_CHECK_SAMPLES, cancelled=cancelled)
    if lossy is None:
        return None
    lossless = estimate_export_size(plan, alternative, samples=LOSSLESS_CHECK_SAMPLES, cancelled=cancelled)
    if lossless is None:
        return None
    return lossless["full"] / max(1, lossy["full"])


def estimate_export_size(plan: BookPlan, opt: ExportOptions, samples: int = ESTIMATE_SAMPLES,
                         cancelled: Callable[[], bool] | None = None, workers: int = 1,
                         progress: Callable[[dict[str, int], int, int], None] | None = None,
                         executor: ProcessPoolExecutor | None = None) -> dict[str, int] | None:
    """Estimated bytes of each finished comic ("full" and/or "reduced").

    Copied pages are counted exactly. Pages that will be encoded are estimated
    by encoding a few, spaced evenly through the book, with the chosen settings
    and scaling their bytes per pixel to the others' pixel area. A sample page
    needed by both copies is prepared once. With several workers, samples are
    encoded in parallel processes, and progress(estimate, done, total) reports
    a provisional estimate as they finish; pass an executor to reuse its
    already-started processes. Measures the archive, or the PNG page folder when
    no archive is made. Returns None if cancelled.
    """
    import time
    started = time.monotonic()
    entries = export_entries(plan, opt)
    variants = ("full", "reduced") if opt.size_output == "both" else (opt.size_output,)
    plans = {}
    for variant in variants:
        reduce = variant == "reduced"
        encoding_opt = copy_encoding_options(opt, reduce)
        exact, encoded = 0, []
        for index, (left, right) in enumerate(entries):
            fmt = _entry_format(plan, opt, encoding_opt, left, right, reduce) if opt.cbz else "png"
            if fmt is None:
                exact += plan.scan.pages[left].size  # Copied as is; size recorded by the scan.
            else:
                width, height = _entry_size(plan, opt, left, right, reduce)
                encoded.append((index, fmt, width * height))
        picks = sorted({round(i * (len(encoded) - 1) / max(1, samples - 1))
                        for i in range(min(samples, len(encoded)))})
        plans[variant] = {"exact": exact, "encoded": encoded, "options": encoding_opt,
                          "samples": {encoded[pick][0]: encoded[pick] for pick in picks},
                          "bytes": 0, "pixels": 0}

    jobs = []
    for index in sorted({index for info in plans.values() for index in info["samples"]}):
        left, right = entries[index]
        source, partner = _entry_sources(plan, left, right)
        rotation, page_rotations = _entry_rotations(plan, left)
        full, reduced = ((plans[variant]["samples"][index][1], plans[variant]["options"])
                         if variant in plans and index in plans[variant]["samples"] else None
                         for variant in ("full", "reduced"))
        top = (len(jobs) * 0.381966) % 1 * (1 - ESTIMATE_BAND)  # Spread band heights evenly.
        jobs.append((index, (str(source), str(partner) if partner else None, rotation, page_rotations,
                             full, reduced, (top, ESTIMATE_BAND), _entry_overlap(plan, left))))

    def estimate():
        result = {}
        for variant, info in plans.items():
            total = info["exact"]
            if info["encoded"]:
                per_pixel = info["bytes"] / max(1, info["pixels"])
                total += round(per_pixel * sum(pixels for _, _, pixels in info["encoded"]))
            result[variant] = total + ARCHIVE_ENTRY_OVERHEAD * (len(entries) + 1)
        return result

    def record(index, sizes):
        for variant, measured in zip(("full", "reduced"), sizes, strict=True):
            if measured is not None:
                plans[variant]["bytes"] += measured[0]
                plans[variant]["pixels"] += measured[1]

    def provisional(done):
        # After a quarter of the samples, once each encoded copy has some.
        if (progress and max(1, len(jobs) // 4) <= done < len(jobs)
                and all(info["pixels"] or not info["encoded"] for info in plans.values())):
            progress(estimate(), done, len(jobs))

    def interrupted():
        return cancelled is not None and cancelled()

    measured = time.monotonic()

    def finish(how):
        now = time.monotonic()
        logging.info("Size estimate: page sizes %.1f s, %d samples %.1f s (%s), total %.1f s",
                     measured - started, len(jobs), now - measured, how, now - started)
        return estimate()

    if not jobs:
        return finish("none needed")
    if executor is None and (workers <= 1 or len(jobs) == 1):
        for done, (index, arguments) in enumerate(jobs, 1):
            if interrupted():
                return None
            record(index, _estimate_sample(*arguments))
            provisional(done)
        return finish("1 process")
    own = executor is None
    if own:
        executor = ProcessPoolExecutor(max_workers=min(workers, len(jobs)), mp_context=get_context("spawn"))
    pending = {}
    try:
        pending = {executor.submit(_estimate_sample, *arguments): index for index, arguments in jobs}
        done = 0
        while pending:
            if interrupted():
                return None
            finished, _ = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
            for future in finished:
                record(pending.pop(future), future.result())
                done += 1
            if finished:
                provisional(done)
        return finish(f"{workers} processes")
    finally:
        for future in pending:
            future.cancel()  # Samples already running finish on their own.
        if own:
            executor.shutdown(wait=False, cancel_futures=True)


def publish_archive(source: Path, destination: Path) -> None:
    """Publish a verified archive without replacing any existing destination."""
    try:
        os.link(source, destination)
        return
    except OSError as exc:
        unsupported = exc.errno in (errno.EXDEV, errno.EPERM, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP)
        if getattr(exc, "winerror", None) in (1, 50):
            unsupported = True
        if not unsupported:
            raise
    if os.name == "nt":
        # Windows rename fails if the destination already exists, including exFAT.
        os.rename(source, destination)
        return
    # Filesystems without hard links on other platforms: exclusive creation still
    # protects an existing file. Remove only our own partial copy on failure.
    with destination.open("xb") as target:
        try:
            with source.open("rb") as origin:
                shutil.copyfileobj(origin, target)
            target.flush()
            os.fsync(target.fileno())
        except BaseException:
            target.close()
            destination.unlink()
            raise


def export_book(plan: BookPlan, opt: ExportOptions,
                progress: Callable[[int, int], None] | None = None) -> Path | tuple[Path, Path]:
    if not opt.cbz and not opt.keep_images:
        raise ValueError("Choose an archive, processed images, or both.")
    if opt.archive_format not in ("cbz", "cb7", "pdf"):
        raise ValueError("Choose CBZ, CB7 or PDF as the archive format.")
    if opt.cbz and opt.archive_format == "pdf" and pdf_format_problem(opt):
        raise ValueError(pdf_format_problem(opt))
    if opt.format not in ("jpeg", "webp", "png") or opt.spread_format not in ("png", "jpeg", "webp"):
        raise ValueError("Choose a supported output format.")
    if opt.size_output not in ("full", "reduced", "both"):
        raise ValueError("Choose full size, reduced size, or both.")
    if opt.resize_mode not in ("height", "width") or opt.resize_filter not in ("bilinear", "bicubic", "lanczos"):
        raise ValueError("Choose a supported resize mode and resampling method.")
    if not 1 <= opt.resize_pixels <= 20000:
        raise ValueError("Resize dimension must be between 1 and 20000 pixels.")
    for quality in (opt.jpeg_quality, opt.webp_quality):
        if not 1 <= quality <= 100:
            raise ValueError("Quality must be between 1 and 100.")
    plan.validate_entries()
    worker_count = encoding_workers(opt.workers)
    check_sources(plan.scan)
    final_page_indices = {left for left, _ in plan.entries()}
    if any(left not in final_page_indices or degrees not in (90, 180, 270)
           for left, degrees in plan.rotations.items()):
        raise ValueError("A saved spread rotation is invalid.")
    title = safe_book_name(opt.book_name)
    folder = plan.scan.output_folder or plan.scan.folder
    variants = ("full", "reduced") if opt.size_output == "both" else (opt.size_output,)
    def destination(variant: str) -> Path:
        suffix = f" - {'Full' if variant == 'full' else 'Reduced'}" if opt.size_output == "both" else ""
        return folder / f"{title}{suffix}.{opt.archive_format}" if opt.cbz else folder / f"{title}{suffix} - PNG pages"

    for variant in variants:
        suffix = f" - {'Full' if variant == 'full' else 'Reduced'}" if opt.size_output == "both" else ""
        for output in ((folder / f"{title}{suffix}.{opt.archive_format}",) if opt.cbz else ()) + ((folder / f"{title}{suffix} - PNG pages",) if opt.keep_images else ()):
            if output.exists():
                raise FileExistsError(f"Output already exists: {output.name}. Choose another book name.")

    work = folder / f"__ComicStitcher_work_{uuid.uuid4().hex}"
    work.mkdir(exist_ok=False)
    entries = export_entries(plan, opt)
    total = len(variants) * (len(entries) + int(opt.cbz))
    completed = 0

    def done():
        nonlocal completed
        completed += 1
        if progress:
            progress(completed, total)

    published: list[Path] = []
    try:
        for variant in variants:
            reduce = variant == "reduced"
            encoding_opt = copy_encoding_options(opt, reduce)
            variant_work = work / variant
            variant_work.mkdir()
            variant_names = planned_names(plan, opt, reduced=reduce)
            png_work = work / f"{variant}-png"
            if opt.keep_images:
                png_work.mkdir()
            jobs = []
            for (left, right), name in zip(entries, variant_names, strict=True):
                fmt = _entry_format(plan, opt, encoding_opt, left, right, reduce)
                if not opt.cbz:
                    fmt = None  # Only lossless PNG pages are being kept.
                jobs.append((*_entry_sources(plan, left, right), variant_work / name, fmt,
                             *_entry_rotations(plan, left), _entry_overlap(plan, left)))
            encode_count = sum(fmt is not None or opt.keep_images for _, _, _, fmt, _, _, _ in jobs)
            if worker_count == 1 or encode_count <= 1:
                for source, right, dest, fmt, rotation, page_rotations, overlap in jobs:
                    if fmt is None and not opt.keep_images:
                        shutil.copyfile(source, dest)
                    else:
                        _encode_entry(str(source), str(right) if right else None, str(dest), fmt, encoding_opt, reduce, rotation,
                                      str(png_work / (dest.stem + ".png")) if opt.keep_images else None, page_rotations,
                                      overlap)
                    done()
            else:
                # Spawn from the GUI worker; cap queued image jobs for large books.
                with ProcessPoolExecutor(max_workers=min(worker_count, encode_count),
                                         mp_context=get_context("spawn")) as executor:
                    pending = set()
                    try:
                        for source, right, dest, fmt, rotation, page_rotations, overlap in jobs:
                            if fmt is None and not opt.keep_images:
                                shutil.copyfile(source, dest)
                                done()
                            else:
                                pending.add(executor.submit(_encode_entry, str(source),
                                                            str(right) if right else None,
                                                            str(dest), fmt, encoding_opt, reduce, rotation,
                                                            str(png_work / (dest.stem + ".png")) if opt.keep_images else None, page_rotations,
                                                            overlap))
                            if len(pending) >= 2 * worker_count:
                                finished, pending = wait(pending, return_when=FIRST_COMPLETED)
                                for future in finished:
                                    future.result()
                                    done()
                        while pending:
                            finished, pending = wait(pending, return_when=FIRST_COMPLETED)
                            for future in finished:
                                future.result()
                                done()
                    except Exception:
                        for future in pending:
                            future.cancel()
                        raise
            check_sources(plan.scan)
            archive_names = list(variant_names)
            if opt.comicinfo_xml is not None:
                from .comicinfo import export_xml
                if opt.cbz and opt.archive_format != "pdf":  # A PDF has no place for it.
                    data = export_xml(opt.comicinfo_xml, plan, entries, [variant_work / name for name in variant_names])
                    (variant_work / "ComicInfo.xml").write_bytes(data)
                    archive_names.append("ComicInfo.xml")
                if opt.keep_images:
                    png_paths = [png_work / (Path(name).stem + ".png") for name in variant_names]
                    (png_work / "ComicInfo.xml").write_bytes(export_xml(opt.comicinfo_xml, plan, entries, png_paths))
            if opt.cbz:
                temporary_archive = work / f"{variant}.{opt.archive_format}"
                if opt.archive_format == "pdf":
                    write_pdf([variant_work / name for name in variant_names], temporary_archive,
                              title=title, right_to_left=plan.scan.right_to_left and opt.front_to_back)
                    valid = verify_pdf(temporary_archive, len(variant_names))
                elif opt.archive_format == "cbz":
                    with zipfile.ZipFile(temporary_archive, "w", compression=zipfile.ZIP_STORED) as zf:
                        for name in archive_names:
                            zf.write(variant_work / name, arcname=name)
                    with zipfile.ZipFile(temporary_archive) as zf:
                        valid = zf.namelist() == archive_names and zf.testzip() is None
                else:
                    # Images are already compressed; COPY stores them without
                    # wasting time recompressing them with LZMA2.
                    with py7zr.SevenZipFile(temporary_archive, "w", filters=[{"id": py7zr.FILTER_COPY}]) as zf:
                        for name in archive_names:
                            zf.write(variant_work / name, arcname=name)
                    with py7zr.SevenZipFile(temporary_archive, "r") as zf:
                        valid = zf.getnames() == archive_names and zf.testzip() is None
                if not valid:
                    raise ValueError("Archive verification failed; working images were preserved.")
                done()
        # Publish only after every requested copy has been built and verified.
        check_sources(plan.scan)
        for variant in variants:
            if opt.cbz:
                out = destination(variant)
                publish_archive(work / f"{variant}.{opt.archive_format}", out)
                published.append(out)
            if opt.keep_images:
                suffix = f" - {'Full' if variant == 'full' else 'Reduced'}" if opt.size_output == "both" else ""
                out = folder / f"{title}{suffix} - PNG pages"
                (work / f"{variant}-png").rename(out)
                published.append(out)
        try:
            shutil.rmtree(work)
        except OSError:
            logging.exception("Comic was created, but could not remove export working folder %s", work)
        results = tuple(destination(variant) for variant in variants)
        return results if len(results) == 2 else results[0]
    except Exception as exc:
        for path in reversed(published):
            if path.is_dir():
                path.rename(work / f"recovered_{len(list(work.iterdir()))}")
            else:
                # A no-replace rename may have moved the only archive out of work.
                path.rename(work / f"recovered_{path.name}")
        raise RuntimeError(f"Export stopped: {exc}\nWorking images, if any: {work}") from exc
