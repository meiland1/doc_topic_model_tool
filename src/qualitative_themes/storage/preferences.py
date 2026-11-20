"""Project-local setup choices in existing audit storage; no schema migration.

These are stored as history_events rows so they travel with backups
without needing new tables.
"""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from uuid import uuid4

from qualitative_themes.analysis.segment import DEFAULT_UNIT, UNITS
from qualitative_themes.analysis.settings import AnalysisSettings, settings_from_dict
from qualitative_themes.storage.database import connect


def save_setup(directory, settings, unit):
    """Remember the last analysis setup so the next setup dialog starts from it."""
    with connect(directory) as conn:
        conn.execute("INSERT INTO history_events VALUES (?, ?, 'analysis_setup_selected', ?)",
                     (str(uuid4()), datetime.now(timezone.utc).isoformat(),
                      json.dumps({"settings": asdict(settings), "unit": unit})))


def load_setup(directory):
    """Returns (AnalysisSettings, unit).

    Order: last saved setup, then settings of the latest completed run, then
    defaults. Never raises for a project with no history.
    """
    with connect(directory) as conn:
        row = conn.execute("SELECT details_json FROM history_events WHERE event_type='analysis_setup_selected' ORDER BY rowid DESC LIMIT 1").fetchone()
        if row:
            data = json.loads(row[0])
            return settings_from_dict(data["settings"]), data["unit"] if data["unit"] in UNITS else DEFAULT_UNIT
        row = conn.execute("SELECT settings_json, model_info_json FROM analysis_runs WHERE status='completed' ORDER BY rowid DESC LIMIT 1").fetchone()
        if row:
            data = json.loads(row[0])
            settings = settings_from_dict(data)
            attempts = json.loads(row[1]).get("labeling_attempts", [])
            if settings.ollama_model is None and attempts:
                settings = replace(settings, ollama_model=attempts[-1].get("model"))
            unit = data.get("analysis_unit")
            return settings, unit if unit in UNITS else DEFAULT_UNIT
    return AnalysisSettings(), DEFAULT_UNIT


def result_summary(directory, run_id):
    """(theme_count, uncategorized_passages) as the model produced them, before any edits."""
    with connect(directory) as conn:
        # Derived from immutable original numeric assignments, unaffected by human merges.
        rows = conn.execute("SELECT bertopic_id, count(*) FROM model_suggestions WHERE run_id=? GROUP BY bertopic_id", (run_id,)).fetchall()
    return len([topic for topic, _ in rows if topic != -1]), sum(n for topic, n in rows if topic == -1)


def save_workspace_view(directory, view):
    """Keep one replaceable UI preference in existing project storage, including backups."""
    with connect(directory) as conn:
        conn.execute("DELETE FROM history_events WHERE event_type='workspace_view_saved'")
        conn.execute("INSERT INTO history_events VALUES (?, ?, 'workspace_view_saved', ?)",
                     (str(uuid4()), datetime.now(timezone.utc).isoformat(), json.dumps(view)))


def load_workspace_view(directory):
    """Saved layout dict (version 1), or {} if missing or unreadable."""
    with connect(directory) as conn:
        row = conn.execute("SELECT details_json FROM history_events WHERE event_type='workspace_view_saved' ORDER BY rowid DESC LIMIT 1").fetchone()
    try:
        value = json.loads(row[0]) if row else {}
        return value if isinstance(value, dict) and value.get('version') == 1 else {}
    except (ValueError, TypeError):
        return {}
