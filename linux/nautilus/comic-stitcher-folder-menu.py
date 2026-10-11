"""Adds "Open in Comic Stitcher" to the Files (Nautilus) right-click menu for folders.

Installed by Comic Stitcher's linux/install.sh when Files' Python add-on
support (python3-nautilus / nautilus-python) is present; removed by uninstall.sh.
It runs inside Files' own Python and only starts Comic Stitcher's launcher.
"""
import subprocess

from gi.repository import GObject, Nautilus

LAUNCHER = "@LAUNCHER@"  # Filled in by install.sh.
INSTALLED_BY = "Comic Stitcher install.sh"  # uninstall.sh only removes files with this line.


def _local_folders(files):
    """Paths of the selection, or [] unless every item is a local folder."""
    paths = []
    for item in files:
        if item.get_uri_scheme() != "file" or not item.is_directory():
            return []
        location = item.get_location()
        path = location.get_path() if location is not None else None
        if not path:
            return []
        paths.append(path)
    return paths


def _open(paths):
    subprocess.Popen([LAUNCHER, *paths], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


class ComicStitcherFolderMenu(GObject.GObject, Nautilus.MenuProvider):
    def get_file_items(self, *args):
        # Files 43 and later pass (files); older versions pass (window, files).
        folders = _local_folders(args[-1])
        if not folders:
            return []
        item = Nautilus.MenuItem(name="ComicStitcher::OpenFolders",
                                 label="Open in Comic Stitcher",
                                 tip="Open the selected folders in Comic Stitcher")
        item.connect("activate", lambda _item: _open(folders))
        return [item]
