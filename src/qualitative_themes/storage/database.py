"""Create and open self-contained project directories.

Every read/write in storage/ goes through connect(). Opening a connection
also runs migrations, so the first connect on an old project upgrades it
(with a backup, see migrations.py).
"""
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
    """Commit/rollback a scoped transaction and always release its file handle.

    Pre: `directory` already contains project.sqlite (raises FileNotFoundError otherwise).
    Post: commits if the block exits cleanly, rolls back on any exception, and
    closes the connection either way. Lock/busy errors are re-raised with a
    message the GUI can show as-is.

    Connections are short-lived on purpose: open one per operation and don't
    hold it across a worker thread or a dialog.
    """
    database = Path(directory) / DB_NAME
    if not database.is_file():
        raise FileNotFoundError(f"No project database found in {directory}")
    conn = sqlite3.connect(database)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        migrate(conn)
        with conn:
            yield conn
    except sqlite3.OperationalError as exc:
        if 'locked' in str(exc).lower() or 'busy' in str(exc).lower():
            raise sqlite3.OperationalError(
                'The project database is locked by another operation. Wait for the current task or cancellation '
                'to finish and close any other copy of the app using this project, then retry. '
                'If it persists, save drafts and restart the app. Do not delete database journal or lock files.') from exc
        raise
    finally:
        conn.close()


def create_project(directory: Path, name: str) -> Project:
    """Pre: `directory` is missing or empty; `name` is not blank.
    Post: project.sqlite (current schema), sources/ and models/ exist.
    If anything fails, the half-created database is deleted.
    """
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
    """Pre: `directory` holds a project created by this app.
    Post: the schema is migrated to the current version (side effect of connect).
    """
    directory = Path(directory).expanduser().resolve()
    with connect(directory) as conn:
        row = conn.execute("SELECT id, name, created_at FROM project").fetchone()
    if row is None:
        raise ValueError("Project metadata is missing.")
    return Project(row[0], row[1], directory, row[2])
