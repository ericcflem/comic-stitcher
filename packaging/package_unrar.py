"""Stage an externally supplied official UnRAR executable and its matching notice."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def main(executable: Path, license_file: Path, destination: Path) -> None:
    if executable.name.casefold() != "unrar.exe":
        raise ValueError("Supply the extracted unrar.exe, not the unrarw64.exe download/self-extractor.")
    if not executable.is_file() or executable.read_bytes()[:2] != b"MZ":
        raise ValueError("Supply the official Windows unrar.exe (a Windows PE executable).")
    if os.name == "nt":
        try:
            usage = subprocess.run([str(executable.resolve())], stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, timeout=8, check=False).stdout
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("unrar.exe did not respond as a command-line tool.") from exc
        if b"UNRAR" not in usage.upper() or b"ARCHIVE" not in usage.upper():
            raise ValueError("That unrar.exe did not show the expected UnRAR command-line help.")
    if not license_file.is_file():
        raise ValueError("Supply the UnRAR license.txt from the same official download.")
    notice = license_file.read_text(encoding="utf-8-sig", errors="replace")
    if "UnRAR" not in notice or "distribution" not in notice.lower():
        raise ValueError("That file does not look like an UnRAR license notice.")
    if not (destination / "ComicStitcher.exe").is_file():
        raise ValueError("Build the Windows app before staging UnRAR.")
    target = destination / "licenses" / "unrar"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(executable, destination / "unrar.exe")
    shutil.copyfile(license_file, target / "license.txt")
    digest = hashlib.sha256((destination / "unrar.exe").read_bytes()).hexdigest()
    (target / "binary-sha256.txt").write_text(f"unrar.exe SHA-256: {digest}\n", encoding="utf-8")
    from package_licenses import write_index
    inventory = destination / "licenses" / "build-inventory.json"
    if inventory.is_file():
        data = json.loads(inventory.read_text(encoding="utf-8"))
        data['external_tools'] = [{'name': 'UnRAR', 'path': 'unrar.exe', 'sha256': digest,
                                   'notice': 'unrar/license.txt', 'provenance_verified': False}]
        data['binaries'] = [entry for entry in data.get('binaries', []) if entry['path'].casefold() != 'unrar.exe']
        data['binaries'].append({'path': 'unrar.exe', 'sha256': digest})
        inventory.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        write_index(destination / "licenses")
    print(f"Staged UnRAR and license; executable SHA-256: {digest}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("Usage: python package_unrar.py PATH_TO_UNRAR_EXE PATH_TO_LICENSE_TXT DIST_FOLDER")
    main(*(Path(argument) for argument in sys.argv[1:]))
