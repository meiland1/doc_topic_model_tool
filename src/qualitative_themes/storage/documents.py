"""Import source copies and snapshots into a project with rollback on failure."""
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from shutil import copyfileobj
from uuid import uuid4

from qualitative_themes.importers.extract import extract_document
from qualitative_themes.storage.database import connect


@dataclass(frozen=True)
class ImportOutcome:
    filename: str
    status: str  # imported, restored, duplicate, failed
    document_id: str | None = None
    message: str = ""
    warnings: tuple[str, ...] = ()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def import_document(directory: Path, source: Path) -> ImportOutcome:
    source = Path(source)
    if source.suffix.lower() not in {".pdf", ".docx", ".txt"}:
        return ImportOutcome(source.name, "failed", message="Choose a PDF, DOCX, or TXT file.")
    copied: Path | None = None
    try:
        digest = _sha256(source)
        with connect(directory) as conn:
            duplicate = conn.execute("SELECT id, original_name, extraction_status FROM documents WHERE source_hash = ?", (digest,)).fetchone()
        if duplicate:
            if duplicate[2] == "removed":
                restore_documents(directory, [duplicate[0]])
                return ImportOutcome(source.name, "restored", duplicate[0],
                                     f"Restored {duplicate[1]} with its saved text and research decisions.")
            return ImportOutcome(source.name, "duplicate", duplicate[0],
                                 f"Already imported as {duplicate[1]}.")
        extracted = extract_document(source)
        document_id = str(uuid4())
        relative = Path("sources") / f"{document_id}{source.suffix.lower()}"
        copied = Path(directory) / relative
        copied.parent.mkdir(exist_ok=True)
        with source.open("rb") as inp, copied.open("xb") as out:
            copyfileobj(inp, out)
        # Detect source modifications during extraction/copy rather than saving mismatched text.
        if _sha256(copied) != digest:
            raise ValueError("The source file changed while it was being imported. Try again.")
        now = datetime.now(timezone.utc).isoformat()
        with connect(directory) as conn:
            conn.execute("INSERT INTO documents (id, original_name, source_path, source_hash, canonical_text, extraction_status, imported_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (document_id, source.name, relative.as_posix(), digest, extracted.text, "ready", now))
            conn.executemany("INSERT INTO document_pages (id, document_id, page_number, paragraph_number, start_char, end_char) VALUES (?, ?, ?, ?, ?, ?)",
                             [(str(uuid4()), document_id, span.page_number, span.paragraph_number,
                               span.start_char, span.end_char) for span in extracted.spans])
        return ImportOutcome(source.name, "imported", document_id,
                             f"{len(extracted.text):,} characters extracted.", extracted.warnings)
    except Exception as exc:
        # The import boundary must return a per-file failure even for malformed
        # PDF/DOCX content, without stopping the remaining files in a batch.
        if copied is not None:
            copied.unlink(missing_ok=True)
        return ImportOutcome(source.name, "failed", message=str(exc))


def list_documents(directory: Path, include_removed: bool = False) -> list[tuple[str, str, str]]:
    with connect(directory) as conn:
        rows = conn.execute("SELECT id, original_name, extraction_status FROM documents ORDER BY imported_at, rowid").fetchall()
    return rows if include_removed else [row for row in rows if row[2] != "removed"]


def _change_availability(directory: Path, document_ids: list[str], removing: bool) -> int:
    """A reversible batch change; never delete source snapshots or research rows."""
    if not document_ids or len(set(document_ids)) != len(document_ids):
        raise ValueError("Select one or more distinct documents.")
    expected, target = ("ready", "removed") if removing else ("removed", "ready")
    with connect(directory) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = []
        for document_id in document_ids:
            row = conn.execute("SELECT id,original_name,extraction_status FROM documents WHERE id=?", (document_id,)).fetchone()
            if row is None or row[2] != expected:
                raise ValueError("A selected document is missing or its availability changed. Refresh and try again.")
            rows.append(row)
        conn.executemany("UPDATE documents SET extraction_status=? WHERE id=?", [(target, row[0]) for row in rows])
        conn.execute("INSERT INTO history_events VALUES (?, ?, ?, ?)",
                     (str(uuid4()), datetime.now(timezone.utc).isoformat(),
                      "documents_removed" if removing else "documents_restored",
                      json.dumps({"documents": [{"document_id": row[0], "name": row[1]} for row in rows],
                                  "policy": "Reversible corpus exclusion; preserve sources, analyses, coding, and notes."})))
    return len(rows)


def remove_documents(directory: Path, document_ids: list[str]) -> int:
    return _change_availability(directory, document_ids, removing=True)


def restore_documents(directory: Path, document_ids: list[str]) -> int:
    return _change_availability(directory, document_ids, removing=False)


def get_document(directory: Path, document_id: str) -> tuple[str, str, list[tuple[int | None, int | None, int, int]]]:
    with connect(directory) as conn:
        row = conn.execute("SELECT original_name, canonical_text FROM documents WHERE id = ?", (document_id,)).fetchone()
        if row is None:
            raise KeyError(document_id)
        spans = conn.execute("SELECT page_number, paragraph_number, start_char, end_char FROM document_pages WHERE document_id = ? ORDER BY start_char", (document_id,)).fetchall()
    return row[0], row[1], spans
