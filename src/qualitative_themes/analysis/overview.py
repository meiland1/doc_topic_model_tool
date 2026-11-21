"""Data for the Themes tab: where to read, theme by theme and document by document."""
from __future__ import annotations

import json
from pathlib import Path

from qualitative_themes.analysis.fit import fit_strength
from qualitative_themes.storage.coding import analysis_document_ids, list_themes
from qualitative_themes.storage.database import connect
from qualitative_themes.storage.documents import list_documents
from qualitative_themes.storage.navigation import navigation_groups
from qualitative_themes.storage.reading import coverage, marks
from qualitative_themes.storage.theme_keywords import theme_keywords_from_connection

UNGROUPED_TITLE = "Unusual / ungrouped passages"
UNGROUPED_NOTE = "Not grouped with other passages. Rare experiences often show up here, so read these too."


def _is_ungrouped(conn, theme_id, run_id) -> bool:
    row = conn.execute("SELECT 1 FROM model_suggestions WHERE run_id=? AND theme_id=? AND bertopic_id=-1 LIMIT 1",
                       (run_id, theme_id)).fetchone()
    return row is not None


def reading_order(rows, fits):
    """Core passages first, then unlabelled, then borderline; ties in document order."""
    rank = {"core": 0, "": 1, "borderline": 2}

    def key(row):
        fit = fits.get((row[1], row[3], row[4]))
        return (rank[fit["label"]] if fit else 1, -(fit["similarity"] if fit else 0.0))
    return sorted(rows, key=key)


def build_overview(directory: Path, run_id: str | None) -> dict:
    """Everything the Overview screen draws for a run (or manual coding when run_id is None).

    Returns {'themes', 'documents', 'matrix', 'coverage', 'has_fit'}; matrix maps
    (theme_id, document_id) -> passage count. Read-only.
    """
    directory = Path(directory)
    if run_id:
        selected = analysis_document_ids(directory, run_id)
        documents = [(doc, name) for doc, name, _ in list_documents(directory, include_removed=True) if doc in selected]
    else:
        documents = [(doc, name) for doc, name, _ in list_documents(directory)]
    theme_rows = list_themes(directory, run_id)
    groups = navigation_groups(directory, {tid for tid, _, _ in theme_rows}, {doc for doc, _ in documents}, run_id)
    fits = fit_strength(directory, run_id) if run_id else {}
    representatives = {}
    if run_id:
        with connect(directory) as conn:
            info = json.loads(conn.execute("SELECT model_info_json FROM analysis_runs WHERE id=?", (run_id,)).fetchone()[0])
            topic_theme = info.get("topic_theme_map", {})
            for topic, ids in info.get("representative_segment_ids", {}).items():
                theme = topic_theme.get(str(topic))
                if not theme or not ids:
                    continue
                spans = conn.execute(f"SELECT document_id, start_char, end_char FROM segments WHERE id IN "
                                     f"({','.join('?' * len(ids))})", ids).fetchall()
                representatives[theme] = {tuple(span) for span in spans}
            ungrouped = {tid for tid, _, _ in theme_rows if _is_ungrouped(conn, tid, run_id)}
    else:
        ungrouped = set()
    with connect(directory) as conn:
        texts = dict(conn.execute("SELECT id, canonical_text FROM documents"))
    # Group rows by theme once; scanning every (theme, document) group per theme
    # was quadratic and dominated large analyses (hundreds of themes x documents).
    rows_by_theme: dict[str, list] = {}
    for (theme, _doc), group_rows in groups.items():
        rows_by_theme.setdefault(theme, []).extend(group_rows)
    # One read for every theme's keywords; each run's details are parsed once.
    keyword_cache: dict = {}
    with connect(directory) as conn:
        keywords = {tid: theme_keywords_from_connection(conn, tid, run_id, keyword_cache)
                    for tid, _, _ in theme_rows if tid not in ungrouped}
    themes = []
    for index, (tid, label, _count) in enumerate(theme_rows):
        rows = rows_by_theme.get(tid, [])
        if not rows and tid not in ungrouped:
            continue
        ordered = reading_order(rows, fits)
        preferred = [r for r in ordered if (r[1], r[3], r[4]) in representatives.get(tid, set())]
        quotes, seen = [], set()
        for row in preferred + ordered:
            words = " ".join(texts[row[1]][row[3]:row[4]].split()).lower()
            if row in quotes or words in seen:
                continue
            quotes.append(row)
            seen.add(words)
            if len(quotes) == 2:
                break
        is_ungrouped = tid in ungrouped
        themes.append({
            "theme_id": tid, "label": UNGROUPED_TITLE if is_ungrouped else label, "stored_label": label,
            "colour": index, "ungrouped": is_ungrouped,
            "keywords": [] if is_ungrouped else keywords.get(tid, [])[:6],
            "passages": len(rows), "documents": len({row[1] for row in rows}),
            # `text` saves the screen from reloading whole documents for each quote.
            "quotes": [{"document_id": r[1], "document": r[2], "start": r[3], "end": r[4],
                        "text": texts[r[1]][r[3]:r[4]]} for r in quotes],
        })
    themes.sort(key=lambda t: (t["ungrouped"], -t["passages"], t["label"].lower()))
    matrix = {(theme, doc): len(rows) for (theme, doc), rows in groups.items()}
    return {"run_id": run_id, "themes": themes, "documents": documents, "matrix": matrix,
            "coverage": coverage(directory, run_id) if run_id else None, "has_fit": bool(fits)}


def reading_queue(directory: Path, run_id: str | None, theme_ids) -> list[dict]:
    """Passages for guided reading, core-first, with their review verdicts."""
    directory = Path(directory)
    if run_id:
        selected = analysis_document_ids(directory, run_id)
        documents = {doc for doc, _, _ in list_documents(directory, include_removed=True) if doc in selected}
    else:
        documents = {doc for doc, _, _ in list_documents(directory)}
    groups = navigation_groups(directory, set(theme_ids), documents, run_id)
    fits = fit_strength(directory, run_id) if run_id else {}
    reviewed = marks(directory)
    queue = []
    for theme in theme_ids:
        rows = [row for (tid, _doc), rows in groups.items() if tid == theme for row in rows]
        for row in reading_order(rows, fits):
            fit = fits.get((row[1], row[3], row[4]))
            queue.append({"theme_id": theme, "document_id": row[1], "document": row[2], "start": row[3],
                          "end": row[4], "fit": fit, "verdict": reviewed.get((row[1], row[3], row[4]))})
    return queue
