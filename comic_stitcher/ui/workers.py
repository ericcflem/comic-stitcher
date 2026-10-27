"""Background threads: running a job, size estimates, the lossless check and image loading."""
from __future__ import annotations

import time
import copy
from dataclasses import replace
from collections import deque
from pathlib import Path
from threading import Condition
from PIL import Image, ImageOps
from PySide6.QtCore import QThread, Signal
from ..backend import stitched_image, rotated_image, encoding_workers
from ..export import estimate_export_size, ESTIMATE_SAMPLES, lossless_alternative, lossless_check
from .. import diagnostics
from ..pdf_import import measure_pdf_pages, suggest_from_pages
from .common import pixels_from_pil


class Worker(QThread):
    progress = Signal(int, int)
    result = Signal(object)
    failure = Signal(str)

    def __init__(self, fn, *args):
        super().__init__()
        self.fn = fn
        self.args = args

    def run(self):
        try:
            self.result.emit(self.fn(*self.args, progress=self.progress.emit))
        except Exception as exc:
            diagnostics.exception("Scan or export failed")
            self.failure.emit(str(exc))


class SizeEstimateWorker(QThread):
    """Estimates export size in the background; a newer request replaces an older one.

    Keeps its helper processes between estimates while the export screen is open,
    since starting them is slow on Windows; release() lets them go.
    """
    ready = Signal(int, object)
    provisional = Signal(int, object)  # An early estimate while more samples are encoded.
    failed = Signal(int, str)
    lossless = Signal(int, object)  # The full-size copy's estimate as lossless WebP, once the estimate is shown.

    def __init__(self):
        super().__init__()
        self.condition = Condition()
        self.requested = None
        self.stopping = False
        self.cancelled = False
        self.releasing = False
        self.pool = None
        self.pool_size = 0

    def request(self, generation, plan, opt, compare_lossless=True):
        with self.condition:
            self.requested = (generation, copy.deepcopy(plan), replace(opt), compare_lossless)
            self.cancelled = False
            self.releasing = False
            self.condition.notify()

    def cancel(self):
        with self.condition:
            self.requested = None
            self.cancelled = True

    def release(self):
        """Cancel, and end the helper processes once idle."""
        with self.condition:
            self.requested = None
            self.cancelled = True
            self.releasing = True
            self.condition.notify()

    def stop(self):
        with self.condition:
            self.stopping = True
            self.requested = None
            self.condition.notify_all()

    def _interrupted(self):
        return self.stopping or self.cancelled or self.requested is not None

    def _end_pool(self):
        if self.pool is not None:
            self.pool.shutdown(wait=False, cancel_futures=True)
            self.pool, self.pool_size = None, 0

    def _pool_for(self, workers):
        if workers <= 1:
            return None
        if self.pool is None or self.pool_size != workers:
            from concurrent.futures import ProcessPoolExecutor
            from multiprocessing import get_context
            self._end_pool()
            self.pool = ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn"))
            self.pool_size = workers
        return self.pool

    def run(self):
        try:
            while True:
                with self.condition:
                    while self.requested is None and not self.stopping and not self.releasing:
                        self.condition.wait()
                    if self.stopping:
                        return
                    if self.requested is None:  # Released while idle.
                        self.releasing = False
                        self._end_pool()
                        continue
                    generation, plan, opt, compare_lossless = self.requested
                    self.requested = None
                started = time.monotonic()
                try:
                    workers = encoding_workers(opt.workers)
                    result = estimate_export_size(
                        plan, opt, cancelled=self._interrupted, workers=workers,
                        executor=self._pool_for(min(workers, ESTIMATE_SAMPLES)),
                        progress=lambda sizes, _done, _total, generation=generation:
                            self.provisional.emit(generation, sizes))
                except Exception as exc:  # The estimate is optional; export reports real problems.
                    diagnostics.note("Size estimate failed: %s", exc)
                    self._end_pool()  # A broken pool is not reused.
                    self.failed.emit(generation, str(exc))
                    continue
                if result is not None:
                    diagnostics.note("Size estimate shown after %.1f s", time.monotonic() - started)
                    self.ready.emit(generation, result)
                    if compare_lossless:
                        self._compare_lossless(generation, plan, opt, workers)
        finally:
            self._end_pool()

    def _compare_lossless(self, generation, plan, opt, workers):
        """Estimate the same comic as lossless WebP, to suggest it when far smaller."""
        alternative = lossless_alternative(opt)
        if alternative is None or self._interrupted():
            return
        try:
            result = estimate_export_size(plan, alternative, cancelled=self._interrupted, workers=workers,
                                          executor=self._pool_for(min(workers, ESTIMATE_SAMPLES)))
        except Exception as exc:  # Only a suggestion; say nothing.
            diagnostics.note("Lossless WebP comparison failed: %s", exc)
            self._end_pool()
            return
        if result is not None:
            self.lossless.emit(generation, result["full"])


