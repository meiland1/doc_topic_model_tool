"""Comments on a passage, a theme, a document or the whole project.

A passage comment anchors to characters, not to a coding, so it survives when a
coding is removed or an analysis is rerun. It can also carry the theme that was
selected when it was written, so it shows up under that theme. A thread is a
top-level comment plus its replies (``parent_id``); replies copy the thread's
scope and target. Drafts are saved as ``state='draft'`` so nothing typed is
lost when the app closes. Pinned threads are listed first whatever the filter.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from qualitative_themes.storage.database import connect

SCOPES = ("passage", "theme", "document", "project")


@dataclass(frozen=True)
class Target:
    """What a comment is on. Use the class methods rather than the constructor."""
    scope: str
    document_id: str | None = None
    theme_id: str | None = None
    start: int | None = None
    end: int | None = None

    @classmethod
    def passage(cls, document_id, start, end, theme_id=None):
        return cls("passage", document_id, theme_id, start, end)

    @classmethod
    def theme(cls, theme_id):
        return cls("theme", theme_id=theme_id)

    @classmethod
    def document(cls, document_id):
        return cls("document", document_id=document_id)

    @classmethod
    def project(cls):
        return cls("project")


@dataclass(frozen=True)
class Comment:
    """One comment row. `start`/`end` are canonical offsets for passage comments, None otherwise."""
    id: str
    scope: str
    document_id: str | None
    theme_id: str | None
    start: int | None
    end: int | None
    quote: str
    body: str
    parent_id: str | None
    key_quote: bool
    pinned: bool
    state: str
    created_at: str
    updated_at: str

    @property
    def target(self) -> Target:
        return Target(self.scope, self.document_id, self.theme_id, self.start, self.end)


_COLUMNS = ("id, scope, document_id, theme_id, start_char, end_char, quote, body, parent_id, key_quote, pinned, "
            "state, created_at, updated_at")
# Project, theme, document, then passage comments; documents in the order they were added.
_ORDER = ("CASE scope WHEN 'project' THEN 0 WHEN 'theme' THEN 1 WHEN 'document' THEN 2 ELSE 3 END, "
          "(SELECT d.rowid FROM documents d WHERE d.id = comments.document_id), start_char, end_char, created_at")


def _row(row) -> Comment:
    return Comment(*row[:9], bool(row[9]), bool(row[10]), *row[11:])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event(conn, kind, details):
    conn.execute("INSERT INTO history_events (id, occurred_at, event_type, details_json) VALUES (?, ?, ?, ?)",
                 (str(uuid4()), _now(), kind, json.dumps(details)))


def _check_target(conn, target: Target) -> str:
    """Raise ValueError if `target` doesn't exist. Returns the quote to store (passage text, max 500 chars)."""
    if target.scope not in SCOPES:
        raise ValueError(f"Unknown comment scope: {target.scope}")
    if target.theme_id is not None and not conn.execute("SELECT 1 FROM themes WHERE id=?", (target.theme_id,)).fetchone():
        raise ValueError("That theme no longer exists.")
    if target.scope == "theme" and target.theme_id is None:
        raise ValueError("Choose a theme to comment on.")
    if target.scope in ("passage", "document"):
        row = conn.execute("SELECT canonical_text FROM documents WHERE id=?", (target.document_id,)).fetchone()
        if row is None:
            raise ValueError("Open a document to comment on it.")
        if target.scope == "passage":
            start, end = target.start, target.end
            if start is None or end is None or not 0 <= start <= end <= len(row[0]):
                raise ValueError("Select text in an imported document to comment on.")
            return row[0][start:end][:500]
    return ""


