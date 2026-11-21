import pytest

from qualitative_themes.storage.coding import (
    add_coding, create_theme, effective_assignments,
    list_themes, merge_themes, occurrences, remove_coding, rename_theme,
    themes_for_range,
)
from qualitative_themes.storage.comments import Target, add_comment, list_comments, update_comment
from qualitative_themes.storage.database import create_project
from qualitative_themes.storage.documents import import_document


def test_remove_rename_and_comments_survive_removing_the_coding(tmp_path):
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
    note = add_comment(project.directory, Target.passage(doc, 2, 12, first), "Comment")
    update_comment(project.directory, note, "Edited comment")
    add_comment(project.directory, Target.theme(first), "Theme comment")
    add_comment(project.directory, Target.document(doc), "Source comment")
    remove_coding(project.directory, coding)
    assert not occurrences(project.directory, first)
    assert len(occurrences(project.directory, second)) == 1
    assert [c.body for c, _ in list_comments(project.directory, document_id=doc)] == ["Source comment", "Edited comment"]
    assert [c.body for c, _ in list_comments(project.directory, theme_id=first)] == ["Theme comment", "Edited comment"]
    assert effective_assignments(project.directory, first) == []
    assert [x[1] for x in list_themes(project.directory)] == ["Other", "Revised"]


def test_merge_moves_comments_and_deduplicates_ranges(tmp_path):
    project = create_project(tmp_path / "project", "Study")
    source = tmp_path / "source.txt"
    source.write_text("Same range. Another range.", encoding="utf-8")
    doc = import_document(project.directory, source).document_id
    source_theme = create_theme(project.directory, "Source")
    target = create_theme(project.directory, "Target")
    add_coding(project.directory, doc, 0, 10, source_theme)
    kept = add_coding(project.directory, doc, 0, 10, target)
    add_coding(project.directory, doc, 12, 25, source_theme)
    memo = add_comment(project.directory, Target.passage(doc, 0, 10, source_theme), "Attached")
    theme_memo = add_comment(project.directory, Target.theme(source_theme), "Overview")
    with pytest.raises(ValueError):
        merge_themes(project.directory, source_theme, "missing")
    assert len(occurrences(project.directory, source_theme)) == 2
    merge_themes(project.directory, source_theme, target)
    assert len(occurrences(project.directory, target)) == 2
    assert [id for id, *_ in occurrences(project.directory, target)].count(kept) == 1
    assert [c.id for c, _ in list_comments(project.directory, theme_id=target)] == [theme_memo, memo]
    assert list_comments(project.directory, theme_id=source_theme) == []
    assert source_theme not in [x[0] for x in list_themes(project.directory)]
