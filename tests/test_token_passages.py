import json
from types import SimpleNamespace

import pytest

from qualitative_themes.analysis.segment import segment_text, model_text, _validate
from qualitative_themes.analysis.token_budget import TokenBudget, load_token_budget
from qualitative_themes.analysis.topics import prepare_passages, analyze, TopicOutput, AnalysisCancelled
from qualitative_themes.analysis.settings import AnalysisSettings
from qualitative_themes.storage.database import create_project, connect
from qualitative_themes.storage.documents import import_document


@pytest.fixture
def budget(monkeypatch):
    # Character tokens deliberately differ from word counts and include two specials.
    tokenizer = SimpleNamespace(encode=lambda text, **kwargs: [0] + list(text) + [1])
    result = TokenBudget(tokenizer, 64, {'model': 'test', 'revision': '1', 'max_tokens': 64})
    monkeypatch.setattr('qualitative_themes.analysis.token_budget.load_token_budget', lambda *a, **k: result)
    return result


@pytest.fixture
def corpus(tmp_path):
    project = create_project(tmp_path / 'p', 'Token test')
    source = tmp_path / 'text.txt'
    source.write_text('Café transport matters. ' * 30 + '\n\n' + 'The garden needs water. ' * 20, encoding='utf-8')
    doc = import_document(project.directory, source).document_id
    return project.directory, doc


@pytest.mark.parametrize('text', [
    'Café 😀 transport. ' * 100,
    'word ' * 300,
    '漢字😀' * 300,
    'Repeated sentence.\r\n\r\nRepeated sentence.\fFinal thought.',
])
def test_auto_exact_coverage_and_no_oversize(text, budget):
    stats = {}
    spans = segment_text(text, 'Automatic passages', token_budget=budget, stats=stats)
    _validate(text, spans)
    assert all(budget.count(model_text(text, span)) <= budget.maximum for span in spans)
    assert spans == segment_text(text, 'Automatic passages', token_budget=budget)
    assert spans


def test_short_sentences_and_paragraphs_are_not_split(budget):
    text = 'A sentence. Another sentence.\n\nA new paragraph.'
    stats = {}
    spans = segment_text(text, 'Automatic passages', token_budget=budget, stats=stats)
    assert [text[s.start_char:s.end_char] for s in spans] == ['A sentence. Another sentence.', 'A new paragraph.']
    assert stats['forced_splits'] == 0


def test_exclusions_are_counted_as_actual_model_input(budget):
    text = 'Interviewer: ' + 'prompt ' * 200 + '\nParticipant: A brief answer.'
    excluded = [(0, text.index('Participant:'))]
    spans = segment_text(text, 'Automatic passages', token_budget=budget, excluded=excluded)
    _validate(text, spans)
    assert len(spans) == 1
    assert model_text(text, spans[0], excluded) == 'A brief answer.'


def test_preview_pipeline_matches_saved_analysis_and_keeps_coding(corpus, budget):
    from qualitative_themes.storage.coding import create_theme, add_coding
    directory, doc = corpus
    theme = create_theme(directory, 'Researcher code')
    add_coding(directory, doc, 0, 10, theme)
    diagnostics = {}
    preview = prepare_passages(directory, 'Automatic passages', [doc], diagnostics=diagnostics)
    assert diagnostics['oversized'] == 0 and diagnostics['max_tokens'] == 64
    def backend(passages, *args):
        assert [(p.start, p.end, p.text) for p in passages] == [(p.start, p.end, p.text) for p in preview]
        return TopicOutput([0] * len(passages), {0: [('transport', 1)]})
    run, _ = analyze(directory, 'Automatic passages', [doc], backend=backend)
    with connect(directory) as conn:
        info = json.loads(conn.execute('SELECT model_info_json FROM analysis_runs WHERE id=?', (run,)).fetchone()[0])
        assert info['segmentation']['method'] == 'automatic-token-v1'
        assert info['segmentation']['oversized'] == 0
        assert conn.execute('SELECT start_char,end_char FROM codings WHERE theme_id=?', (theme,)).fetchall() == [(0, 10)]


