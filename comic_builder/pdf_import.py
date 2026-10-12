"""Render PDF pages to temporary PNGs in page order for the existing scanner."""
from __future__ import annotations

import os
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from multiprocessing import get_context
from pathlib import Path

import pypdfium2 as pdfium

from .backend import scan_folder

MAX_PAGES = 10_000
MAX_PAGE_PIXELS = 60_000_000
MAX_PAGE_SIDE = 20_000
CHUNK_PAGES = 8


def rendered_dimensions(page_size: tuple[float, float], dpi: int) -> tuple[int, int]:
    """Match PDFium's 72-point-per-inch rasterization for a chosen page."""
    return tuple(round(points * dpi / 72) for points in page_size)


# Resolutions scanners and publishers commonly use; a measured value close to
# one of these is shown as that value.
COMMON_DPI = (72, 96, 100, 120, 150, 200, 240, 250, 300, 350, 400, 450, 500, 600)
SAMPLE_PAGES = 12


def _image_resolutions(page):
    """(dpi, share of the page covered) for each image drawn on a page.

    An image's resolution is its pixel size divided by the size it is drawn at.
    Images inside form objects are measured through the form's own scaling.
    """
    import math
    import pypdfium2.raw as pdfium_c
    page_width, page_height = page.get_size()
    page_area = page_width * page_height
    if page_area <= 0:
        return []
    matrices = {}
    found = []
    for obj in page.get_objects(filter=(pdfium_c.FPDF_PAGEOBJ_IMAGE, pdfium_c.FPDF_PAGEOBJ_FORM),
                                max_depth=6):
        level = getattr(obj, "level", 0)
        matrix = obj.get_matrix()
        if level > 0 and level - 1 in matrices:
            matrix = matrix.multiply(matrices[level - 1])
        if obj.type == pdfium_c.FPDF_PAGEOBJ_FORM:
            matrices[level] = matrix
            continue
        width_px, height_px = obj.get_px_size()
        drawn_width = math.hypot(matrix.a, matrix.b)
        drawn_height = math.hypot(matrix.c, matrix.d)
        if min(width_px, height_px, drawn_width, drawn_height) <= 0:
            continue
        dpi = min(width_px / drawn_width, height_px / drawn_height) * 72
        corners = [matrix.on_point(x, y) for x, y in ((0, 0), (1, 0), (0, 1), (1, 1))]
        xs, ys = [x for x, _ in corners], [y for _, y in corners]
        covered = (max(0, min(max(xs), page_width) - max(min(xs), 0))
                   * max(0, min(max(ys), page_height) - max(min(ys), 0)))
        if covered > 0:
            found.append((dpi, covered / page_area))
    return found


def suggest_pdf_dpi(source: Path, sample_pages: int = SAMPLE_PAGES) -> int | None:
    """The resolution of the page art in a PDF, or None if it has little.

    Samples pages across the book and weighs each image by how much of the page
    it covers, so logos and small decorations barely count, while pages cut into
    thin strips still count in full. Returns None when images cover less than
    half of the sampled pages, as in a PDF made mostly of text and drawings.
    """
    document = pdfium.PdfDocument(source)
    try:
        count = len(document)
        if not count:
            return None
        picks = sorted({round(i * (count - 1) / max(1, sample_pages - 1))
                        for i in range(min(count, sample_pages))})
        found = []
        for index in picks:
            page = document[index]
            try:
                found.extend(_image_resolutions(page))
            finally:
                page.close()
    finally:
        document.close()
    total = sum(share for _, share in found)
    if total < 0.5 * len(picks):
        return None
    # The resolution covering the most page area (weighted median).
    running, dpi = 0.0, 0.0
    for measured, share in sorted(found):
        running += share
        dpi = measured
        if running >= total / 2:
            break
    nearest = min(COMMON_DPI, key=lambda common: abs(common - dpi))
    value = nearest if abs(nearest - dpi) <= 0.03 * nearest else round(dpi)
    return max(72, min(600, value))


