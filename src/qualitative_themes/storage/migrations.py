"""Create a project's database schema. There is one version; nothing is upgraded.

Projects made with the 0.2.x test releases (user_version 1 or 2) are refused with
a plain message rather than converted. When the schema changes again, bump
SCHEMA_VERSION and decide then whether an upgrade step is worth writing.
"""
import sqlite3

SCHEMA_VERSION = 3

OLD_PROJECT_MESSAGE = ("This project was made with an older test version of the app. "
                       "Create a new project and add your documents again.")

SCHEMA = """
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
    CREATE TABLE comments (
        id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        start_char INTEGER NOT NULL, end_char INTEGER NOT NULL,
        quote TEXT NOT NULL DEFAULT '', body TEXT NOT NULL,
        parent_id TEXT REFERENCES comments(id) ON DELETE CASCADE,
        author TEXT, key_quote INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL DEFAULT 'open',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, resolved_at TEXT,
        source_note_id TEXT,
        CHECK(start_char >= 0 AND end_char >= start_char),
        CHECK(state IN ('draft', 'open', 'resolved'))
    );
    CREATE INDEX ix_comments_document ON comments(document_id, start_char);
    CREATE TABLE reading_marks (
        id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        start_char INTEGER NOT NULL, end_char INTEGER NOT NULL,
        theme_id TEXT REFERENCES themes(id), run_id TEXT,
        verdict TEXT NOT NULL CHECK(verdict IN ('read', 'relevant', 'not_relevant')),
        marked_at TEXT NOT NULL,
        UNIQUE(document_id, start_char, end_char, theme_id)
    );
    CREATE INDEX ix_reading_marks_document ON reading_marks(document_id, start_char);
"""


def migrate(conn: sqlite3.Connection) -> None:
    """Make sure a connection's schema is the current one.

    Called by connect() on every open; a no-op for a current project.
    Pre: an open connection to a project database.
    Post: PRAGMA user_version == SCHEMA_VERSION for a new (empty) database.
    Raises ValueError for a project from an older or newer version; the file is
    never changed in that case.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == SCHEMA_VERSION:
        return
    if version > SCHEMA_VERSION:
        raise ValueError(f"Project format {version} is newer than this app supports ({SCHEMA_VERSION}).")
    if version != 0:
        raise ValueError(OLD_PROJECT_MESSAGE)
    with conn:
        conn.executescript(SCHEMA)
        _create_search_index(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _create_search_index(conn: sqlite3.Connection) -> None:
    """Full-text index over document text, kept current by triggers."""
    try:
        conn.executescript("""
            CREATE VIRTUAL TABLE documents_fts USING fts5(
                document_id UNINDEXED, original_name, canonical_text, tokenize = 'unicode61 remove_diacritics 2');
            INSERT INTO documents_fts(document_id, original_name, canonical_text)
                SELECT id, original_name, canonical_text FROM documents;
            CREATE TRIGGER documents_fts_insert AFTER INSERT ON documents BEGIN
                INSERT INTO documents_fts(document_id, original_name, canonical_text)
                VALUES (new.id, new.original_name, new.canonical_text);
            END;
            CREATE TRIGGER documents_fts_update AFTER UPDATE OF canonical_text, original_name ON documents BEGIN
                DELETE FROM documents_fts WHERE document_id = old.id;
                INSERT INTO documents_fts(document_id, original_name, canonical_text)
                VALUES (new.id, new.original_name, new.canonical_text);
            END;
            CREATE TRIGGER documents_fts_delete AFTER DELETE ON documents BEGIN
                DELETE FROM documents_fts WHERE document_id = old.id;
            END;
        """)
    except sqlite3.OperationalError:
        # SQLite built without FTS5: search falls back to a plain scan.
        pass
