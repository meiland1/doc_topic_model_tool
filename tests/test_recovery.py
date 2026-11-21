from datetime import date, timedelta
import sqlite3

import pytest

from qualitative_themes.storage.database import create_project, open_project
from qualitative_themes.storage.documents import import_document
from qualitative_themes.storage.backup import restore_backup
from qualitative_themes.storage import comments, recovery


@pytest.fixture
def study(tmp_path):
    project = create_project(tmp_path / 'project', 'Recovery test')
    source = tmp_path / 'source.txt'
    source.write_text('The bus serves the clinic.', encoding='utf-8')
    doc = import_document(project.directory, source).document_id
    return project, doc


def test_daily_snapshot_and_restorable_draft(study, tmp_path):
    project, doc = study
    day = date(2026, 11, 13)
    comments.add_comment(project.directory, doc, 0, 7, 'Keep this draft', draft=True)
    assert recovery.snapshot_due(project.directory, day)
    first = recovery.create_snapshot(project.directory, today=day)
    assert not recovery.snapshot_due(project.directory, day)
    assert recovery.create_snapshot(project.directory, today=day).path == first.path
    assert len(recovery.list_snapshots(project.directory)) == 1
    restored = restore_backup(first.path, tmp_path / 'restored')
    assert open_project(restored).name == project.name
    assert comments.drafts(restored, doc)[0].body == 'Keep this draft'
    assert recovery.recovery_folder(restored) != recovery.recovery_folder(project.directory)


def test_retention_preserves_unmanaged_files(study):
    project, _ = study
    day = date(2026, 11, 1)
    first = recovery.create_snapshot(project.directory, today=day).path
    unrelated = first.parent / 'manual-backup.tmtbackup'
    unrelated.write_bytes(b'not managed by recovery')
    for offset in range(1, 9):
        recovery.create_snapshot(project.directory, today=day + timedelta(days=offset))
    snapshots = recovery.list_snapshots(project.directory)
    assert len(snapshots) == 7
    assert not first.exists()
    assert snapshots[0][0] == '2026-11-09'
    assert snapshots[-1][0] == '2026-11-03'
    assert unrelated.read_bytes() == b'not managed by recovery'


def test_failure_never_prunes_and_retry_works(study, monkeypatch):
    project, _ = study
    day = date(2026, 11, 13)
    original = recovery.create_backup
    def fail(*args, **kwargs):
        raise OSError('Disk full')
    monkeypatch.setattr(recovery, 'create_backup', fail)
    with pytest.raises(OSError, match='Disk full'):
        recovery.create_snapshot(project.directory, today=day)
    assert recovery.snapshot_due(project.directory, day)
    monkeypatch.setattr(recovery, 'create_backup', original)
    first = recovery.create_snapshot(project.directory, today=day).path
    monkeypatch.setattr(recovery, 'create_backup', fail)
    with pytest.raises(OSError):
        recovery.create_snapshot(project.directory, today=day + timedelta(days=1))
    assert first.is_file()
    assert len(recovery.list_snapshots(project.directory)) == 1


def test_tampered_retention_path_cannot_delete_outside_file(study, tmp_path):
    project, _ = study
    recovery.create_snapshot(project.directory)
    outside = tmp_path / 'keep.txt'
    outside.write_text('keep')
    index = recovery.recovery_folder(project.directory) / 'recovery.sqlite'
    conn = sqlite3.connect(index)
    try:
        conn.execute("UPDATE snapshots SET name='../keep.txt'")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ValueError, match='outside'):
        recovery.list_snapshots(project.directory)
    assert outside.read_text() == 'keep'


def test_manual_snapshot_can_capture_new_work_same_day(study):
    project, _ = study
    first = recovery.create_snapshot(project.directory).path
    second = recovery.create_snapshot(project.directory, force=True).path
    assert first != second
    assert len(recovery.list_snapshots(project.directory)) == 2


