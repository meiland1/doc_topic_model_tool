"""Corpus-wide analysis; Qt-free and injectable for weight-free storage tests.

Flow of one analysis (called from a GUI worker thread):

    analyze()
      prepare_passages()   documents -> Passage list (source offsets + model text)
      validate_settings()
      fit_bertopic()       embed (with on-disk cache) -> UMAP -> HDBSCAN -> keywords
                           (or zero-shot matching + discovery of the rest)
      save_result()        one transaction: run, segments, themes, suggestions

Nothing is written until save_result(), so a cancel or an error at any
point before it leaves the project unchanged. Tests pass `backend=` to
analyze() to skip the real models.
"""
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version, PackageNotFoundError
import json
from pathlib import Path
from uuid import uuid4
from time import perf_counter

from qualitative_themes.analysis.transcript_text import MODEL_TEXT_VERSION
from qualitative_themes.analysis.segment import TOKEN_SAFE_UNITS, model_text, segment_text
from qualitative_themes.storage.database import connect
from qualitative_themes.analysis.settings import AnalysisSettings, effective_parameters, validate_settings, vectorizer_options, small_corpus_warning


class AnalysisCancelled(Exception):
    """Raised by check_cancel(). Workers catch it and report "Cancelled"; nothing was saved."""


@dataclass(frozen=True)
class Passage:
    """One analysis passage. start/end index the stored document text; `text` is
    the cleaned model input (may differ from the source slice, e.g. with
    interviewer turns removed), so never use it for highlighting.
    """
    id: str
    document_id: str
    ordinal: int
    start: int
    end: int
    text: str
    source_hash: str


@dataclass
class TopicOutput:
    """What a backend returns. `assignments[i]` is the topic of passages[i]; -1 is Uncategorized.

    representatives maps topic -> passage ids; scores is empty or one per passage.
    """
    assignments: list[int]
    keywords: dict[int, list[tuple[str, float]]]
    representatives: dict[int, list[str]] = field(default_factory=dict)
    scores: list[float | None] = field(default_factory=list)
    model_info: dict = field(default_factory=dict)


# Saved with each run as segmentation['method'] so a run records how it was split.
SEGMENTATION_METHODS = {'Automatic passages': 'automatic-token-v1', 'Interview passages': 'interview-turns-v1',
                        'Paragraphs': 'paragraphs-v1', 'Whole document': 'whole-document-v1'}


def check_cancel(should_cancel):
    """Raise AnalysisCancelled if the caller asked to stop. Cheap; call it often."""
    if should_cancel and should_cancel():
        raise AnalysisCancelled()


def prepare_passages(directory, unit, document_ids, exclude_interviewer=False, interviewer_labels=None,
                     *, settings=None, diagnostics=None, token_budget=None, should_cancel=None):
    """Passages for analysis. With ``exclude_interviewer``, turns whose speaker label is
    in ``interviewer_labels`` (default: known interviewer labels) are left out of model text.

    Pre: `document_ids` are distinct ids of 'ready' documents.
    Post: passages in document order; empty ones (e.g. a passage that was only
    interviewer text) are dropped. If `diagnostics` is passed it's filled with
    segmentation stats that get saved with the run.
    """
    if not document_ids or len(set(document_ids)) != len(document_ids):
        raise ValueError("Select one or more distinct documents to analyze together.")
    if unit not in SEGMENTATION_METHODS:
        raise ValueError("Choose a supported passage type.")
    if diagnostics is not None or unit in TOKEN_SAFE_UNITS:
        from qualitative_themes.analysis.token_budget import load_token_budget
        try:
            token_budget = token_budget or load_token_budget(settings, directory)
        except ValueError as exc:
            if unit in TOKEN_SAFE_UNITS:
                raise
            if diagnostics is not None:
                diagnostics['token_error'] = str(exc)
    stats = diagnostics if diagnostics is not None else {}
    stats.update({'method': SEGMENTATION_METHODS[unit],
                  'preprocessing': MODEL_TEXT_VERSION, 'forced_splits': 0})
    if unit == 'Interview passages':
        stats.update({'speaker_turns': 0, 'speaker_fallbacks': 0, 'fallback_document_ids': []})
    if token_budget:
        stats['tokenizer'] = token_budget.identity
    with connect(directory) as conn:
        rows = conn.execute("SELECT id, canonical_text FROM documents WHERE extraction_status = 'ready' ORDER BY imported_at, rowid").fetchall()
    rows = [row for row in rows if row[0] in set(document_ids)]
    if len(rows) != len(document_ids):
        raise ValueError("A selected document is not ready or was not found.")
    from qualitative_themes.analysis.speakers import interviewer_ranges
    passages = []
    for doc, text in rows:
        check_cancel(should_cancel)
        excluded = interviewer_ranges(text, interviewer_labels) if exclude_interviewer else ()
        if unit == 'Interview passages':
            from qualitative_themes.analysis.speakers import reliable_transcript_turns
            if not reliable_transcript_turns(text):
                excluded = ()
                stats['fallback_document_ids'].append(doc)
        for i, span in enumerate(segment_text(text, unit, token_budget=token_budget, excluded=excluded,
                                              stats=stats, should_cancel=should_cancel)):
            passages.append(Passage(str(uuid4()), doc, i, span.start_char, span.end_char, model_text(text, span, excluded),
                                    hashlib.sha256(text[span.start_char:span.end_char].encode()).hexdigest()))
    passages = [passage for passage in passages if passage.text]
    if token_budget:
        counts = []
        for passage in passages:
            check_cancel(should_cancel)
            counts.append(token_budget.count(passage.text))
        stats.update({'max_tokens': token_budget.maximum, 'largest_tokens': max(counts, default=0),
                      'smallest_tokens': min(counts, default=0),
                      'oversized': sum(n > token_budget.maximum for n in counts)})
    return passages


