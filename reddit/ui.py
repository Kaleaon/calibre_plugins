"""Dialogs for following Reddit stories. Network work happens in the action, never here."""
import os
import time
import webbrowser

from qt.core import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
                     QLineEdit, QMessageBox, QPushButton, QRadioButton, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, Qt, QTimer, QVBoxLayout, QWidget)
try:
    from qt.core import _
except ImportError:
    _ = lambda s: s

try:
    from calibre_plugins.reddit_follower import core, login, subscription
except ImportError:
    import core, login, subscription
from calibre_plugins.reddit_follower.config import prefs
from calibre_plugins.reddit_follower.guikit import EmptyStateOverlay, run_task
try:
    from calibre_plugins.reddit_follower.error_handler import show_friendly_error_dialog
except ImportError:
    try:
        from .error_handler import show_friendly_error_dialog
    except ImportError:
        from error_handler import show_friendly_error_dialog
from calibre_plugins.reddit_follower.storykit import Fetcher

ACCESS_NOTE = (
    'Two ways to read Reddit, and what is known about each:\n'
    '• Public feeds (no account): the same Atom feeds a feed reader uses. They need nothing from you, but only reach the newest '
    'posts, and Reddit\'s robots.txt asks automated clients to stay out, so this is a light, personal-reader use. Reddit may refuse '
    '(HTTP 429/403); when it does the plugin stops and tries later instead of pushing.\n'
    '• Official API (your own credentials): the route Reddit documents. Reddit now requires approval before new apps get API '
    'access (per its Responsible Builder Policy; I could not confirm the current process from Reddit itself). Register an app of '
    'type "installed app" at reddit.com/prefs/apps once approved, enter its client id here and use Log in with Reddit. This path is written to '
    'Reddit\'s documentation but has not been tried against the live service.'
)


class SourceEditDialog(QDialog):
    """Add or edit one feed source rule for a series."""
    def __init__(self, parent, source=None):
        super().__init__(parent)
        self.setWindowTitle('Feed source rule')
        self.resize(500, 260)
        self.source = source or {}
        layout = QVBoxLayout(self)
        form = QFormLayout()

        src_text = core.source_text(self.source) if self.source.get('kind') else ''
        self.where = QLineEdit(src_text)
        self.where.setPlaceholderText('r/HFY   or   u/author   or search address')
        form.addRow('Where to look', self.where)

        self.title_filter = QLineEdit(self.source.get('title_filter', ''))
        self.title_filter.setPlaceholderText('Only posts matching title (or re:regex)')
        form.addRow(_('Title contains'), self.title_filter)

        self.author_filter = QLineEdit(self.source.get('author_filter', ''))
        self.author_filter.setPlaceholderText('Only posts by author (optional)')
        form.addRow(_('Posted by'), self.author_filter)

        self.flair_filter = QLineEdit(self.source.get('flair_filter', ''))
        self.flair_filter.setPlaceholderText('Only posts with flair (optional)')
        form.addRow(_('Flair contains'), self.flair_filter)

        layout.addLayout(form)
        self.msg = QLabel('')
        self.msg.setWordWrap(True)
        layout.addWidget(self.msg)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.try_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def try_accept(self):
        txt = self.where.text().strip()
        if not txt:
            self.msg.setText('Enter where to look.')
            return
        try:
            core.parse_source(txt)
        except core.SourceError as exc:
            self.msg.setText(str(exc))
            return
        self.accept()

    def result_source(self):
        parsed = core.parse_source(self.where.text().strip())
        return {
            'kind': parsed['kind'],
            'subreddit': parsed.get('subreddit', ''),
            'user': parsed.get('user', ''),
            'query': parsed.get('query', ''),
            'title_filter': self.title_filter.text().strip(),
            'author_filter': self.author_filter.text().strip().lstrip('/').replace('u/', '', 1),
            'flair_filter': self.flair_filter.text().strip()
        }


