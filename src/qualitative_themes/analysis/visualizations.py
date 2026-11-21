"""Read-only BERTopic visualizations of the current effective saved-run coding."""
import hashlib
import html
import json
from pathlib import Path

from qualitative_themes.export.research import build_export
from qualitative_themes.analysis.settings import settings_from_dict, vectorizer_options
from qualitative_themes.analysis.topics import check_cancel
from qualitative_themes.analysis.transcript_text import clean_model_text


MAX_RANGES = 30000
MAP_POINTS = 6000


def map_sample(count, limit=MAP_POINTS):
    """Evenly spaced indices so large maps stay responsive; every theme keeps its share."""
    if count <= limit:
        return list(range(count))
    step = count / limit
    return sorted({int(i * step) for i in range(limit)})


def visualization_snapshot(directory, run_id, should_cancel=None):
    """Current effective coding of a run, capped at MAX_RANGES ranges."""
    if not run_id:
        raise ValueError('Choose a saved analysis to visualize.')
    data = build_export(directory, run_id, should_cancel=should_cancel)
    if len(data['passages']) > MAX_RANGES:
        raise ValueError(f'This view supports up to {MAX_RANGES:,} coded ranges. Create an analysis with a smaller document selection for visualization.')
    themes = {t['theme_id']: t for t in data['themes'] if t['unique_coded_ranges']}
    if len(themes) > 100:
        raise ValueError('This view supports up to 100 nonempty themes. Refine to fewer themes or choose a smaller analysis.')
    outliers = set(data['metadata']['outlier_theme_ids'])
    mapping, counter = {}, 0
    for tid in themes:
        if tid in outliers:
            mapping[tid] = -1
        else:
            mapping[tid] = counter; counter += 1
    texts, assignments, range_indices = [], [], []
    for index, passage in enumerate(data['passages']):
        check_cancel(should_cancel)
        cleaned = clean_model_text(passage['quote'])
        if not cleaned:
            continue
        for assignment in passage['assignments']:
            texts.append(cleaned)
            assignments.append(mapping[assignment['theme_id']])
            range_indices.append(index)
    active_topics = set(assignments)
    mapping = {tid: topic for tid, topic in mapping.items() if topic in active_topics}
    if not texts:
        raise ValueError('No active coded text remains to visualize. Accept or code some passages first.')
    return {'metadata': data['metadata'], 'themes': themes, 'passages': data['passages'],
            'texts': texts, 'assignments': assignments, 'range_indices': range_indices,
            'theme_topics': mapping}


def restored_theme_mapping(before, after, theme_topics):
    """{bertopic_topic: theme_id} after BERTopic renumbers the saved groups.

    Raises ValueError if the refit changed any membership, so a plot can never
    show different groups than the saved coding.
    """
    if len(before) != len(after):
        raise ValueError('The visualization model changed the saved passage count.')
    restored = {}
    for old, new in zip(before, after):
        if old in restored and restored[old] != new:
            raise ValueError('The visualization model changed saved theme memberships.')
        restored[old] = int(new)
    if len(set(restored.values())) != len(restored) or any((a == -1) != (b == -1) for a,b in restored.items()):
        raise ValueError('The visualization model did not preserve saved theme identities.')
    return {restored[topic]: tid for tid, topic in theme_topics.items()}


def project_2d(vectors):
    """Deterministic PCA projection; never substitute random map coordinates."""
    import numpy as np
    from sklearn.decomposition import PCA
    vectors = np.asarray(vectors, dtype=float)
    if vectors.ndim != 2 or not len(vectors) or not vectors.shape[1] or not np.isfinite(vectors).all():
        raise ValueError('Invalid passage embeddings for the document map.')
    if len(vectors) == 1 or np.allclose(vectors, vectors[0]):
        return np.zeros((len(vectors), 2))
    result = PCA(n_components=min(2, *vectors.shape), svd_solver='full').fit_transform(vectors)
    return np.pad(result, ((0,0),(0,2-result.shape[1])))