def test_prune_failure_keeps_verified_new_snapshot(study, monkeypatch):
    from pathlib import Path
    project, _ = study
    for offset in range(7):
        recovery.create_snapshot(project.directory, today=date(2026, 11, 1) + timedelta(days=offset))
    oldest = recovery.list_snapshots(project.directory)[-1][1]
    original = Path.unlink
    def protected(path, *args, **kwargs):
        if path == oldest:
            raise PermissionError('Older snapshot is in use')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', protected)
    result = recovery.create_snapshot(project.directory, today=date(2026, 11, 8))
    assert result.path.is_file() and 'older copies' in result.warning
    assert oldest.is_file() and len(recovery.list_snapshots(project.directory)) == 8


def test_restore_dialog_finds_safety_copy_when_original_folder_was_moved(study, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from qualitative_themes.gui.main_window import MainWindow
    from qualitative_themes.gui.restore_dialog import RestoreDialog
    project, _ = study
    window = MainWindow()
    window._show_project(project)
    snapshot = recovery.create_snapshot(project.directory).path
    window.show_hub()
    window.project = None
    project.directory.rename(tmp_path / 'moved-project')
    dialog = RestoreDialog(None)
    assert dialog.from_copy.isChecked() and dialog.chosen_snapshot() == snapshot
    monkeypatch.setattr(RestoreDialog, 'exec', lambda self: QDialog.DialogCode.Accepted)
    chosen = []
    monkeypatch.setattr(window, 'restore_project_backup', chosen.append)
    window.restore_project()
    assert chosen == [snapshot]


def test_restore_dialog_backup_file_choice_asks_for_the_file(study, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog, QFileDialog
    from qualitative_themes.gui.main_window import MainWindow
    from qualitative_themes.gui.restore_dialog import RestoreDialog
    project, _ = study
    window = MainWindow()
    window._show_project(project)
    def accept_with_backup(self):
        self.from_backup.setChecked(True)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(RestoreDialog, 'exec', accept_with_backup)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(tmp_path / 'b.tmtbackup'), ''))
    chosen = []
    monkeypatch.setattr(window, 'restore_project_backup', chosen.append)
    window.restore_project()
    assert chosen == [str(tmp_path / 'b.tmtbackup')]


def test_disabled_preference_persists_on_reopen(study):
    from qualitative_themes.gui.main_window import MainWindow
    project, _ = study
    window = MainWindow()
    window._show_project(project)
    window.recovery.enabled_action.setChecked(False)
    window.recovery.project_changed(project.directory)
    assert not window.recovery.enabled_action.isChecked()
    assert 'off' in window.recovery.label.text()


def test_idle_scheduler_defers_activity_busy_and_disabled(study, monkeypatch):
    from qualitative_themes.gui.main_window import MainWindow
    from qualitative_themes.gui import recovery as gui
    project, _ = study
    window = MainWindow()
    window._show_project(project)
    window.show()
    controller = window.recovery
    attempts = []
    monkeypatch.setattr(controller, 'run', lambda **kwargs: attempts.append(True))
    controller.tick()
    assert not attempts
    controller.last_activity = gui.time.monotonic() - 60
    window._analysis_worker = object()
    controller.tick()
    window._analysis_worker = None
    assert not attempts
    controller.enabled_action.setChecked(False)
    controller.tick()
    assert not attempts
    controller.enabled_action.setChecked(True)
    controller.tick()
    assert attempts == [True]


def test_ui_snapshot_flushes_draft_and_failure_is_nonmodal(study, monkeypatch):
    from qualitative_themes.gui.main_window import MainWindow
    from qualitative_themes.gui import recovery as gui
    project, doc = study
    window = MainWindow()
    window._show_project(project)
    panel = window.workspace.comments_panel
    panel.set_document(project.directory, doc)
    panel.begin_comment(0, 7, 'The bus')
    panel.editor.setPlainText('Before the timer fires')
    window.recovery.run(manual=True)
    assert len(recovery.list_snapshots(project.directory)) == 1
    assert comments.drafts(project.directory, doc)[0].body == 'Before the timer fires'
    def fail(*args, **kwargs):
        raise OSError('Read-only disk')
    monkeypatch.setattr(gui, 'transfer', fail)
    window.recovery.run(manual=True)
    assert 'still saved as normal' in window.recovery.label.text()
    assert not window.recovery.running
    panel.editor.setPlainText('Still able to save')
    assert panel.flush_pending()
