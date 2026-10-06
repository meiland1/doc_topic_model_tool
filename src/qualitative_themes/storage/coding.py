"""Persistent researcher decisions and run-scoped model suggestion precedence."""
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from qualitative_themes.storage.database import connect


def create_theme(directory: Path, label: str) -> str:
    label = label.strip()
    if not label:
        raise ValueError("Enter a theme name.")
    theme_id = str(uuid4())
    with connect(directory) as conn:
        if conn.execute("SELECT 1 FROM themes WHERE status = 'active' AND lower(coalesce(user_label, generated_label)) = lower(?)", (label,)).fetchone():
            raise ValueError("A theme with that name already exists.")
        conn.execute("INSERT INTO themes (id, user_label, creation_source) VALUES (?, ?, 'researcher')", (theme_id, label))
        conn.execute("INSERT INTO history_events (id, occurred_at, event_type, details_json) VALUES (?, ?, 'theme_created', ?)",
                     (str(uuid4()), datetime.now(timezone.utc).isoformat(),
                      json.dumps({"theme_id": theme_id, "label": label})))
    return theme_id


def analysis_document_ids(directory: Path, run_id: str) -> set[str]:
    """Selected corpus for a saved run; support older records without metadata."""
    with connect(directory) as conn:
        return analysis_document_ids_from_connection(conn, run_id)


