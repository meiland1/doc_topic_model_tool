import json
from types import SimpleNamespace

import pytest

from qualitative_themes.analysis.segment import segment_text, model_text, _validate
from qualitative_themes.analysis.speakers import reliable_transcript_turns, interviewer_ranges
from qualitative_themes.analysis.token_budget import TokenBudget
from qualitative_themes.analysis.topics import prepare_passages, analyze, TopicOutput
from qualitative_themes.analysis.settings import AnalysisSettings
from qualitative_themes.storage.database import create_project, connect
from qualitative_themes.storage.documents import import_document


@pytest.fixture
def budget(monkeypatch):
    result = TokenBudget(SimpleNamespace(encode=lambda text, **kw: [0] + list(text) + [1]),
                         100, {'model': 'test', 'max_tokens': 100})
    monkeypatch.setattr('qualitative_themes.analysis.token_budget.load_token_budget', lambda *a, **k: result)
    return result


@pytest.mark.parametrize('labels', [
    ('Interviewer: ', 'P1: '),
    ('[00:01:22] Interviewer: ', '[00:01:23] P1: '),
    ('[Interviewer] ', '[P1] '),
    ('Speaker 1  00:01:22\n', 'Speaker 2  00:01:23\n'),
])
def test_supported_layouts_never_cross_different_speakers(labels, budget):
    q, a = labels
    text = f'{q}How was transport?\n{a}Yes.\n{q}Tell me more.\n{a}' + 'The clinic is far away. ' * 30
    stats = {}
    spans = segment_text(text, 'Interview passages', token_budget=budget, stats=stats)
    turns = reliable_transcript_turns(text)
    assert len(turns) == 4 and stats['speaker_turns'] == 4
    _validate(text, spans)
    assert all(any(t.start <= s.start_char < s.end_char <= t.end for t in turns) for s in spans)
    assert all(budget.count(model_text(text, s)) <= budget.maximum for s in spans)
    assert any('Yes.' in text[s.start_char:s.end_char] for s in spans)
    assert stats['forced_splits'] == 0  # Long answer splits at sentence boundaries.


def test_adjacent_same_speaker_turns_are_combined_and_preamble_preserved(budget):
    text = 'Interview record\nInterviewer: How?\nP1: By bus.\nP1: It is late.\nInterviewer: Why?\nP1: Traffic.'
    spans = segment_text(text, 'Interview passages', token_budget=budget)
    _validate(text, spans)
    fragments = [text[s.start_char:s.end_char] for s in spans]
    assert fragments[0] == 'Interview record'
    assert 'P1: By bus.\nP1: It is late.' in fragments


def test_ambiguous_speaker_fallback_keeps_unconfirmed_answers(budget, tmp_path):
    text = 'Interviewer: Question one?\nP1: Unique answer.\nInterviewer: Question two?\nP2: Another answer.'
    project = create_project(tmp_path / 'p', 'Ambiguous')
    source = tmp_path / 't.txt'
    source.write_text(text)
    doc = import_document(project.directory, source).document_id
    diagnostics = {}
    passages = prepare_passages(project.directory, 'Interview passages', [doc], True, ['interviewer'],
                                diagnostics=diagnostics)
    assert diagnostics['speaker_fallbacks'] == 1
    assert diagnostics['fallback_document_ids'] == [doc]
    assert diagnostics['oversized'] == 0
    combined = ' '.join(p.text for p in passages)
    assert all(word in combined for word in ['Unique answer', 'Another answer', 'Question one', 'Question two'])


def test_interviewer_exclusion_preview_and_saved_ranges_agree(budget, tmp_path):
    text = 'Interviewer: First question?\nP1: ' + 'Café access is hard. ' * 25 + '\nInterviewer: More?\nP1: Yes, the bus.'
    project = create_project(tmp_path / 'p', 'Interview')
    source = tmp_path / 't.txt'
    source.write_text(text, encoding='utf-8')
    doc = import_document(project.directory, source).document_id
    settings = AnalysisSettings(min_topic_size=2, exclude_interviewer=True, interviewer_labels=('interviewer',), allow_truncation=True)
    preview = prepare_passages(project.directory, 'Interview passages', [doc], True, ['interviewer'])
    assert not any('question' in p.text.lower() or 'More?' in p.text for p in preview)
    excluded = interviewer_ranges(text, ['interviewer'])
    assert all(not any(a < p.end and p.start < b for a, b in excluded) for p in preview)
    def backend(passages, effective, *args):
        assert not effective.allow_truncation
        assert [(p.start, p.end, p.text) for p in passages] == [(p.start, p.end, p.text) for p in preview]
        return TopicOutput([0] * len(passages), {0: [('clinic', 1)]})
    run, _ = analyze(project.directory, 'Interview passages', [doc], settings, backend=backend)
    with connect(project.directory) as conn:
        info = json.loads(conn.execute('SELECT model_info_json FROM analysis_runs WHERE id=?', (run,)).fetchone()[0])
    assert info['segmentation']['method'] == 'interview-turns-v1'
    assert info['segmentation']['speaker_fallbacks'] == 0


def test_unlabelled_document_uses_auto_boundaries(budget):
    text = 'A paragraph about transport. ' * 25 + '\n\nAnother paragraph.'
    stats = {}
    spans = segment_text(text, 'Interview passages', token_budget=budget, stats=stats)
    assert spans == segment_text(text, 'Automatic passages', token_budget=budget)
    assert stats['speaker_fallbacks'] == 1


def test_preview_reports_fallback_and_does_not_grey_retained_turns(budget, tmp_path, qt_test_application):
    from PySide6.QtCore import QEventLoop, QTimer
    from qualitative_themes.gui.analysis_setup import AnalysisSetupDialog
    project = create_project(tmp_path / 'p', 'Mixed labels')
    source = tmp_path / 't.txt'
    source.write_text('Interviewer: How?\nP1: Bus.\nInterviewer: Why?\nP2: Traffic.')
    doc = import_document(project.directory, source).document_id
    dialog = AnalysisSetupDialog(project.directory, [(doc, 't.txt')], unit='Interview passages')
    loop, timer = QEventLoop(), QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    if dialog._inspection:
        dialog._inspection.finished.connect(loop.quit)
        timer.start(5000)
        loop.exec()
    qt_test_application.processEvents()
    assert dialog._inspection is None
    assert '1 document(s) used Auto' in dialog.work.text()
    assert 'Auto fallback: t.txt' in dialog.work.text()
    assert not any(selection.format.fontItalic() for selection in dialog.preview.extraSelections())
    assert not dialog.settings().allow_truncation
    dialog.reject()