class ManifestDialog(QDialog):
    """View post manifest entries and chapter order for a series."""
    def __init__(self, parent, follow, action):
        super().__init__(parent)
        self.follow = follow
        self.action = action
        self.setWindowTitle(f"Chapter Manifest \u2013 {follow['name']}")
        self.resize(780, 420)
        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(['Seq', 'Chapter Title', 'Author', 'Created', 'Link', 'Status'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self):
        reg = self.action.registry()
        entries = reg.get_manifest(self.follow['id'])
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            created_str = time.strftime('%Y-%m-%d %H:%M', time.localtime(entry.get('created', 0.0)))
            status = 'Duplicate' if entry.get('is_duplicate') else 'Canonical'
            cells = [
                str(entry.get('sequence_order', row + 1)),
                entry.get('chapter_title') or entry.get('title', ''),
                entry.get('author_attribution') or entry.get('author', ''),
                created_str,
                entry.get('link', ''),
                status
            ]
            for col, txt in enumerate(cells):
                self.table.setItem(row, col, QTableWidgetItem(txt))
        self.table.resizeColumnsToContents()


class FollowDialog(QDialog):
    """Add or edit one followed series with multi-source support."""

    def __init__(self, parent, follow=None):
        super().__init__(parent)
        self.setWindowTitle(_('Follow a Reddit story') if follow is None else _('Edit followed story'))
        self.resize(640, 480)
        self.follow = follow
        self.sources = list(follow.get('sources', [])) if follow and follow.get('sources') else []
        if not self.sources and follow and follow.get('source'):
            self.sources = [{
                'kind': follow['source'].get('kind', 'subreddit'),
                'subreddit': follow['source'].get('subreddit', ''),
                'user': follow['source'].get('user', ''),
                'query': follow['source'].get('query', ''),
                'title_filter': follow.get('title_filter', ''),
                'author_filter': follow.get('author_filter', ''),
                'flair_filter': follow.get('flair_filter', '')
            }]

        layout = QVBoxLayout(self)
        if follow is None:
            row = QHBoxLayout()
            row.addWidget(QLabel(_('Quick start')))
            self.preset = QComboBox()
            self.preset.addItem(_('Fill in by hand…'))
            for preset in core.PRESETS:
                self.preset.addItem(preset['label'])
            self.preset.currentIndexChanged.connect(self.use_preset)
            row.addWidget(self.preset, 1)
            layout.addLayout(row)

        form = QFormLayout()
        self.name = QLineEdit(follow['name'] if follow else '')
        self.name.setPlaceholderText(_('Book title, for example Out of Cruel Space'))
        form.addRow(_('Book title'), self.name)

        primary_src = self.sources[0] if self.sources else (follow['source'] if follow and follow.get('source') else None)
        self.source = QLineEdit(core.source_text(primary_src) if primary_src else '')
        self.source.setPlaceholderText(_('r/HFY, r/sub1+sub2, u/author, u/auth1+auth2, or a Reddit search address'))
        self.source.textChanged.connect(self.check_source)
        form.addRow(_('Where to look'), self.source)

        primary_tf = self.sources[0].get('title_filter', '') if self.sources else (follow.get('title_filter', '') if follow else '')
        self.title_filter = QLineEdit(primary_tf)
        self.title_filter.setPlaceholderText(_('Only posts whose title contains this (or re:regular expression)'))
        form.addRow(_('Title contains'), self.title_filter)

        primary_af = self.sources[0].get('author_filter', '') if self.sources else (follow.get('author_filter', '') if follow else '')
        self.author_filter = QLineEdit(primary_af)
        self.author_filter.setPlaceholderText(_('Only posts by these users, separated by commas or pipes (e.g. Author1, Author2) (optional)'))
        form.addRow(_('Posted by'), self.author_filter)

        primary_ff = self.sources[0].get('flair_filter', '') if self.sources else (follow.get('flair_filter', '') if follow else '')
        self.flair_filter = QLineEdit(primary_ff)
        self.flair_filter.setPlaceholderText(_('Only posts with this flair (optional)'))
        form.addRow(_('Flair contains'), self.flair_filter)
        self.index_url = QLineEdit(follow.get('index_url', '') if follow else '')
        self.index_url.setPlaceholderText(_('Optional: a public Google Sheet listing the chapters'))
        form.addRow(_('Chapter index'), self.index_url)

        self.layout_box = QComboBox()
        self.layout_box.addItem(_('One book that grows, one chapter per post (a series)'), 'series')
        self.layout_box.addItem(_('A separate book for each post (many authors, one story per post)'), 'each')
        if follow and follow.get('layout') == 'each':
            self.layout_box.setCurrentIndex(1)
        form.addRow(_('Make'), self.layout_box)
        layout.addLayout(form)

        # Multi-source section
        layout.addWidget(QLabel(_('Additional Feed Sources (for multi-author or multi-subreddit series):')))
        self.sources_table = QTableWidget(0, 4)
        self.sources_table.setHorizontalHeaderLabels([_('Where'), _('Title filter'), _('Author filter'), _('Flair filter')])
        self.sources_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.sources_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.sources_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.sources_table)

        src_btn_row = QHBoxLayout()
        add_src_btn = QPushButton(_('Add feed source…'))
        add_src_btn.clicked.connect(self.add_source)
        rem_src_btn = QPushButton(_('Remove feed source'))
        rem_src_btn.clicked.connect(self.remove_source)
        src_btn_row.addWidget(add_src_btn)
        src_btn_row.addWidget(rem_src_btn)
        src_btn_row.addStretch(1)
        layout.addLayout(src_btn_row)

        self.title_from_body = QCheckBox(_('The first line of each post is its chapter title (use it to name the chapter)'))
        self.title_from_body.setChecked(bool(follow and follow.get('title_from_body')))
        layout.addWidget(self.title_from_body)

        self.author_note = QCheckBox(_("Also keep the author's own comment under each chapter (official API only)"))
        self.author_note.setChecked(bool(follow and follow.get('author_note')))
        layout.addWidget(self.author_note)

        self.message = QLabel('')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.try_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.refresh_sources_table()
        self.check_source()

    def refresh_sources_table(self):
        self.sources_table.setRowCount(len(self.sources))
        for row, src in enumerate(self.sources):
            cells = [
                core.describe_source(src),
                src.get('title_filter', ''),
                src.get('author_filter', ''),
                src.get('flair_filter', '')
            ]
            for col, text in enumerate(cells):
                self.sources_table.setItem(row, col, QTableWidgetItem(text))

    def add_source(self):
        dialog = SourceEditDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.sources.append(dialog.result_source())
            self.refresh_sources_table()

    def remove_source(self):
        row = self.sources_table.currentRow()
        if 0 <= row < len(self.sources):
            del self.sources[row]
            self.refresh_sources_table()

    def use_preset(self, index):
        if index <= 0:
            return
        preset = core.PRESETS[index - 1]
        self.name.setText(preset['name'])
        self.source.setText(preset['source'])
        self.title_filter.setText(preset['title_filter'])
        self.author_filter.setText(preset['author_filter'])
        self.flair_filter.setText(preset.get('flair_filter', ''))
        self.index_url.setText(preset.get('index_url', ''))
        self.title_from_body.setChecked(bool(preset.get('title_from_body')))
        self.layout_box.setCurrentIndex(1 if preset.get('layout') == 'each' else 0)
        self.sources = []
        self.refresh_sources_table()

    def check_source(self, *_):
        text = self.source.text().strip()
        if not text:
            self.message.setText('')
            return None
        try:
            src = core.parse_source(text)
        except core.SourceError as exc:
            self.message.setText(str(exc))
            return None
        self.message.setText(f'Will follow {core.describe_source(src)}.')
        return src

    def sync_primary_source(self):
        src = self.check_source()
        if src:
            primary = {
                'kind': src['kind'],
                'subreddit': src.get('subreddit', ''),
                'user': src.get('user', ''),
                'query': src.get('query', ''),
                'title_filter': self.title_filter.text().strip(),
                'author_filter': self.author_filter.text().strip().lstrip('/').replace('u/', '', 1),
                'flair_filter': self.flair_filter.text().strip()
            }
            if self.sources:
                self.sources[0] = primary
            else:
                self.sources = [primary]

    def try_accept(self):
        if self.index_url.text().strip():
            try:
                core.sheet_csv_url(self.index_url.text())
            except core.SourceError as exc:
                self.message.setText(str(exc))
                return
        if self.check_source() is None and not self.sources:
            if not self.source.text().strip():
                self.message.setText('Enter where to look first.')
            return
        self.sync_primary_source()
        self.accept()

    def result_follow(self):
        self.sync_primary_source()
        res = core.new_follow(
            self.name.text(),
            self.source.text(),
            self.title_filter.text(),
            self.author_filter.text(),
            self.author_note.isChecked(),
            self.layout_box.currentData(),
            self.flair_filter.text(),
            self.index_url.text(),
            self.title_from_body.isChecked(),
            sources=self.sources
        )
        if self.follow and self.follow.get('id'):
            res['id'] = self.follow['id']
            res['last_checked'] = 0.0
            res['last_status'] = 'changed; will be checked again'
        return res


class SettingsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle('Reddit Story Follower settings')
        self.resize(640, 560)
        layout = QVBoxLayout(self)
        note = QLabel(ACCESS_NOTE)
        note.setWordWrap(True)
        layout.addWidget(note)
        self.rss = QRadioButton('Read public feeds (no account)')
        self.api = QRadioButton('Use the official API with my own credentials')
        (self.api if prefs['mode'] == 'api' else self.rss).setChecked(True)
        layout.addWidget(self.rss)
        layout.addWidget(self.api)
        form = QFormLayout()
        self.client_id = QLineEdit(prefs['client_id'])
        self.client_secret = QLineEdit(prefs['client_secret'])
        self.client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.client_secret.setPlaceholderText('Leave empty for an "installed app"')
        self.username = QLineEdit(prefs['username'])
        self.username.setPlaceholderText('Your Reddit username, shown to Reddit in the user agent')
        form.addRow('Client id', self.client_id)
        form.addRow('Client secret', self.client_secret)
        form.addRow('Reddit username', self.username)
        layout.addLayout(form)
        self.refresh_token, self.account_name = prefs['refresh_token'], prefs['account_name']
        row = QHBoxLayout()
        self.login_button = QPushButton('Log in with Reddit…')
        self.login_button.setToolTip('Opens Reddit in your browser to approve read access. Your password never reaches this plugin.')
        self.login_button.clicked.connect(self.log_in)
        self.logout_button = QPushButton('Log out')
        self.logout_button.clicked.connect(self.log_out)
        self.login_status = QLabel('')
        row.addWidget(self.login_button)
        row.addWidget(self.logout_button)
        row.addWidget(self.login_status, 1)
        layout.addLayout(row)
        hint = QLabel('Register the app at reddit.com/prefs/apps with redirect uri exactly:  ' + login.REDIRECT_URI)
        hint.setWordWrap(True)
        hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(hint)
        self.api.toggled.connect(self.sync)
        self.sync()
        self.auto = QCheckBox('Check for new chapters automatically while Calibre is open')
        self.auto.setChecked(bool(prefs['auto_check']))
        layout.addWidget(self.auto)
        row = QHBoxLayout()
        row.addWidget(QLabel('Check each story at most every'))
        self.hours = QDoubleSpinBox()
        self.hours.setRange(core.MIN_CHECK_HOURS, 168)
        self.hours.setDecimals(1)
        self.hours.setSuffix(' hours')
        self.hours.setValue(max(float(prefs['check_hours']), core.MIN_CHECK_HOURS))
        row.addWidget(self.hours)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(QLabel('Checks only run while Calibre is open (there is no background service), and the interval is never shorter than one hour.'))
        row = QHBoxLayout()
        test = QPushButton('Test connection')
        test.setToolTip('Makes one request to r/HFY with the settings above (nothing is saved or changed)')
        test.clicked.connect(self.test_connection)
        row.addWidget(test)
        self.switch_api_button = QPushButton('Switch to Official API')
        self.switch_api_button.setToolTip('Switch to Official API mode to set up credentials')
        self.switch_api_button.clicked.connect(self.switch_to_api_mode)
        self.switch_api_button.setVisible(False)
        row.addWidget(self.switch_api_button)
        self.test_result = QLabel('')
        self.test_result.setWordWrap(True)
        row.addWidget(self.test_result, 1)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def sync(self, *_):
        for widget in (self.client_id, self.client_secret, self.username):
            widget.setEnabled(self.api.isChecked())
        self.login_button.setEnabled(self.api.isChecked())
        self.logout_button.setEnabled(bool(self.refresh_token))
        self.login_status.setText(f'Logged in as u/{self.account_name}' if self.refresh_token and self.account_name
                                  else 'Logged in' if self.refresh_token else 'Not logged in')

    open_browser = staticmethod(webbrowser.open)
    login_endpoints = {}  # tests point these at a stand-in server

    def log_in(self):
        client_id, secret = self.client_id.text().strip(), self.client_secret.text().strip()
        if not client_id:
            QMessageBox.warning(self, 'Reddit Story Follower', 'Enter your app\'s client id first.')
            return
        try:
            with login.CallbackServer() as server:
                task = run_task(self, 'Waiting for you to approve the login in your browser…', lambda t: login.log_in(
                    Fetcher(cancelled=t.cancelled), client_id, secret, server, self.open_browser, t.cancelled,
                    **{k: v for k, v in self.login_endpoints.items() if k != 'revoke_url'}))
        except login.LoginError as exc:
            show_friendly_error_dialog(self, 'Reddit Story Follower', exc, show=True)
            return
        if task.error:
            if 'cancelled' not in str(task.error).lower():
                show_friendly_error_dialog(self, 'Reddit Story Follower', task.error, show=True)
            return
        if task.result:
            self.refresh_token = task.result['refresh_token']
            self.account_name = task.result['username']
            prefs['refresh_token'], prefs['account_name'] = self.refresh_token, self.account_name
            self.sync()

    def log_out(self):
        if self.refresh_token:
            revoked = login.revoke(Fetcher(), self.client_id.text().strip(), self.client_secret.text().strip(), self.refresh_token,
                                   self.login_endpoints.get('revoke_url'))
            if not revoked:
                QMessageBox.information(self, 'Reddit Story Follower', 'Reddit could not be reached to cancel the login, so it was only '
                                        'removed here. You can also remove it at reddit.com/prefs/apps.')
        self.refresh_token = self.account_name = ''
        prefs['refresh_token'] = prefs['account_name'] = ''
        self.sync()

    def switch_to_api_mode(self):
        self.api.setChecked(True)
        self.switch_api_button.setVisible(False)
        self.test_result.setText('Switched to official API mode. Register an app at reddit.com/prefs/apps and enter your client ID above.')

    def test_connection(self):
        mode = 'api' if self.api.isChecked() else 'rss'
        client_id, secret, username = self.client_id.text().strip(), self.client_secret.text().strip(), self.username.text().strip()
        src = core.parse_source('r/HFY')
        task = run_task(self, 'Testing Reddit…', lambda t: core.probe(core.make_source(mode, client_id, secret, self.account_name or username, t.cancelled, self.refresh_token), src, limit=5))
        err_msg = str(task.error) if task.error else (task.result[1] if task.result and not task.result[0] else None)
        if err_msg:
            if any(code in err_msg for code in ('429', '403')) or any(w in err_msg.lower() for w in ('rate', 'slow down', 'forbidden', 'too many requests')):
                if mode == 'rss':
                    if '403' in err_msg or 'forbidden' in err_msg.lower():
                        self.test_result.setText('Problem: Reddit restricted access (HTTP 403). Public feeds share rate limits across IP addresses. Switch to Official API mode with your own credentials for reliable access.')
                    else:
                        self.test_result.setText('Problem: Reddit asked us to slow down (HTTP 429). Public feeds share rate limits across IP addresses. Switch to Official API mode with your own credentials for reliable access.')
                    self.switch_api_button.setVisible(True)
                else:
                    self.test_result.setText(f'Problem: Reddit API error ({err_msg}). Check client ID, secret, and account credentials.')
                    self.switch_api_button.setVisible(False)
            else:
                self.test_result.setText(f'Problem: {err_msg}')
                self.switch_api_button.setVisible(False)
        elif task.result is not None:
            self.test_result.setText('OK: ' + task.result[1])
            self.switch_api_button.setVisible(False)

    def save(self):
        if self.api.isChecked() and not self.client_id.text().strip():
            QMessageBox.warning(self, 'Reddit Story Follower', 'Enter your app\'s client id to use the official API, or choose public feeds.')
            return
        prefs['mode'] = 'api' if self.api.isChecked() else 'rss'
        prefs['client_id'] = self.client_id.text().strip()
        prefs['client_secret'] = self.client_secret.text().strip()
        prefs['username'] = self.username.text().strip()
        prefs['auto_check'] = self.auto.isChecked()
        prefs['check_hours'] = max(self.hours.value(), core.MIN_CHECK_HOURS)
        self.accept()


