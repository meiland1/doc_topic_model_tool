"""Research-facing method and origin descriptions, shared by UI and exports."""
import json
from qualitative_themes.analysis.settings import ANALYSIS_METHODS
from qualitative_themes.storage.database import connect

ORIGIN_LABELS = {
    'predefined': 'Predefined category',
    'guided_discovery': 'Guided discovery',
    'discovered': 'Discovered topic',
    'uncategorized': 'Uncategorized',
    'researcher': 'Researcher-created theme',
    'earlier_model': 'Earlier model theme',
}


def method_label(settings, manual=False):
    return 'Manual coding only' if manual else ANALYSIS_METHODS.get(settings.get('analysis_method', 'general'), 'Unknown method')


def topic_provenance(settings, info, topic):
    key = str(topic)
    stored = info.get('topic_provenance', {}).get(key)
    if stored:
        return {'origins': sorted(set(stored.get('origins', []))),
                'supplied_names': sorted(set(stored.get('supplied_names', [])))}
    name = info.get('predefined_topic_labels', {}).get(key)
    origin = ('uncategorized' if int(topic) == -1 else 'predefined' if name else
              'guided_discovery' if settings.get('analysis_method') == 'guided' else 'discovered')
    return {'origins': [origin], 'supplied_names': [name] if name else []}


def combine_provenance(entries):
    return {'origins': sorted({origin for entry in entries for origin in entry['origins']}),
            'supplied_names': sorted({name for entry in entries for name in entry['supplied_names']})}


def origin_text(provenance):
    labels = [ORIGIN_LABELS.get(origin, origin) for origin in provenance['origins']]
    text = ' + '.join(labels) or 'Not recorded'
    return ('Combined origins: ' + text) if len(labels) > 1 else text


def theme_provenance_from_connection(conn, theme_id, run_id=None):
    row = conn.execute('SELECT creation_source FROM themes WHERE id=?', (theme_id,)).fetchone()
    if run_id:
        saved = conn.execute('SELECT settings_json,model_info_json FROM analysis_runs WHERE id=?', (run_id,)).fetchone()
        if saved:
            settings, info = map(json.loads, saved)
            topics = [r[0] for r in conn.execute('SELECT DISTINCT bertopic_id FROM model_suggestions WHERE run_id=? AND theme_id=?', (run_id, theme_id))]
            if topics:
                return combine_provenance([topic_provenance(settings, info, topic) for topic in topics])
    return {'origins': ['researcher' if row and row[0] == 'researcher' else 'earlier_model'], 'supplied_names': []}


def theme_provenance(directory, theme_id, run_id=None):
    with connect(directory) as conn:
        return theme_provenance_from_connection(conn, theme_id, run_id)


def method_summary(settings, info):
    lines = ['Analysis method: ' + method_label(settings)]
    method = settings.get('analysis_method', 'general')
    if method in {'guided', 'zero_shot'}:
        lines.append('Seed topics:' if method == 'guided' else 'Predefined topic descriptions:')
        lines.extend(f'  {name} | {text}' for name, text in settings.get('topic_definitions', []))
    if method == 'guided':
        lines.append('Seeds guide discovery; they do not guarantee a topic or determine its final name.')
    if method == 'zero_shot':
        lines.append(f"Minimum cosine similarity: {settings.get('zeroshot_min_similarity', 0.7):.2f} (not confidence)")
        lines.append('Each passage matches at most one predefined category; unmatched passages enter discovery when enough remain.')
        initial = info.get('initial_matching_run_id')
        diagnostics = info.get('zero_shot', {})
        if initial:
            lines.append('Matching counts below describe the initial matching run, before subsequent coding/refinement.')
        else:
            lines.append('Matching counts describe the completed model run, before researcher edits.')
        for key, label in [('matched_passage_count', 'Initially matched passages'), ('unmatched_before_discovery', 'Initially unmatched passages')]:
            if key in diagnostics:
                lines.append(f'{label}: {diagnostics[key]}')
        if diagnostics.get('residual_discovery_skipped'):
            lines.append('Unmatched discovery was skipped: too few passages; these remained Uncategorized.')
        names = diagnostics.get('unmatched_category_names', [])
        if names:
            lines.append('Categories with no initial matches: ' + ', '.join(names))
    return '\n'.join(lines)
