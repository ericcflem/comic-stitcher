"""Plain-language explanations of the file problems people actually meet.

A failed import or export used to show Python's own message ("[WinError 5]
Access is denied", "[Errno 28] No space left on device: '…'"). explain()
finds the operating-system error behind a failure (often wrapped in another
error) and says what happened and what to do; the original still goes in the
diagnostic log.

Windows limits file paths to 260 characters unless its "long paths" setting
is on. Comic Stitcher itself supports long paths, so turning the setting on
(which needs administrator permission) is all it takes; the installer offers
it, and so does the message when a path is too long.
"""
from __future__ import annotations

import errno
import os
import subprocess
from pathlib import Path

WINDOWS_PATH_LIMIT = 260
LONG_PATHS_KEY = r"SYSTEM\CurrentControlSet\Control\FileSystem"

# Windows error codes (winerror) seen on file operations.
_ACCESS_DENIED = {5, 19, 1314}               # Access denied, write-protected media, privilege.
_SHARING = {32, 33}                          # Open in another program (or locked part of it).
_DISK_FULL = {39, 112}                       # Handle disk full, disk full.
_NETWORK = {51, 53, 59, 64, 67, 121, 1231}   # Network path gone, timed out or unreachable.
_CLOUD = {362, 388, 389, 390, 391, 392, 393, 394, 395, 396, 397, 398, 399, 400, 401, 402, 403, 404}
_TOO_LONG = {206}                            # File name or extension too long.
_NOT_FOUND = {2, 3}                          # File, or folder on its path, not found.


class Problem:
    """What went wrong, in plain words; `kind` lets the window offer a fix."""

    def __init__(self, text: str, kind: str = "other"):
        self.text = text
        self.kind = kind

    def __str__(self):
        return self.text


def _os_error(exc: BaseException | None) -> OSError | None:
    """The operating-system error behind a failure, through the errors that wrap it."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, OSError) and (exc.errno is not None or getattr(exc, "winerror", None)):
            return exc
        exc = exc.__cause__ or exc.__context__
    return None


def _where(error: OSError) -> str:
    name = error.filename2 or error.filename
    return str(name) if name else ""


def _drive(path: str) -> str:
    if not path:
        return "the drive"
    anchor = Path(path).anchor
    if os.name == "nt" and anchor:
        return f"drive {anchor.rstrip(chr(92))}" if not anchor.startswith("\\\\") else "the network drive"
    return f"the drive holding {Path(path).parent}"


def windows_path_too_long(path: str) -> bool:
    return os.name == "nt" and len(str(path)) >= WINDOWS_PATH_LIMIT and not long_paths_enabled()


def explain(exc: BaseException) -> Problem | None:
    """A plain explanation of a file problem behind `exc`, or None when it
    isn't one (the original message is then the best there is)."""
    error = _os_error(exc)
    if error is None:
        return None
    code, winerror = error.errno, getattr(error, "winerror", None)
    path = _where(error)
    where = f"\n\n{path}" if path else ""
    if winerror in _TOO_LONG or code == errno.ENAMETOOLONG or (
            (winerror in _NOT_FOUND or code == errno.ENOENT) and path and windows_path_too_long(path)):
        return Problem("This file's path is longer than Windows allows (260 characters), counting "
                       "its folders and the book's name. Use a shorter book name or a folder nearer "
                       "the top of the drive, or allow long paths in Windows." + where, "long path")
    if winerror in _DISK_FULL or code == errno.ENOSPC:
        return Problem(f"There isn't enough free space on {_drive(path)}. Free some space, or "
                       "choose fewer or smaller copies (a reduced size only, say), and try again." + where,
                       "disk full")
    if winerror in _SHARING:
        return Problem("A file Comic Stitcher needs is open in another program, such as a comic "
                       "reader or an image viewer. Close it there and try again." + where, "in use")
    if winerror in _CLOUD:
        return Problem("This file is in OneDrive (or another cloud folder) and isn't on this computer "
                       "right now. Make it available offline (right-click it: Always keep on this "
                       "device), or make sure the cloud app is running, and try again." + where, "cloud")
    if winerror in _NETWORK:
        return Problem("The network location can't be reached. Check the connection to it, or copy "
                       "the comic to this computer first." + where, "network")
    if winerror in _ACCESS_DENIED or code in (errno.EACCES, errno.EPERM, errno.EROFS):
        protected = (" Windows' Controlled folder access, part of Windows Security, can also keep "
                     "programs it doesn't know out of folders such as Documents and Pictures."
                     if os.name == "nt" else "")
        return Problem("Comic Stitcher isn't allowed to change this location: it may be read-only, "
                       "on a disc, or in a protected folder." + protected +
                       " Choose another folder, or copy the comic somewhere you can save, and try again."
                       + where, "access")
    if winerror in _NOT_FOUND or code == errno.ENOENT:
        return Problem("A file or folder Comic Stitcher was using is gone: it may have been moved, "
                       "renamed or deleted, or its drive disconnected, while Comic Stitcher was "
                       "working. Check it's still there and try again." + where, "missing")
    return None


def message_for(exc: BaseException) -> tuple[str, str]:
    """The message to show for a failure, and its kind."""
    problem = explain(exc)
    return (problem.text, problem.kind) if problem else (str(exc), "other")


def cannot_write(folder: Path) -> Problem | None:
    """Why nothing can be saved in `folder`, found by saving (and removing) a
    small file there; None when it can. Read-only folders, discs, folders
    Windows' Controlled folder access protects, and folders that are gone all
    show up this way, before any work is done."""
    import tempfile
    try:
        with tempfile.NamedTemporaryFile(dir=folder, prefix=".comic-stitcher-check-"):
            pass
    except OSError as exc:
        return explain(exc) or Problem(str(exc))
    return None


def long_paths_enabled() -> bool:
    """Whether Windows' long path setting is on (always True elsewhere)."""
    if os.name != "nt":
        return True
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, LONG_PATHS_KEY) as key:
            return winreg.QueryValueEx(key, "LongPathsEnabled")[0] == 1
    except OSError:
        return False


def enable_long_paths() -> bool:
    """Turn on Windows' long path setting. Windows asks for administrator
    permission first; True if the setting is on afterwards."""
    if os.name != "nt" or long_paths_enabled():
        return long_paths_enabled()
    arguments = f'add "HKLM\\{LONG_PATHS_KEY}" /v LongPathsEnabled /t REG_DWORD /d 1 /f'
    command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
               f"Start-Process -FilePath reg.exe -ArgumentList '{arguments}' -Verb RunAs -WindowStyle Hidden -Wait"]
    try:
        subprocess.run(command, check=False, timeout=300, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return False
    return long_paths_enabled()
