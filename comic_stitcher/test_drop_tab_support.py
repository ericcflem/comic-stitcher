"""Layout, launch and sign-in rules for the drop tab (no Qt needed)."""
import os
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from . import drop_tab_support as support
from .drop_tab_support import (
    COLLAPSED_SIZE, DEFAULT_OFFSET, DROP_TAB_FLAG, EDGE_MARGIN, EXPANDED_SIZE, UNINSTALL_FLAG,
    clamp_height, clamp_hover_delay, clamp_offset, clamp_thickness, max_height, offset_range, drop_tab_command, launch_command, linux_autostart_path,
    LINUX_WINDOW_NAME, linux_desktop_entry, needs_x11, qt_arguments, tab_rect, wants_drop_tab,
)

SCREEN = (0, 0, 1920, 1040)


def contains(outer, inner):
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner
    return ox <= ix and oy <= iy and ix + iw <= ox + ow and iy + ih <= oy + oh


class LayoutTests(unittest.TestCase):
    def test_default_tab_sits_on_right_edge_below_the_corner(self):
        x, y, width, height = tab_rect(SCREEN, DEFAULT_OFFSET, expanded=False)
        self.assertEqual((width, height), COLLAPSED_SIZE)
        self.assertEqual(x + width, 1920)
        self.assertEqual(y, DEFAULT_OFFSET)

    def test_expanded_panel_keeps_right_edge_and_covers_the_tab(self):
        for screen in (SCREEN, (1920, 0, 1280, 984), (0, 40, 800, 560)):
            for offset in (0, 1, 200, 450, 10_000, -50):
                collapsed = tab_rect(screen, offset, expanded=False)
                expanded = tab_rect(screen, offset, expanded=True)
                self.assertEqual(expanded[2:], EXPANDED_SIZE)
                self.assertEqual(expanded[0] + expanded[2], screen[0] + screen[2])
                self.assertTrue(contains(expanded, collapsed), (screen, offset))
                self.assertTrue(contains(screen, expanded), (screen, offset))
                self.assertTrue(contains(screen, collapsed), (screen, offset))

    def test_offset_is_clamped_and_tolerates_bad_settings(self):
        self.assertEqual(clamp_offset(-10, 1000), EDGE_MARGIN)
        self.assertEqual(clamp_offset(5000, 1000), 1000 - EDGE_MARGIN - COLLAPSED_SIZE[1])
        self.assertEqual(clamp_offset("not a number", 1000), DEFAULT_OFFSET)
        self.assertEqual(clamp_offset(None, 1000), DEFAULT_OFFSET)
        self.assertEqual(clamp_offset("300", 1000), 300)


    def test_settings_are_kept_in_range(self):
        self.assertEqual(clamp_thickness(1), 3)
        self.assertEqual(clamp_thickness(99), 24)
        self.assertEqual(clamp_thickness("junk"), 8)
        self.assertEqual(clamp_hover_delay(-5), 0)
        self.assertEqual(clamp_hover_delay(9000), 2000)
        self.assertEqual(clamp_hover_delay(None), 450)
        self.assertEqual(max_height(1040), 1040 - 2 * EDGE_MARGIN)
        self.assertEqual(clamp_height(10, 1040), 48)
        self.assertEqual(clamp_height(5000, 1040), 1040 - 2 * EDGE_MARGIN)

    def test_tab_never_enters_the_top_or_bottom_margin(self):
        for height in (48, 96, 400, 944):
            low, high = offset_range(1040, height)
            for offset in (-100, low, high, 10_000):
                x, y, w, h = tab_rect(SCREEN, offset, False, collapsed_size=(12, height))
                self.assertGreaterEqual(y, EDGE_MARGIN)
                self.assertLessEqual(y + h, 1040 - EDGE_MARGIN)
                self.assertEqual((x + w, w, h), (1920, 12, height))

    def test_tall_tab_opens_the_panel_under_the_pointer(self):
        tall = (8, 800)
        tab = tab_rect(SCREEN, 100, False, collapsed_size=tall)
        for pointer in (tab[1], tab[1] + 400, tab[1] + tab[3] - 1):
            panel = tab_rect(SCREEN, 100, True, collapsed_size=tall, anchor_y=pointer)
            self.assertEqual(panel[2:], EXPANDED_SIZE)
            self.assertTrue(panel[1] <= pointer < panel[1] + panel[3], pointer)
            self.assertTrue(contains(SCREEN, panel))

    def test_short_tab_is_covered_by_the_panel_for_any_thickness(self):
        for thickness in (3, 8, 24):
            for height in (48, 96, 150):
                tab = tab_rect(SCREEN, 300, False, collapsed_size=(thickness, height))
                panel = tab_rect(SCREEN, 300, True, collapsed_size=(thickness, height), anchor_y=0)
                self.assertTrue(contains(panel, tab), (thickness, height))


