"""Load the sentence-embedding model once per session, offline only.

Source order:
1. The model bundled with the installed app (``resources/models/embedding``).
2. A legacy per-project cache (``<project>/models/weights``) from older versions.

Nothing here downloads: ``local_files_only=True`` is always passed, and the
offline guard in ``runtime.offline`` blocks Hugging Face network access.
"""
from __future__ import annotations

import threading
from pathlib import Path

from qualitative_themes.runtime.paths import bundled_embedding_model

MISSING_MESSAGE = ("The theme-finding model is missing from this installation. "
                   "Open the start screen and choose Check & repair, or reinstall the app.")

_lock = threading.Lock()
_cache: dict = {}


def _sentence_transformer():
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise ValueError('Install analysis dependencies: pip install -e ".[analysis,test]"') from exc
    return SentenceTransformer


def embedding_source(settings, directory=None):
    """Describe where the encoder will load from, or None when nothing is available."""
    bundled = bundled_embedding_model()
    if bundled is not None:
        return {"kind": "bundled", "path": str(bundled)}
    if directory is not None:
        legacy = Path(directory) / "models" / "weights"
        if legacy.is_dir() and any(legacy.iterdir()):
            return {"kind": "project_cache", "path": str(legacy)}
    return None


def load_encoder(settings, directory=None):
    """Return a cached SentenceTransformer; raise ValueError with plain guidance if missing.

    Thread-safe; the first call per session is slow (loads weights), later
    calls return the same object. Never downloads anything.
    """
    factory = _sentence_transformer()
    source = embedding_source(settings, directory)
    # Fall back to the legacy cache lookup even when the folder looks empty, so an
    # injected test double (or an older layout) still gets a chance to load.
    if source is None:
        source = {"kind": "project_cache", "path": str(Path(directory) / "models" / "weights") if directory else ""}
    key = (id(factory), source["kind"], source["path"], settings.embedding_model, settings.embedding_revision)
    with _lock:
        if key in _cache:
            return _cache[key]
        try:
            device = preferred_device()
            if source["kind"] == "bundled":
                encoder = factory(source["path"], device=device, local_files_only=True)
            else:
                encoder = factory(settings.embedding_model, revision=settings.embedding_revision,
                                  cache_folder=source["path"] or None, local_files_only=True, device=device)
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
