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


def _has_form(root):
    """A fillable form with fields. Acrobat adds an empty form to many PDFs it
    makes, which draws nothing and does not stop JPEG extraction."""
    form = root.get('/AcroForm')
    if form is None:
        return False
    form = form.get_object()
    fields = form.get('/Fields')
    return bool(fields.get_object() if fields is not None else None) or '/XFA' in form


class NotExtractable(ValueError):
    """Why a PDF, or one of its pages, can't give up its JPEGs unchanged."""


# Operators that set how later lines and text would look; they draw nothing.
_STATE_OPERATORS = {b'w', b'J', b'j', b'd', b'M', b'i', b'G', b'g', b'RG', b'rg', b'K', b'k',
                    b'CS', b'cs', b'SC', b'SCN', b'sc', b'scn', b'ri'}
_MARKED_CONTENT = {b'BMC', b'BDC', b'EMC', b'MP', b'DP'}  # Tags for accessibility; draw nothing.
_PATH_OPERATORS = {b'm', b'l', b'c', b'v', b'y', b'h', b're'}
_PAINT_OPERATORS = {b'S', b's', b'f', b'F', b'f*', b'B', b'B*', b'b', b'b*'}
_TEXT_STATE = {b'Tc', b'Tw', b'Tz', b'TL', b'Tf', b'Tr', b'Ts'}
_TEXT_SHOW = {b'Tj', b'TJ', b"'", b'"'}
EDGE_LINE_MAX_WIDTH = 2.0  # Points; a hairline along the page edge, as PDF generators add.
_ANNOTATIONS_THAT_DRAW_NOTHING = {'/Link'}
# The top of each standard PDF font's bounding box, in thousandths of the font size.
_STANDARD_FONT_TOPS = {'Helvetica': 931, 'Helvetica-Bold': 962, 'Helvetica-Oblique': 931,
                       'Helvetica-BoldOblique': 962, 'Times-Roman': 898, 'Times-Bold': 935,
                       'Times-Italic': 883, 'Times-BoldItalic': 921, 'Courier': 805, 'Courier-Bold': 801,
                       'Courier-Oblique': 805, 'Courier-BoldOblique': 801, 'Symbol': 1010,
                       'ZapfDingbats': 820}


def _point(matrix, x, y):
    a, b, c, d, e, f = matrix
    return a*x + c*y + e, b*x + d*y + f


def _draws_nothing(annotation):
    """A web or page link with no visible border or appearance of its own."""
    annotation = annotation.get_object()
    if annotation.get('/Subtype') not in _ANNOTATIONS_THAT_DRAW_NOTHING or '/AP' in annotation:
        return False
    border = annotation.get('/Border')
    border_style = annotation.get('/BS')
    if border_style is not None and float(border_style.get_object().get('/W', 1)) != 0:
        return False
    return border is not None and len(border) >= 3 and float(border[2]) == 0


def _text_shows(operands, operation):
    """Whether a text-showing operator has any characters to draw."""
    if operation == b'TJ':
        return any(not isinstance(item, (int, float)) and len(item) > 0 for item in operands[0])
    text = operands[-1] if operands else ''
    return len(text) > 0


def _font_top(fonts, name):
    """How far the font's glyphs can reach above the baseline, as a fraction of its size,
    or None when that isn't known."""
    try:
        font = fonts[name].get_object()
        if font.get('/Subtype') == '/Type3':
            return None
        descriptor = font.get('/FontDescriptor')
        if descriptor is not None:
            box = descriptor.get_object().get('/FontBBox')
            return max(float(box[1]), float(box[3])) / 1000 if box is not None else None
        return _STANDARD_FONT_TOPS.get(str(font.get('/BaseFont', '')).lstrip('/')) / 1000
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        return None


