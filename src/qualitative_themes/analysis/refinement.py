"""Reviewable refinements. Proposal creation never edits an accepted analysis.

Flow:
    propose_refinement()  builds a new run with status 'proposed' from a
                          completed one (rename / new keywords / fewer
                          themes / fresh discovery)
    review_proposal()     shows how proposed groups overlap the old themes
    apply_proposal()      researcher maps each proposed theme to an old
                          identity (or a new one); the run becomes 'completed'
    cancel_proposal()     run becomes 'cancelled', proposed themes 'discarded'

A fingerprint of the source run and all research decisions is stored with
the proposal. If anything changes before Apply, apply refuses and asks for
a new proposal. review, apply and cancel are in proposals.py.
"""
from collections import defaultdict
from functools import partial
from dataclasses import replace
from qualitative_themes.analysis.segment import TOKEN_SAFE_UNITS
import json
from pathlib import Path
from uuid import uuid4

from qualitative_themes.analysis.transcript_text import clean_model_text
from qualitative_themes.analysis.settings import settings_from_dict, validate_settings
from qualitative_themes.analysis.topics import (
    Passage, TopicOutput, check_cancel, fit_bertopic, prepare_passages, save_result,
)
from qualitative_themes.analysis.labels import _protected, label_run
# Reviewing and applying a proposal live in proposals.py; imported here so callers
# keep one place to import refinement from.
from qualitative_themes.analysis.proposals import (  # noqa: F401
    _event, _fingerprint, apply_proposal, cancel_proposal, proposal_evidence, review_proposal,
)
from qualitative_themes.storage.database import connect
from qualitative_themes.storage.coding import analysis_document_ids

OPERATIONS = {
    "names": ("Suggest new theme names",
              "Asks the AI on this computer for new names (or keeps keyword names if naming isn't available). "
              "Passages stay in the same themes, and names you gave a theme are never replaced."),
    "words": ("Update keywords",
              "Works out each theme's keywords and example passages again. Passages stay in the same themes; "
              "words to ignore only change the keywords."),
    "reduce": ("Combine similar themes",
               "Joins themes whose keywords overlap. Your coding is kept, \"Not in any theme\" stays separate, "
               "and you review each change before it's applied."),
    "discover": ("Find themes again with new settings",
                 "Re-reads all passages and finds themes again. Takes longer. Themes may change, and you may not "
                 "get more of them."),
}


