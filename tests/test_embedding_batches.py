from types import SimpleNamespace

import pytest

from qualitative_themes.analysis.encoder import encode_texts
from qualitative_themes.analysis.settings import AnalysisSettings
from qualitative_themes.analysis.topics import AnalysisCancelled


def test_honors_batch_and_real_progress_messages(qt_test_application):
    from qualitative_themes.gui.task_progress import TaskProgress
    calls = []
    def encode(texts, **kwargs):
        calls.append(kwargs['batch_size'])
        return [[i] for i in range(len(texts))]
    panel = TaskProgress()
    panel.begin('Analysis')
    result = list(encode_texts(SimpleNamespace(encode=encode), ['a'] * 1100,
                              AnalysisSettings(batch_size=7), panel.update_stage))
    assert calls == [7, 7] and len(result) == 1100
    assert panel.bar.maximum() == 1100 and panel.bar.value() == 1100
    panel.finish()
    panel.close()


def test_memory_retry_preserves_order_and_completed_chunks(monkeypatch):
    monkeypatch.setattr('qualitative_themes.analysis.encoder.ENCODE_CHUNK', 4)
    calls = []
    def encode(texts, **kwargs):
        calls.append((list(texts), kwargs['batch_size']))
        if texts[0] == '4' and len(texts) > 1:
            raise MemoryError('allocation failed')
        return [[int(t)] for t in texts]
    texts = [str(i) for i in range(9)]
    result = list(encode_texts(SimpleNamespace(encode=encode), texts, AnalysisSettings(batch_size=4)))
    assert [t for t, _ in result] == texts
    assert [v for _, v in result] == [[i] for i in range(9)]
    assert sum('0' in chunk for chunk, _ in calls) == 1
    assert [batch for _, batch in calls[:4]] == [4, 4, 2, 1]


@pytest.mark.parametrize('error', [RuntimeError('CUDA out of memory.'), MemoryError()])
def test_memory_failure_stops_at_one(error):
    calls = []
    def encode(texts, **kw):
        calls.append(kw['batch_size'])
        raise error
    with pytest.raises(ValueError, match='one passage'):
        list(encode_texts(SimpleNamespace(encode=encode), ['a'], AnalysisSettings(batch_size=4)))
    assert calls == [4, 2, 1]


def test_unrelated_failure_is_not_retried():
    calls = []
    def encode(*args, **kw):
        calls.append(1)
        raise RuntimeError('invalid tensor shape')
    with pytest.raises(RuntimeError, match='tensor shape'):
        list(encode_texts(SimpleNamespace(encode=encode), ['a'], AnalysisSettings()))
    assert len(calls) == 1


def test_cancellation_after_computation_and_before_retry():
    for fail in (False, True):
        cancelled = [False]
        def encode(*args, **kwargs):
            cancelled[0] = True
            if fail:
                raise MemoryError()
            return [[1]]
        with pytest.raises(AnalysisCancelled):
            list(encode_texts(SimpleNamespace(encode=encode), ['a'], AnalysisSettings(),
                              should_cancel=lambda: cancelled[0]))


def test_incomplete_result_rejected():
    with pytest.raises(ValueError, match='incomplete'):
        list(encode_texts(SimpleNamespace(encode=lambda *a, **kw: []), ['a'], AnalysisSettings()))