def _page_jpeg(page):
    """The page's JPEG, unchanged, and its size; raises NotExtractable saying why not.

    Admitted besides the one full-page JPEG: anything that draws nothing (page
    groups, text that is empty, invisible or placed entirely off the page, links,
    state settings, tags) and a thin line drawn along the page's own edge, which
    PDF generators such as TCPDF add. Anything that could show on the artwork is
    refused.
    """
    if int(page.get('/Rotate', 0)) % 360:
        raise NotExtractable('the page is rotated')
    if float(page.get('/UserUnit', 1)) != 1:
        raise NotExtractable('the page uses a custom unit size')
    group = page.get('/Group')
    if group is not None and group.get_object().get('/S') != '/Transparency':
        raise NotExtractable('the page has an unusual page group')
    annotations = page.get('/Annots')
    if annotations and not all(_draws_nothing(item) for item in annotations.get_object()):
        raise NotExtractable('the page has annotations, such as comments or stamps')
    content = page.get_contents()
    if content is None:
        raise NotExtractable('the page is empty')
    resources = page.get('/Resources')
    resources = resources.get_object() if resources is not None else {}
    colour_spaces = resources.get('/ColorSpace')
    if colour_spaces and any(key in colour_spaces.get_object() for key in ('/DefaultRGB', '/DefaultGray', '/DefaultCMYK')):
        raise NotExtractable('the page replaces the standard colour spaces')
    crop, media = page.cropbox, page.mediabox
    if crop.left < media.left or crop.bottom < media.bottom or crop.right > media.right or crop.top > media.top:
        raise NotExtractable("the page's visible area is larger than the page")
    left, bottom, right, top = float(crop.left), float(crop.bottom), float(crop.right), float(crop.top)
    objects = resources.get('/XObject', {}).get_object() if '/XObject' in resources else {}
    states = resources.get('/ExtGState', {}).get_object() if '/ExtGState' in resources else {}
    fonts = resources.get('/Font', {}).get_object() if '/Font' in resources else {}
    state = {'matrix': (1., 0., 0., 1., 0., 0.), 'width': 1., 'font': None, 'font_size': 0., 'render': 0,
             'leading': 0., 'rise': 0.}
    stack = []
    found = None
    path = []           # Points of the path being built, in page space.
    path_rects = []     # Its rectangles, for a full-page clip.
    clipping = False
    in_text = False
    text_matrix = line_matrix = (1., 0., 0., 1., 0., 0.)

    def on_edge(x, y):
        tolerance = 0.5
        return (abs(x - left) <= tolerance or abs(x - right) <= tolerance
                or abs(y - bottom) <= tolerance or abs(y - top) <= tolerance)

    def scale():
        a, b, c, d, _, _ = state['matrix']
        return math.sqrt(abs(a*d - b*c))

    def move_text(tx, ty):
        nonlocal text_matrix, line_matrix
        line_matrix = _multiply((1., 0., 0., 1., tx, ty), line_matrix)
        text_matrix = line_matrix

    def text_off_page():
        combined = _multiply(text_matrix, state['matrix'])
        a, b, c, d, _, _ = combined
        if b or c or a <= 0 or d <= 0:
            return False  # Turned or mirrored text: not worked out, so not admitted.
        font_top = _font_top(fonts, state['font'])
        if font_top is None:
            return False  # A font of unknown height: assume it shows.
        x, y = _point(combined, 0, state['rise'])
        size = state['font_size'] * d
        return y + font_top * size <= bottom or y - 0.3 * size >= top or x >= right

    for operands, operation in content.operations:
        if path and operation not in _PATH_OPERATORS | _PAINT_OPERATORS | {b'W', b'W*', b'n'}:
            raise NotExtractable('the page has an unfinished drawing')
        if operation == b'q':
            stack.append(dict(state))
        elif operation == b'Q':
            if not stack:
                raise NotExtractable("the page's drawing commands are unbalanced")
            state = stack.pop()
        elif operation == b'cm':
            if len(operands) != 6:
                raise NotExtractable("the page's drawing commands are malformed")
            state['matrix'] = _multiply(tuple(map(float, operands)), state['matrix'])
        elif operation in _STATE_OPERATORS or operation in _MARKED_CONTENT:
            if operation == b'w' and operands:
                state['width'] = float(operands[0])
        elif operation == b'gs':
            settings = states.get(operands[0]) if operands else None
            settings = settings.get_object() if settings is not None else {}
            if (settings.get('/SMask', '/None') != '/None' or settings.get('/BM', '/Normal') not in ('/Normal', '/Compatible')
                    or float(settings.get('/CA', 1)) != 1 or float(settings.get('/ca', 1)) != 1):
                raise NotExtractable('the page uses transparency or blending')
        elif operation in _PATH_OPERATORS:
            numbers = [float(value) for value in operands]
            if operation == b're':
                x, y, w, h = numbers
                corners = [_point(state['matrix'], px, py) for px, py in ((x, y), (x + w, y), (x, y + h), (x + w, y + h))]
                path.extend(corners)
                path_rects.append(corners)
            else:
                path.extend(_point(state['matrix'], numbers[i], numbers[i + 1]) for i in range(0, len(numbers) - 1, 2))
        elif operation in (b'W', b'W*'):
            clipping = True
        elif operation == b'n':
            if clipping:
                xs = [x for rect in path_rects for x, _ in rect]
                ys = [y for rect in path_rects for _, y in rect]
                if (len(path_rects) != 1 or len(path) != 4 or min(xs) > left or min(ys) > bottom
                        or max(xs) < right or max(ys) < top):
                    raise NotExtractable('the page clips the image')
            path, path_rects, clipping = [], [], False
        elif operation in _PAINT_OPERATORS:
            line_width = state['width'] * scale()
            if (operation not in (b'S', b's') or clipping or line_width > EDGE_LINE_MAX_WIDTH
                    or not path or not all(on_edge(x, y) for x, y in path)):
                raise NotExtractable('the page draws lines or shapes besides the image')
            path, path_rects = [], []  # A hairline along the page edge; not part of the artwork.
        elif operation == b'BT':
            in_text = True
            text_matrix = line_matrix = (1., 0., 0., 1., 0., 0.)
        elif operation == b'ET':
            in_text = False
        elif operation in _TEXT_STATE:
            if operation == b'Tf' and len(operands) == 2:
                state['font'], state['font_size'] = operands[0], float(operands[1])
            elif operation == b'Tr' and operands:
                state['render'] = int(operands[0])
            elif operation == b'TL' and operands:
                state['leading'] = float(operands[0])
            elif operation == b'Ts' and operands:
                state['rise'] = float(operands[0])
        elif operation in (b'Td', b'TD'):
            tx, ty = map(float, operands)
            if operation == b'TD':
                state['leading'] = -ty
            move_text(tx, ty)
        elif operation == b'Tm':
            text_matrix = line_matrix = tuple(map(float, operands))
        elif operation == b'T*':
            move_text(0, -state['leading'])
        elif operation in _TEXT_SHOW:
            if not in_text:
                raise NotExtractable("the page's drawing commands are malformed")
            if operation in (b"'", b'"'):
                move_text(0, -state['leading'])
            if state['render'] != 3 and _text_shows(operands, operation) and not text_off_page():
                raise NotExtractable('the page has visible text')
        elif operation == b'Do':
            if found is not None:
                raise NotExtractable('the page has more than one image')
            if len(operands) != 1 or operands[0] not in objects:
                raise NotExtractable("the page's drawing commands are malformed")
            image = objects[operands[0]].get_object()
            if image.get('/Subtype') != '/Image':
                raise NotExtractable('the page is assembled from several pieces of artwork')
            filters = image.get('/Filter')
            filters = filters.get_object() if filters is not None else None
            if filters not in ('/DCTDecode', ['/DCTDecode']):
                raise NotExtractable("the page's image is not a JPEG")
            if image.get('/BitsPerComponent') != 8 or image.get('/ImageMask'):
                raise NotExtractable('the JPEG has an unusual bit depth')
            if any(key in image for key in ('/SMask', '/Mask', '/Decode', '/DecodeParms', '/Alternates', '/OC')):
                raise NotExtractable('the image has transparency, a mask or a colour adjustment')
            raw_space = image.get('/ColorSpace')
            try:
                space, profile = _colour_space(raw_space, resources)
            except (ValueError, TypeError, AttributeError):
                cmyk = raw_space is not None and 'CMYK' in str(raw_space.get_object())
                raise NotExtractable('the JPEG is CMYK' if cmyk else 'the JPEG uses an unsupported colour space') from None
            expected = (right - left, 0., 0., top - bottom, left, bottom)
            if any(not math.isfinite(v) or abs(v - target) > .01 for v, target in zip(state['matrix'], expected, strict=True)):
                raise NotExtractable("the image doesn't exactly fill the page")
            data = image.get_data()
            with Image.open(BytesIO(data)) as jpeg:
                if jpeg.format != 'JPEG' or jpeg.size != (image.get('/Width'), image.get('/Height')):
                    raise NotExtractable("the JPEG doesn't match its description in the PDF")
                if jpeg.mode != ('RGB' if space == '/DeviceRGB' else 'L'):
                    raise NotExtractable('the JPEG is CMYK' if jpeg.mode == 'CMYK' else 'the JPEG has an unexpected colour mode')
                if abs((right - left) / (top - bottom) - jpeg.width / jpeg.height) > 0.0001 * jpeg.width / jpeg.height:
                    raise NotExtractable("the image is stretched to the page's shape")
                if jpeg.width > 20000 or jpeg.height > 20000 or jpeg.width * jpeg.height > 60000000:
                    raise NotExtractable('the JPEG is too large')
                if jpeg.getexif().get(274, 1) != 1:
                    raise NotExtractable('the JPEG has its own rotation setting')
                embedded = jpeg.info.get('icc_profile')
                # A PDF profile must agree with an existing JPEG profile.
                # With DeviceRGB/Gray there is no PDF profile to establish agreement.
                if embedded and (profile is None or embedded != profile):
                    raise NotExtractable("the JPEG's colour profile doesn't match the PDF's")
                size = jpeg.size
                jpeg.verify()
            # verify() alone doesn't fully decode JPEG entropy data in Pillow.
            with Image.open(BytesIO(data)) as jpeg:
                jpeg.load()
            if profile and not embedded:
                data = _with_icc(data, profile)
            found = (data, size)
        elif operation in (b'BI', b'ID', b'EI', b'INLINE IMAGE'):
            raise NotExtractable('the page has an inline image')
        elif operation == b'sh':
            raise NotExtractable('the page paints a colour gradient')
        else:
            raise NotExtractable(f'the page uses the drawing command {operation.decode(errors="replace")}')
    if stack or path:
        raise NotExtractable("the page's drawing commands are unbalanced")
    if found is None:
        raise NotExtractable('the page has no image')
    return found


