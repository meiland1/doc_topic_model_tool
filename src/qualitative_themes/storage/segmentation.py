"""Store a corpus segmentation snapshot as an analysis-run precursor.

Flow: create_segmentation_run() cuts every selected document into passages
and saves them as an analysis_runs row (status 'segmented') plus segments
rows. analysis.topics then fits a model on that run's segments and moves
the run to 'completed'.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from qualitative_themes.analysis.segment import TOKEN_SAFE_UNITS, UNITS, segment_text
from qualitative_themes.storage.database import connect


class SegmentationCancelled(Exception):
    """Raised when should_cancel() returns True; nothing has been written yet."""
    pass


def create_segmentation_run(directory: Path, unit: str, document_ids: list[str] | None = None,
                            should_cancel=None) -> tuple[str, int]:
    """Cut documents into passages and save them as a new run.

    Pre: `unit` is a key of analysis.segment.UNITS; `document_ids` (if given)
    are distinct ids of 'ready' documents. None means every ready document.
    `should_cancel` is polled between documents and inside slow passage types.
    Post: returns (run_id, passage_count). The run and all its segments are
    inserted in one transaction at the end, so a cancel or error leaves no
    partial run. Existing runs and researcher work are never modified.

    Semantic and interview passages can load the embedding model/tokenizer,
    so call this from a worker thread, not the GUI thread.
    """
    if unit not in UNITS:
        raise ValueError("Choose a supported passage size.")
    with connect(directory) as conn:
        rows = conn.execute("SELECT id, canonical_text FROM documents WHERE extraction_status = 'ready' ORDER BY imported_at, rowid").fetchall()
    if document_ids is not None:
        if not document_ids or len(set(document_ids)) != len(document_ids):
            raise ValueError("Select one or more distinct documents.")
        selected = set(document_ids)
        rows = [row for row in rows if row[0] in selected]
        if len(rows) != len(selected):
            raise ValueError("A selected document was not found in this project.")
    if not rows:
        raise ValueError("Import at least one readable document first.")
    pending = []
    def cancelled():
        if should_cancel and should_cancel():
            raise SegmentationCancelled()
        return False
    budget = None
    diagnostics = {'forced_splits': 0}
    if unit == 'Interview passages':
        diagnostics.update({'speaker_turns': 0, 'speaker_fallbacks': 0, 'fallback_document_ids': []})
    if unit in TOKEN_SAFE_UNITS:
        from qualitative_themes.analysis.token_budget import load_token_budget
        budget = load_token_budget(directory=directory)
    provider = None
    if unit == 'Semantic passages':
        from qualitative_themes.analysis.semantic import sentence_vectors
        provider = lambda texts: sentence_vectors(texts, directory, should_cancel=cancelled, stats=diagnostics)
    for doc_id, text in rows:
        if should_cancel and should_cancel():
            raise SegmentationCancelled()
        fallbacks_before = diagnostics.get('speaker_fallbacks', 0)
        spans = segment_text(text, unit, token_budget=budget, stats=diagnostics, semantic_provider=provider,
                             should_cancel=cancelled)
        if diagnostics.get('speaker_fallbacks', 0) > fallbacks_before:
            diagnostics['fallback_document_ids'].append(doc_id)
        if not spans:
            raise ValueError("A selected document contains no passages.")
        pending.extend((str(uuid4()), doc_id, ordinal, span.start_char, span.end_char,
                        hashlib.sha256(text[span.start_char:span.end_char].encode("utf-8")).hexdigest())
                       for ordinal, span in enumerate(spans))
    if should_cancel and should_cancel():
        raise SegmentationCancelled()
    run_id = str(uuid4())
    settings = {"analysis_unit": unit, "target_max_words": UNITS[unit],
                "document_ids": [row[0] for row in rows], "overlap_words": 0}
    if budget:
        settings['segmentation'] = {'method': ('semantic-adjacent-min-v2' if unit == 'Semantic passages' else
                                              'interview-turns-v1' if unit == 'Interview passages' else 'automatic-token-v1'),
                                    'tokenizer': budget.identity, **diagnostics}
    # All rows become visible together; old runs and human work remain untouched.
    with connect(directory) as conn:
        conn.execute("INSERT INTO analysis_runs (id, started_at, completed_at, status, settings_json) VALUES (?, ?, ?, ?, ?)",
                     (run_id, datetime.now(timezone.utc).isoformat(),
                      datetime.now(timezone.utc).isoformat(), "segmented", json.dumps(settings)))
        conn.executemany("INSERT INTO segments (id, document_id, run_id, ordinal, start_char, end_char, text_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         [(seg_id, doc_id, run_id, ordinal, start, end, digest)
                          for seg_id, doc_id, ordinal, start, end, digest in pending])
    return run_id, len(pending)


def list_segments(directory: Path, run_id: str) -> list[tuple[str, str, int, int, int, str]]:
    """Rows are (document_id, document_name, ordinal, start, end, text) in reading order."""
    with connect(directory) as conn:
        return conn.execute("""
            SELECT s.document_id, d.original_name, s.ordinal, s.start_char, s.end_char,
                   substr(d.canonical_text, s.start_char + 1, s.end_char - s.start_char)
            FROM segments s JOIN documents d ON d.id = s.document_id
            WHERE s.run_id = ? ORDER BY d.imported_at, d.rowid, s.ordinal
        """, (run_id,)).fetchall()
