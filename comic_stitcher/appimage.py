"""Desktop integration when Comic Stitcher runs as a Linux AppImage.

An AppImage is one file that runs without installing, so on its own it has no
app-menu entry: no icon in the dock, no menu entry for the drop tab, no Open
With, no Files folder menu. With the user's agreement, these add what
linux/install.sh adds, all pointing at the AppImage file:

* the app-menu entry (with the icon and Open With for comics) and the drop
  tab's entry,
* the icon,
* "Open in Comic Stitcher" in the Files folder menu.

Deleting the AppImage leaves nothing that shows or runs: every entry has
TryExec, so desktops hide it once the file is gone, and the Files script
removes itself. remove_integration() takes them all out, and purge_user_data()
the settings, logs and saved reviews, for nothing left at all.

Every file written carries MARKER, and only such files are ever replaced or removed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_ID = "comic-stitcher-appimage"
MARKER = "X-ComicStitcher-AppImage=1"
SCRIPT_MARKER = "# Comic Stitcher AppImage"
MIME_TYPES = ("application/vnd.comicbook+zip;application/vnd.comicbook-rar;application/x-cb7;"
              "application/pdf;application/zip;application/vnd.rar;application/x-7z-compressed;")


def bundled_icon() -> Path:
    """The app icon inside the AppImage."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "comic-stitcher.png"


def appimage_path(environ=None) -> Path | None:
    """The AppImage file Comic Stitcher is running from, or None."""
    environ = os.environ if environ is None else environ
    value = environ.get("APPIMAGE")
    if not value or not sys.platform.startswith("linux") or not getattr(sys, "frozen", False):
        return None
    return Path(value)