def _document_problem(reader):
    """Why the PDF as a whole is not taken apart, or None."""
    root = reader.trailer['/Root']
    if reader.is_encrypted:
        return 'the PDF is encrypted (its publisher restricts copying or printing)'
    if root.get('/OutputIntents'):
        return 'the PDF is prepared for printing with a colour output profile'
    if root.get('/OCProperties'):
        return 'the PDF has optional layers'
    if _has_form(root):
        return 'the PDF has a fillable form'
    if not 1 <= len(reader.pages) <= 10000:
        return 'the PDF has no pages, or too many'
    return None


def _blocks_extraction(reader):
    return _document_problem(reader) is not None


def check_pdf_jpegs(source: Path, progress=None):
    """(summary, None) if every page qualifies, or (None, reason) saying why not."""
    try:
        reader = PdfReader(source)
        problem = _document_problem(reader)
        if problem:
            return None, problem[0].upper() + problem[1:] + '.'
        sizes = []
        for index, page in enumerate(reader.pages, 1):
            try:
                sizes.append(_page_jpeg(page)[1])
            except NotExtractable as exc:
                return None, f'Page {index}: {exc}.'
            if progress:
                progress(index, len(reader.pages))
        return JpegInspection(len(sizes), tuple(sorted(set(sizes)))), None
    except Exception as exc:  # Unreadable or unusual PDFs are rendered instead.
        return None, f'The PDF could not be examined ({type(exc).__name__}).'


