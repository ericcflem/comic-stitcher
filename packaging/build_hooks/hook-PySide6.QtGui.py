"""Keep the standard QtGui hook, excluding unused optional plugins.

Filter before PyInstaller binary analysis so Qt PDF and Virtual Keyboard do
not pull their libraries into the bundle. App PDF imports use pypdf/pypdfium2.
"""
from pathlib import Path

from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
binaries = [(source, target) for source, target in binaries
            if "virtualkeyboard" not in Path(source).name.casefold()
            and Path(source).name.casefold() not in {"qpdf.dll", "qpdfd.dll", "libqpdf.so", "libqpdf.dylib"}]
