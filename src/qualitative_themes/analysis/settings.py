"""Single preset mapping and validation, independent of Qt and model weights."""
from dataclasses import asdict, dataclass, fields
import re

PRESETS = {
    "Balanced": {"min_topic_size": 5, "min_samples": 2, "n_neighbors": 15, "cluster_selection_method": "eom"},
    "Broad": {"min_topic_size": 10, "min_samples": 3, "n_neighbors": 25, "cluster_selection_method": "eom"},
    "Detailed": {"min_topic_size": 3, "min_samples": 1, "n_neighbors": 10, "cluster_selection_method": "eom"},
    "Exploratory": {"min_topic_size": 2, "min_samples": 1, "n_neighbors": 5, "cluster_selection_method": "leaf"},
}
STYLE_HELP = {
    "Balanced": "A starting point for recurring ideas and supporting evidence.",
    "Broad": "Favor larger groups of evidence; small ideas may remain Uncategorized.",
    "Detailed": "Look for smaller groups and more specific ideas in a new discovery run.",
    "Exploratory": "Look for small candidate groups; expect more provisional themes to review.",
}


@dataclass(frozen=True)
class AnalysisSettings:
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_revision: str = "c9745ed1d9f207416be6d2e6f8de32d1f16199bf"
    seed: int = 42
    min_topic_size: int = PRESETS["Balanced"]["min_topic_size"]
    batch_size: int = 16
    analysis_style: str = "Balanced"
    min_samples: int = PRESETS["Balanced"]["min_samples"]
    n_neighbors: int = PRESETS["Balanced"]["n_neighbors"]
    cluster_selection_method: str = PRESETS["Balanced"]["cluster_selection_method"]
    requested_theme_count: int | None = None
    ignored_words: tuple[str, ...] = ()
    ngram_range: tuple[int, int] = (1, 2)
    name_themes: bool = False
    ollama_model: str | None = None


def preset_settings(style, **overrides):
    if style not in PRESETS:
        raise ValueError("Choose a supported analysis style.")
    return AnalysisSettings(analysis_style=style, **{**PRESETS[style], **overrides})


def parse_ignored_words(text):
    words = tuple(sorted(set(re.findall(r"(?u)\b\w\w+\b", text.lower()))))
    if len(words) > 500:
        raise ValueError("Use at most 500 ignored words.")
    return words


def settings_from_dict(values):
    allowed = {field.name for field in fields(AnalysisSettings)}
    options = {key: value for key, value in values.items() if key in allowed}
    for name in ("ignored_words", "ngram_range"):
        if name in options:
            options[name] = tuple(options[name])
    return AnalysisSettings(**options)


def validate_settings(settings, count):
    if any(type(getattr(settings, name)) is not int for name in
           ("min_topic_size", "batch_size", "min_samples", "n_neighbors", "seed")):
        raise ValueError("Analysis counts and seed must be whole numbers.")
    if settings.analysis_style not in PRESETS:
        raise ValueError("Choose a supported analysis style.")
    if settings.min_topic_size < 2 or settings.batch_size < 1 or settings.min_samples < 1 or settings.n_neighbors < 2:
        raise ValueError("Minimum evidence must be at least 2; other advanced counts must be positive.")
    if settings.cluster_selection_method not in {"eom", "leaf"} or settings.ngram_range not in {(1, 1), (1, 2)}:
        raise ValueError("Choose supported clustering and keyword settings.")
    if not settings.embedding_model.strip() or not settings.embedding_revision.strip():
        raise ValueError("The internal embedding model and revision must be set.")
    if len(settings.ignored_words) > 500 or any(not isinstance(word, str) or not re.fullmatch(r"\w{2,}", word) for word in settings.ignored_words):
        raise ValueError("Ignored words must be single words of at least two characters (up to 500).")
    minimum = max(3, settings.min_topic_size)
    if count < minimum:
        raise ValueError(f"Only {count} passages are available; these settings need at least {minimum}. "
                         "Lower minimum evidence, choose Short passages or Paragraphs, or add documents. "
                         "Discovery needs at least 3 passages.")
    target = settings.requested_theme_count
    if target is not None and (type(target) is not int or not 1 <= target <= count):
        raise ValueError(f"Aim for a positive number no larger than the {count} available passages.")


def small_corpus_warning(settings, count):
    if count < max(10, settings.min_topic_size * 2):
        return (f"Small corpus: {count} passages. Analysis can run, but may find few themes "
                "or leave passages Uncategorized. Treat names and groups as preliminary.")
    return ""


def vectorizer_options(settings, english_words):
    stop_words = "english" if not settings.ignored_words else sorted(set(english_words) | set(settings.ignored_words))
    return {"stop_words": stop_words, "ngram_range": settings.ngram_range, "min_df": 1}


def effective_parameters(settings, count):
    return {"umap": {"n_neighbors": min(settings.n_neighbors, count-1), "n_components": min(5, count-2),
                     "min_dist": 0.0, "metric": "cosine", "init": "random", "random_state": settings.seed},
            "hdbscan": {"min_cluster_size": settings.min_topic_size, "min_samples": min(settings.min_samples, count-1),
                        "metric": "euclidean", "cluster_selection_method": settings.cluster_selection_method,
                        "prediction_data": True},
            "vectorizer": {"stop_words": "english", "additional_stop_words": list(settings.ignored_words),
                           "ngram_range": list(settings.ngram_range), "min_df": 1},
            "bertopic": {"top_n_words": 10, "calculate_probabilities": False},
            "reduction": {"aim_for_themes": settings.requested_theme_count, "includes_uncategorized": False}}