def load_source(directory, run_id):
    """Rebuild passages and the current partition of a completed run (respecting merges)."""
    with connect(directory) as conn:
        row = conn.execute(
            "SELECT settings_json,model_info_json FROM analysis_runs WHERE id=? AND status='completed'", (run_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Select a completed analysis to refine.")
        recorded, info = map(json.loads, row)
        rows = conn.execute(
            """SELECT s.document_id,s.ordinal,s.start_char,s.end_char,s.text_hash,
            d.canonical_text,m.theme_id,m.bertopic_id,m.score
            FROM model_suggestions m JOIN segments s ON s.id=m.segment_id
            JOIN documents d ON d.id=s.document_id WHERE m.run_id=?
            ORDER BY d.imported_at,d.rowid,s.ordinal""",
            (run_id,),
        ).fetchall()
        themes = {
            tid: {
                "label": label,
                "user_label": user,
                "generated_label": generated,
                "description": description,
                "protected": bool(_protected(conn, tid)),
            }
            for tid, label, user, generated, description in conn.execute(
                "SELECT id,coalesce(user_label,generated_label,'Unnamed'),user_label,generated_label,description FROM"
                " themes WHERE status='active'"
            )
        }
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
    exclude = bool(recorded.get("exclude_interviewer"))
    excluded_by_doc = {}
    if exclude:
        from qualitative_themes.analysis.speakers import interviewer_ranges, labels_for

        labels = labels_for(settings_from_dict(recorded))
        for doc, _o, _s, _e, _d, text, *_ in rows:
            if doc not in excluded_by_doc:
                excluded_by_doc[doc] = interviewer_ranges(text, labels)
    from qualitative_themes.analysis.speakers import without_ranges

    def model_input(doc, text, start, end):
        source_text = without_ranges(text, start, end, excluded_by_doc[doc]) if exclude else text[start:end]
        return clean_model_text(source_text)

    passages = [
        Passage(str(uuid4()), doc, ordinal, start, end, model_input(doc, text, start, end), digest)
        for doc, ordinal, start, end, digest, text, *_ in rows
    ]
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
    return {
        "provenance": provenance,
        "settings": settings_from_dict(recorded),
        "unit": recorded.get("analysis_unit", "Paragraphs"),
        "documents": sorted(analysis_document_ids(directory, run_id)),
        "passages": passages,
        "output": TopicOutput(assignments, keywords, representatives, [r[8] for r in rows]),
        "groups": {topic: theme for theme, topic in groups.items()},
        "themes": themes,
        "fingerprint": fingerprint,
        "model_info": info,
    }


def list_proposals(directory):
    """Runs awaiting review: (id, started_at, model_info_json), newest first."""
    with connect(directory) as conn:
        return conn.execute(
            "SELECT id,started_at,model_info_json FROM analysis_runs WHERE status='proposed' ORDER BY started_at"
            " DESC,rowid DESC"
        ).fetchall()


def fixed_partition_backend(
    passages, assignments, settings, operation, progress, should_cancel, *, encoder=None, directory=None
):
    """BERTopic's manual modeling path restores groups; reduction uses c-TF-IDF.

    Placeholder embeddings are never used for similarity. BaseCluster and
    BaseDimensionalityReduction bypass discovery; c-TF-IDF drives reduction.
    Key words follow the run's key word settings; the "More varied" and "Based
    on meaning" styles embed words, so they use `encoder` (loaded from
    `directory`'s settings if not given).
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
    from qualitative_themes.analysis.topics import keyword_models

    keywords = keyword_models(settings)
    if keywords["representation_model"] is not None and encoder is None:
        if directory is None:
            keywords["representation_model"] = None  # nothing to embed words with; plain counts
        else:
            from qualitative_themes.analysis.encoder import load_encoder

            encoder = load_encoder(settings, directory)
    model = BERTopic(
        embedding_model=encoder,
        umap_model=BaseDimensionalityReduction(),
        hdbscan_model=BaseCluster(),
        vectorizer_model=vectorizer,
        calculate_probabilities=False,
        **keywords,
    )
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
    keywords = {
        topic: [(str(word), float(score)) for word, score in (model.get_topic(topic) or [])] for topic in set(topics)
    }
    representatives = {}
    for topic in set(topics):
        examples = set(model.get_representative_docs(topic) or [])
        representatives[topic] = [p.id for p, t in zip(passages, topics) if t == topic and p.text in examples][:3]
    return TopicOutput(
        topics,
        keywords,
        representatives,
        model_info={
            "refinement_backend": "BERTopic manual groups; c-TF-IDF reduction",
            "embedding_step": False,
            "discovery_step": False,
        },
    )


def propose_refinement(
    directory,
    source_id,
    operation,
    settings=None,
    unit=None,
    progress=None,
    should_cancel=None,
    discovery_backend=None,
    representation_backend=None,
    client_factory=None,
):
    """Create a 'proposed' run from `source_id` without touching it.

    Pre: `operation` is a key of OPERATIONS. Only 'discover' may change the
    passage size or clustering settings.
    Post: returns (proposal_run_id, passage_count). Slow for 'discover' (full
    embedding and clustering): run in a worker thread.
    """
    if operation not in OPERATIONS:
        raise ValueError("Choose a supported refinement operation.")
    progress = progress or (lambda message: None)
    check_cancel(should_cancel)
    source = load_source(directory, source_id)
    settings = settings or source["settings"]
    unit = unit or source["unit"]
    if operation == "discover" and unit in TOKEN_SAFE_UNITS:
        settings = replace(settings, allow_truncation=False)
    if operation == "reduce" and source["settings"].analysis_method == "zero_shot":
        raise ValueError(
            "Combining themes is turned off when you started from your own list of themes. "
            "Merge themes yourself in Read & code instead."
        )
    if operation != "discover" and unit != source["unit"]:
        raise ValueError("Changing passage size requires fresh discovery.")
    if operation != "discover":
        # Only keyword/naming options may change without fresh discovery.
        permitted = {"ollama_model", "name_themes"}
        if operation in {"words", "reduce"}:
            permitted |= {"ignored_words", "ngram_range", "keyword_style", "play_down_common_words", "top_n_words"}
        if operation == "reduce":
            permitted.add("requested_theme_count")
        from dataclasses import fields

        if any(
            getattr(settings, f.name) != getattr(source["settings"], f.name)
            for f in fields(settings)
            if f.name not in permitted
        ):
            raise ValueError("Changing clustering settings requires fresh discovery.")
    passages = source["passages"]
    output = source["output"]
    if operation == "discover":
        from qualitative_themes.analysis.speakers import labels_for

        segmentation = {}
        passages = prepare_passages(
            directory,
            unit,
            source["documents"],
            settings.exclude_interviewer,
            labels_for(settings),
            settings=settings,
            diagnostics=segmentation,
            should_cancel=should_cancel,
        )
        validate_settings(settings, len(passages))
        output = (discovery_backend or fit_bertopic)(passages, settings, Path(directory), progress, should_cancel)
        output.model_info["segmentation"] = segmentation
    elif operation in {"words", "reduce"}:
        if settings.ngram_range not in {(1, 1), (1, 2)} or len(settings.ignored_words) > 500:
            raise ValueError("Choose supported keyword settings.")
        validate_settings(settings, len(passages))
        if operation == "reduce":
            count = len(set(output.assignments) - {-1})
            target = settings.requested_theme_count
            if type(target) is not int or not 1 <= target < count:
                raise ValueError(f'Choose fewer than the {count} current themes (not counting "Not in any theme").')
        backend = representation_backend or partial(fixed_partition_backend, directory=Path(directory))
        output = backend(passages, output.assignments, settings, operation, progress, should_cancel)
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
            raise ValueError('This change moved passages in or out of "Not in any theme" unexpectedly.')
    if operation != "discover":
        output.model_info["segmentation"] = source["model_info"].get("segmentation", {})
        from qualitative_themes.storage.provenance import combine_provenance

        new_origins = {}
        for previous, current in zip(source["output"].assignments, output.assignments):
            new_origins.setdefault(str(current), set()).add(previous)
        output.model_info["topic_provenance"] = {
            key: combine_provenance([source["provenance"][previous] for previous in entries])
            for key, entries in new_origins.items()
        }
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
            output.model_info["initial_matching_run_id"] = source["model_info"].get(
                "initial_matching_run_id", source_id
            )
    check_cancel(should_cancel)
    output.model_info.update(
        {
            "parent_run_id": source_id,
            "refinement_operation": operation,
            "source_fingerprint": source["fingerprint"],
            "source_model_identity": source["model_info"].get("embedding_identity"),
            "reconciliation_policy": "Exact shared ranges, one-to-one identical groups auto-match; all other identity"
            " reuse reviewed.",
        }
    )
    with connect(directory) as conn:
        if _fingerprint(conn, source_id) != source["fingerprint"]:
            raise ValueError("Research decisions changed during refinement. Please prepare a new proposal.")
    proposal = save_result(
        directory, unit, source["documents"], passages, settings, output, should_cancel=should_cancel, status="proposed"
    )
    try:
        review = review_proposal(directory, proposal)
        with connect(directory) as conn:
            # Copy protected labels to proposal objects, never modify source themes.
            for item in review["themes"]:
                old_id = item["recommended"]
                if old_id:
                    old = source["themes"][old_id]
                    keeps_old_name = (
                        operation == "names"
                        or old["protected"]
                        or (operation in {"words", "reduce"} and not settings.name_themes)
                    )
                    if keeps_old_name:
                        conn.execute(
                            "UPDATE themes SET generated_label=?,user_label=?,description=? WHERE id=?",
                            (old["generated_label"], old["user_label"], old["description"], item["theme_id"]),
                        )
                    if old["protected"] and old["user_label"] is None:
                        conn.execute("UPDATE themes SET user_label=? WHERE id=?", (old["label"], item["theme_id"]))
        if operation == "names" or settings.name_themes:
            label_run(
                directory, proposal, settings.ollama_model, progress, should_cancel, client_factory, proposal=True
            )
        check_cancel(should_cancel)
    except Exception:
        cancel_proposal(directory, proposal)
        raise
    return proposal, len(passages)
