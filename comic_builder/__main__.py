from multiprocessing import freeze_support

freeze_support()
from .diagnostics import install_diagnostics

install_diagnostics()
from .gui import main

if __name__ == "__main__":
    raise SystemExit(main())
