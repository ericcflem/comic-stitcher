import tempfile
import unittest
from pathlib import Path
from .import_sources import expand_sources


class SourceTests(unittest.TestCase):
    def test_parent_batch_natural_order_and_deduplication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('10.cbz', '2.pdf', 'readme.txt'):
                (root / name).touch()
            pages = root / '3 pages'
            pages.mkdir()
            (pages / '001.png').touch()
            nested = root / 'nested'
            nested.mkdir()
            (nested / '4.cbz').touch()
            self.assertEqual(expand_sources([root, root / '2.pdf']),
                             [root / '2.pdf', pages, root / '10.cbz'])

    def test_image_folder_stays_one_comic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '001.png').touch()
            (root / 'book.cbz').touch()
            self.assertEqual(expand_sources([root]), [root])
