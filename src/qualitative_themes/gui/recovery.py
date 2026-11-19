"""Idle scheduling and visible controls for local recovery archives."""
import time
from pathlib import Path

from PySide6.QtCore import QObject, QEvent, QTimer, Qt
from PySide6.QtWidgets import QApplication, QLabel, QInputDialog, QMessageBox

from qualitative_themes.runtime import app_settings
from qualitative_themes.storage.recovery import (
    create_snapshot, list_snapshots, project_key, recovery_folder, snapshot_due,
)
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
        self.label = QLabel('Recovery: open a project')
        window.statusBar().addPermanentWidget(self.label, 1)
        self.enabled_action = menu.addAction('Automatic recovery snapshots')
        self.enabled_action.setCheckable(True)
        self.enabled_action.setEnabled(False)
        self.enabled_action.toggled.connect(self.set_enabled)
        self.create_action = menu.addAction('Create recovery snapshot now…', lambda: self.run(manual=True))
        self.restore_action = menu.addAction('Recover from snapshot…', self.choose_snapshot)
        self.create_action.setEnabled(False)
        self.restore_action.setEnabled(False)
        self.timer = QTimer(self)
        self.timer.setInterval(60_000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        window.hub.recovery_requested.connect(self.choose_recent)
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
        for action in (self.enabled_action, self.create_action, self.restore_action):
            action.setEnabled(True)
        self.label.setToolTip(f'Snapshots: {recovery_folder(directory)}\n'
                             'Keeps the latest seven verified copies. Not encrypted; same-disk copies do not protect against disk failure.')
        self.refresh()

    def refresh(self):
        try:
            snapshots = list_snapshots(self.directory)
            latest = f'Latest snapshot: {snapshots[0][0]}' if snapshots else 'Recovery: waiting for an idle moment'
            self.label.setText(latest + ('' if self.enabled_action.isChecked() else ' · Automatic snapshots off'))
        except Exception as exc:
            self.label.setText(f'Recovery needs attention: {exc}')

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
            self.label.setText(f'Could not save recovery preference: {exc}')
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
                self.label.setText('Recovery deferred: the pending comment could not be saved')
                return
            self.running = True
            result = transfer(self.window, 'Saving recovery snapshot',
                              lambda source, unused, progress: create_snapshot(source, progress, force=manual),
                              self.directory, None)
            self.refresh()
            if result.warning:
                self.label.setText(result.warning)
        except Exception as exc:
            self.label.setText(f'Recovery snapshot not saved: {exc}. Normal saving remains available.')
        finally:
            self.running = False

    def choose_recent(self):
        if self.running or self.window.busy:
            return
        rows = app_settings.recent_projects()
        if not rows:
            QMessageBox.information(self.window, 'Recovery snapshots', 'No recent projects are listed yet.')
            return
        choices = [f"{row.get('name', 'Project')} — {row['path']}" for row in rows]
        choice, ok = QInputDialog.getItem(self.window, 'Recover a recent project',
                                         'The original project does not need to be open or readable.', choices, 0, False)
        if ok:
            self.choose_snapshot(Path(rows[choices.index(choice)]['path']))

    def choose_snapshot(self, directory=None):
        directory = directory if isinstance(directory, Path) else self.directory
        if directory is None or self.running or self.window.busy:
            return
        try:
            snapshots = list_snapshots(directory)
            if not snapshots:
                QMessageBox.information(self.window, 'Recovery snapshots', 'No recovery snapshots have been saved for this project location yet.')
                return
            choices = [f'{day} · {path.stat().st_size / (1024 * 1024):.1f} MB · Copy {i + 1}'
                       for i, (day, path) in enumerate(snapshots)]
            choice, ok = QInputDialog.getItem(self.window, 'Recover from snapshot',
                                             'Choose a saved copy (newest first). It will restore into a new folder.',
                                             choices, 0, False)
            if ok:
                self.window.restore_project_backup(snapshots[choices.index(choice)][1])
        except Exception as exc:
            QMessageBox.warning(self.window, 'Could not read recovery snapshots', str(exc))
