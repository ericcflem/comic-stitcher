"""Extract image entries from comic archives to a disposable scan directory."""
from __future__ import annotations

import os
import re
import shutil
import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath

import py7zr
import rarfile

from .backend import scan_folder
from .comicinfo import MAX_XML_BYTES
from .detector import IMAGE_EXTS

ARCHIVE_EXTS = {".cbz", ".zip", ".cb7", ".7z", ".cbr", ".rar"}
MAX_PAGES = 10_000
MAX_PAGE_BYTES = 512 * 1024 * 1024
MAX_BOOK_BYTES = 20 * 1024 * 1024 * 1024


def _safe_name(name: str) -> bool:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    return (not path.is_absolute() and all(part not in ("..", ".") for part in normalized.split("/"))
            and ":" not in normalized and "\x00" not in normalized)


def _sort_name(name: str):
    return [int(part) if part.isdigit() else part.casefold()
            for part in re.split(r"(\d+)", name.replace("\\", "/"))]


def _check_entries(entries: list[tuple[str, int]]) -> list[tuple[str, int]]:
    pages = [(name, size) for name, size in entries if Path(name).suffix.lower() in IMAGE_EXTS]
    if not pages:
        raise ValueError("This archive contains no supported page images.")
    if len(pages) > MAX_PAGES or sum(size for _, size in pages) > MAX_BOOK_BYTES:
        raise ValueError("This archive exceeds the supported page count or extracted size.")
    if len({name.replace("\\", "/").casefold() for name, _ in pages}) != len(pages):
        raise ValueError("This archive contains duplicate image paths.")
    for name, size in pages:
        if not _safe_name(name) or size < 0 or size > MAX_PAGE_BYTES:
            raise ValueError(f"Unsafe or oversized image entry: {name!r}")
    return sorted(pages, key=lambda item: _sort_name(item[0]))


def _metadata_entry(entries):
    matches = [(name, size) for name, size in entries
               if PurePosixPath(name.replace("\\", "/")).name.casefold() == 'comicinfo.xml']
    if len(matches) > 1:
        raise ValueError('This archive contains more than one ComicInfo.xml; metadata is ambiguous.')
    if not matches:
        return None
    name, size = matches[0]
    if not _safe_name(name) or not 0 <= size <= MAX_XML_BYTES:
        raise ValueError('Unsafe or oversized ComicInfo.xml entry.')
    return name


def _copy_metadata(archive, name, destination):
    if name is None:
        return
    with archive.open(name) as stream:
        data = stream.read(MAX_XML_BYTES + 1)
    if len(data) > MAX_XML_BYTES:
        raise ValueError('ComicInfo.xml exceeds the supported size.')
    (destination / 'ComicInfo.xml').write_bytes(data)


def _unrar_tool() -> str:
    """Prefer the app's companion executable, then an installed UnRAR/WinRAR."""
    executable = Path(sys.executable).resolve().parent
    candidates = [executable / "unrar.exe", Path(__file__).resolve().parent.parent / "unrar.exe"]
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        if os.environ.get(variable):
            candidates.append(Path(os.environ[variable]) / "WinRAR" / "UnRAR.exe")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("unrar.exe") or shutil.which("unrar")
    if found:
        return found
    raise ValueError("RAR/CBR import needs UnRAR. Place the official unrar.exe beside "
                     "ComicStitcher.exe (or launch.py), or install WinRAR/UnRAR, then retry.")


# Shown on Linux and other non-Windows systems when no RAR extractor is installed.
RAR_TOOL_HELP = ("RAR/CBR import needs a RAR extractor, and none was found. Install one, "
                 "then retry: for example 'sudo apt install unar' on Ubuntu or Debian, "
                 "'sudo dnf install unar' on Fedora, or 'sudo pacman -S unarchiver' on Arch. "
                 "UnRAR, 7-Zip and bsdtar also work.")


def _setup_rar_tools(windows: bool | None = None) -> None:
    """Choose the program that extracts RAR archives.

    Windows uses the official UnRAR shipped beside Comic Stitcher (or WinRAR's).
    Elsewhere nothing is bundled: rarfile tries the installed extractors in its
    own order (UnRAR, unar, 7-Zip, bsdtar) and uses the first that works.
    """
    if windows is None:
        windows = os.name == "nt"
    if windows:
        rarfile.UNRAR_TOOL = _unrar_tool()
        backends = dict(unrar=True, unar=False, bsdtar=False, sevenzip=False, sevenzip2=False)
        failure = "UnRAR could not start. Install an up-to-date UnRAR executable and retry."
    else:
        backends = dict(unrar=True, unar=True, bsdtar=True, sevenzip=True, sevenzip2=True)
        failure = RAR_TOOL_HELP
    try:
        rarfile.tool_setup(**backends, force=True)
    except rarfile.RarCannotExec as exc:
        raise ValueError(failure) from exc


