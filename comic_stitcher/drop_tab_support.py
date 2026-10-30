"""Platform helpers for the screen-edge drop tab.

Nothing here imports Qt, so the layout, launch and sign-in rules can be tested
on any machine.
"""
import os
import subprocess
import sys
from pathlib import Path

DROP_TAB_FLAG = "--drop-tab"
ENABLE_AUTOSTART_FLAG = "--enable-autostart"
UNINSTALL_FLAG = "--uninstall-drop-tab"
MODE_FLAGS = (DROP_TAB_FLAG, UNINSTALL_FLAG)

EXPANDED_SIZE = (165, 150)  # Fallback; the tab sizes itself to its icon and text.

# Adjustable in "Drop tab settings…". Pixels are Windows' scaled pixels.
DEFAULT_THICKNESS = 8
THICKNESS_RANGE = (3, 24)
DEFAULT_HEIGHT = 96
MIN_HEIGHT = 48
DEFAULT_OFFSET = 200
HOVER_DELAY_MS = 450
HOVER_RANGE = (0, 2000)
# Kept clear at the top (window controls) and bottom (an auto-hiding taskbar).
EDGE_MARGIN = 48

COLLAPSED_SIZE = (DEFAULT_THICKNESS, DEFAULT_HEIGHT)

AUTOSTART_NAME = "ComicStitcherDropTab"
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
LINUX_DESKTOP_NAME = "comic-stitcher-drop-tab.desktop"
# The tab's own window identity on Linux. If it shared the main app's
# (comic-stitcher), the desktop would treat Comic Stitcher as already open while
# the tab is showing, and clicking it in the app menu would do nothing.
LINUX_WINDOW_NAME = "comic-stitcher-drop-tab"


def wants_drop_tab(argv):
    return any(argument in MODE_FLAGS for argument in argv[1:])


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clamp(value, low, high, default):
    return max(low, min(_int(value, default), high))


def clamp_thickness(value):
    return clamp(value, *THICKNESS_RANGE, DEFAULT_THICKNESS)


def clamp_hover_delay(value):
    return clamp(value, *HOVER_RANGE, HOVER_DELAY_MS)


def max_height(available_height):
    return max(MIN_HEIGHT, available_height - 2 * EDGE_MARGIN)


def clamp_height(value, available_height):
    return clamp(value, MIN_HEIGHT, max_height(available_height), DEFAULT_HEIGHT)


def offset_range(available_height, tab_height=DEFAULT_HEIGHT):
    """Allowed distances from the top, keeping the margins clear where possible."""
    low = EDGE_MARGIN
    high = available_height - EDGE_MARGIN - tab_height
    if high < low:  # A very small screen: fall back to just staying on it.
        low, high = 0, max(0, available_height - tab_height)
    return low, high


def clamp_offset(offset, available_height, tab_height=DEFAULT_HEIGHT):
    return clamp(offset, *offset_range(available_height, tab_height), DEFAULT_OFFSET)


