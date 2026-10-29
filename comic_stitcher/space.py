"""Free disk space, checked before an import or export starts.

A PDF rendered at 600 dpi or a long book can need gigabytes: imported pages go
to the system's temporary folder (usually drive C:), and an export builds its
pages and archive in a working folder beside where the comic is saved. Running
out partway through is slow to find out and, on drive C:, slows the whole
computer, so the space needed is estimated first and compared with what's
free. The estimates err a little high; they only decide whether to warn.
"""
from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path

# Bytes per pixel of a saved page, roughly, by format (comic art; PNG of a scan
# is larger than of flat colour, so this leans high).
BYTES_PER_PIXEL = {"png": 2.0, "jpeg": 0.5, "webp": 0.4, "webp_lossless": 1.2}
HEADROOM = 1.1                   # A tenth more than the estimate...
RESERVE = 300 * 1024 * 1024      # ...and some room left for everything else.


def free_space(path: Path) -> int | None:
    """Free bytes on the drive holding `path` (or its nearest existing folder)."""
    path = Path(path)
    for candidate in (path, *path.parents):
        if candidate.exists():
            try:
                return shutil.disk_usage(candidate).free
            except OSError:
                return None
    return None


def drive_name(path: Path) -> str:
    anchor = Path(path).anchor
    if os.name == "nt" and anchor and not anchor.startswith("\\\\"):
        return f"drive {anchor.rstrip(chr(92))}"
    return f"the drive holding {path}"


def shortfall(path: Path, need: int) -> int | None:
    """The free space, when it's less than `need` allows for; else None."""
    free = free_space(path)
    if free is None or free >= need * HEADROOM + RESERVE:
        return None
    return free


def import_need(source: Path, dpi: int = 300, extract_jpegs: bool = False) -> int:
    """Bytes the imported pages of a PDF or archive take in the temporary folder."""
    source = Path(source)
    suffix = source.suffix.lower()
    try:
        size = source.stat().st_size
    except OSError:
        return 0
    if suffix == ".pdf" and not extract_jpegs:
        try:
            import pypdfium2 as pdfium
            document = pdfium.PdfDocument(str(source))
            try:
                pixels = sum((page.get_width() * dpi / 72) * (page.get_height() * dpi / 72)
                             for page in (document[i] for i in range(len(document))))
            finally:
                document.close()
            return int(pixels * BYTES_PER_PIXEL["png"])
        except Exception:  # A PDF the importer will report on itself.
            return size * 4
    if suffix in (".cbz", ".zip"):
        try:
            with zipfile.ZipFile(source) as archive:
                return sum(info.file_size for info in archive.infolist())
        except (OSError, zipfile.BadZipFile):
            return size
    return int(size * 1.05)  # PDF JPEGs, CB7 and CBR: pages are stored about as they come.


def export_need(plan, opt) -> int:
    """Bytes an export takes where it's saved, at its peak: each copy's pages,
    plus the archive made from them, plus any lossless PNG copies."""
    from .export import _entry_format, _entry_size, copy_encoding_options, export_entries
    variants = ("full", "reduced") if opt.size_output == "both" else (opt.size_output,)
    total = 0
    for variant in variants:
        reduce = variant == "reduced"
        encoding_opt = copy_encoding_options(opt, reduce)
        pages = png = 0
        for left, right in export_entries(plan, opt):
            width, height = _entry_size(plan, opt, left, right, reduce)
            fmt = _entry_format(plan, opt, encoding_opt, left, right, reduce)
            if fmt is None:
                pages += plan.scan.pages[left].size
            else:
                key = "webp_lossless" if fmt == "webp" and encoding_opt.webp_lossless else fmt
                pages += width * height * BYTES_PER_PIXEL[key]
            if opt.keep_images:
                png += width * height * BYTES_PER_PIXEL["png"]
        total += pages * (2 if opt.cbz else 1) + png
    return int(total)