def local_embeddings(directory, texts, metadata, progress, should_cancel):
    """Vectors for the snapshot's passages, from the project cache or the bundled encoder."""
    import numpy as np
    settings = settings_from_dict(metadata['settings'])
    identity = metadata['model_info'].get('embedding_identity') or metadata['model_info'].get('source_model_identity')
    if identity:
        cache = Path(directory)/'models'/'embeddings'/hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    else:
        cache = None
    vectors, missing = {}, []
    dimension = None
    for text in dict.fromkeys(texts):
        check_cancel(should_cancel)
        try:
            if cache is None: raise OSError()
            value = np.load(cache/(hashlib.sha256(text.encode()).hexdigest()+'.npy'), allow_pickle=False)
            if value.ndim != 1 or not len(value) or not np.isfinite(value).all() or (dimension and len(value) != dimension):
                raise ValueError('Invalid cached embedding')
            dimension = len(value); vectors[text] = value
        except (OSError, ValueError):
            missing.append(text)
    if missing:
        progress('Loading local embedding weights for the passage map…')
        from qualitative_themes.analysis.encoder import load_encoder
        encoder = load_encoder(settings, directory)
        # If existing cache shape is incompatible, regenerate the full batch in memory.
        if dimension and dimension != encoder.get_sentence_embedding_dimension():
            missing = list(dict.fromkeys(texts)); vectors = {}
        from qualitative_themes.analysis.encoder import encode_texts
        vectors.update(encode_texts(encoder, missing, settings, progress,
                                    lambda: bool(should_cancel and should_cancel())))
    check_cancel(should_cancel)
    return np.stack([vectors[text] for text in texts])


def offline_html(figure):
    """Plotly figure as standalone HTML (plotly.js inlined, no network)."""
    figure.update_layout(autosize=True, width=None, height=650)
    content = figure.to_html(include_plotlyjs=True, full_html=True, config={'responsive': True, 'displaylogo': False})
    policy = "default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; worker-src blob:; connect-src 'none'"
    return content.replace('<head>', '<head><meta http-equiv="Content-Security-Policy" content="'+policy+'">', 1)


