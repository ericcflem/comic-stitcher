"""Build the release's source package, or refresh its file list.

    python make_source_package.py
        Checks that every file in SOURCE_PACKAGE_FILES.json is present with the
        recorded size and SHA-256, then writes dist/ComicStitcher_Source_<version>.zip
        and prints its SHA-256 for the release page.

    python make_source_package.py --update
        (Maintainers, in a git checkout.) Rewrites SOURCE_PACKAGE_FILES.json from
        the files git tracks, keeping the recorded entries for the large inputs git
        does not hold: third_party_sources/*.tar.* and vendor/unrar.exe.

The library source archives and unrar.exe are not in the git repository. Copy
them from the previous release's source package (they are unchanged unless the
pinned dependencies change) into third_party_sources/ and vendor/ first.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LISTING = ROOT / "SOURCE_PACKAGE_FILES.json"
NOT_IN_GIT = ("third_party_sources/", "vendor/unrar.exe")


def version() -> str:
    text = (ROOT / "comic_builder" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            sha.update(block)
    return sha.hexdigest()


def entry(path: str) -> dict:
    full = ROOT / path
    return {"path": path, "bytes": full.stat().st_size, "sha256": digest(full)}


def update() -> None:
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True,
                             check=True).stdout.decode().split("\0")
    previous = {item["path"]: item for item in json.loads(LISTING.read_text(encoding="utf-8"))["files"]}
    files = [entry(path) for path in tracked if path and path != LISTING.name]
    files += [item for path, item in previous.items() if path.startswith(NOT_IN_GIT)]
    files.sort(key=lambda item: item["path"])
    LISTING.write_text(json.dumps({
        "scope": f"Comic Stitcher {version()} source package. Library source archives and "
                 "unrar.exe are unchanged since 0.6.38.",
        "files": files}, indent=2) + "\n", encoding="utf-8")
    print(f"Listed {len(files)} files in {LISTING.name}.")


def problems(files: list[dict]) -> list[str]:
    found = []
    for item in files:
        path = ROOT / item["path"]
        if not path.is_file():
            hint = " (copy it from the previous release's source package)" \
                if item["path"].startswith(NOT_IN_GIT) else ""
            found.append(f"missing: {item['path']}{hint}")
        elif path.stat().st_size != item["bytes"] or digest(path) != item["sha256"]:
            found.append(f"different from the recorded file: {item['path']}")
    return found


def build() -> Path:
    files = json.loads(LISTING.read_text(encoding="utf-8"))["files"]
    found = problems(files)
    if found:
        if sum(line.startswith("different") for line in found) > 5:
            found.append("Many files differ: this copy may have Windows line endings. Use the release's "
                         "Source code (zip), or a git clone made with core.autocrlf set to false.")
        sys.exit("The source package was not made:\n  " + "\n  ".join(found))
    output = ROOT / "dist" / f"ComicStitcher_Source_{version()}.zip"
    output.parent.mkdir(exist_ok=True)
    partial = output.with_suffix(".zip.part")
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in files + [{"path": LISTING.name}]:
            # Already-compressed archives are stored as they are.
            stored = item["path"].endswith((".gz", ".xz", ".zip", ".exe", ".png", ".ico"))
            archive.write(ROOT / item["path"], item["path"],
                          compress_type=zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED)
    with zipfile.ZipFile(partial) as archive:
        if archive.testzip() is not None or len(archive.namelist()) != len(files) + 1:
            sys.exit("The finished ZIP did not verify; nothing was published.")
    partial.replace(output)
    print(f"{output}\n{output.stat().st_size:,} bytes\nSHA-256 {digest(output)}")
    return output


if __name__ == "__main__":
    update() if "--update" in sys.argv[1:] else build()
