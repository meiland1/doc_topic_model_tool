"""Single preset mapping and validation, independent of Qt and model weights."""
from dataclasses import dataclass, fields
import re
import math

PRESETS = {
    "Balanced": {"min_topic_size": 5, "min_samples": 2, "n_neighbors": 15, "cluster_selection_method": "eom"},
    "Broad": {"min_topic_size": 10, "min_samples": 3, "n_neighbors": 25, "cluster_selection_method": "eom"},
    "Detailed": {"min_topic_size": 3, "min_samples": 1, "n_neighbors": 10, "cluster_selection_method": "eom"},
    "Exploratory": {"min_topic_size": 2, "min_samples": 1, "n_neighbors": 5, "cluster_selection_method": "leaf"},
}
STYLE_HELP = {
    "Balanced": "A good place to start: themes that come up again and again.",
    "Broad": "Fewer, larger themes. Small ideas may end up in \"Not in any theme\".",
    "Detailed": "More themes, each about something more specific.",
    "Exploratory": "Many small themes. Expect more to review, and some that don't hold up.",
}


# Theme-naming model preselected when it is installed. The release kit ships it as a
# model pack; any other installed model can still be chosen.
DEFAULT_NAMING_MODEL = "llama3.1:8b"


# Key word styles: stored value -> what the researcher sees. "varied" uses
# MaximalMarginalRelevance, "meaning" KeyBERTInspired, "counts" plain c-TF-IDF.
KEYWORD_STYLES = {"counts": "Plain counts", "varied": "More varied (recommended)", "meaning": "Based on meaning"}

# How many passages HDBSCAN leaves unsorted, as min_samples relative to the theme size.
UNSORTED_LEVELS = ("Fewer", "Balanced", "More")

# At or below this many passages (or with a minimum of 1 passage per theme) themes are
# found by small-collection grouping instead of UMAP + HDBSCAN. Tuned on the sample interviews.
SMALL_COLLECTION = 30
# Small-collection grouping stops joining groups at the median distance between
# passages, but never above this (cosine distance, average linkage). On the sample
# interviews 0.55 separates the garden, transport, booking and letter answers;
# the median lets a few similar whole documents still split.
SMALL_COLLECTION_DISTANCE = 0.55

ANALYSIS_METHODS = {
    "general": "Let the tool find themes",
    "guided": "Find themes, nudged by my keywords",
    "zero_shot": "Start from my own list of themes",
}


def parse_topic_definitions(text):
    """One Name | matching words or description per line; preserve phrases.

    Returns ((name, text), ...). Blank lines are skipped; raises ValueError on a
    line without '|'. Content is checked later by validate_method_settings().
    """
    definitions = []
    for line in text.splitlines():
        if not line.strip():
            continue
        name, separator, content = line.partition("|")
        if not separator:
            raise ValueError("Use one topic per line: Name | seed words or description.")
        definitions.append((name.strip(), content.strip()))
    return tuple(definitions)


@dataclass(frozen=True)
class AnalysisSettings:
    """Every option for one analysis run. Frozen; use dataclasses.replace() to change.

    The whole thing is saved as JSON with each run (asdict), so adding a field
    is backwards compatible as long as it has a default. settings_from_dict()
    ignores keys it doesn't know.
    """
    analysis_method: str = "general"
    topic_definitions: tuple[tuple[str, str], ...] = ()
    zeroshot_min_similarity: float = 0.7
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
    allow_truncation: bool = False
    ollama_model: str | None = None
    # Leave interviewer turns out of model input (source text and offsets are unchanged).
    exclude_interviewer: bool = False
    # Normalized speaker labels treated as the interviewer (empty = the known defaults).
    interviewer_labels: tuple = ()
    # More options (added in 0.3). Older runs load with these defaults.
    fill_leftovers: bool = False
    fill_threshold: float = 0.5
    keyword_style: str = "varied"
    play_down_common_words: bool = True
    top_n_words: int = 10
    auto_merge: bool = False
    max_themes: int = 100


def min_samples_for(style, level):
    """HDBSCAN min_samples for a theme size and an 'unsorted' level (Fewer/Balanced/More)."""
    preset = PRESETS[style]
    return {"Fewer": 1, "Balanced": preset["min_samples"], "More": preset["min_samples"] + preset["min_topic_size"]}[level]


