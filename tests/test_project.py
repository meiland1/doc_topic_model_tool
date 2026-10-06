import sqlite3
import pytest
from qualitative_themes.storage.database import create_project, open_project, connect
from qualitative_themes.storage.migrations import SCHEMA_VERSION


def test_create_reopen_and_foreign_keys(tmp_path):
    folder = tmp_path / "study"
    created = create_project(folder, "Community Study")
    reopened = open_project(folder)
    assert reopened == created
    assert (folder / "sources").is_dir()
    assert (folder / "models").is_dir()
    with connect(folder) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert {x[0] for x in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} >= {
            "documents", "document_pages", "segments", "themes", "codings", "notes", "analysis_runs", "model_suggestions", "history_events"
        }


def test_refuse_nonempty_or_invalid_project(tmp_path):
    folder = tmp_path / "existing"
    folder.mkdir()
    (folder / "notes.txt").write_text("keep me")
    with pytest.raises(FileExistsError):
        create_project(folder, "New")
    assert (folder / "notes.txt").read_text() == "keep me"
    with pytest.raises(FileNotFoundError):
        open_project(folder)


def test_refuse_newer_schema_without_change(tmp_path):
    folder = tmp_path / "newer"
    create_project(folder, "Future")
    with sqlite3.connect(folder / "project.sqlite") as conn:
        conn.execute("PRAGMA user_version = 99")
    with pytest.raises(ValueError, match="newer"):
        open_project(folder)
    with sqlite3.connect(folder / "project.sqlite") as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 99
