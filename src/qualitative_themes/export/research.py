"""Read-only, consistent exports of the researcher's effective workspace coding."""
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

from qualitative_themes.storage.coding import (
    analysis_document_ids_from_connection, effective_assignments_from_connection,
)
from qualitative_themes.storage.database import connect, DB_NAME
from qualitative_themes.storage.history import analysis_title
from qualitative_themes.storage.provenance import theme_provenance_from_connection, method_label, origin_text
from qualitative_themes.storage.theme_keywords import theme_keywords_from_connection

RANGE_COUNTS = ("One row per unique (document, start, end) range. All active themes on that exact range "
                "are grouped together. Overlapping ranges remain separate. Offsets are zero-based Python "
                "character offsets; start included, end excluded. Unaccepted model suggestions are identified.")
THEME_COUNTS = ("Unique (document, start, end) ranges within each theme. A multi-theme range counts once "
                "in each assigned theme; summing theme counts can exceed the total unique-range count.")


def build_export(directory, run_id=None, progress=None, should_cancel=None):
    """Use one SQLite read transaction for every assignment, label, and note."""
    progress = progress or (lambda message: None)
    def check():
        if should_cancel and should_cancel():
            from qualitative_themes.analysis.topics import AnalysisCancelled
            raise AnalysisCancelled()
    check()
    with connect(directory) as conn:
        conn.execute("BEGIN")
        project = conn.execute("SELECT id,name FROM project").fetchone()
        settings, info, completed, title = {}, {}, None, "Manual coding only"
        scope = None
        if run_id:
            row = conn.execute("SELECT settings_json,model_info_json,completed_at FROM analysis_runs WHERE id=? AND status='completed'", (run_id,)).fetchone()
            if not row:
                raise ValueError("Choose a completed analysis or Manual coding only to export.")
            settings, info, completed = json.loads(row[0]), json.loads(row[1]), row[2]
            title = analysis_title(settings, completed)
            scope = analysis_document_ids_from_connection(conn, run_id)
        documents = {doc: {"name": name, "status": status, "source_hash": digest}
                     for doc, name, status, digest in conn.execute("SELECT id,original_name,extraction_status,source_hash FROM documents ORDER BY imported_at,rowid")
                     if scope is None or doc in scope}
        doc_order = {doc: index for index, doc in enumerate(documents)}
        theme_rows = conn.execute("SELECT id,coalesce(user_label,generated_label,'Unnamed theme'),description,creation_source,user_label FROM themes WHERE status='active' ORDER BY lower(coalesce(user_label,generated_label,'Unnamed theme')),id").fetchall()
        visible = {r[0] for r in conn.execute("SELECT theme_id FROM model_suggestions WHERE run_id=?", (run_id,))} if run_id else set()
        notes = conn.execute("SELECT n.id,n.document_id,n.theme_id,n.coding_id,n.body,n.created_at,c.document_id,c.theme_id,c.start_char,c.end_char,c.state FROM notes n LEFT JOIN codings c ON c.id=n.coding_id ORDER BY n.created_at,n.rowid").fetchall()
        page_spans = {}
        text_cache = {}
        def evidence(doc, start, end):
            if doc not in text_cache:
                text_cache[doc] = conn.execute("SELECT canonical_text FROM documents WHERE id=?", (doc,)).fetchone()[0]
                page_spans[doc] = conn.execute("SELECT page_number,paragraph_number,start_char,end_char FROM document_pages WHERE document_id=? ORDER BY start_char", (doc,)).fetchall()
            text = text_cache[doc]
            if not 0 <= start < end <= len(text):
                raise ValueError("A saved coding has invalid source offsets; export stopped.")
            spans = [s for s in page_spans[doc] if s[2] < end and s[3] > start]
            return {"document": documents[doc]["name"], "document_id": doc,
                    "document_status": documents[doc]["status"], "source_hash": documents[doc]["source_hash"],
                    "pages": sorted({s[0] for s in spans if s[0] is not None}),
                    "paragraphs": sorted({s[1] for s in spans if s[1] is not None}),
                    "start_char": start, "end_char": end, "quote": text[start:end]}
        doc_notes, theme_notes, coding_notes = {}, {}, {}
        for nid, did, tid, cid, body, created, cdoc, ctheme, start, end, state in notes:
            note = {"id": nid, "body": body, "created_utc": created}
            if did:
                doc_notes.setdefault(did, []).append(note)
            if tid:
                theme_notes.setdefault(tid, []).append(note)
            if cid:
                coding_notes.setdefault((ctheme, cdoc, start, end), []).append({**note, "coding_state": state})
        representatives = {}
        if run_id:
            rows = conn.execute("SELECT m.theme_id,m.bertopic_id,s.id,s.document_id,s.start_char,s.end_char FROM model_suggestions m JOIN segments s ON s.id=m.segment_id WHERE m.run_id=?", (run_id,)).fetchall()
            for tid, topic, sid, doc, start, end in rows:
                if sid in info.get("representative_segment_ids", {}).get(str(topic), []):
                    representatives.setdefault(tid, set()).add((doc, start, end))
        codebook, range_groups = [], {}
        for number, (tid, label, description, creation, user_label) in enumerate(theme_rows):
            check()
            assignments = effective_assignments_from_connection(conn, tid, run_id)
            if not assignments and not (tid in visible if run_id else creation == "researcher"):
                continue
            progress(f"Collecting evidence for theme {number + 1}/{len(theme_rows)}…")
            assignments.sort(key=lambda r: (doc_order.get(r[1], len(doc_order)), r[2], r[3]))
            keys = [(doc, start, end) for _, doc, start, end, _ in assignments]
            rep_keys = representatives.get(tid, set())
            selected = sorted(keys, key=lambda key: (key not in rep_keys, doc_order.get(key[0], len(doc_order)), key[1], key[2]))[:3]
            covered = sorted({doc for doc, _, _ in keys}, key=lambda doc: doc_order[doc])
            words = theme_keywords_from_connection(conn, tid, run_id)
            provenance = theme_provenance_from_connection(conn, tid, run_id)
            entry = {"topic_origin": origin_text(provenance), "topic_origins": provenance["origins"],
                     "supplied_category_names": provenance["supplied_names"], "theme_id": tid, "theme": label, "description": description or "",
                     "label_source": "researcher" if user_label is not None or creation == "researcher" else "predefined" if label in provenance["supplied_names"] else "generated",
                     "keywords": words, "unique_coded_ranges": len(set(keys)),
                     "documents_covered": len(covered), "documents": [documents[doc]["name"] for doc in covered],
                     "representative_evidence": [evidence(*key) for key in selected],
                     "theme_notes": theme_notes.get(tid, []),
                     "passage_notes": [note for key in keys for note in coding_notes.get((tid, *key), [])],
                     "document_notes": [{"document": documents[doc]["name"], **note} for doc in covered for note in doc_notes.get(doc, [])]}
            codebook.append(entry)
            for aid, doc, start, end, origin in assignments:
                key = doc, start, end
                group = range_groups.setdefault(key, [])
                group.append({"topic_origin": origin_text(provenance), "topic_origins": provenance["origins"],
                              "supplied_category_names": provenance["supplied_names"], "theme_id": tid, "theme": label, "source": origin,
                              "assignment_id": aid, "notes": coding_notes.get((tid, doc, start, end), [])})
        passages = []
        for key in sorted(range_groups, key=lambda key: (doc_order[key[0]], key[1], key[2])):
            check()
            assignments = range_groups[key]
            passage = evidence(*key)
            passage.update({"themes": [row["theme"] for row in assignments], "assignments": assignments,
                            "document_notes": doc_notes.get(key[0], [])})
            passages.append(passage)
        embedding = info.get("embedding_identity") or info.get("source_model_identity") or {}
        attempts = info.get("labeling_attempts", [])
        topic_ids = {}
        if run_id:
            for tid, topic in conn.execute("SELECT theme_id,bertopic_id FROM model_suggestions WHERE run_id=?", (run_id,)):
                topic_ids.setdefault(tid, set()).add(topic)
        metadata = {"outlier_theme_ids": [tid for tid, topics in topic_ids.items() if topics == {-1}], "format_version": 2, "exported_utc": datetime.now(timezone.utc).isoformat(),
                    "project": project[1], "project_id": project[0], "analysis": title,
                    "analysis_id": run_id, "completed_utc": completed,
                    "analysis_unit": settings.get("analysis_unit", "Manual ranges"),
                    "analysis_method": settings.get("analysis_method", "general") if run_id else "manual",
                    "analysis_method_label": method_label(settings, manual=not run_id),
                    "settings": settings, "model_info": info,
                    "embedding_model": embedding.get("model") or settings.get("embedding_model", ""),
                    "embedding_revision": embedding.get("revision") or settings.get("embedding_revision", ""),
                    "naming_model": attempts[-1].get("model") if attempts else "Keyword fallback",
                    "parent_analysis_id": info.get("parent_run_id"), "count_definition": RANGE_COUNTS,
                    "theme_count_definition": THEME_COUNTS, "unique_ranges": len(passages),
                    "theme_assignments": sum(len(row["assignments"]) for row in passages),
                    "coding_scope": ("Active researcher codings plus non-rejected model suggestions from the selected run; researcher coding takes precedence at identical theme/range." if run_id else "Active researcher codings across the project; unaccepted model suggestions excluded."),
                    "notes_policy": "Retained notes on earlier removed coding records are included when the exact range/theme is currently active and carry their coding_state."}
    return {"metadata": metadata, "themes": codebook, "passages": passages}


