"""Verify the Windows candidate excludes QtPDF while keeping PDFium."""
import sys
from pathlib import Path

from build_policy import validate_qt_bundle

root = Path(sys.argv[1] if len(sys.argv) > 1 else "dist/ComicStitcher")
if not (root / "ComicStitcher.exe").is_file():
    raise SystemExit("Missing candidate executable")
validate_qt_bundle(root)
if not any(p.name.casefold() == "pdfium.dll" for p in root.rglob("*.dll")):
    raise SystemExit("Missing PDFium rendering library")
print("PASS: QtPDF excluded; PDFium rendering library retained.")
