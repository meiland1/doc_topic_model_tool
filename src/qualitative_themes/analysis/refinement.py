"""Reviewable refinements. Proposal creation never edits an accepted analysis."""
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from qualitative_themes.analysis.transcript_text import clean_model_text
from qualitative_themes.analysis.settings import settings_from_dict, validate_settings
from qualitative_themes.analysis.topics import (
    Passage, TopicOutput, check_cancel, fit_bertopic, prepare_passages, save_result,
)
from qualitative_themes.analysis.labels import _protected, label_run
from qualitative_themes.storage.database import connect
from qualitative_themes.storage.coding import analysis_document_ids

OPERATIONS = {
    "names": ("Regenerate machine names", "Uses local Ollama (or keyword fallback). Keeps passage groups. Your names are protected. Names of reused themes apply globally only after Apply."),
    "words": ("Refresh theme words", "Rebuilds keywords and representative evidence from the saved groups. No embedding or clustering step. Keeps passage groups; ignored words affect keywords only."),
    "reduce": ("Propose fewer themes", "Combines saved groups using BERTopic keyword similarity. No embedding or discovery step. Uncategorized stays separate. Combined themes require identity review."),
    "discover": ("Run fresh discovery", "Runs embedding, dimensionality reduction, and clustering with your chosen settings. Cached embeddings are reused when compatible. Groups and passage size may change; more themes are not guaranteed."),
}


def _event(conn, kind, details):
    conn.execute("INSERT INTO history_events VALUES (?, ?, ?, ?)",
                 (str(uuid4()), datetime.now(timezone.utc).isoformat(), kind, json.dumps(details)))


