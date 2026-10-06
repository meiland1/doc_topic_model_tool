"""Persistent analysis titles and paged, human-readable project audit views."""
from datetime import datetime, timezone
import json
from uuid import uuid4

from qualitative_themes.storage.database import connect


def analysis_title(settings_json, completed_at):
    settings = json.loads(settings_json) if isinstance(settings_json, str) else settings_json
    return settings.get("display_name") or (
        f"Analysis {(completed_at or '')[:19].replace('T', ' ')} · {settings.get('analysis_unit', 'Passages')}")


def rename_analysis(directory, run_id, name):
    name = name.strip()
    if len(name) > 120 or any(ord(char) < 32 for char in name):
        raise ValueError("Use a single-line analysis name of at most 120 characters.")
    if name.casefold() == "manual coding only":
        raise ValueError("Choose a name other than Manual coding only.")
    with connect(directory) as conn:
        row = conn.execute("SELECT settings_json FROM analysis_runs WHERE id=? AND status='completed'", (run_id,)).fetchone()
        if not row:
            raise ValueError("Select a saved analysis to rename.")
        settings = json.loads(row[0])
        old = settings.get("display_name")
        for other_id, encoded in conn.execute("SELECT id,settings_json FROM analysis_runs WHERE status='completed' AND id!=?", (run_id,)):
            other_name = json.loads(encoded).get("display_name", "")
            if name and name.casefold() == other_name.casefold():
                raise ValueError("Another analysis already has that name.")
        if (old or "") == name:
            return
        if name:
            settings["display_name"] = name
        else:
            settings.pop("display_name", None)
        conn.execute("UPDATE analysis_runs SET settings_json=? WHERE id=?", (json.dumps(settings), run_id))
        conn.execute("INSERT INTO history_events VALUES (?, ?, 'analysis_renamed', ?)",
                     (str(uuid4()), datetime.now(timezone.utc).isoformat(),
                      json.dumps({"run_id": run_id, "old": old, "new": name or None})))


def history_runs(directory, offset=0, limit=50):
    with connect(directory) as conn:
        count = conn.execute("SELECT count(*) FROM analysis_runs").fetchone()[0]
        rows = conn.execute("SELECT id,started_at,completed_at,status,settings_json,model_info_json FROM analysis_runs ORDER BY started_at DESC,rowid DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return [{"id": rid, "started": started, "completed": completed, "status": status,
             "title": analysis_title(settings, completed or started), "settings": json.loads(settings),
             "model_info": json.loads(info)} for rid, started, completed, status, settings, info in rows], count