class LaunchTests(unittest.TestCase):
    def test_installed_build_passes_paths_straight_to_the_exe(self):
        program, arguments = launch_command([Path("C:/Comics/a b.cbz")], frozen=True,
                                            executable="C:/Program Files/Comic Stitcher/ComicStitcher.exe",
                                            environ={})
        self.assertEqual(program, "C:/Program Files/Comic Stitcher/ComicStitcher.exe")
        self.assertEqual(arguments, [str(Path("C:/Comics/a b.cbz"))])

    def test_an_appimage_starts_the_appimage_file_not_its_mounted_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "Comic Stitcher.AppImage"
            image.write_text("")
            image.chmod(0o755)
            program, arguments = launch_command(["/home/me/x.cbz"], frozen=True,
                                                executable="/tmp/.mount_abc/usr/lib/comic-stitcher/comic-stitcher",
                                                environ={"APPIMAGE": str(image)})
            self.assertEqual((program, arguments), (str(image), ["/home/me/x.cbz"]))
            image.unlink()  # Gone: the program it's running from is all there is.
            program, _ = launch_command([], frozen=True, executable="/tmp/.mount_abc/cs",
                                        environ={"APPIMAGE": str(image)})
            self.assertEqual(program, "/tmp/.mount_abc/cs")

    def test_source_run_uses_launch_script(self):
        program, arguments = launch_command(["/home/me/x.cbz"], frozen=False,
                                            executable="/usr/bin/python3", source_root="/src/cs")
        self.assertEqual(program, "/usr/bin/python3")
        self.assertEqual(arguments, [str(Path("/src/cs") / "launch.py"), "/home/me/x.cbz"])

    def test_drop_tab_prefers_the_installed_launcher(self):
        with tempfile.TemporaryDirectory() as folder:
            launcher = Path(folder) / "comic-stitcher"
            launcher.write_text("#!/bin/sh\n")
            launcher.chmod(0o755)
            command = drop_tab_command(frozen=False, executable="/venv/bin/python",
                                       environ={"COMIC_STITCHER_LAUNCHER": str(launcher)})
            if os.name == "nt":
                self.assertEqual(command[-1], DROP_TAB_FLAG)  # Windows has no launcher script.
            else:
                self.assertEqual(command, [str(launcher), DROP_TAB_FLAG])
        command = drop_tab_command(frozen=True, executable="ComicStitcher.exe",
                                   environ={"COMIC_STITCHER_LAUNCHER": "/missing/launcher"})
        self.assertEqual(command, ["ComicStitcher.exe", DROP_TAB_FLAG])

    def test_drop_tab_command_and_mode_detection(self):
        command = drop_tab_command(frozen=True, executable="ComicStitcher.exe", environ={})
        self.assertEqual(command, ["ComicStitcher.exe", DROP_TAB_FLAG])
        self.assertTrue(wants_drop_tab(command))
        self.assertTrue(wants_drop_tab(["exe", UNINSTALL_FLAG]))
        self.assertFalse(wants_drop_tab(["exe", "comic.cbz"]))
        self.assertFalse(wants_drop_tab([DROP_TAB_FLAG]))  # argv[0] is the program


class PlatformTests(unittest.TestCase):
    def test_wayland_falls_back_to_x11_unless_told_otherwise(self):
        self.assertTrue(needs_x11("linux", {"WAYLAND_DISPLAY": "wayland-0"}))
        self.assertFalse(needs_x11("linux", {"WAYLAND_DISPLAY": "wayland-0", "QT_QPA_PLATFORM": "wayland"}))
        self.assertFalse(needs_x11("linux", {"DISPLAY": ":0"}))
        self.assertFalse(needs_x11("win32", {"WAYLAND_DISPLAY": "wayland-0"}))

    def test_tab_window_is_not_mistaken_for_the_main_app_on_linux(self):
        argv = ["launch.py", DROP_TAB_FLAG]
        self.assertEqual(qt_arguments(argv, "linux"),
                         ["launch.py", "-name", LINUX_WINDOW_NAME, DROP_TAB_FLAG])
        self.assertNotEqual(LINUX_WINDOW_NAME, "comic-stitcher")
        self.assertEqual(qt_arguments(argv, "win32"), argv)

    def test_desktop_entry_quotes_paths_with_spaces(self):
        command = ["/usr/bin/python3", "/home/me/My Projects/comic stitcher/launch.py", DROP_TAB_FLAG]
        entry = linux_desktop_entry(command)
        exec_line = next(line for line in entry.splitlines() if line.startswith("Exec="))
        self.assertEqual(shlex.split(exec_line[len("Exec="):]), command)
        self.assertIn("Type=Application", entry)

    def test_linux_autostart_round_trip(self):
        with tempfile.TemporaryDirectory() as config, \
                patch.dict(support.os.environ, {"XDG_CONFIG_HOME": config}), \
                patch.object(support.sys, "platform", "linux"):
            path = linux_autostart_path()
            self.assertEqual(path, Path(config) / "autostart" / support.LINUX_DESKTOP_NAME)
            self.assertFalse(support.autostart_enabled())
            self.assertTrue(support.set_autostart(True, ["/opt/cs/comic-stitcher", DROP_TAB_FLAG]))
            self.assertIn("Exec=/opt/cs/comic-stitcher --drop-tab", path.read_text(encoding="utf-8"))
            self.assertFalse(support.set_autostart(False))
            self.assertFalse(support.set_autostart(False))  # Already off is fine.

    def test_unsupported_platform_reports_off(self):
        with patch.object(support.sys, "platform", "darwin"):
            self.assertFalse(support.autostart_supported())
            self.assertFalse(support.set_autostart(True, ["x"]))


if __name__ == "__main__":
    unittest.main()
