"""Versioned, verified project archives; restore only into a new directory.

A backup is a zip of project.sqlite, sources/ and models/ plus a manifest
with each file's size and SHA-256. Both directions verify everything
before the result is published, and neither ever overwrites existing files.
"""
from __future__ import annotations

import hashlib
from contextlib import closing
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import tempfile
from datetime import datetime, timezone
from zipfile import ZipFile, ZIP_DEFLATED

from qualitative_themes.storage.migrations import OLD_PROJECT_MESSAGE, SCHEMA_VERSION

MAX_FILES = 100_000
MAX_BYTES = 20 * 1024**3
MANIFEST = 'backup-manifest.json'


def _safe_name(name):
    """Reject archive paths that could escape the project or misbehave on Windows."""
    path = PurePosixPath(name)
    if (not name or '\\' in name or path.is_absolute() or
            any(part in {'', '.', '..'} or ':' in part or part.endswith((' ', '.'))
                or part.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                    *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
                for part in name.split('/'))):
        raise ValueError('The backup contains an unsafe file path.')
    if name != 'project.sqlite' and path.parts[0] not in {'sources', 'models'}:
        raise ValueError('The backup contains an unexpected project file.')
    return path


def _digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _check_database(directory):
    """Open a staged project.sqlite read-only and verify schema, integrity and source hashes.

    Returns the schema version. Raises ValueError on any problem.
    """
    db = directory / 'project.sqlite'
    with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)) as conn:
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        if version != SCHEMA_VERSION:
            raise ValueError(OLD_PROJECT_MESSAGE if version < SCHEMA_VERSION
                             else 'This backup requires a newer version of the application.')
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or conn.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('The project database failed its integrity check.')
        if not conn.execute('SELECT id FROM project').fetchone():
            raise ValueError('Project information is missing.')
        for source, digest in conn.execute('SELECT source_path, source_hash FROM documents'):
            relative = _safe_name(source)
            if relative.parts[0] != 'sources':
                raise ValueError('A document points outside the source folder.')
            target = directory.joinpath(*relative.parts)
            if not target.is_file() or (digest and _digest(target) != digest):
                raise ValueError('A source document is missing or does not match the saved project.')
    return version


def create_backup(directory, destination, progress=None):
    """Write a verified archive of a project.

    Pre: `destination` doesn't exist and is outside the project folder. The GUI
    should also block edits while this runs.
    Post: `destination` is a complete, verified zip, or doesn't exist at all.
    Holds a write reservation on the project database for the duration, so
    other writers wait (or fail with the locked message from connect()).
    `progress(str)` is called with short status messages.
    """
    directory, destination = Path(directory).resolve(), Path(destination).resolve()
    progress = progress or (lambda _: None)
    if destination.is_relative_to(directory):
        raise ValueError('Save the backup outside the project folder.')
    if destination.exists():
        raise FileExistsError('A file already exists there. Choose a new backup name.')
    if not (directory / 'project.sqlite').is_file():
        raise ValueError('Choose a valid project folder.')
    # The reservation prevents another normal SQLite writer from committing while
    # the database and immutable sources are captured. UI callers also disable edits.
    guard = sqlite3.connect(directory / 'project.sqlite', timeout=1)
    try:
        guard.execute('BEGIN IMMEDIATE')
        with tempfile.TemporaryDirectory(prefix='tmt-backup-') as temporary:
            staging = Path(temporary)
            progress('Copying the saved project…')
            source = sqlite3.connect((directory / 'project.sqlite').as_uri() + '?mode=ro', uri=True)
            target = sqlite3.connect(staging / 'project.sqlite')
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
            for folder in ('sources', 'models'):
                root = directory / folder
                if root.is_symlink() or (root.exists() and root.resolve() != root):
                    raise ValueError('Project folders must not be links to other locations.')
                if not root.exists():
                    continue
                for file in root.rglob('*'):
                    if file.is_symlink() or not file.resolve().is_relative_to(directory):
                        raise ValueError('Project files must not be links to other locations.')
                    if not file.is_file():
                        continue
                    name = file.relative_to(directory).as_posix()
                    _safe_name(name)
                    before = file.stat()
                    copied = staging / name
                    copied.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(file, copied)
                    after = file.stat()
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise ValueError('A project file changed during backup. Finish other tasks and try again.')
            version = _check_database(staging)
            files = {p.relative_to(staging).as_posix(): {'size': p.stat().st_size, 'sha256': _digest(p)}
                     for p in staging.rglob('*') if p.is_file()}
            if len(files) > MAX_FILES or sum(item['size'] for item in files.values()) > MAX_BYTES:
                raise ValueError('This project exceeds the supported backup size (20 GiB or 100,000 files).')
            manifest = {'format': 1, 'schema': version, 'created_at': datetime.now(timezone.utc).isoformat(), 'files': files}
            progress('Writing and verifying the backup…')
            # Stage on the destination volume; publish using exclusive creation so
            # existing backups are never replaced, including a file created meanwhile.
            with tempfile.TemporaryDirectory(prefix='.tmt-backup-', dir=destination.parent) as output:
                archive = Path(output) / 'backup.zip'
                with ZipFile(archive, 'w', compression=ZIP_DEFLATED) as zipped:
                    for name in files:
                        zipped.write(staging / name, name)
                    zipped.writestr(MANIFEST, json.dumps(manifest))
                with ZipFile(archive) as zipped:
                    if zipped.testzip() is not None:
                        raise ValueError('The backup could not be verified.')
                # A hard link atomically publishes the completed file without overwrite.
                try:
                    os.link(archive, destination)
                except FileExistsError:
                    raise
                except OSError:
                    # Removable FAT/exFAT volumes may not support hard links.
                    # Exclusive creation still protects previous backups.
                    stream = destination.open('xb')
                    try:
                        with stream, archive.open('rb') as source:
                            shutil.copyfileobj(source, stream)
                            stream.flush()
                            os.fsync(stream.fileno())
                    except Exception:
                        destination.unlink(missing_ok=True)
                        raise
    finally:
        guard.rollback()
        guard.close()
    return destination