def _import_rar(source: Path, destination: Path, progress, total_steps: int = 3) -> list[tuple[str, int]]:
    _setup_rar_tools()
    try:
        with rarfile.RarFile(source) as archive:
            if len(archive.volumelist()) > 1:
                raise ValueError("Multi-part RAR archives are not supported. Combine the volumes first.")
            members = archive.infolist()
            for info in members:
                if Path(info.filename).suffix.lower() in IMAGE_EXTS and (info.is_dir() or info.is_symlink()):
                    raise ValueError(f"Archive contains a non-file image: {info.filename!r}")
            pages = _check_entries([(info.filename, info.file_size) for info in members if not info.is_dir()])
            metadata = _metadata_entry([(info.filename, info.file_size) for info in members if not info.is_dir()])
            if metadata and any(info.is_symlink() for info in members if info.filename == metadata):
                raise ValueError("ComicInfo.xml cannot be a symlink.")
            _copy_metadata(archive, metadata, destination)
            image_names = {name for name, _ in pages}
            if any(info.needs_password() for info in members if info.filename in image_names):
                raise ValueError("Password-protected archives are not supported.")
            for index, (name, declared_size) in enumerate(pages, 1):
                target = destination / f"{index:06d}{Path(name).suffix.lower()}"
                with archive.open(name) as input_file, target.open("wb") as output_file:
                    copied = 0
                    while block := input_file.read(1024 * 1024):
                        copied += len(block)
                        if copied > declared_size or copied > MAX_PAGE_BYTES:
                            raise ValueError(f"Archive image exceeds its declared size: {name!r}")
                        output_file.write(block)
                if copied != declared_size:
                    raise ValueError(f"Archive image size does not match its entry: {name!r}")
                if progress:
                    progress(index, len(pages) * total_steps)
            return pages
    except rarfile.Error as exc:
        raise ValueError(f"Cannot read or extract this RAR/CBR archive: {exc}") from exc


def import_archive(source: Path, destination: Path, manual: bool = False, progress=None, *, right_to_left=False):
    """Stage numbered image copies, then scan; archive and member data stay untouched."""
    source = source.expanduser().resolve(strict=True)
    extension = source.suffix.lower()
    if extension not in ARCHIVE_EXTS:
        raise ValueError("Choose a CBZ, ZIP, CB7, 7z, CBR, or RAR archive.")
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError("The temporary import folder is not empty.")
    total_steps = 2 if manual else 3
    if extension in (".cbr", ".rar"):
        pages = _import_rar(source, destination, progress, total_steps)
    elif extension in (".cbz", ".zip"):
        with zipfile.ZipFile(source) as archive:
            members = [info for info in archive.infolist() if not info.is_dir()]
            for info in members:
                if Path(info.filename).suffix.lower() in IMAGE_EXTS and stat.S_ISLNK(info.external_attr >> 16):
                    raise ValueError(f"Archive contains an image symlink: {info.filename!r}")
            pages = _check_entries([(info.filename, info.file_size) for info in members])
            if any(archive.getinfo(name).flag_bits & 1 for name, _ in pages):
                raise ValueError("Password-protected archives are not supported.")
            metadata = _metadata_entry([(info.filename, info.file_size) for info in members])
            if metadata and stat.S_ISLNK(archive.getinfo(metadata).external_attr >> 16):
                raise ValueError("ComicInfo.xml cannot be a symlink.")
            _copy_metadata(archive, metadata, destination)
            for index, (name, _) in enumerate(pages, 1):
                target = destination / f"{index:06d}{Path(name).suffix.lower()}"
                with archive.open(name) as input_file, target.open("wb") as output_file:
                    shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
                if progress:
                    progress(index, len(pages) * total_steps)
    else:
        # py7zr 1.1.3+ guards extraction size and unsafe paths. Extract only
        # regular image entries into a disposable directory before copying.
        with py7zr.SevenZipFile(source, "r", max_extract_size=MAX_BOOK_BYTES) as archive:
            infos = archive.list()
            for info in infos:
                if Path(info.filename).suffix.lower() in IMAGE_EXTS and (not info.is_file or info.is_symlink):
                    raise ValueError(f"Archive contains a non-file image: {info.filename!r}")
            pages = _check_entries([(info.filename, info.uncompressed) for info in infos if info.is_file])
            metadata = _metadata_entry([(info.filename, info.uncompressed) for info in infos if info.is_file])
            if metadata and any(info.is_symlink for info in infos if info.filename == metadata):
                raise ValueError("ComicInfo.xml cannot be a symlink.")
            unpack = destination / "_archive_extract"
            unpack.mkdir()
            archive.extract(path=unpack, targets=[name for name, _ in pages] + ([metadata] if metadata else []))
        for index, (name, _) in enumerate(pages, 1):
            original = unpack.joinpath(*PurePosixPath(name.replace("\\", "/")).parts)
            if original.is_symlink() or not original.is_file() or not original.resolve().is_relative_to(unpack.resolve()):
                raise ValueError(f"Unsafe image entry: {name!r}")
            shutil.copyfile(original, destination / f"{index:06d}{Path(name).suffix.lower()}")
            if progress:
                progress(index, len(pages) * total_steps)
        if metadata:
            xml = unpack.joinpath(*PurePosixPath(metadata.replace("\\", "/")).parts)
            if xml.is_symlink() or not xml.resolve().is_relative_to(unpack.resolve()) or xml.stat().st_size > MAX_XML_BYTES:
                raise ValueError('Unsafe ComicInfo.xml entry.')
            shutil.copyfile(xml, destination / 'ComicInfo.xml')
        shutil.rmtree(unpack)
    scan = scan_folder(destination,
                       progress=(lambda n, total: progress(len(pages) + n, len(pages) * total_steps))
                       if progress else None, manual=manual, right_to_left=right_to_left)
    scan.output_folder = source.parent
    scan.source_archive = source
    return scan
