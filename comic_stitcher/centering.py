"""Centering art that print margins push to one side of the page.

Printed comics keep a wider margin on the side of the page that goes into the
binding, so the art sits left on one page and right on the next. On a white or
black page, the art can be moved sideways to the middle and the strip it
uncovers filled with the page colour; the page keeps its size.

Each page is measured on a small preview (about 850 pixels wide):

* The background colour comes from the page edges. Pages are left alone when
  art reaches the edges (full bleed), on a coloured background, blank, holding
  only a small illustration, already centered, or wider than tall (a spread).
* An optional header and footer strip (a page number, say) is split from the
  art at the plain gaps between them. One that is already centered stays where
  it is; one that isn't moves with the art.
* The art's width is measured from its outermost panel sides when it has them:
  straight vertical edges where the background meets a panel. Lettering or art
  reaching a little past them (a sound effect) isn't measured, but moves with
  the art. Without panel sides it is measured from its ink, ignoring specks and
  smudges in the margins.
* The art is never moved off the page.

Worked out with Eric Fleming's center_comic.py script, on Fire Power and other
books. Most pages come out well centered, but some can't be measured exactly.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

PREVIEW_WIDTH = 850          # Pixels across the measuring preview.
BG_TOL = 40                  # Gray-level difference that counts as "not background".
EDGE_BG_MIN = 0.985          # Share of edge pixels that must be background (else full bleed).
MIN_ART_WIDTH = 0.55         # Art narrower than this share of the page: a spot illustration.
MIN_SHIFT = 0.005            # Shifts smaller than this share of the width are left alone (a
                             # 175 px gutter against 135 moves the art 20 px: 1% of a 2000 px page).
GAP_MIN = 0.005              # A plain gap (share of height) separating a header or footer from the art.
BAND_ZONE = 0.08             # A header or footer starts or ends within this share of the top or bottom.
BAND_MAX_H = 0.03            # A header or footer strip is at most this share of the height.
CENTER_TOL = 0.006           # A strip within this share of the width of the middle is centered.
SPREAD_RATIO = 1.05          # With nothing to compare: pages this much wider than tall are spreads.
SPREAD_WIDTH = 1.6           # In a book: pages this much wider than its usual page are spreads
                             # (so a landscape book, such as a strip collection, is still measured).
SPECK_RUN = 0.008            # A column holds art only with an ink run this share of the height tall.
FRAME_MIN_RUN = 0.12         # A panel side: a straight vertical edge this share of the art's height.
PROTRUDE_MAX = 0.08          # Art past the outermost panel sides, up to this share of the width.


def _ink_mask(gray, bg):
    """Pixels that differ from the background, less lone single-pixel specks."""
    mask = np.abs(gray - bg) > BG_TOL
    padded = np.pad(mask, 1)
    neighbours = sum(np.roll(np.roll(padded, dy, 0), dx, 1)
                     for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx)[1:-1, 1:-1]
    return mask & (neighbours > 0)


def _row_segments(mask, gap):
    rows = np.where(mask.sum(axis=1) >= 2)[0]  # A row needs two or more ink pixels.
    if len(rows) == 0:
        return []
    segments, start, previous = [], rows[0], rows[0]
    for row in rows[1:]:
        if row - previous > gap:
            segments.append((int(start), int(previous) + 1))
            start = row
        previous = row
    segments.append((int(start), int(previous) + 1))
    return segments


def _col_extent(mask, y0, y1):
    cols = np.where(mask[int(y0):int(y1)].sum(axis=0) >= 1)[0]
    return (int(cols[0]), int(cols[-1]) + 1) if len(cols) else None


def _longest_runs(mask):
    """The longest vertical run of True in each column."""
    best = np.zeros(mask.shape[1], int)
    current = np.zeros(mask.shape[1], int)
    for row in mask:
        current = np.where(row, current + 1, 0)
        np.maximum(best, current, out=best)
    return best


def _art_extent(mask, y0, y1, h):
    """Columns holding art: an ink run at least SPECK_RUN of the height tall."""
    cols = np.where(_longest_runs(mask[int(y0):int(y1)]) >= max(4, int(SPECK_RUN * h)))[0]
    return (int(cols[0]), int(cols[-1]) + 1) if len(cols) else None


def _edge_runs(art):
    """For each column, the longest vertical run of rows where art starts there
    (background to its left) and where it ends there (background to its right).
    A panel side makes a long run; ragged lettering doesn't. A pixel of wobble
    is allowed, as a scanned or slightly skewed edge has."""
    starts = np.zeros_like(art)
    ends = np.zeros_like(art)
    starts[:, 2:] = ~art[:, :-2] & ~art[:, 1:-1] & art[:, 2:]
    ends[:, :-2] = art[:, :-2] & ~art[:, 1:-1] & ~art[:, 2:]

    def wobble(mask):
        out = mask.copy()
        out[:, 1:] |= mask[:, :-1]
        out[:, :-1] |= mask[:, 1:]
        return out
    return _longest_runs(wobble(starts)), _longest_runs(wobble(ends))


def _frame_extent(gray, bg, y0, y1, w, ink):
    """From the leftmost panel's left side to the rightmost panel's right side,
    when the art's outer edges are panel sides with at most a narrow protrusion
    past them; else None."""
    art = np.abs(gray[int(y0):int(y1)] - bg) > BG_TOL
    starts, ends = _edge_runs(art)
    tall = FRAME_MIN_RUN * (y1 - y0)
    left, right = np.where(starts >= tall)[0], np.where(ends >= tall)[0]
    if not len(left) or not len(right):
        return None
    fx0, fx1 = max(int(left[0]), ink[0]), min(int(right[-1]) + 1, ink[1])  # Wobble can overshoot a pixel.
    if fx1 - fx0 < MIN_ART_WIDTH * w:
        return None
    if fx0 - ink[0] <= PROTRUDE_MAX * w and ink[1] - fx1 <= PROTRUDE_MAX * w:
        return fx0, fx1
    return None


def analyze(preview: Image.Image) -> dict:
    """What to do with one page, from its preview, in preview pixels: "skip"
    (why it is left alone), or "shift" (+ moves right) and "moving" (the rows
    that move; rows outside it, a centered header or footer, stay)."""
    rgb = np.asarray(preview.convert("RGB")).astype(np.int16)
    gray = rgb.mean(axis=2)
    h, w = gray.shape
    result = {"w": w, "h": h}
    b = 3
    edge = np.concatenate([gray[:b].ravel(), gray[-b:].ravel(), gray[:, :b].ravel(), gray[:, -b:].ravel()])
    bg = float(np.median(edge))
    if float(np.mean(np.abs(edge - bg) < 25)) < EDGE_BG_MIN:
        return {**result, "skip": "art to the edges"}
    if not (bg > 210 or bg < 45):  # White or black, give or take paper and scanning.
        return {**result, "skip": "colored background"}
    mask = _ink_mask(gray, bg)
    segments = _row_segments(mask, max(2, int(GAP_MIN * h)))
    if not segments:
        return {**result, "skip": "blank"}
    header = footer = None
    if len(segments) >= 2:
        y0, y1 = segments[0]
        if y1 <= BAND_ZONE * h and (y1 - y0) <= BAND_MAX_H * h:
            header = (y0, y1)
        y0, y1 = segments[-1]
        if y0 >= (1 - BAND_ZONE) * h and (y1 - y0) <= BAND_MAX_H * h:
            footer = (y0, y1)
    art = segments[(1 if header else 0):(len(segments) - 1 if footer else len(segments))]
    if not art:
        return {**result, "skip": "only a header or footer"}
    ay0, ay1 = art[0][0], art[-1][1]
    ink = _art_extent(mask, ay0, ay1, h)
    if not ink:
        return {**result, "skip": "no art found"}
    frames = _frame_extent(gray, bg, ay0, ay1, w, ink)
    x0, x1 = frames or ink
    if (x1 - x0) < MIN_ART_WIDTH * w:
        return {**result, "skip": "spot illustration"}
    shift = (w - x1 - x0) / 2.0
    # Never off the page: lettering past a panel goes at most to the edge, and
    # lettering already cut off at an edge isn't moved further off it.
    room = (min(0.0, 1.0 - ink[0]), max(0.0, w - 1.0 - ink[1]))
    shift = min(shift, room[1]) if shift > 0 else max(shift, room[0])
    if abs(shift) < MIN_SHIFT * w:
        return {**result, "skip": "already centered"}
    top, bottom = 0.0, float(h)
    for band, is_header in ((header, True), (footer, False)):
        if not band:
            continue
        bx0, bx1 = _col_extent(mask, *band)
        if abs((bx0 + bx1) / 2 - w / 2) <= CENTER_TOL * w:  # Centered: it stays.
            if is_header:
                top = (band[1] + ay0) / 2
            else:
                bottom = (ay1 + band[0]) / 2
    return {**result, "shift": shift, "moving": (top, bottom), "frames": bool(frames)}


def spread_width(sizes) -> float | None:
    """The width above which a page of this book is a spread: well over its
    usual page width. None for a book too small to tell."""
    widths = sorted(width for width, _ in sizes)
    return SPREAD_WIDTH * widths[len(widths) // 2] if len(widths) >= 3 else None


def measure_page(path: str, spread_from: float | None = None) -> tuple[int, int, int] | None:
    """(shift in pixels, first row, end row) to center one page's art, at its
    own size, or None to leave it."""
    found = check_page(path, spread_from)
    return found if isinstance(found, tuple) else None


def check_page(path: str, spread_from: float | None = None) -> tuple[int, int, int] | str:
    """measure_page's answer, or why the page is left alone. A page wider than
    `spread_from` (see spread_width), or without it wider than tall, is a
    spread. Top level, to run in another process."""
    with Image.open(path) as image:
        if image.getexif().get(0x0112, 1) not in (1, None):
            return "turned by its EXIF orientation"
        if (image.width > spread_from) if spread_from else (image.width > image.height * SPREAD_RATIO):
            return "spread"
        width, height = image.size
        image.draft("RGB", (PREVIEW_WIDTH, round(height * PREVIEW_WIDTH / width)))
        source = image.convert("RGB") if image.mode not in ("RGB", "L") else image
        preview = source.resize((PREVIEW_WIDTH, max(1, round(height * PREVIEW_WIDTH / width))), Image.BOX)
    found = analyze(preview)
    if "skip" in found:
        return found["skip"]
    scale_x, scale_y = width / found["w"], height / found["h"]
    shift = round(found["shift"] * scale_x)
    if not shift:
        return "already centered"
    top, bottom = found["moving"]
    return shift, round(top * scale_y), min(height, round(bottom * scale_y))


def border_fill(image: Image.Image):
    """The most common colour along the image's edges, in its own mode."""
    pixels = np.asarray(image)
    b = 3
    if pixels.ndim == 2:
        edge = np.concatenate([pixels[:b].ravel(), pixels[-b:].ravel(), pixels[:, :b].ravel(), pixels[:, -b:].ravel()])
        values, counts = np.unique(edge, return_counts=True)
        value = values[counts.argmax()]
        return bool(value) * 255 if image.mode == "1" else int(value)
    depth = pixels.shape[2]
    edge = np.concatenate([pixels[:b].reshape(-1, depth), pixels[-b:].reshape(-1, depth),
                           pixels[:, :b].reshape(-1, depth), pixels[:, -b:].reshape(-1, depth)])
    values, counts = np.unique(edge, axis=0, return_counts=True)
    return tuple(int(v) for v in values[counts.argmax()])


def centered_image(image: Image.Image, centering: tuple[int, int, int]) -> Image.Image:
    """A copy of the page with its art moved sideways by the measured shift;
    the strip it uncovers is filled with the page colour."""
    shift, top, bottom = centering
    result = image.copy()
    band = image.crop((0, top, image.width, bottom))
    result.paste(border_fill(image), (0, top, image.width, bottom))
    result.paste(band, (shift, top))
    band.close()
    return result


def open_centered(path: Path, centering: tuple[int, int, int] | None) -> Image.Image:
    """A page as it will be in the comic: EXIF orientation applied, then centered."""
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source)
        image.load()
    if centering:
        moved = centered_image(image, centering)
        image.close()
        image = moved
    return image
