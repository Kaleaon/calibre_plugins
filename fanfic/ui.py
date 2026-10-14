"""Fanfic downloader dialog. Network work never runs on the GUI thread (see guikit.run_task)."""
from qt.core import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
                     QPushButton, QTextBrowser, QVBoxLayout)
try:
    from qt.core import _
except ImportError:
    _ = lambda s: s

from calibre_plugins.fanfic_downloader import engine, flaresolverr
from calibre_plugins.fanfic_downloader.config import prefs
from calibre_plugins.fanfic_downloader.guikit import run_task

ADVANCED_HELP = '''# FanFicFare personal.ini settings, optional. Examples (remove the # to use).
# Each [section] header may appear only once: put all of a site's lines under one header.
#
# Royal Road through a FlareSolverr server you run yourself (see docs/royalroad-flaresolverr.md):
#[www.royalroad.com]
#use_flaresolverr_proxy:true
#
# A login for one site:
#[www.royalroad.com]
#username:you@example.com
#password:your-password
#
# Read pages from your browser's cache instead:
#[defaults]
#browser_cache_path:/home/you/.cache/google-chrome/Default/Cache/Cache_Data
#[www.royalroad.com]
#use_browser_cache:true
'''


def guide_text():
    """The Royal Road / FlareSolverr guide, shipped inside the plugin zip."""
    try:
        data = get_resources('royalroad-flaresolverr.md')  # noqa: F821  injected by Calibre's plugin loader
    except NameError:
        data = None
    return data.decode('utf-8') if data else _('The setup guide is missing from this copy of the plugin.')


