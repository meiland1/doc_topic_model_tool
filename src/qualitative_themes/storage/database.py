"""Create and open self-contained project directories."""
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from qualitative_themes.domain.models import Project
from qualitative_themes.storage.migrations import migrate

DB_NAME = "project.sqlite"


@contextmanager
def connect(directory: Path) -> Iterator[sqlite3.Connection]:
    """Commit/rollback a scoped transaction and always release its file handle."""
    database = Path(directory) / DB_NAME
    if not database.is_file():
        raise FileNotFoundError(f"No project database found in {directory}")
    conn = sqlite3.connect(database)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        migrate(conn)
        with conn:
            yield conn
    finally:
        conn.close()


def create_project(directory: Path, name: str) -> Project:
    directory = Path(directory).expanduser().resolve()
    name = name.strip()
    if not name:
        raise ValueError("Give the project a name.")
    if directory.exists() and any(directory.iterdir()):
        raise FileExistsError("Choose an empty folder for the new project.")
    directory.mkdir(parents=True, exist_ok=True)
    db = directory / DB_NAME
    # Exclusive creation prevents replacing an existing database.
    with db.open("xb"):
        pass
    try:
        with connect(directory) as conn:
            project = Project(str(uuid4()), name, directory, datetime.now(timezone.utc).isoformat())
            conn.execute("INSERT INTO project(id, name, created_at) VALUES (?, ?, ?)",
                         (project.id, project.name, project.created_at))
        (directory / "sources").mkdir(exist_ok=True)
        (directory / "models").mkdir(exist_ok=True)
        return project
    except Exception:
        db.unlink(missing_ok=True)
        raise


def open_project(directory: Path) -> Project:
    directory = Path(directory).expanduser().resolve()
    with connect(directory) as conn:
        row = conn.execute("SELECT id, name, created_at FROM project").fetchone()
    if row is None:
        raise ValueError("Project metadata is missing.")
    return Project(row[0], row[1], directory, row[2])