class LosslessCheckWorker(QThread):
    """The quick lossless WebP check, on one process, while the comic is reviewed."""
    done = Signal(object, object)  # The plan checked, and lossless WebP's share of the size (or None).

    def __init__(self, plan, opt):
        super().__init__()
        self.plan = plan
        self.copy, self.opt = copy.deepcopy(plan), replace(opt)

    def run(self):
        try:
            share = lossless_check(self.copy, self.opt, cancelled=self.isInterruptionRequested)
        except Exception as exc:  # Only a suggestion; the full comparison can still run.
            diagnostics.note("Lossless WebP check failed: %s", exc)
            share = None
        if share is not None:
            diagnostics.note("Lossless WebP check: %.2f of the lossy size", share)
        if not self.isInterruptionRequested():
            self.done.emit(self.plan, share)


class ReviewImageWorker(QThread):
    """Decode one pair at a time, discarding queued pairs after navigation."""

    ready = Signal(int, object, object)
    failed = Signal(int, str)

    def __init__(self):
        super().__init__()
        self.condition = Condition()
        self.requested: tuple[int, Path, Path] | None = None
        self.stopping = False

    def request(self, generation: int, left: Path, right: Path):
        with self.condition:
            self.requested = (generation, left, right)
            self.condition.notify()

    def stop(self):
        with self.condition:
            self.stopping = True
            self.requested = None
            self.condition.notify_all()

    def run(self):
        while True:
            with self.condition:
                while self.requested is None and not self.stopping:
                    self.condition.wait()
                if self.stopping:
                    return
                generation, left, right = self.requested
                self.requested = None
            try:
                with Image.open(left) as image:
                    image.draft("RGB", (1800, 2200))
                    first = pixels_from_pil(image, (1800, 2200))
                with Image.open(right) as image:
                    image.draft("RGB", (1800, 2200))
                    second = pixels_from_pil(image, (1800, 2200))
                diagnostics.note("Pair images decoded: request %d", generation)
                self.ready.emit(generation, first, second)
            except Exception as exc:
                diagnostics.exception("Pair image decoding failed")
                self.failed.emit(generation, str(exc))


class CropPreviewWorker(QThread):
    ready = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self):
        super().__init__()
        self.condition = Condition()
        self.requested = None
        self.stopping = False

    def request(self, generation, path):
        with self.condition:
            self.requested = (generation, path)
            self.condition.notify()

    def stop(self):
        with self.condition:
            self.stopping = True
            self.requested = None
            self.condition.notify_all()

    def run(self):
        while True:
            with self.condition:
                while self.requested is None and not self.stopping:
                    self.condition.wait()
                if self.stopping:
                    return
                generation, path = self.requested
                self.requested = None
            try:
                with Image.open(path) as image:
                    image.draft("RGB", (1300, 1000))
                    pixels = pixels_from_pil(image, (1300, 1000))
                self.ready.emit(generation, pixels)
            except Exception as exc:
                self.failed.emit(generation, str(exc))


