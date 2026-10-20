"""WebP encoding with sharp RGB->YUV conversion, through libwebp's cwebp tool.

Lossy WebP stores colour at half resolution. libwebp's "sharp YUV" option
chooses that colour more carefully, which keeps edges between strong colours
(red lettering, blue skies against black ink) from bleeding or darkening. Pillow
doesn't offer the option, so these images go through cwebp, the encoder from the
same libwebp project. Without the option cwebp writes exactly the bytes Pillow
does for the same quality and effort, so the only change is the conversion.

Windows builds carry cwebp.exe beside ComicStitcher.exe. Elsewhere an installed
cwebp is used (the "webp" package on most Linux distributions).
"""
from __future__ import annotations

import functools
import io
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

from PIL import Image

MINIMUM_VERSION = (1, 0)  # Reading the image from standard input.
PARTITION0_OVERFLOW = 6  # libwebp's error code when a large, detailed page overflows its header.
# libwebp keeps the first part of a lossy WebP (block types and prediction
# modes) under 512 KB. Large, finely detailed pages (halftone screens, for
# example) can exceed it at the fastest effort levels, 0 to 2, which don't
# measure that part as they go. From effort 3 the encoder keeps it in bounds
# itself, so an overflowing page is encoded again at effort 3 (or the effort
# chosen, if higher). A partition limit, then a single segment, are further
# fallbacks: they let the encoder describe blocks more simply where needed.
OVERFLOW_MIN_METHOD = 3
OVERFLOW_FALLBACKS = ((), ("-partition_limit", "50"), ("-partition_limit", "100"),
                      ("-partition_limit", "100", "-segments", "1"))


class PartitionOverflow(RuntimeError):
    """The page is too large and detailed for lossy WebP even with every fallback."""


def _candidates() -> list[str]:
    beside_app = Path(sys.executable).resolve().parent
    source = Path(__file__).resolve().parent.parent
    names = ("cwebp.exe",) if os.name == "nt" else ("cwebp",)
    found = [str(folder / name) for folder in (beside_app, source, source / "vendor") for name in names]
    installed = shutil.which("cwebp")
    if installed:
        found.append(installed)
    return found


def _hidden_window() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


@functools.lru_cache(maxsize=1)
def cwebp_tool() -> str | None:
    """The cwebp to use, or None when there isn't one recent enough."""
    for candidate in _candidates():
        if not Path(candidate).is_file():
            continue
        try:
            output = subprocess.run([candidate, "-version"], capture_output=True, timeout=10,
                                    check=False, **_hidden_window()).stdout.decode(errors="replace")
        except (OSError, subprocess.SubprocessError):
            continue
        match = re.match(r"\s*(\d+)\.(\d+)", output)
        if match and tuple(map(int, match.groups())) >= MINIMUM_VERSION:
            return candidate
    return None


def sharp_yuv_available() -> bool:
    return cwebp_tool() is not None


def _input(image: Image.Image, windows: bool | None = None) -> bytes:
    """The image as a file cwebp can read from standard input.

    Google's Windows cwebp reads input through Windows Imaging Component, which
    has no PAM reader, so it gets an uncompressed PNG. Elsewhere PAM, which every
    cwebp reads with no image libraries, is quicker. Either way the pixels are
    exactly the image's own; the colour profile is added to the WebP afterwards.
    """
    if os.name == "nt" if windows is None else windows:
        buffer = io.BytesIO()
        image.save(buffer, "PNG", compress_level=0, icc_profile=None)
        return buffer.getvalue()
    return _pam(image)


def _pam(image: Image.Image) -> bytes:
    alpha = image.mode == "RGBA"
    header = (f"P7\nWIDTH {image.width}\nHEIGHT {image.height}\nDEPTH {4 if alpha else 3}\n"
              f"MAXVAL 255\nTUPLTYPE {'RGB_ALPHA' if alpha else 'RGB'}\nENDHDR\n")
    return header.encode("ascii") + image.tobytes()


def _chunks(data: bytes):
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("cwebp did not write a WebP file")
    position = 12
    while position + 8 <= len(data):
        name = data[position:position + 4]
        size = struct.unpack("<I", data[position + 4:position + 8])[0]
        yield name, data[position + 8:position + 8 + size]
        position += 8 + size + (size & 1)


def _chunk(name: bytes, payload: bytes) -> bytes:
    return name + struct.pack("<I", len(payload)) + payload + (b"\0" if len(payload) & 1 else b"")


def with_icc(data: bytes, profile: bytes, size: tuple[int, int]) -> bytes:
    """Add a colour profile to a still WebP file, as Pillow's encoder lays it out."""
    chunks = [(name, payload) for name, payload in _chunks(data) if name not in (b"VP8X", b"ICCP")]
    flags = 0x20 | (0x10 if any(name == b"ALPH" for name, _ in chunks) else 0)
    width, height = size
    header = bytes((flags, 0, 0, 0)) + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    body = b"WEBP" + _chunk(b"VP8X", header) + _chunk(b"ICCP", profile) + b"".join(
        _chunk(name, payload) for name, payload in chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def save_sharp_webp(image: Image.Image, path: Path, quality: int, method: int,
                    icc_profile: bytes | None = None, *, sharp_yuv: bool = True) -> None:
    """Encode lossy WebP with sharp YUV conversion; the same settings as Pillow's otherwise."""
    tool = cwebp_tool()
    if tool is None:
        raise RuntimeError("Sharp color conversion needs libwebp's cwebp tool, which wasn't found. "
                           "Turn the option off, or install cwebp (the \"webp\" package).")
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if image.has_transparency_data else "RGB")
    picture = _input(image)
    attempts = [(method, ())]
    for fallback in OVERFLOW_FALLBACKS:
        if (max(method, OVERFLOW_MIN_METHOD), fallback) not in attempts:
            attempts.append((max(method, OVERFLOW_MIN_METHOD), fallback))
    for effort, fallback in attempts:
        command = [tool, "-quiet", "-q", str(quality), "-m", str(effort),
                   *(["-sharp_yuv"] if sharp_yuv else []), *fallback, "-o", "-", "--", "-"]
        result = subprocess.run(command, input=picture, capture_output=True, check=False, **_hidden_window())
        if result.returncode == 0 and result.stdout:
            break
        message = result.stderr.decode(errors="replace").strip() or f"exit code {result.returncode}"
        if "PARTITION0_OVERFLOW" not in message:
            raise RuntimeError(f"cwebp could not encode the image: {message}")
    else:
        raise PartitionOverflow(
            f"This page ({image.width:,} × {image.height:,} px) is too large and detailed for lossy WebP, "
            "even at a higher compression effort with libwebp's size limits relaxed. Export it as JPEG "
            "or PNG, or as lossless WebP.")
    data = result.stdout
    if icc_profile:
        data = with_icc(data, icc_profile, image.size)
    if hasattr(path, "write"):  # A file object, as the size estimate passes.
        path.write(data)
    else:
        Path(path).write_bytes(data)
