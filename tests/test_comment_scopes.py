"""Comments on passages, themes, documents and the project; pinning; the schema 3 → 4 upgrade."""
import sqlite3

import pytest

from qualitative_themes.storage import comments as store
from qualitative_themes.storage.comments import Target
from qualitative_themes.storage.coding import add_coding, create_theme, merge_themes
from qualitative_themes.storage.database import connect, create_project, open_project
from qualitative_themes.storage.documents import import_document

TEXT = "The clinic is far away. The bus route changed twice.\n\nWe redid the intake paperwork."


@pytest.fixture
def project(tmp_path):
    directory = create_project(tmp_path / "p", "P").directory
    docs = []
    for name in ("one", "two"):
        source = tmp_path / f"{name}.txt"
        source.write_text(TEXT + f" ({name})", encoding="utf-8")  # different files, or the second is a duplicate
        docs.append(import_document(directory, source).document_id)
    theme = create_theme(directory, "Transport")
    add_coding(directory, docs[0], 24, 52, theme)
    return directory, docs, theme


def bodies(threads):
    return [root.body for root, _ in threads]


def test_each_scope_lands_in_the_right_filter(project):
    directory, docs, theme = project
    store.add_comment(directory, Target.project(), "Coding rules")
    store.add_comment(directory, Target.theme(theme), "Theme is broad")
    store.add_comment(directory, Target.document(docs[0]), "Interview cut short")
    store.add_comment(directory, Target.passage(docs[0], 24, 52, theme), "Route changes")
    store.add_comment(directory, Target.passage(docs[1], 4, 10), "Other clinic")
    assert bodies(store.list_comments(directory, document_id=docs[0])) == ["Interview cut short", "Route changes"]
    assert bodies(store.list_comments(directory, theme_id=theme)) == ["Theme is broad", "Route changes"]
    assert bodies(store.list_comments(directory)) == ["Coding rules", "Theme is broad", "Interview cut short",
                                                      "Route changes", "Other clinic"]
    assert store.open_count(directory, document_id=docs[1]) == 1
    assert store.comment_counts(directory) == {docs[0]: 2, docs[1]: 1}


def test_replies_follow_their_thread_and_bad_targets_are_refused(project):
    directory, docs, theme = project
    root = store.add_comment(directory, Target.theme(theme), "Theme is broad")
    store.add_comment(directory, Target.project(), "Agreed", parent_id=root)
    (thread, replies), = store.list_comments(directory, theme_id=theme)
    assert replies[0].scope == "theme" and replies[0].theme_id == theme
    for bad in (Target.theme("missing"), Target.document("missing"), Target.passage(docs[0], 0, 10_000),
                Target("elsewhere")):
        with pytest.raises(ValueError):
            store.add_comment(directory, bad, "nope")


def test_pinned_threads_show_under_every_filter(project):
    directory, docs, theme = project
    rules = store.add_comment(directory, Target.project(), "Coding rules")
    store.add_comment(directory, Target.document(docs[1]), "Second interview")
    assert bodies(store.list_comments(directory, document_id=docs[1], pinned_too=True)) == ["Second interview"]
    store.set_pinned(directory, rules)
    for args in ({"document_id": docs[1]}, {"theme_id": theme}, {}):
        assert "Coding rules" in bodies(store.list_comments(directory, pinned_too=True, **args))
    assert "Coding rules" not in bodies(store.list_comments(directory, document_id=docs[1]))
    reply = store.add_comment(directory, Target.project(), "a reply", parent_id=rules)
    with pytest.raises(ValueError):
        store.set_pinned(directory, reply)
    store.set_pinned(directory, rules, False)
    assert bodies(store.list_comments(directory, theme_id=theme, pinned_too=True)) == []


def test_drafts_can_move_target_and_theme_drafts_come_back_in_any_document(project):
    directory, docs, theme = project
    draft = store.add_comment(directory, Target.passage(docs[0], 0, 3), "half", draft=True)
    store.update_comment(directory, draft, "half-written", target=Target.theme(theme))
    assert [d.id for d in store.drafts(directory, docs[1])] == [draft]
    store.update_comment(directory, draft, publish=True)
    with pytest.raises(ValueError):
        store.update_comment(directory, draft, target=Target.project())


def test_merging_themes_moves_their_comments(project):
    directory, docs, theme = project
    other = create_theme(directory, "Buses")
    store.add_comment(directory, Target.theme(other), "About buses")
    merge_themes(directory, other, theme)
    assert bodies(store.list_comments(directory, theme_id=theme)) == ["About buses"]


