"""Restore a project: from a daily safety copy or from a backup file.

Both end the same way (the copy is unpacked into a new folder), so this dialog
only asks where the copy comes from. MainWindow.restore_project_backup() does
the rest.
"""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QLabel, QListWidget,
                               QListWidgetItem, QRadioButton, QVBoxLayout)

from qualitative_themes.runtime import app_settings
from qualitative_themes.storage.recovery import list_snapshots


class RestoreDialog(QDialog):
    """Pick a safety copy (project, then date) or say a backup file will be chosen.

    Post (after exec() == Accepted): chosen_snapshot() is the snapshot path, or
    None when the researcher picked "From a backup file…".
    """

    def __init__(self, current_project: Path | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Restore a project")
        self.resize(560, 460)
        layout = QVBoxLayout(self)
        note = QLabel("The restored copy opens in a new folder. Your current project is never overwritten.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.from_copy = QRadioButton("From a daily safety copy")
        self.from_backup = QRadioButton("From a backup file…")
        group = QButtonGroup(self)
        group.addButton(self.from_copy)
        group.addButton(self.from_backup)
        layout.addWidget(self.from_copy)
        copy_help = QLabel("Copies the app makes for you. Works even if the project folder is damaged or missing.")
        copy_help.setObjectName("quiet")
        copy_help.setWordWrap(True)
        layout.addWidget(copy_help)
        self.project = QComboBox()
        self.project.setAccessibleName("Project")
        for path, name in self._projects(current_project):
            self.project.addItem(f"{name} · {path}", str(path))
        layout.addWidget(self.project)
        self.copies = QListWidget()
        self.copies.setAccessibleName("Safety copies, newest first")
        layout.addWidget(self.copies, 1)
        layout.addWidget(self.from_backup)
        backup_help = QLabel("A .tmtbackup file made with File → Back up project, for example on a USB drive.")
        backup_help.setObjectName("quiet")
        backup_help.setWordWrap(True)
        layout.addWidget(backup_help)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Restore…")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.project.currentIndexChanged.connect(self.load_copies)
        self.copies.currentItemChanged.connect(self.update_buttons)
        group.buttonToggled.connect(self.update_buttons)
        self.load_copies()
        (self.from_copy if self.copies.count() and self.copies.item(0).data(Qt.ItemDataRole.UserRole)
         else self.from_backup).setChecked(True)

    @staticmethod
    def _projects(current):
        """(path, name) for the open project first, then recent projects, without repeats."""
        rows = []
        if current is not None:
            rows.append((Path(current), Path(current).name))
        for row in app_settings.recent_projects():
            path = Path(row["path"])
            if all(path != existing for existing, _ in rows):
                rows.append((path, row.get("name") or path.name))
        return rows

    def load_copies(self, _index=-1):
        self.copies.clear()
        path = self.project.currentData()
        snapshots = []
        if path:
            try:
                snapshots = list_snapshots(Path(path))
            except Exception as exc:
                self.copies.addItem(f"Couldn't read the safety copies: {exc}")
        for day, archive in snapshots:
            size = archive.stat().st_size / (1024 * 1024)
            item = QListWidgetItem(f"{day} · {size:.1f} MB")
            item.setData(Qt.ItemDataRole.UserRole, str(archive))
            self.copies.addItem(item)
        if not snapshots and self.copies.count() == 0:
            item = QListWidgetItem("No safety copies for this project yet.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.copies.addItem(item)
        if snapshots:
            self.copies.setCurrentRow(0)
        self.update_buttons()

    def chosen_snapshot(self) -> Path | None:
        if not self.from_copy.isChecked() or self.copies.currentItem() is None:
            return None
        value = self.copies.currentItem().data(Qt.ItemDataRole.UserRole)
        return Path(value) if value else None

    def update_buttons(self, *_args):
        copy = self.from_copy.isChecked()
        self.project.setEnabled(copy)
        self.copies.setEnabled(copy)
        ready = self.from_backup.isChecked() or self.chosen_snapshot() is not None
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ready)
