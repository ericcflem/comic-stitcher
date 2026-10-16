"""Stage Google's official cwebp.exe (libwebp) beside the Windows app, with its notices.

cwebp encodes lossy WebP pages when sharp color conversion (sharp YUV) is on;
Pillow, which encodes everything else, doesn't offer that option.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main(executable: Path, destination: Path) -> None:
    record = json.loads((ROOT / "vendor" / "CWEBP-PROVENANCE.json").read_text(encoding="utf-8"))
    if executable.name.casefold() != "cwebp.exe" or not executable.is_file() \
            or executable.read_bytes()[:2] != b"MZ":
        raise ValueError("Supply bin\\cwebp.exe from Google's libwebp Windows download.")
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    expected = record.get("executable_sha256")
    if expected and digest != expected:
        raise ValueError(f"cwebp.exe is not the recorded libwebp {record['version']} build "
                         f"(SHA-256 {digest}). Use the download in vendor/CWEBP-PROVENANCE.json.")
    if os.name == "nt":
        try:
            version = subprocess.run([str(executable.resolve()), "-version"], capture_output=True,
                                     timeout=10, check=False).stdout.decode(errors="replace").split()
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("cwebp.exe did not respond as a command-line tool.") from exc
        if not version or version[0] != record["version"]:
            raise ValueError(f"cwebp.exe reports version {version[:1]}, expected {record['version']}.")
    if not (destination / "ComicStitcher.exe").is_file():
        raise ValueError("Build the Windows app before staging cwebp.")
    notices = destination / "licenses" / "libwebp"
    notices.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(executable, destination / "cwebp.exe")
    for name in ("COPYING", "PATENTS"):
        shutil.copyfile(ROOT / "vendor" / "libwebp" / name, notices / f"{name}.txt")
    (notices / "binary-sha256.txt").write_text(f"cwebp.exe SHA-256: {digest}\n", encoding="utf-8")
    from package_licenses import write_index
    inventory = destination / "licenses" / "build-inventory.json"
    if inventory.is_file():
        data = json.loads(inventory.read_text(encoding="utf-8"))
        tools = [tool for tool in data.get("external_tools", []) if tool.get("path") != "cwebp.exe"]
        tools.append({"name": "libwebp cwebp", "path": "cwebp.exe", "sha256": digest,
                      "notice": "libwebp/COPYING.txt", "provenance_verified": bool(expected)})
        data["external_tools"] = tools
        data["runtime_tool_notices"] = [row for row in data.get("runtime_tool_notices", [])
                                        if row.get("name") != "libwebp cwebp"]
        data["runtime_tool_notices"].append({"name": "libwebp cwebp", "version": record["version"],
                                             "license": "BSD-3-Clause"})
        data["binaries"] = [entry for entry in data.get("binaries", []) if entry["path"].casefold() != "cwebp.exe"]
        data["binaries"].append({"path": "cwebp.exe", "sha256": digest})
        inventory.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        write_index(destination / "licenses")
    print(f"Staged cwebp {record['version']} and its notices; executable SHA-256: {digest}")
    if not expected:
        print("Record this SHA-256 as executable_sha256 in vendor/CWEBP-PROVENANCE.json.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: python package_cwebp.py PATH_TO_CWEBP_EXE DIST_FOLDER")
    main(Path(sys.argv[1]), Path(sys.argv[2]))