def unsorted_level(settings):
    """The Fewer/Balanced/More choice that gives settings.min_samples (Balanced if none does)."""
    for level in UNSORTED_LEVELS:
        if min_samples_for(settings.analysis_style, level) == settings.min_samples:
            return level
    return "Balanced"


def small_collection(settings, count):
    """True when themes are found by small-collection grouping (agglomerative, no UMAP)."""
    return settings.analysis_method != "zero_shot" and (count <= SMALL_COLLECTION or settings.min_topic_size == 1)


def preset_settings(style, **overrides):
    """Settings for a named style (Balanced/Broad/...), with any field overridden."""
    if style not in PRESETS:
        raise ValueError("Choose a supported analysis style.")
    return AnalysisSettings(analysis_style=style, **{**PRESETS[style], **overrides})


def parse_ignored_words(text):
    words = tuple(sorted(set(re.findall(r"(?u)\b\w\w+\b", text.lower()))))
    if len(words) > 500:
        raise ValueError("Use at most 500 ignored words.")
    return words


def settings_from_dict(values):
    """Rebuild settings from saved JSON. Unknown keys are dropped; lists become tuples."""
    allowed = {field.name for field in fields(AnalysisSettings)}
    options = {key: value for key, value in values.items() if key in allowed}
    for name in ("ignored_words", "ngram_range", "interviewer_labels"):
        if name in options:
            options[name] = tuple(options[name])
    if "topic_definitions" in options:
        options["topic_definitions"] = tuple(tuple(item) for item in options["topic_definitions"])
    return AnalysisSettings(**options)


def validate_method_settings(settings):
    """Raise ValueError (researcher-facing message) if the method/topic setup is invalid."""
    if settings.analysis_method not in ANALYSIS_METHODS:
        raise ValueError("Choose a supported analysis method.")
    score = settings.zeroshot_min_similarity
    if isinstance(score, bool) or not isinstance(score, (float, int)) or not math.isfinite(score) or not 0 <= score <= 1:
        raise ValueError("Zero-shot similarity must be between 0 and 1.")
    if len(settings.topic_definitions) > 50:
        raise ValueError("Use at most 50 predefined topics.")
    names = set()
    for item in settings.topic_definitions:
        if not isinstance(item, (tuple, list)) or len(item) != 2 or any(not isinstance(value, str) for value in item):
            raise ValueError("Each topic needs a name and matching text.")
        name, content = item
        if not name.strip() or len(name) > 80 or any(ord(c) < 32 for c in name):
            raise ValueError("Topic names must be single lines of 1–80 characters.")
        if not content.strip() or len(content) > 2000 or any(ord(c) < 32 for c in content):
            raise ValueError("Each topic needs single-line seed words or a description (up to 2000 characters).")
        if name.strip().casefold() in names:
            raise ValueError("Use distinct topic names.")
        names.add(name.strip().casefold())
        if settings.analysis_method == "guided" and not any(word.strip() for word in content.split(",")):
            raise ValueError("Guided topics need comma-separated seed words or phrases.")
    if settings.analysis_method != "general" and not settings.topic_definitions:
        raise ValueError("Add at least one topic for this method.")
    if settings.analysis_method == "zero_shot" and settings.requested_theme_count is not None:
        raise ValueError("Automatic theme reduction is unavailable for zero-shot categories.")


