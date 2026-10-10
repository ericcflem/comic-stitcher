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

COLLAPSED_SIZE = (8, 96)
EXPANDED_SIZE = (165, 150)  # Fallback; the tab sizes itself to its icon and text.
HOVER_DELAY_MS = 600
DEFAULT_OFFSET = 200

AUTOSTART_NAME = "ComicStitcherDropTab"
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
LINUX_DESKTOP_NAME = "comic-stitcher-drop-tab.desktop"


def wants_drop_tab(argv):
    return any(argument in MODE_FLAGS for argument in argv[1:])


def clamp_offset(offset, available_height):
    """Keep the collapsed tab fully on screen."""
    try:
        offset = int(offset)
    except (TypeError, ValueError):
        offset = DEFAULT_OFFSET
    return max(0, min(offset, max(0, available_height - COLLAPSED_SIZE[1])))


def tab_rect(available, offset, expanded, expanded_size=EXPANDED_SIZE):
    """Return (x, y, width, height) for the tab on the right edge.

    ``available`` is the screen's usable area as (x, y, width, height). The
    expanded panel shares the collapsed tab's centre and right edge, so a drag
    that entered the thin tab is still inside it after it grows.
    """
    x, y, width, height = available
    tab_width, tab_height = expanded_size if expanded else COLLAPSED_SIZE
    centre = y + clamp_offset(offset, height) + COLLAPSED_SIZE[1] // 2
    top = max(y, min(centre - tab_height // 2, y + height - tab_height))
    return (x + width - tab_width, top, tab_width, tab_height)


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


def drop_tab_command(frozen=None, executable=None, source_root=None):
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
    return ("[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Comic Stitcher drop tab\n"
            "Comment=Drag comics to the right edge of the screen to open them in Comic Stitcher\n"
            f"Exec={exec_line}\n"
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