def test_whole_document_offsets_unchanged_and_oversize_reported(corpus, budget):
    directory, doc = corpus
    old = prepare_passages(directory, 'Whole document', [doc])
    info = {}
    measured = prepare_passages(directory, 'Whole document', [doc], diagnostics=info)
    assert [(p.start, p.end) for p in old] == [(p.start, p.end) for p in measured]
    assert info['oversized'] == 1 and info['method'] == 'whole-document-v1'


def test_automatic_cancellation_leaves_no_partial_run(corpus, budget):
    directory, doc = corpus
    with pytest.raises(AnalysisCancelled):
        prepare_passages(directory, 'Automatic passages', [doc], should_cancel=lambda: True)
    with connect(directory) as conn:
        assert conn.execute('SELECT count(*) FROM analysis_runs').fetchone()[0] == 0


def test_local_tokenizer_and_limit_without_loading_encoder():
    from qualitative_themes.runtime.paths import bundled_embedding_model
    if bundled_embedding_model() is None:
        pytest.skip('Bundled tokenizer not present in this checkout')
    pytest.importorskip('transformers')
    actual = load_token_budget()
    text = 'Long medical terminology and transport difficulties. ' * 250
    spans = segment_text(text, 'Automatic passages', token_budget=actual)
    _validate(text, spans)
    assert actual.maximum == 256
    assert all(actual.count(model_text(text, span)) <= actual.maximum for span in spans)


def test_setup_requires_consent_for_oversized_passages(corpus, budget, qt_test_application):
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QDialogButtonBox
    from qualitative_themes.gui.analysis_setup import AnalysisSetupDialog
    directory, doc = corpus
    dialog = AnalysisSetupDialog(directory, [(doc, 'text')], unit='Whole document', settings=AnalysisSettings(min_topic_size=2))
    def wait():
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        if dialog._inspection:
            dialog._inspection.finished.connect(loop.quit)
            timer.start(5000)
            loop.exec()
        qt_test_application.processEvents()
        assert dialog._inspection is None
    wait()
    assert dialog.summary['segmentation']['oversized'] == 1
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    dialog.allow_truncation.setChecked(True)
    assert dialog.settings().allow_truncation
    dialog.unit.setCurrentText('Automatic passages')
    wait()
    assert dialog.summary['segmentation']['oversized'] == 0
    assert not dialog.settings().allow_truncation
    assert dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    dialog.reject()


def test_runtime_guard_blocks_embedding_until_explicit_consent(budget, tmp_path, monkeypatch):
    from dataclasses import replace
    pytest.importorskip('bertopic')
    from qualitative_themes.analysis.topics import fit_bertopic
    calls = []
    def encode(*args, **kwargs):
        calls.append(True)
        raise RuntimeError('Embedding stage reached')
    encoder = SimpleNamespace(tokenizer=budget.tokenizer, max_seq_length=budget.maximum,
                              encode=encode, get_sentence_embedding_dimension=lambda: 3)
    monkeypatch.setattr('qualitative_themes.analysis.encoder.load_encoder', lambda *args: encoder)
    passages = [SimpleNamespace(id=str(i), text='Medical transport barriers ' * 20) for i in range(5)]
    settings = AnalysisSettings()
    with pytest.raises(ValueError, match='exceed the model token limit'):
        fit_bertopic(passages, settings, tmp_path, lambda _: None, None)
    assert not calls
    with pytest.raises(RuntimeError, match='Embedding stage reached'):
        fit_bertopic(passages, replace(settings, allow_truncation=True), tmp_path, lambda _: None, None)
    assert calls == [True]


def test_missing_tokenizer_never_downloads_or_guesses(monkeypatch):
    monkeypatch.setattr('qualitative_themes.analysis.token_budget.embedding_source', lambda *args: None)
    with pytest.raises(ValueError, match='Check & repair'):
        load_token_budget()
