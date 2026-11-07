"""Inkbunny & SoFurry dialog. Network work never runs on the GUI thread (see guikit.run_task)."""

from calibre_plugins.inkbunny_sofurry_downloader import core, plog
from calibre_plugins.inkbunny_sofurry_downloader.config import prefs
from calibre_plugins.inkbunny_sofurry_downloader.guikit import (
    DownloadOptions,
    clean_lines,
    run_task,
)
from qt.core import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    Qt,
    QVBoxLayout,
)

SITE_LABEL = {core.INKBUNNY: "Inkbunny", core.SOFURRY: "SoFurry"}


class DownloadDialog(QDialog):
    """Paste story / pool / folder / author addresses, fetch the story list, tick what to download."""

    def __init__(self, parent, existing_ids=()):
        super().__init__(parent)
        self.setWindowTitle("Download from Inkbunny & SoFurry")
        self.resize(680, 640)
        self.existing = set(existing_ids)
        self.refs = []
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "One per line: a story (inkbunny.net/s/…, sofurry.com/s/…), an Inkbunny pool (poolview_process.php?pool_id=…), "
                "a SoFurry folder (…/u/name/gallery?folder=…), or an author page on either site (all their writing). "
                "A pool or folder compiles into one book; loose stories are separate books unless you combine them below."
            )
        )
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText(
            "https://inkbunny.net/s/1234567\nhttps://inkbunny.net/poolview_process.php?pool_id=1234\n"
            "https://sofurry.com/u/someauthor/gallery?folder=abcd1234\nhttps://sofurry.com/s/z1V1z3Zn"
        )
        layout.addWidget(self.text, 2)

        creds = QHBoxLayout()
        creds.addWidget(
            QLabel(
                "Inkbunny login (optional; blank = guest, public general-rated only):"
            )
        )
        self.ib_user = QLineEdit()
        self.ib_user.setPlaceholderText("username")
        self.ib_user.setText(prefs["inkbunny_username"])
        creds.addWidget(self.ib_user)
        self.ib_password = QLineEdit()
        self.ib_password.setPlaceholderText("password (saved)")
        self.ib_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.ib_password.setText(prefs["inkbunny_password"])
        creds.addWidget(self.ib_password)
        layout.addLayout(creds)
        layout.addWidget(
            QLabel(
                'Your account needs "Enable API access" in its Inkbunny account settings. '
                "SoFurry login-only stories cannot be read yet; public ones can."
            )
        )

        row = QHBoxLayout()
        fetch = QPushButton("Fetch story list")
        fetch.clicked.connect(self.fetch_list)
        row.addWidget(fetch)
        for label, state in (("Check all", True), ("Uncheck all", False)):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, s=state: self.set_all(s))
            row.addWidget(b)
        row.addStretch(1)
        layout.addLayout(row)

        self.summary = QLabel("Paste addresses, then Fetch story list.")
        layout.addWidget(self.summary)
        self.list = QListWidget()
        layout.addWidget(self.list, 3)

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

    # -- list fetching
    def fetch_list(self):
        lines = clean_lines(self.text)
        if not lines:
            self.summary.setText("Paste at least one address first.")
            return
        user, password = self.ib_user.text().strip(), self.ib_password.text()
        prefs["inkbunny_username"], prefs["inkbunny_password"] = (
            user,
            password,
        )  # saved so it is not re-entered
        plog.log("fetch start", f"lines={len(lines)} user={user or 'guest'}")

        def work(task):
            fetcher = core.Fetcher(cancelled=task.cancelled)
            return core.expand_targets(
                fetcher,
                lines,
                lambda msg: setattr(task, "status", msg),
                inkbunny_username=user,
                inkbunny_password=password,
            )

        # Disable the whole dialog while the task runs (v1.0.1): run_task spins the Qt event loop,
        # so without this the user can click Download/Cancel on this dialog mid-fetch and close it
        # while fetch_list is still on the stack — using a deleted dialog, which can crash calibre.
        self.setEnabled(False)
        try:
            task = run_task(self, "Reading addresses…", work)
        finally:
            self.setEnabled(True)
        plog.log(
            "fetch end",
            f"error={task.error!r} cancelled={task.cancelled()} "
            f"refs={len(task.result[0]) if task.result else 0}",
        )
        if task.error:
            self.summary.setText(f"Could not read the addresses: {task.error}")
            return
        if task.cancelled() or task.result is None:
            return
        self.refs, problems = task.result
        self.list.clear()
        in_library = 0
        for ref in self.refs:
            have = ref["id"] in self.existing or bool(
                ref.get("group_id")
                and f"{ref['site']}:{ref['group_id']}" in self.existing
            )
            in_library += have
            label = f"[{SITE_LABEL[ref['site']]}] {ref['title']}"
            if ref["author"]:
                label += f" — {ref['author']}"
            if ref["group"]:
                label += f"  ⟨{ref['group']}⟩"
            if have:
                label += "   (already in library)"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, ref)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            skip = have and self.options.skip.isChecked()
            item.setCheckState(
                Qt.CheckState.Unchecked if skip else Qt.CheckState.Checked
            )
            self.list.addItem(item)
        text = f"{len(self.refs)} stories found ({in_library} already in your library)."
        if problems:
            text += "\n" + "\n".join(problems)
        self.summary.setText(text)

    def set_all(self, checked):
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )

    # -- results
    def selected_refs(self):
        return [
            self.list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.list.count())
            if self.list.item(i).checkState() == Qt.CheckState.Checked
        ]

    def credentials(self):
        return self.ib_user.text().strip(), self.ib_password.text()