def restore_backup(archive, destination, progress=None):
    """Unpack and verify an archive into a brand new folder.

    Pre: `destination` doesn't exist yet.
    Post: `destination` is a complete project that opens normally. On any
    failure, only that new folder is removed; nothing else is touched.
    """
    archive, destination = Path(archive).resolve(), Path(destination).resolve()
    progress = progress or (lambda _: None)
    if destination.exists():
        raise FileExistsError('Restore into a new folder; existing folders are never overwritten.')
    with tempfile.TemporaryDirectory(prefix='.tmt-restore-', dir=destination.parent) as temporary:
        staging = Path(temporary)
        progress('Checking the backup…')
        with ZipFile(archive) as zipped:
            entries = zipped.infolist()
            names = [item.filename for item in entries]
            if len(entries) > MAX_FILES + 1 or len({n.casefold() for n in names}) != len(names):
                raise ValueError('The backup has too many files or duplicate paths.')
            if sum(item.file_size for item in entries) > MAX_BYTES:
                raise ValueError('The expanded backup exceeds the supported size (20 GiB).')
            metadata = zipped.getinfo(MANIFEST)
            if metadata.file_size > 32 * 1024**2:
                raise ValueError('The backup manifest is too large.')
            manifest = json.loads(zipped.read(MANIFEST))
            if manifest.get('format') != 1 or not isinstance(manifest.get('files'), dict):
                raise ValueError('Unsupported backup format.')
            files = manifest['files']
            if set(names) != set(files) | {MANIFEST} or 'project.sqlite' not in files:
                raise ValueError('The backup is incomplete.')
            progress('Restoring and verifying project files…')
            for item in entries:
                if item.filename == MANIFEST:
                    continue
                relative = _safe_name(item.filename)
                if stat.S_ISLNK(item.external_attr >> 16) or item.is_dir():
                    raise ValueError('Backup entries must be regular files.')
                expected = files[item.filename]
                if item.file_size != expected['size']:
                    raise ValueError('A backup file has an incorrect size.')
                path = staging.joinpath(*relative.parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(item) as source, path.open('xb') as target:
                    shutil.copyfileobj(source, target)
                if _digest(path) != expected['sha256']:
                    raise ValueError('A backup file failed verification.')
        if _check_database(staging) != manifest.get('schema'):
            raise ValueError('The backup schema does not match its manifest.')
        (staging / 'sources').mkdir(exist_ok=True)
        (staging / 'models').mkdir(exist_ok=True)
        # Reserve a fresh destination; failure never touches a pre-existing folder.
        destination.mkdir()
        try:
            for child in staging.iterdir():
                shutil.move(str(child), str(destination / child.name))
        except Exception:
            # Only this newly reserved destination is cleaned up.
            shutil.rmtree(destination)
            raise
    return destination
