"""Semantic matching and residual discovery, independent of Qt."""
from dataclasses import replace


def guided_seed_topics(settings):
    return [[word.strip() for word in text.split(',') if word.strip()] for _, text in settings.topic_definitions]


def match_predefined(embeddings, category_embeddings, threshold):
    import numpy as np
    passages, categories = np.asarray(embeddings, dtype=float), np.asarray(category_embeddings, dtype=float)
    if passages.ndim != 2 or categories.ndim != 2 or passages.shape[1] != categories.shape[1] or not len(categories):
        raise ValueError('Invalid passage or category embedding dimensions.')
    if not np.isfinite(passages).all() or not np.isfinite(categories).all():
        raise ValueError('Embeddings must contain finite values.')
    pnorm, cnorm = np.linalg.norm(passages, axis=1), np.linalg.norm(categories, axis=1)
    if (cnorm == 0).any():
        raise ValueError('A topic description produced an empty embedding; revise it.')
    similarities = (passages / np.maximum(pnorm[:, None], 1e-12)) @ (categories / cnorm[:, None]).T
    best = similarities.argmax(axis=1)
    scores = similarities[np.arange(len(passages)), best]
    return ([int(topic) if norm > 0 and score >= threshold else -1 for topic, score, norm in zip(best, scores, pnorm)],
            [float(np.clip(score, -1, 1)) for score in scores])


def zero_shot_partition(passages, embeddings, category_embeddings, settings, discover, check=lambda: None):
    import numpy as np
    assignments, similarities = match_predefined(embeddings, category_embeddings, settings.zeroshot_min_similarity)
    residual = [i for i, topic in enumerate(assignments) if topic == -1]
    minimum, discovered = max(3, settings.min_topic_size), 0
    if len(residual) >= minimum:
        check()
        output = discover([passages[i] for i in residual], np.asarray(embeddings)[residual].copy(),
                          replace(settings, analysis_method='general', topic_definitions=(), requested_theme_count=None))
        if len(output.assignments) != len(residual):
            raise ValueError('Residual discovery returned incomplete memberships.')
        groups = sorted(set(output.assignments) - {-1})
        if any(type(topic) is not int or topic < 0 for topic in groups):
            raise ValueError('Residual discovery returned invalid topic identifiers.')
        mapping = {topic: len(settings.topic_definitions) + i for i, topic in enumerate(groups)}
        for index, topic in zip(residual, output.assignments):
            assignments[index] = mapping[topic] if topic != -1 else -1
        discovered = len(groups)
    check()
    labels = {str(i): name for i, (name, _) in enumerate(settings.topic_definitions) if i in assignments}
    return assignments, {
        'predefined_topic_labels': labels,
        'topic_origins': {str(t): ('uncategorized' if t == -1 else 'predefined' if str(t) in labels else 'discovered') for t in sorted(set(assignments))},
        'zero_shot': {'matching': 'best cosine similarity; one category per passage',
                     'minimum_similarity': settings.zeroshot_min_similarity,
                     'best_similarity_by_segment': {p.id: score for p, score in zip(passages, similarities)},
                     'matched_passage_count': len(passages)-len(residual),
                     'unmatched_before_discovery': len(residual),
                     'residual_discovery_skipped': bool(residual) and len(residual) < minimum,
                     'unmatched_category_names': [name for i, (name, _) in enumerate(settings.topic_definitions) if str(i) not in labels]},
        'discovery_theme_count': discovered,
    }


def restore_partition_keywords(passages, assignments, representation):
    if len(representation.assignments) != len(assignments):
        raise ValueError('Keyword extraction returned incomplete memberships.')
    mapping = {}
    for old, current in zip(assignments, representation.assignments):
        if current in mapping and mapping[current] != old:
            raise ValueError('Keyword extraction merged distinct topics.')
        mapping[current] = old
    if len(set(mapping.values())) != len(mapping):
        raise ValueError('Keyword extraction split a topic.')
    return ({old: representation.keywords.get(current, []) for current, old in mapping.items()},
            {old: representation.representatives.get(current, []) for current, old in mapping.items()})
