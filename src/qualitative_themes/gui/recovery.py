"""Daily safety copies: taken when the researcher is idle, plus the File menu items for them."""
import time

from PySide6.QtCore import QObject, QEvent, QTimer, Qt
from PySide6.QtWidgets import QApplication, QLabel

from qualitative_themes.runtime import app_settings
from qualitative_themes.storage.recovery import (
    create_snapshot, list_snapshots, project_key, recovery_folder, snapshot_due,
)
from qualitative_themes.gui import strings
from qualitative_themes.gui.project_backup import transfer


class RecoveryController(QObject):
    """Takes the daily recovery snapshot when the user is idle.

    Every minute tick() checks: a project is open, snapshots are enabled for
    it, nothing else is running, no dialog is open, and there's been no input
    for 30 s. After a failure it waits 15 min before trying again. MainWindow
    won't close while `running` is True.
    """
    def __init__(self, window, menu):
        super().__init__(window)
        self.window = window
        self.directory = None
        self.running = False
        self.last_activity = time.monotonic()
        self.retry_after = 0
        self.label = QLabel('Safety copies: open a project')
        window.statusBar().addPermanentWidget(self.label, 1)
        self.enabled_action = menu.addAction(strings.DAILY_SAFETY_COPIES)
        self.enabled_action.setCheckable(True)
        self.enabled_action.setEnabled(False)
        self.enabled_action.toggled.connect(self.set_enabled)
        self.create_action = menu.addAction(strings.MAKE_SAFETY_COPY, lambda: self.run(manual=True))
        self.create_action.setEnabled(False)
        self.timer = QTimer(self)
        self.timer.setInterval(60_000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() in {QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress, QEvent.Type.Wheel,
                            QEvent.Type.InputMethod}:
            self.last_activity = time.monotonic()
        return False

    def project_changed(self, directory):
        self.directory = directory
        self.retry_after = 0
        self.last_activity = time.monotonic()
        preferences = app_settings.load().get('automatic_recovery', {})
        enabled = preferences.get(project_key(directory), True) if isinstance(preferences, dict) else True
        self.enabled_action.blockSignals(True)
        self.enabled_action.setChecked(bool(enabled))
        self.enabled_action.blockSignals(False)
        for action in (self.enabled_action, self.create_action):
            action.setEnabled(True)
        self.label.setToolTip(f'Safety copies: {recovery_folder(directory)}\n'
                             'Keeps the last 7 daily copies, on this computer. Back up to another drive for full protection.')
        self.refresh()

    def project_closed(self):
        """No project open: stop taking safety copies and grey out the menu items."""
        self.directory = None
        for action in (self.enabled_action, self.create_action):
            action.setEnabled(False)
        self.label.setText('Safety copies: open a project')

    def refresh(self):
        try:
            snapshots = list_snapshots(self.directory)
            latest = f'Last safety copy: {snapshots[0][0]}' if snapshots else 'Last safety copy: none yet'
            self.label.setText(latest + ('' if self.enabled_action.isChecked() else ' · Daily safety copies off'))
        except Exception as exc:
            self.label.setText(f'Safety copies need attention: {exc}')

    def set_enabled(self, enabled):
        if self.directory is None:
            return
        try:
            settings = app_settings.load()
            if not isinstance(settings.get('automatic_recovery'), dict):
                settings['automatic_recovery'] = {}
            settings.setdefault('automatic_recovery', {})[project_key(self.directory)] = bool(enabled)
            app_settings.save(settings)
        except Exception as exc:
            self.enabled_action.blockSignals(True)
            self.enabled_action.setChecked(not enabled)
            self.enabled_action.blockSignals(False)
            self.label.setText(f'Could not save the safety-copy setting: {exc}')
            return
        self.retry_after = 0
        self.refresh()

    def tick(self):
        if (self.directory is None or not self.enabled_action.isChecked() or self.running or
                self.window.busy or not self.window.isVisible() or self.window.isMinimized() or
                QApplication.activeModalWidget() or QApplication.activePopupWidget() or
                QApplication.mouseButtons() != Qt.MouseButton.NoButton or
                time.monotonic() < self.retry_after or time.monotonic() - self.last_activity < 30):
            return
        self.run()

    def run(self, manual=False):
        if self.directory is None or self.running or self.window.busy or QApplication.activeModalWidget():
            return
        self.retry_after = time.monotonic() + 15 * 60
        try:
            if not manual and not snapshot_due(self.directory):
                return
            if not self.window.workspace.comments_panel.flush_pending():
                self.label.setText('Safety copy postponed: the comment you are writing could not be saved')
                return
            self.running = True
            result = transfer(self.window, 'Making a safety copy',
                              lambda source, unused, progress: create_snapshot(source, progress, force=manual),
                              self.directory, None)
            self.refresh()
            if result.warning:
                self.label.setText(result.warning)
        except Exception as exc:
            self.label.setText(f'Safety copy not saved: {exc}. Your work is still saved as normal.')
        finally:
            self.running = False
