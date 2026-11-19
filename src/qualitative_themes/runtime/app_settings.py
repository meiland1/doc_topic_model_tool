"""App-level preferences kept outside research projects (recent projects, naming model)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from qualitative_themes.runtime.paths import app_data_dir

MAX_RECENT = 8


def _file() -> Path:
    return app_data_dir() / "settings.json"


def load() -> dict:
    """settings.json as a dict; {} if missing or unreadable (never raises)."""
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data: dict) -> None:
    """Write via a temp file and replace, so a crash can't leave half a settings file."""
    path = _file()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temporary.replace(path)


def recent_projects() -> list[dict]:
    rows = load().get("recent_projects", [])
    return [row for row in rows if isinstance(row, dict) and isinstance(row.get("path"), str)]


def remember_project(path: Path, name: str) -> None:
    """Move a project to the top of the recent list (max MAX_RECENT)."""
    data = load()
    path = str(Path(path).resolve())
    rows = [row for row in recent_projects() if row["path"] != path]
    rows.insert(0, {"path": path, "name": name, "opened_at": datetime.now(timezone.utc).isoformat()})
    data["recent_projects"] = rows[:MAX_RECENT]
    save(data)


def forget_project(path: Path) -> None:
    data = load()
    path = str(Path(path).resolve())
    data["recent_projects"] = [row for row in recent_projects() if row["path"] != path]
    save(data)
