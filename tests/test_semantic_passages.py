import json
from types import SimpleNamespace

import numpy as np
import pytest

from qualitative_themes.analysis.segment import segment_text, model_text, _validate
from qualitative_themes.analysis.token_budget import TokenBudget
from qualitative_themes.analysis.settings import AnalysisSettings
from qualitative_themes.analysis.topics import prepare_passages, analyze, TopicOutput, AnalysisCancelled
from qualitative_themes.analysis.semantic import sentence_vectors
from qualitative_themes.storage.database import create_project, connect
from qualitative_themes.storage.documents import import_document


@pytest.fixture
def budget(monkeypatch):
    result = TokenBudget(SimpleNamespace(encode=lambda text, **kw: [0] + list(text) + [1]), 80, {})
    monkeypatch.setattr('qualitative_themes.analysis.token_budget.load_token_budget', lambda *a, **k: result)
    return result


def vectors(texts, *args, **kwargs):
    return [[1, 0] if 'garden' in t else [0, 1] for t in texts]


def test_boundaries_and_sensitivity(budget):
    text = 'Café garden. Green garden. Buses arrive. Buses leave.'
    stats = {}
    spans = segment_text(text, 'Semantic passages', token_budget=budget, semantic_provider=vectors, stats=stats,
                         semantic_min_tokens=0)
    assert [text[s.start_char:s.end_char] for s in spans] == ['Café garden. Green garden.', 'Buses arrive. Buses leave.']
    assert stats['semantic_breaks'] == 1
    assert len(segment_text(text, 'Semantic passages', token_budget=budget, semantic_provider=vectors, semantic_threshold=0)) == 1


def test_token_limits_unicode_and_exclusions(budget):
    text = '漢字😀 garden. ' * 80
    spans = segment_text(text, 'Semantic passages', token_budget=budget, semantic_provider=vectors)
    _validate(text, spans)
    assert all(budget.count(model_text(text, span)) <= budget.maximum for span in spans)
    assert len(spans) > 1
    spans = segment_text(text, 'Semantic passages', token_budget=budget, excluded=[(0, len(text))],
                         semantic_provider=lambda _: pytest.fail('Excluded text must not be embedded'))
    _validate(text, spans)


@pytest.mark.parametrize('bad', [[], [[float('nan'), 1]], [[0, 0]], [[1, 0], [1]]])
def test_invalid_vectors_rejected(budget, bad):
    with pytest.raises(ValueError):
        segment_text('Garden grows. Buses arrive.', 'Semantic passages', token_budget=budget,
                     semantic_provider=lambda _: bad)


def test_cancellation(budget):
    with pytest.raises(AnalysisCancelled):
        segment_text('Garden grows.', 'Semantic passages', token_budget=budget,
                     semantic_provider=vectors, should_cancel=lambda: True)


def test_minimum_target_prevents_sentence_explosion():
    word_budget = TokenBudget(SimpleNamespace(encode=lambda text, **kw: [0] + text.split() + [1]), 256, {})
    text = ' '.join(('The garden needs water and regular care today.' if i % 2 else
                     'The bus route changed and access became difficult.') for i in range(420))
    baseline = segment_text(text, 'Semantic passages', token_budget=word_budget, semantic_provider=vectors,
                            semantic_min_tokens=0)
    stats = {}
    guarded = segment_text(text, 'Semantic passages', token_budget=word_budget, semantic_provider=vectors, stats=stats)
    assert len(baseline) == 420  # 44 such documents would produce 18,480 passages.
    assert len(guarded) < len(baseline) / 5
    assert stats['suppressed_semantic_breaks'] > 0
    _validate(text, guarded)
    assert all(word_budget.count(model_text(text, span)) <= 256 for span in guarded)
    assert all(word_budget.count(model_text(text, span)) >= 128 for span in guarded[:-1])


def test_short_tail_merged_without_exceeding_limit(budget):
    text = 'Green garden. Buses leave.'
    stats = {}
    spans = segment_text(text, 'Semantic passages', token_budget=budget, semantic_provider=vectors,
                         semantic_min_tokens=15, stats=stats)
    assert len(spans) == 1
    assert stats['semantic_breaks'] == 0


