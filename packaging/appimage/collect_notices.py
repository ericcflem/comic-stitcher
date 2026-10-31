"""Collect the AppImage's third-party notices, as package_licenses.py does for Windows.

    python packaging/appimage/collect_notices.py APP_FOLDER DOWNLOADS

APP_FOLDER is the frozen app (usr/lib/comic-stitcher in the AppDir); DOWNLOADS
is where fetch_tools.py put UnRAR, cwebp and the AppImage runtime's license.
Writes APP_FOLDER/licenses, with index.html (the About window's "View
third-party notices") and build-inventory.json. Run in the build's Python.

Besides the Python packages and Qt, an AppImage carries some of the build
machine's own libraries (X11 and the like, which not every desktop has):
each one's Ubuntu package and copyright file are included. A bundled library
that is neither from a Python package nor from an Ubuntu package stops the
build, so nothing goes out without its notice.
"""
from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import json
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
from package_licenses import (QT_PACKAGES, frozen_distributions, license_label,  # noqa: E402
                              notice_member, runtime_distributions, vendor_metadata, write_index)

EXECUTABLE = "comic-stitcher"
# auditwheel's renamed copies (libjpeg-31e2ca52.so...) always come from a wheel.
_WHEEL_COPY = re.compile(r"-[0-9a-f]{8}\.so")


def _copy_notices(dist, target: Path, key: str) -> list[str]:
    copied = []
    for item in dist.files or []:
        if not notice_member(item):
            continue
        if item.is_absolute() or ".." in item.parts:
            raise RuntimeError(f"Unsafe notice path in {key}: {item}")
        source = Path(dist.locate_file(item))
        if not source.is_file():
            continue
        output = target / key / Path(*item.parts)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, output)
        copied.append(output.relative_to(target).as_posix())
    return copied


def python_packages(app: Path, target: Path) -> tuple[list[dict], dict]:
    packages = []
    distributions = runtime_distributions(ROOT / "requirements.txt")
    frozen, inventory = frozen_distributions(app, EXECUTABLE)
    if not inventory["inspected"]:
        raise RuntimeError(f"{app / EXECUTABLE} wasn't found to inspect.")
    distributions.update(frozen)
    for key, dist in sorted(distributions.items()):
        copied = _copy_notices(dist, target, key)
        if not copied and key not in QT_PACKAGES:
            raise RuntimeError(f"No license notice was found for {key}; inspect its exact wheel.")
        info = target / key / "PACKAGE-METADATA.txt"
        info.parent.mkdir(parents=True, exist_ok=True)
        info.write_text(dist.read_text("METADATA") or "", encoding="utf-8")
        packages.append({"name": dist.metadata["Name"], "version": dist.version, "license": license_label(dist),
                         "notices": copied, "vendor_metadata": vendor_metadata(dist)})
    return packages, inventory


def build_tools(target: Path) -> list[dict]:
    rows = []
    for tool in ("pyinstaller", "pyinstaller-hooks-contrib"):
        dist = metadata.distribution(tool)
        copied = _copy_notices(dist, target, tool)
        if not copied:
            raise RuntimeError(f"No embedded-runtime license notice was found for {tool}.")
        rows.append({"name": dist.metadata["Name"], "version": dist.version, "license": license_label(dist),
                     "notices": copied, "role": "build tool and potentially embedded runtime code"})
    return rows


def wheel_file_names() -> set[str]:
    names = set()
    for dist in metadata.distributions():
        names.update(Path(item).name for item in dist.files or [])
    return names


def _dpkg(*arguments: str) -> str:
    return subprocess.run(["dpkg-query", *arguments], capture_output=True, text=True, check=False).stdout


def ubuntu_package(library: str) -> str | None:
    """The Ubuntu package a bundled library came from, if one did."""
    for line in _dpkg("-S", f"*/{library}").splitlines():
        package, _, path = line.partition(": ")
        if path.rsplit("/", 1)[-1] == library:
            return package.split(":")[0].split(",")[0].strip()
    return None


def system_libraries(app: Path, target: Path) -> list[dict]:
    """Notices for the build machine's libraries in the bundle."""
    from_wheels = wheel_file_names()
    packages: dict[str, list[str]] = {}
    unknown = []
    for library in sorted({path.name for path in app.rglob("*.so*") if path.is_file() or path.is_symlink()}):
        if (library in from_wheels or _WHEEL_COPY.search(library) or library.startswith("libpython3")
                or ".cpython-3" in library):  # Python itself and its own modules: python/LICENSE.txt.
            continue
        package = ubuntu_package(library)
        if package is None:
            unknown.append(library)
        else:
            packages.setdefault(package, []).append(library)
    if unknown:
        raise RuntimeError("These bundled libraries are from neither a Python package nor an Ubuntu "
                           "package, so their notices can't be found: " + ", ".join(unknown))
    rows = []
    release = platform.freedesktop_os_release().get("PRETTY_NAME", "Ubuntu")
    for package, libraries in sorted(packages.items()):
        copyright_file = Path("/usr/share/doc") / package / "copyright"
        if not copyright_file.is_file():
            raise RuntimeError(f"{package} (for {', '.join(libraries)}) has no copyright file.")
        output = target / "system" / package / "copyright"
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(copyright_file, output)
        version = _dpkg("-W", "-f", "${Version}", package).strip()
        rows.append({"name": f"{package} ({release})", "version": version,
                     "license": "See its copyright file", "notices": [output.relative_to(target).as_posix()],
                     "role": "Library from the build machine: " + ", ".join(libraries),
                     "source": f"Ubuntu source package for {package} {version}"})
    return rows