def validate_settings(settings, count):
    """Full check before fitting. `count` is the number of prepared passages.

    Pre: called after passages are prepared so the minimum-size checks are real.
    Post: returns None, or raises ValueError with what to change.
    """
    if type(settings.allow_truncation) is not bool:
        raise ValueError('Choose whether to allow truncation of oversized passages.')
    validate_method_settings(settings)
    if any(type(getattr(settings, name)) is not int for name in
           ("min_topic_size", "batch_size", "min_samples", "n_neighbors", "seed", "top_n_words", "max_themes")):
        raise ValueError("Analysis counts and seed must be whole numbers.")
    if settings.analysis_style not in PRESETS:
        raise ValueError("Choose a supported analysis style.")
    if settings.min_topic_size < 1 or settings.batch_size < 1 or settings.min_samples < 1 or settings.n_neighbors < 2:
        raise ValueError("A theme needs at least 1 passage; the other counts must be above zero.")
    if settings.keyword_style not in KEYWORD_STYLES or not 5 <= settings.top_n_words <= 20:
        raise ValueError("Choose a key word style, and 5 to 20 key words per theme.")
    if not 1 <= settings.max_themes <= 1000:
        raise ValueError("Allow between 1 and 1,000 themes.")
    if any(type(getattr(settings, name)) is not bool for name in ("fill_leftovers", "play_down_common_words", "auto_merge")):
        raise ValueError("Choose on or off for each option.")
    if isinstance(settings.fill_threshold, bool) or not 0 <= settings.fill_threshold <= 1:
        raise ValueError("The closeness for leftover passages must be between 0 and 1.")
    if settings.cluster_selection_method not in {"eom", "leaf"} or settings.ngram_range not in {(1, 1), (1, 2)}:
        raise ValueError("Choose supported clustering and keyword settings.")
    if not settings.embedding_model.strip() or not settings.embedding_revision.strip():
        raise ValueError("The internal embedding model and revision must be set.")
    if len(settings.ignored_words) > 500 or any(not isinstance(word, str) or not re.fullmatch(r"\w{2,}", word) for word in settings.ignored_words):
        raise ValueError("Ignored words must be single words of at least two characters (up to 500).")
    if settings.analysis_method == "zero_shot":
        minimum = 1
    elif small_collection(settings, count):
        minimum = 2
    else:
        minimum = settings.min_topic_size
    if count < minimum:
        raise ValueError(f"Only {count} passage{'s are' if count != 1 else ' is'} available; these settings need "
                         f"at least {minimum}. Lower \"A theme needs at least\", split documents differently, "
                         "or add documents.")
    target = settings.requested_theme_count
    if target is not None and (type(target) is not int or not 1 <= target <= count):
        raise ValueError(f"Aim for a positive number no larger than the {count} available passages.")


def small_corpus_warning(settings, count):
    """A note for small collections, or '' when the collection is big enough."""
    if small_collection(settings, count):
        why = "A theme can be a single passage" if settings.min_topic_size == 1 else "Few passages"
        return (f"{why}, so using small-collection grouping. Every passage gets a theme; "
                "treat the themes as a first sort.")
    if count < max(10, settings.min_topic_size * 2):
        return (f"Small collection: {count} passages. Expect few themes and many passages in "
                "\"Not in any theme\". Treat names and groups as preliminary.")
    return ""


def keyword_parameters(settings):
    """How key words are picked, as recorded with a run."""
    return {"style": settings.keyword_style, "top_n_words": settings.top_n_words,
            "reduce_frequent_words": settings.play_down_common_words,
            "representation": {"varied": "MaximalMarginalRelevance(diversity=0.3)", "meaning": "KeyBERTInspired",
                               "counts": None}[settings.keyword_style]}


def vectorizer_options(settings, english_words):
    stop_words = "english" if not settings.ignored_words else sorted(set(english_words) | set(settings.ignored_words))
    return {"stop_words": stop_words, "ngram_range": settings.ngram_range, "min_df": 1}


