"""Load the sentence-embedding model once per session, offline only.

The model always comes from the app's resources folder
(``resources/models/embedding``). A source checkout gets it from
``scripts/fetch_resources.py``; tests point DOC_TOPIC_MODEL_RESOURCES elsewhere.

Nothing here downloads: ``local_files_only=True`` is always passed, and the
offline guard in ``runtime.offline`` blocks Hugging Face network access.
"""
from __future__ import annotations

import threading

from qualitative_themes.runtime.paths import bundled_embedding_model, missing_files_fix

MISSING_MESSAGE = ("The theme-finding model is missing from this installation. "
                   "Open the start screen and choose Check & repair, or " + missing_files_fix() + ".")

_lock = threading.Lock()
_cache: dict = {}


def _sentence_transformer():
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise ValueError('Install analysis dependencies: pip install -e ".[analysis,test]"') from exc
    return SentenceTransformer


def embedding_source(settings, directory=None):
    """Where the encoder loads from ({'kind', 'path'}), or None when the model is missing.

    `settings` and `directory` are accepted so callers don't care where the model lives.
    """
    bundled = bundled_embedding_model()
    return {"kind": "bundled", "path": str(bundled)} if bundled is not None else None


def load_encoder(settings, directory=None):
    """Return a cached SentenceTransformer; raise ValueError with plain guidance if missing.

    Thread-safe; the first call per session is slow (loads weights), later
    calls return the same object. Never downloads anything.
    """
    factory = _sentence_transformer()
    source = embedding_source(settings, directory)
    if source is None:
        raise ValueError(MISSING_MESSAGE)
    key = (id(factory), source["path"], settings.embedding_model, settings.embedding_revision)
    with _lock:
        if key in _cache:
            return _cache[key]
        try:
            device = preferred_device()
            encoder = factory(source["path"], device=device, local_files_only=True)
        except Exception as exc:
            raise ValueError(MISSING_MESSAGE) from exc
        _cache[key] = encoder
        return encoder


ENCODE_CHUNK = 1024


def preferred_device() -> str:
    """CUDA when the installed PyTorch can use an NVIDIA GPU; otherwise CPU.

    The released installers ship CPU-only PyTorch. Developers with an NVIDIA GPU
    can install a CUDA build of PyTorch in their environment to embed much faster.
    """
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def encode_texts(encoder, texts, settings, progress=None, should_cancel=None):
    """Keep completed chunks; shrink batches/chunks only for recognized memory failures.

    Generator of (text, vector) in input order. Vectors are normalized.
    `should_cancel` is checked between chunks.
    """
    from qualitative_themes.analysis.topics import check_cancel
    batch = settings.batch_size
    if type(batch) is not int or batch < 1:
        raise ValueError('Embedding batch size must be a positive whole number.')
    total = len(texts)
    offset, chunk_size = 0, ENCODE_CHUNK
    while offset < total:
        check_cancel(should_cancel)
        chunk = texts[offset:offset + chunk_size]
        if progress:
            progress(f"Embedding passages: {offset}/{total} done…")
        retry = False
        try:
            values = encoder.encode(chunk, batch_size=batch, normalize_embeddings=True,
                                    convert_to_numpy=True, show_progress_bar=False)
        except (MemoryError, RuntimeError) as exc:
            message = str(exc).lower()
            memory_failure = isinstance(exc, MemoryError) or any(marker in message for marker in (
                'cuda out of memory', 'cuda error: out of memory',
                'defaultcpuallocator: not enough memory', 'mps backend out of memory'))
            if not memory_failure:
                raise
            if batch == 1 and len(chunk) == 1:
                raise ValueError('Not enough memory to embed one passage. Close other applications and try again.') from exc
            retry = True
        if retry:
            # Leave the exception scope before freeing memory so its traceback releases tensors.
            import gc
            gc.collect()
            import sys
            torch = sys.modules.get('torch')
            if torch is not None and torch.cuda.is_available():
                torch.cuda.empty_cache()
            batch = max(1, batch // 2)
            chunk_size = max(1, min(chunk_size // 2, len(chunk) // 2 or 1))
            check_cancel(should_cancel)
            if progress:
                progress('Memory is limited; retrying with smaller embedding batches…')
            continue
        check_cancel(should_cancel)
        if len(values) != len(chunk):
            raise ValueError('The embedding model returned incomplete results.')
        yield from zip(chunk, values)
        offset += len(chunk)
        del values
    if progress and total:
        progress(f"Embedding passages: {total}/{total} done.")


def clear_encoder_cache():
    """Drop loaded encoders so the next load_encoder() reads from disk again (used by tests)."""
    with _lock:
        _cache.clear()
