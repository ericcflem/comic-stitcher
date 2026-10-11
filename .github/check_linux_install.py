"""Check the result of linux/install.sh or uninstall.sh at GitHub.

    python3 .github/check_linux_install.py installed
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
ICON = DATA / "icons" / "hicolor" / "256x256" / "apps" / "comic-stitcher.png"
BIN_LINK = HOME / ".local" / "bin" / "comic-stitcher"
SETTINGS = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / "ComicStitcher"

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


def removed():
    check(not PREFIX.exists(), f"{PREFIX} removed")
    check(not DESKTOP.exists(), "desktop entry removed")
    check(not ICON.exists(), "menu icon removed")
    check(not BIN_LINK.exists() and not BIN_LINK.is_symlink(), "~/.local/bin link removed")
    check(not SETTINGS.exists(), "settings removed by --purge")


if __name__ == "__main__":
    {"installed": installed, "removed": removed}[sys.argv[1]]()
    if failures:
        sys.exit(f"{len(failures)} check(s) failed")
