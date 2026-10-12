"""Check the result of linux/install.sh or uninstall.sh at GitHub.

    python3 .github/check_linux_install.py installed
    xvfb-run -a python3 .github/check_linux_install.py windows
    python3 .github/check_linux_install.py removed
"""
import os
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
DATA = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local" / "share")
PREFIX = DATA / "comic-stitcher"
APP, VENV, LAUNCHER = PREFIX / "app", PREFIX / "venv", PREFIX / "comic-stitcher"
DESKTOP = DATA / "applications" / "comic-stitcher.desktop"
DROP_TAB_DESKTOP = DATA / "applications" / "comic-stitcher-drop-tab.desktop"
ICON = DATA / "icons" / "hicolor" / "256x256" / "apps" / "comic-stitcher.png"
BIN_LINK = HOME / ".local" / "bin" / "comic-stitcher"
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")
SETTINGS = CONFIG / "ComicStitcher"
DROP_TAB_AUTOSTART = CONFIG / "autostart" / "comic-stitcher-drop-tab.desktop"

failures = []


def annotate(title, text):
    """Show text on the pull request (GitHub workflow annotation)."""
    text = text[-3000:].replace("%", "%25").replace("\r", "").replace("\n", "%0A")
    print(f"::error title={title}::{text}")


def check(condition, message, details=""):
    print(("ok    " if condition else "FAIL  ") + message)
    if not condition:
        failures.append(message)
        annotate("Installer check failed", message + ("\n" + details if details else ""))


