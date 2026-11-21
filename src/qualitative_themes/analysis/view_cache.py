"""Built visualizations saved inside the project, so they reopen instantly.

Saved under ``<project>/models/views/<run_id>/``. A fingerprint of the coding
state (themes, codings, suggestion decisions, history) records what the views
were built from; when coding changes later the saved views still open, marked
as out of date, until the researcher refreshes them.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from qualitative_themes.storage.database import connect

VIEW_KEYS = ("map", "heatmap", "hierarchy")
# Tables whose changes don't affect the plots.
_IGNORED = {"comments", "reading_marks", "documents_fts", "app_meta"}


def coding_fingerprint(directory: Path, run_id: str) -> str:
    """Cheap signature of the coding state (row counts + latest rowids), not a full hash."""
    parts = [run_id]
    with connect(directory) as conn:
        tables = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for table in tables:
            if table in _IGNORED or table.startswith("documents_fts"):
                continue
            where = " WHERE event_type != 'workspace_view_saved'" if table == 'history_events' else ''
            count, last = conn.execute(f'SELECT count(*), max(rowid) FROM "{table}"' + where).fetchone()
            parts.append(f"{table}:{count}:{last}")
        latest = conn.execute("SELECT max(occurred_at) FROM history_events WHERE event_type != 'workspace_view_saved'").fetchone()[0]
        parts.append(str(latest))
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def _folder(directory: Path, run_id: str) -> Path:
    return Path(directory) / "models" / "views" / run_id


def save_views(directory: Path, run_id: str, result: dict) -> Path:
    """Write views to a .partial folder, then swap it in so a crash never leaves half a cache."""
    folder = _folder(directory, run_id)
    temporary = folder.with_name(folder.name + ".partial")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    for key in VIEW_KEYS:
        if key in result.get("html", {}):
            (temporary / f"{key}.html").write_text(result["html"][key], encoding="utf-8")
    snapshot = result.get("snapshot", {})
    themes = {tid: {name: theme.get(name) for name in ("theme", "unique_coded_ranges", "documents_covered", "keywords")}
              for tid, theme in snapshot.get("themes", {}).items()}
    meta = {"fingerprint": coding_fingerprint(directory, run_id), "built_at": datetime.now(timezone.utc).isoformat(),
            "messages": result.get("messages", {}), "tree": result.get("tree", ""), "notes": result.get("notes", []),
            "snapshot": {"theme_topics": snapshot.get("theme_topics", {}), "themes": themes}}
    (temporary / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    if folder.exists():
        shutil.rmtree(folder)
    temporary.replace(folder)
    return folder


def load_views(directory: Path, run_id: str) -> dict | None:
    """Saved views for a run, or None. ``current`` is False when coding changed since."""
    folder = _folder(directory, run_id)
    try:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    paths = {key: folder / f"{key}.html" for key in VIEW_KEYS if (folder / f"{key}.html").is_file()}
    return {"paths": paths, "messages": meta.get("messages", {}), "tree": meta.get("tree", ""),
            "snapshot": meta.get("snapshot", {}), "built_at": meta.get("built_at", ""), "notes": meta.get("notes", []),
            "current": meta.get("fingerprint") == coding_fingerprint(directory, run_id)}
