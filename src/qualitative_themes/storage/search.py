"""Search across every document (and optionally comments), with exact source offsets.

SQLite's full-text index (FTS5) narrows the search to documents containing
the words; a precise scan then finds every match and its offsets. Without FTS5
the scan covers all documents. Matches start at a word boundary, so "insur" finds
"insurance". Results carry the themes each match falls in.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from qualitative_themes.storage.database import connect

CONTEXT = 70


@dataclass
class Hit:
    """One search result. `before`/`match`/`after` are whitespace-collapsed for display;
    `start`/`end` are canonical offsets for jumping to the match.
    """
    document_id: str
    document: str
    start: int
    end: int
    before: str
    match: str
    after: str
    page: int | None = None
    themes: list = field(default_factory=list)
    kind: str = "text"  # or "comment"
    comment: str = ""


def _fts_candidates(conn, query: str) -> set[str] | None:
    """Documents that could match, or None when FTS5 is unavailable (caller scans everything)."""
    words = re.findall(r"\w+", query)
    if not words:
        return None
    # Every word as a prefix: a superset of documents the exact scan can match.
    expression = " AND ".join('"' + word.replace('"', '""') + '"*' for word in words)
    try:
        return {row[0] for row in conn.execute("SELECT document_id FROM documents_fts WHERE documents_fts MATCH ?",
                                               (f"canonical_text : ({expression})",))}
    except sqlite3.OperationalError:
        return None


def _snippet(text, start, end):
    before = text[max(0, start - CONTEXT):start]
    after = text[end:end + CONTEXT]
    before = ("…" if start > CONTEXT else "") + " ".join(before.split()) + (" " if before[-1:].isspace() else "")
    after = (" " if after[:1].isspace() else "") + " ".join(after.split()) + ("…" if end + CONTEXT < len(text) else "")
    return before, " ".join(text[start:end].split()), after


def search(directory: Path, query: str, *, document_ids=None, theme_ids=None, run_id=None,
           include_comments: bool = False, case_sensitive: bool = False, limit: int = 500) -> list[Hit]:
    """Find `query` in ready documents (and comment bodies if asked).

    Pre: query is at least 2 characters after stripping, otherwise returns [].
    Filters: document_ids limits documents; theme_ids keeps only matches that
    overlap an assignment of those themes in `run_id` (or researcher codings
    if run_id is None).
    Post: at most `limit` hits, text hits in document order first, then comments.
    """
    query = query.strip()
    if len(query) < 2 or limit <= 0:
        return []
    # Matches start at a word boundary ("insur" finds "insurance", not "reinsure").
    prefix = r"(?<!\w)" if query[:1].isalnum() else ""
    pattern = re.compile(prefix + re.escape(query), 0 if case_sensitive else re.IGNORECASE)
    with connect(directory) as conn:
        docs = [(doc, name) for doc, name in conn.execute(
            "SELECT id, original_name FROM documents WHERE extraction_status='ready' "
            "ORDER BY imported_at, rowid") if not document_ids or doc in document_ids]
        candidates = _fts_candidates(conn, query)
        text_docs = [(doc, name) for doc, name in docs if candidates is None or doc in candidates]
        relevant = {doc for doc, _ in (docs if include_comments else text_docs)}
        if not relevant:
            return []
        coded = _coded_ranges(directory, run_id, relevant, conn, theme_ids)

        def labels_at(doc, start, end):
            return sorted({label for s, e, tid, label in coded.get(doc, []) if s < end and start < e})

        hits: list[Hit] = []
        # Fetch one candidate at a time; unrelated document text is never loaded,
        # and reaching the result limit stops further reads.
        for doc, name in text_docs:
            text = conn.execute("SELECT canonical_text FROM documents WHERE id=?", (doc,)).fetchone()[0]
            pages = conn.execute("SELECT start_char, end_char, page_number FROM document_pages "
                                 "WHERE document_id=? AND page_number IS NOT NULL", (doc,)).fetchall()
            for found in pattern.finditer(text):
                themes = labels_at(doc, found.start(), found.end())
                if theme_ids and not themes:
                    continue
                before, match, after = _snippet(text, found.start(), found.end())
                page = next((p for s, e, p in pages if s <= found.start() < e), None)
                hits.append(Hit(doc, name, found.start(), found.end(), before, match, after, page, themes))
                if len(hits) >= limit:
                    return hits
        if include_comments:
            for doc, name, start, end, quote, body in conn.execute("""SELECT c.document_id, d.original_name,
                    c.start_char, c.end_char, c.quote, c.body
                    FROM comments c JOIN documents d ON d.id=c.document_id WHERE c.state != 'draft' AND c.scope = 'passage'
                    ORDER BY d.imported_at, d.rowid, c.start_char"""):
                if doc not in relevant or not pattern.search(body):
                    continue
                themes = labels_at(doc, start, end)
                if theme_ids and not themes:
                    continue
                hits.append(Hit(doc, name, start, end, "", " ".join(quote.split())[:120], "",
                                themes=themes, kind="comment", comment=body))
                if len(hits) >= limit:
                    break
    return hits


def _coded_ranges(directory, run_id, document_ids, conn, theme_ids=None):
    from qualitative_themes.storage.coding import list_themes, effective_assignments_from_connection
    result = {}
    for tid, label, _ in list_themes(directory, run_id):
        if theme_ids and tid not in theme_ids:
            continue
        # Search only needs offsets and labels, not navigation previews or full text.
        for _aid, doc, start, end, _origin in effective_assignments_from_connection(conn, tid, run_id):
            if doc in document_ids:
                result.setdefault(doc, []).append((start, end, tid, label))
    return result
