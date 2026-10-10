"""Local per-run diagnostics for packaged, windowed builds."""
from __future__ import annotations

import faulthandler
import logging
import os
import sys
import threading
from datetime import datetime
from pathlib import Path

_log_path: Path | None = None
_fault_file = None


def install_diagnostics() -> Path:
    global _log_path, _fault_file
    if _log_path is not None:
        return _log_path
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
    folder = base / "ComicStitcher" / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    _log_path = folder / f"session-{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}.log"
    logging.basicConfig(filename=_log_path, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", force=True)
    _fault_file = _log_path.open("a", encoding="utf-8", buffering=1)
    faulthandler.enable(file=_fault_file, all_threads=True)

    old_hook = sys.excepthook

    def on_exception(kind, value, tb):
        logging.critical("Uncaught exception", exc_info=(kind, value, tb))
        old_hook(kind, value, tb)

    sys.excepthook = on_exception

    def on_thread_exception(args):
        logging.critical("Uncaught thread exception: %s", args.thread.name,
                         exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    threading.excepthook = on_thread_exception
    note("Application started")
    return _log_path


def log_path() -> Path:
    return install_diagnostics()


def note(message: str, *args) -> None:
    logging.info(message, *args)


def exception(message: str) -> None:
    logging.exception(message)
