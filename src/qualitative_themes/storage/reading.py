"""Reading progress: which passages a researcher has reviewed, and their verdicts.

Verdicts: ``relevant`` / ``not_relevant`` from guided reading, ``read`` when a
passage was opened. Coverage reports what share of an analysis's passages has
been reviewed, so blind spots are visible.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from bisect import bisect_left
from qualitative_themes.storage.database import connect

VERDICTS = {"read", "relevant", "not_relevant"}


def mark(directory: Path, document_id: str, start: int, end: int, verdict: str,
         theme_id: str | None = None, run_id: str | None = None) -> None:
    """Record a verdict for one passage. 'read' never overwrites relevant/not_relevant.

    theme_id=None is a general 'opened' mark; guided reading passes the theme.
    """
    if verdict not in VERDICTS:
        raise ValueError("Unknown reading verdict.")
    now = datetime.now(timezone.utc).isoformat()
    with connect(directory) as conn:
        existing = conn.execute("SELECT id, verdict FROM reading_marks WHERE document_id=? AND start_char=? AND end_char=? "
                                "AND theme_id IS ?", (document_id, start, end, theme_id)).fetchone()
        if existing:
            # "read" never downgrades an explicit relevance judgement.
            if verdict != "read" or existing[1] == "read":
                conn.execute("UPDATE reading_marks SET verdict=?, marked_at=?, run_id=coalesce(?, run_id) WHERE id=?",
                             (verdict, now, run_id, existing[0]))
            return
        conn.execute("INSERT INTO reading_marks (id, document_id, start_char, end_char, theme_id, run_id, verdict, marked_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (str(uuid4()), document_id, start, end, theme_id, run_id, verdict, now))


def marks(directory: Path, theme_id: str | None = None) -> dict[tuple[str, int, int], str]:
    """{(document_id, start, end): verdict}; relevance verdicts win over plain 'read'."""
    with connect(directory) as conn:
        if theme_id is None:
            rows = conn.execute("SELECT document_id, start_char, end_char, verdict FROM reading_marks").fetchall()
        else:
            rows = conn.execute("SELECT document_id, start_char, end_char, verdict FROM reading_marks WHERE theme_id=?",
                                (theme_id,)).fetchall()
    result = {}
    for doc, start, end, verdict in rows:
        key = (doc, start, end)
        if result.get(key) in {"relevant", "not_relevant"} and verdict == "read":
            continue
        result[key] = verdict
    return result


def coverage(directory: Path, run_id: str) -> dict:
    """Share of the analysis's passages reviewed, overall and per document.

    A segment counts as reviewed if any mark overlaps it, regardless of theme.
    """
    with connect(directory) as conn:
        segments = conn.execute("""SELECT s.document_id, d.original_name, s.start_char, s.end_char FROM segments s
            JOIN documents d ON d.id=s.document_id WHERE s.run_id=? ORDER BY d.imported_at, d.rowid, s.ordinal""",
                                (run_id,)).fetchall()
        reviewed = conn.execute("SELECT document_id, start_char, end_char FROM reading_marks").fetchall()
    # Per document: marks sorted by start, plus the running max of their ends.
    # A passage [start, end) overlaps some mark (a < end and start < b) exactly
    # when the largest b among marks with a < end is greater than start. This
    # replaces a passages x marks scan that took seconds on large analyses.
    by_doc: dict[str, list[tuple[int, int]]] = {}
    for doc, start, end in reviewed:
        by_doc.setdefault(doc, []).append((start, end))
    index: dict[str, tuple[list[int], list[int]]] = {}
    for doc, spans in by_doc.items():
        spans.sort()
        starts, running_max, best = [], [], None
        for a, b in spans:
            best = b if best is None or b > best else best
            starts.append(a)
            running_max.append(best)
        index[doc] = (starts, running_max)
    per_doc: dict[str, dict] = {}
    total = done = 0
    for doc, name, start, end in segments:
        entry = per_doc.setdefault(doc, {"name": name, "passages": 0, "reviewed": 0})
        entry["passages"] += 1
        total += 1
        starts, running_max = index.get(doc, ((), ()))
        before = bisect_left(starts, end)
        if before and running_max[before - 1] > start:
            entry["reviewed"] += 1
            done += 1
    for entry in per_doc.values():
        entry["share"] = entry["reviewed"] / entry["passages"] if entry["passages"] else 0.0
    return {"passages": total, "reviewed": done, "share": done / total if total else 0.0, "documents": per_doc}