def inspect_pdf_jpegs(source: Path, progress=None):
    """Return a summary only if EVERY page qualifies; otherwise stay silent."""
    return check_pdf_jpegs(source, progress)[0]


def import_pdf_jpegs(source: Path, destination: Path, manual=False, progress=None, *, right_to_left=False,
                     workers: int = 0):
    source = Path(source).resolve(strict=True)
    reader = PdfReader(source)
    if _blocks_extraction(reader):
        raise ValueError('This PDF no longer qualifies for original JPEG extraction.')
    count = len(reader.pages)
    if not 1 <= count <= 10000:
        raise ValueError('The PDF has an unsupported page count.')
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError('The temporary import folder is not empty.')
    passes = 2 if manual else 3
    for index, page in enumerate(reader.pages, 1):
        try:
            result = _page_jpeg(page)
        except NotExtractable as exc:
            raise ValueError(f'This PDF no longer qualifies for original JPEG extraction (page {index}: {exc}). '
                             'Import it using rendering instead.') from None
        (destination / f'{index:06d}.jpg').write_bytes(result[0])
        if progress:
            progress(index, count * passes)
    scan = scan_folder(destination, manual=manual, right_to_left=right_to_left, workers=workers,
                       progress=(lambda n, total: progress(count + n, count * passes)) if progress else None)
    scan.source_archive = source
    scan.output_folder = source.parent
    return scan
