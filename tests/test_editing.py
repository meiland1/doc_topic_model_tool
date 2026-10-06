import pytest

from qualitative_themes.storage.coding import (
    add_coding, add_note, create_theme, effective_assignments, list_notes,
    list_themes, merge_themes, occurrences, remove_coding, rename_theme,
    themes_for_range, update_note,
)
from qualitative_themes.storage.database import create_project
from qualitative_themes.storage.documents import import_document


def test_remove_rename_and_notes_survive_reopen(tmp_path):
    project = create_project(tmp_path / "project", "Study")
    source = tmp_path / "source.txt"
    source.write_text("A meaningful passage.", encoding="utf-8")
    doc = import_document(project.directory, source).document_id
    first = create_theme(project.directory, "Initial")
    second = create_theme(project.directory, "Other")
    coding = add_coding(project.directory, doc, 2, 12, first)
    add_coding(project.directory, doc, 2, 12, second)
    rename_theme(project.directory, first, "Revised")
    assert themes_for_range(project.directory, doc, 2, 12) == ["Other", "Revised"]
    note = add_note(project.directory, "Memo", coding_id=coding)
    update_note(project.directory, note, "Edited memo")
    add_note(project.directory, "Theme memo", theme_id=first)
    add_note(project.directory, "Source memo", document_id=doc)
    remove_coding(project.directory, coding)
    assert not occurrences(project.directory, first)
    assert len(occurrences(project.directory, second)) == 1
    assert list_notes(project.directory, coding_id=coding) == [(note, "Edited memo")]
    assert list_notes(project.directory, document_id=doc)[0][1] == "Source memo"
    assert effective_assignments(project.directory, first) == []
    assert [x[1] for x in list_themes(project.directory)] == ["Other", "Revised"]


def test_merge_preserves_notes_and_deduplicates_ranges(tmp_path):
    project = create_project(tmp_path / "project", "Study")
    source = tmp_path / "source.txt"
    source.write_text("Same range. Another range.", encoding="utf-8")
    doc = import_document(project.directory, source).document_id
    source_theme = create_theme(project.directory, "Source")
    target = create_theme(project.directory, "Target")
    duplicate = add_coding(project.directory, doc, 0, 10, source_theme)
    kept = add_coding(project.directory, doc, 0, 10, target)
    add_coding(project.directory, doc, 12, 25, source_theme)
    memo = add_note(project.directory, "Attached", coding_id=duplicate)
    theme_memo = add_note(project.directory, "Overview", theme_id=source_theme)
    with pytest.raises(ValueError):
        merge_themes(project.directory, source_theme, "missing")
    assert len(occurrences(project.directory, source_theme)) == 2
    merge_themes(project.directory, source_theme, target)
    assert len(occurrences(project.directory, target)) == 2
    assert [id for id, *_ in occurrences(project.directory, target)].count(kept) == 1
    assert list_notes(project.directory, coding_id=kept) == [(memo, "Attached")]
    assert list_notes(project.directory, theme_id=target) == [(theme_memo, "Overview")]
    assert source_theme not in [x[0] for x in list_themes(project.directory)]
