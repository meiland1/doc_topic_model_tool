"""Margin comments anchored to a range of source text (Google Docs-style notes).

Comments anchor to characters, not to a coding, so they survive when a coding
is removed or an analysis is rerun. A thread is a top-level comment plus its
replies (``parent_id``). Drafts are saved as ``state='draft'`` so nothing typed
is lost when the app closes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from qualitative_themes.storage.database import connect


@dataclass(frozen=True)
class Comment:
    """One comment row. `start`/`end` are canonical offsets; `quote` is the anchored text (max 500 chars)."""
    id: str
    document_id: str
    start: int
    end: int
    quote: str
    body: str
    parent_id: str | None
    author: str | None
    key_quote: bool
    state: str
    created_at: str
    updated_at: str


_COLUMNS = ("id, document_id, start_char, end_char, quote, body, parent_id, author, key_quote, state, "
            "created_at, updated_at")


def _row(row) -> Comment:
    return Comment(row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], bool(row[8]), row[9], row[10], row[11])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event(conn, kind, details):
    conn.execute("INSERT INTO history_events (id, occurred_at, event_type, details_json) VALUES (?, ?, ?, ?)",
                 (str(uuid4()), _now(), kind, json.dumps(details)))


def add_comment(directory: Path, document_id: str, start: int, end: int, body: str, *,
                parent_id: str | None = None, author: str | None = None, key_quote: bool = False,
                draft: bool = False) -> str:
    """Pre: [start, end) is inside the document (end == start is allowed for a caret
    comment). Replies (parent_id set) take the parent's document and range,
    so the passed ones are ignored.
    Post: returns the new id. Drafts may be empty and aren't logged to history.
    """
    body = body.strip()
    if not body and not draft:
        raise ValueError("Write a comment first.")
    with connect(directory) as conn:
        if parent_id:
            parent = conn.execute("SELECT document_id, start_char, end_char FROM comments WHERE id=?", (parent_id,)).fetchone()
            if parent is None:
                raise ValueError("The comment you are replying to no longer exists.")
            document_id, start, end = parent
        row = conn.execute("SELECT canonical_text FROM documents WHERE id=?", (document_id,)).fetchone()
        if row is None or not 0 <= start <= end <= len(row[0]):
            raise ValueError("Select text in an imported document to comment on.")
        comment_id = str(uuid4())
        now = _now()
        conn.execute(f"INSERT INTO comments ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (comment_id, document_id, start, end, row[0][start:end][:500], body, parent_id, author,
                      int(key_quote), "draft" if draft else "open", now, now))
        if not draft:
            _event(conn, "comment_added", {"comment_id": comment_id, "document_id": document_id,
                                           "start": start, "end": end, "reply": bool(parent_id)})
    return comment_id


def update_comment(directory: Path, comment_id: str, body: str | None = None, *, key_quote: bool | None = None,
                   publish: bool = False) -> None:
    """Change body and/or key-quote flag. publish=True turns a draft into an open comment."""
    with connect(directory) as conn:
        row = conn.execute("SELECT body, state FROM comments WHERE id=?", (comment_id,)).fetchone()
        if row is None:
            raise ValueError("This comment no longer exists.")
        new_body = row[0] if body is None else body.strip()
        state = "open" if publish and row[1] == "draft" else row[1]
        if not new_body and state != "draft":
            raise ValueError("A comment cannot be empty.")
        conn.execute("UPDATE comments SET body=?, key_quote=coalesce(?, key_quote), state=?, updated_at=? WHERE id=?",
                     (new_body, None if key_quote is None else int(key_quote), state, _now(), comment_id))
        if row[1] != "draft" or publish:
            _event(conn, "comment_edited", {"comment_id": comment_id})


def set_resolved(directory: Path, comment_id: str, resolved: bool = True) -> None:
    """Resolve or reopen a thread. Pre: `comment_id` is a top-level comment, not a reply."""
    with connect(directory) as conn:
        if conn.execute("SELECT 1 FROM comments WHERE id=? AND parent_id IS NULL", (comment_id,)).fetchone() is None:
            raise ValueError("Only a thread's first comment can be resolved.")
        conn.execute("UPDATE comments SET state=?, resolved_at=?, updated_at=? WHERE id=?",
                     ("resolved" if resolved else "open", _now() if resolved else None, _now(), comment_id))
        _event(conn, "comment_resolved" if resolved else "comment_reopened", {"comment_id": comment_id})


def delete_comment(directory: Path, comment_id: str) -> None:
    """Hard delete. Replies go with it (ON DELETE CASCADE); history keeps a record."""
    with connect(directory) as conn:
        conn.execute("DELETE FROM comments WHERE id=?", (comment_id,))
        _event(conn, "comment_deleted", {"comment_id": comment_id})


def list_threads(directory: Path, document_id: str, include_resolved: bool = False) -> list[tuple[Comment, list[Comment]]]:
    """Top-level comments in reading order, each with its replies (oldest first).

    Drafts are never included; resolved threads only with include_resolved.
    """
    with connect(directory) as conn:
        rows = [_row(r) for r in conn.execute(
            f"SELECT {_COLUMNS} FROM comments WHERE document_id=? AND state != 'draft' "
            "ORDER BY start_char, end_char, created_at", (document_id,))]
    roots = [c for c in rows if c.parent_id is None and (include_resolved or c.state != "resolved")]
    replies: dict[str, list[Comment]] = {}
    for comment in rows:
        if comment.parent_id:
            replies.setdefault(comment.parent_id, []).append(comment)
    return [(root, sorted(replies.get(root.id, []), key=lambda c: c.created_at)) for root in roots]


def resolved_count(directory: Path, document_id: str) -> int:
    with connect(directory) as conn:
        return conn.execute("SELECT count(*) FROM comments WHERE document_id=? AND parent_id IS NULL AND state='resolved'",
                            (document_id,)).fetchone()[0]


def drafts(directory: Path, document_id: str) -> list[Comment]:
    """Unsent drafts for a document, most recently edited first (restored when the panel reopens)."""
    with connect(directory) as conn:
        return [_row(r) for r in conn.execute(
            f"SELECT {_COLUMNS} FROM comments WHERE document_id=? AND state='draft' ORDER BY updated_at DESC",
            (document_id,))]


def comment_counts(directory: Path) -> dict[str, int]:
    """{document_id: open thread count}, for badges in the document list."""
    with connect(directory) as conn:
        return dict(conn.execute("SELECT document_id, count(*) FROM comments WHERE parent_id IS NULL "
                                 "AND state='open' GROUP BY document_id").fetchall())
