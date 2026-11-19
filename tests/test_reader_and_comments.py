"""Reader upgrades: margin comments, schema v2 migration, find bar, selection toolbar, undo."""
import sqlite3

import pytest
from PySide6.QtGui import QTextCursor

from qualitative_themes.storage import comments as store
from qualitative_themes.storage.coding import add_coding, add_note, create_theme, occurrences
from qualitative_themes.storage.database import connect, create_project
from qualitative_themes.storage.documents import import_document
from qualitative_themes.storage.migrations import SCHEMA_VERSION

TEXT = ("The clinic is forty minutes away by bus. The bus route changed twice.\n\n"
        "We had to redo the intake paperwork. The clinic wanted the insurance card again.")


@pytest.fixture
def project(tmp_path):
    p = create_project(tmp_path / "p", "P")
    source = tmp_path / "doc.txt"
    source.write_text(TEXT)
    doc = import_document(p.directory, source).document_id
    return p.directory, doc


def test_comments_thread_resolve_and_drafts(project):
    directory, doc = project
    root = store.add_comment(directory, doc, 4, 10, "Ties to the transport theme", key_quote=True)
    store.add_comment(directory, doc, 0, 0, "Agreed", parent_id=root)
    threads = store.list_threads(directory, doc)
    assert len(threads) == 1 and threads[0][0].quote == "clinic" and threads[0][1][0].body == "Agreed"
    assert threads[0][1][0].start == 4  # replies inherit the thread's anchor
    store.set_resolved(directory, root)
    assert store.list_threads(directory, doc) == []
    assert len(store.list_threads(directory, doc, include_resolved=True)) == 1
    draft = store.add_comment(directory, doc, 0, 3, "half-written", draft=True)
    assert [d.id for d in store.drafts(directory, doc)] == [draft]
    store.update_comment(directory, draft, "finished thought", publish=True)
    assert store.drafts(directory, doc) == []
    with pytest.raises(ValueError):
        store.add_comment(directory, doc, 0, 999, "out of range")


def test_comments_survive_coding_removal(project):
    from qualitative_themes.storage.coding import remove_coding
    directory, doc = project
    theme = create_theme(directory, "Transport")
    coding = add_coding(directory, doc, 4, 10, theme)
    store.add_comment(directory, doc, 4, 10, "note on the coding")
    remove_coding(directory, coding)
    assert store.list_threads(directory, doc)[0][0].body == "note on the coding"


def test_v1_project_upgrades_with_backup_and_migrated_passage_notes(project):
    directory, doc = project
    theme = create_theme(directory, "Transport")
    coding = add_coding(directory, doc, 4, 10, theme)
    add_note(directory, "old passage note", coding_id=coding)
    database = directory / "project.sqlite"
    raw = sqlite3.connect(database)
    raw.executescript("DROP TABLE comments; DROP TABLE IF EXISTS documents_fts; "
                      "DROP TRIGGER IF EXISTS documents_fts_insert; DROP TRIGGER IF EXISTS documents_fts_update; "
                      "DROP TRIGGER IF EXISTS documents_fts_delete; PRAGMA user_version = 1;")
    raw.close()
    with connect(directory) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert (directory / "project.sqlite.v1-backup").is_file()
    thread = store.list_threads(directory, doc)[0][0]
    assert thread.body == "old passage note" and thread.quote == "clinic"


def make_workspace(directory, doc):
    from qualitative_themes.gui.coding_workspace import CodingWorkspace
    w = CodingWorkspace()
    w.set_project(directory)
    w.document_choice.setCurrentIndex(w.document_choice.findData(doc))
    return w


def select(w, start, end):
    q_start, q_end = w.coords.qt_range(start, end)
    cursor = w.viewer.textCursor()
    cursor.setPosition(q_start)
    cursor.setPosition(q_end, QTextCursor.MoveMode.KeepAnchor)
    w.viewer.setTextCursor(cursor)


