"""PyInstaller entry point; also runs with `python launch.py`."""
from multiprocessing import freeze_support

freeze_support()
import os
import sys

if getattr(sys, "frozen", False) and sys.platform.startswith("linux"):
    # The frozen app's own libraries are found through LD_LIBRARY_PATH, which its
    # starter sets (keeping the user's in LD_LIBRARY_PATH_ORIG). Programs it
    # starts, such as the file manager opening a folder, mustn't inherit it: the
    # system's own programs fail with the app's copies of shared libraries. This
    # process already has its libraries, so putting the original back is safe.
    original = os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
    if original is not None:
        os.environ["LD_LIBRARY_PATH"] = original
    else:
        os.environ.pop("LD_LIBRARY_PATH", None)
from comic_stitcher.diagnostics import install_diagnostics

install_diagnostics()
from comic_stitcher.drop_tab_support import wants_drop_tab

if wants_drop_tab(sys.argv):
    # The edge tab skips the main window's imports to stay light.
    from comic_stitcher.drop_tab import main
else:
    from comic_stitcher.gui import main

if __name__ == "__main__":
    raise SystemExit(main())