def add_comment(directory: Path, target: Target, body: str, *, parent_id: str | None = None,
                key_quote: bool = False, draft: bool = False) -> str:
    """Pre: `target` exists (for a passage, [start, end) is inside the document;
    end == start is a caret comment). A reply (parent_id set) takes its thread's
    target, so the one passed is ignored.
    Post: returns the new id. Drafts may be empty and aren't logged to history.
    """
    body = body.strip()
    if not body and not draft:
        raise ValueError("Write a comment first.")
    with connect(directory) as conn:
        if parent_id:
            parent = conn.execute("SELECT scope, document_id, theme_id, start_char, end_char FROM comments WHERE id=?",
                                  (parent_id,)).fetchone()
            if parent is None:
                raise ValueError("The comment you are replying to no longer exists.")
            target = Target(*parent)
        quote = _check_target(conn, target)
        comment_id = str(uuid4())
        now = _now()
        conn.execute("INSERT INTO comments (id, scope, document_id, theme_id, start_char, end_char, quote, body, "
                     "parent_id, key_quote, state, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (comment_id, target.scope, target.document_id, target.theme_id, target.start, target.end, quote,
                      body, parent_id, int(key_quote), "draft" if draft else "open", now, now))
        if not draft:
            _event(conn, "comment_added", {"comment_id": comment_id, "scope": target.scope,
                                           "document_id": target.document_id, "theme_id": target.theme_id,
                                           "start": target.start, "end": target.end, "reply": bool(parent_id)})
    return comment_id


def update_comment(directory: Path, comment_id: str, body: str | None = None, *, key_quote: bool | None = None,
                   publish: bool = False, target: Target | None = None) -> None:
    """Change body, key-quote flag and/or (for a draft) its target. publish=True turns a draft into an open comment."""
    with connect(directory) as conn:
        row = conn.execute("SELECT body, state FROM comments WHERE id=?", (comment_id,)).fetchone()
        if row is None:
            raise ValueError("This comment no longer exists.")
        new_body = row[0] if body is None else body.strip()
        state = "open" if publish and row[1] == "draft" else row[1]
        if not new_body and state != "draft":
            raise ValueError("A comment cannot be empty.")
        if target is not None:
            if row[1] != "draft":
                raise ValueError("Only a draft can be moved to something else.")
            quote = _check_target(conn, target)
            conn.execute("UPDATE comments SET scope=?, document_id=?, theme_id=?, start_char=?, end_char=?, quote=? "
                         "WHERE id=?", (target.scope, target.document_id, target.theme_id, target.start, target.end,
                                        quote, comment_id))
        conn.execute("UPDATE comments SET body=?, key_quote=coalesce(?, key_quote), state=?, updated_at=? WHERE id=?",
                     (new_body, None if key_quote is None else int(key_quote), state, _now(), comment_id))
        if row[1] != "draft" or publish:
            _event(conn, "comment_edited", {"comment_id": comment_id})


def _thread_root(conn, comment_id: str, action: str) -> None:
    if conn.execute("SELECT 1 FROM comments WHERE id=? AND parent_id IS NULL", (comment_id,)).fetchone() is None:
        raise ValueError(f"Only a thread's first comment can be {action}.")


def set_resolved(directory: Path, comment_id: str, resolved: bool = True) -> None:
    """Resolve or reopen a thread. Pre: `comment_id` is a top-level comment, not a reply."""
    with connect(directory) as conn:
        _thread_root(conn, comment_id, "resolved")
        conn.execute("UPDATE comments SET state=?, resolved_at=?, updated_at=? WHERE id=?",
                     ("resolved" if resolved else "open", _now() if resolved else None, _now(), comment_id))
        _event(conn, "comment_resolved" if resolved else "comment_reopened", {"comment_id": comment_id})


def set_pinned(directory: Path, comment_id: str, pinned: bool = True) -> None:
    """Pin a thread to the top of the comments panel, or unpin it. Pre: a top-level comment."""
    with connect(directory) as conn:
        _thread_root(conn, comment_id, "pinned")
        conn.execute("UPDATE comments SET pinned=? WHERE id=?", (int(pinned), comment_id))
        _event(conn, "comment_pinned" if pinned else "comment_unpinned", {"comment_id": comment_id})


def delete_comment(directory: Path, comment_id: str) -> None:
    """Hard delete. Replies go with it (ON DELETE CASCADE); history keeps a record."""
    with connect(directory) as conn:
        conn.execute("DELETE FROM comments WHERE id=?", (comment_id,))
        _event(conn, "comment_deleted", {"comment_id": comment_id})


