"""The batch queue: its window, and exporting queued comics one after another."""
from __future__ import annotations

import shutil
from pathlib import Path
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QListWidgetItem, QMessageBox,
    QPushButton, QVBoxLayout)
from ..export import copy_encoding_options
from .. import diagnostics
from ..window_outline import OutlinedDialog
from ..session import clear_session
from .common import ui_primary, tooltip_text
from .theme import tone
from .thumbnails import QueueList
from .queue import QueuedComic, export_queued_comic, queued_outputs


class QueueFlow:
    """Mixed into ComicWindow (gui.py), which builds the window and holds its state."""

    def _edit_queued_job(self, job: QueuedComic):
        """Open a queued comic's settings. Its pages and pairs are kept as reviewed."""
        self.plan = job.plan
        self.current_job = job
        self.editing_queued = True
        self.active_right_to_left = job.plan.scan.right_to_left
        self._load_options_to_screen(job.options)
        self.show_export()

    def _refresh_queue_status(self):
        count = len(self.queue_jobs)
        waiting = len(self.pending_imports)
        self.queue_view_button.setEnabled(bool(count))
        if hasattr(self, "export_queue_button"):
            self.export_queue_button.setText(f"View queue ({count})…")
            self.export_queue_button.setVisible(bool(count))
        self.queue_start_button.setEnabled(bool(count) and self.active_queue_job is None and
                                           not (self.worker and self.worker.isRunning()))
        self.next_import_button.setVisible(bool(waiting))

    def show_queue(self, *, reviewing=False):
        """The queued comics, in export order. From the Create your comic screen
        (reviewing) the comic on screen is among them; starting the queue is left
        to that screen's blue button."""
        dialog = OutlinedDialog(self)
        dialog.setWindowTitle("Queue")
        dialog.resize(760, 480)
        dialog.setStyleSheet("QListWidget { font-size:14px; } "
                            f"QListWidget::item {{ padding:10px; border-bottom:1px solid {tone('list_line')}; }} "
                            f"QListWidget::item:selected {{ color:{tone('list_selected_text')}; "
                            f"background:{tone('list_selected')}; }}")
        layout = QVBoxLayout(dialog)
        note = QLabel("Comics are exported from the top down; drag one to change the order. Double-click "
                      "a comic to change its settings. Output files and folders will be saved alongside "
                      "each comic's originals. Removing a comic leaves its source untouched.")
        note.setWordWrap(True)
        layout.addWidget(note)
        listing = QueueList()
        listing.setToolTip(tooltip_text("Drag a comic to change the export order, or select it and press "
                                        "Ctrl with Up, Down, Home or End. Double-click to change its settings."))
        listing.setWordWrap(True)
        listing.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(listing)
        controls = QHBoxLayout()
        remove = QPushButton("Remove selected")
        start = QPushButton("Start export")
        action = {"next": None, "job": None}
        def refresh():
            listing.clear()
            for job in self.queue_jobs:
                opt = job.options
                kinds = ("full", "reduced") if opt.size_output == "both" else (opt.size_output,)
                copies = []
                for kind in kinds:
                    encoding = self._format_summary(copy_encoding_options(opt, kind == "reduced"),
                                                    original=kind == "full" and opt.keep_originals)
                    copies.append(f"{kind.title()}: {encoding}")
                outputs = queued_outputs(job.plan, opt)
                destinations = "\n".join(path.name for path in outputs)
                item = QListWidgetItem(f"{opt.book_name}\n" + " · ".join(copies) + "\n" + destinations)
                item.setToolTip("\n".join(str(path) for path in outputs))
                item.setData(Qt.UserRole, id(job))
                listing.addItem(item)
            remove.setEnabled(False)
            start.setEnabled(bool(self.queue_jobs))
        listing.currentRowChanged.connect(lambda index: remove.setEnabled(index >= 0))
        def job_at(row):
            jobs = {id(job): job for job in self.queue_jobs}
            return jobs[listing.item(row).data(Qt.UserRole)]
        def reordered():
            # The list's new order becomes the queue's order.
            order = [job_at(row) for row in range(listing.count())]
            if len(order) == len(self.queue_jobs) and order != self.queue_jobs:
                self.queue_jobs[:] = order
                diagnostics.note("Queue reordered: %s", " | ".join(job.options.book_name for job in order))
        listing.reordered.connect(reordered)
        def finish(next_action, job=None):
            action["next"], action["job"] = next_action, job
            dialog.accept()
        def remove_selected():
            row = listing.currentRow()
            if row < 0:
                return
            job = job_at(row)
            if job is self.current_job:
                if QMessageBox.question(
                        dialog, "Remove this comic?",
                        f"“{job.options.book_name}” is the comic you're working on. Remove it from the queue "
                        "and move on? Its source is untouched, and its saved review can be resumed by "
                        "opening it again.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                    return
                finish("skip current")
                return
            self.queue_jobs.remove(job)
            if job.import_work is not None:
                job.import_work.cleanup()
            refresh()
            self._refresh_queue_status()
        remove.clicked.connect(remove_selected)
        listing.itemDoubleClicked.connect(lambda item: finish("edit", job_at(listing.row(item))))
        controls.addWidget(remove)
        another = QPushButton("Add another comic")
        another.setToolTip(tooltip_text("Keep these comics queued and go back to the start screen to add more."))
        another.clicked.connect(lambda: finish("another"))
        controls.addWidget(another)
        files = QPushButton("Add comic files…")
        files.clicked.connect(lambda: finish("files"))
        controls.addWidget(files)
        folder = QPushButton("Add folder…")
        folder.clicked.connect(lambda: finish("folder"))
        controls.addWidget(folder)
        controls.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(dialog.reject)
        controls.addWidget(close)
        start.clicked.connect(lambda: finish("start"))
        ui_primary(start)
        controls.addWidget(start)
        layout.addLayout(controls)
        another.setVisible(reviewing)
        for button in (files, folder, start):
            button.setVisible(not reviewing)
        refresh()
        dialog.exec()
        dialog.deleteLater()
        self._queue_window_action(action["next"], action["job"])

    def _queue_window_action(self, next_action, job=None):
        if next_action == "start":
            self.start_queue()
        elif next_action in ("files", "folder"):
            self._add_batch_items(folder=next_action == "folder")
        elif next_action == "another":
            if self._settle_current_job():
                self._commit_current_job()
                self.new_comic()
                self._refresh_queue_status()
        elif next_action == "skip current":
            self.queue_jobs.remove(self.current_job)
            self.current_job = None
            self.editing_queued = False
            self.new_comic()
            self._refresh_queue_status()
            if self.pending_imports:
                QTimer.singleShot(0, self._open_next_pending)
        elif next_action == "edit" and job is not None and job is not self.current_job:
            if self.current_job is not None:
                if not self._settle_current_job():
                    return
                self._commit_current_job()
                self.new_comic()
            self._edit_queued_job(job)

    def _add_batch_items(self, *, folder=False):
        if folder:
            selected = QFileDialog.getExistingDirectory(self, "Add comic folder or batch folder")
            paths = [Path(selected)] if selected else []
        else:
            selected, _ = QFileDialog.getOpenFileNames(
                self, "Add comic files", "",
                "Comic files (*.cbz *.CBZ *.cbr *.CBR *.cb7 *.CB7 *.zip *.ZIP *.rar *.RAR *.7z *.7Z *.pdf *.PDF)")
            paths = [Path(path) for path in selected]
        if paths:
            self.batch_import_active = True
            self._import_paths(paths)

    def start_queue(self):
        if (not self.queue_jobs or self.active_queue_job is not None or
                self.worker and self.worker.isRunning()):
            return
        self.batch_import_active = False
        self.queue_completed = 0
        self.queue_results.clear()
        self._show_progress_page()
        self._compact_window()
        self._start_next_queued_export()

    def _start_next_queued_export(self):
        if not self.queue_jobs:
            self.active_queue_job = None
            self.progress_bar.setValue(100)
            paths = [path.name for path in self.queue_results]
            visible = paths[:5]
            extra = f"\n…and {len(paths) - 5} more outputs" if len(paths) > 5 else ""
            self.progress_label.setText(
                f"Queue finished: {self.queue_completed} comics created.\n" +
                "\n".join(visible) + extra)
            self.done_button.show()
            self._compact_window()
            self._refresh_queue_status()
            return
        job = self.queue_jobs[0]
        self.active_queue_job = job
        job.options.workers = self.worker_count()  # The current setting, even if changed since queueing.
        if job.pdf_options is not None:
            job.pdf_options["workers"] = job.options.workers
        self._refresh_queue_status()
        self.progress_bar.setValue(0)
        self.progress_label.setText(
            f"Creating comic {self.queue_completed + 1} of "
            f"{self.queue_completed + len(self.queue_jobs)}: {job.options.book_name}…")
        self._run_worker(export_queued_comic, (job,), self._queued_export_done,
                         progress_handler=self._queued_export_progress,
                         failure_handler=self._queued_export_failed)

    def _queued_export_progress(self, done, total):
        self.progress_bar.setValue(round(100 * done / max(1, total)))
        job = self.active_queue_job
        if job is not None:
            phase = "Re-importing and checking pages" if job.reimport_source and done < 50 else "Creating comic"
            self.progress_label.setText(f"{phase}: {job.options.book_name}…")

    def _completed_export_cleanup(self, source=None, import_work=None, screenshot_stage=None):
        actions = []
        if source:
            actions.append(("saved review", lambda: clear_session(source, diagnostics.log_path().parent.parent)))
        if import_work is not None:
            actions.append(("imported pages", import_work.cleanup))
        if screenshot_stage is not None:
            actions.append(("cropped pages", lambda: shutil.rmtree(screenshot_stage)))
        for label, cleanup in actions:
            try:
                cleanup()
            except Exception:
                diagnostics.exception(f"Comic was created, but could not clean up {label}")

    def _queued_export_done(self, destination):
        job = self.queue_jobs.pop(0)
        self._completed_export_cleanup(job.session_source, job.import_work, job.screenshot_stage_dir)
        diagnostics.note("Queued comic created: %s", destination)
        self.queue_results.extend(queued_outputs(job.plan, job.options))
        self.queue_completed += 1
        self.active_queue_job = None
        QTimer.singleShot(0, self._queue_advance_when_ready)

    def _queue_advance_when_ready(self):
        # Worker.result is emitted before QThread has necessarily finished.
        # Keep the prior QThread alive until its run() fully exits.
        if self.worker and self.worker.isRunning():
            QTimer.singleShot(20, self._queue_advance_when_ready)
        else:
            self._start_next_queued_export()

    def _queued_export_failed(self, message):
        diagnostics.note("Queued export stopped: %s", message)
        QMessageBox.warning(self, "Queue stopped",
                            f"Could not create {self.active_queue_job.options.book_name}: {message}\n\n"
                            "This comic and the remaining jobs are still in the queue. "
                            "Correct the issue and start the queue again.")
        self.active_queue_job = None
        self.pages.setCurrentIndex(0)
        self._compact_window()
        QTimer.singleShot(0, self._queue_refresh_when_ready)

    def _queue_refresh_when_ready(self):
        if self.worker and self.worker.isRunning():
            QTimer.singleShot(20, self._queue_refresh_when_ready)
        else:
            self._refresh_queue_status()