def effective_parameters(settings, count):
    """UMAP/HDBSCAN/vectorizer parameters as actually used for `count` passages.

    Saved with each run so the exact configuration can be reported later.
    """
    vectorizer = {"stop_words": "english", "additional_stop_words": list(settings.ignored_words),
                  "ngram_range": list(settings.ngram_range), "min_df": 1}
    if settings.analysis_method == "zero_shot":
        return {"matching": {"metric": "cosine", "minimum_similarity": settings.zeroshot_min_similarity,
                             "assignment": "single best matching description"},
                "residual_discovery": {"minimum_passages": max(3, settings.min_topic_size),
                                       "parameters": "Computed for the unmatched subset; below the minimum they stay ungrouped."},
                "vectorizer": vectorizer, "keywords": keyword_parameters(settings),
                "reduction": {"aim_for_themes": None, "disabled": True, "max_themes": settings.max_themes}}
    common = {"vectorizer": vectorizer, "keywords": keyword_parameters(settings),
              "bertopic": {"top_n_words": settings.top_n_words, "calculate_probabilities": False},
              "reduction": {"aim_for_themes": settings.requested_theme_count, "includes_uncategorized": False,
                            "auto_merge": settings.auto_merge, "max_themes": settings.max_themes},
              "leftovers": {"fill": settings.fill_leftovers, "strategy": "embeddings",
                            "threshold": settings.fill_threshold}}
    if small_collection(settings, count):
        grouping = {"method": "AgglomerativeClustering", "metric": "cosine", "linkage": "average"}
        if settings.requested_theme_count:
            grouping["n_clusters"] = min(settings.requested_theme_count, count)
        else:
            grouping["distance_threshold"] = f"median distance between passages, at most {SMALL_COLLECTION_DISTANCE}"
        return {"small_collection": grouping, **common}
    return {"umap": {"n_neighbors": min(settings.n_neighbors, count-1), "n_components": min(5, count-2),
                     "min_dist": 0.0, "metric": "cosine", "init": "random", "random_state": settings.seed},
            "hdbscan": {"min_cluster_size": settings.min_topic_size, "min_samples": min(settings.min_samples, count-1),
                        "metric": "euclidean", "cluster_selection_method": settings.cluster_selection_method,
                        "prediction_data": True}, **common}


def settings_report(values: dict, info: dict) -> str:
    """Every setting of a run with the value used, in plain words (About this analysis).

    `values` is the run's saved settings JSON, `info` its model_info. Settings
    a run predates show as "not recorded" rather than today's default.
    """
    def has(key):
        return key in values

    def on(key):
        return ("On" if values[key] else "Off") if has(key) else "not recorded"
    from qualitative_themes.analysis.segment import UNIT_LABELS
    style = values.get("analysis_style", "Balanced")
    target = values.get("requested_theme_count")
    lines = [
        ("How themes were found", ANALYSIS_METHODS.get(values.get("analysis_method", "general"), "not recorded")),
        ("How documents were split", UNIT_LABELS.get(values.get("analysis_unit"), "not recorded")),
        ("Theme size", style),
        ("A theme needs at least", f"{values.get('min_topic_size', '?')} passages"
         + (" (not used: small-collection grouping gives every passage a theme)"
            if info.get("small_collection") and values.get("min_topic_size") != 1 else "")),
        ("How many passages stay unsorted",
         f"{unsorted_level(settings_from_dict(values))} (min_samples {values.get('min_samples', '?')})"
         if style in PRESETS else "not recorded"),
        ("Place leftover passages in their closest theme",
         on("fill_leftovers") + (f" ({info['filled_leftovers']} passages placed)" if info.get("filled_leftovers") else "")),
        ("Merge very similar themes automatically", on("auto_merge")),
        ("Number of themes", f"About {target}" if target else "Let the tool decide"),
        ("At most", f"{values['max_themes']} themes" if has("max_themes") else "not recorded"),
        ("Keyword style", KEYWORD_STYLES.get(values.get("keyword_style"), "not recorded (plain counts)")),
        ("Play down words found in every theme", on("play_down_common_words")),
        ("Keywords per theme", str(values.get("top_n_words", "10"))),
        ("Words to ignore", ", ".join(values.get("ignored_words") or []) or "none"),
        ("Two-word keywords", "On" if tuple(values.get("ngram_range", (1, 2))) == (1, 2) else "Off"),
        ("Ignore the interviewer's questions", "On" if values.get("exclude_interviewer") else "Off"),
        ("Random seed", str(values.get("seed", "not recorded"))),
        ("Grouping", ("Small-collection grouping" + (f" (cut at distance {info['small_collection_distance']})"
                                                       if "small_collection_distance" in info else ""))
         if info.get("small_collection") else "UMAP and HDBSCAN"),
    ]
    found, kept = info.get("discovery_theme_count"), info.get("actual_theme_count")
    if found is not None and kept is not None and found != kept:
        lines.append(("Themes found, then kept", f"{found}, then {kept}"))
    width = max(len(name) for name, _ in lines)
    return "Settings used\n" + "\n".join(f"  {name + ':':<{width + 1}} {value}" for name, value in lines)