def _filter(document_id=None, theme_id=None):
    """WHERE clause (on root rows) for one of the panel's filters.

    document_id: passage and document comments in that document.
    theme_id:    theme comments on it, plus passage comments tagged with it.
    neither:     everything in the project.
    """
    if document_id is not None:
        return "document_id=?", (document_id,)
    if theme_id is not None:
        return "theme_id=? AND scope IN ('theme', 'passage')", (theme_id,)
    return "1", ()


def list_comments(directory: Path, *, document_id: str | None = None, theme_id: str | None = None,
                  include_resolved: bool = False, pinned_too: bool = False) -> list[tuple[Comment, list[Comment]]]:
    """Threads matching the filter (see _filter), each with its replies oldest first.

    Order: project, theme, document, then passage comments by position. Drafts
    are never included; resolved threads only with include_resolved.
    pinned_too=True adds every pinned thread in the project, wherever it is.
    """
    where, args = _filter(document_id, theme_id)
    with connect(directory) as conn:
        roots = [_row(r) for r in conn.execute(
            f"SELECT {_COLUMNS} FROM comments WHERE parent_id IS NULL AND state != 'draft' "
            f"AND ({where}{' OR pinned=1' if pinned_too else ''}) "
            f"{'' if include_resolved else 'AND state != ' + repr('resolved')} ORDER BY {_ORDER}", args)]
        replies: dict[str, list[Comment]] = {}
        ids = [root.id for root in roots]
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            for r in conn.execute(f"SELECT {_COLUMNS} FROM comments WHERE state != 'draft' AND parent_id IN "
                                  f"({','.join('?' * len(chunk))}) ORDER BY created_at", chunk):
                reply = _row(r)
                replies.setdefault(reply.parent_id, []).append(reply)
    return [(root, replies.get(root.id, [])) for root in roots]


def list_threads(directory: Path, document_id: str, include_resolved: bool = False) -> list[tuple[Comment, list[Comment]]]:
    """Threads in one document (passage and document comments), in reading order."""
    return list_comments(directory, document_id=document_id, include_resolved=include_resolved)


def resolved_count(directory: Path, document_id: str | None = None, theme_id: str | None = None) -> int:
    where, args = _filter(document_id, theme_id)
    with connect(directory) as conn:
        return conn.execute(f"SELECT count(*) FROM comments WHERE parent_id IS NULL AND state='resolved' AND ({where})",
                            args).fetchone()[0]


def open_count(directory: Path, document_id: str | None = None, theme_id: str | None = None) -> int:
    """Open threads matching the filter (for the Comments (N) button)."""
    where, args = _filter(document_id, theme_id)
    with connect(directory) as conn:
        return conn.execute(f"SELECT count(*) FROM comments WHERE parent_id IS NULL AND state='open' AND ({where})",
                            args).fetchone()[0]


def drafts(directory: Path, document_id: str | None = None) -> list[Comment]:
    """Unsent drafts, most recently edited first (restored when the panel reopens).

    With a document: drafts in it plus drafts on a theme or the project, which
    belong to no document. Without: every draft.
    """
    where, args = ("(document_id=? OR document_id IS NULL)", (document_id,)) if document_id else ("1", ())
    with connect(directory) as conn:
        return [_row(r) for r in conn.execute(
            f"SELECT {_COLUMNS} FROM comments WHERE state='draft' AND {where} ORDER BY updated_at DESC", args)]


def comment_counts(directory: Path) -> dict[str, int]:
    """{document_id: open thread count}, for badges in the document list."""
    with connect(directory) as conn:
        return dict(conn.execute("SELECT document_id, count(*) FROM comments WHERE parent_id IS NULL "
                                 "AND state='open' AND document_id IS NOT NULL GROUP BY document_id").fetchall())


def move_theme(conn, source_id: str, target_id: str) -> None:
    """Theme merge: comments on or tagged with `source_id` follow it into `target_id`. Uses the caller's transaction."""
    conn.execute("UPDATE comments SET theme_id=? WHERE theme_id=?", (target_id, source_id))
