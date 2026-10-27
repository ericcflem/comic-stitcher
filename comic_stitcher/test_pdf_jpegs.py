import tempfile
import unittest
from pathlib import Path
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject,
                           NumberObject)
from .pdf_jpegs import check_pdf_jpegs, inspect_pdf_jpegs, import_pdf_jpegs


class PdfJpegTests(unittest.TestCase):
    def simple_pdf(self, path):
        Image.new('RGB', (80, 120), 'red').save(path, 'PDF', save_all=True,
                   append_images=[Image.new('RGB', (100, 140), 'blue')])

    def modified(self, source, target, change):
        reader = PdfReader(source)
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        change(writer.pages[-1])
        writer.write(target)

    def test_complete_jpegs_are_extracted_byte_for_byte_in_pdf_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'book.pdf'
            self.simple_pdf(source)
            original = source.read_bytes()
            report = inspect_pdf_jpegs(source)
            self.assertEqual(report.pages, 2)
            self.assertEqual(report.sizes, ((80, 120), (100, 140)))
            expected = []
            for page in PdfReader(source).pages:
                image = next(iter(page['/Resources']['/XObject'].values())).get_object()
                expected.append(image.get_data())
            scan = import_pdf_jpegs(source, root / 'stage', manual=True, right_to_left=True)
            self.assertEqual([p.path.read_bytes() for p in scan.pages], expected[::-1])
            self.assertEqual(source.read_bytes(), original)

    def test_empty_acrobat_form_allows_offer_but_real_fields_do_not(self):
        from pypdf.generic import DictionaryObject, TextStringObject
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'book.pdf'
            self.simple_pdf(source)
            for fields, offered in (([], True), ([DictionaryObject({NameObject('/T'): TextStringObject('Name')})], False)):
                writer = PdfWriter(clone_from=PdfReader(source))
                # Acrobat adds a form like this, with no fields, to many PDFs it makes.
                writer._root_object[NameObject('/AcroForm')] = writer._add_object(DictionaryObject({
                    NameObject('/Fields'): ArrayObject([writer._add_object(f) for f in fields]),
                    NameObject('/DA'): TextStringObject('/Helv 0 Tf 0 g ')}))
                target = root / f'form-{offered}.pdf'
                writer.write(target)
                report = inspect_pdf_jpegs(target)
                self.assertEqual(report is not None, offered)
                if offered:
                    scan = import_pdf_jpegs(target, root / 'stage', manual=True)
                    self.assertEqual(len(scan.pages), 2)

    def test_any_extra_content_prevents_offer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'book.pdf'
            self.simple_pdf(source)
            for content in (b'0 0 1 1 re f', b'BT /F1 12 Tf 10 10 Td (Hi) Tj ET', b'0 0 1 1 re W n'):
                def change(page, content=content):
                    stream = DecodedStreamObject()
                    stream.set_data(page.get_contents().get_data() + b'\n' + content)
                    page[NameObject('/Contents')] = stream
                target = root / 'modified.pdf'
                self.modified(source, target, change)
                self.assertIsNone(inspect_pdf_jpegs(target))

    def test_masks_rotations_and_decode_adjustments_prevent_offer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'book.pdf'
            self.simple_pdf(source)
            changes = [lambda page: page.__setitem__(NameObject('/Rotate'), NumberObject(90)),
                       lambda page: next(iter(page['/Resources']['/XObject'].values())).get_object().__setitem__(
                           NameObject('/Decode'), ArrayObject([NumberObject(0), NumberObject(1)])),
                       lambda page: next(iter(page['/Resources']['/XObject'].values())).get_object().__setitem__(
                           NameObject('/Mask'), ArrayObject([NumberObject(0), NumberObject(0)]))]
            for change in changes:
                target = root / 'modified.pdf'
                self.modified(source, target, change)
                self.assertIsNone(inspect_pdf_jpegs(target))

    def test_blank_or_fragment_page_prevents_whole_book_offer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'book.pdf'
            self.simple_pdf(source)
            def change(page):
                page.cropbox.upper_right = (150, 200)
            self.modified(source, root / 'fragment.pdf', change)
            self.assertIsNone(inspect_pdf_jpegs(root / 'fragment.pdf'))
            writer = PdfWriter()
            writer.add_blank_page(width=80, height=120)
            writer.write(root / 'blank.pdf')
            self.assertIsNone(inspect_pdf_jpegs(root / 'blank.pdf'))

    def test_multiple_images_or_non_jpeg_page_prevents_offer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'book.pdf'
            self.simple_pdf(source)
            def duplicate(page):
                name = next(iter(page['/Resources']['/XObject']))
                stream = DecodedStreamObject()
                stream.set_data(page.get_contents().get_data() + b'\n' + str(name).encode() + b' Do')
                page[NameObject('/Contents')] = stream
            self.modified(source, root / 'multiple.pdf', duplicate)
            self.assertIsNone(inspect_pdf_jpegs(root / 'multiple.pdf'))
            def non_jpeg(page):
                next(iter(page['/Resources']['/XObject'].values())).get_object()[NameObject('/Filter')] = NameObject('/FlateDecode')
            self.modified(source, root / 'mixed.pdf', non_jpeg)
            self.assertIsNone(inspect_pdf_jpegs(root / 'mixed.pdf'))

    def test_full_page_clip_and_indirect_icc_keep_jpeg_payload(self):
        from PIL import ImageCms
        from io import BytesIO
        from .backend import stitched_image
        from .export import _encode_entry, ExportOptions
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'book.pdf'
            self.simple_pdf(source)
            reader = PdfReader(source)
            writer = PdfWriter()
            profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
            expected = []
            for page in reader.pages:
                writer.add_page(page)
                page = writer.pages[-1]
                image = next(iter(page['/Resources']['/XObject'].values())).get_object()
                expected.append(image.get_data())
                icc = DecodedStreamObject(); icc.set_data(profile)
                icc[NameObject('/N')] = NumberObject(3)
                icc[NameObject('/Alternate')] = NameObject('/DeviceRGB')
                image[NameObject('/ColorSpace')] = writer._add_object(ArrayObject([
                    NameObject('/ICCBased'), writer._add_object(icc)]))
                w, h = page.cropbox.width, page.cropbox.height
                stream = DecodedStreamObject()
                stream.set_data(f'q 0 0 {w} {h} re W n /Perceptual ri\n'.encode()+page.get_contents().get_data()+b'\nQ')
                page[NameObject('/Contents')] = stream
            wrapped = root / 'wrapped.pdf'; writer.write(wrapped)
            self.assertIsNotNone(inspect_pdf_jpegs(wrapped))
            scan = import_pdf_jpegs(wrapped, root / 'stage', manual=True)
            for page, original in zip(scan.pages, expected, strict=True):
                extracted = page.path.read_bytes()
                # Removing our inserted APP2 segments reconstructs every original byte.
                pos = 2
                while extracted[pos:pos+2] == b'\xff\xe2':
                    pos += 2 + int.from_bytes(extracted[pos+2:pos+4], 'big')
                self.assertEqual(extracted[:2]+extracted[pos:], original)
                with Image.open(page.path) as im:
                    self.assertEqual(im.info['icc_profile'], profile)
                    with Image.open(BytesIO(original)) as before:
                        self.assertEqual(im.tobytes(), before.tobytes())
            joined = stitched_image(scan.pages[0].path, scan.pages[1].path)
            self.assertTrue(joined.info['icc_profile']); joined.close()
            for fmt in ('jpeg', 'png', 'webp'):
                output = root / ('processed.'+fmt)
                _encode_entry(str(scan.pages[0].path), None, str(output), fmt, ExportOptions(book_name="test", resize_pixels=60), reduce=True)
                with Image.open(output) as im:
                    self.assertTrue(im.info['icc_profile'])

    def test_nontrivial_clip_transform_and_icc_conflicts_still_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'book.pdf'; self.simple_pdf(source)
            def small_clip(page):
                stream = DecodedStreamObject()
                stream.set_data(b'q 0 0 1 1 re W n\n'+page.get_contents().get_data()+b'\nQ')
                page[NameObject('/Contents')] = stream
            self.modified(source, root / 'clipped.pdf', small_clip)
            self.assertIsNone(inspect_pdf_jpegs(root / 'clipped.pdf'))
            def transform(page):
                stream = DecodedStreamObject()
                stream.set_data(b'1 0 0 1 5 0 cm\n'+page.get_contents().get_data())
                page[NameObject('/Contents')] = stream
            self.modified(source, root / 'shifted.pdf', transform)
            self.assertIsNone(inspect_pdf_jpegs(root / 'shifted.pdf'))
            def bad_profile(page):
                obj = next(iter(page['/Resources']['/XObject'].values())).get_object()
                icc = DecodedStreamObject(); icc.set_data(b'not an ICC profile')
                icc[NameObject('/N')] = NumberObject(3)
                obj[NameObject('/ColorSpace')] = ArrayObject([NameObject('/ICCBased'), icc])
            self.modified(source, root / 'bad-icc.pdf', bad_profile)
            self.assertIsNone(inspect_pdf_jpegs(root / 'bad-icc.pdf'))

    def test_metadata_insertion_spans_app2_segments_without_recompressing(self):
        from io import BytesIO
        from .pdf_jpegs import _with_icc
        b = BytesIO(); Image.new('RGB', (8, 8), 'red').save(b, 'JPEG')
        original = b.getvalue()
        profile = b'x' * 140000
        result = _with_icc(original, profile)
        with Image.open(BytesIO(result)) as image:
            self.assertEqual(image.info['icc_profile'], profile)
        pos = 2
        while result[pos:pos+2] == b'\xff\xe2':
            pos += 2 + int.from_bytes(result[pos+2:pos+4], 'big')
        self.assertEqual(result[:2]+result[pos:], original)

    def test_pdf_and_jpeg_profiles_must_agree(self):
        from PIL import ImageCms
        from .pdf_jpegs import _with_icc
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'book.pdf'; self.simple_pdf(source)
            profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
            for matching in (True, False):
                def change(page, matching=matching):
                    old = next(iter(page['/Resources']['/XObject'].values())).get_object()
                    data = old.get_data()
                    image = DecodedStreamObject()
                    image.update(old)
                    jpeg_profile = profile if matching else profile[:80] + bytes((profile[80] ^ 1,)) + profile[81:]
                    image.set_data(_with_icc(data, jpeg_profile))
                    icc = DecodedStreamObject(); icc.set_data(profile)
                    icc[NameObject('/N')] = NumberObject(3)
                    image[NameObject('/ColorSpace')] = ArrayObject([NameObject('/ICCBased'), icc])
                    name = next(iter(page['/Resources']['/XObject']))
                    page['/Resources']['/XObject'][name] = image
                target = root / 'profile.pdf'; self.modified(source, target, change)
                self.assertEqual(inspect_pdf_jpegs(target) is not None, matching)

    def tcpdf_like(self, page, extra=b'', *, border=0, font='/Helvetica', number_y=-11.52):
        """The extras TCPDF adds around a full-page JPEG: empty text blocks, a hairline
        along the bottom edge, a page number just below the page, and a borderless link."""
        width = float(page.cropbox.width)
        resources = page['/Resources']
        resources[NameObject('/Font')] = DictionaryObject({NameObject('/F1'): DictionaryObject({
            NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
            NameObject('/BaseFont'): NameObject(font)})})
        stream = DecodedStreamObject()
        stream.set_data(b'0.57 w 0 J 0 j [] 0 d 0 G 0 g\nBT /F1 12.00 Tf ET\n' + page.get_contents().get_data()
                        + b'\nq BT /F1 12.00 Tf ET 0.85 w 2 J 0 0 0 RG 0 g\n'
                        + f'0.00 0.00 m {width:.2f} 0.00 l S\n'.encode()
                        + f'q 0 0 0 rg BT 0 Tr 0.00 w ET BT {width - 60:.2f} {number_y:.2f} Td [( 1 / 2)] TJ ET Q\nQ\n'.encode()
                        + extra)
        page[NameObject('/Contents')] = stream
        page[NameObject('/Annots')] = ArrayObject([DictionaryObject({
            NameObject('/Type'): NameObject('/Annot'), NameObject('/Subtype'): NameObject('/Link'),
            NameObject('/Rect'): ArrayObject([FloatObject(0), FloatObject(0), FloatObject(10), FloatObject(10)]),
            NameObject('/Border'): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(border)])})])

    def test_tcpdf_extras_still_allow_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'book.pdf'; self.simple_pdf(source)
            expected = [next(iter(page['/Resources']['/XObject'].values())).get_object().get_data()
                        for page in PdfReader(source).pages]
            target = root / 'tcpdf.pdf'
            self.modified(source, target, self.tcpdf_like)
            report, reason = check_pdf_jpegs(target)
            self.assertIsNone(reason)
            self.assertEqual(report.pages, 2)
            scan = import_pdf_jpegs(target, root / 'stage', manual=True)
            self.assertEqual([p.path.read_bytes() for p in scan.pages], expected)

    def test_reasons_say_why_jpegs_are_not_extracted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'book.pdf'; self.simple_pdf(source)
            cases = [
                (lambda page: self.tcpdf_like(page, number_y=2), 'Page 2: the page has visible text.'),
                (lambda page: self.tcpdf_like(page, font='/MadeUpFont'), 'Page 2: the page has visible text.'),
                (lambda page: self.tcpdf_like(page, border=1),
                 'Page 2: the page has annotations, such as comments or stamps.'),
                (lambda page: self.tcpdf_like(page, b'5 w 0 0 m 10 0 l S'),
                 'Page 2: the page draws lines or shapes besides the image.'),
                (lambda page: self.tcpdf_like(page, b'0 0 m 10 10 l S'),
                 'Page 2: the page draws lines or shapes besides the image.'),
                (lambda page: page.__setitem__(NameObject('/Rotate'), NumberObject(90)), 'Page 2: the page is rotated.'),
            ]
            for change, expected in cases:
                target = root / 'modified.pdf'
                self.modified(source, target, change)
                self.assertEqual(check_pdf_jpegs(target), (None, expected))
            writer = PdfWriter(clone_from=PdfReader(source))
            writer.encrypt('', 'owner')
            writer.write(root / 'locked.pdf')
            self.assertEqual(check_pdf_jpegs(root / 'locked.pdf'),
                             (None, 'The PDF is encrypted (its publisher restricts copying or printing).'))
            (root / 'broken.pdf').write_bytes(b'not a pdf')
            report, reason = check_pdf_jpegs(root / 'broken.pdf')
            self.assertIsNone(report)
            self.assertTrue(reason.startswith('The PDF could not be examined'))