def _make_version_3(directory, docs, theme):
    """Turn a new project back into the stage 0–2 layout: old comments table, memos, user_version 3."""
    with sqlite3.connect(directory / "project.sqlite") as raw:
        raw.executescript(f"""
            DROP TABLE comments;
            CREATE TABLE comments (id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                start_char INTEGER NOT NULL, end_char INTEGER NOT NULL, quote TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL, parent_id TEXT REFERENCES comments(id) ON DELETE CASCADE, author TEXT,
                key_quote INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, resolved_at TEXT, source_note_id TEXT);
            CREATE TABLE notes (id TEXT PRIMARY KEY, document_id TEXT, theme_id TEXT, coding_id TEXT,
                body TEXT NOT NULL, created_at TEXT NOT NULL);
            INSERT INTO comments VALUES ('c1', '{docs[0]}', 4, 10, 'clinic', 'Old comment', NULL, NULL, 1, 'open',
                '2026-01-01', '2026-01-01', NULL, NULL);
            INSERT INTO notes VALUES ('n1', NULL, '{theme}', NULL, 'Theme memo', '2026-01-02');
            INSERT INTO notes VALUES ('n2', '{docs[1]}', NULL, NULL, 'Document memo', '2026-01-03');
            INSERT INTO notes SELECT 'n3', NULL, NULL, id, 'Passage memo', '2026-01-04' FROM codings;
            PRAGMA user_version = 3;
        """)


def test_version_3_project_is_upgraded_and_memos_become_comments(project):
    directory, docs, theme = project
    _make_version_3(directory, docs, theme)
    open_project(directory)
    with connect(directory) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='notes'").fetchone()
    assert bodies(store.list_comments(directory, document_id=docs[0])) == ["Old comment", "Passage memo"]
    assert bodies(store.list_comments(directory, theme_id=theme)) == ["Theme memo", "Passage memo"]
    assert bodies(store.list_comments(directory, document_id=docs[1])) == ["Document memo"]
    passage = next(root for root, _ in store.list_comments(directory) if root.body == "Passage memo")
    assert (passage.start, passage.end, passage.quote) == (24, 52, TEXT[24:52])
    assert passage.theme_id == theme
    old = next(root for root, _ in store.list_comments(directory) if root.body == "Old comment")
    assert old.key_quote and old.scope == "passage"


def test_version_3_backup_restores_and_upgrades(project, tmp_path):
    from qualitative_themes.storage.backup import create_backup, restore_backup
    directory, docs, theme = project
    archive = tmp_path / "study.tmtbackup"
    _make_version_3(directory, docs, theme)
    create_backup(directory, archive)  # copies the database as it is, still version 3
    restore_backup(archive, tmp_path / "restored")
    assert "Theme memo" in bodies(store.list_comments(tmp_path / "restored", theme_id=theme))


# GUI ---------------------------------------------------------------------------

def make_workspace(directory, doc):
    from qualitative_themes.gui.read_code import CodingWorkspace
    workspace = CodingWorkspace()
    workspace.set_project(directory)
    workspace.document_choice.setCurrentIndex(workspace.document_choice.findData(doc))
    return workspace


def test_comment_on_choices_and_pinned_section(project, qt_test_application):
    directory, docs, theme = project
    w = make_workspace(directory, docs[0])
    w.reload_themes(theme)
    panel = w.comments_panel
    for key, body in (("theme", "On the theme"), ("document", "On the document"), ("project", "On everything")):
        panel.comment_on.setCurrentIndex(panel.comment_on.findData(key))
        panel.add_chosen()
        assert panel.pending.scope == key
        panel.editor.setPlainText(body)
        panel.save_comment()
    panel.comment_on.setCurrentIndex(panel.comment_on.findData("passage"))
    cursor = w.viewer.textCursor()
    cursor.clearSelection()
    w.viewer.setTextCursor(cursor)
    panel.add_chosen()
    assert panel.pending is None and "Select some text" in panel.save_status.text()
    assert [card.root.body for card in panel.cards] == ["On the document"]
    assert "(1)" in w.comments_toggle.text()
    panel.set_filter("project")
    assert [card.root.body for card in panel.cards] == ["On everything", "On the theme", "On the document"]
    project_card = panel.cards[0]
    panel.pin(project_card.root.id, True)
    panel.set_filter("theme")
    assert [card.root.body for card in panel.cards] == ["On everything", "On the theme"]
    assert panel.cards[0].root.pinned
    w.close()


def test_view_saves_comment_filter_highlight_and_panel(project, qt_test_application):
    directory, docs, theme = project
    w = make_workspace(directory, docs[0])
    w.comments_panel.set_filter("project")
    w.highlight_selected.setChecked(True)
    w.comments_toggle.setChecked(False)
    view = w.capture_view()
    w.close()
    again = make_workspace(directory, docs[0])
    again.restore_view(view)
    assert again.comments_panel.filter_key() == "project"
    assert again.highlight_selected.isChecked() and not again.comments_toggle.isChecked()
    assert again.comments_panel.isHidden()
    again.close()


def test_card_click_opens_the_passage_in_another_document(project, qt_test_application):
    directory, docs, theme = project
    store.add_comment(directory, Target.passage(docs[1], 4, 10), "Other clinic")
    w = make_workspace(directory, docs[0])
    w.open_comment_target(store.list_comments(directory, document_id=docs[1])[0][0].target)
    assert w.doc_id == docs[1]
    assert w.viewer.textCursor().selectedText() == "clinic"
    w.close()
