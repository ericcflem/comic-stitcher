"""PyInstaller entry point; also runs with `python launch.py`."""
from multiprocessing import freeze_support

freeze_support()
from comic_builder.diagnostics import install_diagnostics

install_diagnostics()
import sys
from comic_builder.drop_tab_support import wants_drop_tab

if wants_drop_tab(sys.argv):
    # The edge tab skips the main window's imports to stay light.
    from comic_builder.drop_tab import main
else:
    from comic_builder.gui import main

if __name__ == "__main__":
    raise SystemExit(main())
