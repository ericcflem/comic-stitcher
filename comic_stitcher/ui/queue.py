"""Queued comics and exporting them."""
from __future__ import annotations

import tempfile
import copy
from dataclasses import dataclass
from pathlib import Path
from ..backend import BookPlan, safe_book_name
from ..export import export_outputs, ExportOptions, export_book
from ..archive_import import import_archive
from ..pdf_import import import_pdf


@dataclass
class QueuedComic:
    plan: BookPlan
    options: ExportOptions
    session_source: Path | None
    import_work: tempfile.TemporaryDirectory | None
    screenshot_stage_dir: Path | None
    reimport_source: Path | None = None
    source_stat: tuple[int, int] | None = None
    pdf_options: dict | None = None


def export_queued_comic(job: QueuedComic, progress=None):
    """Recreate discarded imports for one job, then always release its working pages."""
    if job.reimport_source is None:
        return export_book(job.plan, job.options, progress=progress)
    source = job.reimport_source
    stat = source.stat()
    if (stat.st_size, stat.st_mtime_ns) != job.source_stat:
        raise ValueError("The original comic has changed since review. Import and review it again.")
    with tempfile.TemporaryDirectory(prefix="comic-stitcher-queue-") as working:
        destination = Path(working) / "pages"
        prepare_progress = (lambda n, total: progress(round(50 * n / max(1, total)), 100)) if progress else None
        if source.suffix.lower() == ".pdf":
            scan = import_pdf(source, destination, manual=job.plan.scan.manual_mode, right_to_left=job.plan.scan.right_to_left,
                              progress=prepare_progress, **(job.pdf_options or {}))
        else:
            scan = import_archive(source, destination, manual=job.plan.scan.manual_mode, right_to_left=job.plan.scan.right_to_left,
                                  progress=prepare_progress, workers=job.options.workers)
        old = job.plan.scan
        if ([(p.path.name, p.width, p.height) for p in scan.pages] !=
                [(p.path.name, p.width, p.height) for p in old.pages] or
                [(c.left, c.right, c.likely, c.continuity) for c in scan.candidates] !=
                [(c.left, c.right, c.likely, c.continuity) for c in old.candidates[:job.plan.base_candidate_count]]):
            raise ValueError("The imported pages or suggestions changed. Review this comic again.")
        scan.candidates = list(old.candidates)
        scan.output_folder = old.output_folder  # Where it was to be saved, perhaps a folder chosen instead.
        plan = copy.deepcopy(job.plan)
        plan.scan = scan
        export_progress = (lambda n, total: progress(50 + round(50 * n / max(1, total)), 100)) if progress else None
        return export_book(plan, job.options, progress=export_progress)


def queued_outputs(plan: BookPlan, opt: ExportOptions) -> list[Path]:
    """All final destinations, including both sizes and retained loose pages."""
    folder = plan.scan.output_folder or plan.scan.folder
    return [path for _size, _kind, path in export_outputs(plan, folder, safe_book_name(opt.book_name), opt)]
