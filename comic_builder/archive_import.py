"""Extract image entries from comic archives to a disposable scan directory."""
from __future__ import annotations

import os
import shutil
import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath

import py7zr
import rarfile

from .backend import describe_case_twins, scan_folder
from .comicinfo import MAX_XML_BYTES
from .detector import IMAGE_EXTS, name_sort_key

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
    return name_sort_key(name.replace("\\", "/"))


def _extraction_layers(pages: list[tuple[str, int]]) -> list[list[str]]:
    """Split pages so no two in one batch differ only in capitalisation.

    Extracted side by side, "001.jpg" and "001.JPG" would overwrite each other
    on Windows and macOS, so each such twin is extracted into its own folder.
    """
    seen: dict[str, int] = {}
    layers: list[list[str]] = []
    for name, _ in pages:
        key = name.replace("\\", "/").casefold()
        layer = seen.get(key, 0)
        seen[key] = layer + 1
        if layer == len(layers):
            layers.append([])
        layers[layer].append(name)
    return layers


def _check_entries(entries: list[tuple[str, int]]) -> list[tuple[str, int]]:
    pages = [(name, size) for name, size in entries if Path(name).suffix.lower() in IMAGE_EXTS]
    if not pages:
        raise ValueError("This archive contains no supported page images.")
    if len(pages) > MAX_PAGES or sum(size for _, size in pages) > MAX_BOOK_BYTES:
        raise ValueError("This archive exceeds the supported page count or extracted size.")
    if len({name.replace("\\", "/") for name, _ in pages}) != len(pages):
        raise ValueError("This archive contains the same image path twice.")
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


# The extractors rarfile can use, in the order they are tried outside Windows.
RAR_EXTRACTORS = (("unrar", "UnRAR"), ("unar", "unar"), ("bsdtar", "bsdtar"),
                  ("sevenzip", "7-Zip (7z)"), ("sevenzip2", "7-Zip (7zz)"))

# Shown on Linux and other non-Windows systems when no RAR extractor is installed.
RAR_TOOL_HELP = ("RAR/CBR import needs a RAR extractor, and none was found. Install UnRAR, "
                 "then retry: 'sudo apt install unrar' on Ubuntu or Debian, 'sudo pacman -S unrar' "
                 "on Arch, or 'sudo dnf install unrar' on Fedora (from RPM Fusion). 7-Zip with its "
                 "RAR support (Ubuntu's '7zip-rar' package) also works. unar, bsdtar, and 7-Zip without "
                 "its RAR support, can't fully read archives in the newer RAR5 format.")

# Shown when every installed extractor stopped partway through a page.
RAR5_HELP = ("{tools} couldn't fully read this archive: the pages came out cut short. It is most "
             "likely in the newer RAR5 format, which unar, bsdtar, and 7-Zip without its RAR support, "
             "can't fully extract. Install UnRAR ('sudo apt install unrar' on Ubuntu or Debian), or 7-Zip's "
             "RAR support ('sudo apt install 7zip-rar'), then retry.")


class _ExtractorFailed(Exception):
    """The extractor started but didn't deliver a page whole; another may manage."""


def _rar_extractors(windows: bool | None = None) -> list[tuple[str, dict]]:
    """The extractors to try, in order, as rarfile.tool_setup arguments.

    Windows uses the official UnRAR shipped beside Comic Stitcher (or WinRAR's).
    Elsewhere nothing is bundled: each installed extractor is tried in turn
    (UnRAR, unar, bsdtar, 7-Zip), since some can't fully read RAR5 archives.
    """
    if windows is None:
        windows = os.name == "nt"
    if windows:
        rarfile.UNRAR_TOOL = _unrar_tool()
        return [("UnRAR", dict(unrar=True, unar=False, bsdtar=False, sevenzip=False, sevenzip2=False))]
    return [(label, {name: name == key for name, _ in RAR_EXTRACTORS}) for key, label in RAR_EXTRACTORS]