def analysis_document_ids_from_connection(conn, run_id):
    row = conn.execute("SELECT settings_json FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        return set()
    settings = json.loads(row[0])
    if "document_ids" in settings:
        return set(settings["document_ids"])
    return {r[0] for r in conn.execute("SELECT DISTINCT document_id FROM segments WHERE run_id=?", (run_id,))}


def list_themes(directory: Path, run_id: str | None = None) -> list[tuple[str, str, int]]:
    with connect(directory) as conn:
        rows = conn.execute("""
            SELECT t.id, coalesce(t.user_label, t.generated_label, 'Unnamed theme'),
                   count(c.id) FROM themes t LEFT JOIN codings c
                   ON c.theme_id = t.id AND c.state = 'active' AND c.origin = 'researcher'
            WHERE t.status = 'active' GROUP BY t.id
            ORDER BY lower(coalesce(t.user_label, t.generated_label, 'Unnamed theme'))
        """).fetchall()
        if run_id is None:
            manual = {r[0] for r in conn.execute(
                "SELECT id FROM themes WHERE creation_source='researcher' AND status='active'")}
            return [(tid, label, count) for tid, label, count in rows
                    if tid in manual or count > 0]
        visible = {r[0] for r in conn.execute("SELECT theme_id FROM model_suggestions WHERE run_id=?", (run_id,))}
    scoped = [(tid, label, len(effective_assignments(directory, tid, run_id)))
              for tid, label, _ in rows]
    return [(tid, label, count) for tid, label, count in scoped
            if tid in visible or count]


def add_coding(directory: Path, document_id: str, start: int, end: int, theme_id: str) -> str:
    with connect(directory) as conn:
        row = conn.execute("SELECT canonical_text FROM documents WHERE id = ?", (document_id,)).fetchone()
        if row is None or not (0 <= start < end <= len(row[0])):
            raise ValueError("Select a valid range in an imported document.")
        if not row[0][start:end].strip():
            raise ValueError("Select some document text, not only whitespace.")
        if not conn.execute("SELECT 1 FROM themes WHERE id = ? AND status = 'active'", (theme_id,)).fetchone():
            raise ValueError("Select an active theme.")
        existing = conn.execute("SELECT id FROM codings WHERE document_id = ? AND theme_id = ? AND start_char = ? AND end_char = ? AND state = 'active'", (document_id, theme_id, start, end)).fetchone()
        if existing:
            return existing[0]
        coding_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("INSERT INTO codings (id, document_id, theme_id, start_char, end_char, origin, state, created_at) VALUES (?, ?, ?, ?, ?, 'researcher', 'active', ?)",
                     (coding_id, document_id, theme_id, start, end, now))
        conn.execute("INSERT INTO history_events (id, occurred_at, event_type, details_json) VALUES (?, ?, 'coding_added', ?)",
                     (str(uuid4()), now, json.dumps({"coding_id": coding_id, "document_id": document_id, "theme_id": theme_id, "start": start, "end": end})))
    return coding_id


def occurrences(directory: Path, theme_id: str, run_id: str | None = None) -> list[tuple[str, str, str, int, int, str]]:
    if run_id is not None:
        with connect(directory) as conn:
            documents = {row[0]: (row[1], row[2], i) for i, row in enumerate(conn.execute(
                "SELECT id, original_name, canonical_text FROM documents ORDER BY imported_at, rowid"))}
        assignments = effective_assignments(directory, theme_id, run_id)
        assignments.sort(key=lambda r: (documents[r[1]][2], r[2], r[3]))
        return [(aid, doc, documents[doc][0], start, end, documents[doc][1][start:end])
                for aid, doc, start, end, _ in assignments]
    with connect(directory) as conn:
        rows = conn.execute("""
            SELECT c.id, c.document_id, d.original_name, c.start_char, c.end_char,
                   d.canonical_text FROM codings c JOIN documents d ON c.document_id = d.id
            WHERE c.theme_id = ? AND c.state = 'active'
            ORDER BY d.imported_at, d.rowid, c.start_char, c.end_char
        """, (theme_id,)).fetchall()
    return [(coding_id, doc_id, name, start, end, text[start:end])
            for coding_id, doc_id, name, start, end, text in rows]


def _event(conn, kind: str, details: dict) -> None:
    conn.execute("INSERT INTO history_events (id, occurred_at, event_type, details_json) VALUES (?, ?, ?, ?)",
                 (str(uuid4()), datetime.now(timezone.utc).isoformat(), kind, json.dumps(details)))


def _active_theme(conn, theme_id: str) -> tuple[str, str]:
    row = conn.execute("SELECT id, coalesce(user_label, generated_label, 'Unnamed theme') FROM themes WHERE id = ? AND status = 'active'", (theme_id,)).fetchone()
    if row is None:
        raise ValueError("The selected theme is no longer active.")
    return row


def rename_theme(directory: Path, theme_id: str, new_label: str) -> None:
    new_label = new_label.strip()
    if not new_label:
        raise ValueError("Enter a theme name.")
    with connect(directory) as conn:
        _id, old_label = _active_theme(conn, theme_id)
        if conn.execute("SELECT 1 FROM themes WHERE id != ? AND status = 'active' AND lower(coalesce(user_label, generated_label)) = lower(?)", (theme_id, new_label)).fetchone():
            raise ValueError("Another theme already has that name.")
        if old_label == new_label:
            return
        conn.execute("UPDATE themes SET user_label = ? WHERE id = ?", (new_label, theme_id))
        _event(conn, "theme_renamed", {"theme_id": theme_id, "old": old_label, "new": new_label})


def remove_coding(directory: Path, coding_id: str) -> None:
    """Remove one researcher coding, preserving its record and history."""
    with connect(directory) as conn:
        row = conn.execute("SELECT document_id, theme_id, start_char, end_char FROM codings WHERE id = ? AND state = 'active'", (coding_id,)).fetchone()
        if row is None:
            raise ValueError("This coding is no longer active.")
        conn.execute("UPDATE codings SET state = 'removed' WHERE id = ?", (coding_id,))
        _event(conn, "coding_removed", {"coding_id": coding_id, "document_id": row[0],
                                        "theme_id": row[1], "start": row[2], "end": row[3]})


def reject_model_suggestion(directory: Path, document_id: str, start: int, end: int,
                            theme_id: str) -> str:
    """Keep an explicit rejection so the same model suggestion stays hidden."""
    with connect(directory) as conn:
        _active_theme(conn, theme_id)
        row = conn.execute("SELECT canonical_text FROM documents WHERE id = ?", (document_id,)).fetchone()
        if row is None or not 0 <= start < end <= len(row[0]):
            raise ValueError("Invalid model-suggested passage range.")
        existing = conn.execute("SELECT id FROM codings WHERE document_id = ? AND theme_id = ? AND start_char = ? AND end_char = ? AND origin = 'researcher' AND state = 'rejected'", (document_id, theme_id, start, end)).fetchone()
        if existing:
            return existing[0]
        rejection_id = str(uuid4())
        conn.execute("INSERT INTO codings(id, document_id, theme_id, start_char, end_char, origin, state, created_at) VALUES (?, ?, ?, ?, ?, 'researcher', 'rejected', ?)",
                     (rejection_id, document_id, theme_id, start, end, datetime.now(timezone.utc).isoformat()))
        _event(conn, "model_suggestion_rejected", {"rejection_id": rejection_id,
                                                    "document_id": document_id, "theme_id": theme_id,
                                                    "start": start, "end": end})
    return rejection_id


def effective_assignments(directory: Path, theme_id: str, run_id: str | None = None) -> list[tuple[str, str, int, int, str]]:
    """Human active codings plus unsuppressed run suggestions; exact-range dedup.

    Explicit rejections suppress model suggestions at the same range, while a
    subsequent human active addition remains visible. Call with a specific run
    ID; never combine model topic IDs across runs.
    """
    with connect(directory) as conn:
        return effective_assignments_from_connection(conn, theme_id, run_id)


def effective_assignments_from_connection(conn, theme_id, run_id=None):
    """Same precedence rules within a caller-owned read snapshot."""
    manual = conn.execute("SELECT id, document_id, start_char, end_char FROM codings WHERE theme_id = ? AND origin = 'researcher' AND state = 'active'", (theme_id,)).fetchall()
    rejected = {(r[0], r[1], r[2]) for r in conn.execute(
        "SELECT document_id, start_char, end_char FROM codings WHERE theme_id = ? AND state IN ('rejected', 'removed')", (theme_id,))}
    suggestions = []
    if run_id is not None:
        suggestions = conn.execute("""
            SELECT m.id, s.document_id, s.start_char, s.end_char
            FROM model_suggestions m JOIN segments s ON m.segment_id = s.id
            WHERE m.run_id = ? AND m.theme_id = ?
        """, (run_id, theme_id)).fetchall()
    if run_id is not None:
        selected = analysis_document_ids_from_connection(conn, run_id)
        manual = [row for row in manual if row[1] in selected]
    result = {(doc_id, start, end): (coding_id, doc_id, start, end, "researcher")
              for coding_id, doc_id, start, end in manual}
    for suggestion_id, doc_id, start, end in suggestions:
        key = doc_id, start, end
        if key not in rejected and key not in result:
            result[key] = (suggestion_id, doc_id, start, end, "model")
    return sorted(result.values(), key=lambda r: (r[1], r[2], r[3]))


def merge_themes(directory: Path, source_id: str, target_id: str) -> None:
    """Move source work into target; retain duplicate provenance and notes."""
    if source_id == target_id:
        raise ValueError("Choose two different themes to merge.")
    with connect(directory) as conn:
        _, source_label = _active_theme(conn, source_id)
        _, target_label = _active_theme(conn, target_id)
        rows = conn.execute("SELECT id, document_id, start_char, end_char, state FROM codings WHERE theme_id = ? ORDER BY created_at, rowid", (source_id,)).fetchall()
        for coding_id, doc_id, start, end, state in rows:
            duplicate = conn.execute("SELECT id FROM codings WHERE theme_id = ? AND document_id = ? AND start_char = ? AND end_char = ? AND state = ? ORDER BY created_at, rowid LIMIT 1", (target_id, doc_id, start, end, state)).fetchone()
            if duplicate:
                conn.execute("UPDATE notes SET coding_id = ? WHERE coding_id = ?", (duplicate[0], coding_id))
                conn.execute("UPDATE codings SET state = 'merged_duplicate' WHERE id = ?", (coding_id,))
            else:
                conn.execute("UPDATE codings SET theme_id = ? WHERE id = ?", (target_id, coding_id))
        conn.execute("UPDATE notes SET theme_id = ? WHERE theme_id = ?", (target_id, source_id))
        conn.execute("UPDATE model_suggestions SET theme_id = ? WHERE theme_id = ?", (target_id, source_id))
        conn.execute("UPDATE themes SET status = 'merged', merged_into_id = ? WHERE id = ?", (target_id, source_id))
        _event(conn, "themes_merged", {"source_id": source_id, "target_id": target_id,
                                       "source_label": source_label, "target_label": target_label})


def add_note(directory: Path, body: str, *, document_id: str | None = None,
             theme_id: str | None = None, coding_id: str | None = None) -> str:
    body = body.strip()
    if not body or sum(x is not None for x in (document_id, theme_id, coding_id)) != 1:
        raise ValueError("Write a note and choose exactly one document, theme, or passage.")
    note_id = str(uuid4())
    with connect(directory) as conn:
        if theme_id:
            _active_theme(conn, theme_id)
        if document_id and not conn.execute("SELECT 1 FROM documents WHERE id = ?", (document_id,)).fetchone():
            raise ValueError("Document not found.")
        if coding_id and not conn.execute("SELECT 1 FROM codings WHERE id = ? AND state = 'active'", (coding_id,)).fetchone():
            raise ValueError("Active passage coding not found.")
        conn.execute("INSERT INTO notes(id, document_id, theme_id, coding_id, body, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (note_id, document_id, theme_id, coding_id, body, datetime.now(timezone.utc).isoformat()))
        _event(conn, "note_added", {"note_id": note_id, "document_id": document_id,
                                    "theme_id": theme_id, "coding_id": coding_id})
    return note_id


def update_note(directory: Path, note_id: str, body: str) -> None:
    body = body.strip()
    if not body:
        raise ValueError("A note cannot be empty.")
    with connect(directory) as conn:
        old = conn.execute("SELECT body FROM notes WHERE id = ?", (note_id,)).fetchone()
        if old is None:
            raise ValueError("Note not found.")
        if old[0] == body:
            return
        conn.execute("UPDATE notes SET body = ? WHERE id = ?", (body, note_id))
        _event(conn, "note_updated", {"note_id": note_id, "old": old[0], "new": body})


def list_notes(directory: Path, *, document_id: str | None = None,
               theme_id: str | None = None, coding_id: str | None = None) -> list[tuple[str, str]]:
    args = [x for x in (document_id, theme_id, coding_id) if x is not None]
    if len(args) != 1:
        raise ValueError("Choose one note target.")
    field = "document_id" if document_id else "theme_id" if theme_id else "coding_id"
    with connect(directory) as conn:
        return conn.execute(f"SELECT id, body FROM notes WHERE {field} = ? ORDER BY created_at, rowid", args).fetchall()


def themes_for_range(directory: Path, document_id: str, start: int, end: int, run_id: str | None = None) -> list[str]:
    if run_id:
        return [label for tid, label, _ in list_themes(directory, run_id)
                if any(doc == document_id and a == start and b == end
                       for _, doc, a, b, _ in effective_assignments(directory, tid, run_id))]
    with connect(directory) as conn:
        return [row[0] for row in conn.execute("""
            SELECT DISTINCT coalesce(t.user_label, t.generated_label, 'Unnamed theme')
            FROM codings c JOIN themes t ON t.id = c.theme_id
            WHERE c.document_id = ? AND c.start_char = ? AND c.end_char = ?
              AND c.state = 'active' AND t.status = 'active'
            ORDER BY lower(coalesce(t.user_label, t.generated_label, 'Unnamed theme'))
        """, (document_id, start, end))]


def assignment_origin(directory: Path, assignment_id: str) -> str:
    with connect(directory) as conn:
        return "researcher" if conn.execute("SELECT 1 FROM codings WHERE id=?", (assignment_id,)).fetchone() else "model"


def passage_notes(directory: Path, document_id: str, theme_id: str, start: int, end: int):
    """Includes retained notes on previously removed researcher codings."""
    with connect(directory) as conn:
        return conn.execute("""SELECT n.id, n.body FROM notes n JOIN codings c ON c.id=n.coding_id
            WHERE c.document_id=? AND c.theme_id=? AND c.start_char=? AND c.end_char=?
            ORDER BY n.created_at, n.rowid""", (document_id, theme_id, start, end)).fetchall()
