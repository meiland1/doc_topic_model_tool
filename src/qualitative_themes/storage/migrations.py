"""Ordered SQLite migrations. Never mutate a newer project's schema."""
import sqlite3

SCHEMA_VERSION = 1


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise ValueError(f"Project format {version} is newer than this app supports ({SCHEMA_VERSION}).")
    if version == SCHEMA_VERSION:
        return
    if version == 0:
        with conn:
            conn.executescript("""
                CREATE TABLE project (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE documents (
                    id TEXT PRIMARY KEY, original_name TEXT NOT NULL, source_path TEXT NOT NULL,
                    source_hash TEXT, canonical_text TEXT NOT NULL DEFAULT '',
                    extraction_status TEXT NOT NULL DEFAULT 'pending', imported_at TEXT NOT NULL
                );
                CREATE TABLE document_pages (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    page_number INTEGER, paragraph_number INTEGER,
                    start_char INTEGER NOT NULL, end_char INTEGER NOT NULL,
                    CHECK(start_char >= 0 AND end_char >= start_char)
                );
                CREATE TABLE analysis_runs (
                    id TEXT PRIMARY KEY, started_at TEXT NOT NULL, completed_at TEXT,
                    status TEXT NOT NULL, settings_json TEXT NOT NULL, model_info_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE segments (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL, start_char INTEGER NOT NULL, end_char INTEGER NOT NULL,
                    text_hash TEXT NOT NULL,
                    CHECK(start_char >= 0 AND end_char > start_char),
                    UNIQUE(run_id, document_id, ordinal)
                );
                CREATE TABLE themes (
                    id TEXT PRIMARY KEY, generated_label TEXT, user_label TEXT, description TEXT,
                    creation_source TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
                    merged_into_id TEXT REFERENCES themes(id)
                );
                CREATE TABLE model_suggestions (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
                    segment_id TEXT NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
                    theme_id TEXT REFERENCES themes(id), bertopic_id INTEGER NOT NULL,
                    score REAL, UNIQUE(run_id, segment_id)
                );
                CREATE TABLE codings (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    theme_id TEXT NOT NULL REFERENCES themes(id), start_char INTEGER NOT NULL,
                    end_char INTEGER NOT NULL, origin TEXT NOT NULL, state TEXT NOT NULL,
                    created_at TEXT NOT NULL, CHECK(start_char >= 0 AND end_char > start_char)
                );
                CREATE TABLE notes (
                    id TEXT PRIMARY KEY, document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
                    theme_id TEXT REFERENCES themes(id), coding_id TEXT REFERENCES codings(id) ON DELETE CASCADE,
                    body TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE history_events (
                    id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL, event_type TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                CREATE INDEX ix_segments_document ON segments(document_id, start_char);
                CREATE INDEX ix_codings_theme ON codings(theme_id, document_id, start_char);
                CREATE INDEX ix_suggestions_theme ON model_suggestions(theme_id);
            """)
            conn.execute("PRAGMA user_version = 1")