def build_visualizations(directory, run_id, progress=None, should_cancel=None, embedding_provider=None, model_factory=None):
    """Build map / heatmap / hierarchy for a run. Read-only for the project.

    Returns {'html': {view: html}, 'messages': {view: why_missing}, 'tree',
    'snapshot', 'notes'}. Each view fails on its own and records a message
    instead of failing the whole call. Slow: run in a worker; results are
    cached by view_cache.
    """
    progress = progress or (lambda message: None)
    snapshot = visualization_snapshot(directory, run_id, should_cancel)
    settings = settings_from_dict(snapshot['metadata']['settings'])
    try:
        import numpy as np
        from sklearn.feature_extraction.text import CountVectorizer, ENGLISH_STOP_WORDS
        if model_factory is None:
            from bertopic import BERTopic
            from bertopic.cluster import BaseCluster
            from bertopic.dimensionality import BaseDimensionalityReduction
            model_factory = lambda vectorizer: BERTopic(embedding_model=None, umap_model=BaseDimensionalityReduction(),
                hdbscan_model=BaseCluster(), vectorizer_model=vectorizer, calculate_probabilities=False)
    except ImportError as exc:
        raise ValueError('Visualization dependencies are missing. Install pip install -e ".[analysis,test]" in your active environment.') from exc
    vectorizer = CountVectorizer(**vectorizer_options(settings, ENGLISH_STOP_WORDS))
    texts = snapshot['texts']
    if not any(vectorizer.build_analyzer()(text) for text in texts):
        raise ValueError('No usable keyword vocabulary remains for visualization. Adjust ignored words and refresh the analysis.')
    check_cancel(should_cancel)
    progress('Rebuilding a temporary BERTopic view of current coding; no discovery…')
    model = model_factory(vectorizer)
    model.fit_transform(texts, embeddings=np.zeros((len(texts),1)), y=np.asarray(snapshot['assignments']))
    topic_themes = restored_theme_mapping(snapshot['assignments'], model.topics_, snapshot['theme_topics'])
    labels = {topic: snapshot['themes'][tid]['theme'] for topic, tid in topic_themes.items()}
    model.set_topic_labels({topic: html.escape(label) for topic,label in labels.items()})
    result = {'html': {}, 'messages': {}, 'tree': '', 'snapshot': snapshot, 'notes': []}
    check_cancel(should_cancel)
    try:
        progress('Preparing the document map from local passage embeddings…')
        unique = list(dict.fromkeys(texts))
        embeddings = (embedding_provider or local_embeddings)(directory, unique, snapshot['metadata'], progress, should_cancel)
        coordinates = project_2d(embeddings)
        by_text = dict(zip(unique, coordinates))
        hover = [html.escape(snapshot['passages'][i]['document']+' · '+snapshot['passages'][i]['quote'][:500]) for i in snapshot['range_indices']]
        keep = map_sample(len(texts))
        title = 'Document map — passages (PCA projection)'
        if len(keep) < len(texts):
            title += f' · showing {len(keep):,} of {len(texts):,} evenly spaced points'
            result['notes'].append(f'The map shows {len(keep):,} of {len(texts):,} passages so it stays responsive; '
                                   'the heatmap and hierarchy use all of them.')
        original_topics = model.topics_
        model.topics_ = [original_topics[i] for i in keep]
        try:
            fig = model.visualize_documents([hover[i] for i in keep],
                reduced_embeddings=np.asarray([by_text[texts[i]] for i in keep]),
                custom_labels=True, hide_annotations=True, title=title)
        finally:
            model.topics_ = original_topics
        result['html']['map'] = offline_html(fig)
    except Exception as exc:
        from qualitative_themes.analysis.topics import AnalysisCancelled
        if isinstance(exc, AnalysisCancelled): raise
        result['messages']['map'] = str(exc)
    check_cancel(should_cancel)
    ordinary = [topic for topic in labels if topic != -1]
    if ordinary:
        progress('Building theme similarity heatmap…')
        try:
            result['html']['heatmap'] = offline_html(model.visualize_heatmap(topics=ordinary, custom_labels=True, use_ctfidf=True,
                title='Theme similarity — current c-TF-IDF evidence'))
        except Exception as exc:
            result['messages']['heatmap'] = 'Heatmap unavailable: ' + str(exc)
    else:
        result['messages']['heatmap'] = 'Only passages in "Not in any theme" are available. The similarity heatmap needs a regular theme.'
    check_cancel(should_cancel)
    if len(ordinary) >= 2:
        progress('Building the hierarchy and text topic tree…')
        try:
            hierarchy = model.hierarchical_topics(texts, use_ctfidf=True)
            for column, id_column in [('Child_Left_Name','Child_Left_ID'), ('Child_Right_Name','Child_Right_ID')]:
                hierarchy[column] = [labels.get(int(tid), name) for tid,name in zip(hierarchy[id_column], hierarchy[column])]
            result['tree'] = model.get_topic_tree(hierarchy)
            safe_hierarchy = hierarchy.copy()
            for column in ['Child_Left_Name', 'Child_Right_Name', 'Parent_Name']:
                if column in safe_hierarchy:
                    safe_hierarchy[column] = safe_hierarchy[column].map(html.escape)
            result['html']['hierarchy'] = offline_html(model.visualize_hierarchy(hierarchical_topics=safe_hierarchy, custom_labels=True,
                use_ctfidf=True, title='Exploratory theme hierarchy'))
        except Exception as exc:
            result['messages']['hierarchy'] = 'Hierarchy unavailable: ' + str(exc)
            if not result['tree']:
                result['tree'] = result['messages']['hierarchy']
    else:
        result['messages']['hierarchy'] = 'The theme tree needs at least two regular themes; "Not in any theme" is left out.'
        result['tree'] = '\n'.join(labels[t] for t in ordinary) or 'No regular themes.'
    if -1 in labels:
        result['tree'] += '\n\nNot in the tree: ' + labels[-1]
    check_cancel(should_cancel)
    return result
