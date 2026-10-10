"""Non-destructive screenshot import staging for the existing page scanner."""
from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from .backend import Scan, scan_folder
from .detector import collect_images
from .screenshot_geometry import CropProposal, propose_crops, screenshot_workers


@dataclass(frozen=True)
class CropChoice:
    source: Path
    kind: str  # single, double, sideways
    box: tuple[int, int, int, int]
    inferred: bool = False
    custom: bool = False
    flagged: bool = False  # Possible sideways capture, retained for Final Preview.


@dataclass
class StagedScreenshots:
    scan: Scan
    flagged_pages: set[int]


def looks_like_screenshot_folder(folder: Path) -> tuple[bool, int]:
    """Quickly sample black surroundings; the user confirms the import type.

    Only a few evenly spaced images are read. This is a hint, never approval
    for cropping, and deliberately avoids the full geometry measurement.
    """
    paths = collect_images(folder)
    if not paths:
        return False, 0
    indexes = sorted({round(i * (len(paths) - 1) / min(4, len(paths) - 1))
                      for i in range(min(5, len(paths)))}) if len(paths) > 1 else [0]
    evidence = 0
    checked = 0
    for index in indexes:
        try:
            with Image.open(paths[index]) as image:
                width, height = image.size
                if not 1.15 <= width / height <= 1.75:
                    checked += 1
                    continue
                image.thumbnail((420, 320), Image.Resampling.BILINEAR)
                rgb = np.asarray(image.convert("RGB"))
        except (OSError, ValueError):
            continue
        checked += 1
        ink = np.max(rgb, axis=2) > 12
        columns = ink.mean(axis=0)
        # Emulator margins surround the centered artwork on both sides.
        margin = max(2, round(rgb.shape[1] * .015))
        if (columns[:margin].max(initial=0) < .03 and
                columns[-margin:].max(initial=0) < .03 and
                ink[:, margin:-margin].mean() > .005):
            evidence += 1
    return evidence >= (2 if checked >= 3 else 1), checked


def analyze_screenshots(folder: Path, single_size: tuple[int, int] | None = None,
                        workers: int = 0,
                        *, progress: Callable[[int, int], None] | None = None
                        ) -> list[CropProposal]:
    paths = collect_images(folder)
    if not paths:
        raise ValueError("No supported screenshots were found in this folder.")
    return propose_crops(paths, single_size=single_size, progress=progress,
                         workers=workers)


def _stage_one(choice: CropChoice, destination: Path, page_index: int) -> None:
    with Image.open(choice.source) as source:
        x0, y0, x1, y1 = choice.box
        if not (0 <= x0 < x1 <= source.width and 0 <= y0 < y1 <= source.height):
            raise ValueError(f"Crop for {choice.source.name} lies outside the screenshot.")
        if choice.kind == "double" and x1 - x0 < 2:
            raise ValueError(f"Double-page crop for {choice.source.name} is too narrow.")
        cuts = ((x0, x1),) if choice.kind != "double" else (
            (x0, x0 + (x1 - x0) // 2), (x0 + (x1 - x0) // 2, x1))
        for offset, (left, right) in enumerate(cuts):
            with source.crop((left, y0, right, y1)) as image:
                image.save(destination / f"{page_index + offset + 1:06d}.png", format="PNG")


def stage_screenshots(choices: list[CropChoice], destination: Path, output_folder: Path,
                      manual_mode: bool = False,
                      workers: int = 0,
                      *, right_to_left=False, progress: Callable[[int, int], None] | None = None
                      ) -> StagedScreenshots:
    """Crop approved boxes to temporary lossless PNGs and split every double.

    The crop rectangle uses PIL's exclusive right/bottom. An odd double width
    puts the spare column in the right half, preserving every source pixel.
    """
    if not choices:
        raise ValueError("Approve at least one screenshot before continuing.")
    destination.mkdir(parents=True, exist_ok=True)
    flagged: set[int] = set()
    jobs = []
    page_index = 0
    for choice in choices:
        if choice.kind not in ("single", "double", "sideways"):
            raise ValueError(f"Choose a page type for {choice.source.name}.")
        jobs.append((choice, page_index))
        if choice.flagged:
            flagged.update(range(page_index, page_index + (2 if choice.kind == "double" else 1)))
        page_index += 2 if choice.kind == "double" else 1
    count = min(screenshot_workers(workers), len(jobs))
    if count <= 1:
        for completed, (choice, index) in enumerate(jobs, 1):
            _stage_one(choice, destination, index)
            if progress:
                progress(completed, len(choices) * 2)
    else:
        with ThreadPoolExecutor(max_workers=count) as executor:
            futures = [executor.submit(_stage_one, choice, destination, index)
                       for choice, index in jobs]
            for completed, future in enumerate(as_completed(futures), 1):
                future.result()
                if progress:
                    progress(completed, len(choices) * 2)
    scan = scan_folder(destination, manual=manual_mode, right_to_left=right_to_left, progress=(
        lambda done, total: progress(len(choices) + round(done * len(choices) / total), len(choices) * 2)
        if progress else None))
    scan.output_folder = output_folder
    scan.screenshot_source = output_folder
    if right_to_left:
        flagged = {len(scan.pages) - 1 - index for index in flagged}
    scan.flagged_pages = flagged
    return StagedScreenshots(scan, flagged)