def analysis_details(directory, run_id):
    with connect(directory) as conn:
        row = conn.execute("SELECT started_at,completed_at,status,settings_json,model_info_json FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise ValueError("Analysis not found.")
        started, completed, status, encoded, encoded_info = row
        settings, info = json.loads(encoded), json.loads(encoded_info)
        doc_ids = settings.get("document_ids", [])
        documents = []
        for doc in doc_ids:
            name_status = conn.execute("SELECT original_name,extraction_status FROM documents WHERE id=?", (doc,)).fetchone()
            if name_status:
                documents.append(name_status[0] + (" [removed]" if name_status[1] == "removed" else ""))
        segments = conn.execute("SELECT count(*) FROM segments WHERE run_id=?", (run_id,)).fetchone()[0]
        source = info.get("parent_run_id")
        parent = conn.execute("SELECT settings_json,completed_at,started_at FROM analysis_runs WHERE id=?", (source,)).fetchone() if source else None
    lines = [analysis_title(settings, completed or started), f"Status: {status}",
             f"Started (UTC): {started}", f"Completed (UTC): {completed or 'Not completed'}",
             "Documents: " + (", ".join(documents) or "None recorded"), f"Passages: {segments}",
             f"Passage size: {settings.get('analysis_unit', 'Not recorded')}"]
    if parent:
        lines.append("Refined from: " + analysis_title(parent[0], parent[1] or parent[2]))
    for key, label in [("analysis_style", "Discovery style"), ("min_topic_size", "Minimum evidence"),
                       ("requested_theme_count", "Requested theme goal"), ("ollama_model", "Local naming model")]:
        lines.append(f"{label}: {settings.get(key) if settings.get(key) is not None else 'Automatic / not set'}")
    lines += ["Ignored keyword words: " + (", ".join(settings.get("ignored_words", [])) or "None"),
              "Keyword phrases: " + ("Words only" if settings.get("ngram_range") == [1, 1] else "Words and two-word phrases"),
              f"Originally discovered themes: {info.get('actual_theme_count', 'Not recorded')}",
              f"Originally Uncategorized passages: {info.get('uncategorized_passage_count', 'Not recorded')}"]
    embedding = info.get("embedding_identity") or info.get("source_model_identity") or {}
    lines.append("Embedding model: " + (embedding.get("model") or settings.get("embedding_model") or "Not recorded"))
    if embedding.get("revision") or settings.get("embedding_revision"):
        lines.append("Embedding revision: " + (embedding.get("revision") or settings["embedding_revision"]))
    attempts = info.get("labeling_attempts", [])
    if attempts:
        attempt = attempts[-1]
        outcomes = {}
        for record in attempt.get("records", []):
            state = record.get("status", "unknown")
            outcomes[state] = outcomes.get(state, 0) + 1
        lines.append(f"Latest naming attempt: {attempt.get('model') or 'Keyword fallback'} · {attempt.get('occurred_at', '')}")
        lines.append("Naming outcomes: " + ", ".join(f"{key.replace('_', ' ')}: {value}" for key, value in outcomes.items()))
    if info.get("packages"):
        lines.append("Package versions: " + ", ".join(f"{key} {value}" for key, value in info["packages"].items()))
    return "\n".join(lines), {"settings": settings, "model_info": info}


def history_events(directory, offset=0, limit=100):
    with connect(directory) as conn:
        total = conn.execute("SELECT count(*) FROM history_events").fetchone()[0]
        rows = conn.execute("SELECT occurred_at,event_type,details_json FROM history_events ORDER BY occurred_at DESC,rowid DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        themes, docs, runs = {}, {}, {}
        def theme(tid):
            if not tid:
                return "Theme"
            if tid not in themes:
                row = conn.execute("SELECT coalesce(user_label,generated_label,'Unnamed theme') FROM themes WHERE id=?", (tid,)).fetchone()
                themes[tid] = row[0] if row else "Earlier theme"
            return themes[tid]
        def document(did):
            if did not in docs:
                row = conn.execute("SELECT original_name FROM documents WHERE id=?", (did,)).fetchone()
                docs[did] = row[0] if row else "Document"
            return docs[did]
        def run(rid):
            if rid not in runs:
                row = conn.execute("SELECT settings_json,completed_at,started_at FROM analysis_runs WHERE id=?", (rid,)).fetchone()
                runs[rid] = analysis_title(row[0], row[1] or row[2]) if row else "Earlier analysis"
            return runs[rid]
        result = []
        for timestamp, kind, encoded in rows:
            data = json.loads(encoded)
            summary = kind.replace("_", " ").capitalize()
            if kind == "analysis_renamed":
                summary = f"Analysis renamed: {data.get('old') or 'Automatic name'} → {data.get('new') or 'Automatic name'}"
            elif kind == "theme_renamed":
                summary = f"Theme renamed: {data['old']} → {data['new']}"
            elif kind == "theme_created":
                summary = "Theme created: " + data.get("label", theme(data.get("theme_id")))
            elif kind == "themes_merged":
                summary = f"Themes merged: {data.get('source_label', 'Earlier theme')} → {data.get('target_label', 'Target theme')}"
            elif kind in {"coding_added", "coding_removed", "model_suggestion_rejected"}:
                action = {"coding_added": "Passage coded", "coding_removed": "Coding removed", "model_suggestion_rejected": "Model passage rejected"}[kind]
                summary = f"{action}: {document(data.get('document_id'))} · {theme(data.get('theme_id'))} · characters {data.get('start')}–{data.get('end')}"
            elif kind in {"analysis_completed", "analysis_proposed", "refinement_applied", "refinement_cancelled"}:
                summary += ": " + run(data.get("run_id"))
                if data.get("passages") is not None:
                    summary += f" · {data['passages']} passages"
            elif kind == "themes_named":
                attempt = data.get("attempt", {})
                summary = f"Theme naming attempt: {run(data.get('run_id'))} · {attempt.get('model') or 'Keyword fallback'}"
            elif kind in {"documents_removed", "documents_restored"}:
                summary += ": " + ", ".join(row.get("name", "Document") for row in data.get("documents", []))
            elif kind == "note_added":
                target = theme(data["theme_id"]) if data.get("theme_id") else document(data["document_id"]) if data.get("document_id") else "Coded passage"
                summary = "Note added: " + target
            elif kind == "note_updated":
                summary = "Note updated: " + data.get("new", "")[:100].replace("\n", " ")
            elif kind == "analysis_setup_selected":
                summary = f"Analysis setup saved: {data.get('unit', 'Passages')} · {data.get('settings', {}).get('analysis_style', 'Custom')}"
            result.append({"timestamp": timestamp, "summary": summary, "kind": kind, "details": data})
    return result, total
