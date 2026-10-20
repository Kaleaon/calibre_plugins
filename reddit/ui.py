"""Dialogs for following Reddit stories. Network work happens in the action, never here."""
import time
import webbrowser

from qt.core import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
                     QLineEdit, QMessageBox, QPushButton, QRadioButton, QSpinBox, QTableWidget, QTableWidgetItem, Qt, QVBoxLayout)
try:
    from qt.core import _
except ImportError:
    _ = lambda s: s

from calibre_plugins.reddit_follower import core, login
from calibre_plugins.reddit_follower.config import prefs
from calibre_plugins.reddit_follower.guikit import run_task
from calibre_plugins.reddit_follower.storykit import Fetcher

ACCESS_NOTE = _(
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
        form.addRow('Title contains', self.title_filter)

        self.author_filter = QLineEdit(self.source.get('author_filter', ''))
        self.author_filter.setPlaceholderText('Only posts by author (optional)')
        form.addRow('Posted by', self.author_filter)

        self.flair_filter = QLineEdit(self.source.get('flair_filter', ''))
        self.flair_filter.setPlaceholderText('Only posts with flair (optional)')
        form.addRow('Flair contains', self.flair_filter)

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
        self.resize(560, 360)
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
        self.source = QLineEdit(core.source_text(follow['source']) if follow else '')
        self.source.setPlaceholderText(_('r/HFY   or   u/author   or a Reddit search address'))
        self.source.textChanged.connect(self.check_source)
        form.addRow(_('Where to look'), self.source)
        self.title_filter = QLineEdit(follow['title_filter'] if follow else '')
        self.title_filter.setPlaceholderText(_('Only posts whose title contains this (or re:regular expression)'))
        form.addRow(_('Title contains'), self.title_filter)
        self.author_filter = QLineEdit(follow['author_filter'] if follow else '')
        self.author_filter.setPlaceholderText(_('Only posts by this user (optional)'))
        form.addRow(_('Posted by'), self.author_filter)
        self.flair_filter = QLineEdit(follow.get('flair_filter', '') if follow else '')
        self.flair_filter.setPlaceholderText(_('Only posts with this flair, for example FICTION (official API only; optional)'))
        form.addRow(_('Flair contains'), self.flair_filter)
        self.index_url = QLineEdit(follow.get('index_url', '') if follow else '')
        self.index_url.setPlaceholderText(_('Optional: a public Google Sheet listing the chapters (Date, Author, Chapter, Note)'))
        form.addRow(_('Chapter index'), self.index_url)
        self.layout_box = QComboBox()
        self.layout_box.addItem(_('One book that grows, one chapter per post (a series)'), 'series')
        self.layout_box.addItem(_('A separate book for each post (many authors, one story per post)'), 'each')
        if follow and follow.get('layout') == 'each':
            self.layout_box.setCurrentIndex(1)
        form.addRow(_('Make'), self.layout_box)
        layout.addLayout(form)
        self.title_from_body = QCheckBox(_('The first line of each post is its chapter title (use it to name the chapter)'))
        self.title_from_body.setToolTip(_('For authors who open every post with a short title line. The line becomes the chapter name and is taken out of the text; '
                                        'a note in brackets next to it becomes a boxed author\'s note; posts that do not open that way keep their own title.'))
        self.title_from_body.setChecked(bool(follow and follow.get('title_from_body')))
        layout.addWidget(self.title_from_body)
        self.author_note = QCheckBox(_("Also keep the author's own comment under each chapter (official API only)"))
        self.author_note.setToolTip(_('Authors often use their comment for notes, prefaces or links. Costs one extra request per chapter, '
                                    'so a long series takes a while on the first pass; reader comments are not included.'))
        self.author_note.setChecked(bool(follow and follow.get('author_note')))
        layout.addWidget(self.author_note)

        self.message = QLabel('')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        layout.addWidget(QLabel(_(
            'Tip: for one series inside a busy subreddit, use a search address, for example the one Reddit shows when you search the '
            'series name inside r/HFY, or follow the author with u/name, and set "Title contains" so only that series is kept. '
            'Each matching post becomes a chapter, oldest first, in one book that grows as new posts appear.')))
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
        rows = {i.row() for i in self.sources_table.selectedItems()}
        if not rows:
            return
        for r in sorted(rows, reverse=True):
            if r < len(self.sources):
                self.sources.pop(r)
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

    def check_source(self, *args):
        text = self.source.text().strip()
        if not text:
            self.message.setText('')
            return None
        try:
            src = core.parse_source(text)
        except core.SourceError as exc:
            self.message.setText(str(exc))
            return None
        self.message.setText(_('Will follow {source}.').format(source=core.describe_source(src)))
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
                self.message.setText(_('Enter where to look first.'))
            return
        self.sync_primary_source()
        self.accept()

    def result_follow(self):
        """A new follow, or the edited existing one (keeping its id and history)."""
        if self.follow is None:
            return core.new_follow(self.name.text(), self.source.text(), self.title_filter.text(), self.author_filter.text(), self.author_note.isChecked(),
                                  self.layout_box.currentData(), self.flair_filter.text(), self.index_url.text(),
                                  self.title_from_body.isChecked())
        updated = core.new_follow(self.name.text() or self.follow['name'], self.source.text(), self.title_filter.text(), self.author_filter.text(), self.author_note.isChecked(),
                                  self.layout_box.currentData(), self.flair_filter.text(), self.index_url.text(),
                                  self.title_from_body.isChecked())
        updated.update(id=self.follow['id'], last_checked=0.0, last_status=_('changed; will be checked again'))
        return updated


class SettingsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(_('Reddit Story Follower settings'))
        self.resize(640, 560)
        layout = QVBoxLayout(self)
        note = QLabel(ACCESS_NOTE)
        note.setWordWrap(True)
        layout.addWidget(note)
        self.rss = QRadioButton(_('Read public feeds (no account)'))
        self.api = QRadioButton(_('Use the official API with my own credentials'))
        (self.api if prefs['mode'] == 'api' else self.rss).setChecked(True)
        layout.addWidget(self.rss)
        layout.addWidget(self.api)
        form = QFormLayout()
        self.client_id = QLineEdit(prefs['client_id'])
        self.client_secret = QLineEdit(prefs['client_secret'])
        self.client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.client_secret.setPlaceholderText(_('Leave empty for an "installed app"'))
        self.username = QLineEdit(prefs['username'])
        self.username.setPlaceholderText(_('Your Reddit username, shown to Reddit in the user agent'))
        form.addRow(_('Client id'), self.client_id)
        form.addRow(_('Client secret'), self.client_secret)
        form.addRow(_('Reddit username'), self.username)
        layout.addLayout(form)
        self.refresh_token, self.account_name = prefs['refresh_token'], prefs['account_name']
        row = QHBoxLayout()
        self.login_button = QPushButton(_('Log in with Reddit…'))
        self.login_button.setToolTip(_('Opens Reddit in your browser to approve read access. Your password never reaches this plugin.'))
        self.login_button.clicked.connect(self.log_in)
        self.logout_button = QPushButton(_('Log out'))
        self.logout_button.clicked.connect(self.log_out)
        self.login_status = QLabel('')
        row.addWidget(self.login_button)
        row.addWidget(self.logout_button)
        row.addWidget(self.login_status, 1)
        layout.addLayout(row)
        hint = QLabel(_('Register the app at reddit.com/prefs/apps with redirect uri exactly:  {uri}').format(uri=login.REDIRECT_URI))
        hint.setWordWrap(True)
        hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(hint)
        self.api.toggled.connect(self.sync)
        self.sync()
        self.auto = QCheckBox(_('Check for new chapters automatically while Calibre is open'))
        self.auto.setChecked(bool(prefs['auto_check']))
        layout.addWidget(self.auto)
        row = QHBoxLayout()
        row.addWidget(QLabel(_('Check each story at most every')))
        self.hours = QDoubleSpinBox()
        self.hours.setRange(core.MIN_CHECK_HOURS, 168)
        self.hours.setDecimals(1)
        self.hours.setSuffix(_(' hours'))
        self.hours.setValue(max(float(prefs['check_hours']), core.MIN_CHECK_HOURS))
        row.addWidget(self.hours)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(QLabel(_('Checks only run while Calibre is open (there is no background service), and the interval is never shorter than one hour.')))
        row = QHBoxLayout()
        test = QPushButton(_('Test connection'))
        test.setToolTip(_('Makes one request to r/HFY with the settings above (nothing is saved or changed)'))
        test.clicked.connect(self.test_connection)
        row.addWidget(test)
        self.test_result = QLabel('')
        self.test_result.setWordWrap(True)
        row.addWidget(self.test_result, 1)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def sync(self, *args):
        for widget in (self.client_id, self.client_secret, self.username):
            widget.setEnabled(self.api.isChecked())
        self.login_button.setEnabled(self.api.isChecked())
        self.logout_button.setEnabled(bool(self.refresh_token))
        self.login_status.setText(_('Logged in as u/{user}').format(user=self.account_name) if self.refresh_token and self.account_name
                                  else _('Logged in') if self.refresh_token else _('Not logged in'))

    open_browser = staticmethod(webbrowser.open)
    login_endpoints = {}  # tests point these at a stand-in server

    def log_in(self):
        client_id, secret = self.client_id.text().strip(), self.client_secret.text().strip()
        if not client_id:
            QMessageBox.warning(self, _('Reddit Story Follower'), _('Enter your app\'s client id first.'))
            return
        try:
            with login.CallbackServer() as server:
                task = run_task(self, _('Waiting for you to approve the login in your browser…'), lambda t: login.log_in(
                    Fetcher(cancelled=t.cancelled), client_id, secret, server, self.open_browser, t.cancelled,
                    **{k: v for k, v in self.login_endpoints.items() if k != 'revoke_url'}))
        except login.LoginError as exc:
            QMessageBox.warning(self, _('Reddit Story Follower'), str(exc))
            return
        if task.error:
            if 'cancelled' not in str(task.error).lower():
                QMessageBox.warning(self, _('Reddit Story Follower'), _('Login failed: {error}').format(error=task.error))
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
                QMessageBox.information(self, _('Reddit Story Follower'), _('Reddit could not be reached to cancel the login, so it was only '
                                        'removed here. You can also remove it at reddit.com/prefs/apps.'))
        self.refresh_token = self.account_name = ''
        prefs['refresh_token'] = prefs['account_name'] = ''
        self.sync()

    def test_connection(self):
        mode = 'api' if self.api.isChecked() else 'rss'
        client_id, secret, username = self.client_id.text().strip(), self.client_secret.text().strip(), self.username.text().strip()
        src = core.parse_source('r/HFY')
        task = run_task(self, _('Testing Reddit…'), lambda t: core.probe(core.make_source(mode, client_id, secret, self.account_name or username, t.cancelled, self.refresh_token), src, limit=5))
        if task.error:
            self.test_result.setText(_('The test failed: {error}').format(error=task.error))
        elif task.result is not None:
            self.test_result.setText((_('OK: ') if task.result[0] else _('Problem: ')) + task.result[1])

    def save(self):
        if self.api.isChecked() and not self.client_id.text().strip():
            QMessageBox.warning(self, _('Reddit Story Follower'), _('Enter your app\'s client id to use the official API, or choose public feeds.'))
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
        self.setWindowTitle(_('Find stories on Reddit'))
        self.resize(820, 520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.subreddit = QLineEdit('HFY')
        form.addRow(_('Subreddit'), self.subreddit)
        self.words = QLineEdit()
        self.words.setPlaceholderText(_('Search words, for example deathworld, or leave empty to browse'))
        self.words.returnPressed.connect(self.search)
        form.addRow(_('Search words'), self.words)
        self.browse = QComboBox()
        for key, label in core.BROWSE:
            self.browse.addItem(_(label), key)
        self.browse.setCurrentIndex(1)
        form.addRow(_('Order'), self.browse)
        self.pages = QSpinBox()
        self.pages.setRange(1, 10)
        self.pages.setValue(3)
        self.pages.setToolTip(_('Each page is up to 100 posts and one request to Reddit. Looking through more pages finds more series.'))
        form.addRow(_('Pages to look through'), self.pages)
        self.flair = QLineEdit()
        self.flair.setPlaceholderText(_('Only this flair, for example OC-FirstOfSeries (official API only; optional)'))
        form.addRow(_('Flair contains'), self.flair)
        layout.addLayout(form)
        row = QHBoxLayout()
        go = QPushButton(_('Search'))
        go.clicked.connect(self.search)
        row.addWidget(go)
        self.message = QLabel(_('Series are guessed from the author and the part numbers in the titles (Part 3, 14, VIII).'))
        self.message.setWordWrap(True)
        row.addWidget(self.message, 1)
        layout.addLayout(row)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([_('Series or story'), _('Author'), _('Posts found'), _('Parts seen'), _('Rating'), _('Flair')])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 330)
        self.table.doubleClicked.connect(lambda *args: self.follow_selected())
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.follow_button = QPushButton(_('Follow the whole series…'))
        self.follow_button.setToolTip(_('Looks through the author\'s posts for every part, shows what it found, then lets you adjust and follow it'))
        self.follow_button.clicked.connect(self.follow_selected)
        row.addWidget(self.follow_button)
        row.addSpacing(24)
        row.addWidget(QLabel(_('Or add the top')))
        self.top_count = QSpinBox()
        self.top_count.setRange(1, 25)
        self.top_count.setValue(5)
        row.addWidget(self.top_count)
        row.addWidget(QLabel(_('series with at least')))
        self.min_parts = QSpinBox()
        self.min_parts.setRange(2, 100)
        self.min_parts.setValue(3)
        row.addWidget(self.min_parts)
        row.addWidget(QLabel(_('parts found')))
        self.top_button = QPushButton(_('Add top rated…'))
        self.top_button.setToolTip(_('Picks the highest rated series in the list above (the rating is the total score of the posts found; '
                                   'it needs the official API) and follows them all, skipping any you already follow'))
        self.top_button.clicked.connect(self.add_top)
        row.addWidget(self.top_button)
        row.addStretch(1)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def add_top(self):
        if not self.groups:
            self.message.setText(_('Search or browse first (for example Order: Top of all time), then add the top rated.'))
            return
        if not any(g.get('score') for g in self.groups):
            self.message.setText(_('Ratings need the official API (Settings); the public feeds carry no scores.'))
            return
        picked = core.top_series(self.groups, self.top_count.value(), self.min_parts.value(), prefs['follows'])
        if not picked:
            self.message.setText(_('No series with that many parts found that you do not already follow. Look through more pages or lower the number of parts.'))
            return
        rating_str = _('rating')
        parts_str = _('parts seen')
        text = '\n'.join(f"{g['name']} \u2013 {g['author']}  ({rating_str} {g['score']:,}, {len(g['posts'])} {parts_str})" for g in picked)
        if QMessageBox.question(self, _('Follow these series?'), _('{count} highest rated series:\n\n{text}\n\nFollow them all as growing books? '
                                'Each is collected in full from its author\'s posts, which can take a few minutes.').format(count=len(picked), text=text)) != QMessageBox.StandardButton.Yes:
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
            task = run_task(self, _('Searching Reddit…'), lambda t: core.discover(
                self.action.make_source(t.cancelled), src, flair, pages=self.pages.value(), cancelled=t.cancelled, progress=lambda msg: setattr(t, 'status', msg)))
        except core.SourceError as exc:
            self.message.setText(str(exc))
            return
        if task.error:
            self.message.setText(_('The search failed: {error}').format(error=task.error))
            return
        if task.result is None:
            return
        self.groups = core.rank_groups(task.result, 'score' if any(g.get('score') for g in task.result) else 'posts')
        self.table.setRowCount(len(self.groups))
        for row, g in enumerate(self.groups):
            nums = sorted(g['numbers'])
            parts = f'{nums[0]}\u2013{nums[-1]}' if len(nums) > 1 else (str(nums[0]) if nums else '')
            for col, text in enumerate([g['name'] + ('' if g['is_series'] else _('  (single story)')), g['author'], str(len(g['posts'])), parts,
                                        f"{g['score']:,}" if g.get('score') else '', ', '.join(sorted(g['flairs']))[:40]]):
                self.table.setItem(row, col, QTableWidgetItem(text))
        series = sum(1 for g in self.groups if g['is_series'])
        self.message.setText(_('{series} series and {other} single stories among the posts looked through. '
                             'Counts are only what the search showed; following a series reads all of its parts.').format(series=series, other=len(self.groups) - series) if self.groups
                             else _('Nothing found. Try other words, another order, or no flair.'))

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
        text = _('{name} by {author}: {count} post(s) from {first} to {last}.\n\n{titles}\n\nFollow it as one book that grows?').format(
            name=follow['name'], author=group['author'], count=found['count'], first=found['first'], last=found['last'], titles='\n'.join(found['titles']))
        if QMessageBox.question(self, _('Follow this series?'), text) != QMessageBox.StandardButton.Yes:
            return
        dialog = FollowDialog(self, follow)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.chosen = dialog.result_follow()
            self.accept()


class ManageDialog(QDialog):
    """The list of followed stories, with add, edit, remove and check buttons."""
    COLUMNS = [_('Book'), _('Following'), _('Chapters'), _('Last checked'), _('Status')]

    def __init__(self, parent, action):
        super().__init__(parent)
        self.action = action
        self.setWindowTitle(_('Followed Reddit stories'))
        self.resize(860, 460)
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        self.mode_label = QLabel('')
        layout.addWidget(self.mode_label)
        row = QHBoxLayout()
        for label, slot in ((_('Find stories…'), self.find), (_('Add…'), self.add), (_('Edit…'), self.edit), (_('Remove'), self.remove), (_('Queue manager…'), self.open_queue_manager), (_('Test selected'), self.test_selected), (_('Check selected now'), self.check_selected),
                            (_('Check all now'), self.check_all), (_('Settings…'), self.settings)):
            button = QPushButton(label)
            button.clicked.connect(slot)
            row.addWidget(button)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def open_queue_manager(self):
        self.action.open_queue_manager()
        self.refresh()

    def follows(self):
        if hasattr(self.action, 'get_follows'):
            return self.action.get_follows()
        return prefs['follows']

    def refresh(self):
        follows = self.follows()
        self.table.setRowCount(len(follows))
        for r, follow in enumerate(follows):
            checked = time.strftime('%Y-%m-%d %H:%M', time.localtime(follow['last_checked'])) if follow.get('last_checked') else _('never')
            cells = [follow['name'], core.describe_source(follow['source']) + core.describe_filters(follow), str(self.action.chapter_count(follow)), checked, follow.get('last_status', '')]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, follow['id'])
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        mode_str = _('the official API') if prefs['mode'] == 'api' else _('public feeds')
        auto_str = _('; automatic checks every {hours:.1f} hours.').format(hours=max(float(prefs['check_hours']), core.MIN_CHECK_HOURS)) if prefs['auto_check'] else _('; automatic checks are off.')
        self.mode_label.setText(_('Reading: {mode}{auto}').format(mode=mode_str, auto=auto_str))

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
        if QMessageBox.question(self, _('Remove'), _('Stop following {count} story/stories? The books already in your library are kept.').format(count=len(ids)),
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        for follow in [f for f in self.follows() if f['id'] in ids]:
            core.ChapterCache(self.action.cache_dir(), follow['id'], registry=getattr(self.action, 'registry', lambda: None)()).delete()
        prefs['follows'] = [f for f in self.follows() if f['id'] not in ids]
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

