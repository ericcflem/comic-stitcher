"""Resolve a selection into comics, expanding batch folders one level only."""
from pathlib import Path
from .archive_import import ARCHIVE_EXTS
from .detector import collect_images, natural_key


def expand_sources(paths):
    result = []
    seen = set()
    for raw in paths:
        path = Path(raw).resolve()
        candidates = [path]
        if path.is_dir() and not collect_images(path):
            candidates = sorted((child for child in path.iterdir()
                                 if (child.is_file() and child.suffix.lower() in ARCHIVE_EXTS | {'.pdf'})
                                 or (child.is_dir() and collect_images(child))), key=natural_key)
            if not candidates:
                candidates = [path]  # Let the import UI explain an empty/unsupported folder.
        for candidate in candidates:
            if candidate not in seen:
                result.append(candidate)
                seen.add(candidate)
    return result
