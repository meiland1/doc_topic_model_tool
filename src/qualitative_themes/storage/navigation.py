"""Read-only navigation groups using the workspace's effective coding rules."""
from qualitative_themes.storage.database import connect
from qualitative_themes.storage.coding import effective_assignments_from_connection


def navigation_groups(directory, theme_ids, document_ids, run_id=None):
    """One source snapshot; retain short previews rather than full corpus text."""
    groups = {}
    with connect(directory) as conn:
        conn.execute('BEGIN')
        documents = {doc: (name, text, order) for order, (doc, name, text) in enumerate(conn.execute(
            'SELECT id,original_name,canonical_text FROM documents ORDER BY imported_at,rowid')) if doc in document_ids}
        for theme in theme_ids:
            rows = effective_assignments_from_connection(conn, theme, run_id)
            for aid, doc, start, end, origin in rows:
                if doc not in documents:
                    continue
                name, text, _ = documents[doc]
                preview = ' '.join(text[start:min(end, start+180)].split())[:100]
                groups.setdefault((theme, doc), []).append((aid, doc, name, start, end, preview))
        for rows in groups.values():
            rows.sort(key=lambda row: (row[3], row[4]))
    return groups
