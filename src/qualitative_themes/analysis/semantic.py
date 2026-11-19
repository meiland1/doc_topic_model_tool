"""Experimental adjacent-sentence boundaries using the bundled local encoder."""
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
from uuid import uuid4


def sentence_vectors(texts, directory=None, settings=None, should_cancel=None, stats=None):
    """Embed sentences for semantic boundary detection, using the project's embedding cache."""
    import numpy as np
    from qualitative_themes.analysis.encoder import load_encoder, encode_texts
    from qualitative_themes.analysis.settings import AnalysisSettings
    from qualitative_themes.analysis.topics import check_cancel
    settings = settings or AnalysisSettings()
    check_cancel(should_cancel)
    encoder = load_encoder(settings, directory)
    identity = {'model': settings.embedding_model, 'revision': settings.embedding_revision,
                'sentence_transformers': version('sentence-transformers'), 'normalize_embeddings': True,
                'max_seq_length': encoder.max_seq_length}
    cache = None
    if directory is not None:
        cache = Path(directory) / 'models' / 'embeddings' / hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        cache.mkdir(parents=True, exist_ok=True)
    def validate(vector):
        vector = np.asarray(vector)
        if (vector.ndim != 1 or len(vector) != encoder.get_sentence_embedding_dimension()
                or not np.isfinite(vector).all() or np.linalg.norm(vector) == 0):
            raise ValueError('Invalid cached sentence embedding')
        return vector
    vectors, missing = {}, []
    for text in dict.fromkeys(texts):
        check_cancel(should_cancel)
        if getattr(encoder, 'tokenizer', None) is not None and len(encoder.tokenizer.encode(
                text, add_special_tokens=True, truncation=False, verbose=False)) > encoder.max_seq_length:
            raise ValueError('A sentence exceeds the active encoder limit. Check the local model installation.')
        try:
            if cache is None:
                raise FileNotFoundError()
            vectors[text] = validate(np.load(cache / (hashlib.sha256(text.encode()).hexdigest() + '.npy'), allow_pickle=False))
        except (OSError, ValueError):
            missing.append(text)
    if stats is not None:
        stats['sentence_vectors_reused'] = stats.get('sentence_vectors_reused', 0) + len(vectors)
        stats['sentence_vectors_computed'] = stats.get('sentence_vectors_computed', 0) + len(missing)
        stats['boundary_embedding_identity'] = identity
    for text, vector in encode_texts(encoder, missing, settings, should_cancel=should_cancel):
        check_cancel(should_cancel)
        vectors[text] = validate(vector)
        if cache is not None:
            destination = cache / (hashlib.sha256(text.encode()).hexdigest() + '.npy')
            temporary = destination.with_suffix(f'.{uuid4().hex}.tmp')
            try:
                with temporary.open('wb') as stream:
                    np.save(stream, vector, allow_pickle=False)
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
    return [vectors[text] for text in texts]


def semantic_spans(text, budget, provider, threshold, excluded=(), stats=None, should_cancel=None, *, min_tokens=128):
    """Split where adjacent sentences are dissimilar (below `threshold`), then enforce token limits.

    Experimental. Breaks that would leave a passage under `min_tokens` are skipped.
    """
    import math
    import numpy as np
    from qualitative_themes.analysis.segment import _paragraphs, _sentences, _automatic, _validate, model_text, SegmentRange
    from qualitative_themes.analysis.topics import check_cancel
    if isinstance(threshold, bool) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError('Semantic boundary sensitivity must be between 0 and 1.')
    if type(min_tokens) is not int or not 0 <= min_tokens <= 4096:
        raise ValueError('Semantic minimum passage target must be between 0 and 4096 tokens.')
    minimum = min(min_tokens, budget.maximum)
    sentences = [s for p in _paragraphs(text) for s in _sentences(text, p)]
    atoms = _automatic(text, budget, excluded, None, should_cancel, sentences)
    inputs = [model_text(text, atom, excluded) for atom in atoms]
    nonempty = [body for body in inputs if body]
    values = list(provider(nonempty)) if nonempty else []
    if len(values) != len(nonempty):
        raise ValueError('Sentence embeddings are incomplete; no boundaries were accepted.')
    normalized = []
    dimensions = None
    for value in values:
        value = np.asarray(value, dtype=float)
        if value.ndim != 1 or not value.size or not np.isfinite(value).all() or np.linalg.norm(value) == 0:
            raise ValueError('Invalid sentence embedding; no boundaries were accepted.')
        if dimensions is not None and value.size != dimensions:
            raise ValueError('Sentence embedding sizes do not match.')
        dimensions = value.size
        normalized.append(value / np.linalg.norm(value))
    vectors = iter(normalized)
    current, previous = None, None
    current_tokens, suppressed, merged_tail = 0, 0, 0
    result, semantic_breaks, limit_breaks = [], 0, 0
    for atom, body in zip(atoms, inputs):
        check_cancel(should_cancel)
        vector = next(vectors) if body else None
        candidate = SegmentRange(current.start_char if current else atom.start_char, atom.end_char)
        candidate_tokens = budget.count(model_text(text, candidate, excluded))
        over = candidate_tokens > budget.maximum
        change = previous is not None and vector is not None and float(np.dot(previous, vector)) < threshold
        if change and not over and current_tokens < minimum:
            suppressed += 1
            change = False
        if current and (over or change):
            result.append(current)
            current = atom
            current_tokens = budget.count(body)
            limit_breaks += int(over)
            semantic_breaks += int(change and not over)
        else:
            current = candidate
            current_tokens = candidate_tokens
        if vector is not None:
            previous = vector
    if current:
        result.append(current)
    # Avoid a tiny final passage when it can fit with its predecessor without truncation.
    if len(result) > 1 and budget.count(model_text(text, result[-1], excluded)) < minimum:
        combined = SegmentRange(result[-2].start_char, result[-1].end_char)
        if budget.count(model_text(text, combined, excluded)) <= budget.maximum:
            result[-2:] = [combined]
            semantic_breaks -= 1
            merged_tail = 1
    _validate(text, result)
    if any(budget.count(model_text(text, span, excluded)) > budget.maximum for span in result):
        raise ValueError('Semantic passage validation exceeded the model limit.')
    if stats is not None:
        ends = {s.end_char for s in sentences}
        for key, value in {'semantic_breaks': semantic_breaks, 'token_limit_breaks': limit_breaks,
                           'suppressed_semantic_breaks': suppressed, 'merged_short_tails': merged_tail,
                           'forced_splits': sum(span.end_char not in ends for span in result)}.items():
            stats[key] = stats.get(key, 0) + value
        stats['semantic_threshold'] = threshold
        stats['semantic_min_tokens'] = min_tokens
        stats['effective_semantic_min_tokens'] = minimum
        stats['experimental'] = True
    return result