def tools(app: Path, downloads: Path, target: Path) -> list[dict]:
    record = json.loads((HERE / "tools.json").read_text(encoding="utf-8"))
    rows = []
    unrar = target / "unrar"
    unrar.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "vendor" / "license.txt", unrar / "license.txt")
    rows.append({"name": "UnRAR", "version": record["unrar"]["version"], "license": "UnRAR freeware license",
                 "notices": ["unrar/license.txt"], "role": "RAR extraction; separate executable (unrar)"})
    webp = target / "libwebp"
    webp.mkdir(parents=True, exist_ok=True)
    for name in ("COPYING", "PATENTS"):
        shutil.copyfile(ROOT / "vendor" / "libwebp" / name, webp / f"{name}.txt")
    rows.append({"name": "libwebp cwebp", "version": record["cwebp"]["version"], "license": "BSD-3-Clause",
                 "notices": ["libwebp/COPYING.txt", "libwebp/PATENTS.txt"],
                 "role": "WebP encoder run for sharp color conversion; separate executable (cwebp)"})
    runtime = target / "appimage-runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(downloads / "runtime-LICENSE", runtime / "LICENSE")
    (runtime / "ABOUT.txt").write_text(record["runtime"]["license"] + "\n", encoding="utf-8")
    rows.append({"name": "AppImage type2-runtime", "version": record["runtime"]["version"],
                 "license": "MIT; includes LGPL-2.1 libfuse", "notices": ["appimage-runtime/LICENSE",
                                                                         "appimage-runtime/ABOUT.txt"],
                 "role": "Starts the AppImage file and mounts its contents"})
    for name in ("unrar", "cwebp"):
        if not (app / name).is_file():
            raise RuntimeError(f"{name} isn't beside the app; stage it before collecting notices.")
    return rows


def collect(app: Path, downloads: Path) -> None:
    target = app / "licenses"
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    packages, frozen_inventory = python_packages(app, target)
    runtime_tool_notices = build_tools(target)
    shutil.copytree(ROOT / "assets" / "licenses" / "qt", target / "qt")
    from PySide6.QtCore import qVersion
    qt = qVersion()
    native = ROOT / "assets" / "licenses" / "native" / f"qt-{qt}"
    if not native.is_dir():
        raise RuntimeError(f"Native Qt notices have only been prepared for other versions, not {qt}.")
    shutil.copytree(native, target / "native" / f"qt-{qt}")
    shutil.copyfile(ROOT / "assets" / "licenses" / "Phosphor-LICENSE.txt", target / "Phosphor-LICENSE.txt")
    shutil.copyfile(ROOT / "docs" / "THIRD_PARTY_ACKNOWLEDGEMENTS.md", target / "THIRD_PARTY_ACKNOWLEDGEMENTS.md")
    shutil.copyfile(ROOT / "LICENSE", target / "ComicStitcher-MIT.txt")
    python_license = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError(f"Python's LICENSE.txt wasn't found at {python_license}.")
    (target / "python").mkdir()
    shutil.copyfile(python_license, target / "python" / "LICENSE.txt")
    packages.append({"name": "Python", "version": platform.python_version(),
                     "license": "PSF-2.0 and incorporated notices", "notices": ["python/LICENSE.txt"]})
    packages += system_libraries(app, target)
    runtime_tool_notices += tools(app, downloads, target)
    data = {"platform": platform.platform(), "python": platform.python_version(), "qt": qt,
            "packages": packages, "runtime_tool_notices": runtime_tool_notices,
            "frozen_code": frozen_inventory, "release_clearance": False,
            "application_license": {"id": "MIT", "notice": "ComicStitcher-MIT.txt"},
            "binaries": [{"path": path.relative_to(app).as_posix(),
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                         for path in sorted(app.rglob("*"))
                         if path.is_file() and not path.is_symlink()
                         and (".so" in path.name or path.name in (EXECUTABLE, "unrar", "cwebp"))],
            "build_tools": {"PyInstaller": metadata.version("pyinstaller"),
                            "pyinstaller-hooks-contrib": metadata.version("pyinstaller-hooks-contrib")}}
    (target / "build-inventory.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    write_index(target)
    print(f"Collected notices for {len(packages)} components and {len(runtime_tool_notices)} tools in {target}.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    collect(Path(sys.argv[1]), Path(sys.argv[2]))