class RoyalRoadSetupDialog(QDialog):
    """Guide plus helpers: copy the Docker command, test the FlareSolverr server, and switch it on for Royal Road."""

    def __init__(self, parent, advanced_edit):
        super().__init__(parent)
        self.advanced = advanced_edit  # the dialog's Advanced settings box; changes here are saved when that dialog is accepted
        self.setWindowTitle(_('Royal Road setup (FlareSolverr)'))
        self.resize(760, 680)
        layout = QVBoxLayout(self)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setMarkdown(guide_text())
        layout.addWidget(browser, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.program = QLabel()
        self.program.setWordWrap(True)
        layout.addWidget(self.program)
        row = QHBoxLayout()
        self.choose_button = QPushButton(_('Choose FlareSolverr program…'))
        self.choose_button.clicked.connect(self.choose_program)
        self.start_button = QPushButton(_('Start FlareSolverr'))
        self.start_button.clicked.connect(self.start_program)
        self.stop_button = QPushButton(_('Stop FlareSolverr'))
        self.stop_button.clicked.connect(self.stop_program)
        for button in (self.choose_button, self.start_button, self.stop_button):
            row.addWidget(button)
        layout.addLayout(row)
        row = QHBoxLayout()
        for label, slot in ((_('Copy Docker command'), self.copy_docker), (_('Test: is FlareSolverr running?'), self.test_server),
                            (_('Test: load Royal Road through it'), self.test_site), (_('Enable for Royal Road'), self.enable)):
            button = QPushButton(label)
            button.clicked.connect(slot)
            row.addWidget(button)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.show_enabled_state()
        self.refresh_program()

    def settings(self):
        return flaresolverr.settings_from_ini(self.advanced.toPlainText())

    def show_enabled_state(self):
        on = flaresolverr.is_enabled(self.advanced.toPlainText())
        self.status.setText(_('FlareSolverr is switched on for Royal Road in Advanced settings.') if on else
                            _('FlareSolverr is not switched on for Royal Road yet (the last button does that).'))

    def copy_docker(self):
        QApplication.clipboard().setText(flaresolverr.DOCKER_COMMAND)
        self.status.setText(_('Docker command copied. Paste it into a terminal (Command Prompt or PowerShell on Windows).'))

    def _run(self, title, func):
        task = run_task(self, title, func)  # func receives the task, whose cancelled() it may poll
        if task.error:
            self.status.setText(_('Test failed unexpectedly: {error}').format(error=task.error))
        elif task.result is not None:
            self.status.setText((_('OK: ') if task.result[0] else _('Problem: ')) + task.result[1])

    def test_server(self):
        settings = self.settings()
        self._run(_('Checking FlareSolverr…'), lambda task: flaresolverr.check_server(settings))

    def test_site(self):
        settings = self.settings()
        self._run(_('Loading Royal Road through FlareSolverr (up to a minute)…'), lambda task: flaresolverr.fetch_through(settings))

    # -- running a FlareSolverr program the user downloaded
    def refresh_program(self):
        path = prefs['flaresolverr_path']
        running = flaresolverr.launcher.running()
        self.program.setText((_('Program: {path}').format(path=path) if path else _('No FlareSolverr program chosen. Download the Windows or Linux build from '
                              'FlareSolverr\'s releases page, unpack it, and choose the flaresolverr file.')) +
                             (_('\nStarted by this plugin and still running; it stops when Calibre closes or you press Stop.') if running else ''))
        self.start_button.setEnabled(bool(path) and not running)
        self.stop_button.setEnabled(running)

    def choose_program(self):
        path, _ = QFileDialog.getOpenFileName(self, _('Choose the FlareSolverr program (flaresolverr or flaresolverr.exe)'), prefs['flaresolverr_path'] or '')
        if not path:
            return
        problem = flaresolverr.validate_executable(path)
        if problem:
            self.status.setText(problem)
            return
        prefs['flaresolverr_path'] = path
        self.refresh_program()
        self.status.setText(_('Chosen. Press Start FlareSolverr to run it.'))

    def start_program(self):
        path = prefs['flaresolverr_path']
        if not flaresolverr.looks_like_flaresolverr(path) and QMessageBox.question(
                self, _('Run this program?'), _('The file "{path}" is not named like FlareSolverr.\n\nOnly start programs you trust. Run it anyway?').format(path=path),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        settings = self.settings()
        self._run(_('Starting FlareSolverr (the first start can take a minute)…'),
                  lambda task: flaresolverr.launcher.start(path, settings, cancelled=task.cancelled))
        self.refresh_program()

    def stop_program(self):
        self.status.setText(flaresolverr.launcher.stop())
        self.refresh_program()

    def enable(self):
        text, changed = flaresolverr.enable_in_ini(self.advanced.toPlainText())
        if changed:
            self.advanced.setPlainText(text)
        self.show_enabled_state()
        self.status.setText(self.status.text() + (_(' Saved when you press OK in the download window.') if changed else ''))


class SitesDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(_('Supported sites'))
        self.resize(640, 520)
        layout = QVBoxLayout(self)
        extra = engine.parse_extra_adult(prefs['extra_adult'])
        rows = engine.supported_sites(extra_adult=extra)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText('\n'.join(f"{'[adult] ' if adult else '        '}{domain}    e.g. {example}" for domain, example, adult in rows))
        layout.addWidget(text)
        layout.addWidget(QLabel(_('[adult] sites are only used when "Allow adult sites" is on.')))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class DownloadDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(_('Download fanfiction and web serials'))
        self.resize(640, 640)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(_('One story address per line. Add a chapter range like [1-5], [3-] or [-4] to the end of an address '
                                'to fetch only those chapters.')))
        self.urls = QPlainTextEdit()
        self.urls.setPlaceholderText('https://www.royalroad.com/fiction/21220\nhttps://archiveofourown.org/works/51711850')
        layout.addWidget(self.urls, 2)

        self.adult = QCheckBox(_('Allow adult sites (adult-only sites, and confirming adult content on mixed sites)'))
        self.adult.setChecked(bool(prefs['allow_adult']))
        layout.addWidget(self.adult)
        layout.addWidget(QLabel(_('Extra domains to treat as adult sites (one per line):')))
        self.extra = QPlainTextEdit(prefs['extra_adult'])
        self.extra.setMaximumHeight(60)
        layout.addWidget(self.extra)
        self.skip = QCheckBox(_('Skip stories already in this library (matched by their address)'))
        self.skip.setChecked(bool(prefs['skip_existing']))
        layout.addWidget(self.skip)
        self.combine = QCheckBox(_('Combine downloaded stories into one omnibus book'))
        self.combine.setChecked(bool(prefs['combine_stories']))
        layout.addWidget(self.combine)

        layout.addWidget(QLabel(_('Advanced settings (FanFicFare personal.ini format) for logins, browser cache, proxies:')))
        self.advanced = QPlainTextEdit(prefs['advanced_ini'])
        self.advanced.setPlaceholderText(ADVANCED_HELP)
        layout.addWidget(self.advanced, 2)

        row = QHBoxLayout()
        sites = QPushButton(_('Supported sites…'))
        sites.clicked.connect(lambda: SitesDialog(self).exec())
        row.addWidget(sites)
        royal = QPushButton(_('Royal Road setup…'))
        royal.clicked.connect(lambda: RoyalRoadSetupDialog(self, self.advanced).exec())
        row.addWidget(royal)
        row.addStretch(1)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        prefs['allow_adult'] = self.adult.isChecked()
        prefs['extra_adult'] = self.extra.toPlainText()
        prefs['skip_existing'] = self.skip.isChecked()
        prefs['combine_stories'] = self.combine.isChecked()
        prefs['advanced_ini'] = self.advanced.toPlainText()
        return {
            'lines': [line for line in self.urls.toPlainText().splitlines() if line.strip()],
            'allow_adult': self.adult.isChecked(),
            'extra_adult': engine.parse_extra_adult(self.extra.toPlainText()),
            'skip': self.skip.isChecked(),
            'combine': self.combine.isChecked(),
            'ini': self.advanced.toPlainText(),
        }
