"""How strongly each passage fits its theme: cosine similarity to the theme's centre.

Uses the passage embeddings the analysis already cached in
``models/embeddings/<identity>``; nothing is re-embedded and no model is loaded.
This is a similarity, not a probability or a confidence. Within each theme,
passages in the top half are labelled *core* and the bottom quarter *borderline*.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from qualitative_themes.storage.database import connect

CACHE_KEY = "fit_strength_v1"


def _passage_model_text(text, start, end, excluded):
    from qualitative_themes.analysis.segment import SegmentRange, model_text
    return model_text(text, SegmentRange(start, end), excluded)


def fit_strength(directory: Path, run_id: str) -> dict[tuple[str, int, int], dict]:
    """``{(document_id, start, end): {"similarity": float, "label": "core"|"borderline"|""}}``.

    Empty when the analysis has no cached embeddings (for example, manual coding only).
    """
    if not run_id:
        return {}
    with connect(directory) as conn:
        row = conn.execute("SELECT settings_json, model_info_json FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return {}
        settings, info = json.loads(row[0]), json.loads(row[1])
        cached = info.get(CACHE_KEY)
        if isinstance(cached, dict):
            return {tuple(json.loads(key)): value for key, value in cached.items()}
        identity = info.get("embedding_identity")
        rows = conn.execute("""SELECT s.document_id, s.start_char, s.end_char, m.theme_id, m.bertopic_id
            FROM model_suggestions m JOIN segments s ON s.id = m.segment_id WHERE m.run_id=?""", (run_id,)).fetchall()
        texts = {doc: text for doc, text in conn.execute(
            "SELECT id, canonical_text FROM documents WHERE id IN (SELECT DISTINCT document_id FROM segments WHERE run_id=?)",
            (run_id,))}
    if not identity or not rows:
        return {}
    try:
        import numpy as np
    except ImportError:
        return {}
    cache = Path(directory) / "models" / "embeddings" / hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    if not cache.is_dir():
        return {}
    excluded = {}
    if settings.get("exclude_interviewer"):
        from qualitative_themes.analysis.settings import settings_from_dict
        from qualitative_themes.analysis.speakers import interviewer_ranges, labels_for
        labels = labels_for(settings_from_dict(settings))
        excluded = {doc: interviewer_ranges(text, labels) for doc, text in texts.items()}
    vectors, groups = {}, {}
    for doc, start, end, theme, topic in rows:
        if topic == -1:
            continue
        text = _passage_model_text(texts[doc], start, end, excluded.get(doc, ()))
        path = cache / (hashlib.sha256(text.encode()).hexdigest() + ".npy")
        try:
            vector = np.load(path, allow_pickle=False)
        except (OSError, ValueError):
            continue
        if vector.ndim != 1 or not np.isfinite(vector).all():
            continue
        vectors[(doc, start, end)] = vector
        groups.setdefault(theme, []).append((doc, start, end))
    result = {}
    for theme, keys in groups.items():
        matrix = np.stack([vectors[k] for k in keys])
        centre = matrix.mean(axis=0)
        norm = np.linalg.norm(centre)
        if not norm:
            continue
        sims = (matrix @ (centre / norm)) / np.maximum(np.linalg.norm(matrix, axis=1), 1e-12)
        high, low = np.quantile(sims, 0.5), np.quantile(sims, 0.25)
        for key, sim in zip(keys, sims):
            label = "core" if sim >= high else ("borderline" if sim <= low and len(keys) >= 4 else "")
            result[key] = {"similarity": round(float(sim), 4), "label": label}
    if result:
        with connect(directory) as conn:
            current = json.loads(conn.execute("SELECT model_info_json FROM analysis_runs WHERE id=?", (run_id,)).fetchone()[0])
            current[CACHE_KEY] = {json.dumps(list(key)): value for key, value in result.items()}
            conn.execute("UPDATE analysis_runs SET model_info_json=? WHERE id=?", (json.dumps(current), run_id))
    return result
