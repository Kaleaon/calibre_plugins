"""Fanfic downloader dialog. Network work never runs on the GUI thread (see guikit.run_task)."""
from qt.core import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
                     QPushButton, QRadioButton, QTextBrowser, QVBoxLayout, QWizard, QWizardPage)
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


class StartupMethodPage(QWizardPage):
    """Step 1: Choose startup method."""

    def __init__(self, wizard):
        super().__init__(wizard)
        self.wizard = wizard
        self.setTitle('Step 1: Select Startup Method')
        self.setSubTitle('Choose how FlareSolverr will be launched or reached to bypass Cloudflare protection.')
        layout = QVBoxLayout(self)

        info = QLabel(
            'Cloudflare-protected sites (such as Royal Road) require a FlareSolverr proxy to solve bot challenges.\n'
            'Please select your preferred startup method:'
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.rb_local = QRadioButton('Local Program (Download binary executable on this computer)')
        self.rb_docker = QRadioButton('Docker Container (Run background Docker container)')
        self.rb_existing = QRadioButton('Existing Server (FlareSolverr is already running on this or another machine)')

        if self.wizard.method == 0:
            self.rb_local.setChecked(True)
        elif self.wizard.method == 2:
            self.rb_existing.setChecked(True)
        else:
            self.rb_docker.setChecked(True)

        layout.addWidget(self.rb_local)
        layout.addWidget(self.rb_docker)
        layout.addWidget(self.rb_existing)

        # Include guide browser in page 1 layout so it is visible and findChild(QTextBrowser) works
        layout.addWidget(self.wizard.guide_browser, 1)

    def validatePage(self):
        if self.rb_local.isChecked():
            self.wizard.method = 0
        elif self.rb_docker.isChecked():
            self.wizard.method = 1
        else:
            self.wizard.method = 2
        return True


class MethodConfigPage(QWizardPage):
    """Step 2: Method configuration."""

    def __init__(self, wizard):
        super().__init__(wizard)
        self.wizard = wizard
        self.setTitle('Step 2: Configure Startup Method')
        self.setSubTitle('Select executable path, copy Docker command, or verify server endpoint.')
        self.layout = QVBoxLayout(self)

    def initializePage(self):
        while self.layout.count():
            item = self.layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        method = self.wizard.method
        if method == 0:  # Local Program
            desc = QLabel('Select your FlareSolverr program executable and start it:')
            desc.setWordWrap(True)
            self.layout.addWidget(desc)
            self.layout.addWidget(self.wizard.program)
            row = QHBoxLayout()
            row.addWidget(self.wizard.choose_button)
            row.addWidget(self.wizard.start_button)
            row.addWidget(self.wizard.stop_button)
            self.layout.addLayout(row)
        elif method == 1:  # Docker Container
            desc = QLabel('Run the following Docker command in your terminal or PowerShell:')
            desc.setWordWrap(True)
            self.layout.addWidget(desc)
            cmd_box = QPlainTextEdit(flaresolverr.DOCKER_COMMAND)
            cmd_box.setReadOnly(True)
            cmd_box.setMaximumHeight(60)
            self.layout.addWidget(cmd_box)
            copy_btn = QPushButton('Copy Docker Command')
            copy_btn.clicked.connect(self.wizard.copy_docker)
            self.layout.addWidget(copy_btn)
        else:  # Existing Server
            settings = self.wizard.settings()
            desc = QLabel(f'FlareSolverr endpoint is configured as: {flaresolverr.endpoint(settings)}\n'
                          'Ensure your server is running and accessible at this address.')
            desc.setWordWrap(True)
            self.layout.addWidget(desc)

        self.layout.addWidget(self.wizard.status)
        self.wizard.refresh_program()


class HealthCheckPage(QWizardPage):
    """Step 3: Connectivity Health Check."""

    def __init__(self, wizard):
        super().__init__(wizard)
        self.wizard = wizard
        self.setTitle('Step 3: Connectivity Verification')
        self.setSubTitle('Verify that FlareSolverr answers requests and loads Cloudflare-protected pages.')
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel('Run connectivity checks to verify server health:'))

        row = QHBoxLayout()
        test_srv_btn = QPushButton('Test: Is FlareSolverr running?')
        test_srv_btn.clicked.connect(self.wizard.test_server)
        test_site_btn = QPushButton('Test: Load Royal Road through it')
        test_site_btn.clicked.connect(self.wizard.test_site)
        row.addWidget(test_srv_btn)
        row.addWidget(test_site_btn)
        layout.addLayout(row)

        layout.addWidget(self.wizard.status)

    def initializePage(self):
        self.wizard.test_server()


