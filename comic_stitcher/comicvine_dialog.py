"""Background, user-triggered metadata search and candidate selection."""
import html
import threading
from PySide6.QtCore import QObject, Signal, Slot, QRunnable, QThreadPool, Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QComboBox, QPushButton,
    QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QCheckBox, QSplitter)
from .comicvine import ComicVineError, LookupCancelled, site_url, plain_text
from .window_outline import OutlinedWindow


class _Signals(QObject):
    result = Signal(object)
    failed = Signal(str)


class _Job(QRunnable):
    def __init__(self, task, cancel):
        super().__init__(); self.task = task; self.cancel = cancel; self.signals = _Signals()
    @Slot()
    def run(self):
        try:
            result = self.task(self.cancel)
            if not self.cancel.is_set(): self.signals.result.emit(result)
        except LookupCancelled: pass
        except ComicVineError as exc:
            if not self.cancel.is_set(): self.signals.failed.emit(str(exc))
        except Exception:
            if not self.cancel.is_set(): self.signals.failed.emit('The lookup could not be completed. Manual metadata editing is still available.')


class ComicVineDialog(OutlinedWindow, QDialog):
    def __init__(self, client, search_hint='', parent=None):
        super().__init__(parent)
        self._setup_outline()  # GNOME on Wayland: a visible window edge
        self.client = client
        self.result_fields = {}
        self.replace_existing = False
        self.cancel_event = threading.Event()
        self.finished.connect(lambda _: self.cancel_event.set())
        self.setWindowTitle('Look up on ComicVine'); self.resize(880, 620)
        self.mode = 'search'; self.offset = 0; self.total = 0; self.page_count = 0
        self.volume_id = None; self.loaded_issue_id = None; self.loaded_fields = {}; self.busy = False
        self.search_query = ''; self.search_resource = 'volume'
        box = QVBoxLayout(self)
        heading = QLabel('Find this comic'); heading.setStyleSheet('font-size:20px; font-weight:bold;'); box.addWidget(heading)
        hint = QLabel('Find a series, then choose its issue or collected edition. You can also search issues directly.'); hint.setWordWrap(True); box.addWidget(hint)
        row = QHBoxLayout()
        self.query = QLineEdit(search_hint); self.query.setPlaceholderText('Edit the title before searching')
        self.resource = QComboBox(); self.resource.addItems(['Series', 'Issues / collected editions'])
        self.search_button = QPushButton('Search'); self.search_button.clicked.connect(self.search)
        self.query.returnPressed.connect(self.search)
        row.addWidget(self.query, 1); row.addWidget(self.resource); row.addWidget(self.search_button); box.addLayout(row)
        self.context = QLabel(); self.context.setTextFormat(Qt.PlainText); self.context.setWordWrap(True); box.addWidget(self.context)
        split = QSplitter()
        self.results = QListWidget(); self.results.currentItemChanged.connect(self.selection_changed)
        self.results.itemDoubleClicked.connect(lambda _: self.inspect_selected())
        self.preview = QPlainTextEdit(); self.preview.setReadOnly(True)
        self.preview.setPlaceholderText('Select a result and get its metadata to review the available fields.')
        split.addWidget(self.results); split.addWidget(self.preview); split.setSizes([440, 380]); box.addWidget(split, 1)
        paging = QHBoxLayout()
        self.back_button = QPushButton('Back to search results'); self.back_button.clicked.connect(self.back_to_search)
        self.previous = QPushButton('Previous results'); self.previous.clicked.connect(lambda: self.page(-1))
        self.next = QPushButton('Next results'); self.next.clicked.connect(lambda: self.page(1))
        self.inspect_button = QPushButton('Browse issues'); self.inspect_button.clicked.connect(self.inspect_selected)
        for b in (self.back_button, self.previous, self.next): paging.addWidget(b)
        paging.addStretch(); paging.addWidget(self.inspect_button); box.addLayout(paging)
        self.source = QLabel('<a href="https://comicvine.gamespot.com/">Data from ComicVine</a>')
        self.source.setOpenExternalLinks(True); self.source.setWordWrap(True); box.addWidget(self.source)
        self.status = QLabel('Searches use your API key. Requests are paced and results cached for this session.')
        self.status.setTextFormat(Qt.PlainText); self.status.setWordWrap(True); box.addWidget(self.status)
        self.replace = QCheckBox('Replace populated metadata fields')
        self.replace.setToolTip('Unchecked: fill empty fields only. Checked: replace fields supplied by ComicVine, including populated fields currently unchecked in the editor. Missing ComicVine values never erase existing information.')
        box.addWidget(self.replace)
        buttons = QHBoxLayout(); buttons.addStretch()
        cancel = QPushButton('Cancel'); cancel.clicked.connect(self.reject)
        self.use = QPushButton('Use these fields'); self.use.clicked.connect(self.use_fields)
        buttons.addWidget(cancel); buttons.addWidget(self.use); box.addLayout(buttons)
        self.update_buttons()

    def update_buttons(self):
        item = self.results.currentItem()
        record = item.data(Qt.UserRole) if item else None
        kind = record.get('resource_type') if record else None
        self.search_button.setEnabled(not self.busy)
        self.query.setEnabled(not self.busy); self.resource.setEnabled(not self.busy)
        self.results.setEnabled(not self.busy)
        self.back_button.setVisible(self.mode == 'issues'); self.back_button.setEnabled(not self.busy)
        self.previous.setEnabled(not self.busy and self.offset > 0)
        self.next.setEnabled(not self.busy and self.page_count > 0 and self.offset+self.page_count < self.total)
        self.inspect_button.setText('Browse issues' if kind == 'volume' else 'Get metadata')
        self.inspect_button.setEnabled(not self.busy and kind in ('volume', 'issue'))
        self.use.setEnabled(not self.busy and bool(self.loaded_fields) and record is not None and record.get('id') == self.loaded_issue_id)

    def run_task(self, task, callback):
        if self.busy: return
        self.busy = True; self.status.setText('Contacting ComicVine… Requests may pause briefly to respect API limits.'); self.update_buttons()
        job = _Job(task, self.cancel_event)
        self.active_job = job
        self.task_callback = callback
        job.signals.result.connect(self.task_result)
        job.signals.failed.connect(self.task_failed)
        QThreadPool.globalInstance().start(job)

    @Slot(object)
    def task_result(self, result):
        self.task_done(result, self.task_callback)

    def task_done(self, result, callback):
        if self.cancel_event.is_set(): return
        self.busy = False
        try: callback(result)
        except (ValueError, TypeError, KeyError, AttributeError):
            self.status.setText('ComicVine returned incomplete results. Try another search.')
        self.update_buttons()

    @Slot(str)
    def task_failed(self, message):
        if self.cancel_event.is_set(): return
        self.busy = False; self.status.setText(message); self.update_buttons()

    def search(self):
        if self.busy: return
        query = self.query.text().strip()
        if not query: self.status.setText('Enter a search title.'); return
        self.search_query = query; self.search_resource = 'volume' if self.resource.currentIndex() == 0 else 'issue'
        self.mode = 'search'; self.offset = 0; self.volume_id = None
        self.fetch_list()

    def fetch_list(self):
        self.loaded_fields = {}; self.loaded_issue_id = None; self.preview.clear()
        if self.mode == 'search':
            self.context.setText('Search results — select the correct series or edition.')
            task = lambda cancel: self.client.search(self.search_query, self.search_resource, self.offset, cancel)
        else:
            self.context.setText(f'Issues / collected editions in {self.volume_name} — choose the actual book.')
            task = lambda cancel: self.client.issues(self.volume_id, self.offset, cancel)
        self.run_task(task, self.show_results)

    def show_results(self, response):
        records = response.get('results', [])
        if not isinstance(records, list): raise ValueError('Invalid results')
        self.results.clear()
        self.total = max(0, int(response.get('number_of_total_results', len(records))))
        self.page_count = len(records)
        for record in records:
            if not isinstance(record, dict) or not str(record.get('id', '')).isdigit(): continue
            record = dict(record)
            kind = 'issue' if self.mode == 'issues' else record.get('resource_type', self.search_resource)
            if kind not in ('volume', 'issue'): continue
            record['resource_type'] = kind
            name = plain_text(record.get('name')) or '(Untitled issue)'
            if not isinstance(name, str): continue
            if kind == 'volume':
                publisher = record.get('publisher') or {}
                publisher_name = plain_text(publisher.get('name')) if isinstance(publisher, dict) else ''
                label = f'{name} · {record.get("start_year") or "Year unknown"} · {publisher_name or "Publisher unknown"}'
            else:
                series = record.get('volume') or {}
                series_name = plain_text(series.get('name')) if isinstance(series, dict) else ''
                label = f'{series_name} #{record.get("issue_number") if record.get("issue_number") is not None else "?"} · {name} · {record.get("cover_date") or "Date unknown"}'
            item = QListWidgetItem(label); item.setData(Qt.UserRole, record); self.results.addItem(item)
        self.status.setText(f'{self.results.count()} results shown. Select a series to browse its issues, or an issue to get metadata.' if self.results.count() else 'No matches. Try a shorter or different title.')
        if self.results.count(): self.results.setCurrentRow(0)

    def selection_changed(self, *_):
        self.loaded_fields = {}; self.loaded_issue_id = None; self.preview.clear()
        item = self.results.currentItem(); record = item.data(Qt.UserRole) if item else {}
        url = site_url(record.get('site_detail_url'))
        self.source.setText(f'<a href="{html.escape(url, quote=True)}">View selected entry on ComicVine</a>' if url else '<a href="https://comicvine.gamespot.com/">Data from ComicVine</a>')
        self.update_buttons()

    def inspect_selected(self):
        if self.busy: return
        item = self.results.currentItem()
        if item is None: return
        record = item.data(Qt.UserRole)
        if record['resource_type'] == 'volume':
            self.volume_id = record['id']; self.volume_name = plain_text(record.get('name')) or 'Selected series'
            self.mode = 'issues'; self.offset = 0; self.fetch_list()
        else:
            issue_id = record['id']
            self.run_task(lambda cancel: self.client.details(issue_id, cancel), lambda result: self.show_details(issue_id, result))

    def show_details(self, issue_id, result):
        self.loaded_issue_id = issue_id; self.loaded_fields = result['fields']
        self.preview.setPlainText('\n\n'.join(f'{name}: {value}' for name, value in self.loaded_fields.items()))
        url = site_url(result.get('source_url'))
        if url: self.source.setText(f'<a href="{html.escape(url, quote=True)}">Metadata from ComicVine — view this entry</a>')
        source = result.get('date_source')
        self.status.setText('Review the fields, then use them in the metadata editor.' + (f' Publication date source: {source}.' if source else '') + (' '+result['warning'] if result.get('warning') else ''))

    def page(self, direction):
        if self.busy: return
        size = 20 if self.mode == 'issues' else 10
        self.offset = max(0, self.offset + direction*size); self.fetch_list()

    def back_to_search(self):
        if self.busy: return
        self.mode = 'search'; self.offset = 0; self.fetch_list()

    def use_fields(self):
        if not self.use.isEnabled(): return
        self.result_fields = dict(self.loaded_fields); self.replace_existing = self.replace.isChecked(); self.accept()
