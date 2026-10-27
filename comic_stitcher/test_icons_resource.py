"""Building must not change the recorded icon resource file unless the icons change."""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packaging"))
import package_icons  # noqa: E402

RESOURCE = ROOT / "comic_stitcher" / "icons_resource.py"


class IconResourceTests(unittest.TestCase):
    def test_file_times_alone_do_not_count_as_a_change(self):
        recorded = RESOURCE.read_bytes()
        changed_times = recorded.replace(b"qt_resource_struct = b\"\\\n", b"qt_resource_struct = b\"\\\n\\x00\\\n", 1)
        self.assertNotEqual(changed_times, recorded)
        self.assertTrue(package_icons.same_icons(recorded, changed_times.replace(b"\n", b"\r\n")))
        other_data = recorded.replace(b"qt_resource_data = b\"\\\n", b"qt_resource_data = b\"\\\n\\x01\\\n", 1)
        self.assertFalse(package_icons.same_icons(recorded, other_data))
        self.assertFalse(package_icons.same_icons(recorded, b"not a resource file"))

    # package_icons finds Qt's resource compiler wherever PySide6 is installed,
    # so this runs on every test machine with the app's requirements.
    @unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 is not installed")
    def test_building_keeps_the_recorded_file(self):
        recorded = RESOURCE.read_bytes()
        try:
            package_icons.prepare()
            self.assertEqual(RESOURCE.read_bytes(), recorded,
                             "The build changed icons_resource.py although assets/icons did not change.")
        finally:
            RESOURCE.write_bytes(recorded)


if __name__ == "__main__":
    unittest.main()
