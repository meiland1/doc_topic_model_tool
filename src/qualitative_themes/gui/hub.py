"""Start screen: new/open/recent projects and the Setup status panel."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QBoxLayout, QScrollArea, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from qualitative_themes.runtime import app_settings, health
from qualitative_themes.runtime.paths import missing_files_fix
from qualitative_themes.gui import strings


def _relative_day(stamp: str) -> str:
    try:
        when = datetime.fromisoformat(stamp).astimezone()
    except ValueError:
        return ""
    days = (datetime.now().astimezone().date() - when.date()).days
    if days == 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    return f"{when:%b} {when.day}"


class SetupPanel(QFrame):
    """Local setup status on the start screen (runtime.health checks) with Repair / Add model buttons."""
    repair_requested = Signal()
    add_model_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("setupPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel("Setup")
        title.setObjectName("panelTitle")
        header.addWidget(title)
        header.addStretch()
        shared = QLabel("Shared by all projects")
        shared.setObjectName("quiet")
        header.addWidget(shared)
        layout.addLayout(header)
        self.rows = QGridLayout()
        self.rows.setHorizontalSpacing(12)
        self.rows.setVerticalSpacing(4)
        layout.addLayout(self.rows)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setObjectName("setupNotice")
        layout.addWidget(self.notice)
        buttons = QHBoxLayout()
        self.repair_button = QPushButton("Check && repair")
        self.repair_button.clicked.connect(self.repair_requested)
        self.add_model_button = QPushButton("Add theme-naming model…")
        self.add_model_button.setToolTip("Add the theme-naming AI from a file (works offline).")
        self.add_model_button.clicked.connect(self.add_model_requested)
        buttons.addWidget(self.repair_button)
        buttons.addWidget(self.add_model_button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.checks = []

    def show_checks(self, checks, busy_message: str | None = None):
        self.checks = checks
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for row, check in enumerate(checks):
            name = QLabel(check.title)
            name.setObjectName("setupName")
            state = QLabel("Ready" if check.ok else ("Needs attention" if check.required else "Not set up"))
            state.setObjectName("stateGood" if check.ok else ("stateBad" if check.required else "stateWarn"))
            state.setAlignment(Qt.AlignmentFlag.AlignRight)
            detail = QLabel(check.detail)
            detail.setObjectName("quiet")
            detail.setWordWrap(True)
            self.rows.addWidget(name, row * 2, 0)
            self.rows.addWidget(state, row * 2, 1)
            self.rows.addWidget(detail, row * 2 + 1, 0, 1, 2)
        overall = health.summary(checks)
        for widget in (self, self.notice):
            widget.setProperty("state", overall)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        if busy_message:
            self.notice.setText(busy_message)
        elif overall == "ready":
            self.notice.setText("Everything is installed. Nothing leaves this computer.")
        elif overall == "limited":
            self.notice.setText("Theme finding works. Theme names will use keywords until a naming model is added.")
        else:
            self.notice.setText(f"Something is missing. Choose Check & repair; if that doesn't help, {missing_files_fix()}. "
                                "Your projects are not affected.")


class Hub(QWidget):
    """Start screen. It only emits signals; MainWindow does the actual work."""
    new_requested = Signal()
    practice_requested = Signal()
    guide_requested = Signal()
    back_requested = Signal()
    open_requested = Signal()
    restore_requested = Signal()
    recent_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("hub")
        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        content = QWidget()
        self.scroll.setWidget(content)
        shell.addWidget(self.scroll)
        outer = QBoxLayout(QBoxLayout.Direction.LeftToRight, content)
        self.columns = outer
        outer.setContentsMargins(56, 40, 56, 40)
        outer.setSpacing(32)
        left = QVBoxLayout()
        left.setSpacing(14)
        heading = QLabel("Welcome")
        heading.setObjectName("hubTitle")
        left.addWidget(heading)
        intro = QLabel("Find preliminary themes across your documents, then go straight to the passages worth "
                       "reading closely. Projects are folders you can back up or move.")
        intro.setWordWrap(True)
        intro.setObjectName("quiet")
        left.addWidget(intro)
        # Shown while a project is open behind the start screen.
        self.back_card = QFrame()
        self.back_card.setObjectName("backCard")
        card = QHBoxLayout(self.back_card)
        card.setContentsMargins(16, 12, 16, 12)
        card_text = QVBoxLayout()
        self.back_title = QLabel()
        self.back_title.setObjectName("setupName")
        self.back_where = QLabel()
        self.back_where.setObjectName("quiet")
        card_text.addWidget(self.back_title)
        card_text.addWidget(self.back_where)
        card.addLayout(card_text, 1)
        self.back_button = QPushButton("Back to project →")
        self.back_button.setProperty("primary", True)
        self.back_button.setMinimumHeight(40)
        self.back_button.clicked.connect(self.back_requested)
        card.addWidget(self.back_button)
        self.back_card.hide()
        left.addWidget(self.back_card)
        actions = QHBoxLayout()
        self.new_button = QPushButton("+  New project")
        self.new_button.setProperty("primary", True)
        self.new_button.setObjectName("hubAction")
        self.new_button.clicked.connect(self.new_requested)
        self.open_button = QPushButton(strings.OPEN_PROJECT)
        self.open_button.setObjectName("hubAction")
        self.open_button.clicked.connect(self.open_requested)
        for button in (self.new_button, self.open_button):
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setMinimumHeight(72)
            actions.addWidget(button)
        left.addLayout(actions)
        self.guide_button = QPushButton('Quick guide  ·  5 steps, ends with sample interviews')
        self.guide_button.setToolTip('How the tool works, step by step. No project needed.')
        self.guide_button.clicked.connect(self.guide_requested)
        left.addWidget(self.guide_button)
        self.restore_button = QPushButton(strings.RESTORE_PROJECT + "  ·  from a safety copy or a backup")
        self.restore_button.clicked.connect(self.restore_requested)
        left.addWidget(self.restore_button)
        recent_label = QLabel("Recent projects")
        recent_label.setObjectName("sectionLabel")
        left.addWidget(recent_label)
        self.recent = QListWidget()
        self.recent.setObjectName("recentList")
        self.recent.setAccessibleName("Recent projects")
        self.recent.itemActivated.connect(self._open_recent)
        self.recent.itemClicked.connect(self._open_recent)
        left.addWidget(self.recent, 1)
        outer.addLayout(left, 3)
        right = QVBoxLayout()
        self.readiness = QLabel()
        self.readiness.setWordWrap(True)
        self.readiness.setAccessibleName('Topic finding and optional naming readiness')
        right.addWidget(self.readiness)
        self.advanced_toggle = QCheckBox('Setup details')
        right.addWidget(self.advanced_toggle)
        self.setup = SetupPanel()
        self.setup.hide()
        self.advanced_toggle.toggled.connect(self.setup.setVisible)
        right.addWidget(self.setup)
        right.addStretch()
        outer.addLayout(right, 2)
        self.reload_recent()

    def set_open_project(self, name, where=""):
        """Show the Back card for the project still open behind the start screen (None hides it)."""
        self.back_card.setVisible(bool(name))
        if name:
            self.back_title.setText(f"{name} is still open")
            self.back_where.setText(where)
            self.back_where.setVisible(bool(where))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        stacked = self.width() < max(900, self.fontMetrics().averageCharWidth() * 120)
        self.columns.setDirection(QBoxLayout.Direction.TopToBottom if stacked else QBoxLayout.Direction.LeftToRight)
        self.columns.setContentsMargins(20 if stacked else 56, 24 if stacked else 40,
                                       20 if stacked else 56, 24 if stacked else 40)

    def reload_recent(self):
        self.recent.clear()
        rows = app_settings.recent_projects()
        for row in rows:
            path = Path(row["path"])
            when = _relative_day(row.get("opened_at", ""))
            missing = not (path / "project.sqlite").is_file()
            item = QListWidgetItem(f"{row.get('name') or path.name}\n{path}" + (" · folder not found" if missing else "")
                                   + (f"   ·   {when}" if when else ""))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            if missing:
                item.setForeground(Qt.GlobalColor.gray)
            self.recent.addItem(item)
        if not rows:
            item = QListWidgetItem("No recent projects yet. Create one to get started.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.recent.addItem(item)

    def _open_recent(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.recent_requested.emit(path)

    def refresh_setup(self, busy_message: str | None = None):
        checks = health.run_checks()
        self.setup.show_checks(checks, busy_message)
        state = health.summary(checks)
        self.readiness.setText(busy_message or (
            'Ready. Theme finding and theme naming work offline on this computer.' if state == 'ready' else
            'Ready to find themes. Theme naming isn\'t set up, so themes will keep keyword names.' if state == 'limited' else
            'Theme finding needs attention. Open Setup details to check and repair. Reading, coding and practice still work.'))
