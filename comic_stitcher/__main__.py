from multiprocessing import freeze_support

freeze_support()
from .diagnostics import install_diagnostics

install_diagnostics()
import sys
from .drop_tab_support import wants_drop_tab

if wants_drop_tab(sys.argv):
    # The edge tab skips the main window's imports to stay light.
    from .drop_tab import main
else:
    from .gui import main

if __name__ == "__main__":
    raise SystemExit(main())