def pdf_workers(requested: int = 0) -> int:
    """Keep concurrent full-page bitmaps within a practical memory budget."""
    return requested or min(4, max(1, (os.cpu_count() or 1) // 2))


def _render_chunk(source: str, target_folder: str, first: int, last: int,
                  dpi: int, digits: int, smooth_images: bool) -> int:
    """Runs in an independent process; PDFium must not render on several threads."""
    document = pdfium.PdfDocument(source)
    try:
        for index in range(first, last):
            page = document[index]
            try:
                width, height = page.get_size()
                scaled_width, scaled_height = rendered_dimensions((width, height), dpi)
                if (min(scaled_width, scaled_height) < 1 or
                        max(scaled_width, scaled_height) > MAX_PAGE_SIDE or
                        scaled_width * scaled_height > MAX_PAGE_PIXELS):
                    raise ValueError(f"PDF page {index + 1} would render too large at {dpi} dpi. "
                                     "Choose a lower resolution.")
                bitmap = page.render(scale=dpi / 72, fill_color=(255, 255, 255, 255),
                                     no_smoothimage=not smooth_images)
                try:
                    image = bitmap.to_pil().convert("RGB")
                    try:
                        image.save(Path(target_folder) / f"{index + 1:0{digits}d}.png",
                                   "PNG", compress_level=1)
                    finally:
                        image.close()
                finally:
                    bitmap.close()
            except Exception as exc:
                raise ValueError(f"Could not render PDF page {index + 1}: {exc}") from exc
            finally:
                page.close()
    finally:
        document.close()
    return last - first


def import_pdf(source: Path, destination: Path, dpi: int = 300,
               workers: int = 0, smooth_images: bool = True,
               manual: bool = False, progress=None, *, right_to_left=False, extract_jpegs=False):
    """Rasterize with bounded processes, inspect pages, and return output next to PDF."""
    source = source.expanduser().resolve(strict=True)
    if extract_jpegs:
        from .pdf_jpegs import import_pdf_jpegs
        return import_pdf_jpegs(source, destination, manual=manual, progress=progress,
                               right_to_left=right_to_left)
    if source.suffix.lower() != ".pdf" or not source.is_file():
        raise ValueError("Choose a PDF file.")
    if not 72 <= dpi <= 600 or not 0 <= workers <= 8:
        raise ValueError("Choose 72–600 dpi and up to eight rendering processes.")
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError("The temporary import folder is not empty.")
    try:
        document = pdfium.PdfDocument(source)
        try:
            count = len(document)
        finally:
            document.close()
    except Exception as exc:
        raise ValueError(f"Cannot open this PDF: {exc}") from exc
    if not 1 <= count <= MAX_PAGES:
        raise ValueError("The PDF must contain between 1 and 10,000 pages.")
    digits = max(4, len(str(count)))
    jobs = [(i, min(i + CHUNK_PAGES, count)) for i in range(0, count, CHUNK_PAGES)]
    parallel = min(pdf_workers(workers), len(jobs))
    completed = 0
    total_steps = 2 if manual else 3
    if progress:
        progress(0, count * total_steps)
    # Small documents avoid the cost of starting worker processes. Larger ones
    # launch a bounded number of tasks; no page bitmaps cross process boundaries.
    if parallel == 1:
        for first, last in jobs:
            completed += _render_chunk(str(source), str(destination), first, last,
                                       dpi, digits, smooth_images)
            if progress:
                progress(completed, count * total_steps)
    else:
        with ProcessPoolExecutor(max_workers=parallel, mp_context=get_context("spawn")) as pool:
            pending = set()
            job_iter = iter(jobs)
            def submit_next():
                try:
                    first, last = next(job_iter)
                except StopIteration:
                    return False
                pending.add(pool.submit(_render_chunk, str(source), str(destination),
                                        first, last, dpi, digits, smooth_images))
                return True

            for _ in range(parallel * 2):
                if not submit_next():
                    break
            while pending:
                finished, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in finished:
                    try:
                        completed += future.result()
                    except Exception:
                        for remaining in pending:
                            remaining.cancel()
                        raise
                    if progress:
                        progress(completed, count * total_steps)
                    submit_next()
    expected = [destination / f"{i:0{digits}d}.png" for i in range(1, count + 1)]
    if not all(path.is_file() and path.stat().st_size for path in expected):
        raise ValueError("PDF rendering did not produce every page; temporary images were preserved for inspection.")
    scan = scan_folder(destination,
                       progress=(lambda n, total: progress(count + n, count * total_steps))
                       if progress else None, manual=manual, right_to_left=right_to_left)
    scan.output_folder = source.parent
    scan.source_archive = source  # Also used for non-archive file imports.
    return scan
