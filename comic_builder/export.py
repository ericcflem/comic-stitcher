"""Exporting a reviewed comic: output options, page names, encoding, size estimates,
the lossless WebP suggestion, and writing and publishing the archive or page folders."""
from __future__ import annotations

import os
import errno
import logging
import shutil
import threading
import uuid
import zipfile
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field, replace
from multiprocessing import get_context
from pathlib import Path
from typing import Callable
import py7zr
import numpy as np
from PIL import Image, ImageFile, ImageOps
from io import BytesIO
from .sharp_webp import OVERFLOW_MIN_METHOD, PARTITION0_OVERFLOW, save_sharp_webp, sharp_yuv_available
from .pdf_export import PDF_IMAGE_SUFFIXES, verify_pdf, write_pdf
from .backend import (BookPlan, Page, _working_colour, check_sources, encoding_workers,
    rotated_image, safe_book_name, stitched_image)


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
    save_cover: bool = False  # Also save the front cover as an image beside the comic.
    save_comicinfo: bool = False  # Also save ComicInfo.xml beside the archive.
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
    already-started processes. Measures the archive, or the folder of pages when
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
            fmt = _entry_format(plan, opt, encoding_opt, left, right, reduce)
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


def _images_label(opt: ExportOptions, variant: str) -> str:
    """How a folder of finished pages is named after its format."""
    if opt.keep_originals and variant == "full":
        return "Pages"  # Unchanged pages copied as they are, joined spreads encoded.
    return {"jpeg": "JPEG", "webp": "WebP", "png": "PNG"}[copy_encoding_options(opt, variant == "reduced").format] + " pages"


def keeps_png(opt: ExportOptions, variant: str) -> bool:
    """Whether a separate folder of lossless PNG pages is made for this size:
    not when, with no archive, the pages are already being saved as PNG."""
    return opt.keep_images and (opt.cbz or _images_label(opt, variant) != "PNG pages")


def saves_comicinfo(opt: ExportOptions) -> bool:
    """ComicInfo.xml beside the archive: asked for, included, and not already in
    a folder of pages (no archive)."""
    return opt.save_comicinfo and opt.comicinfo_xml is not None and opt.cbz


def _cover_name(plan: BookPlan, opt: ExportOptions, variant: str) -> str | None:
    """The finished comic's front-cover file, as named in this size's export."""
    from .comicinfo import front_cover_output
    cover = front_cover_output(plan, export_entries(plan, opt))
    return None if cover is None else planned_names(plan, opt, reduced=variant == "reduced")[cover]


def export_outputs(plan: BookPlan, folder: Path, title: str, opt: ExportOptions) -> list[tuple[str, str, Path]]:
    """Every (size, kind, path) an export makes: the archive, or with no archive a
    folder of the finished pages in the chosen format; plus, when asked, a folder
    of lossless PNG pages, the front cover as an image, and ComicInfo.xml."""
    variants = ("full", "reduced") if opt.size_output == "both" else (opt.size_output,)
    outputs = []
    for variant in variants:
        suffix = f" - {'Full' if variant == 'full' else 'Reduced'}" if opt.size_output == "both" else ""
        if opt.cbz:
            outputs.append((variant, "archive", folder / f"{title}{suffix}.{opt.archive_format}"))
        else:
            outputs.append((variant, "images", folder / f"{title}{suffix} - {_images_label(opt, variant)}"))
        if keeps_png(opt, variant):
            outputs.append((variant, "png", folder / f"{title}{suffix} - PNG pages"))
        if opt.save_cover and (cover := _cover_name(plan, opt, variant)) is not None:
            outputs.append((variant, "cover", folder / f"{title}{suffix} - Cover{Path(cover).suffix}"))
        if saves_comicinfo(opt):
            outputs.append((variant, "comicinfo", folder / f"{title}{suffix} - ComicInfo.xml"))
    return outputs


def export_book(plan: BookPlan, opt: ExportOptions,
                progress: Callable[[int, int], None] | None = None) -> Path | tuple[Path, Path]:
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
    outputs = export_outputs(plan, folder, title, opt)

    def destination(variant: str, kind: str | None = None) -> Path:
        return next(path for size, what, path in outputs
                    if size == variant and (what == kind if kind else what in ("archive", "images")))

    for _variant, _kind, output in outputs:
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
            keep_png = keeps_png(opt, variant)
            if keep_png:
                png_work.mkdir()
            jobs = []
            for (left, right), name in zip(entries, variant_names, strict=True):
                fmt = _entry_format(plan, opt, encoding_opt, left, right, reduce)
                jobs.append((*_entry_sources(plan, left, right), variant_work / name, fmt,
                             *_entry_rotations(plan, left), _entry_overlap(plan, left)))
            encode_count = sum(fmt is not None or keep_png for _, _, _, fmt, _, _, _ in jobs)
            if worker_count == 1 or encode_count <= 1:
                for source, right, dest, fmt, rotation, page_rotations, overlap in jobs:
                    if fmt is None and not keep_png:
                        shutil.copyfile(source, dest)
                    else:
                        _encode_entry(str(source), str(right) if right else None, str(dest), fmt, encoding_opt, reduce, rotation,
                                      str(png_work / (dest.stem + ".png")) if keep_png else None, page_rotations,
                                      overlap)
                    done()
            else:
                # Spawn from the GUI worker; cap queued image jobs for large books.
                with ProcessPoolExecutor(max_workers=min(worker_count, encode_count),
                                         mp_context=get_context("spawn")) as executor:
                    pending = set()
                    try:
                        for source, right, dest, fmt, rotation, page_rotations, overlap in jobs:
                            if fmt is None and not keep_png:
                                shutil.copyfile(source, dest)
                                done()
                            else:
                                pending.add(executor.submit(_encode_entry, str(source),
                                                            str(right) if right else None,
                                                            str(dest), fmt, encoding_opt, reduce, rotation,
                                                            str(png_work / (dest.stem + ".png")) if keep_png else None, page_rotations,
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
            if opt.save_cover and (cover := _cover_name(plan, opt, variant)) is not None:
                # The cover exactly as it is in the comic.
                shutil.copyfile(variant_work / cover, work / f"{variant}-cover{Path(cover).suffix}")
            archive_names = list(variant_names)
            if opt.comicinfo_xml is not None:
                from .comicinfo import export_xml
                data = export_xml(opt.comicinfo_xml, plan, entries, [variant_work / name for name in variant_names])
                if not opt.cbz or opt.archive_format != "pdf":  # A PDF has no place for it.
                    (variant_work / "ComicInfo.xml").write_bytes(data)
                    archive_names.append("ComicInfo.xml")
                if saves_comicinfo(opt):
                    (work / f"{variant}-ComicInfo.xml").write_bytes(data)
                if keep_png:
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
            out = destination(variant)
            if opt.cbz:
                publish_archive(work / f"{variant}.{opt.archive_format}", out)
            else:
                (work / variant).rename(out)  # The finished pages, in the chosen format.
            published.append(out)
            if keeps_png(opt, variant):
                out = destination(variant, "png")
                (work / f"{variant}-png").rename(out)
                published.append(out)
            for kind, temporary in (("cover", None), ("comicinfo", work / f"{variant}-ComicInfo.xml")):
                out = next((path for size, what, path in outputs if size == variant and what == kind), None)
                if out is None:
                    continue
                (temporary or work / f"{variant}-cover{out.suffix}").rename(out)
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
