import pytest

from qualitative_themes.analysis.segment import UNITS, model_text, segment_text
from qualitative_themes.storage.database import create_project
from qualitative_themes.storage.documents import import_document
from qualitative_themes.analysis.topics import AnalysisCancelled, prepare_passages


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
        if unit in {'Automatic passages', 'Interview passages'}:
            continue  # Token-based coverage is exercised with an injected tokenizer in test_token_passages.
        spans = segment_text(text, unit)
        _assert_exact_coverage(text, spans)
        assert all(model_text(text, span) for span in spans)
        assert spans == segment_text(text, unit)
    assert len(segment_text(text, "Paragraphs")) == 3
    assert len(segment_text(text, "Whole document")) == 1
    assert len(segment_text("First.\r\n\r\nSecond.", "Paragraphs")) == 2


def test_prepare_passages_rejects_bad_choices_and_honours_cancel(tmp_path):
    project = create_project(tmp_path / "study", "Study")
    source = tmp_path / "source.txt"
    source.write_text("A complete sentence.\n\nAnother one.", encoding="utf-8")
    doc = import_document(project.directory, source).document_id
    with pytest.raises(ValueError):
        prepare_passages(project.directory, "Short passages", [doc])
    with pytest.raises(ValueError):
        prepare_passages(project.directory, "Paragraphs", ["missing"])
    with pytest.raises(AnalysisCancelled):
        prepare_passages(project.directory, "Paragraphs", [doc], should_cancel=lambda: True)
    assert len(prepare_passages(project.directory, "Paragraphs", [doc])) == 2
    assert len(prepare_passages(project.directory, "Whole document", [doc])) == 1
