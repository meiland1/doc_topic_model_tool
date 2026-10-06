"""Project-local setup choices in existing audit storage; no schema migration."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from uuid import uuid4

from qualitative_themes.analysis.settings import AnalysisSettings, settings_from_dict
from qualitative_themes.storage.database import connect


def save_setup(directory, settings, unit):
    with connect(directory) as conn:
        conn.execute("INSERT INTO history_events VALUES (?, ?, 'analysis_setup_selected', ?)",
                     (str(uuid4()), datetime.now(timezone.utc).isoformat(),
                      json.dumps({"settings": asdict(settings), "unit": unit})))


def load_setup(directory):
    with connect(directory) as conn:
        row = conn.execute("SELECT details_json FROM history_events WHERE event_type='analysis_setup_selected' ORDER BY rowid DESC LIMIT 1").fetchone()
        if row:
            data = json.loads(row[0])
            return settings_from_dict(data["settings"]), data["unit"]
        row = conn.execute("SELECT settings_json, model_info_json FROM analysis_runs WHERE status='completed' ORDER BY rowid DESC LIMIT 1").fetchone()
        if row:
            data = json.loads(row[0])
            settings = settings_from_dict(data)
            attempts = json.loads(row[1]).get("labeling_attempts", [])
            if "ollama_model" not in data and attempts:
                settings = replace(settings, ollama_model=attempts[-1].get("model"))
            return settings, data.get("analysis_unit", "Standard passages")
    return AnalysisSettings(), "Standard passages"


def result_summary(directory, run_id):
    with connect(directory) as conn:
        # Derived from immutable original numeric assignments, unaffected by human merges.
        rows = conn.execute("SELECT bertopic_id, count(*) FROM model_suggestions WHERE run_id=? GROUP BY bertopic_id", (run_id,)).fetchall()
    return len([topic for topic, _ in rows if topic != -1]), sum(n for topic, n in rows if topic == -1)