def tab_rect(available, offset, expanded, expanded_size=EXPANDED_SIZE,
             collapsed_size=COLLAPSED_SIZE, anchor_y=None):
    """Return (x, y, width, height) for the tab on the right edge.

    ``available`` is the screen's usable area as (x, y, width, height). The
    open panel keeps the right edge. When the tab is shorter than the panel the
    panel is centred on it, so it covers the whole tab; when the tab is taller,
    the panel opens at ``anchor_y`` (the pointer) within the tab's span, so the
    pointer is inside it either way.
    """
    x, y, width, height = available
    thickness, tab_height = collapsed_size
    tab_height = clamp_height(tab_height, height)
    tab_top = y + clamp_offset(offset, height, tab_height)
    if not expanded:
        return (x + width - thickness, tab_top, thickness, tab_height)
    panel_width, panel_height = expanded_size
    centre = tab_top + tab_height // 2
    if tab_height > panel_height and anchor_y is not None:
        centre = max(tab_top + panel_height // 2,
                     min(int(anchor_y), tab_top + tab_height - panel_height // 2))
    top = max(y, min(centre - panel_height // 2, y + height - panel_height))
    return (x + width - panel_width, top, panel_width, panel_height)


def launch_command(paths=(), frozen=None, executable=None, source_root=None):
    """Return (program, arguments) that start Comic Stitcher with ``paths``.

    A running Comic Stitcher window receives the paths through its existing
    single-instance handoff, so this works whether or not it is open.
    """
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    executable = str(executable or sys.executable)
    arguments = [str(path) for path in paths]
    if frozen:
        return executable, arguments
    root = Path(source_root) if source_root else Path(__file__).resolve().parent.parent
    return executable, [str(root / "launch.py"), *arguments]


def drop_tab_command(frozen=None, executable=None, source_root=None, environ=None):
    """The command that starts the drop tab (used for starting it at sign-in).

    When started through the Linux installer's launcher, use the launcher, so
    the sign-in entry keeps working after updates rebuild the private Python.
    """
    environ = os.environ if environ is None else environ
    launcher = environ.get("COMIC_STITCHER_LAUNCHER")
    if launcher and os.access(launcher, os.X_OK):
        return [launcher, DROP_TAB_FLAG]
    program, arguments = launch_command((), frozen, executable, source_root)
    return [program, *arguments, DROP_TAB_FLAG]


def needs_x11(platform=None, environ=None):
    """Wayland will not let an ordinary window pin itself to a screen edge.

    Running the tab through XWayland keeps it in place and still accepts drags
    from Wayland file managers. An explicit QT_QPA_PLATFORM is respected.
    """
    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    return (platform.startswith("linux") and bool(environ.get("WAYLAND_DISPLAY"))
            and not environ.get("QT_QPA_PLATFORM"))


def qt_arguments(argv, platform=None):
    """Arguments for QApplication, giving the tab its own window name on Linux.

    Qt's -name option sets the X11 window name and wins over the launcher's
    RESOURCE_NAME, which is meant for the main window. Qt removes the option
    itself, and it does not reach programs the tab starts.
    """
    platform = sys.platform if platform is None else platform
    argv = list(argv)
    if platform.startswith("linux"):
        return argv[:1] + ["-name", LINUX_WINDOW_NAME] + argv[1:]
    return argv


def _desktop_quote(argument):
    if argument and not any(c in argument for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return argument
    escaped = "".join("\\" + c if c in '"`$\\' else c for c in argument)
    return '"' + escaped + '"'


def linux_autostart_path(environ=None):
    environ = os.environ if environ is None else environ
    base = environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / LINUX_DESKTOP_NAME


def linux_desktop_entry(command):
    exec_line = " ".join(_desktop_quote(argument) for argument in command)
    # TryExec: once the program is gone (an AppImage deleted, say), the desktop
    # skips the entry instead of failing to start it at every sign-in.
    try_exec = f"TryExec={command[0]}\n" if command[0].startswith("/") else ""
    return ("[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Comic Stitcher drop tab\n"
            "Comment=Drag comics to the right edge of the screen to open them in Comic Stitcher\n"
            f"Exec={exec_line}\n{try_exec}"
            "Terminal=false\n"
            "NoDisplay=true\n"
            "X-GNOME-Autostart-enabled=true\n")


def autostart_supported(platform=None):
    platform = sys.platform if platform is None else platform
    return platform == "win32" or platform.startswith("linux")


def autostart_enabled():
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY) as key:
                winreg.QueryValueEx(key, AUTOSTART_NAME)
            return True
        except OSError:
            return False
    if sys.platform.startswith("linux"):
        return linux_autostart_path().exists()
    return False


def set_autostart(enabled, command=None):
    """Start the drop tab at sign-in, or stop doing so. Returns the new state."""
    command = command or drop_tab_command()
    if sys.platform == "win32":
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY) as key:
            if enabled:
                winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(command))
            else:
                try:
                    winreg.DeleteValue(key, AUTOSTART_NAME)
                except FileNotFoundError:
                    pass
    elif sys.platform.startswith("linux"):
        path = linux_autostart_path()
        if enabled:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(linux_desktop_entry(command), encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
    else:
        return False
    return autostart_enabled()
