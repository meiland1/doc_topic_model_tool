from contextlib import contextmanager
import sqlite3

import pytest

from qualitative_themes.storage.database import connect, create_project
from qualitative_themes.storage.documents import import_document
from qualitative_themes.analysis.topics import prepare_passages, save_result, TopicOutput, AnalysisCancelled
from qualitative_themes.analysis.settings import AnalysisSettings


def test_cancel_save_rolls_back_releases_lock_and_reuses_document_text(tmp_path, monkeypatch):
    directory = create_project(tmp_path / 'p', 'Cancellation').directory
    source = tmp_path / 'source.txt'
    source.write_text('\n\n'.join(f'Paragraph {i} about transport.' for i in range(30)))
    doc = import_document(directory, source).document_id
    passages = prepare_passages(directory, 'Paragraphs', [doc])
    output = TopicOutput([0] * len(passages), {0: [('transport', 1)]})
    cancel = [False]
    reads = []
    @contextmanager
    def traced(path):
        with connect(path) as conn:
            def trace(sql):
                if sql.startswith('SELECT canonical_text FROM documents'):
                    reads.append(sql)
                if sql.startswith('INSERT INTO segments'):
                    cancel[0] = True
            conn.set_trace_callback(trace)
            yield conn
    monkeypatch.setattr('qualitative_themes.analysis.topics.connect', traced)
    with pytest.raises(AnalysisCancelled):
        save_result(directory, 'Paragraphs', [doc], passages, AnalysisSettings(), output,
                    should_cancel=lambda: cancel[0])
    with connect(directory) as conn:
        conn.execute('BEGIN IMMEDIATE')
        assert conn.execute('SELECT count(*) FROM analysis_runs').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM segments').fetchone()[0] == 0
    reads.clear()
    run = save_result(directory, 'Paragraphs', [doc], passages, AnalysisSettings(), output)
    assert len(reads) == 1
    with connect(directory) as conn:
        assert conn.execute('SELECT count(*) FROM segments WHERE run_id=?', (run,)).fetchone()[0] == 30


def test_busy_database_has_actionable_message_and_recovers(tmp_path):
    directory = create_project(tmp_path / 'p', 'Busy').directory
    with connect(directory) as owner:
        owner.execute('BEGIN IMMEDIATE')
        with pytest.raises(sqlite3.OperationalError, match='close any other copy'):
            with connect(directory) as other:
                other.execute("UPDATE project SET name='Changed'")
    with connect(directory) as conn:
        conn.execute("UPDATE project SET name='Recovered'")