def _destination(directory, destination):
    destination = Path(destination).expanduser().resolve()
    root = Path(directory).resolve()
    protected = {root / DB_NAME}
    if destination in protected or destination == root or any(parent in {root / "sources", root / "models"} for parent in destination.parents):
        raise ValueError("Choose an export location outside the project database, sources, and models.")
    return destination


def _check_cancel(should_cancel):
    if should_cancel and should_cancel():
        from qualitative_themes.analysis.topics import AnalysisCancelled
        raise AnalysisCancelled()


def _write_atomic(destination, write, encoding, should_cancel=None):
    # Do not replace the previous export if a write fails.
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as stream:
            write(stream)
        _check_cancel(should_cancel)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _context(metadata):
    return {"analysis_method": metadata["analysis_method"], "analysis_method_label": metadata["analysis_method_label"],
            "analysis_configuration": json.dumps({"analysis_method": metadata["analysis_method"],
                "topic_definitions": metadata["settings"].get("topic_definitions", []),
                "zeroshot_min_similarity": metadata["settings"].get("zeroshot_min_similarity", 0.7) if metadata["analysis_method"] == "zero_shot" else None}, ensure_ascii=False),
            "analysis": metadata["analysis"], "analysis_id": metadata["analysis_id"] or "",
            "analysis_unit": metadata["analysis_unit"], "project": metadata["project"],
            "exported_utc": metadata["exported_utc"], "embedding_model": metadata["embedding_model"],
            "embedding_revision": metadata["embedding_revision"], "naming_model": metadata["naming_model"] or "Keyword fallback",
            "parent_analysis_id": metadata["parent_analysis_id"] or ""}


