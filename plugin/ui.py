try:
    from qt.core import QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget
except ImportError:
    try:
        from PyQt6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget
    except ImportError:
        QCheckBox = QDialog = QDialogButtonBox = QFileDialog = QFormLayout = QHBoxLayout = QLabel = QLineEdit = QPushButton = QSpinBox = QVBoxLayout = QWidget = object
from calibre_plugins.takeout_book_fixer.config import prefs


class ConfigWidget(QWidget):
    """Configuration panel for Takeout Book Fixer preferences."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        form = QFormLayout()

        self.auto_watch = QCheckBox('Enable Watch Folder Auto-Importer (monitor folder while Calibre runs)')
        self.auto_watch.setChecked(bool(prefs['auto_watch']))
        layout.addWidget(self.auto_watch)

        # Watch Directory
        self.watch_dir = QLineEdit()
        self.watch_dir.setText(prefs['watch_dir'] or '')
        btn_watch = QPushButton('Browse…')
        btn_watch.clicked.connect(lambda: self._browse_dir(self.watch_dir, 'Select Watch Folder'))
        box_watch = QHBoxLayout()
        box_watch.addWidget(self.watch_dir)
        box_watch.addWidget(btn_watch)
        form.addRow(QLabel('Watch Folder:'), box_watch)

        # Output Directory
        self.output_dir = QLineEdit()
        self.output_dir.setText(prefs['output_dir'] or '')
        btn_output = QPushButton('Browse…')
        btn_output.clicked.connect(lambda: self._browse_dir(self.output_dir, 'Select Output Folder for Recovered Books'))
        box_output = QHBoxLayout()
        box_output.addWidget(self.output_dir)
        box_output.addWidget(btn_output)
        form.addRow(QLabel('Output Folder:'), box_output)

        # Processed Directory
        self.processed_dir = QLineEdit()
        self.processed_dir.setText(prefs['processed_dir'] or '')
        self.processed_dir.setPlaceholderText('Default: <watch_dir>/processed')
        btn_processed = QPushButton('Browse…')
        btn_processed.clicked.connect(lambda: self._browse_dir(self.processed_dir, 'Select Processed Folder'))
        box_processed = QHBoxLayout()
        box_processed.addWidget(self.processed_dir)
        box_processed.addWidget(btn_processed)
        form.addRow(QLabel('Processed Folder:'), box_processed)

        # Quiet period timer
        self.quiet_period = QSpinBox()
        self.quiet_period.setRange(1, 60)
        self.quiet_period.setSuffix(' seconds')
        self.quiet_period.setValue(int(prefs['quiet_period'] or 3))
        form.addRow(QLabel('Debounce Quiet Period:'), self.quiet_period)

        layout.addLayout(form)

        # Checkboxes
        self.convert_comics = QCheckBox('Convert text-free PDFs to CBZ comics')
        self.convert_comics.setChecked(bool(prefs['convert_comics']))
        layout.addWidget(self.convert_comics)

        self.auto_import = QCheckBox('Import recovered books into current Calibre library')
        self.auto_import.setChecked(bool(prefs['auto_import']))
        layout.addWidget(self.auto_import)

    def _browse_dir(self, line_edit, title):
        directory = QFileDialog.getExistingDirectory(self, title, line_edit.text() or '')
        if directory:
            line_edit.setText(directory)

    def save_settings(self):
        prefs['auto_watch'] = self.auto_watch.isChecked()
        prefs['watch_dir'] = self.watch_dir.text().strip()
        prefs['output_dir'] = self.output_dir.text().strip()
        prefs['processed_dir'] = self.processed_dir.text().strip()
        prefs['convert_comics'] = self.convert_comics.isChecked()
        prefs['auto_import'] = self.auto_import.isChecked()
        prefs['quiet_period'] = self.quiet_period.value()


class ConfigDialog(QDialog):
    """Settings dialog when opened from plugin menu."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Takeout Book Fixer Preferences')
        layout = QVBoxLayout(self)

        self.widget = ConfigWidget(self)
        layout.addWidget(self.widget)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save_settings(self):
        self.widget.save_settings()

    def accept(self):
        self.save_settings()
        super().accept()
