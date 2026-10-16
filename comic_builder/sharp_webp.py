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
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

from PIL import Image

MINIMUM_VERSION = (1, 0)  # Reading the image from standard input.


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


def _pam(image: Image.Image) -> bytes:
    """The image as a PAM file, which cwebp reads from standard input."""
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
    command = [tool, "-quiet", "-q", str(quality), "-m", str(method),
               *(["-sharp_yuv"] if sharp_yuv else []), "-o", "-", "--", "-"]
    result = subprocess.run(command, input=_pam(image), capture_output=True, check=False, **_hidden_window())
    if result.returncode != 0 or not result.stdout:
        message = result.stderr.decode(errors="replace").strip() or f"exit code {result.returncode}"
        raise RuntimeError(f"cwebp could not encode the image: {message}")
    data = result.stdout
    if icc_profile:
        data = with_icc(data, icc_profile, image.size)
    if hasattr(path, "write"):  # A file object, as the size estimate passes.
        path.write(data)
    else:
        Path(path).write_bytes(data)
