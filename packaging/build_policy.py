"""Fail the build when an unreviewed Qt native module enters the distribution."""
from pathlib import Path

# These are the reviewed licensing routes for the Windows widget application.
# PDF imports use pypdf/pypdfium2. Do not package the unused Qt PDF plugin.
REVIEWED_QT_DLLS = {"qt6core.dll", "qt6gui.dll", "qt6network.dll", "qt6widgets.dll",
                    "qt6svg.dll", "qt6opengl.dll"}


def validate_qt_bundle(destination: Path) -> None:
    unexpected = []
    for path in destination.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.casefold()
        if ("virtualkeyboard" in name or name in {"qpdf.dll", "qpdfd.dll"} or
                (name.startswith("qt6") and name.endswith(".dll") and name not in REVIEWED_QT_DLLS)):
            unexpected.append(path.relative_to(destination).as_posix())
    if unexpected:
        raise RuntimeError("Unreviewed Qt components in the app bundle; inspect their licensing before packaging: "
                           + ", ".join(sorted(unexpected)))