def _extract_rar(source: Path, destination: Path, progress, total_steps: int) -> list[tuple[str, int]]:
    try:
        archive = rarfile.RarFile(source)
    except rarfile.Error as exc:
        raise ValueError(f"Cannot read or extract this RAR/CBR archive: {exc}") from exc
    with archive:
        try:
            if len(archive.volumelist()) > 1:
                raise ValueError("Multi-part RAR archives are not supported. Combine the volumes first.")
            members = archive.infolist()
        except rarfile.Error as exc:
            raise ValueError(f"Cannot read or extract this RAR/CBR archive: {exc}") from exc
        for info in members:
            if Path(info.filename).suffix.lower() in IMAGE_EXTS and (info.is_dir() or info.is_symlink()):
                raise ValueError(f"Archive contains a non-file image: {info.filename!r}")
        pages = _check_entries([(info.filename, info.file_size) for info in members if not info.is_dir()])
        metadata = _metadata_entry([(info.filename, info.file_size) for info in members if not info.is_dir()])
        if metadata and any(info.is_symlink() for info in members if info.filename == metadata):
            raise ValueError("ComicInfo.xml cannot be a symlink.")
        image_names = {name for name, _ in pages}
        if any(info.needs_password() for info in members if info.filename in image_names):
            raise ValueError("Password-protected archives are not supported.")
        try:
            _copy_metadata(archive, metadata, destination)
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
                    raise _ExtractorFailed(f"Archive image size does not match its entry: {name!r}")
                if progress:
                    progress(index, len(pages) * total_steps)
        except rarfile.RarCannotExec:
            raise
        except rarfile.Error as exc:
            raise _ExtractorFailed(str(exc)) from exc
        return pages


def _clear(folder: Path) -> None:
    for item in folder.iterdir():
        if item.is_dir() and not item.is_symlink():
            shutil.rmtree(item)
        else:
            item.unlink()


def _import_rar(source: Path, destination: Path, progress, total_steps: int = 3,
                windows: bool | None = None) -> list[tuple[str, int]]:
    if windows is None:
        windows = os.name == "nt"
    failed: list[str] = []
    problem = None
    for label, backends in _rar_extractors(windows):
        try:
            rarfile.tool_setup(**backends, force=True)
            return _extract_rar(source, destination, progress, total_steps)
        except rarfile.RarCannotExec:
            continue  # Not installed, or it wouldn't start.
        except _ExtractorFailed as exc:
            failed.append(label)
            problem = exc
            _clear(destination)  # Start the next extractor afresh.
    if not failed:
        raise ValueError("UnRAR could not start. Install an up-to-date UnRAR executable and retry."
                         if windows else RAR_TOOL_HELP)
    if windows:
        raise ValueError(f"Cannot read or extract this RAR/CBR archive: {problem}")
    raise ValueError(RAR5_HELP.format(tools=" and ".join(failed)))


def import_archive(source: Path, destination: Path, manual: bool = False, progress=None, *, right_to_left=False,
                   workers: int = 0):
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
        # Twins differing only in capitalisation go to separate folders (see
        # _extraction_layers); py7zr extracts once per opened archive.
        layers = _extraction_layers(pages)
        folder_of = {}
        for number, names in enumerate(layers):
            folder = unpack / str(number)
            folder.mkdir()
            with py7zr.SevenZipFile(source, "r", max_extract_size=MAX_BOOK_BYTES) as archive:
                archive.extract(path=folder, targets=names + ([metadata] if metadata and number == 0 else []))
            folder_of.update((name, folder) for name in names)
        for index, (name, _) in enumerate(pages, 1):
            original = folder_of[name].joinpath(*PurePosixPath(name.replace("\\", "/")).parts)
            if original.is_symlink() or not original.is_file() or not original.resolve().is_relative_to(unpack.resolve()):
                raise ValueError(f"Unsafe image entry: {name!r}")
            shutil.copyfile(original, destination / f"{index:06d}{Path(name).suffix.lower()}")
            if progress:
                progress(index, len(pages) * total_steps)
        if metadata:
            xml = (unpack / "0").joinpath(*PurePosixPath(metadata.replace("\\", "/")).parts)
            if xml.is_symlink() or not xml.resolve().is_relative_to(unpack.resolve()) or xml.stat().st_size > MAX_XML_BYTES:
                raise ValueError('Unsafe ComicInfo.xml entry.')
            shutil.copyfile(xml, destination / 'ComicInfo.xml')
        shutil.rmtree(unpack)
    scan = scan_folder(destination,
                       progress=(lambda n, total: progress(len(pages) + n, len(pages) * total_steps))
                       if progress else None, manual=manual, right_to_left=right_to_left, workers=workers)
    scan.output_folder = source.parent
    scan.source_archive = source
    # The staged copies are numbered, so name any capitalisation twins by
    # their names in the archive.
    names = [PurePosixPath(name.replace("\\", "/")).as_posix() for name, _ in pages]
    staged = [destination / f"{index:06d}{Path(name).suffix.lower()}" for index, (name, _) in enumerate(pages, 1)]
    scan.case_twins = describe_case_twins(names, staged)
    return scan