def analyze(directory: Path, unit: str, document_ids: list[str], settings=None,
            backend=None, progress=None, should_cancel=None) -> tuple[str, int]:
    """Run a full analysis and save it. Returns (run_id, passage_count).

    Pre: documents imported and ready; `settings` valid for the passage count
    (checked here). Call off the GUI thread: this loads models and can take minutes.
    Post: a 'completed' run with themes and model suggestions, or an exception
    (ValueError with guidance / AnalysisCancelled) and no changes.
    `progress(str)` gets short status lines for the progress dialog.
    """
    settings = settings or AnalysisSettings()
    if unit in TOKEN_SAFE_UNITS:
        settings = replace(settings, allow_truncation=False)
    started_at = datetime.now(timezone.utc).isoformat()
    progress = progress or (lambda message: None)
    check_cancel(should_cancel)
    progress("Preparing exact source passages…")
    preparation_started = perf_counter()
    from qualitative_themes.analysis.speakers import labels_for
    segmentation = {}
    passages = prepare_passages(directory, unit, document_ids, settings.exclude_interviewer, labels_for(settings),
                                settings=settings, diagnostics=segmentation, should_cancel=should_cancel)
    preparation_seconds = perf_counter() - preparation_started
    if settings.exclude_interviewer:
        progress("Leaving interviewer turns out of theme finding…")
    if segmentation.get('speaker_fallbacks'):
        progress(f"Using Auto and retaining all turns in {segmentation['speaker_fallbacks']} document(s) with uncertain speaker boundaries.")
    validate_settings(settings, len(passages))
    warning = small_corpus_warning(settings, len(passages))
    if warning:
        progress(warning)
    check_cancel(should_cancel)
    output = (backend or fit_bertopic)(passages, settings, Path(directory), progress, should_cancel)
    output.model_info['segmentation'] = segmentation
    output.model_info.setdefault('stage_timings', {})['passage_preparation_seconds'] = preparation_seconds
    check_cancel(should_cancel)
    progress("Saving completed analysis…")
    run_id = save_result(directory, unit, document_ids, passages, settings, output, should_cancel, started_at)
    return run_id, len(passages)