class ApplySettingsPage(QWizardPage):
    """Step 4: Enable & Inject Settings."""

    def __init__(self, wizard):
        super().__init__(wizard)
        self.wizard = wizard
        self.setTitle('Step 4: Enable & Apply Settings')
        self.setSubTitle('Inject FlareSolverr proxy setting into personal INI configuration.')
        layout = QVBoxLayout(self)

        info = QLabel(
            'Click Finish to inject "use_flaresolverr_proxy:true" into your personal INI settings '
            'and enable FlareSolverr for story downloads.'
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        enable_btn = QPushButton('Enable for Royal Road / Cloudflare')
        enable_btn.clicked.connect(self.wizard.enable)
        layout.addWidget(enable_btn)

        layout.addWidget(self.wizard.status)

    def initializePage(self):
        self.wizard.enable()


class FlareSolverrSetupWizard(QWizard):
    """Multi-step setup wizard for FlareSolverr proxy configuration."""

    def __init__(self, parent=None, advanced_edit=None, recovery_mode=False, recovery_url=None):
        super().__init__(parent)
        self.advanced = advanced_edit  # the dialog's Advanced settings box; changes here are saved when that dialog is accepted
        self.recovery_mode = recovery_mode
        self.recovery_url = recovery_url or flaresolverr.TEST_URL
        self.setWindowTitle(_('Royal Road setup (FlareSolverr)'))
        self.resize(760, 680)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.program = QLabel()
        self.program.setWordWrap(True)
        self.choose_button = QPushButton(_('Choose FlareSolverr program…'))
        self.choose_button.clicked.connect(self.choose_program)
        self.start_button = QPushButton(_('Start FlareSolverr'))
        self.start_button.clicked.connect(self.start_program)
        self.stop_button = QPushButton(_('Stop FlareSolverr'))
        self.stop_button.clicked.connect(self.stop_program)

        self.guide_browser = QTextBrowser()
        self.guide_browser.setOpenExternalLinks(True)
        self.guide_browser.setMarkdown(guide_text())

        self.method = 0 if prefs['flaresolverr_path'] else 1

        self.addPage(StartupMethodPage(self))
        self.addPage(MethodConfigPage(self))
        self.addPage(HealthCheckPage(self))
        self.addPage(ApplySettingsPage(self))
        self.restart()

        self.show_enabled_state()
        self.refresh_program()

        self.finished.connect(self._on_finished)

    def _on_finished(self, result):
        if result == QWizard.DialogCode.Accepted or result == 1:
            self.enable()

    def settings(self):
        text = self.advanced.toPlainText() if hasattr(self.advanced, 'toPlainText') else (self.advanced or '')
        return flaresolverr.settings_from_ini(text)

    def show_enabled_state(self):
        on = flaresolverr.is_enabled(self.advanced.toPlainText())
        self.status.setText(_('FlareSolverr is switched on for Royal Road in Advanced settings.') if on else
                            _('FlareSolverr is not switched on for Royal Road yet (the last button does that).'))

    def copy_docker(self):
        QApplication.clipboard().setText(flaresolverr.DOCKER_COMMAND)
        self.status.setText(_('Docker command copied. Paste it into a terminal (Command Prompt or PowerShell on Windows).'))

    def _run(self, title, func):
        task = run_task(self, title, func)
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
        if not path:
            self.status.setText('No FlareSolverr program chosen.')
            return
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

    def pageCount(self):
        return len(self.pageIds())

    def enable(self):
        text = self.advanced.toPlainText() if hasattr(self.advanced, 'toPlainText') else (self.advanced or '')
        new_text, changed = flaresolverr.enable_in_ini(text)
        if changed:
            if hasattr(self.advanced, 'setPlainText'):
                self.advanced.setPlainText(new_text)
            else:
                self.advanced = new_text
            prefs['advanced_ini'] = new_text
        self.show_enabled_state()
        self.status.setText(self.status.text() + (_(' Saved when you press OK in the download window.') if changed else ''))


RoyalRoadSetupDialog = FlareSolverrSetupWizard


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
