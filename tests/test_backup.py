import hashlib
import json
from zipfile import ZipFile

import pytest

from qualitative_themes.storage.backup import create_backup, restore_backup
from qualitative_themes.storage.database import create_project, open_project, connect
from qualitative_themes.storage.documents import import_document, remove_documents
from qualitative_themes.storage import comments


@pytest.fixture
def project(tmp_path):
    result = create_project(tmp_path / 'study', 'Study')
    source = tmp_path / 'interview.txt'
    source.write_text('The clinic bus is late. The garden needs water.', encoding='utf-8')
    doc = import_document(result.directory, source).document_id
    return result, doc


def test_backup_roundtrip_preserves_research_and_sources(project, tmp_path):
    from qualitative_themes.storage.coding import create_theme, add_coding, add_note
    study, doc = project
    theme = create_theme(study.directory, 'Transport')
    add_coding(study.directory, doc, 0, 10, theme)
    add_note(study.directory, 'Existing memo is preserved', theme_id=theme)
    comments.add_comment(study.directory, doc, 0, 10, 'Pending observation', draft=True)
    remove_documents(study.directory, [doc])
    artifact = study.directory / 'models' / 'nested' / 'saved.bin'
    artifact.parent.mkdir()
    artifact.write_bytes(b'saved model artifact')
    archive = create_backup(study.directory, tmp_path / 'copy.tmtbackup')
    restored = restore_backup(archive, tmp_path / 'restored')
    assert open_project(restored).name == 'Study'
    assert (restored / 'models/nested/saved.bin').read_bytes() == artifact.read_bytes()
    with connect(study.directory) as before, connect(restored) as after:
        for table in ('project', 'documents', 'document_pages', 'themes', 'codings', 'notes', 'comments', 'history_events'):
            assert before.execute(f'SELECT * FROM {table}').fetchall() == after.execute(f'SELECT * FROM {table}').fetchall()


def test_existing_backups_and_destinations_are_untouched(project, tmp_path):
    study, _ = project
    archive = create_backup(study.directory, tmp_path / 'copy.tmtbackup')
    original = archive.read_bytes()
    with pytest.raises(FileExistsError):
        create_backup(study.directory, archive)
    with pytest.raises(FileExistsError):
        restore_backup(archive, study.directory)
    assert archive.read_bytes() == original
    with pytest.raises(ValueError, match='outside'):
        create_backup(study.directory, study.directory / 'copy.tmtbackup')


def test_missing_source_refuses_incomplete_backup(project, tmp_path):
    study, _ = project
    next((study.directory / 'sources').iterdir()).unlink()
    with pytest.raises(ValueError, match='missing'):
        create_backup(study.directory, tmp_path / 'copy.tmtbackup')
    assert not (tmp_path / 'copy.tmtbackup').exists()


def test_corruption_refused_before_destination_created(project, tmp_path):
    study, _ = project
    archive = create_backup(study.directory, tmp_path / 'copy.tmtbackup')
    damaged = tmp_path / 'damaged.tmtbackup'
    with ZipFile(archive) as original, ZipFile(damaged, 'w') as target:
        for entry in original.infolist():
            data = original.read(entry)
            if entry.filename.startswith('sources/'):
                data = b'x' * len(data)
            target.writestr(entry, data)
    with pytest.raises(ValueError, match='verification'):
        restore_backup(damaged, tmp_path / 'restored')
    assert not (tmp_path / 'restored').exists()


@pytest.mark.parametrize('name', ['../escape', 'sources/../../escape', 'sources/C:escape', 'sources/CON', '/absolute'])
def test_unsafe_archive_paths_refused(tmp_path, name):
    archive = tmp_path / 'unsafe.tmtbackup'
    files = {name: {'size': 1, 'sha256': hashlib.sha256(b'x').hexdigest()},
             'project.sqlite': {'size': 1, 'sha256': hashlib.sha256(b'x').hexdigest()}}
    with ZipFile(archive, 'w') as target:
        target.writestr('backup-manifest.json', json.dumps({'format': 1, 'files': files}))
        for path in files:
            target.writestr(path, b'x')
    with pytest.raises(ValueError, match='unsafe'):
        restore_backup(archive, tmp_path / 'restored')
    assert not (tmp_path / 'restored').exists()


