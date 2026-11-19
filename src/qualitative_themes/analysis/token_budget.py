"""Load only the local tokenizer/configuration for passage planning, not weights."""
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import threading

from qualitative_themes.analysis.encoder import embedding_source, MISSING_MESSAGE
from qualitative_themes.analysis.settings import AnalysisSettings

_lock = threading.Lock()


@dataclass
class TokenBudget:
    """Tokenizer plus the encoder's max input length, used to plan passages that fit.

    `identity` (model, revision, file hashes) is saved with runs so a different
    tokenizer can be detected later.
    """
    tokenizer: object
    maximum: int
    identity: dict

    def count(self, text):
        return len(self.tokenizer.encode(text, add_special_tokens=True, truncation=False, verbose=False))


def load_token_budget(settings=None, directory=None):
    """Pre: the embedding model is bundled (or in the legacy project cache).
    Post: a cached TokenBudget. Only reads the tokenizer, not model weights,
    so it's fast enough for previews.
    """
    settings = settings or AnalysisSettings()
    source = embedding_source(settings, directory)
    if source is None:
        raise ValueError(MISSING_MESSAGE)
    with _lock:
        return _load(source['kind'], source['path'], settings.embedding_model, settings.embedding_revision)


@lru_cache(maxsize=8)
def _load(kind, path, model, revision):
    try:
        from transformers import AutoTokenizer
        if kind == 'bundled':
            config = Path(path) / 'sentence_bert_config.json'
            tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True)
        else:
            from transformers.utils.hub import cached_file
            config = Path(cached_file(model, 'sentence_bert_config.json', revision=revision,
                                      cache_dir=path, local_files_only=True))
            tokenizer = AutoTokenizer.from_pretrained(model, revision=revision, cache_dir=path, local_files_only=True)
        content = config.read_bytes()
        maximum = json.loads(content)['max_seq_length']
        if type(maximum) is not int or maximum <= tokenizer.num_special_tokens_to_add():
            raise ValueError('Invalid model token limit')
        maximum = min(maximum, tokenizer.model_max_length)
        if maximum <= tokenizer.num_special_tokens_to_add():
            raise ValueError('Invalid tokenizer limit')
        fingerprints = {}
        for filename in ('tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json',
                         'vocab.txt', 'merges.txt', 'spiece.model', 'sentencepiece.bpe.model'):
            file = config.parent / filename
            if file.is_file():
                fingerprints[filename] = hashlib.sha256(file.read_bytes()).hexdigest()
        return TokenBudget(tokenizer, maximum, {'model': model, 'revision': revision,
                           'configuration_sha256': hashlib.sha256(content).hexdigest(),
                           'tokenizer_files_sha256': fingerprints,
                           'max_tokens': maximum, 'special_tokens_included': True})
    except Exception as exc:
        raise ValueError('The local tokenizer or model limit could not be read. Use Check & repair or reinstall the app.') from exc