def _fingerprint(conn, source_id):
    # Proposed themes are excluded; edits to real research decisions invalidate review.
    data = {
        "run": conn.execute("SELECT settings_json,model_info_json,status FROM analysis_runs WHERE id=?", (source_id,)).fetchall(),
        "suggestions": conn.execute("SELECT id,theme_id,bertopic_id FROM model_suggestions WHERE run_id=? ORDER BY id", (source_id,)).fetchall(),
        "themes": conn.execute("SELECT * FROM themes WHERE status NOT IN ('proposed','discarded') ORDER BY id").fetchall(),
        "codings": conn.execute("SELECT * FROM codings ORDER BY id").fetchall(),
        "notes": conn.execute("SELECT * FROM notes ORDER BY id").fetchall(),
        "sources": conn.execute("SELECT id,source_hash,canonical_text,extraction_status FROM documents ORDER BY id").fetchall(),
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def load_source(directory, run_id):
    with connect(directory) as conn:
        row = conn.execute("SELECT settings_json,model_info_json FROM analysis_runs WHERE id=? AND status='completed'", (run_id,)).fetchone()
        if row is None:
            raise ValueError("Select a completed analysis to refine.")
        recorded, info = map(json.loads, row)
        rows = conn.execute("""SELECT s.document_id,s.ordinal,s.start_char,s.end_char,s.text_hash,
            d.canonical_text,m.theme_id,m.bertopic_id,m.score
            FROM model_suggestions m JOIN segments s ON s.id=m.segment_id
            JOIN documents d ON d.id=s.document_id WHERE m.run_id=?
            ORDER BY d.imported_at,d.rowid,s.ordinal""", (run_id,)).fetchall()
        themes = {tid: {"label": label, "user_label": user, "generated_label": generated,
                        "description": description, "protected": bool(_protected(conn, tid))}
                  for tid, label, user, generated, description in conn.execute(
                      "SELECT id,coalesce(user_label,generated_label,'Unnamed'),user_label,generated_label,description FROM themes WHERE status='active'")}
        fingerprint = _fingerprint(conn, run_id)
    if not rows:
        raise ValueError("This analysis has no saved model passages.")
    # Respect researcher merges: actual theme UUIDs define the saved partition.
    ordinary = {r[6] for r in rows if r[7] != -1}
    groups = {}
    for row in rows:
        theme = row[6]
        if theme not in themes:
            raise ValueError("A saved theme is missing; the analysis cannot be refined.")
        if theme not in groups:
            groups[theme] = len([n for n in groups.values() if n != -1]) if theme in ordinary else -1
    passages = [Passage(str(uuid4()), doc, ordinal, start, end,
                        clean_model_text(text[start:end]), digest)
                for doc, ordinal, start, end, digest, text, *_ in rows]
    assignments = [groups[r[6]] for r in rows]
    keywords, representatives = {}, {}
    for theme, topic in groups.items():
        original_topics = {str(r[7]) for r in rows if r[6] == theme}
        words = {}
        for original in sorted(original_topics, key=int):
            for word, score in info.get("keywords", {}).get(original, []):
                words[word] = max(score, words.get(word, 0))
        keywords[topic] = sorted(words.items(), key=lambda r: (-r[1], r[0]))[:10]
        representatives[topic] = [p.id for p, t in zip(passages, assignments) if t == topic][:3]
    from qualitative_themes.storage.provenance import theme_provenance_from_connection
    with connect(directory) as conn:
        provenance = {topic: theme_provenance_from_connection(conn, theme, run_id) for theme, topic in groups.items()}
    return {"provenance": provenance, "settings": settings_from_dict(recorded), "unit": recorded.get("analysis_unit", "Paragraphs"),
            "documents": sorted(analysis_document_ids(directory, run_id)), "passages": passages,
            "output": TopicOutput(assignments, keywords, representatives, [r[8] for r in rows]),
            "groups": {topic: theme for theme, topic in groups.items()}, "themes": themes,
            "fingerprint": fingerprint, "model_info": info}


def list_proposals(directory):
    with connect(directory) as conn:
        return conn.execute("SELECT id,started_at,model_info_json FROM analysis_runs WHERE status='proposed' ORDER BY started_at DESC,rowid DESC").fetchall()


def fixed_partition_backend(passages, assignments, settings, operation, progress, should_cancel):
    """BERTopic's manual modeling path restores groups; reduction uses c-TF-IDF.

    Placeholder embeddings are never used for similarity. BaseCluster and
    BaseDimensionalityReduction bypass discovery; c-TF-IDF drives reduction.
    """
    try:
        import numpy as np
        from bertopic import BERTopic
        from bertopic.cluster import BaseCluster
        from bertopic.dimensionality import BaseDimensionalityReduction
        from sklearn.feature_extraction.text import CountVectorizer, ENGLISH_STOP_WORDS
    except ImportError as exc:
        raise ValueError('Install analysis dependencies: pip install -e ".[analysis,test]"') from exc
    from qualitative_themes.analysis.settings import vectorizer_options
    texts = [p.text for p in passages]
    vectorizer = CountVectorizer(**vectorizer_options(settings, ENGLISH_STOP_WORDS))
    if not any(vectorizer.build_analyzer()(text) for text in texts):
        raise ValueError("No usable keywords remain. Remove some ignored words.")
    check_cancel(should_cancel)
    progress("Rebuilding theme words from saved groups; no discovery clustering…")
    model = BERTopic(embedding_model=None, umap_model=BaseDimensionalityReduction(),
                    hdbscan_model=BaseCluster(), vectorizer_model=vectorizer,
                    top_n_words=10, calculate_probabilities=False)
    model.fit_transform(texts, embeddings=np.zeros((len(passages), 1)), y=np.asarray(assignments))
    check_cancel(should_cancel)
    # BERTopic may renumber groups by frequency. Verify actual membership.
    restored = {}
    for previous, current in zip(assignments, model.topics_):
        if previous in restored and restored[previous] != current:
            raise ValueError("Saved theme groups changed unexpectedly; no proposal saved.")
        restored[previous] = current
    if len(set(restored.values())) != len(restored) or any((a == -1) != (b == -1) for a, b in restored.items()):
        raise ValueError("Saved theme partition could not be restored safely.")
    if operation == "reduce":
        progress(f"Combining saved groups toward {settings.requested_theme_count} themes…")
        model.reduce_topics(texts, nr_topics=settings.requested_theme_count + int(-1 in assignments), use_ctfidf=True)
    check_cancel(should_cancel)
    topics = [int(t) for t in model.topics_]
    keywords = {topic: [(str(word), float(score)) for word, score in (model.get_topic(topic) or [])]
                for topic in set(topics)}
    representatives = {}
    for topic in set(topics):
        examples = set(model.get_representative_docs(topic) or [])
        representatives[topic] = [p.id for p, t in zip(passages, topics)
                                   if t == topic and p.text in examples][:3]
    return TopicOutput(topics, keywords, representatives,
                       model_info={"refinement_backend": "BERTopic manual groups; c-TF-IDF reduction",
                                   "embedding_step": False, "discovery_step": False})


def propose_refinement(directory, source_id, operation, settings=None, unit=None,
                       progress=None, should_cancel=None, discovery_backend=None,
                       representation_backend=None, client_factory=None):
    if operation not in OPERATIONS:
        raise ValueError("Choose a supported refinement operation.")
    progress = progress or (lambda message: None)
    check_cancel(should_cancel)
    source = load_source(directory, source_id)
    settings = settings or source["settings"]
    unit = unit or source["unit"]
    if operation == "reduce" and source["settings"].analysis_method == "zero_shot":
        raise ValueError("Automatic reduction is disabled for zero-shot analyses. Merge themes explicitly in Research Workspace instead.")
    if operation != "discover" and unit != source["unit"]:
        raise ValueError("Changing passage size requires fresh discovery.")
    if operation != "discover":
        # Only keyword/naming options may change without fresh discovery.
        permitted = {"ollama_model", "name_themes"}
        if operation in {"words", "reduce"}:
            permitted |= {"ignored_words", "ngram_range"}
        if operation == "reduce":
            permitted.add("requested_theme_count")
        from dataclasses import fields
        if any(getattr(settings, f.name) != getattr(source["settings"], f.name)
               for f in fields(settings) if f.name not in permitted):
            raise ValueError("Changing clustering settings requires fresh discovery.")
    passages = source["passages"]
    output = source["output"]
    if operation == "discover":
        passages = prepare_passages(directory, unit, source["documents"])
        validate_settings(settings, len(passages))
        output = (discovery_backend or fit_bertopic)(passages, settings, Path(directory), progress, should_cancel)
    elif operation in {"words", "reduce"}:
        if settings.ngram_range not in {(1, 1), (1, 2)} or len(settings.ignored_words) > 500:
            raise ValueError("Choose supported keyword settings.")
        if operation == "reduce":
            count = len(set(output.assignments) - {-1})
            target = settings.requested_theme_count
            if type(target) is not int or not 1 <= target < count:
                raise ValueError(f"Choose fewer than the {count} current themes, excluding Uncategorized.")
        output = (representation_backend or fixed_partition_backend)(
            passages, output.assignments, settings, operation, progress, should_cancel)
        if len(output.assignments) != len(passages):
            raise ValueError("Refinement returned incomplete memberships; no proposal saved.")
        if operation == "words":
            old_groups, new_groups = defaultdict(set), defaultdict(set)
            for index, (old, new) in enumerate(zip(source["output"].assignments, output.assignments)):
                old_groups[old].add(index)
                new_groups[new].add(index)
            if {frozenset(v) for v in old_groups.values()} != {frozenset(v) for v in new_groups.values()}:
                raise ValueError("Keyword refresh changed memberships; no proposal saved.")
        if operation == "reduce":
            destinations = defaultdict(set)
            for old, new in zip(source["output"].assignments, output.assignments):
                destinations[old].add(new)
            if any(len(group) != 1 for group in destinations.values()) or len(set(output.assignments) - {-1}) >= count:
                raise ValueError("Reduction must combine whole saved groups into fewer themes.")
        if any((a == -1) != (b == -1) for a, b in zip(source["output"].assignments, output.assignments)):
            raise ValueError("This refinement changed Uncategorized memberships unexpectedly.")
    if operation != "discover":
        from qualitative_themes.storage.provenance import combine_provenance
        new_origins = {}
        for previous, current in zip(source["output"].assignments, output.assignments):
            new_origins.setdefault(str(current), set()).add(previous)
        output.model_info["topic_provenance"] = {key: combine_provenance([source["provenance"][previous] for previous in entries]) for key, entries in new_origins.items()}
        # Protect the current researcher-visible label, including deliberate merges/renames.
        category_labels = {}
        for previous, current in zip(source["output"].assignments, output.assignments):
            if source["provenance"][previous]["supplied_names"]:
                theme = source["groups"][previous]
                category_labels[str(current)] = source["themes"][theme]["label"]
        output.model_info["predefined_topic_labels"] = category_labels
        if source["settings"].analysis_method == "zero_shot":
            diagnostics = dict(source["model_info"].get("zero_shot", {}))
            diagnostics.pop("best_similarity_by_segment", None)
            output.model_info["zero_shot"] = diagnostics
            output.model_info["initial_matching_run_id"] = source["model_info"].get("initial_matching_run_id", source_id)
    check_cancel(should_cancel)
    output.model_info.update({"parent_run_id": source_id, "refinement_operation": operation,
                              "source_fingerprint": source["fingerprint"],
                              "source_model_identity": source["model_info"].get("embedding_identity"),
                              "reconciliation_policy": "Exact shared ranges, one-to-one identical groups auto-match; all other identity reuse reviewed."})
    with connect(directory) as conn:
        if _fingerprint(conn, source_id) != source["fingerprint"]:
            raise ValueError("Research decisions changed during refinement. Please prepare a new proposal.")
    proposal = save_result(directory, unit, source["documents"], passages, settings, output,
                           should_cancel=should_cancel, status="proposed")
    try:
        review = review_proposal(directory, proposal)
        with connect(directory) as conn:
            # Copy protected labels to proposal objects, never modify source themes.
            for item in review["themes"]:
                old_id = item["recommended"]
                if old_id:
                    old = source["themes"][old_id]
                    if operation == "names" or (operation in {"words", "reduce"} and not settings.name_themes) or old["protected"]:
                        conn.execute("UPDATE themes SET generated_label=?,user_label=?,description=? WHERE id=?",
                                     (old["generated_label"], old["user_label"], old["description"], item["theme_id"]))
                    if old["protected"] and old["user_label"] is None:
                        conn.execute("UPDATE themes SET user_label=? WHERE id=?", (old["label"], item["theme_id"]))
        if operation == "names" or settings.name_themes:
            label_run(directory, proposal, settings.ollama_model, progress, should_cancel, client_factory, proposal=True)
        check_cancel(should_cancel)
    except Exception:
        cancel_proposal(directory, proposal)
        raise
    return proposal, len(passages)


def _groups(conn, run_id):
    rows = conn.execute("""SELECT m.theme_id,m.bertopic_id,s.document_id,s.start_char,s.end_char
        FROM model_suggestions m JOIN segments s ON s.id=m.segment_id WHERE m.run_id=?""", (run_id,)).fetchall()
    groups, ordinary = defaultdict(set), set()
    for theme, topic, doc, start, end in rows:
        groups[theme].add((doc, start, end))
        if topic != -1:
            ordinary.add(theme)
    # A human merge into a regular theme is respected in the saved partition.
    outliers = {r for theme, ranges in groups.items() if theme not in ordinary for r in ranges}
    return groups, outliers


def review_proposal(directory, proposal_id):
    with connect(directory) as conn:
        row = conn.execute("SELECT model_info_json FROM analysis_runs WHERE id=? AND status='proposed'", (proposal_id,)).fetchone()
        if not row:
            raise ValueError("This proposal is no longer awaiting review.")
        info = json.loads(row[0])
        source_id = info["parent_run_id"]
        old, old_outliers = _groups(conn, source_id)
        new, new_outliers = _groups(conn, proposal_id)
        labels = {tid: label for tid, label in conn.execute("SELECT id,coalesce(user_label,generated_label,'Unnamed') FROM themes")}
        outlier_themes = {theme for topic, theme in info["topic_theme_map"].items() if int(topic) == -1}
        old_outlier_themes = {tid for tid, ranges in old.items() if ranges <= old_outliers}
        items = []
        for tid, ranges in new.items():
            candidates = []
            for oid, previous in old.items():
                if (tid in outlier_themes) != (oid in old_outlier_themes):
                    continue
                overlap = len(ranges & previous)
                if overlap:
                    candidates.append({"theme_id": oid, "label": labels[oid], "shared": overlap,
                                       "old_count": len(previous), "exact": previous == ranges})
            candidates.sort(key=lambda c: (-c["shared"], c["label"], c["theme_id"]))
            exact = [c["theme_id"] for c in candidates if c["exact"]]
            recommended = exact[0] if len(exact) == 1 else None
            items.append({"theme_id": tid, "label": labels[tid], "count": len(ranges),
                          "candidates": candidates, "recommended": recommended,
                          "ambiguous": bool(candidates) and recommended is None,
                          "outlier": tid in outlier_themes})
        recommended = {item["theme_id"]: item["recommended"] for item in items}
        old_by_range = {r: tid for tid, ranges in old.items() for r in ranges}
        new_by_range = {r: tid for tid, ranges in new.items() for r in ranges}
        shared = set(old_by_range) & set(new_by_range)
        changed = sum(old_by_range[r] != recommended[new_by_range[r]] for r in shared)
        retired = [labels[oid] for oid in old if oid not in set(recommended.values())]
        human = []
        for oid in old:
            label, count, note_count, user = conn.execute("""SELECT coalesce(t.user_label,t.generated_label),
                (SELECT count(*) FROM codings WHERE theme_id=t.id),
                (SELECT count(*) FROM notes WHERE theme_id=t.id OR coding_id IN (SELECT id FROM codings WHERE theme_id=t.id)),
                t.user_label FROM themes t WHERE t.id=?""", (oid,)).fetchone()
            if count or note_count or user is not None:
                human.append({"theme_id": oid, "label": label, "codings": count, "notes": note_count,
                              "matched": oid in set(recommended.values())})
        return {"proposal_id": proposal_id, "source_id": source_id, "operation": info["refinement_operation"],
                "themes": items, "old_theme_count": len(old) - len(old_outlier_themes),
                "new_theme_count": len(new) - len(outlier_themes), "retired": retired,
                "changed_memberships": changed, "shared_passages": len(shared),
                "new_passages": len(set(new_by_range) - shared), "retired_passages": len(set(old_by_range) - shared),
                "old_uncategorized": len(old_outliers), "new_uncategorized": len(new_outliers),
                "old_labels": {tid: labels[tid] for tid in old},
                "human_decisions": human, "stale": _fingerprint(conn, source_id) != info["source_fingerprint"]}


def proposal_evidence(directory, proposal_id, theme_id):
    """Canonical evidence and exact previous memberships for a review row."""
    with connect(directory) as conn:
        row = conn.execute("SELECT model_info_json FROM analysis_runs WHERE id=? AND status='proposed'", (proposal_id,)).fetchone()
        if row is None:
            raise ValueError("This proposal is no longer awaiting review.")
        source_id = json.loads(row[0])["parent_run_id"]
        old, _ = _groups(conn, source_id)
        labels = {tid: label for tid, label in conn.execute("SELECT id,coalesce(user_label,generated_label,'Unnamed') FROM themes")}
        previous = {key: labels[tid] for tid, ranges in old.items() for key in ranges}
        rows = conn.execute("""SELECT d.original_name,s.document_id,s.start_char,s.end_char,d.canonical_text
            FROM model_suggestions m JOIN segments s ON s.id=m.segment_id JOIN documents d ON d.id=s.document_id
            WHERE m.run_id=? AND m.theme_id=? ORDER BY d.imported_at,d.rowid,s.ordinal""", (proposal_id, theme_id)).fetchall()
    return [(name, start, end, text[start:end], previous.get((doc, start, end), "New passage range"))
            for name, doc, start, end, text in rows]


def apply_proposal(directory, proposal_id, mappings):
    """Reuse reviewed identities atomically; manual records are never rewritten."""
    with connect(directory) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT model_info_json FROM analysis_runs WHERE id=? AND status='proposed'", (proposal_id,)).fetchone()
        if row is None:
            raise ValueError("This proposal is no longer awaiting review.")
        info = json.loads(row[0])
        source = info["parent_run_id"]
        if _fingerprint(conn, source) != info["source_fingerprint"]:
            raise ValueError("Research decisions changed since preview. Cancel and prepare a new proposal.")
        old, old_outliers = _groups(conn, source)
        new, new_outliers = _groups(conn, proposal_id)
        if set(mappings) != set(new):
            raise ValueError("Review every proposed theme before applying.")
        chosen = [v for v in mappings.values() if v is not None]
        if len(chosen) != len(set(chosen)):
            raise ValueError("An earlier theme identity can be reused only once. Keep separate identities for split themes.")
        for tid, oid in mappings.items():
            if oid is None:
                continue
            if oid not in old or not (new[tid] & old[oid]):
                raise ValueError("Theme identity reuse needs actual shared source passages.")
            if (new[tid] <= new_outliers) != (old[oid] <= old_outliers):
                raise ValueError("Uncategorized cannot inherit a regular theme identity.")
            if conn.execute("SELECT status FROM themes WHERE id=?", (oid,)).fetchone()[0] != "active":
                raise ValueError("An earlier theme was merged; prepare a new proposal.")
            proposed = conn.execute("SELECT generated_label,description FROM themes WHERE id=?", (tid,)).fetchone()
            if not _protected(conn, oid):
                conn.execute("UPDATE themes SET generated_label=?,description=? WHERE id=?", (*proposed, oid))
            conn.execute("UPDATE model_suggestions SET theme_id=? WHERE run_id=? AND theme_id=?", (oid, proposal_id, tid))
            conn.execute("UPDATE themes SET status='discarded' WHERE id=?", (tid,))
        conn.execute("UPDATE themes SET status='active' WHERE status='proposed' AND id IN (SELECT theme_id FROM model_suggestions WHERE run_id=?)", (proposal_id,))
        info["topic_theme_map"] = {topic: mappings[tid] or tid for topic, tid in info["topic_theme_map"].items()}
        for attempt in info.get("labeling_attempts", []):
            for record in attempt.get("records", []):
                record["theme_id"] = mappings.get(record["theme_id"]) or record["theme_id"]
        info["reviewed_identity_map"] = mappings
        info["applied_at"] = datetime.now(timezone.utc).isoformat()
        conn.execute("UPDATE analysis_runs SET status='completed',completed_at=?,model_info_json=? WHERE id=?", (info["applied_at"], json.dumps(info), proposal_id))
        _event(conn, "refinement_applied", {"run_id": proposal_id, "parent_run_id": source,
                                          "operation": info["refinement_operation"], "identity_map": mappings})
    return proposal_id


def cancel_proposal(directory, proposal_id):
    with connect(directory) as conn:
        row = conn.execute("SELECT status FROM analysis_runs WHERE id=?", (proposal_id,)).fetchone()
        if row is None or row[0] != "proposed":
            return
        conn.execute("UPDATE analysis_runs SET status='cancelled' WHERE id=?", (proposal_id,))
        conn.execute("UPDATE themes SET status='discarded' WHERE status='proposed' AND id IN (SELECT theme_id FROM model_suggestions WHERE run_id=?)", (proposal_id,))
        _event(conn, "refinement_cancelled", {"run_id": proposal_id})
