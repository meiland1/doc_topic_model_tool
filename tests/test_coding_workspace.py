import pytest

from qualitative_themes.domain.text_coordinates import TextCoordinates
from qualitative_themes.storage.coding import (add_coding, create_theme,
                                                list_themes, occurrences)
from qualitative_themes.storage.database import create_project
from qualitative_themes.storage.documents import import_document


def test_coordinate_map_crlf_formfeed_and_emoji():
    source = "A\r\nB\fC😀D\u2028E"
    mapping = TextCoordinates.build(source)
    assert mapping.display_text == "A\nB\nC😀D\nE"
    for start, end in [(0, 1), (1, 3), (3, 5), (5, 6), (6, 7), (7, 8), (9, 10)]:
        qt_start, qt_end = mapping.qt_range(start, end)
        assert mapping.canonical_range(qt_start, qt_end) == (start, end)
    assert mapping.qt_range(6, 7) == (5, 7)  # Emoji uses two UTF-16 code units.
    with pytest.raises(ValueError):
        mapping.canonical_range(6, 7)  # Cursor cannot land inside a surrogate pair.


def test_manual_coding_multiple_themes_repeated_text_and_reopen(tmp_path):
    project = create_project(tmp_path / "project", "Study")
    docs = []
    for name in ["one", "two"]:
        source = tmp_path / f"{name}.txt"
        source.write_text("Café. Same phrase. Same phrase.\nAnother idea.", encoding="utf-8")
        # Byte-identical sources are treated as duplicates, so change the second.
        if name == "two":
            source.write_text("Café. Same phrase. Same phrase.\nAnother idea!", encoding="utf-8")
        docs.append(import_document(project.directory, source).document_id)
    first = create_theme(project.directory, "Access")
    second = create_theme(project.directory, "Trust")
    # Index the second occurrence rather than searching for the first at display time.
    start = len("Café. Same phrase. ")
    end = start + len("Same phrase.")
    coding = add_coding(project.directory, docs[0], start, end, first)
    assert add_coding(project.directory, docs[0], start, end, first) == coding
    add_coding(project.directory, docs[0], start, end, second)
    add_coding(project.directory, docs[1], start, end, first)
    assert [x[1] for x in occurrences(project.directory, first)] == docs
    assert [x[5] for x in occurrences(project.directory, first)] == ["Same phrase."] * 2
    assert [x[2] for x in list_themes(project.directory)] == [2, 1]
    with pytest.raises(ValueError):
        add_coding(project.directory, docs[0], 500, 501, first)
    assert len(occurrences(project.directory, first)) == 2
