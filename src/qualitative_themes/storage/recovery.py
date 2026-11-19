"""Daily verified archives with conservative, project-specific retention.

Snapshots live next to the project in .tmt-recovery-<key>/, not inside it,
so they survive if the project folder is damaged. A small recovery.sqlite
index records which snapshots belong to which project path. The newest
KEEP snapshots are kept.
"""
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from qualitative_themes.storage.backup import create_backup

KEEP = 7
_NAME = re.compile(r'snapshot-\d{4}-\d{2}-\d{2}-[0-9a-f]{32}\.tmtbackup')


def project_key(directory):
    """Stable id for a project *location* (so a moved project gets a fresh recovery folder)."""
    return hashlib.sha256(os.path.normcase(str(Path(directory).resolve())).encode()).hexdigest()


def recovery_folder(directory):
    directory = Path(directory).resolve()
    return directory.parent / ('.tmt-recovery-' + project_key(directory)[:20])


def _safe_path(root, name):
    path = root / name
    if not _NAME.fullmatch(name) or path.is_symlink() or path.resolve().parent != root.resolve():
        raise ValueError('A recovery entry points outside its managed snapshot folder.')
    return path


@contextmanager
def _index(directory, create=False):
    """Open the recovery index. Yields None if it doesn't exist and create=False.

    Refuses to touch folders or indexes it doesn't recognize, so it never
    deletes files it didn't create.
    """
    root = recovery_folder(directory)
    if root.is_symlink() or root.resolve() != root:
        raise ValueError('The recovery folder must not be a link to another location.')
    index = root / 'recovery.sqlite'
    if create:
        root.mkdir(exist_ok=True)
    if not index.exists():
        if not create:
            yield None
            return
        if any(root.iterdir()):
            raise ValueError('The recovery folder contains unrecognized files; it was left unchanged.')
    if index.is_symlink():
        raise ValueError('The recovery index must not be a link.')
    database = str(index) if create else index.as_uri() + '?mode=ro'
    with closing(sqlite3.connect(database, timeout=0, uri=not create)) as conn:
        conn.execute('BEGIN IMMEDIATE' if create else 'BEGIN')
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        if version == 0 and create:
            if conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                raise ValueError('The recovery index is unrecognized and was left unchanged.')
            conn.execute('CREATE TABLE owner (project_key TEXT NOT NULL)')
            conn.execute('INSERT INTO owner VALUES (?)', (project_key(directory),))
            conn.execute('CREATE TABLE snapshots (name TEXT PRIMARY KEY, day TEXT NOT NULL, created TEXT NOT NULL)')
            conn.execute('PRAGMA user_version = 1')
            conn.commit()
            conn.execute('BEGIN IMMEDIATE')
        elif version != 1:
            raise ValueError('Unsupported recovery index; existing snapshots were left unchanged.')
        if conn.execute('SELECT project_key FROM owner').fetchall() != [(project_key(directory),)]:
            raise ValueError('This recovery folder belongs to a different project location.')
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def list_snapshots(directory):
    """[(day_iso, path)] newest first; only entries whose file still exists."""
    root = recovery_folder(directory)
    with _index(directory) as conn:
        if conn is None:
            return []
        return [(day, _safe_path(root, name)) for name, day in conn.execute(
            'SELECT name, day FROM snapshots ORDER BY rowid DESC')
            if _safe_path(root, name).is_file()]


def snapshot_due(directory, today=None):
    """True if there's no snapshot for `today` yet (checked when a project opens)."""
    today = today or date.today()
    return not any(day == today.isoformat() for day, _ in list_snapshots(directory))


@dataclass(frozen=True)
class SnapshotResult:
    path: Path
    warning: str = ''


def create_snapshot(directory, progress=None, *, today=None, force=False):
    """Make today's snapshot (or return the existing one unless force=True), then prune.

    Post: the new snapshot is recorded before pruning starts, so a pruning
    failure only produces a warning and never loses the new copy.
    """
    today = today or date.today()
    root = recovery_folder(directory)
    with _index(directory, create=True) as conn:
        for name, in conn.execute('SELECT name FROM snapshots WHERE day=?', (today.isoformat(),)):
            path = _safe_path(root, name)
            if path.is_file() and not force:
                return SnapshotResult(path)
        name = f'snapshot-{today.isoformat()}-{uuid4().hex}.tmtbackup'
        path = _safe_path(root, name)
        create_backup(directory, path, progress)
        conn.execute('INSERT INTO snapshots VALUES (?, ?, ?)',
                     (name, today.isoformat(), datetime.now(timezone.utc).isoformat()))
        # Preserve the verified snapshot even if pruning later fails.
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        old = conn.execute('SELECT name FROM snapshots ORDER BY rowid DESC').fetchall()[KEEP:]
        for old_name, in old:
            try:
                _safe_path(root, old_name).unlink(missing_ok=True)
            except (OSError, ValueError) as exc:
                return SnapshotResult(path, f'Snapshot saved; older copies could not be removed: {exc}')
            conn.execute('DELETE FROM snapshots WHERE name=?', (old_name,))
        return SnapshotResult(path)
