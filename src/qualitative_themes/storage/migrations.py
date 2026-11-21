"""Create a project's database schema, or bring a 0.3 development project up to date.

Projects made with the 0.2.x test releases (user_version 1 or 2) are refused with
a plain message rather than converted. Version 3 (the first 0.3 builds) is
upgraded to 4 in place: comments gain scopes and pinning, and memos become
comments.
"""
import sqlite3

SCHEMA_VERSION = 4
OLDEST_UPGRADABLE = 3  # older projects are refused (OLD_PROJECT_MESSAGE)

OLD_PROJECT_MESSAGE = ("This project was made with an older test version of the app. "
                       "Create a new project and add your documents again.")

# A comment is on a passage (document + range), a theme, a document or the whole
# project. Replies copy their thread's scope and target.
COMMENTS_TABLE = """
    CREATE TABLE {name} (
        id TEXT PRIMARY KEY,
        scope TEXT NOT NULL DEFAULT 'passage' CHECK(scope IN ('passage', 'theme', 'document', 'project')),
        document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
        theme_id TEXT REFERENCES themes(id),
        start_char INTEGER, end_char INTEGER,
        quote TEXT NOT NULL DEFAULT '', body TEXT NOT NULL,
        parent_id TEXT REFERENCES {name}(id) ON DELETE CASCADE,
        author TEXT, key_quote INTEGER NOT NULL DEFAULT 0, pinned INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL DEFAULT 'open',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, resolved_at TEXT,
        source_note_id TEXT,
        CHECK((scope = 'passage') = (start_char IS NOT NULL AND end_char IS NOT NULL)),
        CHECK(start_char IS NULL OR (start_char >= 0 AND end_char >= start_char)),
        CHECK(scope NOT IN ('passage', 'document') OR document_id IS NOT NULL),
        CHECK(scope != 'theme' OR theme_id IS NOT NULL),
        CHECK(state IN ('draft', 'open', 'resolved'))
    );
"""
COMMENT_INDEXES = """
    CREATE INDEX ix_comments_document ON comments(document_id, start_char);
    CREATE INDEX ix_comments_theme ON comments(theme_id);
"""

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
    CREATE TABLE history_events (
        id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL, event_type TEXT NOT NULL,
        details_json TEXT NOT NULL
    );
    CREATE INDEX ix_segments_document ON segments(document_id, start_char);
    CREATE INDEX ix_codings_theme ON codings(theme_id, document_id, start_char);
    CREATE INDEX ix_suggestions_theme ON model_suggestions(theme_id);
""" + COMMENTS_TABLE.format(name="comments") + COMMENT_INDEXES + """
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
    if version == 3:
        with conn:
            _upgrade_3_to_4(conn)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        return
    if version != 0:
        raise ValueError(OLD_PROJECT_MESSAGE)
    with conn:
        conn.executescript(SCHEMA)
        _create_search_index(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _upgrade_3_to_4(conn: sqlite3.Connection) -> None:
    """Rebuild comments with scopes and pinning, and turn memos into comments.

    SQLite can't relax NOT NULL in place, so the table is copied. A memo on a
    coded passage becomes a passage comment on that range (tagged with the
    theme), a theme memo a theme comment, a document memo a document comment.
    `source_note_id` keeps the memo's id.
    """
    conn.execute(COMMENTS_TABLE.format(name="comments_new"))
    conn.execute("""INSERT INTO comments_new (id, scope, document_id, start_char, end_char, quote, body, parent_id,
                        author, key_quote, state, created_at, updated_at, resolved_at, source_note_id)
                    SELECT id, 'passage', document_id, start_char, end_char, quote, body, parent_id, author, key_quote,
                           state, created_at, updated_at, resolved_at, source_note_id FROM comments""")
    conn.execute("""INSERT INTO comments_new (id, scope, document_id, theme_id, start_char, end_char, quote, body,
                        created_at, updated_at, source_note_id)
                    SELECT 'note-' || n.id, 'passage', c.document_id, c.theme_id, c.start_char, c.end_char,
                           substr(d.canonical_text, c.start_char + 1, min(500, c.end_char - c.start_char)),
                           n.body, n.created_at, n.created_at, n.id
                    FROM notes n JOIN codings c ON c.id = n.coding_id JOIN documents d ON d.id = c.document_id""")
    conn.execute("""INSERT INTO comments_new (id, scope, theme_id, body, created_at, updated_at, source_note_id)
                    SELECT 'note-' || id, 'theme', theme_id, body, created_at, created_at, id FROM notes
                    WHERE coding_id IS NULL AND theme_id IS NOT NULL""")
    conn.execute("""INSERT INTO comments_new (id, scope, document_id, body, created_at, updated_at, source_note_id)
                    SELECT 'note-' || id, 'document', document_id, body, created_at, created_at, id FROM notes
                    WHERE coding_id IS NULL AND theme_id IS NULL AND document_id IS NOT NULL""")
    conn.execute("DROP TABLE comments")
    conn.execute("DROP TABLE notes")
    conn.execute("ALTER TABLE comments_new RENAME TO comments")
    for statement in COMMENT_INDEXES.strip().split(";"):
        if statement.strip():
            conn.execute(statement)


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
