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
