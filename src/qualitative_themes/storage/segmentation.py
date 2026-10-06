"""Store a corpus segmentation snapshot as an analysis-run precursor."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from qualitative_themes.analysis.segment import UNITS, segment_text
from qualitative_themes.storage.database import connect


class SegmentationCancelled(Exception):
    pass


def create_segmentation_run(directory: Path, unit: str, document_ids: list[str] | None = None,
                            should_cancel=None) -> tuple[str, int]:
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
    for doc_id, text in rows:
        if should_cancel and should_cancel():
            raise SegmentationCancelled()
        spans = segment_text(text, unit)
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
    with connect(directory) as conn:
        return conn.execute("""
            SELECT s.document_id, d.original_name, s.ordinal, s.start_char, s.end_char,
                   substr(d.canonical_text, s.start_char + 1, s.end_char - s.start_char)
            FROM segments s JOIN documents d ON d.id = s.document_id
            WHERE s.run_id = ? ORDER BY d.imported_at, d.rowid, s.ordinal
        """, (run_id,)).fetchall()
