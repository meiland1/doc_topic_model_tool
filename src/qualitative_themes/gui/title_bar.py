"""The strip above every page: app name, the project switcher and the offline badge.

The switcher names the open project and is the way to the start screen, other
projects and new ones. Looking at the start screen doesn't close the project,
so coming back is instant (no reload).
"""
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QToolButton, QWidget

from qualitative_themes.runtime import app_settings
from qualitative_themes.gui import strings


class TitleBar(QWidget):
    """Emits what the researcher picked from the switcher; MainWindow does the work."""
    recent_requested = Signal(str)
    open_requested = Signal()
    new_requested = Signal()
    start_screen_requested = Signal()
    current_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("titleBar")
        self._project = None
        self._where = ""
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 6, 12, 6)
        title = QLabel("Topic Model Tool")
        title.setObjectName("applicationTitle")
        row.addWidget(title)
        self.switcher = QToolButton()
        self.switcher.setObjectName("projectSwitcher")
        self.switcher.setAccessibleName("Switch project")
        self.switcher.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu = QMenu(self.switcher)
        self.menu.aboutToShow.connect(self.fill_menu)
        self.switcher.setMenu(self.menu)
        self.switcher.hide()
        row.addWidget(self.switcher)
        row.addStretch(1)
        self.offline_badge = QLabel("Offline · nothing leaves this computer")
        self.offline_badge.setObjectName("offlineBadge")
        self.offline_badge.setToolTip("The app makes no network requests. The local AI engine only accepts "
                                      "connections from this computer.")
        row.addWidget(self.offline_badge)

    def set_project(self, project):
        """Show `project` in the switcher, or hide the switcher when it's None."""
        self._project = project
        self.switcher.setVisible(project is not None)
        if project is not None:
            self.switcher.setText(f"Project  {project.name}  ▾")
            self.switcher.setToolTip(f"{project.name}\n{project.directory}")

    def set_location(self, where: str):
        """Where the researcher is in the open project, e.g. "Read & code · interview_04.docx"."""
        self._where = where

    def fill_menu(self):
        """Rebuilt each time it opens, so the recent list is always current."""
        self.menu.clear()
        if self._project is not None:
            self.menu.addSection("Open now")
            label = self._project.name + (f"  ·  {self._where}" if self._where else "")
            self.menu.addAction(label, self.current_requested.emit)
        others = [row for row in app_settings.recent_projects()
                  if self._project is None or Path(row["path"]) != Path(self._project.directory)]
        if others:
            self.menu.addSection("Recent")
            for row in others[:8]:
                path = row["path"]
                self.menu.addAction(row.get("name") or Path(path).name,
                                    lambda path=path: self.recent_requested.emit(path))
        self.menu.addSeparator()
        self.menu.addAction("Open another project…", self.open_requested.emit)
        self.menu.addAction(strings.NEW_PROJECT, self.new_requested.emit)
        self.menu.addAction("Start screen", self.start_screen_requested.emit)