class DiscoverDialog(QDialog):
    """Search or browse a subreddit such as r/HFY, see the stories grouped into series, and follow a whole series."""

    def __init__(self, parent, action):
        super().__init__(parent)
        self.action = action
        self.chosen = None
        self.chosen_many = []
        self.groups = []
        self.setWindowTitle('Find stories on Reddit')
        self.resize(820, 520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.subreddit = QLineEdit('HFY')
        form.addRow('Subreddit', self.subreddit)
        self.words = QLineEdit()
        self.words.setPlaceholderText('Search words, for example deathworld, or leave empty to browse')
        self.words.returnPressed.connect(self.search)
        form.addRow('Search words', self.words)
        self.browse = QComboBox()
        for key, label in core.BROWSE:
            self.browse.addItem(label, key)
        self.browse.setCurrentIndex(1)
        form.addRow('Order', self.browse)
        self.pages = QSpinBox()
        self.pages.setRange(1, 10)
        self.pages.setValue(3)
        self.pages.setToolTip('Each page is up to 100 posts and one request to Reddit. Looking through more pages finds more series.')
        form.addRow('Pages to look through', self.pages)
        self.flair = QLineEdit()
        self.flair.setPlaceholderText('Only this flair, for example OC-FirstOfSeries (official API only; optional)')
        form.addRow('Flair contains', self.flair)
        layout.addLayout(form)
        row = QHBoxLayout()
        go = QPushButton('Search')
        go.clicked.connect(self.search)
        row.addWidget(go)
        self.message = QLabel('Series are guessed from the author and the part numbers in the titles (Part 3, 14, VIII).')
        self.message.setWordWrap(True)
        row.addWidget(self.message, 1)
        layout.addLayout(row)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(['Series or story', 'Author', 'Posts found', 'Parts seen', 'Rating', 'Flair'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 330)
        self.table.doubleClicked.connect(lambda *_: self.follow_selected())
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.follow_button = QPushButton('Follow the whole series…')
        self.follow_button.setToolTip('Looks through the author\'s posts for every part, shows what it found, then lets you adjust and follow it')
        self.follow_button.clicked.connect(self.follow_selected)
        row.addWidget(self.follow_button)
        row.addSpacing(24)
        row.addWidget(QLabel('Or add the top'))
        self.top_count = QSpinBox()
        self.top_count.setRange(1, 25)
        self.top_count.setValue(5)
        row.addWidget(self.top_count)
        row.addWidget(QLabel('series with at least'))
        self.min_parts = QSpinBox()
        self.min_parts.setRange(2, 100)
        self.min_parts.setValue(3)
        row.addWidget(self.min_parts)
        row.addWidget(QLabel('parts found'))
        self.top_button = QPushButton('Add top rated…')
        self.top_button.setToolTip('Picks the highest rated series in the list above (the rating is the total score of the posts found; '
                                   'it needs the official API) and follows them all, skipping any you already follow')
        self.top_button.clicked.connect(self.add_top)
        row.addWidget(self.top_button)
        row.addStretch(1)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.overlay = EmptyStateOverlay(
            self.table,
            icon='🔎',
            title='Find stories on Reddit',
            message='Enter search terms or select a subreddit and click "Search" to discover stories.',
            action_text='Search',
            on_action=go.click
        )
        self.overlay.show_empty()

    def add_top(self):
        if not self.groups:
            self.message.setText('Search or browse first (for example Order: Top of all time), then add the top rated.')
            return
        if not any(g.get('score') for g in self.groups):
            self.message.setText('Ratings need the official API (Settings); the public feeds carry no scores.')
            return
        picked = core.top_series(self.groups, self.top_count.value(), self.min_parts.value(), prefs['follows'])
        if not picked:
            self.message.setText('No series with that many parts found that you do not already follow. Look through more pages or lower the number of parts.')
            return
        text = '\n'.join(f"{g['name']} \u2013 {g['author']}  (rating {g['score']:,}, {len(g['posts'])} parts seen)" for g in picked)
        if QMessageBox.question(self, 'Follow these series?', f'{len(picked)} highest rated series:\n\n{text}\n\nFollow them all as growing books? '
                                'Each is collected in full from its author\'s posts, which can take a few minutes.') != QMessageBox.StandardButton.Yes:
            return
        self.chosen_many = [core.series_follow(g) for g in picked]
        self.accept()

    def search(self):
        try:
            src = core.discover_source(self.subreddit.text(), self.words.text(), self.browse.currentData())
        except core.SourceError as exc:
            self.message.setText(str(exc))
            return
        flair = self.flair.text().strip()
        try:
            task = run_task(self, 'Searching Reddit…', lambda t: core.discover(
                self.action.make_source(t.cancelled), src, flair, pages=self.pages.value(), cancelled=t.cancelled, progress=lambda msg: setattr(t, 'status', msg)))
        except core.SourceError as exc:
            self.message.setText(str(exc))
            return
        if task.error:
            self.message.setText(f'The search failed: {task.error}')
            return
        if task.result is None:
            return
        self.groups = core.rank_groups(task.result, 'score' if any(g.get('score') for g in task.result) else 'posts')
        self.table.setRowCount(len(self.groups))
        for row, g in enumerate(self.groups):
            nums = sorted(g['numbers'])
            parts = f'{nums[0]}\u2013{nums[-1]}' if len(nums) > 1 else (str(nums[0]) if nums else '')
            for col, text in enumerate([g['name'] + ('' if g['is_series'] else '  (single story)'), g['author'], str(len(g['posts'])), parts,
                                        f"{g['score']:,}" if g.get('score') else '', ', '.join(sorted(g['flairs']))[:40]]):
                self.table.setItem(row, col, QTableWidgetItem(text))
        series = sum(1 for g in self.groups if g['is_series'])
        self.message.setText(_('{series} series and {other} single stories among the posts looked through. '
                             'Counts are only what the search showed; following a series reads all of its parts.').format(series=series, other=len(self.groups) - series) if self.groups
                             else _('Nothing found. Try other words, another order, or no flair.'))
        if self.groups:
            self.overlay.hide_empty()
        else:
            self.overlay.show_empty(
                title='No stories found',
                message='No series or stories matched your search criteria. Try using different keywords, order, or clearing flair filters.',
                icon='🔎'
            )

    def follow_selected(self):
        rows = self.table.selectionModel().selectedRows()
        if len(rows) != 1:
            self.message.setText(_('Select one series in the list first.'))
            return
        group = self.groups[rows[0].row()]
        follow = core.series_follow(group)
        try:
            task = run_task(self, _('Looking for every part…'), lambda t: core.preview_series(
                self.action.make_source(t.cancelled), follow, cancelled=t.cancelled, progress=lambda msg: setattr(t, 'status', msg)))
        except core.SourceError as exc:
            self.message.setText(str(exc))
            return
        if task.error:
            self.message.setText(_('Looking for the parts failed: {error}').format(error=task.error))
            return
        if task.result is None:
            return
        found = task.result
        if not found['count']:
            self.message.setText(_('That author has no posts under this name any more.'))
            return
        text = (f"{follow['name']} by {group['author']}: {found['count']} post(s) from {found['first']} to {found['last']}.\n\n"
                + '\n'.join(found['titles']) + '\n\nFollow it as one book that grows?')
        if QMessageBox.question(self, 'Follow this series?', text) != QMessageBox.StandardButton.Yes:
            return
        dialog = FollowDialog(self, follow)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.chosen = dialog.result_follow()
            self.accept()


class SubscriptionExchangeDialog(QDialog):
    """Multi-tab dialog for subscription export and import with category mapping and conflict resolution."""

    def __init__(self, parent, action=None):
        super().__init__(parent)
        self.action = action
        self.preview_subs = []
        self.setWindowTitle('Subscription Exchange')
        self.resize(780, 520)

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs)

        # --- Tab 1: Import ---
        import_widget = QWidget()
        import_layout = QVBoxLayout(import_widget)

        file_row = QHBoxLayout()
        file_row.addWidget(QLabel('File to import:'))
        self.import_file_path = QLineEdit()
        self.import_file_path.setPlaceholderText('Select an OPML (.opml, .xml) or JSON (.json) subscription file…')
        file_row.addWidget(self.import_file_path, 1)
        browse_btn = QPushButton('Browse…')
        browse_btn.clicked.connect(self.browse_import_file)
        file_row.addWidget(browse_btn)
        import_layout.addLayout(file_row)

        self.import_status_label = QLabel('Select a subscription file exported from Feedly, NetNewsWire, or another reader.')
        self.import_status_label.setWordWrap(True)
        import_layout.addWidget(self.import_status_label)

        self.import_table = QTableWidget(0, 5)
        self.import_table.setHorizontalHeaderLabels(['Import', 'Book Title', 'Source Address', 'Categories / Tags', 'Match Status'])
        self.import_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.import_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.import_table.horizontalHeader().setStretchLastSection(True)
        import_layout.addWidget(self.import_table, 1)

        tbl_ctrl_row = QHBoxLayout()
        sel_all_btn = QPushButton('Select All')
        sel_all_btn.clicked.connect(lambda: self.set_import_selection(True))
        desel_all_btn = QPushButton('Deselect All')
        desel_all_btn.clicked.connect(lambda: self.set_import_selection(False))
        tbl_ctrl_row.addWidget(sel_all_btn)
        tbl_ctrl_row.addWidget(desel_all_btn)
        tbl_ctrl_row.addStretch(1)
        import_layout.addLayout(tbl_ctrl_row)

        strat_row = QHBoxLayout()
        strat_row.addWidget(QLabel('Duplicate Strategy:'))
        self.strategy_combo = QComboBox()
        self.strategy_combo.addItem('Skip duplicate subscriptions', subscription.STRATEGY_SKIP)
        self.strategy_combo.addItem('Overwrite existing follows', subscription.STRATEGY_OVERWRITE)
        self.strategy_combo.addItem('Create unique suffixed entries', subscription.STRATEGY_ADD_SUFFIX)
        strat_row.addWidget(self.strategy_combo)

        strat_row.addSpacing(16)
        strat_row.addWidget(QLabel('Suffix:'))
        self.suffix_input = QLineEdit(' (Imported)')
        self.suffix_input.setFixedWidth(120)
        strat_row.addWidget(self.suffix_input)
        strat_row.addStretch(1)
        import_layout.addLayout(strat_row)

        imp_btn_row = QHBoxLayout()
        self.exec_import_btn = QPushButton('Import Selected Subscriptions')
        self.exec_import_btn.clicked.connect(self.execute_import)
        imp_btn_row.addWidget(self.exec_import_btn)
        imp_btn_row.addStretch(1)
        import_layout.addLayout(imp_btn_row)

        self.tabs.addTab(import_widget, 'Import Subscriptions')

        # --- Tab 2: Export ---
        export_widget = QWidget()
        export_layout = QVBoxLayout(export_widget)

        export_layout.addWidget(QLabel('Select followed stories to export:'))

        self.export_table = QTableWidget(0, 4)
        self.export_table.setHorizontalHeaderLabels(['Export', 'Book Title', 'Source Address', 'Categories / Tags'])
        self.export_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.export_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.export_table.horizontalHeader().setStretchLastSection(True)
        export_layout.addWidget(self.export_table, 1)

        exp_ctrl_row = QHBoxLayout()
        exp_sel_all = QPushButton('Select All')
        exp_sel_all.clicked.connect(lambda: self.set_export_selection(True))
        exp_desel_all = QPushButton('Deselect All')
        exp_desel_all.clicked.connect(lambda: self.set_export_selection(False))
        exp_ctrl_row.addWidget(exp_sel_all)
        exp_ctrl_row.addWidget(exp_desel_all)
        exp_ctrl_row.addStretch(1)
        export_layout.addLayout(exp_ctrl_row)

        exp_fmt_row = QHBoxLayout()
        exp_fmt_row.addWidget(QLabel('Export Format:'))
        self.export_format_combo = QComboBox()
        self.export_format_combo.addItem('OPML (.opml)', 'opml')
        self.export_format_combo.addItem('JSON (.json)', 'json')
        exp_fmt_row.addWidget(self.export_format_combo)

        exp_fmt_row.addSpacing(16)
        self.include_categories_cb = QCheckBox('Include category folder hierarchy')
        self.include_categories_cb.setChecked(True)
        exp_fmt_row.addWidget(self.include_categories_cb)
        exp_fmt_row.addStretch(1)
        export_layout.addLayout(exp_fmt_row)

        exp_btn_row = QHBoxLayout()
        exec_export_btn = QPushButton('Export Selected Subscriptions…')
        exec_export_btn.clicked.connect(self.execute_export)
        exp_btn_row.addWidget(exec_export_btn)
        exp_btn_row.addStretch(1)
        export_layout.addLayout(exp_btn_row)

        self.tabs.addTab(export_widget, 'Export Subscriptions')

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.populate_export_table()

    def browse_import_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, 'Select Subscription File', '', 'Subscription Files (*.opml *.xml *.json);;OPML Files (*.opml *.xml);;JSON Files (*.json);;All Files (*)'
        )
        if file_path:
            self.import_file_path.setText(file_path)
            self.load_import_file(file_path)

    def load_import_file(self, file_path):
        if not os.path.exists(file_path):
            self.import_status_label.setText('Error: Selected file does not exist.')
            return
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as stream:
                content = stream.read()

            if file_path.lower().endswith('.json') or content.strip().startswith('{') or content.strip().startswith('['):
                subs = subscription.parse_json(content)
            else:
                subs = subscription.parse_opml(content)

            self.preview_subs = subs
            self.import_table.setRowCount(len(subs))
            existing_follows = prefs['follows']

            for r, sub in enumerate(subs):
                chk_item = QTableWidgetItem()
                chk_item.setCheckState(Qt.CheckState.Checked)
                self.import_table.setItem(r, 0, chk_item)

                self.import_table.setItem(r, 1, QTableWidgetItem(sub.get('name', '')))
                src_txt = sub.get('source_text') or (core.describe_source(sub['source']) if sub.get('source') else '')
                self.import_table.setItem(r, 2, QTableWidgetItem(src_txt))
                cats = sub.get('categories', [])
                self.import_table.setItem(r, 3, QTableWidgetItem(', '.join(cats)))

                # Check match status
                matched = False
                for ex in existing_follows:
                    if subscription.is_duplicate(sub, ex):
                        matched = True
                        self.import_table.setItem(r, 4, QTableWidgetItem(f"Duplicate of '{ex['name']}'"))
                        break
                if not matched:
                    self.import_table.setItem(r, 4, QTableWidgetItem('New'))

            self.import_table.resizeColumnsToContents()
            self.import_status_label.setText(f'Loaded {len(subs)} subscription(s) from {os.path.basename(file_path)}.')

        except Exception as exc:
            self.import_status_label.setText(f'Failed to parse subscription file: {exc}')
            self.preview_subs = []
            self.import_table.setRowCount(0)

    def set_import_selection(self, checked):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for r in range(self.import_table.rowCount()):
            item = self.import_table.item(r, 0)
            if item:
                item.setCheckState(state)

    def execute_import(self):
        selected_subs = []
        for r in range(self.import_table.rowCount()):
            item = self.import_table.item(r, 0)
            if item and item.checkState() == Qt.CheckState.Checked and r < len(self.preview_subs):
                selected_subs.append(self.preview_subs[r])

        if not selected_subs:
            QMessageBox.warning(self, 'Subscription Exchange', 'No subscriptions selected for import.')
            return

        strat = self.strategy_combo.currentData()
        sfx = self.suffix_input.text().strip() if strat == subscription.STRATEGY_ADD_SUFFIX else ''
        updated_follows, stats = subscription.merge_subscriptions(prefs['follows'], selected_subs, strategy=strat, suffix=sfx)
        prefs['follows'] = updated_follows

        QMessageBox.information(
            self,
            'Import Complete',
            f"Successfully processed {len(selected_subs)} selected subscription(s):\n\n"
            f"• Added: {stats['added']}\n"
            f"• Updated: {stats['updated']}\n"
            f"• Skipped: {stats['skipped']}"
        )
        self.populate_export_table()

    def populate_export_table(self):
        follows = prefs['follows']
        self.export_table.setRowCount(len(follows))
        for r, follow in enumerate(follows):
            chk_item = QTableWidgetItem()
            chk_item.setCheckState(Qt.CheckState.Checked)
            chk_item.setData(Qt.ItemDataRole.UserRole, follow['id'])
            self.export_table.setItem(r, 0, chk_item)

            self.export_table.setItem(r, 1, QTableWidgetItem(follow.get('name', '')))
            src_text = core.describe_source(follow['source']) if isinstance(follow.get('source'), dict) else follow.get('source_text', '')
            self.export_table.setItem(r, 2, QTableWidgetItem(src_text))

            cats = follow.get('categories') or follow.get('tags') or []
            if isinstance(cats, str):
                cats = [c.strip() for c in cats.split(',') if c.strip()]
            self.export_table.setItem(r, 3, QTableWidgetItem(', '.join(cats)))

        self.export_table.resizeColumnsToContents()

    def set_export_selection(self, checked):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for r in range(self.export_table.rowCount()):
            item = self.export_table.item(r, 0)
            if item:
                item.setCheckState(state)

    def execute_export(self):
        follows = prefs['follows']
        selected_follows = []
        for r in range(self.export_table.rowCount()):
            item = self.export_table.item(r, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                fid = item.data(Qt.ItemDataRole.UserRole)
                m = next((f for f in follows if f['id'] == fid), None)
                if m:
                    selected_follows.append(m)

        if not selected_follows:
            QMessageBox.warning(self, 'Subscription Exchange', 'No followed stories selected for export.')
            return

        fmt = self.export_format_combo.currentData()
        default_ext = '.opml' if fmt == 'opml' else '.json'
        file_filter = 'OPML Files (*.opml);;XML Files (*.xml);;All Files (*)' if fmt == 'opml' else 'JSON Files (*.json);;All Files (*)'

        file_path, _ = QFileDialog.getSaveFileName(self, 'Export Subscriptions', f'reddit_subscriptions{default_ext}', file_filter)
        if not file_path:
            return

        # Strip category hierarchy if unchecked
        export_list = selected_follows
        if not self.include_categories_cb.isChecked():
            export_list = [dict(f, categories=[]) for f in selected_follows]

        try:
            if fmt == 'opml':
                content = subscription.generate_opml(export_list)
            else:
                content = subscription.generate_json(export_list)

            with open(file_path, 'w', encoding='utf-8') as stream:
                stream.write(content)

            QMessageBox.information(
                self,
                'Export Complete',
                f'Successfully exported {len(export_list)} subscription(s) to:\n{file_path}'
            )

        except Exception as exc:
            QMessageBox.warning(self, 'Export Error', f'Failed to write export file: {exc}')


class ManageDialog(QDialog):
    """The list of followed stories, with add, edit, manifest, remove and check buttons."""
    COLUMNS = [_('Book'), _('Feed Sources'), _('Chapters'), _('Last checked'), _('Status')]

    def __init__(self, parent, action):
        super().__init__(parent)
        self.action = action
        self.setWindowTitle(_('Followed Reddit stories'))
        self.resize(880, 460)
        layout = QVBoxLayout(self)

        self.error_banner = QFrame(self)
        self.error_banner.setFrameShape(QFrame.Shape.StyledPanel)
        self.error_banner.setStyleSheet('QFrame { background-color: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; border-radius: 4px; padding: 4px; }')
        error_layout = QHBoxLayout(self.error_banner)
        error_layout.setContentsMargins(6, 2, 6, 2)
        self.error_label = QLabel(self.error_banner)
        self.error_label.setWordWrap(True)
        self.dismiss_error_button = QPushButton('Dismiss', self.error_banner)
        self.dismiss_error_button.clicked.connect(self.dismiss_error)
        error_layout.addWidget(self.error_label, 1)
        error_layout.addWidget(self.dismiss_error_button)
        layout.addWidget(self.error_banner)
        self.error_banner.setVisible(False)

        self.backoff_banner = QFrame(self)
        self.backoff_banner.setFrameShape(QFrame.Shape.StyledPanel)
        self.backoff_banner.setStyleSheet('QFrame { background-color: #fff3cd; color: #856404; border: 1px solid #ffeeba; border-radius: 4px; padding: 4px; }')
        backoff_layout = QHBoxLayout(self.backoff_banner)
        backoff_layout.setContentsMargins(6, 2, 6, 2)
        self.backoff_label = QLabel(self.backoff_banner)
        self.backoff_label.setWordWrap(True)
        self.reset_backoff_button = QPushButton('Reset Backoff & Retry', self.backoff_banner)
        self.reset_backoff_button.clicked.connect(self.reset_backoff_and_retry)
        backoff_layout.addWidget(self.backoff_label, 1)
        backoff_layout.addWidget(self.reset_backoff_button)
        layout.addWidget(self.backoff_banner)
        self.backoff_banner.setVisible(False)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        self.mode_label = QLabel('')
        layout.addWidget(self.mode_label)
        row1 = QHBoxLayout()
        for label, slot in ((_('Find stories…'), self.find), (_('Add…'), self.add), (_('Edit…'), self.edit), (_('Manifest…'), self.manifest), (_('Remove'), self.remove), (_('Queue manager…'), self.open_queue_manager), (_('Test selected'), self.test_selected)):
            button = QPushButton(label)
            button.clicked.connect(slot)
            row1.addWidget(button)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        for label, slot in ((_('Check selected now'), self.check_selected), (_('Check all now'), self.check_all), (_('Import / Export…'), self.subscription_exchange), (_('Export OPML…'), self.export_opml), (_('Import OPML…'), self.import_opml), (_('Settings…'), self.settings)):
            button = QPushButton(label)
            button.clicked.connect(slot)
            row2.addWidget(button)
        layout.addLayout(row2)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.overlay = EmptyStateOverlay(
            self.table,
            icon='📚',
            title='No followed stories',
            message='You are not following any Reddit stories yet. Discover and follow stories to keep your library updated.',
            action_text='Find stories…',
            on_action=self.find
        )

        self.update_timer = QTimer(self)
        self.update_timer.setInterval(1000)
        self.update_timer.timeout.connect(self.update_backoff_timer)
        self.update_timer.start()
        self.refresh()

    def open_queue_manager(self):
        self.action.open_queue_manager()
        self.refresh()

    def follows(self):
        if hasattr(self.action, 'get_follows'):
            res = self.action.get_follows()
            if isinstance(res, (list, tuple)):
                return res
        return prefs.get('follows', [])

    def dismiss_error(self):
        if hasattr(self.action, 'last_background_error'):
            self.action.last_background_error = None
        self.refresh()

    def reset_backoff_and_retry(self):
        if hasattr(self.action, 'backoff_until'):
            self.action.backoff_until = 0
        self.action.check_all_now()
        self.refresh()

    def update_backoff_timer(self):
        backoff_until = getattr(self.action, 'backoff_until', 0.0)
        try:
            backoff_until = float(backoff_until or 0.0)
        except (TypeError, ValueError):
            backoff_until = 0.0
        now = time.time()
        if backoff_until > now:
            remaining = max(0, int(backoff_until - now))
            mins, secs = divmod(remaining, 60)
            hours, mins = divmod(mins, 60)
            if hours > 0:
                time_str = f'{hours}h {mins}m {secs}s'
            else:
                time_str = f'{mins}m {secs}s'
            self.backoff_label.setText(f'Backoff active: retrying in {time_str}')
            self.backoff_banner.setVisible(True)
        else:
            self.backoff_banner.setVisible(False)

    def refresh(self):
        err = getattr(self.action, 'last_background_error', None)
        if err:
            self.error_label.setText(f'Last background check failed: {err}')
            self.error_banner.setVisible(True)
        else:
            self.error_banner.setVisible(False)

        self.update_backoff_timer()

        follows = self.follows()
        self.table.setRowCount(len(follows))
        for r, follow in enumerate(follows):
            checked = time.strftime('%Y-%m-%d %H:%M', time.localtime(follow['last_checked'])) if follow.get('last_checked') else _('never')
            sources = follow.get('sources') or ([follow.get('source')] if follow.get('source') else [])
            if len(sources) > 1:
                source_desc = f"{len(sources)} source(s) (" + ", ".join(core.describe_source(s) for s in sources[:2]) + (", …" if len(sources) > 2 else "") + ")"
            elif sources:
                source_desc = core.describe_source(sources[0])
            else:
                source_desc = core.describe_source(follow.get('source', {}))
            cells = [follow['name'], source_desc + core.describe_filters(follow), str(self.action.chapter_count(follow)), checked, follow.get('last_status', '')]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, follow['id'])
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        mode_str = _('the official API') if prefs['mode'] == 'api' else _('public feeds')
        auto_str = _('; automatic checks every {hours:.1f} hours.').format(hours=max(float(prefs['check_hours']), core.MIN_CHECK_HOURS)) if prefs['auto_check'] else _('; automatic checks are off.')
        self.mode_label.setText(_('Reading: {mode}{auto}').format(mode=mode_str, auto=auto_str))
        if follows:
            self.overlay.hide_empty()
        else:
            self.overlay.show_empty(
                title='No followed stories',
                message='You are not following any Reddit stories yet. Discover and follow stories to keep your library updated.',
                icon='📚',
                action_text='Find stories…',
                on_action=self.find
            )

    def selected_ids(self):
        rows = {i.row() for i in self.table.selectedItems()}
        return [self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in sorted(rows)]

    def find(self):
        self.action.discover()
        self.refresh()

    def add(self):
        dialog = FollowDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_f = dialog.result_follow()
            if hasattr(self.action, 'registry'):
                self.action.registry().save_series(new_f)
            prefs['follows'] = prefs['follows'] + [new_f]
            self.refresh()

    def edit(self):
        ids = self.selected_ids()
        if len(ids) != 1:
            return
        current = next((f for f in self.follows() if f['id'] == ids[0]), None)
        if not current:
            return
        dialog = FollowDialog(self, current)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            updated = dialog.result_follow()
            if hasattr(self.action, 'registry'):
                self.action.registry().save_series(updated)
            prefs['follows'] = [updated if f['id'] == current['id'] else f for f in self.follows()]
            self.refresh()

    def manifest(self):
        ids = self.selected_ids()
        if len(ids) != 1:
            return
        current = next((f for f in self.follows() if f['id'] == ids[0]), None)
        if not current:
            return
        ManifestDialog(self, current, self.action).exec()

    def remove(self):
        ids = self.selected_ids()
        if not ids:
            return
        if QMessageBox.question(self, 'Remove', f'Stop following {len(ids)} story/stories? The books already in your library are kept.',
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        for follow in [f for f in self.follows() if f['id'] in ids]:
            core.ChapterCache(self.action.cache_dir(), follow['id'], registry=getattr(self.action, 'registry', lambda: None)()).delete()
        prefs['follows'] = [f for f in self.follows() if f['id'] not in ids]
        self.refresh()

    def subscription_exchange(self):
        SubscriptionExchangeDialog(self, self.action).exec()
        self.refresh()

    def test_selected(self):
        ids = self.selected_ids()
        if len(ids) == 1:
            self.action.test_follow(next(f for f in self.follows() if f['id'] == ids[0]))

    def check_selected(self):
        ids = self.selected_ids()
        self.action.check_now([f for f in self.follows() if f['id'] in ids])
        self.refresh()

    def check_all(self):
        self.action.check_now(list(self.follows()))
        self.refresh()

    def settings(self):
        SettingsDialog(self).exec()
        self.refresh()

    def export_opml(self):
        filename, _filt = QFileDialog.getSaveFileName(
            self,
            _('Export OPML'),
            'reddit_subscriptions.opml',
            _('OPML Files (*.opml *.xml);;All Files (*)')
        )
        if not filename:
            return
        try:
            follows = self.follows()
            count = core.export_opml(follows, filename)
            QMessageBox.information(
                self,
                _('Export OPML'),
                _('Successfully exported {count} story series to OPML file.').format(count=count)
            )
        except Exception as exc:
            show_friendly_error_dialog(self, _('Failed to export OPML'), exc)

    def import_opml(self):
        filename, _filt = QFileDialog.getOpenFileName(
            self,
            _('Import OPML'),
            '',
            _('OPML Files (*.opml *.xml);;All Files (*)')
        )
        if not filename:
            return
        try:
            existing = self.follows()
            imported, skipped = core.import_opml(filename, existing=existing)
            if imported:
                if hasattr(self.action, 'registry'):
                    reg = self.action.registry()
                    for item in imported:
                        reg.save_series(item)
                prefs['follows'] = prefs['follows'] + imported
                self.refresh()
            QMessageBox.information(
                self,
                _('Import OPML'),
                _('Imported {count} new story series ({skipped} duplicates skipped).').format(
                    count=len(imported), skipped=skipped
                )
            )
        except Exception as exc:
            show_friendly_error_dialog(self, _('Failed to import OPML'), exc)