def test_cache_reuse_corruption_and_revision(tmp_path, monkeypatch):
    calls = []
    encoder = SimpleNamespace(max_seq_length=80, get_sentence_embedding_dimension=lambda: 2)
    monkeypatch.setattr('qualitative_themes.analysis.encoder.load_encoder', lambda *a: encoder)
    def encode(_encoder, texts, *a, **kw):
        calls.extend(texts)
        return zip(texts, vectors(texts))
    monkeypatch.setattr('qualitative_themes.analysis.encoder.encode_texts', encode)
    sentence_vectors(['garden', 'garden'], tmp_path)
    assert calls == ['garden']
    stats = {}
    sentence_vectors(['garden'], tmp_path, stats=stats)
    assert calls == ['garden'] and stats['sentence_vectors_reused'] == 1
    next(tmp_path.rglob('*.npy')).write_bytes(b'broken')
    sentence_vectors(['garden'], tmp_path)
    assert len(calls) == 2
    sentence_vectors(['garden'], tmp_path, AnalysisSettings(embedding_revision='different'))
    assert len(calls) == 3


def test_preview_and_saved_analysis_agree(budget, tmp_path, monkeypatch):
    monkeypatch.setattr('qualitative_themes.analysis.semantic.sentence_vectors', vectors)
    directory = create_project(tmp_path / 'p', 'Semantic test').directory
    source = tmp_path / 'source.txt'
    source.write_text('Café garden. Green garden. Buses arrive. Buses leave. ' * 6, encoding='utf-8')
    doc = import_document(directory, source).document_id
    from qualitative_themes.storage.coding import create_theme, add_coding
    theme = create_theme(directory, 'Existing code')
    add_coding(directory, doc, 0, 10, theme)
    settings = AnalysisSettings(min_topic_size=2, semantic_threshold=.7, allow_truncation=True)
    preview = prepare_passages(directory, 'Semantic passages', [doc], settings=settings)
    def backend(passages, effective, *args):
        assert not effective.allow_truncation
        assert [(p.start, p.end, p.text) for p in passages] == [(p.start, p.end, p.text) for p in preview]
        return TopicOutput([0] * len(passages), {0: [('garden', 1)]})
    run, _ = analyze(directory, 'Semantic passages', [doc], settings, backend=backend)
    with connect(directory) as conn:
        detail = json.loads(conn.execute('SELECT model_info_json FROM analysis_runs WHERE id=?', (run,)).fetchone()[0])['segmentation']
        assert detail['experimental'] and detail['semantic_threshold'] == .7
        assert detail['oversized'] == 0
        assert conn.execute('SELECT start_char,end_char FROM codings WHERE theme_id=?', (theme,)).fetchall() == [(0, 10)]


def test_preview_sensitivity_refresh(budget, tmp_path, monkeypatch, qt_test_application):
    from PySide6.QtCore import QEventLoop, QTimer
    from qualitative_themes.gui.analysis_setup import AnalysisSetupDialog
    monkeypatch.setattr('qualitative_themes.analysis.semantic.sentence_vectors', vectors)
    directory = create_project(tmp_path / 'p', 'Preview').directory
    source = tmp_path / 'source.txt'
    source.write_text('Green garden. Buses arrive. ' * 8)
    doc = import_document(directory, source).document_id
    dialog = AnalysisSetupDialog(directory, [(doc, 'source.txt')], unit='Semantic passages')
    def wait_preview():
        loop, timer = QEventLoop(), QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: loop.quit() if dialog._inspection is None else None)
        timer.start()
        QTimer.singleShot(5000, loop.quit)
        loop.exec()
        qt_test_application.processEvents()
        assert dialog._inspection is None
    wait_preview()
    old = dialog.summary.copy()
    assert 'Experimental:' in dialog.work.text()
    dialog.semantic_threshold.setValue(.25)
    dialog.inspected('Semantic passages', old)
    assert dialog.summary is None
    wait_preview()
    assert dialog.summary['segmentation']['semantic_threshold'] == .25
    old = dialog.summary.copy()
    dialog.semantic_min_tokens.setValue(32)
    dialog.inspected('Semantic passages', old)
    assert dialog.summary is None
    wait_preview()
    assert dialog.summary['segmentation']['semantic_min_tokens'] == 32
    assert dialog.settings().semantic_min_tokens == 32
    assert not dialog.settings().allow_truncation
    dialog.restore_defaults()
    assert dialog.unit.currentText() == 'Automatic passages'
    dialog.reject()
