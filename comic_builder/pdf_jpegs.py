"""Conservative inspection of PDFs containing one unmodified JPEG per page."""
from dataclasses import dataclass
from pathlib import Path
from io import BytesIO
import math
import struct
from PIL import Image, ImageCms
from pypdf import PdfReader
from .backend import scan_folder


@dataclass(frozen=True)
class JpegInspection:
    pages: int
    sizes: tuple[tuple[int, int], ...]


def _multiply(left, right):
    a, b, c, d, e, f = left
    g, h, i, j, k, l = right
    return (a*g+b*i, a*h+b*j, c*g+d*i, c*h+d*j, e*g+f*i+k, e*h+f*j+l)


def _rgb_profile(profile):
    """Accept a usable RGB profile, not merely a plausible ICC header."""
    if (len(profile) < 128 or len(profile) > 16 * 1024 * 1024
            or int.from_bytes(profile[:4], 'big') != len(profile)
            or profile[36:40] != b'acsp' or profile[16:20] != b'RGB '):
        raise ValueError('Unsupported ICC profile')
    cms = ImageCms.ImageCmsProfile(BytesIO(profile))
    ImageCms.buildTransformFromOpenProfiles(cms, ImageCms.createProfile('sRGB'), 'RGB', 'RGB')
    return profile


def _colour_space(value, resources, depth=0):
    if depth > 8 or value is None:
        raise ValueError('Unsupported color space')
    value = value.get_object()
    if value in ('/DeviceRGB', '/DeviceGray'):
        return str(value), None
    if isinstance(value, str):
        spaces = resources.get('/ColorSpace')
        if spaces is not None and value in spaces.get_object():
            return _colour_space(spaces.get_object()[value], resources, depth+1)
    if isinstance(value, (list, tuple)) and len(value) == 2 and value[0] == '/ICCBased':
        stream = value[1].get_object()
        if stream.get('/N') != 3 or '/Range' in stream:
            raise ValueError('Unsupported ICC component mapping')
        return '/DeviceRGB', _rgb_profile(stream.get_data())
    raise ValueError('Unsupported color space')


def _with_icc(data, profile):
    """Insert JPEG APP2 metadata; never decode/re-encode the compressed image."""
    if data[:2] != b'\xff\xd8':
        raise ValueError('Invalid JPEG header')
    chunk_size = 65519  # Maximum APP2 payload minus ICC signature and counters.
    chunks = [profile[n:n+chunk_size] for n in range(0, len(profile), chunk_size)]
    if len(chunks) > 255:
        raise ValueError('ICC profile too large for JPEG')
    segments = []
    for n, chunk in enumerate(chunks, 1):
        payload = b'ICC_PROFILE\x00' + bytes((n, len(chunks))) + chunk
        segments.append(b'\xff\xe2' + struct.pack('>H', len(payload)+2) + payload)
    return data[:2] + b''.join(segments) + data[2:]