class ThumbnailWorker(QThread):
    """Decode only visible thumbnails, prioritizing the latest scroll position."""

    ready = Signal(int, int, object)
    failed = Signal(int, int)

    def __init__(self):
        super().__init__()
        self.condition = Condition()
        self.jobs = deque()
        self.pending = set()
        self.entries = []
        self.generation = 0
        self.stopping = False

    def reset(self, entries: list[tuple[Path, Path | None, int]]) -> int:
        with self.condition:
            self.generation += 1
            self.entries = entries
            self.jobs.clear()
            self.pending.clear()
            self.condition.notify_all()
            return self.generation

    def request(self, generation: int, index: int):
        with self.condition:
            if generation != self.generation or not 0 <= index < len(self.entries):
                return
            if index in self.pending:
                self.jobs.remove(index)
            else:
                self.pending.add(index)
            self.jobs.appendleft(index)
            self.condition.notify()

    def stop(self):
        with self.condition:
            self.stopping = True
            self.jobs.clear()
            self.condition.notify_all()

    def run(self):
        while True:
            with self.condition:
                while not self.jobs and not self.stopping:
                    self.condition.wait()
                if self.stopping:
                    return
                index = self.jobs.popleft()
                self.pending.remove(index)
                generation = self.generation
                left, right, rotation, *extra = self.entries[index]
                page_rotations = extra[0] if extra else (0, 0)
                overlap = extra[1] if len(extra) > 1 else (0, "right")
            try:
                if right is None:
                    with Image.open(left) as image:
                        image.draft("RGB", (100, 84))
                        if rotation:
                            oriented = ImageOps.exif_transpose(image)
                            try:
                                turned = rotated_image(oriented, rotation)
                                try:
                                    thumbnail = pixels_from_pil(turned, (100, 84))
                                finally:
                                    turned.close()
                            finally:
                                oriented.close()
                        else:
                            thumbnail = pixels_from_pil(image, (100, 84))
                else:
                    image = stitched_image(left, right, rotation, page_rotations, overlap)
                    try:
                        thumbnail = pixels_from_pil(image, (100, 84))
                    finally:
                        image.close()
                self.ready.emit(generation, index, thumbnail)
            except Exception:
                self.failed.emit(generation, index)


class PdfPreviewWorker(QThread):
    """Read PDF dimensions and a small page preview without blocking the dialog."""
    ready = Signal(int, int, object, object)
    failed = Signal(str)
    resolution_found = Signal(object)  # dpi of the PDF's page images, or None

    def __init__(self, source: Path, right_to_left: bool = False):
        super().__init__()
        self.source = source
        self.right_to_left = right_to_left
        self.condition = Condition()
        self.requested = None
        self.stopping = False

    def request(self, generation: int, index: int, smooth_images: bool):
        with self.condition:
            self.requested = (generation, index, smooth_images)
            self.condition.notify()

    def stop(self):
        with self.condition:
            self.stopping = True
            self.requested = None
            self.condition.notify_all()

    def run(self):
        try:
            measured = measure_pdf_pages(self.source)
            found = suggest_from_pages(measured)
            diagnostics.note("PDF resolution suggested: %s (sampled pages: %s)", found, ", ".join(
                f"{number}: {round(dpi) if dpi else '-'} dpi" for number, dpi, _ in measured))
        except Exception as exc:  # A suggestion is optional; the preview reports real problems.
            diagnostics.note("PDF resolution not measured: %s", exc)
            found = None
        self.resolution_found.emit(found)
        try:
            import pypdfium2 as pdfium
            document = pdfium.PdfDocument(self.source)
            try:
                count = len(document)
                if not count:
                    raise ValueError("The PDF has no pages.")
                while True:
                    with self.condition:
                        while self.requested is None and not self.stopping:
                            self.condition.wait()
                        if self.stopping:
                            return
                        generation, index, smooth_images = self.requested
                        self.requested = None
                    source_index = min(index, count - 1)
                    if self.right_to_left:
                        source_index = count - 1 - source_index
                    page = document[source_index]
                    try:
                        points = page.get_size()
                        if min(points) <= 0:
                            raise ValueError("This page has invalid dimensions.")
                        scale = min(260 / points[0], 340 / points[1])
                        bitmap = page.render(scale=scale, fill_color=(255, 255, 255, 255),
                                             no_smoothimage=not smooth_images)
                        try:
                            image = bitmap.to_pil()
                            pixels = pixels_from_pil(image, (260, 340))
                        finally:
                            bitmap.close()
                    finally:
                        page.close()
                    self.ready.emit(generation, count, points, pixels)
            finally:
                document.close()
        except Exception as exc:
            self.failed.emit(str(exc))