def save_result(directory, unit, document_ids, passages, settings, output, should_cancel=None, started_at=None, status="completed"):
    """Write a run and everything it produced in a single transaction.

    Pre: one assignment per passage and representatives that belong to their topic.
    Each passage's source text is re-hashed here, so if a document changed since
    preparation nothing is saved.
    Post: run (status 'completed' or 'proposed' for refinements), segments, one
    theme per topic (generated label "Theme N: top, words"), and suggestions.
    """
    if status not in {"completed", "proposed"}:
        raise ValueError("Unsupported analysis status.")
    if len(output.assignments) != len(passages) or (output.scores and len(output.scores) != len(passages)):
        raise ValueError("Analysis returned an incomplete assignment list; no results were saved.")
    topics = sorted(set(output.assignments))
    if any(type(topic) is not int or topic < -1 for topic in topics):
        raise ValueError("Analysis returned an invalid topic identifier.")
    ids = {p.id for p in passages}
    for topic, reps in output.representatives.items():
        assigned = {p.id for p, assignment in zip(passages, output.assignments) if assignment == topic}
        if not set(reps).issubset(ids & assigned):
            raise ValueError("Representative passages do not match their topic.")
    run_id = str(uuid4())
    mapping = {topic: str(uuid4()) for topic in topics}
    now = datetime.now(timezone.utc).isoformat()
    started_at = started_at or now
    recorded = {**asdict(settings), "analysis_unit": unit, "document_ids": document_ids,
                "parameters": effective_parameters(settings, len(passages))}
    info = {**output.model_info, "topic_theme_map": mapping, "keywords": output.keywords,
            "representative_segment_ids": output.representatives, "provenance": "BERTopic suggestion", "model_text_filter": MODEL_TEXT_VERSION}
    from qualitative_themes.storage.provenance import topic_provenance
    info["analysis_method"] = settings.analysis_method
    info["topic_provenance"] = {str(topic): topic_provenance(recorded, info, topic) for topic in topics}
    info["actual_theme_count"] = len([topic for topic in topics if topic != -1])
    info["uncategorized_passage_count"] = output.assignments.count(-1)
    encoded_settings = json.dumps(recorded, allow_nan=False)
    encoded_info = json.dumps(info, allow_nan=False)
    # One transaction: no failed or cancelled run becomes active in the UI.
    with connect(directory) as conn:
        check_cancel(should_cancel)
        conn.execute("INSERT INTO analysis_runs(id, started_at, completed_at, status, settings_json, model_info_json) VALUES (?, ?, ?, ?, ?, ?)",
                     (run_id, started_at, now, status, encoded_settings, encoded_info))
        sources = {}
        for passage in passages:
            check_cancel(should_cancel)
            if passage.document_id not in sources:
                row = conn.execute("SELECT canonical_text FROM documents WHERE id=?", (passage.document_id,)).fetchone()
                sources[passage.document_id] = row[0] if row else None
            source = sources[passage.document_id]
            if source is None or not 0 <= passage.start < passage.end <= len(source) or hashlib.sha256(source[passage.start:passage.end].encode()).hexdigest() != passage.source_hash:
                raise ValueError("A source passage changed; no analysis was saved.")
            conn.execute("INSERT INTO segments VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (passage.id, passage.document_id, run_id, passage.ordinal, passage.start, passage.end, passage.source_hash))
        for topic, theme_id in mapping.items():
            check_cancel(should_cancel)
            words = [word for word, _ in output.keywords.get(topic, [])[:3]]
            label = "Uncategorized" if topic == -1 else f"Theme {topic + 1}: " + (", ".join(words) or "Unnamed")
            label = output.model_info.get("predefined_topic_labels", {}).get(str(topic), label)
            conn.execute("INSERT INTO themes(id, generated_label, creation_source, status) VALUES (?, ?, 'model', ?)", (theme_id, label, "proposed" if status == "proposed" else "active"))
        for i, (passage, topic) in enumerate(zip(passages, output.assignments)):
            check_cancel(should_cancel)
            score = output.scores[i] if output.scores else None
            conn.execute("INSERT INTO model_suggestions(id, run_id, segment_id, theme_id, bertopic_id, score) VALUES (?, ?, ?, ?, ?, ?)",
                         (str(uuid4()), run_id, passage.id, mapping[topic], topic, score))
        conn.execute("INSERT INTO history_events VALUES (?, ?, ?, ?)",
                     (str(uuid4()), now, "analysis_proposed" if status == "proposed" else "analysis_completed", json.dumps({"run_id": run_id, "passages": len(passages)})))
        check_cancel(should_cancel)
    return run_id


def list_analysis_runs(directory):
    """Completed runs, newest first: (id, completed_at, settings_json)."""
    with connect(directory) as conn:
        return conn.execute("SELECT id, completed_at, settings_json FROM analysis_runs WHERE status='completed' ORDER BY completed_at DESC, rowid DESC").fetchall()


def apply_topic_goal(model, texts, target, progress):
    """Reduce only; translate a researcher goal that excludes Uncategorized.

    Returns the topic count before reduction. Never increases the topic count.
    """
    discovery_count = len(set(model.topics_) - {-1})
    if target is not None and target < discovery_count:
        progress(f"Reducing {discovery_count} discovered themes toward {target}…")
        model.reduce_topics(texts, nr_topics=target + int(-1 in model.topics_))
    return discovery_count


def fit_bertopic(passages, settings, directory, progress, should_cancel):
    """Default backend: embed passages and find topics with BERTopic.

    Pre: validate_settings() passes; the bundled embedding model is available
    (otherwise ValueError with the Check & repair message).
    Post: TopicOutput with model_info recording package versions, model
    identity, timings and cache use. Embeddings are cached under
    <project>/models/embeddings/<model-identity-hash>/ and reused next time.
    Cancellation is checked between steps; HDBSCAN itself can't be interrupted.
    """
    initialization_started = perf_counter()
    validate_settings(settings, len(passages))
    try:
        import numpy as np
        from bertopic import BERTopic
        from umap import UMAP
        from hdbscan import HDBSCAN
        from sklearn.feature_extraction.text import CountVectorizer, ENGLISH_STOP_WORDS
    except ImportError as exc:
        raise ValueError('Install analysis dependencies in your environment: pip install -e ".[analysis,test]"') from exc
    vectorizer = CountVectorizer(**vectorizer_options(settings, ENGLISH_STOP_WORDS))
    if not any(vectorizer.build_analyzer()(p.text) for p in passages):
        raise ValueError("No usable keywords remain. Remove some ignored words or choose text with more vocabulary.")
    progress("Loading local embedding model…")
    timings = {'backend_initialization_seconds': perf_counter() - initialization_started}
    stage_started = perf_counter()
    from qualitative_themes.analysis.encoder import load_encoder
    encoder = load_encoder(settings, directory)
    timings['encoder_load_seconds'] = perf_counter() - stage_started
    stage_started = perf_counter()
    # Paragraph and whole-document passages can exceed the encoder's limit; check them.
    # Test/injected embedding providers may not expose a tokenizer.
    if getattr(encoder, 'tokenizer', None) is not None:
        oversized = sum(len(encoder.tokenizer.encode(p.text, add_special_tokens=True,
                        truncation=False, verbose=False)) > encoder.max_seq_length for p in passages)
        if oversized and not settings.allow_truncation:
            raise ValueError(f'{oversized} passages exceed the model token limit. Choose Automatic passages '
                             'or explicitly allow truncation in Analysis Setup.')
    identity = {"model": settings.embedding_model, "revision": settings.embedding_revision,
                "sentence_transformers": version("sentence-transformers"), "normalize_embeddings": True,
                "max_seq_length": encoder.max_seq_length}
    identity_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    cache = directory/"models"/"embeddings"/identity_hash
    cache.mkdir(parents=True, exist_ok=True)
    vectors = {}
    texts = list(dict.fromkeys(p.text for p in passages))
    missing = []
    timings['token_guard_and_cache_setup_seconds'] = perf_counter() - stage_started
    stage_started = perf_counter()
    for text in texts:
        check_cancel(should_cancel)
        key = hashlib.sha256(text.encode()).hexdigest()
        path = cache/f"{key}.npy"
        try:
            vector = np.load(path, allow_pickle=False)
            if vector.ndim != 1 or len(vector) != encoder.get_sentence_embedding_dimension() or not np.isfinite(vector).all():
                raise ValueError("Invalid cached embedding")
            vectors[text] = vector
        except (OSError, ValueError):
            missing.append(text)
    from qualitative_themes.analysis.encoder import encode_texts
    timings['cache_read_seconds'] = perf_counter() - stage_started
    reused = len(vectors)
    stage_started = perf_counter()
    if len(vectors):
        progress(f"Reusing {len(vectors):,} cached passage embeddings.")
    for text, vector in encode_texts(encoder, missing, settings, progress,
                                     lambda: bool(should_cancel and should_cancel())):
        vectors[text] = vector
        path = cache/f"{hashlib.sha256(text.encode()).hexdigest()}.npy"
        temp = path.with_suffix(f".{uuid4().hex}.tmp")
        with temp.open("wb") as stream:
            np.save(stream, vector, allow_pickle=False)
        temp.replace(path)
    timings['embedding_and_cache_write_seconds'] = perf_counter() - stage_started
    stage_started = perf_counter()
    embeddings = np.stack([vectors[p.text] for p in passages])
    from qualitative_themes.analysis.methods import guided_seed_topics, zero_shot_partition, restore_partition_keywords

    def discover(selected, selected_embeddings, selected_settings):
        check_cancel(should_cancel)
        progress("Finding corpus-wide themes… Cancellation takes effect after clustering finishes.")
        selected_params = effective_parameters(selected_settings, len(selected))
        options = {}
        if selected_settings.analysis_method == "guided":
            options["seed_topic_list"] = guided_seed_topics(selected_settings)
            progress("Guiding discovery with researcher-supplied seed topics…")
        model = BERTopic(embedding_model=encoder, top_n_words=10, calculate_probabilities=False,
            umap_model=UMAP(**selected_params["umap"]), hdbscan_model=HDBSCAN(**selected_params["hdbscan"]),
            vectorizer_model=CountVectorizer(**vectorizer_options(selected_settings, ENGLISH_STOP_WORDS)), **options)
        try:
            model.fit_transform([p.text for p in selected], embeddings=selected_embeddings.copy())
        except ValueError as exc:
            raise ValueError("The corpus could not form themes. Try more varied text, more documents, or Paragraphs. " + str(exc)) from exc
        check_cancel(should_cancel)
        count = apply_topic_goal(model, [p.text for p in selected], selected_settings.requested_theme_count, progress)
        topics = [int(t) for t in model.topics_]
        words = {topic: [(str(word), float(score)) for word, score in (model.get_topic(topic) or [])] for topic in set(topics)}
        reps = {}
        for topic in set(topics):
            examples = set(model.get_representative_docs(topic) or [])
            reps[topic] = [p.id for p, t in zip(selected, topics) if t == topic and p.text in examples][:3]
        scores = model.probabilities_
        return TopicOutput(topics, words, reps, [float(s) for s in scores] if scores is not None else [],
                           {"discovery_theme_count": count})

    check_cancel(should_cancel)
    if settings.analysis_method == "zero_shot":
        progress("Matching passages to predefined topic descriptions…")
        categories = np.stack([vector for _, vector in encode_texts(
            encoder, [text for _, text in settings.topic_definitions], settings, should_cancel=should_cancel)])
        check_cancel(should_cancel)
        topics, method_info = zero_shot_partition(passages, embeddings, categories, settings, discover,
                                                 lambda: check_cancel(should_cancel))
        if method_info["zero_shot"]["residual_discovery_skipped"]:
            progress("Too few unmatched passages for discovery; keeping those passages Uncategorized.")
        # Reuse the existing BERTopic fixed-partition path for c-TF-IDF keywords.
        from qualitative_themes.analysis.refinement import fixed_partition_backend
        represented = fixed_partition_backend(passages, topics, settings, "words", progress, should_cancel)
        keywords, representatives = restore_partition_keywords(passages, topics, represented)
        output = TopicOutput(topics, keywords, representatives, model_info=method_info)
    else:
        output = discover(passages, embeddings, settings)
    check_cancel(should_cancel)
    packages = {}
    for package in ["bertopic", "sentence-transformers", "umap-learn", "hdbscan", "scikit-learn", "numpy", "torch"]:
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            packages[package] = "unknown"
    timings['topic_processing_seconds'] = perf_counter() - stage_started
    output.model_info.update({"packages": packages, "embedding_identity": identity, "device": str(getattr(encoder, 'device', 'unknown')),
        "stage_timings": timings, "embedding_cache": {"reused": reused, "computed": len(missing)},
        "analysis_method": settings.analysis_method,
        "reduction_note": ("Automatic reduction disabled for predefined categories." if settings.analysis_method == "zero_shot" else
                           "Requested count is a reduction goal; creating more themes requires a new discovery run." if settings.requested_theme_count else
                           "Model decided the theme count.")})
    return output
