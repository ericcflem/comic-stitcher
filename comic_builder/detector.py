"""Page edge classification and seam scoring used to find split two-page spreads."""
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
import numpy as np
from PIL import Image, ImageOps

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}

def natural_key(path: Path):
    parts = re.split(r"(\d+)", path.name.lower())
    return [int(p) if p.isdigit() else p for p in parts]


def load_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        ImageOps.exif_transpose(im, in_place=True)
        return np.asarray(im.convert("RGB"), dtype=np.uint8)


def load_edge_rgb(path: Path, side: str, band: int = 24) -> np.ndarray:
    """Decode an edge for scoring without allocating a full RGB NumPy page."""
    with Image.open(path) as im:
        ImageOps.exif_transpose(im, in_place=True)
        width = min(im.width, max(8 * band, 2))
        x = 0 if side == "left" else im.width - width
        return np.asarray(im.crop((x, 0, x + width, im.height)).convert("RGB"), dtype=np.uint8)


def strip_stats(arr: np.ndarray, side: str, width: int) -> dict:
    """Uniformity stats for a vertical edge strip."""
    h, w = arr.shape[:2]
    width = max(1, min(width, w // 4))
    strip = arr[:, :width] if side == "left" else arr[:, w - width :]
    return _strip_metrics(strip, width)


def _strip_metrics(strip: np.ndarray, width: int) -> dict:
    # Per-pixel mean color, then variation across the strip
    flat = strip.reshape(-1, 3).astype(np.float32)
    std = float(flat.std())
    # How much the strip changes from top to bottom (true border is stable)
    row_means = strip.mean(axis=1).astype(np.float32)  # (h, 3)
    vertical_std = float(row_means.std())
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


def seam_score(left: np.ndarray, right: np.ndarray, band: int = 24) -> dict:
    """
    How well the right edge of `left` continues into the left edge of `right`.

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
        left = _strip_metrics(np.asarray(im.crop((0, 0, width, h)).convert("RGB"), dtype=np.uint8), width)
        right = _strip_metrics(np.asarray(im.crop((w - width, 0, w, h)).convert("RGB"), dtype=np.uint8), width)
    left_solid = is_solid_border(left, std_thresh, vert_thresh)
    right_solid = is_solid_border(right, std_thresh, vert_thresh)

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
