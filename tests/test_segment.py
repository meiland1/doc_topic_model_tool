import json
from pathlib import Path

import pytest

from qualitative_themes.analysis.segment import UNITS, model_text, segment_text
from qualitative_themes.storage.database import connect, create_project
from qualitative_themes.storage.documents import import_document
from qualitative_themes.storage.segmentation import (SegmentationCancelled,
                                                      create_segmentation_run,
                                                      list_segments)


def _assert_exact_coverage(text, spans):
    previous = 0
    for span in spans:
        assert 0 <= previous <= span.start_char < span.end_char <= len(text)
        assert not text[previous:span.start_char].strip()
        assert text[span.start_char:span.end_char].strip()
        previous = span.end_char
    assert not text[previous:].strip()


def test_all_units_cover_exact_unicode_and_page_boundaries():
    text = ("  Café access is difficult. Transport matters!\r\n"
            "An appointment was missed.\n\n"
            "The phrase repeats. The phrase repeats.\n\f\n"
            "Résumé services changed in year 2026.\n\n")
    for unit in UNITS:
        spans = segment_text(text, unit)
        _assert_exact_coverage(text, spans)
        assert all(model_text(text, span) for span in spans)
        assert spans == segment_text(text, unit)
    assert len(segment_text(text, "Paragraphs")) == 3
    assert len(segment_text(text, "Whole document")) == 1
    assert len(segment_text("First.\r\n\r\nSecond.", "Paragraphs")) == 2


def test_long_paragraph_splits_without_loss():
    text = "Start. " + " ".join(f"word{i}" for i in range(520)) + ". End."
    for unit, maximum in (("Short passages", 125), ("Standard passages", 250),
                          ("Long passages", 450)):
        spans = segment_text(text, unit)
        _assert_exact_coverage(text, spans)
        assert len(spans) > 1
        assert all(len(text[s.start_char:s.end_char].split()) <= maximum for s in spans)


def test_persistent_run_and_reopen_with_multiple_documents(tmp_path):
    project = create_project(tmp_path / "study", "Study")
    sources = []
    for n in range(2):
        source = tmp_path / f"source{n}.txt"
        source.write_text(f"Unique first thought {n}.\n\nSecond idea relates to costs.", encoding="utf-8")
        sources.append(import_document(project.directory, source).document_id)
    run, count = create_segmentation_run(project.directory, "Paragraphs")
    assert count == 4
    records = list_segments(project.directory, run)
    assert set(r[0] for r in records) == set(sources)
    assert all(r[5].strip() for r in records)
    with connect(project.directory) as conn:
        settings = json.loads(conn.execute(
            "SELECT settings_json FROM analysis_runs WHERE id = ?", (run,)).fetchone()[0])
        assert settings["analysis_unit"] == "Paragraphs"
        assert set(settings["document_ids"]) == set(sources)
    second_run, _ = create_segmentation_run(project.directory, "Whole document", sources[:1])
    assert second_run != run
    assert len(list_segments(project.directory, run)) == 4
    assert len(list_segments(project.directory, second_run)) == 1


def test_invalid_selection_and_cancellation_do_not_write(tmp_path):
    project = create_project(tmp_path / "study", "Study")
    source = tmp_path / "source.txt"
    source.write_text("A complete sentence.", encoding="utf-8")
    import_document(project.directory, source)
    with pytest.raises(ValueError):
        create_segmentation_run(project.directory, "Missing unit")
    with pytest.raises(ValueError):
        create_segmentation_run(project.directory, "Paragraphs", ["missing"])
    with pytest.raises(SegmentationCancelled):
        create_segmentation_run(project.directory, "Paragraphs", should_cancel=lambda: True)
    with connect(project.directory) as conn:
        assert conn.execute("SELECT count(*) FROM analysis_runs").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM segments").fetchone()[0] == 0
