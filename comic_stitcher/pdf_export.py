"""Write finished page images into a PDF without re-encoding them.

JPEG files are stored byte for byte. PNG files keep their compressed image data
whenever PDF can use it directly (no interlacing or transparency); otherwise the
pixels are stored losslessly. Pages are sized at PAGE_DPI, and EXIF orientation
is honoured by how each image is placed, not by changing it.
"""
from __future__ import annotations

import struct
import uuid
import zlib
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from .colour import standard_mode

PAGE_DPI = 300  # The resolution of comic page scans and of most publishers' PDFs.
PDF_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _name_string(text: str) -> bytes:
    """A PDF text string (UTF-16 with a byte order mark), safe for any title."""
    return b"<FEFF" + text.encode("utf-16-be").hex().upper().encode() + b">"


def _dictionary(entries: dict[str, bytes | str | int | float]) -> bytes:
    parts = []
    for key, value in entries.items():
        if isinstance(value, bool):
            value = b"true" if value else b"false"
        elif isinstance(value, int):
            value = str(value).encode()
        elif isinstance(value, float):
            value = f"{value:.4f}".rstrip("0").rstrip(".").encode()
        elif isinstance(value, str):
            value = value.encode()
        parts.append(b"/" + key.encode() + b" " + value)
    return b"<< " + b" ".join(parts) + b" >>"


# Placement of an image's unit square on a W x H page for each EXIF orientation,
# as the matrix (a, b, c, d, e, f) multiplied by (W, H) where marked.
def _orientation_matrix(orientation: int, width: float, height: float) -> tuple[float, ...]:
    w, h = width, height
    return {
        1: (w, 0, 0, h, 0, 0),
        2: (-w, 0, 0, h, w, 0),
        3: (-w, 0, 0, -h, w, h),
        4: (w, 0, 0, -h, 0, h),
        5: (0, -h, -w, 0, w, h),
        6: (0, -h, w, 0, 0, h),
        7: (0, h, w, 0, 0, 0),
        8: (0, h, -w, 0, w, 0),
    }.get(orientation, (w, 0, 0, h, 0, 0))


class _Writer:
    def __init__(self, handle):
        self.handle = handle
        self.offsets: dict[int, int] = {}
        self.next_number = 1

    def reserve(self) -> int:
        number = self.next_number
        self.next_number += 1
        return number

    def write_object(self, number: int, body: bytes, stream: bytes | None = None) -> None:
        self.offsets[number] = self.handle.tell()
        self.handle.write(f"{number} 0 obj\n".encode())
        self.handle.write(body)
        if stream is not None:
            self.handle.write(b"\nstream\n")
            self.handle.write(stream)
            self.handle.write(b"\nendstream")
        self.handle.write(b"\nendobj\n")

    def add(self, body: bytes, stream: bytes | None = None) -> int:
        number = self.reserve()
        self.write_object(number, body, stream)
        return number


def _icc_object(writer: _Writer, profile: bytes, components: int) -> bytes:
    alternate = {1: "/DeviceGray", 3: "/DeviceRGB", 4: "/DeviceCMYK"}[components]
    data = zlib.compress(profile)
    number = writer.add(_dictionary({"N": components, "Alternate": alternate, "Filter": "/FlateDecode",
                                     "Length": len(data)}), data)
    return f"[/ICCBased {number} 0 R]".encode()


def _device_space(components: int) -> bytes:
    return {1: b"/DeviceGray", 3: b"/DeviceRGB", 4: b"/DeviceCMYK"}[components]


def _png_chunks(data: bytes):
    position = len(PNG_SIGNATURE)
    while position + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[position:position + 8])
        yield kind, data[position + 8:position + 8 + length]
        position += 12 + length


def _png_passthrough(writer: _Writer, data: bytes):
    """Image entries reusing a PNG's own compressed data, or None if PDF can't."""
    header = palette = profile = None
    idat = []
    transparency = False
    for kind, body in _png_chunks(data):
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = body
        elif kind == b"tRNS":
            transparency = True
        elif kind == b"iCCP":
            name_end = body.index(b"\0")
            profile = zlib.decompress(body[name_end + 2:])
        elif kind == b"IDAT":
            idat.append(body)
    if header is None:
        return None
    width, height, depth, colour_type, _compression, _filter, interlace = header
    if interlace or transparency or colour_type not in (0, 2, 3):
        return None  # Interlaced or transparent images must be decoded.
    if colour_type == 3:
        if palette is None or depth > 8:
            return None
        base = _icc_object(writer, profile, 3) if profile else b"/DeviceRGB"
        space = (b"[/Indexed " + base + f" {len(palette) // 3 - 1} <".encode()
                 + palette.hex().encode() + b">]")
        colours = 1
    else:
        colours = 1 if colour_type == 0 else 3
        space = _icc_object(writer, profile, colours) if profile else _device_space(colours)
    stream = b"".join(idat)
    entries = {"Type": "/XObject", "Subtype": "/Image", "Width": width, "Height": height,
               "ColorSpace": space, "BitsPerComponent": depth, "Filter": "/FlateDecode",
               "DecodeParms": _dictionary({"Predictor": 15, "Colors": colours,
                                           "BitsPerComponent": depth, "Columns": width}),
               "Length": len(stream)}
    return entries, stream, (width, height)


