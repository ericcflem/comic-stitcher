"""Optional per-book metadata editing, with infrequent fields in Advanced."""
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTabWidget, QWidget,
    QScrollArea, QFormLayout, QCheckBox, QLineEdit, QPlainTextEdit, QComboBox, QPushButton, QMessageBox)
from .comicinfo import BASIC, FIELDS, ENUMS, LABELS, parse_xml, edit_xml
from .comicvine_dialog import ComicVineDialog


class MetadataDialog(QDialog):
    def __init__(self, original=None, parent=None, *, search_hint="", lookup_provider=None):
        super().__init__(parent)
        self.setWindowTitle('Comic metadata')
        self.resize(680, 640)
        self.search_hint = search_hint
        self.lookup_provider = lookup_provider
        self.original = original
        self.result_xml = original
        root = parse_xml(original) if original else None
        self.initial = {}
        self.controls = {}
        box = QVBoxLayout(self)
        title = QLabel('ComicInfo.xml'); title.setStyleSheet('font-size:20px; font-weight:bold;')
        title_row = QHBoxLayout()
        title_row.addWidget(title); title_row.addStretch()
        self.lookup_button = QPushButton('Look up on ComicVine…')
        self.lookup_button.setEnabled(lookup_provider is not None)
        self.lookup_button.clicked.connect(self.lookup)
        title_row.addWidget(self.lookup_button)
        box.addLayout(title_row)
        hint = QLabel('Check fields to include them. Unchecked fields are omitted. Existing unedited information is retained.')
        hint.setWordWrap(True); box.addWidget(hint)
        tabs = QTabWidget(); box.addWidget(tabs, 1)
        credits = ('Writer', 'Penciller', 'Inker', 'Colorist', 'Letterer', 'CoverArtist', 'Editor')
        groups = [('Book', [f for f in BASIC if f not in credits]), ('Credits', credits),
                  ('Advanced', [f for f in FIELDS if f not in BASIC and f not in ('Pages', 'PageCount')])]
        for label, fields in groups:
            content = QWidget(); form = QFormLayout(content)
            form.setVerticalSpacing(10)
            scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(content)
            tabs.addTab(scroll, label)
            for name in fields:
                node = root.find(name) if root is not None else None
                value = node.text or '' if node is not None else None
                self.initial[name] = value
                check = QCheckBox(LABELS.get(name, name)); check.setChecked(value is not None)
                if name in ENUMS:
                    control = QComboBox(); control.addItems(ENUMS[name])
                    if value is not None and value not in ENUMS[name]:
                        control.addItem(value)
                    if value is not None: control.setCurrentText(value)
                elif name in ('Summary', 'Notes', 'Review'):
                    control = QPlainTextEdit(value or ''); control.setFixedHeight(85)
                else:
                    control = QLineEdit(value or '')
                control.setEnabled(check.isChecked()); check.toggled.connect(control.setEnabled)
                if name == 'LanguageISO': control.setPlaceholderText('e.g. en, fr, ja')
                if name == 'Number': control.setToolTip('Issue number may include decimals or text, such as 1.5 or Annual 1.')
                if name == 'CommunityRating': control.setToolTip('A number from 0 to 5, with at most two decimal places.')
                self.controls[name] = (check, control)
                form.addRow(check, control)
        foot = QLabel('Final page count is calculated at export. Existing page labels and bookmarks follow your edits. Metadata applies only to this comic.')
        foot.setWordWrap(True); box.addWidget(foot)
        row = QHBoxLayout(); row.addStretch()
        cancel = QPushButton('Cancel'); cancel.clicked.connect(self.reject); row.addWidget(cancel)
        okay = QPushButton('OK'); okay.setDefault(True); okay.clicked.connect(self.apply); row.addWidget(okay)
        box.addLayout(row)

    def lookup(self):
        if self.lookup_provider is None:
            return
        client = self.lookup_provider()
        if client is None:
            return
        dialog = ComicVineDialog(client, self.search_hint, self)
        if dialog.exec() == QDialog.Accepted:
            self.fill_from_lookup(dialog.result_fields, dialog.replace_existing)

    def fill_from_lookup(self, fields, replace_existing=False):
        for name, value in fields.items():
            if name not in self.controls or not isinstance(value, str) or not value.strip():
                continue
            check, control = self.controls[name]
            current = control.currentText() if isinstance(control, QComboBox) else control.toPlainText() if isinstance(control, QPlainTextEdit) else control.text()
            # Keep populated drafts even when the user has unchecked their field.
            if not replace_existing and current.strip():
                continue
            if isinstance(control, QComboBox):
                if value not in ENUMS.get(name, ()):
                    continue
                control.setCurrentText(value)
            elif isinstance(control, QPlainTextEdit):
                control.setPlainText(value)
            else:
                control.setText(value)
            check.setChecked(True)

    def apply(self):
        values = {}
        for name, (check, control) in self.controls.items():
            value = None
            if check.isChecked():
                value = control.currentText() if isinstance(control, QComboBox) else control.toPlainText() if isinstance(control, QPlainTextEdit) else control.text()
            if value != self.initial[name]:
                values[name] = value
        try:
            self.result_xml = edit_xml(self.original, values)
        except ValueError as exc:
            QMessageBox.warning(self, 'Check metadata', str(exc)); return
        self.accept()
