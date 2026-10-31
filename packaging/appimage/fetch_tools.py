"""Download what the AppImage build uses, as recorded in tools.json.

    python packaging/appimage/fetch_tools.py DESTINATION

Each download, and each program taken out of one, must match its recorded
SHA-256; anything else stops the build. Writes DESTINATION/unrar,
DESTINATION/cwebp, DESTINATION/appimagetool, DESTINATION/runtime-x86_64 and
DESTINATION/runtime-LICENSE. Files already there and matching are kept.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = json.loads((HERE / "tools.json").read_text(encoding="utf-8"))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def download(url: str, tries: int = 4) -> bytes:
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                return response.read()
        except OSError:
            if attempt == tries - 1:
                raise
            time.sleep(5 * (attempt + 1))
    raise AssertionError("unreachable")


def checked(name: str, data: bytes, expected: str) -> bytes:
    actual = sha256(data)
    if actual != expected:
        raise SystemExit(f"{name}: SHA-256 {actual} is not the recorded {expected}; "
                         "check packaging/appimage/tools.json before changing it.")
    return data


def member(archive: bytes, name: str) -> bytes:
    import io
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        found = tar.extractfile(name)
        if found is None:
            raise SystemExit(f"{name} isn't in the download.")
        return found.read()


def write(path: Path, data: bytes, executable: bool) -> None:
    path.write_bytes(data)
    path.chmod(0o755 if executable else 0o644)
    print(f"  {path.name}: {sha256(data)}")


def main(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for key in ("unrar", "cwebp"):
        tool = TOOLS[key]
        target = destination / key
        if target.is_file() and sha256(target.read_bytes()) == tool["member_sha256"]:
            print(f"  {key}: already here")
            continue
        archive = checked(tool["url"], download(tool["url"]), tool["sha256"])
        write(target, checked(tool["member"], member(archive, tool["member"]), tool["member_sha256"]), True)
    for key, name in (("appimagetool", "appimagetool"), ("runtime", "runtime-x86_64")):
        tool = TOOLS[key]
        target = destination / name
        if target.is_file() and sha256(target.read_bytes()) == tool["sha256"]:
            print(f"  {name}: already here")
            continue
        write(target, checked(tool["url"], download(tool["url"]), tool["sha256"]), True)
    licence = destination / "runtime-LICENSE"
    if not licence.is_file():
        text = download(TOOLS["runtime"]["license_url"])
        if b"Permission is hereby granted" not in text:
            raise SystemExit("The AppImage runtime's LICENSE isn't the expected MIT license.")
        write(licence, text, False)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(Path(sys.argv[1]))