def _decoded_image(writer: _Writer, path: Path):
    """Image entries holding the decoded pixels, losslessly compressed.

    For images PDF can't take as they are. Transparency is flattened onto white,
    as JPEG export does.
    """
    with Image.open(path) as image:
        image.load()
        profile = image.info.get("icc_profile")
        if image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info:
            rgba = image.convert("RGBA")
            image = Image.new("RGB", rgba.size, "white")
            image.paste(rgba, mask=rgba.getchannel("A"))
        elif image.mode not in ("L", "RGB", "CMYK"):
            image = standard_mode(image)  # 16-bit grey is scaled to 8 bits, not clipped.
            if image.mode not in ("L", "RGB"):
                image = image.convert("RGB")
        components = {"L": 1, "RGB": 3, "CMYK": 4}[image.mode]
        stream = zlib.compress(image.tobytes(), 6)
        size = image.size
    space = _icc_object(writer, profile, components) if profile else _device_space(components)
    entries = {"Type": "/XObject", "Subtype": "/Image", "Width": size[0], "Height": size[1],
               "ColorSpace": space, "BitsPerComponent": 8, "Filter": "/FlateDecode",
               "Length": len(stream)}
    return entries, stream, size


def _jpeg_image(writer: _Writer, path: Path, data: bytes):
    with Image.open(path) as image:
        if image.format != "JPEG" or image.mode not in ("L", "RGB", "CMYK"):
            return None  # For example a 12-bit JPEG, which PDF readers rarely support.
        mode = image.mode
        size = image.size
        profile = image.info.get("icc_profile")
        adobe = "adobe" in image.info
    components = {"L": 1, "RGB": 3, "CMYK": 4}[mode]
    space = _icc_object(writer, profile, components) if profile else _device_space(components)
    entries = {"Type": "/XObject", "Subtype": "/Image", "Width": size[0], "Height": size[1],
               "ColorSpace": space, "BitsPerComponent": 8, "Filter": "/DCTDecode", "Length": len(data)}
    if mode == "CMYK" and adobe:
        # Standalone CMYK JPEG files with Adobe's marker store their values
        # inverted (Photoshop's convention); inside a PDF they must be marked so.
        entries["Decode"] = "[1 0 1 0 1 0 1 0]"
    return entries, data, size


def _orientation(path: Path) -> int:
    with Image.open(path) as image:
        return image.getexif().get(274, 1)


def write_pdf(images: list[Path], destination: Path, *, title: str = "", right_to_left: bool = False,
              dpi: int = PAGE_DPI, progress=None) -> None:
    """Write one page per image, in order, sized at dpi."""
    with open(destination, "wb") as handle:
        handle.write(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        writer = _Writer(handle)
        catalog, pages = writer.reserve(), writer.reserve()
        kids = []
        for number, path in enumerate(images, 1):
            data = path.read_bytes()
            image = None
            if data[:2] == b"\xff\xd8":
                image = _jpeg_image(writer, path, data)
            elif data.startswith(PNG_SIGNATURE):
                image = _png_passthrough(writer, data)
            if image is None:
                image = _decoded_image(writer, path)
            entries, stream, (width_px, height_px) = image
            del data
            image_number = writer.add(_dictionary(entries), stream)
            orientation = _orientation(path)
            if orientation in (5, 6, 7, 8):
                width_px, height_px = height_px, width_px
            width, height = width_px * 72 / dpi, height_px * 72 / dpi
            matrix = " ".join(f"{value:.4f}".rstrip("0").rstrip(".")
                              for value in _orientation_matrix(orientation, width, height))
            content = f"q {matrix} cm /Im0 Do Q".encode()
            content_number = writer.add(_dictionary({"Length": len(content)}), content)
            box = f"[0 0 {width:.4f} {height:.4f}]"
            kids.append(writer.add(_dictionary({
                "Type": "/Page", "Parent": f"{pages} 0 R", "MediaBox": box,
                "Resources": _dictionary({"XObject": _dictionary({"Im0": f"{image_number} 0 R"})}),
                "Contents": f"{content_number} 0 R"})))
            if progress:
                progress(number, len(images))
        writer.write_object(pages, _dictionary({
            "Type": "/Pages", "Kids": "[" + " ".join(f"{kid} 0 R" for kid in kids) + "]",
            "Count": len(kids)}))
        catalog_entries = {"Type": "/Catalog", "Pages": f"{pages} 0 R"}
        if right_to_left:
            catalog_entries["ViewerPreferences"] = "<< /Direction /R2L >>"
        writer.write_object(catalog, _dictionary(catalog_entries))
        now = datetime.now(timezone.utc).strftime("D:%Y%m%d%H%M%SZ")
        info_entries = {"Producer": _name_string("Comic Stitcher"), "CreationDate": f"({now})"}
        if title:
            info_entries["Title"] = _name_string(title)
        info = writer.add(_dictionary(info_entries))
        xref = handle.tell()
        count = writer.next_number
        handle.write(f"xref\n0 {count}\n0000000000 65535 f \n".encode())
        for number in range(1, count):
            handle.write(f"{writer.offsets[number]:010d} 00000 n \n".encode())
        file_id = uuid.uuid4().hex.upper()
        handle.write(b"trailer\n" + _dictionary({
            "Size": count, "Root": f"{catalog} 0 R", "Info": f"{info} 0 R",
            "ID": f"[<{file_id}> <{file_id}>]"}) + f"\nstartxref\n{xref}\n%%EOF\n".encode())


def verify_pdf(path: Path, page_count: int) -> bool:
    """The PDF opens and has the expected number of pages."""
    from pypdf import PdfReader
    try:
        return len(PdfReader(path, strict=True).pages) == page_count
    except Exception:
        return False
