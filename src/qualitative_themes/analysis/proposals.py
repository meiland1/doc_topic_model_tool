"""Reviewing, applying and cancelling a refinement proposal (see refinement.py for making one).

    review_proposal()     how proposed groups overlap the old themes
    apply_proposal()      researcher maps each proposed theme to an old identity
                          (or a new one); the run becomes 'completed'
    cancel_proposal()     run becomes 'cancelled', proposed themes 'discarded'
"""
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4

from qualitative_themes.analysis.labels import _protected
from qualitative_themes.storage.database import connect


def _event(conn, kind, details):
    conn.execute("INSERT INTO history_events VALUES (?, ?, ?, ?)",
                 (str(uuid4()), datetime.now(timezone.utc).isoformat(), kind, json.dumps(details)))


def _fingerprint(conn, source_id):
    # Proposed themes are excluded; edits to real research decisions invalidate review.
    """Hash of the source run plus all codings/comments/themes; changes invalidate a proposal."""
    data = {
        "run": conn.execute(
            "SELECT settings_json,model_info_json,status FROM analysis_runs WHERE id=?", (source_id,)
        ).fetchall(),
        "suggestions": conn.execute(
            "SELECT id,theme_id,bertopic_id FROM model_suggestions WHERE run_id=? ORDER BY id", (source_id,)
        ).fetchall(),
        "themes": conn.execute(
            "SELECT * FROM themes WHERE status NOT IN ('proposed','discarded') ORDER BY id"
        ).fetchall(),
        "codings": conn.execute("SELECT * FROM codings ORDER BY id").fetchall(),
        "comments": conn.execute(
            "SELECT id,theme_id,state FROM comments WHERE theme_id IS NOT NULL ORDER BY id"
        ).fetchall(),
        "sources": conn.execute(
            "SELECT id,source_hash,canonical_text,extraction_status FROM documents ORDER BY id"
        ).fetchall(),
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _groups(conn, run_id):
    rows = conn.execute(
        """SELECT m.theme_id,m.bertopic_id,s.document_id,s.start_char,s.end_char
        FROM model_suggestions m JOIN segments s ON s.id=m.segment_id WHERE m.run_id=?""",
        (run_id,),
    ).fetchall()
    groups, ordinary = defaultdict(set), set()
    for theme, topic, doc, start, end in rows:
        groups[theme].add((doc, start, end))
        if topic != -1:
            ordinary.add(theme)
    # A human merge into a regular theme is respected in the saved partition.
    outliers = {r for theme, ranges in groups.items() if theme not in ordinary for r in ranges}
    return groups, outliers


def review_proposal(directory, proposal_id):
    """Summary for the review dialog: per-theme rows with recommended old identities,
    before/after counts, which researcher decisions are affected, and `stale`
    (True if research decisions changed since the proposal was made).
    """
    with connect(directory) as conn:
        row = conn.execute(
            "SELECT model_info_json FROM analysis_runs WHERE id=? AND status='proposed'", (proposal_id,)
        ).fetchone()
        if not row:
            raise ValueError("This proposal is no longer awaiting review.")
        info = json.loads(row[0])
        source_id = info["parent_run_id"]
        old, old_outliers = _groups(conn, source_id)
        new, new_outliers = _groups(conn, proposal_id)
        labels = {
            tid: label
            for tid, label in conn.execute("SELECT id,coalesce(user_label,generated_label,'Unnamed') FROM themes")
        }
        outlier_themes = {theme for topic, theme in info["topic_theme_map"].items() if int(topic) == -1}
        old_outlier_themes = {tid for tid, ranges in old.items() if ranges <= old_outliers}
        items = []
        for tid, ranges in new.items():
            candidates = []
            for oid, previous in old.items():
                if (tid in outlier_themes) != (oid in old_outlier_themes):
                    continue
                overlap = len(ranges & previous)
                if overlap:
                    candidates.append(
                        {
                            "theme_id": oid,
                            "label": labels[oid],
                            "shared": overlap,
                            "old_count": len(previous),
                            "exact": previous == ranges,
                        }
                    )
            candidates.sort(key=lambda c: (-c["shared"], c["label"], c["theme_id"]))
            exact = [c["theme_id"] for c in candidates if c["exact"]]
            recommended = exact[0] if len(exact) == 1 else None
            items.append(
                {
                    "theme_id": tid,
                    "label": labels[tid],
                    "count": len(ranges),
                    "candidates": candidates,
                    "recommended": recommended,
                    "ambiguous": bool(candidates) and recommended is None,
                    "outlier": tid in outlier_themes,
                }
            )
        recommended = {item["theme_id"]: item["recommended"] for item in items}
        old_by_range = {r: tid for tid, ranges in old.items() for r in ranges}
        new_by_range = {r: tid for tid, ranges in new.items() for r in ranges}
        shared = set(old_by_range) & set(new_by_range)
        changed = sum(old_by_range[r] != recommended[new_by_range[r]] for r in shared)
        retired = [labels[oid] for oid in old if oid not in set(recommended.values())]
        human = []
        for oid in old:
            label, count, note_count, user = conn.execute(
                """SELECT coalesce(t.user_label,t.generated_label),
                (SELECT count(*) FROM codings WHERE theme_id=t.id),
                (SELECT count(*) FROM comments WHERE theme_id=t.id AND parent_id IS NULL AND state != 'draft'),
                t.user_label FROM themes t WHERE t.id=?""",
                (oid,),
            ).fetchone()
            if count or note_count or user is not None:
                human.append(
                    {
                        "theme_id": oid,
                        "label": label,
                        "codings": count,
                        "comments": note_count,
                        "matched": oid in set(recommended.values()),
                    }
                )
        return {
            "proposal_id": proposal_id,
            "source_id": source_id,
            "operation": info["refinement_operation"],
            "themes": items,
            "old_theme_count": len(old) - len(old_outlier_themes),
            "new_theme_count": len(new) - len(outlier_themes),
            "retired": retired,
            "changed_memberships": changed,
            "shared_passages": len(shared),
            "new_passages": len(set(new_by_range) - shared),
            "retired_passages": len(set(old_by_range) - shared),
            "old_uncategorized": len(old_outliers),
            "new_uncategorized": len(new_outliers),
            "old_labels": {tid: labels[tid] for tid in old},
            "human_decisions": human,
            "stale": _fingerprint(conn, source_id) != info["source_fingerprint"],
        }


def proposal_evidence(directory, proposal_id, theme_id):
    """Canonical evidence and exact previous memberships for a review row."""
    with connect(directory) as conn:
        row = conn.execute(
            "SELECT model_info_json FROM analysis_runs WHERE id=? AND status='proposed'", (proposal_id,)
        ).fetchone()
        if row is None:
            raise ValueError("This proposal is no longer awaiting review.")
        source_id = json.loads(row[0])["parent_run_id"]
        old, _ = _groups(conn, source_id)
        labels = {
            tid: label
            for tid, label in conn.execute("SELECT id,coalesce(user_label,generated_label,'Unnamed') FROM themes")
        }
        previous = {key: labels[tid] for tid, ranges in old.items() for key in ranges}
        rows = conn.execute(
            """SELECT d.original_name,s.document_id,s.start_char,s.end_char,d.canonical_text
            FROM model_suggestions m JOIN segments s ON s.id=m.segment_id JOIN documents d ON d.id=s.document_id
            WHERE m.run_id=? AND m.theme_id=? ORDER BY d.imported_at,d.rowid,s.ordinal""",
            (proposal_id, theme_id),
        ).fetchall()
    return [
        (name, start, end, text[start:end], previous.get((doc, start, end), "New passage range"))
        for name, doc, start, end, text in rows
    ]


def apply_proposal(directory, proposal_id, mappings):
    """Reuse reviewed identities atomically; manual records are never rewritten.

    Pre: `mappings` covers every proposed theme: {proposed_id: old_theme_id or None}.
    An old identity can be reused once and only if they share passages.
    Post: the proposal is a normal completed run. Raises ValueError (and
    changes nothing) if research decisions changed since the proposal was made.
    """
    with connect(directory) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT model_info_json FROM analysis_runs WHERE id=? AND status='proposed'", (proposal_id,)
        ).fetchone()
        if row is None:
            raise ValueError("This proposal is no longer awaiting review.")
        info = json.loads(row[0])
        source = info["parent_run_id"]
        if _fingerprint(conn, source) != info["source_fingerprint"]:
            raise ValueError("Research decisions changed since preview. Cancel and prepare a new proposal.")
        old, old_outliers = _groups(conn, source)
        new, new_outliers = _groups(conn, proposal_id)
        if set(mappings) != set(new):
            raise ValueError("Review every proposed theme before applying.")
        chosen = [v for v in mappings.values() if v is not None]
        if len(chosen) != len(set(chosen)):
            raise ValueError(
                "An earlier theme identity can be reused only once. Keep separate identities for split themes."
            )
        for tid, oid in mappings.items():
            if oid is None:
                continue
            if oid not in old or not (new[tid] & old[oid]):
                raise ValueError("Theme identity reuse needs actual shared source passages.")
            if (new[tid] <= new_outliers) != (old[oid] <= old_outliers):
                raise ValueError('"Not in any theme" can\'t be matched to a regular theme.')
            if conn.execute("SELECT status FROM themes WHERE id=?", (oid,)).fetchone()[0] != "active":
                raise ValueError("An earlier theme was merged; prepare a new proposal.")
            proposed = conn.execute("SELECT generated_label,description FROM themes WHERE id=?", (tid,)).fetchone()
            if not _protected(conn, oid):
                conn.execute("UPDATE themes SET generated_label=?,description=? WHERE id=?", (*proposed, oid))
            conn.execute(
                "UPDATE model_suggestions SET theme_id=? WHERE run_id=? AND theme_id=?", (oid, proposal_id, tid)
            )
            conn.execute("UPDATE themes SET status='discarded' WHERE id=?", (tid,))
        conn.execute(
            "UPDATE themes SET status='active' WHERE status='proposed' AND id IN (SELECT theme_id FROM"
            " model_suggestions WHERE run_id=?)",
            (proposal_id,),
        )
        info["topic_theme_map"] = {topic: mappings[tid] or tid for topic, tid in info["topic_theme_map"].items()}
        for attempt in info.get("labeling_attempts", []):
            for record in attempt.get("records", []):
                record["theme_id"] = mappings.get(record["theme_id"]) or record["theme_id"]
        info["reviewed_identity_map"] = mappings
        info["applied_at"] = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE analysis_runs SET status='completed',completed_at=?,model_info_json=? WHERE id=?",
            (info["applied_at"], json.dumps(info), proposal_id),
        )
        _event(
            conn,
            "refinement_applied",
            {
                "run_id": proposal_id,
                "parent_run_id": source,
                "operation": info["refinement_operation"],
                "identity_map": mappings,
            },
        )
    return proposal_id


def cancel_proposal(directory, proposal_id):
    """Discard a proposal. Safe to call twice."""
    with connect(directory) as conn:
        row = conn.execute("SELECT status FROM analysis_runs WHERE id=?", (proposal_id,)).fetchone()
        if row is None or row[0] != "proposed":
            return
        conn.execute("UPDATE analysis_runs SET status='cancelled' WHERE id=?", (proposal_id,))
        conn.execute(
            "UPDATE themes SET status='discarded' WHERE status='proposed' AND id IN (SELECT theme_id FROM"
            " model_suggestions WHERE run_id=?)",
            (proposal_id,),
        )
        _event(conn, "refinement_cancelled", {"run_id": proposal_id})
