"""Page edge classification and seam scoring used to find split two-page spreads."""
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
import numpy as np
from PIL import Image, ImageOps

from .colour import standard_mode

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}

def _natural(text: str) -> list:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", text)]


def name_sort_key(name: str):
    """Sort file names the way Linux file managers and comic readers do.

    Numbers sort by value ("page 2" before "page 10") and capitalisation is
    ignored. Names that differ only in capitalisation ("001.jpg", "001.JPG")
    put lowercase first, as GNOME Files, KDE Dolphin and Ubuntu's Document
    Viewer do, so a book that reads correctly there keeps its order here.
    """
    return _natural(name.casefold()), _natural(name.swapcase())


def natural_key(path: Path):
    return name_sort_key(path.name)


def case_twin_groups(names: list[str]) -> list[list[int]]:
    """Indexes of names that are the same apart from capitalisation, in groups."""
    groups: dict[str, list[int]] = {}
    for index, name in enumerate(names):
        groups.setdefault(name.casefold(), []).append(index)
    return [group for group in groups.values() if len(group) > 1]


def load_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        ImageOps.exif_transpose(im, in_place=True)
        return np.asarray(standard_mode(im).convert("RGB"), dtype=np.uint8)


def load_edge_rgb(path: Path, side: str, band: int = 24, width: int | None = None) -> np.ndarray:
    """Decode an edge for scoring without allocating a full RGB NumPy page:
    8 bands' worth of columns, or `width` columns when given."""
    with Image.open(path) as im:
        ImageOps.exif_transpose(im, in_place=True)
        width = min(im.width, max(8 * band, 2) if width is None else max(width, 2))
        x = 0 if side == "left" else im.width - width
        return np.asarray(standard_mode(im.crop((x, 0, x + width, im.height))).convert("RGB"), dtype=np.uint8)


