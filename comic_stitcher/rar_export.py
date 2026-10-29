"""CBR export through the user's own RAR program.

Only RARLAB's RAR may create RAR archives, so Comic Stitcher bundles nothing
for it: CBR is offered when RAR is installed (WinRAR's console Rar.exe on
Windows, RARLAB's rar elsewhere) or the user points to it in Settings. The free
UnRAR, which Comic Stitcher uses for import, can only extract.

RAR 7 and later make only the RAR5 format; RAR 5 and 6 can also make the older
RAR4 format, which some comic readers need.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

MINIMUM_VERSION = (5, 0)  # UTF-8 list files (-scfl) and the RAR5 format.
RAR4_LAST_VERSION = (6, 99)  # RAR 7.00 stopped making RAR4 archives.
_BANNER = re.compile(r"^\s*RAR\s+(\d+)\.(\d+)", re.MULTILINE)


@dataclass(frozen=True)
class RarProgram:
    path: str
    version: tuple[int, int]

    @property
    def can_make_rar4(self) -> bool:
        return self.version <= RAR4_LAST_VERSION

    @property
    def label(self) -> str:
        return f"RAR {self.version[0]}.{self.version[1]:02d}"


def _hidden_window() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def _registry_folders() -> list[Path]:
    """WinRAR's install folders, as its installer records them."""
    if os.name != "nt":
        return []
    import winreg
    folders = []
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(root, r"SOFTWARE\WinRAR", 0, winreg.KEY_READ | view) as key:
                    for name in ("exe64", "exe32"):
                        try:
                            folders.append(Path(winreg.QueryValueEx(key, name)[0]).parent)
                        except OSError:
                            pass
            except OSError:
                pass
    return folders


def candidates() -> list[str]:
    """Where RAR usually is: WinRAR's folder on Windows, the PATH elsewhere."""
    found = []
    if os.name == "nt":
        folders = _registry_folders()
        for variable in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            if os.environ.get(variable):
                folders.append(Path(os.environ[variable]) / "WinRAR")
        found += [str(folder / "Rar.exe") for folder in folders]
    for name in (("Rar.exe",) if os.name == "nt" else ("rar",)):
        installed = shutil.which(name)
        if installed:
            found.append(installed)
    return list(dict.fromkeys(found))


def identify(path: str | os.PathLike) -> RarProgram | None:
    """The program at `path` if it is RARLAB's RAR, recent enough to use. UnRAR
    (which can't create archives) and WinRAR.exe (a window, not a console
    program) aren't."""
    path = str(path)
    if not path or not Path(path).is_file():
        return None
    try:
        result = subprocess.run([path], capture_output=True, timeout=15, check=False, stdin=subprocess.DEVNULL,
                                **_hidden_window())
    except (OSError, subprocess.SubprocessError):
        return None
    match = _BANNER.search((result.stdout + result.stderr).decode(errors="replace"))
    if not match:
        return None
    version = (int(match.group(1)), int(match.group(2)))
    return RarProgram(path, version) if version >= MINIMUM_VERSION else None


def find_rar(chosen: str | None = None) -> RarProgram | None:
    """The RAR to use: the one chosen in Settings, else one found where it usually is."""
    if chosen:
        return identify(chosen)
    for candidate in candidates():
        program = identify(candidate)
        if program:
            return program
    return None


def write_cbr(program: str, folder: Path, names: list[str], archive: Path, rar4: bool = False) -> None:
    """Store `names` (files in `folder`, in reading order) in a new RAR archive,
    uncompressed like a CBZ, then test it. Raises ValueError if RAR fails."""
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".lst", delete=False) as listing:
        listing.write("\n".join(names) + "\n")
    try:
        # -cfg- ignores RAR's own settings and the RAR environment variable, so a
        # personal default (a password, a comment, compression) can't change the
        # comic. -m0 stores the already-compressed images; -ep keeps names bare.
        command = [program, "a", "-cfg-", "-idq", "-y", "-m0", "-ep", "-ma4" if rar4 else "-ma5",
                   "-scfl", "--", str(archive), f"@{listing.name}"]
        _run(command, folder, "make")
    finally:
        Path(listing.name).unlink(missing_ok=True)
    _run([program, "t", "-cfg-", "-idq", "-y", "--", str(archive)], folder, "check")
    listed = _run([program, "lb", "-cfg-", "--", str(archive)], folder, "list")
    if len([line for line in listed.splitlines() if line.strip()]) != len(names):
        raise ValueError("The CBR made by RAR doesn't hold every page.")


def _run(command: list[str], folder: Path, step: str) -> str:
    try:
        result = subprocess.run(command, cwd=folder, capture_output=True, timeout=3600, check=False,
                                stdin=subprocess.DEVNULL, **_hidden_window())
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"RAR couldn't {step} the CBR: {exc}") from exc
    output = (result.stdout + result.stderr).decode(errors="replace").strip()
    if result.returncode:
        detail = output.splitlines()[-1] if output else f"exit code {result.returncode}"
        raise ValueError(f"RAR couldn't {step} the CBR ({detail}).")
    return output