def test_backup_works_without_hard_links(project, tmp_path, monkeypatch):
    study, _ = project
    def unsupported(*args):
        raise OSError('Hard links unavailable')
    monkeypatch.setattr('qualitative_themes.storage.backup.os.link', unsupported)
    archive = create_backup(study.directory, tmp_path / 'copy.tmtbackup')
    assert open_project(restore_backup(archive, tmp_path / 'restored')).name == 'Study'


def test_failed_archive_write_leaves_no_backup_and_releases_database(project, tmp_path, monkeypatch):
    study, _ = project
    destination = tmp_path / 'failed.tmtbackup'
    def fail(*args, **kwargs):
        raise OSError('Disk full')
    monkeypatch.setattr(ZipFile, 'write', fail)
    with pytest.raises(OSError, match='Disk full'):
        create_backup(study.directory, destination)
    assert not destination.exists()
    with connect(study.directory) as conn:
        conn.execute("UPDATE project SET name='Still writable'")
    assert open_project(study.directory).name == 'Still writable'


def test_backup_blocks_other_database_writers_until_finished(project, tmp_path):
    import sqlite3
    study, _ = project
    checked = []
    def progress(message):
        other = sqlite3.connect(study.directory / 'project.sqlite', timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                other.execute("UPDATE project SET name='Other writer'")
            checked.append(message)
        finally:
            other.close()
    create_backup(study.directory, tmp_path / 'copy.tmtbackup', progress)
    assert checked and open_project(study.directory).name == 'Study'


def test_close_flushes_comment_before_autosave_timer(project):
    from qualitative_themes.gui.main_window import MainWindow
    study, doc = project
    window = MainWindow()
    window._show_project(study)
    panel = window.workspace.comments_panel
    panel.set_document(study.directory, doc)
    panel.begin_comment(0, 10, 'The clinic')
    panel.editor.setPlainText('Save this immediately')
    assert panel._autosave.isActive()
    window.close()
    assert comments.drafts(study.directory, doc)[0].body == 'Save this immediately'


def test_failed_comment_flush_keeps_text_and_document(project, monkeypatch):
    from qualitative_themes.gui.comments_panel import CommentsPanel
    study, doc = project
    panel = CommentsPanel()
    panel.set_document(study.directory, doc)
    panel.begin_comment(0, 10, 'The clinic')
    panel.editor.setPlainText('Do not lose this')
    def fail(*args, **kwargs):
        raise OSError('Disk full')
    monkeypatch.setattr(comments, 'add_comment', fail)
    assert panel.set_document(None, None) is False
    assert panel.document_id == doc
    assert panel.editor.toPlainText() == 'Do not lose this'
    assert 'Could not save draft' in panel.save_status.text()


def test_transfer_dialog_finishes_and_closes_worker(project, tmp_path):
    from qualitative_themes.gui.project_backup import transfer
    study, _ = project
    archive = tmp_path / 'copy.tmtbackup'
    transfer(None, 'Backup', create_backup, study.directory, archive)
    assert archive.is_file()


def test_backup_action_flushes_draft_and_restore_opens_new_copy(project, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox
    from qualitative_themes.gui.main_window import MainWindow
    study, doc = project
    window = MainWindow()
    window._show_project(study)
    panel = window.workspace.comments_panel
    panel.set_document(study.directory, doc)
    panel.begin_comment(0, 10, 'The clinic')
    panel.editor.setPlainText('Draft included in backup')
    archive = tmp_path / 'via-ui.tmtbackup'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(archive), ''))
    monkeypatch.setattr(QMessageBox, 'information', lambda *a, **k: None)
    errors = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a, **k: errors.append(a[2]))
    window.back_up_project()
    assert archive.is_file() and not errors
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(archive), ''))
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *a, **k: str(tmp_path))
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('Restored via UI', True))
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.restore_project_backup()
    assert not errors
    assert window.project.directory == tmp_path / 'Restored via UI'
    assert comments.drafts(window.project.directory, doc)[0].body == 'Draft included in backup'
