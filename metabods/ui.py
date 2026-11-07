"""Metabods dialogs. Network work never runs on the GUI thread (see guikit.run_task)."""

import os

from calibre_plugins.metabods_downloader import core
from calibre_plugins.metabods_downloader.config import prefs
from calibre_plugins.metabods_downloader.guikit import (
    DownloadOptions,
    clean_lines,
    run_task,
)
from qt.core import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    Qt,
    QVBoxLayout,
)

try:
    from calibre_plugins.metabods_downloader import opml_kit
    from calibre_plugins.metabods_downloader.error_handler import (
        show_friendly_error_dialog,
    )
except ImportError:
    try:
        import opml_kit
        from error_handler import show_friendly_error_dialog
    except ImportError:
        from common import opml_kit
        from common.error_handler import show_friendly_error_dialog


class DownloadDialog(QDialog):
    """Paste story links, story ids, or list pages (author, tag, category, archive)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Download Metabods stories")
        self.resize(560, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "One per line: story links (story.php?id=...), story ids, or list pages such as an author, tag or category page."
            )
        )
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText(
            "https://metabods.com/mbxy/site/story.php?id=bennet-3120\nhttps://metabods.com/mbxy/site/archive.php?list=author&id=368"
        )
        layout.addWidget(self.text)
        self.options = DownloadOptions(self, prefs)
        layout.addWidget(self.options)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def lines(self):
        return clean_lines(self.text)


class TagSearchDialog(QDialog):
    """Browse the site's tags, keep favorites, find matching stories, pick which to download."""

    def __init__(self, parent, existing_ids):
        super().__init__(parent)
        self.setWindowTitle("Search Metabods by tag")
        self.resize(760, 640)
        self.existing = existing_ids
        self.tags = [tuple(t) for t in prefs["tag_cache"]]
        self.favorites = {int(t[0]): t[1] for t in prefs["favorite_tags"]}
        self.checked_tags = set()
        self.rows = []
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter tags…")
        self.filter.textChanged.connect(self.fill_tags)
        top.addWidget(self.filter)
        self.fav_only = QCheckBox("Favorites only")
        self.fav_only.toggled.connect(self.fill_tags)
        top.addWidget(self.fav_only)
        refresh = QPushButton("Refresh tag list")
        refresh.clicked.connect(self.load_tags)
        top.addWidget(refresh)
        layout.addLayout(top)

        self.tag_list = QListWidget()
        self.tag_list.itemChanged.connect(self.tag_toggled)
        layout.addWidget(self.tag_list, 3)

        row = QHBoxLayout()
        star = QPushButton("★ Toggle favorite for highlighted tag")
        star.clicked.connect(self.toggle_favorite)
        row.addWidget(star)
        check_favs = QPushButton("Check all favorites")
        check_favs.clicked.connect(self.check_favorites)
        row.addWidget(check_favs)
        clear = QPushButton("Clear checks")
        clear.clicked.connect(self.clear_tags)
        row.addWidget(clear)
        self.mode = QComboBox()
        self.mode.addItems(
            ["Stories with ANY checked tag", "Stories with ALL checked tags"]
        )
        self.mode.setCurrentIndex(1 if prefs["match_all"] else 0)
        row.addWidget(self.mode)
        search = QPushButton("Search")
        search.clicked.connect(self.search)
        row.addWidget(search)
        layout.addLayout(row)

        fav_actions_row = QHBoxLayout()
        self.export_btn = QPushButton("Export List...")
        self.export_btn.clicked.connect(self.export_favorites)
        fav_actions_row.addWidget(self.export_btn)
        self.import_btn = QPushButton("Import List...")
        self.import_btn.clicked.connect(self.import_favorites)
        fav_actions_row.addWidget(self.import_btn)
        fav_actions_row.addStretch(1)
        layout.addLayout(fav_actions_row)

        self.summary = QLabel("")
        layout.addWidget(self.summary)
        self.result_list = QListWidget()
        layout.addWidget(self.result_list, 4)
        row = QHBoxLayout()
        for label, state in (("Check all", True), ("Uncheck all", False)):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, s=state: self.set_all_results(s))
            row.addWidget(b)
        row.addStretch(1)
        layout.addLayout(row)

        self.options = DownloadOptions(self, prefs)
        layout.addWidget(self.options)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Download checked stories"
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if self.tags:
            self.fill_tags()
        else:
            self.load_tags()

    # -- tags
    def load_tags(self):
        task = run_task(
            self,
            "Loading tag list…",
            lambda t: core.parse_tag_index(
                core.Fetcher(cancelled=t.cancelled).get(
                    core.SITE + "archive.php?list=tag"
                )
            ),
        )
        if task.error:
            self.summary.setText(f"Could not load tags: {task.error}")
        elif task.result:
            self.tags = list(task.result)
            prefs["tag_cache"] = [list(t) for t in self.tags]
        self.fill_tags()

    def fill_tags(self, *_):
        needle = self.filter.text().strip().casefold()
        favs_only = self.fav_only.isChecked()
        shown = [
            t
            for t in self.tags
            if needle in t[1].casefold() and (not favs_only or t[0] in self.favorites)
        ]
        shown.sort(key=lambda t: (t[0] not in self.favorites, t[1].casefold()))
        self.tag_list.blockSignals(True)
        self.tag_list.clear()
        for tid, name in shown:
            item = QListWidgetItem(("★ " if tid in self.favorites else "") + name)
            item.setData(Qt.ItemDataRole.UserRole, tid)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if tid in self.checked_tags
                else Qt.CheckState.Unchecked
            )
            self.tag_list.addItem(item)
        self.tag_list.blockSignals(False)

    def tag_toggled(self, item):
        tid = item.data(Qt.ItemDataRole.UserRole)
        (
            self.checked_tags.add
            if item.checkState() == Qt.CheckState.Checked
            else self.checked_tags.discard
        )(tid)

    def toggle_favorite(self):
        item = self.tag_list.currentItem()
        if item is None:
            return
        tid = item.data(Qt.ItemDataRole.UserRole)
        name = dict(self.tags).get(tid, item.text())
        if tid in self.favorites:
            del self.favorites[tid]
        else:
            self.favorites[tid] = name
        prefs["favorite_tags"] = [[k, v] for k, v in self.favorites.items()]
        self.fill_tags()

    def check_favorites(self):
        self.checked_tags |= set(self.favorites)
        self.fill_tags()

    def clear_tags(self):
        self.checked_tags.clear()
        self.fill_tags()

    def export_favorites(self):
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Favorite Tags",
            "metabods_tags.opml",
            "OPML Files (*.opml *.xml);;JSON Files (*.json);;All Files (*)",
        )
        if not file_path:
            return
        try:
            is_json = (
                file_path.lower().endswith(".json") or "json" in selected_filter.lower()
            )
            if not os.path.splitext(file_path)[1]:
                file_path += ".json" if is_json else ".opml"
            items = [[k, v] for k, v in self.favorites.items()]
            if is_json:
                opml_kit.export_list_to_json(
                    items,
                    target=file_path,
                    title="Metabods Favorite Tags",
                    item_type="metabods",
                )
            else:
                opml_kit.export_list_to_opml(
                    items,
                    target=file_path,
                    title="Metabods Favorite Tags",
                    item_type="metabods",
                )
            QMessageBox.information(
                self,
                "Export Complete",
                f"Successfully exported {len(self.favorites)} tag(s) to:\n{file_path}",
            )
        except Exception as exc:
            show_friendly_error_dialog(self, "Failed to export tags", exc)

    def import_favorites(self, file_path=None, strategy=None):
        if not file_path:
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "Import Favorite Tags",
                "",
                "List Files (*.opml *.xml *.json);;OPML Files (*.opml *.xml);;JSON Files (*.json);;All Files (*)",
            )
            if not file_path:
                return

        if strategy is None and self.favorites:
            strategy = opml_kit.ask_import_strategy(self)
            if strategy is None:
                return
        elif strategy is None:
            strategy = opml_kit.STRATEGY_MERGE

        try:
            existing = [[k, v] for k, v in self.favorites.items()]
            if "<opml" in file_path.lower():
                updated_list, stats = opml_kit.import_list_from_opml(
                    file_path,
                    item_type="metabods",
                    existing=existing,
                    strategy=strategy,
                    return_stats=True,
                )
            elif file_path.strip().startswith(("{", "[")):
                updated_list, stats = opml_kit.import_list_from_json(
                    file_path,
                    item_type="metabods",
                    existing=existing,
                    strategy=strategy,
                    return_stats=True,
                )
            else:
                updated_list, stats = opml_kit.import_list_from_file(
                    file_path,
                    item_type="metabods",
                    existing=existing,
                    strategy=strategy,
                    return_stats=True,
                )

            tags_dict = dict(self.tags)
            name_to_id = {v.casefold(): k for k, v in self.tags}
            resolved_tags = []
            for tid, name in updated_list:
                if not tid and name.casefold() in name_to_id:
                    tid = name_to_id[name.casefold()]
                if tid and not name and tid in tags_dict:
                    name = tags_dict[tid]
                resolved_tags.append([int(tid), name])

            self.favorites = {int(t[0]): t[1] for t in resolved_tags}
            prefs["favorite_tags"] = [[k, v] for k, v in self.favorites.items()]
            self.fill_tags()
            QMessageBox.information(
                self,
                "Import Complete",
                f"Successfully processed {stats['total']} tag(s):\n\n"
                f"• Added: {stats['added']}\n"
                f"• Updated: {stats['updated']}\n"
                f"• Skipped: {stats['skipped']}",
            )
        except Exception as exc:
            show_friendly_error_dialog(self, "Failed to import tags", exc)

    # -- search
    def search(self):
        if not self.checked_tags:
            self.summary.setText("Check at least one tag first.")
            return
        match_all = self.mode.currentIndex() == 1
        prefs["match_all"] = match_all
        ids = sorted(self.checked_tags)

        def work(task):
            fetcher = core.Fetcher(cancelled=task.cancelled)

            def progress(completed, total):
                task.status = f"Reading tag {completed} of {total}…"

            results = core.search_tags(fetcher, ids, progress=progress)
            return core.combine_tag_results(results, match_all)

        task = run_task(self, "Searching…", work)
        if task.error:
            self.summary.setText(f"Search failed: {task.error}")
            return
        if task.cancelled() or task.result is None:
            return
        self.rows = task.result
        self.result_list.clear()
        in_library = 0
        for row in self.rows:
            have = row["id"] in self.existing
            in_library += have
            label = (
                f"{row['title']} — {row['author']}" if row["author"] else row["title"]
            )
            item = QListWidgetItem(label + ("   (already in library)" if have else ""))
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            skip = have and self.options.skip.isChecked()
            item.setCheckState(
                Qt.CheckState.Unchecked if skip else Qt.CheckState.Checked
            )
            self.result_list.addItem(item)
        self.summary.setText(
            f"{len(self.rows)} stories found ({in_library} already in your library)."
        )

    def set_all_results(self, checked):
        for i in range(self.result_list.count()):
            self.result_list.item(i).setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )

    def selected_ids(self):
        return [
            self.result_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.result_list.count())
            if self.result_list.item(i).checkState() == Qt.CheckState.Checked
        ]
