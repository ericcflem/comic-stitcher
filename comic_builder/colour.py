"""Bring unusual page images to ordinary 8-bit colour before they are measured,
shown or encoded.

Two kinds of page need care, because Pillow's plain conversions get them wrong:

- 16-bit greyscale (some scanners' PNG and TIFF output) opens as mode "I;16".
  Converting that to "L" or "RGB" clips every value above 255, so anything
  lighter than nearly black turns white. These are scaled to 8 bits instead.
- CMYK (some publishers' scans and PDF images) is converted with its embedded
  colour profile when it has one, as the profile describes the printing inks;
  Pillow's built-in formula is only used without one. PNG can't hold CMYK.

Everything else is returned unchanged.
"""
from __future__ import annotations

from io import BytesIO

import numpy as np
from PIL import Image, ImageCms

SIXTEEN_BIT_GREY = {"I;16", "I;16L", "I;16B", "I;16N", "I"}


def _sixteen_bit_to_l(image: Image.Image) -> Image.Image:
    values = np.asarray(image, dtype=np.float64)
    if image.mode == "I" and values.size and values.max() <= 255:
        scaled = values  # 32-bit integers that only hold 8-bit values.
    else:
        scaled = values / 257.0  # 0-65535 onto 0-255, so white stays white.
    out = Image.fromarray(np.clip(np.rint(scaled), 0, 255).astype(np.uint8), "L")
    out.info.update(image.info)
    return out


def _cmyk_to_rgb(image: Image.Image) -> Image.Image:
    profile = image.info.get("icc_profile")
    if profile:
        try:
            srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
            out = ImageCms.profileToProfile(image, ImageCms.ImageCmsProfile(BytesIO(profile)), srgb,
                                            outputMode="RGB")
            if out is not None:
                out.info.update({key: value for key, value in image.info.items() if key != "icc_profile"})
                out.info["icc_profile"] = srgb.tobytes()
                return out
        except (ImageCms.PyCMSError, OSError, ValueError):
            pass  # A damaged or mismatched profile: fall back to the plain conversion.
    out = image.convert("RGB")
    out.info.pop("icc_profile", None)  # A CMYK profile doesn't describe the RGB result.
    return out


def standard_mode(image: Image.Image) -> Image.Image:
    """The image in an ordinary 8-bit mode; the same object when it already is one."""
    if image.mode in SIXTEEN_BIT_GREY:
        return _sixteen_bit_to_l(image)
    if image.mode == "CMYK":
        return _cmyk_to_rgb(image)
    return image