def test_toolbar_codes_selection_and_undo_redo(project):
    directory, doc = project
    theme = create_theme(directory, "Transport")
    w = make_workspace(directory, doc)
    select(w, 4, 10)
    w.update_selection_toolbar()
    assert w.selection_toolbar.isVisibleTo(w.viewer.viewport())
    w.code_selection_with_number(1)
    assert [(m[3], m[4]) for m in occurrences(directory, theme)] == [(4, 10)]
    assert w.layers.theme_ranges and w.markers.markers
    w.undo_stack.undo()
    assert occurrences(directory, theme) == []
    w.undo_stack.redo()
    assert [(m[3], m[4]) for m in occurrences(directory, theme)] == [(4, 10)]


def test_find_bar_counts_cycles_and_filters_coded(project):
    directory, doc = project
    theme = create_theme(directory, "Transport")
    add_coding(directory, doc, 0, 40, theme)
    w = make_workspace(directory, doc)
    w.reload_themes(theme)
    w.open_find()
    w.find_bar.field.setText("clinic")
    assert len(w.find_matches) == 2 and w.find_bar.count.text() == "1 of 2"
    w.step_find(1)
    assert w.find_bar.count.text() == "2 of 2"
    w.find_bar.case.setChecked(True)
    w.find_bar.field.setText("The")
    assert len(w.find_matches) == 3
    w.find_bar.coded_only.setChecked(True)
    assert len(w.find_matches) == 1
    w.close_find()
    assert not w.layers.find_ranges


def test_comment_from_selection_is_saved_beside_the_text(project):
    directory, doc = project
    w = make_workspace(directory, doc)
    select(w, 4, 10)
    w.comment_on_selection()
    assert w.comments_panel.composer.isVisibleTo(w)
    w.comments_panel.editor.setPlainText("Distance matters here")
    w.comments_panel.save_draft()
    assert store.drafts(directory, doc)
    w.comments_panel.save_comment()
    assert store.list_threads(directory, doc)[0][0].body == "Distance matters here"
    assert store.drafts(directory, doc) == []
    assert len(w.comments_panel.cards) == 1


def test_comments_can_be_edited_and_deleted_from_the_panel(project, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    directory, doc = project
    root = store.add_comment(directory, doc, 4, 10, "first wording")
    store.add_comment(directory, doc, 0, 0, "a reply", parent_id=root)
    w = make_workspace(directory, doc)
    panel = w.comments_panel
    panel.edit(root, "second wording")
    thread = store.list_threads(directory, doc)[0]
    assert thread[0].body == "second wording" and thread[0].updated_at != thread[0].created_at
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    panel.delete(thread[1][0])
    assert store.list_threads(directory, doc)[0][1] == []
    panel.delete(store.list_threads(directory, doc)[0][0])
    assert store.list_threads(directory, doc) == [] and panel.cards == []


def test_view_cache_round_trip_and_staleness(project, tmp_path):
    from qualitative_themes.analysis import view_cache
    directory, doc = project
    result = {"html": {"map": "<html>m</html>"}, "messages": {"heatmap": "n/a"}, "tree": "T", "notes": ["sampled"],
              "snapshot": {"theme_topics": {"t": 0}, "themes": {"t": {"theme": "A", "unique_coded_ranges": 1,
                                                                      "documents_covered": 1, "keywords": ["k"], "big": "x"}}}}
    view_cache.save_views(directory, "run-1", result)
    saved = view_cache.load_views(directory, "run-1")
    assert saved["current"] and saved["tree"] == "T" and saved["paths"]["map"].read_text() == "<html>m</html>"
    assert "big" not in saved["snapshot"]["themes"]["t"]
    theme = create_theme(directory, "New")
    add_coding(directory, doc, 0, 3, theme)
    assert view_cache.load_views(directory, "run-1")["current"] is False
    assert view_cache.load_views(directory, "missing") is None
