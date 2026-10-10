"""PyInstaller entry point; also runs with `python launch.py`."""
from multiprocessing import freeze_support

freeze_support()
from comic_builder.diagnostics import install_diagnostics

install_diagnostics()
from comic_builder.gui import main

if __name__ == "__main__":
    raise SystemExit(main())
