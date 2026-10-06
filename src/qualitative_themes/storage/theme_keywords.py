"""Saved BERTopic keywords shared by the workspace and codebook exports."""
import json
from qualitative_themes.storage.database import connect


def theme_keyword_scores_from_connection(conn, theme_id, run_id=None):
    rows = conn.execute("""
        SELECT m.bertopic_id,r.id,r.model_info_json
        FROM model_suggestions m JOIN analysis_runs r ON r.id=m.run_id
        WHERE m.theme_id=? AND r.status='completed'
        GROUP BY r.id,m.bertopic_id
        ORDER BY r.completed_at DESC,r.rowid DESC
    """, (theme_id,)).fetchall()
    selected = [row for row in rows if row[1] == run_id] if run_id else []
    if not selected and rows:
        selected = [row for row in rows if row[1] == rows[0][1]]
    words = {}
    for topic, _rid, encoded in selected:
        for word, score in json.loads(encoded).get('keywords', {}).get(str(topic), []):
            words[word] = max(score, words.get(word, 0))
    return sorted(words.items(), key=lambda row: (-row[1], row[0]))[:10]


def theme_keywords_from_connection(conn, theme_id, run_id=None):
    return [word for word, _score in theme_keyword_scores_from_connection(conn, theme_id, run_id)]


def theme_keyword_scores(directory, theme_id, run_id=None):
    with connect(directory) as conn:
        return theme_keyword_scores_from_connection(conn, theme_id, run_id)


def theme_keywords(directory, theme_id, run_id=None):
    with connect(directory) as conn:
        return theme_keywords_from_connection(conn, theme_id, run_id)