def _page_jpeg(page):
    if (int(page.get('/Rotate', 0)) % 360 or float(page.get('/UserUnit', 1)) != 1
            or page.get('/Annots') or page.get('/Group')):
        return None
    content = page.get_contents()
    if content is None:
        return None
    matrix = (1., 0., 0., 1., 0., 0.)
    stack = []
    found = None
    pending_path = None
    pending_clip = False
    resources = page.get('/Resources')
    resources = resources.get_object() if resources is not None else {}
    colour_spaces = resources.get('/ColorSpace')
    if colour_spaces and any(key in colour_spaces.get_object() for key in ('/DefaultRGB', '/DefaultGray', '/DefaultCMYK')):
        return None
    crop, media = page.cropbox, page.mediabox
    if crop.left < media.left or crop.bottom < media.bottom or crop.right > media.right or crop.top > media.top:
        return None
    objects = resources.get('/XObject', {}).get_object() if '/XObject' in resources else {}
    for operands, operation in content.operations:
        # Only an isolated rectangle followed by W/W* and n is admitted.
        if pending_path is not None and operation not in (b'W', b'W*', b'n'):
            return None
        if operation == b'q':
            if operands:
                return None
            stack.append(matrix)
        elif operation == b'Q':
            if not stack or operands:
                return None
            matrix = stack.pop()
        elif operation == b'cm':
            if len(operands) != 6:
                return None
            matrix = _multiply(tuple(map(float, operands)), matrix)
        elif operation == b're':
            if pending_path is not None or len(operands) != 4:
                return None
            x, y, w, h = map(float, operands)
            a, b, c, d, e, f = matrix
            if b != 0 or c != 0:
                return None
            xs = (a*x+e, a*(x+w)+e)
            ys = (d*y+f, d*(y+h)+f)
            pending_path = (min(xs), min(ys), max(xs), max(ys))
            if (not all(math.isfinite(v) for v in pending_path)
                    or pending_path[0] > float(crop.left) or pending_path[1] > float(crop.bottom)
                    or pending_path[2] < float(crop.right) or pending_path[3] < float(crop.top)):
                return None
        elif operation in (b'W', b'W*'):
            if pending_path is None or pending_clip or operands:
                return None
            pending_clip = True
        elif operation == b'n':
            if pending_path is None or not pending_clip or operands:
                return None
            pending_path = None
            pending_clip = False
        elif operation == b'ri':
            if len(operands) != 1 or operands[0] not in ('/Perceptual', '/RelativeColorimetric', '/Saturation', '/AbsoluteColorimetric'):
                return None
        elif operation == b'Do':
            if found is not None or len(operands) != 1 or operands[0] not in objects:
                return None
            image = objects[operands[0]].get_object()
            filters = image.get('/Filter')
            filters = filters.get_object() if filters is not None else None
            if (image.get('/Subtype') != '/Image' or filters not in ('/DCTDecode', ['/DCTDecode'])
                    or image.get('/BitsPerComponent') != 8 or image.get('/ImageMask')
                    or any(key in image for key in ('/SMask', '/Mask', '/Decode', '/DecodeParms', '/Alternates', '/OC'))):
                return None
            try:
                space, profile = _colour_space(image.get('/ColorSpace'), resources)
            except (ValueError, TypeError, AttributeError):
                return None
            expected = (float(crop.width), 0., 0., float(crop.height), float(crop.left), float(crop.bottom))
            if any(not math.isfinite(v) or abs(v - target) > .01 for v, target in zip(matrix, expected, strict=True)):
                return None
            data = image.get_data()
            with Image.open(BytesIO(data)) as jpeg:
                if (jpeg.format != 'JPEG' or jpeg.mode != ('RGB' if space == '/DeviceRGB' else 'L')
                        or jpeg.size != (image.get('/Width'), image.get('/Height'))
                        or abs(float(crop.width) / float(crop.height) - jpeg.width / jpeg.height) > 0.0001 * jpeg.width / jpeg.height
                        or jpeg.width > 20000 or jpeg.height > 20000 or jpeg.width * jpeg.height > 60000000
                        or jpeg.getexif().get(274, 1) != 1):
                    return None
                embedded = jpeg.info.get('icc_profile')
                # A PDF profile must agree with an existing JPEG profile.
                # With DeviceRGB/Gray there is no PDF profile to establish agreement.
                if embedded and (profile is None or embedded != profile):
                    return None
                size = jpeg.size
                jpeg.verify()
            # verify() alone doesn't fully decode JPEG entropy data in Pillow.
            with Image.open(BytesIO(data)) as jpeg:
                jpeg.load()
            if profile and not embedded:
                data = _with_icc(data, profile)
            found = (data, size)
        else:
            return None
    return found if not stack and pending_path is None else None


def inspect_pdf_jpegs(source: Path, progress=None):
    """Return a summary only if EVERY page qualifies; otherwise stay silent."""
    try:
        reader = PdfReader(source)
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 10000:
            return None
        root = reader.trailer['/Root']
        if root.get('/OutputIntents') or root.get('/OCProperties') or root.get('/AcroForm'):
            return None
        sizes = []
        for index, page in enumerate(reader.pages, 1):
            result = _page_jpeg(page)
            if result is None:
                return None
            sizes.append(result[1])
            if progress:
                progress(index, len(reader.pages))
        return JpegInspection(len(sizes), tuple(sorted(set(sizes))))
    except Exception:
        return None


def import_pdf_jpegs(source: Path, destination: Path, manual=False, progress=None, *, right_to_left=False):
    source = Path(source).resolve(strict=True)
    reader = PdfReader(source)
    if reader.is_encrypted or reader.trailer['/Root'].get('/OutputIntents') or reader.trailer['/Root'].get('/OCProperties') or reader.trailer['/Root'].get('/AcroForm'):
        raise ValueError('This PDF no longer qualifies for original JPEG extraction.')
    count = len(reader.pages)
    if not 1 <= count <= 10000:
        raise ValueError('The PDF has an unsupported page count.')
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError('The temporary import folder is not empty.')
    passes = 2 if manual else 3
    for index, page in enumerate(reader.pages, 1):
        result = _page_jpeg(page)
        if result is None:
            raise ValueError('This PDF no longer qualifies for original JPEG extraction. Import it using rendering instead.')
        (destination / f'{index:06d}.jpg').write_bytes(result[0])
        if progress:
            progress(index, count * passes)
    scan = scan_folder(destination, manual=manual, right_to_left=right_to_left,
                       progress=(lambda n, total: progress(count + n, count * passes)) if progress else None)
    scan.source_archive = source
    scan.output_folder = source.parent
    return scan