def write_codebook(directory, run_id, destination, progress=None, should_cancel=None):
    destination = _destination(directory, destination)
    data = build_export(directory, run_id, progress, should_cancel)
    if destination.suffix.lower() == ".json":
        # Evidence and full provenance included; no change to the project itself.
        _write_atomic(destination, lambda stream: json.dump({"metadata": data["metadata"], "themes": data["themes"]}, stream, ensure_ascii=False, indent=2), "utf-8", should_cancel)
    elif destination.suffix.lower() == ".csv":
        context = _context(data["metadata"])
        fields = ["topic_origin", "topic_origins", "supplied_category_names", "theme", "description", "keywords", "unique_coded_ranges", "documents_covered", "documents", "label_source",
                  "representative_evidence", "theme_notes", "passage_notes", "document_notes", "theme_id", *context, "count_definition"]
        def write(stream):
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for entry in data["themes"]:
                _check_cancel(should_cancel)
                row = dict(entry)
                for key in ["topic_origins", "supplied_category_names", "keywords", "documents", "representative_evidence", "theme_notes", "passage_notes", "document_notes"]:
                    row[key] = json.dumps(row[key], ensure_ascii=False)
                writer.writerow({**row, **context, "count_definition": THEME_COUNTS})
        _write_atomic(destination, write, "utf-8-sig", should_cancel)
    else:
        raise ValueError("Codebooks must use a .csv or .json filename.")
    return len(data["themes"]), data["metadata"]["unique_ranges"]


def write_coded_passages(directory, run_id, destination, progress=None, should_cancel=None):
    destination = _destination(directory, destination)
    if destination.suffix.lower() != ".csv":
        raise ValueError("Coded passages must use a .csv filename.")
    data = build_export(directory, run_id, progress, should_cancel)
    context = _context(data["metadata"])
    fields = ["document", "document_status", "pages", "paragraphs", "start_char", "end_char", "quote", "themes",
              "assignment_source", "passage_notes", "document_notes", "assignments_json", "document_id", "source_hash", *context, "count_definition"]
    def write(stream):
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for passage in data["passages"]:
            _check_cancel(should_cancel)
            notes = {note["id"]: note for assignment in passage["assignments"] for note in assignment["notes"]}
            row = {key: passage[key] for key in ["document", "document_status", "start_char", "end_char", "quote", "document_id", "source_hash"]}
            row.update({"pages": "; ".join(map(str, passage["pages"])), "paragraphs": "; ".join(map(str, passage["paragraphs"])),
                        "themes": json.dumps(passage["themes"], ensure_ascii=False),
                        "assignment_source": "; ".join(dict.fromkeys(assignment["source"] for assignment in passage["assignments"])),
                        "passage_notes": "\n\n".join(note["body"] for note in notes.values()),
                        "document_notes": "\n\n".join(note["body"] for note in passage["document_notes"]),
                        "assignments_json": json.dumps(passage["assignments"], ensure_ascii=False), **context, "count_definition": RANGE_COUNTS})
            writer.writerow(row)
    _write_atomic(destination, write, "utf-8-sig", should_cancel)
    return data["metadata"]["unique_ranges"], data["metadata"]["theme_assignments"]
