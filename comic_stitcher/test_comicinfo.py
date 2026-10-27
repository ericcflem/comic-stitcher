import tempfile
import unittest
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET
from PIL import Image
import py7zr
from .backend import BookPlan, scan_folder
from .export import ExportOptions, export_book
from .archive_import import import_archive
from .comicinfo import edit_xml, parse_xml, read_folder_metadata
from .session import save_session, read_session, restore_plan, cached_folder_scan

XML = '''<ComicInfo custom="retained"><Title>Old &amp; Title</Title><Series>Series</Series><PageCount>4</PageCount>
<ScanInformation>Keep this</ScanInformation><Pages>
<Page Image="0" Type="FrontCover" Bookmark="Cover"/><Page Image="1" Bookmark="Part one"/>
<Page Image="2" Bookmark="Part two"/><Page Image="3" Type="BackCover"/></Pages>
<Unfamiliar flag="yes">Retain this too</Unfamiliar></ComicInfo>'''


class ComicInfoTests(unittest.TestCase):
    def source(self, root):
        folder = root / 'pages'; folder.mkdir()
        for n in range(4): Image.new('RGB', (80, 120), 'white').save(folder / f'{n:03}.png')
        (folder / 'ComicInfo.xml').write_text(XML)
        return folder

    def test_import_and_export_preserve_unknown_data_and_remap_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); folder = self.source(root)
            for fmt in ('cbz', 'cb7'):
                plan = BookPlan(scan_folder(folder, manual=True))
                plan.join(1, override_boundary=True); plan.removed_pages.add(3)
                plan.comicinfo_xml = edit_xml(plan.comicinfo_xml, {'Title': 'New <Title> & stuff', 'Series': None})
                opt = ExportOptions(fmt, archive_format=fmt, format='png', workers=1, size_output='both',
                                    resize_pixels=60, keep_images=True, comicinfo_xml=plan.comicinfo_xml)
                outputs = export_book(plan, opt)
                for output in outputs:
                    if fmt == 'cbz':
                        with zipfile.ZipFile(output) as z:
                            self.assertEqual(z.namelist()[-1], 'ComicInfo.xml')
                            doc = ET.fromstring(z.read('ComicInfo.xml'))
                    else:
                        unpack = root / (output.stem + '-unpack'); unpack.mkdir()
                        with py7zr.SevenZipFile(output) as z: z.extractall(unpack)
                        doc = ET.parse(unpack / 'ComicInfo.xml').getroot()
                    self.assertEqual(doc.findtext('Title'), 'New <Title> & stuff')
                    self.assertIsNone(doc.find('Series'))
                    self.assertEqual(doc.findtext('ScanInformation'), 'Keep this')
                    self.assertEqual(doc.find('Unfamiliar').get('flag'), 'yes')
                    self.assertEqual(doc.get('custom'), 'retained')
                    self.assertEqual(doc.findtext('PageCount'), '2')
                    pages = doc.find('Pages').findall('Page')
                    self.assertEqual([p.get('Image') for p in pages], ['0', '1'])
                    self.assertEqual(pages[1].get('Bookmark'), 'Part one / Part two')
                    self.assertEqual(pages[1].get('DoublePage'), 'true')
                    expected = (80, 120) if 'Full' in output.stem else (40, 60)
                    self.assertEqual((int(pages[0].get('ImageWidth')), int(pages[0].get('ImageHeight'))), expected)
                self.assertTrue((folder / (fmt+' - Full - PNG pages') / 'ComicInfo.xml').is_file())
                reimported = BookPlan(import_archive(outputs[0], root / (fmt+'-staged'), manual=True))
                self.assertEqual(parse_xml(reimported.comicinfo_xml).findtext('Title'), 'New <Title> & stuff')
                self.assertEqual((folder / 'ComicInfo.xml').read_text(), XML)

    def test_archive_import_and_rtl_reorder_use_original_page_indices(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); folder = self.source(root)
            archive = root / 'book.cbz'
            with zipfile.ZipFile(archive, 'w') as z:
                for p in folder.iterdir(): z.write(p, p.name)
            scan = import_archive(archive, root / 'stage', manual=True, right_to_left=True)
            plan = BookPlan(scan)
            for front in (True, False):
                opt = ExportOptions(str(front), format='png', workers=1, front_to_back=front, comicinfo_xml=plan.comicinfo_xml)
                with zipfile.ZipFile(export_book(plan, opt)) as z:
                    doc = ET.fromstring(z.read('ComicInfo.xml'))
                    cover = next(p for p in doc.find('Pages') if p.get('Type') == 'FrontCover')
                    self.assertEqual(cover.get('Image'), '3' if front else '0')

    def test_new_metadata_lists_every_page_with_cover_and_spreads(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'pages'; folder.mkdir()
            sizes = [(80, 120)] * 4 + [(160, 120), (80, 120)]  # Page 4 is already a wide spread.
            for n, size in enumerate(sizes): Image.new('RGB', size, 'white').save(folder / f'{n:03}.png')
            plan = BookPlan(scan_folder(folder, manual=True))
            self.assertIsNone(plan.comicinfo_xml)
            plan.join(1, override_boundary=True)
            opt = ExportOptions('new', format='png', workers=1, comicinfo_xml='<ComicInfo/>')
            with zipfile.ZipFile(export_book(plan, opt)) as z:
                doc = ET.fromstring(z.read('ComicInfo.xml'))
            self.assertEqual(doc.findtext('PageCount'), '5')
            pages = doc.find('Pages').findall('Page')
            self.assertEqual([p.get('Image') for p in pages], ['0', '1', '2', '3', '4'])
            self.assertEqual([p.get('Type') for p in pages], ['FrontCover', None, None, None, None])
            self.assertEqual([p.get('DoublePage') for p in pages], [None, 'true', None, 'true', None])
            self.assertEqual((pages[1].get('ImageWidth'), pages[1].get('ImageHeight')), ('160', '120'))
            self.assertTrue(all(int(p.get('ImageSize')) > 0 for p in pages))

    def test_right_to_left_cover_follows_the_saved_page_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'pages'; folder.mkdir()
            for n in range(3): Image.new('RGB', (80, 120), 'white').save(folder / f'{n:03}.png')
            plan = BookPlan(scan_folder(folder, manual=True, right_to_left=True))
            for front, cover in ((True, '0'), (False, '2')):
                opt = ExportOptions(str(front), format='png', workers=1, front_to_back=front,
                                    comicinfo_xml='<ComicInfo/>')
                with zipfile.ZipFile(export_book(plan, opt)) as z:
                    pages = ET.fromstring(z.read('ComicInfo.xml')).find('Pages').findall('Page')
                marked = [p.get('Image') for p in pages if p.get('Type') == 'FrontCover']
                self.assertEqual(marked, [cover])

    def test_cover_marked_in_the_source_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.source(Path(tmp))
            (folder / 'ComicInfo.xml').write_text(XML.replace('Type="FrontCover" ', '').replace(
                '<Page Image="2" ', '<Page Image="2" Type="FrontCover" '))
            plan = BookPlan(scan_folder(folder, manual=True))
            opt = ExportOptions('kept', format='png', workers=1, comicinfo_xml=plan.comicinfo_xml)
            with zipfile.ZipFile(export_book(plan, opt)) as z:
                pages = ET.fromstring(z.read('ComicInfo.xml')).find('Pages').findall('Page')
            self.assertEqual([p.get('Image') for p in pages if 'FrontCover' in (p.get('Type') or '')], ['2'])

    def test_metadata_is_optional_and_session_keeps_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); folder = self.source(root); plan = BookPlan(scan_folder(folder, manual=True))
            plan.comicinfo_xml = edit_xml(plan.comicinfo_xml, {'Publisher': 'Publisher'})
            plan.include_comicinfo = False
            app = root / 'app'
            save_session(folder, app, plan, possible_started=False, stage='preview', preview_index=0)
            data = read_session(folder, app); restored = restore_plan(cached_folder_scan(folder, data), data)
            self.assertEqual(restored.comicinfo_xml, plan.comicinfo_xml)
            self.assertFalse(restored.include_comicinfo)
            out = export_book(plan, ExportOptions('no-metadata', format='png', workers=1))
            with zipfile.ZipFile(out) as z: self.assertNotIn('ComicInfo.xml', z.namelist())

    def test_invalid_fields_and_xml_entities_are_rejected(self):
        for values in ({'Month': '13'}, {'Volume': 'no'}, {'CommunityRating': '6'}, {'CommunityRating': '1e0'}, {'Manga': 'Maybe'}, {'Title': '\x01'}):
            with self.assertRaises(ValueError): edit_xml(None, values)
        with self.assertRaises(ValueError): parse_xml('<!DOCTYPE ComicInfo [<!ENTITY x "oops">]><ComicInfo>&x;</ComicInfo>')
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'ComicInfo.xml'
            p.write_bytes('<!DOCTYPE ComicInfo [<!ENTITY x "oops">]><ComicInfo>&x;</ComicInfo>'.encode('utf-16'))
            with self.assertRaises(ValueError): read_folder_metadata(Path(tmp))

    def test_flushed_queue_reimport_retains_edited_metadata(self):
        import shutil
        from .gui import QueuedComic, export_queued_comic
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); folder = self.source(root)
            archive = root / 'queue.cbz'
            with zipfile.ZipFile(archive, 'w') as z:
                for p in folder.iterdir(): z.write(p, p.name)
            scan = import_archive(archive, root / 'stage', manual=True)
            plan = BookPlan(scan)
            edited = edit_xml(plan.comicinfo_xml, {'Title': 'Queued edit'})
            opt = ExportOptions('queued-export', format='png', workers=1, comicinfo_xml=edited)
            stat = archive.stat()
            job = QueuedComic(plan, opt, archive, None, None, reimport_source=archive,
                              source_stat=(stat.st_size, stat.st_mtime_ns))
            shutil.rmtree(root / 'stage')
            result = export_queued_comic(job)
            with zipfile.ZipFile(result) as z:
                self.assertEqual(ET.fromstring(z.read('ComicInfo.xml')).findtext('Title'), 'Queued edit')