def strip_stats(arr: np.ndarray, side: str, width: int) -> dict:
    """Uniformity stats for a vertical edge strip."""
    h, w = arr.shape[:2]
    width = max(1, min(width, w // 4))
    strip = arr[:, :width] if side == "left" else arr[:, w - width :]
    stats = _strip_metrics(strip, width)
    stats["plain_px"] = _plain_edge_width(strip, side)
    return stats


def _strip_metrics(strip: np.ndarray, width: int) -> dict:
    # Variation across the strip, measured per colour channel. (Measuring all
    # channels together counted a flat teal or brown as varied, because its red,
    # green and blue values differ; only black, white and pure greys were solid.)
    flat = strip.reshape(-1, 3).astype(np.float32)
    std = float(flat.std(axis=0).max())
    # How much the strip changes from top to bottom (true border is stable)
    row_means = strip.mean(axis=1).astype(np.float32)  # (h, 3)
    vertical_std = float(row_means.std(axis=0).max())
    # Dominant color
    mean_color = flat.mean(axis=0).tolist()
    return {
        "std": std,
        "vertical_std": vertical_std,
        "mean_color": [round(c, 1) for c in mean_color],
        "width_used": width,
    }


def is_solid_border(stats: dict, std_thresh: float, vert_thresh: float) -> bool:
    """
    A border is a near-uniform strip. Allow a little scan noise, but reject
    strips that change a lot top-to-bottom (that's usually art, not a frame).
    """
    return stats["std"] <= std_thresh and stats["vertical_std"] <= vert_thresh


BORDER_MIN_PLAIN_PX = 4  # A plain strip this wide at the very edge is a border on its own.


def _plain_edge_width(strip: np.ndarray, side: str) -> int:
    """How many columns at the page edge are one flat colour from top to bottom.

    Catches borders narrower than the strip examined: a 6 px white frame with
    artwork just inside it is still a frame. Slicing debris is 1-3 px wide.
    """
    columns = strip if side == "left" else strip[:, ::-1]
    spread = columns.astype(np.float32).std(axis=0).max(axis=1)  # per column, worst channel
    plain = 0
    for value in spread:
        if value >= 3:
            break
        plain += 1
    return plain


# --- Seam scoring -----------------------------------------------------------
#
# A seam is judged two ways and the results are combined in seam_score():
#
# 1. Roughness ratio (the main test). Crossing a real seam should look no
#    rougher than moving the same distance inside either page. The outermost
#    columns are skipped when they are slicing debris (often a copy of the
#    neighbouring page's last column, or a blank strip), and lines may land a
#    little higher or lower on the far side so diagonals still count as
#    continuing. Rows with nothing in them near the edge are ignored. About 1
#    means "as smooth as the page itself"; false pairs usually score 1.5-7.
#
# 2. Pixel match (the previous method). Kept as a fallback because a very
#    high value catches designed spreads whose shapes do not line up exactly.
#
# Tuned on 1,042 pages from four books (84 real spreads). See CHANGELOG.

ROUGHNESS_WINDOW_ROWS = 48     # Rows compared together; windows overlap by half.
ROUGHNESS_MAX_SLOPE = 2.5      # Largest vertical drift (px per column) a line may have.
ROUGHNESS_ASSUMED_LOSS = 2     # Columns assumed lost when the publisher sliced the spread.
ROUGHNESS_MIN_DETAIL = 15.0    # Rows near the edge flatter than this carry no evidence.
ROUGHNESS_MIN_EVIDENCE = 0.05  # Share of the page height that must carry evidence.
ROUGHNESS_MAX_TRIM = 6         # Most debris columns skipped at an edge.
ROUGHNESS_LIKELY = 1.1         # Ratio at or below this maps to continuity 0.55 ("likely").
ROUGHNESS_POSSIBLE = 1.5       # Ratio at or below this maps to continuity 0.40 ("possible").
PIXEL_MATCH_TRUSTED = 0.85     # A pixel-match score this high is accepted on its own.
NO_EVIDENCE = 99.0             # Ratio reported when nothing crosses the seam.


def _debris_columns(edge: np.ndarray) -> int:
    """How many outermost columns to skip; ``edge`` has column 0 at the page edge."""
    gray = edge.mean(axis=2)
    jumps = np.abs(np.diff(gray[:, :14], axis=1)).mean(axis=0)  # jump between column k and k+1
    normal = max(float(np.median(jumps[6:14])), 1.0)
    skip = 0
    for k in range(ROUGHNESS_MAX_TRIM):
        if jumps[k] > 2.5 * normal + 2:
            skip = k + 1
    # A blank strip in front of detailed art is debris too, even when the art
    # is mostly white and the strip therefore makes no unusual jump.
    detail = edge[:, :14].std(axis=0).max(axis=1)
    if float(np.median(detail[6:14])) > 10:
        for k in range(ROUGHNESS_MAX_TRIM):
            if detail[k] >= 2:
                break
            skip = max(skip, k + 1)
    return skip + 1  # The very outermost column is never trusted.


def _best_shift_difference(p: np.ndarray, q: np.ndarray, max_shift: int, window: int) -> np.ndarray:
    """For each overlapping row window, the smallest mean difference between
    columns p and q when q may slide up or down by up to ``max_shift`` rows."""
    h = p.shape[0]
    step = window // 2
    count = (h - window) // step + 1
    starts = np.arange(count) * step
    best = np.full(count, np.inf)
    for s in range(-max_shift, max_shift + 1):
        d = np.full(h, 255.0)
        if s >= 0:
            d[:h - s] = np.abs(p[:h - s] - q[s:]).mean(axis=1)
        else:
            d[-s:] = np.abs(p[-s:] - q[:h + s]).mean(axis=1)
        c = np.concatenate([[0.0], np.cumsum(d)])
        best = np.minimum(best, (c[starts + window] - c[starts]) / window)
    return best


def _window_detail(col: np.ndarray, window: int) -> np.ndarray:
    """Largest per-channel standard deviation of each row window of one column."""
    h = col.shape[0]
    step = window // 2
    count = (h - window) // step + 1
    c1 = np.concatenate([np.zeros((1, 3)), np.cumsum(col, axis=0)])
    c2 = np.concatenate([np.zeros((1, 3)), np.cumsum(col * col, axis=0)])
    starts = np.arange(count) * step
    mean = (c1[starts + window] - c1[starts]) / window
    var = (c2[starts + window] - c2[starts]) / window - mean * mean
    return np.sqrt(np.maximum(var, 0)).max(axis=1)


def _roughness(left: np.ndarray, right: np.ndarray) -> tuple[float, int, int] | None:
    """(ratio, debris columns skipped on the left page, on the right page),
    or None when the images are too small to measure."""
    window = ROUGHNESS_WINDOW_ROWS
    h = min(left.shape[0], right.shape[0])
    if left.ndim != 3 or right.ndim != 3 or h < 4 * window or min(left.shape[1], right.shape[1]) < 40:
        return None
    a = left[:h, ::-1].astype(np.float64)   # Column 0 is the page edge on both sides.
    b = right[:h].astype(np.float64)
    ta, tb = _debris_columns(a), _debris_columns(b)
    p, q = a[:, ta], b[:, tb]
    gap = ta + tb + 1 + ROUGHNESS_ASSUMED_LOSS
    if ta + gap >= a.shape[1] or tb + gap >= b.shape[1]:
        return None
    shift = int(round(ROUGHNESS_MAX_SLOPE * gap))
    seam = _best_shift_difference(p, q, shift, window)
    inside = np.maximum(_best_shift_difference(p, a[:, ta + gap], shift, window),
                        _best_shift_difference(q, b[:, tb + gap], shift, window))
    detail = np.maximum(_window_detail(p, window), _window_detail(q, window))
    useful = detail > ROUGHNESS_MIN_DETAIL
    if useful.sum() < max(3, ROUGHNESS_MIN_EVIDENCE * useful.size):
        return NO_EVIDENCE, ta, tb
    return float(seam[useful].sum() / max(float(inside[useful].sum()), 1e-6)), ta, tb


def roughness_ratio(left: np.ndarray, right: np.ndarray) -> float | None:
    """Seam roughness relative to each page's own texture (lower is better).

    Returns None when the images are too small to measure, and NO_EVIDENCE
    when nothing near the seam can be compared.
    """
    result = _roughness(left, right)
    return None if result is None else result[0]


def _ratio_to_continuity(ratio: float) -> float:
    """Map a roughness ratio onto the 0-1 continuity scale (higher is better),
    so ROUGHNESS_LIKELY and ROUGHNESS_POSSIBLE land on the review cutoffs
    backend.LIKELY_MIN_SCORE (0.55) and POSSIBLE_MIN_SCORE (0.40)."""
    slope = (0.55 - 0.40) / (ROUGHNESS_POSSIBLE - ROUGHNESS_LIKELY)
    return float(min(1.0, max(0.0, 0.55 + slope * (ROUGHNESS_LIKELY - ratio))))


def seam_score(left: np.ndarray, right: np.ndarray, band: int = 24) -> dict:
    """
    How well the right edge of `left` continues into the left edge of `right`.

    ``continuity`` runs from 0 (bad) to 1 (excellent). It comes from the
    roughness ratio, except that a trusted pixel-match score is used when it is
    higher. Tiny images that cannot be measured use the pixel match alone.
    """
    score = _pixel_match_score(left, right, band)
    result = _roughness(left, right)
    if result is None:
        ratio, pixel = None, score["continuity"]
        continuity = pixel
    else:
        ratio, ta, tb = result
        # The fallback must not see slicing debris either: a copied edge
        # column would otherwise make two unrelated pages look identical.
        pixel = _pixel_match_score(left[:, :left.shape[1] - ta], right[:, tb:], band)["continuity"]
        continuity = _ratio_to_continuity(ratio)
        if pixel >= PIXEL_MATCH_TRUSTED:
            continuity = max(continuity, pixel)
    score["pixel_match"] = pixel
    score["roughness_ratio"] = None if ratio is None else round(ratio, 3)
    score["continuity"] = round(float(continuity), 3)
    return score


def _pixel_match_score(left: np.ndarray, right: np.ndarray, band: int = 24) -> dict:
    """
    The previous seam test: compares pixels at the same height across the join.

    Lower edge_mad and higher hist_corr => more likely a real split spread.
    """
    h = min(left.shape[0], right.shape[0])
    band = max(2, min(band, left.shape[1] // 8, right.shape[1] // 8))
    L = left[:h, -band:].astype(np.float32)
    R = right[:h, :band].astype(np.float32)

    # Column-to-column jump at the join
    edge_mad = float(np.mean(np.abs(L[:, -1] - R[:, 0])))

    # A few columns in from each side (less sensitive to a 1px crop mismatch)
    inner_mad = float(np.mean(np.abs(L[:, -3:].mean(axis=1) - R[:, :3].mean(axis=1))))

    def hist3(a: np.ndarray) -> np.ndarray:
        hs = []
        for c in range(3):
            hst, _ = np.histogram(a[:, :, c], bins=32, range=(0, 256), density=True)
            hs.append(hst)
        return np.concatenate(hs)

    hL, hR = hist3(L), hist3(R)
    denom = float(np.linalg.norm(hL) * np.linalg.norm(hR))
    hist_corr = float(np.dot(hL, hR) / denom) if denom else 0.0

    # Vertical gradient similarity at the join (art lines should line up).
    # Use a 1-D grayscale column so np.diff runs down the page, not across channels.
    def vgrad_col(rgb_img: np.ndarray, from_right: bool) -> np.ndarray:
        col = rgb_img[:, -1] if from_right else rgb_img[:, 0]  # (h, 3)
        gray = col.mean(axis=1)  # (h,)
        return np.abs(np.diff(gray))  # (h-1,)

    gL = vgrad_col(L, from_right=True)
    gR = vgrad_col(R, from_right=False)
    n = min(gL.size, gR.size)
    gL, gR = gL[:n], gR[:n]
    g_denom = float(np.linalg.norm(gL) * np.linalg.norm(gR))
    grad_corr = float(np.dot(gL, gR) / g_denom) if g_denom else 0.0

    # Combined: 0 = bad, 1 = excellent
    # edge_mad of 0 is perfect; 40+ is usually a hard mismatch
    mad_term = 1.0 / (1.0 + (edge_mad / 18.0))
    inner_term = 1.0 / (1.0 + (inner_mad / 22.0))
    continuity = 0.40 * mad_term + 0.25 * inner_term + 0.20 * hist_corr + 0.15 * max(grad_corr, 0.0)

    return {
        "edge_mad": round(edge_mad, 2),
        "inner_mad": round(inner_mad, 2),
        "hist_corr": round(hist_corr, 3),
        "grad_corr": round(grad_corr, 3),
        "continuity": round(float(continuity), 3),
    }


@dataclass
class PageInfo:
    path: str
    index: int
    width: int
    height: int
    left_solid: bool
    right_solid: bool
    left_stats: dict
    right_stats: dict
    role_hint: str


def classify_page(path: Path, index: int, border_px: int, std_thresh: float, vert_thresh: float) -> PageInfo:
    with Image.open(path) as im:
        ImageOps.exif_transpose(im, in_place=True)
        w, h = im.size
        width = max(1, min(border_px, w // 4))
        left_strip = np.asarray(standard_mode(im.crop((0, 0, width, h))).convert("RGB"), dtype=np.uint8)
        right_strip = np.asarray(standard_mode(im.crop((w - width, 0, w, h))).convert("RGB"), dtype=np.uint8)
    left, right = _strip_metrics(left_strip, width), _strip_metrics(right_strip, width)
    left["plain_px"] = _plain_edge_width(left_strip, "left")
    right["plain_px"] = _plain_edge_width(right_strip, "right")
    left_solid = (is_solid_border(left, std_thresh, vert_thresh)
                  or left["plain_px"] >= min(BORDER_MIN_PLAIN_PX, width))
    right_solid = (is_solid_border(right, std_thresh, vert_thresh)
                   or right["plain_px"] >= min(BORDER_MIN_PLAIN_PX, width))

    if left_solid and right_solid:
        hint = "single_framed"
    elif left_solid and not right_solid:
        hint = "possible_left_half_or_bleed_right"
    elif not left_solid and right_solid:
        hint = "possible_right_half_or_bleed_left"
    else:
        hint = "borderless"

    return PageInfo(
        path=str(path),
        index=index,
        width=w,
        height=h,
        left_solid=left_solid,
        right_solid=right_solid,
        left_stats=left,
        right_stats=right,
        role_hint=hint,
    )


def is_candidate_pair(a: PageInfo, b: PageInfo) -> bool:
    # Preserve tiny split/rounding differences, but exclude distinct page
    # geometries before scoring unrelated edges as a potential spread.
    if (abs(a.width - b.width) > max(2, round(min(a.width, b.width) * .005)) or
            abs(a.height - b.height) > max(2, round(min(a.height, b.height) * .002))):
        return False
    classic = (not a.right_solid) and (not b.left_solid)
    both_borderless = (
        not a.left_solid
        and not a.right_solid
        and not b.left_solid
        and not b.right_solid
    )
    return classic or both_borderless



def collect_images(folder: Path) -> list[Path]:
    return sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS), key=natural_key)
