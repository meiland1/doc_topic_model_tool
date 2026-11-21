"""Saved BERTopic keywords shared by the workspace and codebook exports."""
import json
from qualitative_themes.storage.database import connect


def theme_keyword_scores_from_connection(conn, theme_id, run_id=None, cache=None):
    """Top 10 (word, score) pairs for a theme.

    Uses `run_id` if that run suggested the theme, otherwise the most recent
    completed run that did. Returns [] for a theme the model never suggested
    (e.g. one the researcher created).

    `cache` (optional dict) keeps parsed keywords per run, so callers that ask
    for many themes at once (the Overview) read each run's details only once.
    """
    # Find which runs/topics suggested the theme without pulling model_info_json
    # along: joining the blob onto every suggestion row made big themes take
    # seconds (it was copied once per suggested passage before grouping).
    rows = conn.execute("""
        SELECT r.id, m.bertopic_id
        FROM (SELECT DISTINCT run_id, bertopic_id FROM model_suggestions WHERE theme_id=?) m
        JOIN analysis_runs r ON r.id=m.run_id
        WHERE r.status='completed'
        ORDER BY r.completed_at DESC, r.rowid DESC
    """, (theme_id,)).fetchall()
    selected = [row for row in rows if row[0] == run_id] if run_id else []
    if not selected and rows:
        selected = [row for row in rows if row[0] == rows[0][0]]
    cache = {} if cache is None else cache
    words = {}
    for rid, topic in selected:
        if rid not in cache:
            encoded = conn.execute("SELECT model_info_json FROM analysis_runs WHERE id=?", (rid,)).fetchone()[0]
            cache[rid] = json.loads(encoded).get('keywords', {})
        for word, score in cache[rid].get(str(topic), []):
            words[word] = max(score, words.get(word, 0))
    return sorted(words.items(), key=lambda row: (-row[1], row[0]))[:10]


def theme_keywords_from_connection(conn, theme_id, run_id=None, cache=None):
    return [word for word, _score in theme_keyword_scores_from_connection(conn, theme_id, run_id, cache)]


def theme_keyword_scores(directory, theme_id, run_id=None):
    with connect(directory) as conn:
        return theme_keyword_scores_from_connection(conn, theme_id, run_id)


def theme_keywords(directory, theme_id, run_id=None):
    with connect(directory) as conn:
        return theme_keywords_from_connection(conn, theme_id, run_id)