def _data_home(environ=None) -> Path:
    environ = os.environ if environ is None else environ
    return Path(environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def _config_home(environ=None) -> Path:
    environ = os.environ if environ is None else environ
    return Path(environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def paths(environ=None) -> dict[str, Path]:
    data = _data_home(environ)
    return {"app": data / "applications" / f"{APP_ID}.desktop",
            "drop_tab": data / "applications" / f"{APP_ID}-drop-tab.desktop",
            "icon": data / "icons" / "hicolor" / "256x256" / "apps" / f"{APP_ID}.png",
            "script": data / "nautilus" / "scripts" / "Open in Comic Stitcher"}


def _quote(argument: str) -> str:
    """An argument for a desktop entry's Exec line (Desktop Entry spec)."""
    if argument and not any(c in argument for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return argument
    return '"' + "".join("\\" + c if c in '"`$\\' else c for c in argument) + '"'


def _ours(path: Path, marker: str = MARKER) -> bool:
    try:
        return marker in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def integrated_path(environ=None) -> Path | None:
    """The AppImage the app-menu entry points at, if Comic Stitcher added one."""
    entry = paths(environ)["app"]
    if not _ours(entry):
        return None
    for line in entry.read_text(encoding="utf-8").splitlines():
        if line.startswith("TryExec="):
            return Path(line[len("TryExec="):])
    return None


def installer_entry_exists(environ=None) -> bool:
    """Whether linux/install.sh's own menu entry is there too."""
    entry = _data_home(environ) / "applications" / "comic-stitcher.desktop"
    return _ours(entry, "X-ComicStitcher-Installer=")


def desktop_entry(appimage: Path, drop_tab: bool, name_suffix: str = "") -> str:
    program = _quote(str(appimage))
    if drop_tab:
        return ("[Desktop Entry]\nType=Application\n"
                f"Name=Comic Stitcher Drop Tab{name_suffix}\n"
                "Comment=Show a drop tab on the right edge of the screen; drop comics onto it to open them\n"
                f"Exec={program} --drop-tab\nTryExec={appimage}\nIcon={APP_ID}\nTerminal=false\n"
                "Categories=Graphics;\nKeywords=comic;drop;tab;\nStartupNotify=false\n"
                f"StartupWMClass=comic-stitcher-drop-tab\n{MARKER}\n")
    return ("[Desktop Entry]\nType=Application\n"
            f"Name=Comic Stitcher{name_suffix}\nGenericName=Comic Book Preparation\n"
            "Comment=Turn PDFs, image folders and comic archives into clean comic book files\n"
            f"Exec={program} %F\nTryExec={appimage}\nIcon={APP_ID}\nTerminal=false\n"
            "Categories=Graphics;\nKeywords=comic;cbz;cbr;cb7;pdf;manga;spread;stitch;\n"
            f"MimeType={MIME_TYPES}\nStartupNotify=true\nStartupWMClass=comic-stitcher\n{MARKER}\n")


def files_script(appimage: Path) -> str:
    """Files' right-click > Scripts > Open in Comic Stitcher. Once the AppImage is
    gone, it removes itself the next time it's chosen."""
    import shlex
    return f"""#!/bin/sh
{SCRIPT_MARKER}: opens the selected folders in Comic Stitcher.
# Added from Comic Stitcher's Settings; removed there, or by itself once the AppImage is gone.
APPIMAGE={shlex.quote(str(appimage))}
if [ ! -x "$APPIMAGE" ]; then
    rm -f "$0"
    command -v notify-send >/dev/null 2>&1 && notify-send "Comic Stitcher" \\
        "The Comic Stitcher AppImage is gone, so this menu item was removed."
    exit 0
fi
[ -n "${{NAUTILUS_SCRIPT_SELECTED_FILE_PATHS:-}}" ] || exit 0
set --
while IFS= read -r path; do
    [ -n "$path" ] && set -- "$@" "$path"
done <<EOF
$NAUTILUS_SCRIPT_SELECTED_FILE_PATHS
EOF
[ "$#" -gt 0 ] || exit 0
exec "$APPIMAGE" "$@"
"""


def _refresh_menus(applications: Path) -> None:
    """Let the desktop see new or removed entries (Open With especially)."""
    for command in (["update-desktop-database", str(applications)],
                    ["gtk-update-icon-cache", "-q", "-t", str(applications.parent / "icons" / "hicolor")]):
        if shutil.which(command[0]):
            try:
                subprocess.run(command, check=False, timeout=30,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError):
                pass


def add_integration(appimage: Path, icon_source: Path, environ=None) -> None:
    """Add (or update) the app-menu entries, icon and Files menu item."""
    where = paths(environ)
    suffix = " (AppImage)" if installer_entry_exists(environ) else ""
    for key in ("app", "drop_tab", "icon", "script"):
        where[key].parent.mkdir(parents=True, exist_ok=True)
    where["app"].write_text(desktop_entry(appimage, False, suffix), encoding="utf-8")
    where["drop_tab"].write_text(desktop_entry(appimage, True, suffix), encoding="utf-8")
    shutil.copyfile(icon_source, where["icon"])
    script = where["script"]
    if not script.exists() or _ours(script, SCRIPT_MARKER):  # Never replace install.sh's own.
        script.write_text(files_script(appimage), encoding="utf-8")
        script.chmod(0o755)
    _refresh_menus(where["app"].parent)


def remove_integration(environ=None) -> None:
    """Take out everything add_integration added (and the drop tab's sign-in
    entry, when it starts this AppImage)."""
    where = paths(environ)
    for key in ("app", "drop_tab"):
        if _ours(where[key]):
            where[key].unlink()
    if _ours(where["script"], SCRIPT_MARKER):
        where["script"].unlink()
    where["icon"].unlink(missing_ok=True)
    from .drop_tab_support import linux_autostart_path
    autostart = linux_autostart_path(environ)
    current = appimage_path(environ)
    if current is not None and autostart.exists() and str(current) in autostart.read_text(encoding="utf-8"):
        autostart.unlink()
    _refresh_menus(where["app"].parent)


def follow_move(environ=None) -> bool:
    """When the AppImage was moved since its entries were added, point them at
    its new place. True if they were updated."""
    current = appimage_path(environ)
    integrated = integrated_path(environ)
    if current is None or integrated is None or integrated == current:
        return False
    add_integration(current, bundled_icon(), environ)
    from .drop_tab_support import linux_autostart_path, set_autostart
    autostart = linux_autostart_path(environ)
    if autostart.exists() and str(integrated) in autostart.read_text(encoding="utf-8"):
        set_autostart(True, [str(current), "--drop-tab"])
    return True


def purge_user_data(environ=None) -> None:
    """Settings, logs, review reports and saved reviews, as uninstall.sh --purge
    removes them. (The remembered ComicVine key is forgotten by the caller.)"""
    environ = os.environ if environ is None else environ
    home = Path(environ.get("HOME") or Path.home())
    folders = {_config_home(environ) / "ComicStitcher",  # Settings (Qt follows XDG_CONFIG_HOME).
               home / ".local" / "share" / "ComicStitcher",  # Logs and saved reviews (diagnostics.py).
               _data_home(environ) / "ComicStitcher"}
    for folder in folders:
        shutil.rmtree(folder, ignore_errors=True)