def venv_python(code):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    try:
        return subprocess.run([str(VENV / "bin" / "python"), "-c", code, str(APP)],
                              env=env, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired as exc:
        out = lambda data: data.decode(errors="replace") if isinstance(data, bytes) else (data or "")
        return subprocess.CompletedProcess(exc.cmd, 1, out(exc.stdout), out(exc.stderr) + "\n(timed out)")


def installed():
    check(os.access(LAUNCHER, os.X_OK), f"launcher is executable: {LAUNCHER}")
    check(BIN_LINK.is_symlink() and BIN_LINK.resolve() == LAUNCHER.resolve(), "~/.local/bin link points at the launcher")
    check(not list(APP.glob("comic_builder/test_*.py")), "tests are not copied into the installed app")
    check((PREFIX / "uninstall.sh").is_file(), "uninstall.sh is kept beside the installation")
    check(ICON.is_file(), "256 px menu icon installed")

    text = DESKTOP.read_text(encoding="utf-8") if DESKTOP.is_file() else ""
    check("Exec=" in text and str(LAUNCHER) in text, "desktop entry starts the launcher")
    check("application/vnd.comicbook+zip" in text, "desktop entry offers Open With for comics")
    check("inode/directory" not in text, "desktop entry does not claim folders")
    result = subprocess.run(["desktop-file-validate", str(DESKTOP)], capture_output=True, text=True)
    print(result.stdout + result.stderr, end="")
    check(result.returncode == 0, "desktop-file-validate passes", result.stdout + result.stderr)
    tab = DROP_TAB_DESKTOP.read_text(encoding="utf-8") if DROP_TAB_DESKTOP.is_file() else ""
    check("--drop-tab" in tab, "drop tab app-menu entry starts the tab")
    autostart = DROP_TAB_AUTOSTART.read_text(encoding="utf-8") if DROP_TAB_AUTOSTART.is_file() else ""
    check(str(LAUNCHER) in autostart and "--drop-tab" in autostart,
          "a new install starts the drop tab at sign-in, through the launcher", autostart)
    tab_result = subprocess.run(["desktop-file-validate", str(DROP_TAB_DESKTOP)], capture_output=True, text=True)
    check(tab_result.returncode == 0, "drop tab entry passes desktop-file-validate",
          tab_result.stdout + tab_result.stderr)

    probe = venv_python(
        "import importlib.metadata as m, sys\n"
        "names = {d.metadata['Name'].lower().replace('_', '-') for d in m.distributions()}\n"
        "print('essentials' if 'pyside6-essentials' in names else 'no-essentials')\n"
        "print('addons' if 'pyside6-addons' in names else 'no-addons')\n")
    check("essentials" in probe.stdout.split(), "PySide6-Essentials is installed", probe.stdout + probe.stderr)
    check("no-addons" in probe.stdout.split(), "the large PySide6-Addons package is not installed")

    plugins = list(VENV.glob("lib/python3*/site-packages/PySide6/Qt/plugins/platforms/*.so"))
    names = {plugin.name for plugin in plugins}
    print("Qt platform plugins:", ", ".join(sorted(names)) or "(none)")
    check("libqxcb.so" in names, "the X11/XWayland plugin is present", ", ".join(sorted(names)))
    check(any(name.startswith("libqwayland") for name in names), "a native Wayland plugin is present",
          ", ".join(sorted(names)))
    xcb = [plugin for plugin in plugins if plugin.name == "libqxcb.so"]
    if xcb:
        linked = subprocess.run(["ldd", str(xcb[0])], capture_output=True, text=True).stdout
        missing = [line.split()[0] for line in linked.splitlines() if "not found" in line]
        check(not missing, "every library the X11/XWayland plugin needs is installed", " ".join(missing))

    start = venv_python(
        "import sys; sys.path.insert(0, sys.argv[1])\n"
        "from PySide6.QtWidgets import QApplication\n"
        "app = QApplication([])\n"
        "from comic_builder.gui import ComicWindow\n"
        "ComicWindow.show_startup_notice = lambda self: None  # A first-run dialog waits for a click.\n"
        "window = ComicWindow(); window.show(); app.processEvents(); window.close()\n"
        "print('started')\n")
    print(start.stdout + start.stderr, end="")
    check("started" in start.stdout, "the installed app starts with the trimmed Qt", start.stdout + start.stderr)


def repair():
    """Break the private environment and check the launcher offers a repair."""
    import tempfile
    import time
    saved = VENV.with_name("venv.saved")
    VENV.rename(saved)  # As if a system upgrade removed the Python it was built on.
    try:
        with tempfile.TemporaryDirectory() as folder:
            fake = Path(folder)
            log = fake / "calls.log"
            for name, body in (("zenity", 'echo "zenity $*" >> "$LOG"; exit "${ANSWER:-1}"'),
                               ("xdg-terminal-exec", 'echo "terminal $*" >> "$LOG"')):
                (fake / name).write_text("#!/bin/sh\n" + body + "\n")
                (fake / name).chmod(0o755)
            for answer, label in (("1", "Not now"), ("0", "Repair now")):
                log.unlink(missing_ok=True)
                env = dict(os.environ, PATH=f"{fake}:{os.environ['PATH']}", DISPLAY=":99",
                           LOG=str(log), ANSWER=answer)
                env.pop("WAYLAND_DISPLAY", None)
                result = subprocess.run([str(LAUNCHER)], env=env, capture_output=True, text=True, timeout=60)
                deadline = time.monotonic() + 5
                while answer == "0" and "terminal" not in (log.read_text() if log.exists() else "") \
                        and time.monotonic() < deadline:
                    time.sleep(0.1)
                calls = log.read_text() if log.exists() else ""
                print(f"[{label}] exit={result.returncode}\n{calls}{result.stderr}", end="")
                check("Repair now" in calls and "Not now" in calls,
                      f"{label}: a broken environment shows the repair question", calls + result.stderr)
                check(result.returncode != 0, f"{label}: the launcher reports that the app did not start")
                if answer == "0":
                    check(f"terminal bash {APP}/linux/install.sh" in calls,
                          "Repair now: the kept installer runs in a terminal", calls)
                else:
                    check("terminal" not in calls, "Not now: nothing else happens", calls)
                check("To repair, run:" in result.stderr, f"{label}: the repair command is printed too")
    finally:
        saved.rename(VENV)


def window_names():
    """Window names (ICCCM instance names) on the X display, from xwininfo."""
    import re
    tree = subprocess.run(["xwininfo", "-root", "-tree"], capture_output=True, text=True).stdout
    return set(re.findall(r'\("([^"]*)" "[^"]*"\)', tree)), tree


def windows():
    """The main window and the drop tab must not share a window name.

    The desktop matches windows to app-menu entries by name. If the drop tab used
    the main app's name, a showing tab would make the desktop think Comic
    Stitcher was already open, and the app menu would not start it.
    """
    import time
    expected = (("main window", [], "comic-stitcher"),
                ("drop tab", ["--drop-tab"], "comic-stitcher-drop-tab"))
    for label, arguments, name in expected:
        env = dict(os.environ)
        env.pop("WAYLAND_DISPLAY", None)
        env.pop("QT_QPA_PLATFORM", None)
        process = subprocess.Popen([str(LAUNCHER), *arguments], env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        names, tree = set(), ""
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and process.poll() is None:
            names, tree = window_names()
            if name in names:
                break
            time.sleep(0.5)
        process.terminate()
        try:
            output = process.communicate(timeout=10)[0]
        except subprocess.TimeoutExpired:
            process.kill()
            output = process.communicate()[0]
        print(f"[{label}] window names: {sorted(names)}")
        check(name in names, f"{label} uses the window name {name}", tree[-1500:] + "\n" + (output or ""))
        if label == "drop tab":
            check("comic-stitcher" not in names,
                  "drop tab does not use the main app's window name", tree[-1500:])
    entry = DROP_TAB_DESKTOP.read_text(encoding="utf-8") if DROP_TAB_DESKTOP.is_file() else ""
    check("StartupWMClass=comic-stitcher-drop-tab" in entry,
          "drop tab app-menu entry is matched to the tab's window", entry)


def removed():
    check(not PREFIX.exists(), f"{PREFIX} removed")
    check(not DESKTOP.exists(), "desktop entry removed")
    check(not DROP_TAB_DESKTOP.exists(), "drop tab entry removed")
    check(not DROP_TAB_AUTOSTART.exists(), "drop tab sign-in entry removed")
    check(not ICON.exists(), "menu icon removed")
    check(not BIN_LINK.exists() and not BIN_LINK.is_symlink(), "~/.local/bin link removed")
    check(not SETTINGS.exists(), "settings removed by --purge")


if __name__ == "__main__":
    {"installed": installed, "repair": repair, "windows": windows,
     "removed": removed}[sys.argv[1]]()
    if failures:
        sys.exit(f"{len(failures)} check(s) failed")
