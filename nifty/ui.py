"""Nifty dialogs. Network work never runs on the GUI thread (see guikit.run_task)."""

from calibre_plugins.nifty_downloader import core
from calibre_plugins.nifty_downloader.config import prefs
from calibre_plugins.nifty_downloader.guikit import DownloadOptions, run_task
from qt.core import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    Qt,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)


class LinksDialog(QDialog):
    """Paste Nifty story files, series folders or category folders."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Download Nifty stories")
        self.resize(560, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "One address per line: a story file, a series folder, or a category folder (for example "
                "https://www.nifty.org/nifty/gay/college/). A chapter link downloads the whole series."
            )
        )
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("https://www.nifty.org/nifty/gay/college/the-brotherhood/")
        layout.addWidget(self.text)
        self.options = DownloadOptions(self, prefs)
        layout.addWidget(self.options)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def lines(self):
        return [line for line in self.text.toPlainText().splitlines() if line.strip()]


class BrowseDialog(QDialog):
    """Pick a section and category, filter its stories, tick the ones to download."""

    def __init__(self, parent, existing_ids):
        super().__init__(parent)
        self.setWindowTitle("Browse Nifty by category")
        self.resize(760, 640)
        self.existing = existing_ids
        self.favorites = list(prefs["favorite_categories"])
        self.refs = []
        self.checked = set()
        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        self.section = QComboBox()
        self.section.addItems(core.SECTIONS)
        self.section.currentIndexChanged.connect(self.load_categories)
        row.addWidget(QLabel("Section"))
        row.addWidget(self.section)
        self.category = QComboBox()
        self.category.setMinimumWidth(240)
        row.addWidget(QLabel("Category"))
        row.addWidget(self.category, 1)
        star = QPushButton("★ Toggle favorite")
        star.clicked.connect(self.toggle_favorite)
        row.addWidget(star)
        load = QPushButton("Load stories")
        load.clicked.connect(self.load_stories)
        row.addWidget(load)
        layout.addLayout(row)

        self.favs = QComboBox()
        self.favs.activated.connect(self.jump_to_favorite)
        layout.addWidget(self.favs)

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter stories by title…")
        self.filter.textChanged.connect(self.fill_stories)
        layout.addWidget(self.filter)
        self.summary = QLabel("")
        layout.addWidget(self.summary)
        self.story_list = QListWidget()
        self.story_list.itemChanged.connect(self.story_toggled)
        layout.addWidget(self.story_list, 1)
        row = QHBoxLayout()
        for label, state in (("Check shown", True), ("Uncheck shown", False)):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, s=state: self.set_shown(s))
            row.addWidget(b)
        row.addStretch(1)
        layout.addLayout(row)
        self.options = DownloadOptions(self, prefs)
        layout.addWidget(self.options)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Download checked")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh_favorites()
        self.load_categories()

    # -- categories
    def current_path(self):
        cat = self.category.currentData()
        return cat

    def load_categories(self, *_):
        section = self.section.currentText()
        task = run_task(
            self,
            "Loading categories…",
            lambda t: core.parse_categories(
                core.Fetcher(cancelled=t.cancelled).get(core.url_for(section + "/")), section
            ),
        )
        self.category.clear()
        if task.error:
            self.summary.setText(f"Could not load categories: {task.error}")
            return
        for cat in task.result or []:
            path = f"{section}/{cat}"
            self.category.addItem(("★ " if path in self.favorites else "") + core.humanize(cat), path)

    def refresh_favorites(self):
        self.favs.clear()
        self.favs.addItem("Favorite categories…", None)
        for path in self.favorites:
            self.favs.addItem("★ " + " / ".join(core.humanize(p) for p in path.split("/")), path)

    def toggle_favorite(self):
        path = self.current_path()
        if not path:
            return
        self.favorites = [f for f in self.favorites if f != path] if path in self.favorites else self.favorites + [path]
        prefs["favorite_categories"] = self.favorites
        self.refresh_favorites()
        self.load_categories()
        index = self.category.findData(path)
        if index >= 0:
            self.category.setCurrentIndex(index)

    def jump_to_favorite(self, index):
        path = self.favs.itemData(index)
        if not path:
            return
        section = path.split("/")[0]
        if section != self.section.currentText():
            self.section.setCurrentText(section)  # reloads that section's categories
        i = self.category.findData(path)
        if i >= 0:
            self.category.setCurrentIndex(i)
            self.load_stories()

    # -- stories
    def load_stories(self):
        path = self.current_path()
        if not path:
            return
        task = run_task(self, "Loading stories…", lambda t: core.expand_dir(core.Fetcher(cancelled=t.cancelled), path))
        if task.error:
            self.summary.setText(f"Could not load stories: {task.error}")
            return
        if task.result is None:
            return
        self.refs = task.result
        self.checked = set()
        self.fill_stories()

    def in_library(self, ref):
        return any(e == ref["id"] or e.startswith(ref["id"] + "/") for e in self.existing)

    def fill_stories(self, *_):
        needle = self.filter.text().strip().casefold()
        self.story_list.blockSignals(True)
        self.story_list.clear()
        for ref in self.refs:
            if needle not in ref["title"].casefold():
                continue
            have = self.in_library(ref)
            item = QListWidgetItem(
                f"{ref['title']} — {ref['detail']}, {ref['date']}" + ("   (already in library)" if have else "")
            )
            item.setData(Qt.ItemDataRole.UserRole, ref["id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if ref["id"] in self.checked else Qt.CheckState.Unchecked)
            self.story_list.addItem(item)
        self.story_list.blockSignals(False)
        in_lib = sum(self.in_library(r) for r in self.refs)
        self.summary.setText(
            f"{len(self.refs)} entries ({in_lib} already in your library). {len(self.checked)} checked."
        )

    def story_toggled(self, item):
        rid = item.data(Qt.ItemDataRole.UserRole)
        (self.checked.add if item.checkState() == Qt.CheckState.Checked else self.checked.discard)(rid)
        self.summary.setText(f"{len(self.refs)} entries. {len(self.checked)} checked.")

    def set_shown(self, state):
        for i in range(self.story_list.count()):
            self.story_list.item(i).setCheckState(Qt.CheckState.Checked if state else Qt.CheckState.Unchecked)

    def selected_refs(self):
        return [r for r in self.refs if r["id"] in self.checked]


class AuthorDialog(QDialog):
    """Pick an author, see all their stories across every folder, and combine the ones that belong together."""

    def __init__(self, parent, existing_ids):
        super().__init__(parent)
        self.setWindowTitle("Browse Nifty by author")
        self.resize(900, 700)
        self.existing = {core.norm_path(e) for e in existing_ids}
        self.authors = []
        self.texts = {}  # path -> sampled start and end of the story, filled by 'Scan story contents'
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Nifty files an author's stories under many category folders. This lists them all together. "
                "Sets that look like one series are suggested, but they are only suggestions: you decide what to tick."
            )
        )
        row = QHBoxLayout()
        self.section = QComboBox()
        self.section.addItems(core.SECTIONS)
        self.section.currentIndexChanged.connect(self.fill_authors)
        row.addWidget(QLabel("Section"))
        row.addWidget(self.section)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter authors by name…")
        self.filter.textChanged.connect(self.fill_authors)
        row.addWidget(self.filter, 1)
        reload = QPushButton("Reload authors")
        reload.clicked.connect(self.load_authors)
        row.addWidget(reload)
        layout.addLayout(row)

        body = QHBoxLayout()
        self.author_list = QListWidget()
        self.author_list.setMaximumWidth(300)
        self.author_list.currentItemChanged.connect(self.show_author)
        body.addWidget(self.author_list)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        body.addWidget(self.tree, 1)
        layout.addLayout(body, 1)

        row = QHBoxLayout()
        for label, slot in (
            ("Tick suggested sets", self.check_suggested),
            ("Tick everything", lambda: self.check_all(True)),
            ("Untick all", lambda: self.check_all(False)),
            ("Scan story contents for more sets…", self.scan_contents),
        ):
            button = QPushButton(label)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        self.summary = QLabel("")
        layout.addWidget(self.summary)

        self.skip = QCheckBox("Skip stories already in this library")
        self.skip.setChecked(bool(prefs["skip_existing"]))
        layout.addWidget(self.skip)
        row = QHBoxLayout()
        row.addWidget(QLabel("Each ticked set becomes:"))
        self.how = QComboBox()
        self.how.addItems(["one combined book", "separate books grouped as a Calibre series", "separate books"])
        row.addWidget(self.how, 1)
        layout.addLayout(row)
        self.combine_all = QCheckBox("Combine everything ticked into one book, titled:")
        self.combine_title = QLineEdit()
        self.combine_title.setEnabled(False)
        self.combine_all.toggled.connect(self.combine_title.setEnabled)
        row = QHBoxLayout()
        row.addWidget(self.combine_all)
        row.addWidget(self.combine_title, 1)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Download ticked stories")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.load_authors()

    # -- authors
    def load_authors(self):
        task = run_task(
            self,
            "Reading the authors directory…",
            lambda t: core.load_authors(core.Fetcher(cancelled=t.cancelled), lambda msg: setattr(t, "status", msg)),
        )
        if task.error:
            self.summary.setText(f"Could not load the authors directory: {task.error}")
        elif task.result is not None:
            self.authors = task.result
        self.fill_authors()

    def section_stories(self, author):
        prefix = self.section.currentText() + "/"
        return [s for s in author["stories"] if s["path"].startswith(prefix)]

    def fill_authors(self, *_):
        needle = self.filter.text().strip().casefold()
        self.author_list.blockSignals(True)
        self.author_list.clear()
        for author in self.authors:
            stories = self.section_stories(author)
            if stories and needle in author["name"].casefold():
                item = QListWidgetItem(f"{author['name']} ({len(stories)})")
                item.setData(Qt.ItemDataRole.UserRole, author["id"])
                self.author_list.addItem(item)
        self.author_list.blockSignals(False)
        self.tree.clear()
        self.summary.setText(f"{self.author_list.count()} authors with stories in {self.section.currentText()}.")

    # -- one author's stories
    def current_author(self):
        item = self.author_list.currentItem()
        if item is None:
            return None
        wanted = item.data(Qt.ItemDataRole.UserRole)
        return next((a for a in self.authors if a["id"] == wanted), None)

    def in_library(self, story):
        return any(e == story["path"] or e.startswith(story["path"] + "/") for e in self.existing)

    def story_item(self, story, ticked=False):
        folder = story["path"].split("/")[1:-1]
        label = f"{story['title']}    [{'/'.join(folder)}]" + (
            "    (already in library)" if self.in_library(story) else ""
        )
        item = QTreeWidgetItem([label])
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Checked if ticked else Qt.CheckState.Unchecked)
        item.setData(0, Qt.ItemDataRole.UserRole, story)
        return item

    def ticked_paths(self):
        paths = set()
        for parent in self.top_items():
            for i in range(parent.childCount()):
                if parent.child(i).checkState(0) == Qt.CheckState.Checked:
                    paths.add(parent.child(i).data(0, Qt.ItemDataRole.UserRole)["path"])
        return paths

    def show_author(self, *_, keep=frozenset()):
        author = self.current_author()
        self.tree.blockSignals(True)
        self.tree.clear()
        if author is not None:
            stories = self.section_stories(author)
            if any(s["path"] in self.texts for s in stories):
                series, rest = core.suggest_series_with_contents(stories, self.texts)
            else:
                series, rest = core.suggest_series(stories)
            for entry in series:
                folders = {core.story_folder(s) for s in entry["stories"]}
                parent = QTreeWidgetItem(
                    [f"Suggested set: {entry['name']}  ({len(entry['stories'])} stories in {len(folders)} folder(s))"]
                )
                parent.setFlags(parent.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
                tip = (
                    f"Suggested because of {entry['reason']}. Only a suggestion; untick anything that does not belong."
                )
                if entry.get("evidence"):
                    tip += "\n\nFound in the stories:\n" + "\n".join(entry["evidence"])
                parent.setToolTip(0, tip)
                parent.setData(0, Qt.ItemDataRole.UserRole, ("series", entry["name"]))
                for story in entry["stories"]:
                    parent.addChild(self.story_item(story, story["path"] in keep))
                self.tree.addTopLevelItem(parent)
                parent.setExpanded(True)
            if rest:
                parent = QTreeWidgetItem([f"Other stories ({len(rest)})"])
                parent.setFlags(parent.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
                parent.setData(0, Qt.ItemDataRole.UserRole, ("rest", ""))
                for story in sorted(rest, key=lambda s: s["title"].casefold()):
                    parent.addChild(self.story_item(story, story["path"] in keep))
                self.tree.addTopLevelItem(parent)
                parent.setExpanded(not series)
            scanned = sum(1 for s in stories if s["path"] in self.texts)
            self.summary.setText(
                f"{author['name']}: {len(stories)} stories, {len(series)} suggested set(s)"
                + (f"; contents of {scanned} read." if scanned else ".")
            )
        self.tree.blockSignals(False)

    def scan_contents(self):
        author = self.current_author()
        if author is None:
            self.summary.setText("Pick an author first.")
            return
        stories = self.section_stories(author)
        todo = [s for s in stories if s["path"] not in self.texts]
        if not todo:
            self.summary.setText("The contents of all these stories have already been read.")
            return
        requests = sum(3 if s.get("dir") else 2 for s in todo)
        if (
            QMessageBox.question(
                self,
                "Scan story contents?",
                f'This reads only the first and last few kilobytes of {len(todo)} stories to look for lines such as "continued from ..." '
                f"that name another of this author's stories. It makes about {requests} requests, taking roughly {requests // 2 + 1} "
                "seconds; you can cancel and keep what was read.\n\nGo ahead?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        keep = self.ticked_paths()
        task = run_task(
            self,
            "Reading story openings and endings…",
            lambda t: core.scan_texts(
                core.Fetcher(cancelled=t.cancelled), stories, self.texts, lambda msg: setattr(t, "status", msg)
            ),
        )
        if task.error:
            self.summary.setText(f"Scanning failed: {task.error}")
        self.show_author(keep=keep)

    def top_items(self):
        return [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]

    def check_all(self, state):
        for parent in self.top_items():
            parent.setCheckState(0, Qt.CheckState.Checked if state else Qt.CheckState.Unchecked)

    def check_suggested(self):
        for parent in self.top_items():
            kind = parent.data(0, Qt.ItemDataRole.UserRole)[0]
            parent.setCheckState(0, Qt.CheckState.Checked if kind == "series" else Qt.CheckState.Unchecked)

    # -- result
    def selection(self):
        """download_selection() input for what is ticked; call after the dialog is accepted."""
        series_checked, singles = [], []
        for parent in self.top_items():
            kind, name = parent.data(0, Qt.ItemDataRole.UserRole)
            ticked = [
                parent.child(i).data(0, Qt.ItemDataRole.UserRole)
                for i in range(parent.childCount())
                if parent.child(i).checkState(0) == Qt.CheckState.Checked
            ]
            if kind == "series" and ticked:
                series_checked.append((name, ticked))
            elif kind == "rest":
                singles += ticked
        title = self.combine_title.text() if self.combine_all.isChecked() else None
        prefs["skip_existing"] = self.skip.isChecked()
        how = {0: True, 1: "series", 2: False}[self.how.currentIndex()]
        return core.build_selection(series_checked, singles, how, title)
