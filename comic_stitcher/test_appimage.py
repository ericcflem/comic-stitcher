"""App-menu entries for the Linux AppImage (no Qt needed)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from . import appimage
from .drop_tab_support import linux_autostart_path, linux_desktop_entry


@unittest.skipUnless(sys.platform.startswith("linux"), "AppImages are Linux only")
class AppImageIntegrationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.environ = {"HOME": str(self.root / "home"),
                        "XDG_DATA_HOME": str(self.root / "data"),
                        "XDG_CONFIG_HOME": str(self.root / "config")}
        self.image = self.root / "Apps" / "Comic Stitcher.AppImage"
        self.image.parent.mkdir()
        self.image.write_text("not really an AppImage")
        self.image.chmod(0o755)
        self.icon = self.root / "icon.png"
        self.icon.write_bytes(b"png")
        frozen = patch.object(sys, "frozen", True, create=True)
        frozen.start()
        self.addCleanup(frozen.stop)
        quiet = patch.object(appimage, "_refresh_menus")
        quiet.start()
        self.addCleanup(quiet.stop)

    def running_from(self, image):
        return dict(self.environ, APPIMAGE=str(image))

    def test_not_an_appimage_without_the_variable(self):
        self.assertIsNone(appimage.appimage_path(self.environ))
        self.assertEqual(appimage.appimage_path(self.running_from(self.image)), self.image)

    def test_entries_hide_themselves_when_the_appimage_is_gone(self):
        for drop_tab in (False, True):
            entry = appimage.desktop_entry(self.image, drop_tab)
            self.assertIn(f"TryExec={self.image}\n", entry)
            self.assertIn(f'Exec="{self.image}"', entry)
            self.assertIn(appimage.MARKER, entry)
        self.assertIn("--drop-tab", appimage.desktop_entry(self.image, True))
        self.assertIn("MimeType=", appimage.desktop_entry(self.image, False))

    def test_add_and_remove_leave_nothing(self):
        appimage.add_integration(self.image, self.icon, self.environ)
        where = appimage.paths(self.environ)
        for path in where.values():
            self.assertTrue(path.exists(), path)
        self.assertTrue(os.access(where["script"], os.X_OK))
        self.assertEqual(appimage.integrated_path(self.environ), self.image)
        appimage.remove_integration(self.environ)
        for path in where.values():
            self.assertFalse(path.exists(), path)
        self.assertIsNone(appimage.integrated_path(self.environ))

    def test_remove_leaves_other_files_alone(self):
        where = appimage.paths(self.environ)
        for path in where.values():
            path.parent.mkdir(parents=True, exist_ok=True)
        where["app"].write_text("[Desktop Entry]\nName=Something else\n")
        where["script"].write_text("#!/bin/sh\necho someone else's\n")
        appimage.remove_integration(self.environ)
        self.assertTrue(where["app"].exists())
        self.assertTrue(where["script"].exists())

    def test_installed_copy_keeps_its_files_script_and_names_differ(self):
        applications = self.root / "data" / "applications"
        applications.mkdir(parents=True)
        (applications / "comic-stitcher.desktop").write_text("[Desktop Entry]\nX-ComicStitcher-Installer=1\n")
        script = appimage.paths(self.environ)["script"]
        script.parent.mkdir(parents=True)
        script.write_text("#!/bin/sh\n# install.sh's own\n")
        appimage.add_integration(self.image, self.icon, self.environ)
        self.assertEqual(script.read_text(), "#!/bin/sh\n# install.sh's own\n")
        self.assertIn("Name=Comic Stitcher (AppImage)\n",
                      appimage.paths(self.environ)["app"].read_text())

    def test_files_script_removes_itself_once_the_appimage_is_gone(self):
        script = appimage.files_script(self.image)
        self.assertIn(appimage.SCRIPT_MARKER, script)
        self.assertIn('rm -f "$0"', script)
        self.assertIn("'" + str(self.image) + "'", script)

    def test_entries_follow_a_moved_appimage(self):
        appimage.add_integration(self.image, self.icon, self.environ)
        autostart = linux_autostart_path(self.environ)
        autostart.parent.mkdir(parents=True)
        autostart.write_text(linux_desktop_entry([str(self.image), "--drop-tab"]))
        moved = self.root / "Elsewhere.AppImage"
        self.image.rename(moved)
        environ = self.running_from(moved)
        with patch.object(appimage, "bundled_icon", return_value=self.icon), \
                patch("comic_stitcher.drop_tab_support.set_autostart") as set_autostart:
            self.assertTrue(appimage.follow_move(environ))
        self.assertEqual(appimage.integrated_path(self.environ), moved)
        set_autostart.assert_called_once_with(True, [str(moved), "--drop-tab"])
        self.assertFalse(appimage.follow_move(environ))  # Already up to date.

    def test_nothing_follows_when_never_added(self):
        self.assertFalse(appimage.follow_move(self.running_from(self.image)))
        self.assertFalse(appimage.paths(self.environ)["app"].exists())

    def test_removing_takes_out_this_appimages_sign_in_entry(self):
        autostart = linux_autostart_path(self.environ)
        autostart.parent.mkdir(parents=True)
        autostart.write_text(linux_desktop_entry([str(self.image), "--drop-tab"]))
        appimage.remove_integration(self.running_from(self.image))
        self.assertFalse(autostart.exists())

    def test_purge_removes_settings_logs_and_reviews(self):
        folders = [self.root / "config" / "ComicStitcher", self.root / "data" / "ComicStitcher",
                   self.root / "home" / ".local" / "share" / "ComicStitcher" / "sessions"]
        for folder in folders:
            folder.mkdir(parents=True)
            (folder / "file").write_text("x")
        appimage.purge_user_data(self.environ)
        for folder in folders:
            self.assertFalse(folder.exists(), folder)


class TryExecTests(unittest.TestCase):
    def test_sign_in_entry_hides_when_its_program_is_gone(self):
        self.assertIn("TryExec=/opt/Comic Stitcher.AppImage\n",
                      linux_desktop_entry(["/opt/Comic Stitcher.AppImage", "--drop-tab"]))
        self.assertNotIn("TryExec", linux_desktop_entry(["comic-stitcher", "--drop-tab"]))


if __name__ == "__main__":
    unittest.main()
