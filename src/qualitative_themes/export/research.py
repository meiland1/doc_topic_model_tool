"""Read-only, consistent exports of the researcher's effective workspace coding.

build_export() gathers everything in one read transaction; the write_*
functions turn that into JSON, CSV (via export/tables.py) or Excel (export/excel.py)
and replace the destination file atomically. Exports never modify the project.
"""
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
from qualitative_themes.storage.provenance import (
    NO_ANALYSIS_LABEL,
    theme_provenance_from_connection,
    method_label,
    origin_text,
)
from qualitative_themes.storage.theme_keywords import theme_keywords_from_connection
from qualitative_themes.export.tables import about_text, codebook_table, passages_table

RANGE_COUNTS = (
    "One row per unique (document, start, end) range. All active themes on that exact range "
    "are grouped together. Overlapping ranges remain separate. Offsets are zero-based Python "
    "character offsets; start included, end excluded. Unaccepted model suggestions are identified."
)
THEME_COUNTS = (
    "Unique (document, start, end) ranges within each theme. A multi-theme range counts once "
    "in each assigned theme; summing theme counts can exceed the total unique-range count."
)


def build_export(directory, run_id=None, progress=None, should_cancel=None):
    """Use one SQLite read transaction for every assignment, label, and note.

    run_id=None exports researcher codings across the whole project ("Manual
    coding only"). With a run_id it exports that run's effective assignments
    (codings + unrejected suggestions) for the run's documents only.
    Returns {'metadata', 'themes', 'passages'}. Raises AnalysisCancelled if
    should_cancel() turns True between themes.
    """
    progress = progress or (lambda message: None)

    def check():
        if should_cancel and should_cancel():
            from qualitative_themes.analysis.topics import AnalysisCancelled

            raise AnalysisCancelled()

    check()
    with connect(directory) as conn:
        conn.execute("BEGIN")
        project = conn.execute("SELECT id,name FROM project").fetchone()
        settings, info, completed, title = {}, {}, None, NO_ANALYSIS_LABEL
        scope = None
        if run_id:
            row = conn.execute(
                "SELECT settings_json,model_info_json,completed_at FROM analysis_runs WHERE id=? AND"
                " status='completed'",
                (run_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"Choose a completed analysis, or {NO_ANALYSIS_LABEL}, to export.")
            settings, info, completed = json.loads(row[0]), json.loads(row[1]), row[2]
            title = analysis_title(settings, completed)
            scope = analysis_document_ids_from_connection(conn, run_id)
        documents = {
            doc: {"name": name, "status": status, "source_hash": digest}
            for doc, name, status, digest in conn.execute(
                "SELECT id,original_name,extraction_status,source_hash FROM documents ORDER BY imported_at,rowid"
            )
            if scope is None or doc in scope
        }
        doc_order = {doc: index for index, doc in enumerate(documents)}
        theme_rows = conn.execute(
            "SELECT id,coalesce(user_label,generated_label,'Unnamed theme'),description,creation_source,user_label"
            " FROM themes WHERE status='active' ORDER BY lower(coalesce(user_label,generated_label,'Unnamed"
            " theme')),id"
        ).fetchall()
        visible = (
            {r[0] for r in conn.execute("SELECT theme_id FROM model_suggestions WHERE run_id=?", (run_id,))}
            if run_id
            else set()
        )
        comment_rows = conn.execute(
            "SELECT id,parent_id,scope,document_id,theme_id,start_char,end_char,quote,body,state,pinned,created_at "
            "FROM comments WHERE state != 'draft' ORDER BY created_at,rowid"
        ).fetchall()
        page_spans = {}
        text_cache = {}

        def evidence(doc, start, end):
            if doc not in text_cache:
                text_cache[doc] = conn.execute("SELECT canonical_text FROM documents WHERE id=?", (doc,)).fetchone()[0]
                page_spans[doc] = conn.execute(
                    "SELECT page_number,paragraph_number,start_char,end_char FROM document_pages WHERE document_id=?"
                    " ORDER BY start_char",
                    (doc,),
                ).fetchall()
            text = text_cache[doc]
            if not 0 <= start < end <= len(text):
                raise ValueError("A saved coding has invalid source offsets; export stopped.")
            spans = [s for s in page_spans[doc] if s[2] < end and s[3] > start]
            return {
                "document": documents[doc]["name"],
                "document_id": doc,
                "document_status": documents[doc]["status"],
                "source_hash": documents[doc]["source_hash"],
                "pages": sorted({s[0] for s in spans if s[0] is not None}),
                "paragraphs": sorted({s[1] for s in spans if s[1] is not None}),
                "start_char": start,
                "end_char": end,
                "quote": text[start:end],
            }

        # Threads (root + replies) grouped by what they're on. Passage comments are
        # matched to every exported passage they overlap.
        threads, project_comments, theme_comments, doc_comments, passage_comments = {}, [], {}, {}, {}
        for cid, parent, kind, did, tid, start, end, quote, body, state, pinned, created in comment_rows:
            if parent:
                if parent in threads:
                    threads[parent]["replies"].append({"id": cid, "body": body, "created_utc": created})
                continue
            thread = {
                "id": cid,
                "scope": kind,
                "body": body,
                "state": state,
                "pinned": bool(pinned),
                "created_utc": created,
                "replies": [],
            }
            threads[cid] = thread
            if kind == "project":
                project_comments.append(thread)
            elif kind == "theme":
                theme_comments.setdefault(tid, []).append(thread)
            elif kind == "document":
                doc_comments.setdefault(did, []).append(thread)
            else:
                thread.update({"start_char": start, "end_char": end, "quote": quote})
                passage_comments.setdefault(did, []).append(thread)

        def comments_on(doc, start, end):
            return [
                c
                for c in passage_comments.get(doc, ())
                if c["start_char"] < end and start < max(c["end_char"], c["start_char"] + 1)
            ]

        representatives = {}
        if run_id:
            rows = conn.execute(
                "SELECT m.theme_id,m.bertopic_id,s.id,s.document_id,s.start_char,s.end_char FROM model_suggestions m"
                " JOIN segments s ON s.id=m.segment_id WHERE m.run_id=?",
                (run_id,),
            ).fetchall()
            for tid, topic, sid, doc, start, end in rows:
                if sid in info.get("representative_segment_ids", {}).get(str(topic), []):
                    representatives.setdefault(tid, set()).add((doc, start, end))
        codebook, range_groups = [], {}
        keyword_cache = {}  # parsed run keywords, shared across themes
        for number, (tid, label, description, creation, user_label) in enumerate(theme_rows):
            check()
            assignments = effective_assignments_from_connection(conn, tid, run_id)
            if not assignments and not (tid in visible if run_id else creation == "researcher"):
                continue
            progress(f"Collecting evidence for theme {number + 1}/{len(theme_rows)}…")
            assignments.sort(key=lambda r: (doc_order.get(r[1], len(doc_order)), r[2], r[3]))
            keys = [(doc, start, end) for _, doc, start, end, _ in assignments]
            rep_keys = representatives.get(tid, set())
            selected = sorted(
                keys, key=lambda key: (key not in rep_keys, doc_order.get(key[0], len(doc_order)), key[1], key[2])
            )[:3]
            covered = sorted({doc for doc, _, _ in keys}, key=lambda doc: doc_order[doc])
            words = theme_keywords_from_connection(conn, tid, run_id, keyword_cache)
            provenance = theme_provenance_from_connection(conn, tid, run_id)
            entry = {
                "topic_origin": origin_text(provenance),
                "topic_origins": provenance["origins"],
                "supplied_category_names": provenance["supplied_names"],
                "theme_id": tid,
                "theme": label,
                "description": description or "",
                "label_source": "researcher"
                if user_label is not None or creation == "researcher"
                else "predefined"
                if label in provenance["supplied_names"]
                else "generated",
                "keywords": words,
                "unique_coded_ranges": len(set(keys)),
                "documents_covered": len(covered),
                "documents": [documents[doc]["name"] for doc in covered],
                "representative_evidence": [evidence(*key) for key in selected],
                "theme_comments": theme_comments.get(tid, []),
                "passage_comments": list({c["id"]: c for key in keys for c in comments_on(*key)}.values()),
                "document_comments": [
                    {"document": documents[doc]["name"], **c} for doc in covered for c in doc_comments.get(doc, [])
                ],
            }
            codebook.append(entry)
            for aid, doc, start, end, origin in assignments:
                key = doc, start, end
                group = range_groups.setdefault(key, [])
                group.append(
                    {
                        "topic_origin": origin_text(provenance),
                        "topic_origins": provenance["origins"],
                        "supplied_category_names": provenance["supplied_names"],
                        "theme_id": tid,
                        "theme": label,
                        "source": origin,
                        "assignment_id": aid,
                    }
                )
        passages = []
        for key in sorted(range_groups, key=lambda key: (doc_order[key[0]], key[1], key[2])):
            check()
            assignments = range_groups[key]
            passage = evidence(*key)
            passage.update(
                {
                    "themes": [row["theme"] for row in assignments],
                    "assignments": assignments,
                    "comments": comments_on(*key),
                    "document_comments": doc_comments.get(key[0], []),
                }
            )
            passages.append(passage)
        embedding = info.get("embedding_identity") or info.get("source_model_identity") or {}
        attempts = info.get("labeling_attempts", [])
        topic_ids = {}
        if run_id:
            for tid, topic in conn.execute(
                "SELECT theme_id,bertopic_id FROM model_suggestions WHERE run_id=?", (run_id,)
            ):
                topic_ids.setdefault(tid, set()).add(topic)
        metadata = {
            "outlier_theme_ids": [tid for tid, topics in topic_ids.items() if topics == {-1}],
            "format_version": 3,
            "exported_utc": datetime.now(timezone.utc).isoformat(),
            "project": project[1],
            "project_id": project[0],
            "analysis": title,
            "analysis_id": run_id,
            "completed_utc": completed,
            "analysis_unit": settings.get("analysis_unit", "Manual ranges"),
            "analysis_method": settings.get("analysis_method", "general") if run_id else "manual",
            "analysis_method_label": method_label(settings, manual=not run_id),
            "settings": settings,
            "model_info": info,
            "embedding_model": embedding.get("model") or settings.get("embedding_model", ""),
            "embedding_revision": embedding.get("revision") or settings.get("embedding_revision", ""),
            "naming_model": attempts[-1].get("model") if attempts else "Keyword fallback",
            "parent_analysis_id": info.get("parent_run_id"),
            "count_definition": RANGE_COUNTS,
            "theme_count_definition": THEME_COUNTS,
            "unique_ranges": len(passages),
            "theme_assignments": sum(len(row["assignments"]) for row in passages),
            "coding_scope": (
                "Active researcher codings plus non-rejected model suggestions from the selected run; researcher"
                " coding takes precedence at identical theme/range."
                if run_id
                else "Active researcher codings across the project; unaccepted model suggestions excluded."
            ),
            "project_comments": project_comments,
            "document_comments": [
                {"document": documents[doc]["name"], **thread}
                for doc in documents
                for thread in doc_comments.get(doc, [])
            ],
            "comments_policy": "Passage comments are listed with every exported passage they overlap. Resolved"
            " comments are included with state 'resolved'; drafts are not.",
        }
    return {"metadata": metadata, "themes": codebook, "passages": passages}


def _destination(directory, destination):
    """Resolve the output path and refuse anything inside the project's own files."""
    destination = Path(destination).expanduser().resolve()
    root = Path(directory).resolve()
    protected = {root / DB_NAME}
    if (
        destination in protected
        or destination == root
        or any(parent in {root / "sources", root / "models"} for parent in destination.parents)
    ):
        raise ValueError("Choose an export location outside the project database, sources, and models.")
    return destination


def _check_cancel(should_cancel):
    if should_cancel and should_cancel():
        from qualitative_themes.analysis.topics import AnalysisCancelled

        raise AnalysisCancelled()


def _write_atomic(destination, write, encoding, should_cancel=None):
    # Do not replace the previous export if a write fails.
    """Write to a temp file in the same folder, then os.replace(); the old file survives any failure."""
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as stream:
            write(stream)
        _check_cancel(should_cancel)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def about_path(destination: Path) -> Path:
    """Where the About this export text goes: beside the file, named after it."""
    return destination.with_name(f"{destination.stem} - About this export.txt")


def _write_csv(destination, headers, rows, should_cancel):
    def write(stream):
        writer = csv.writer(stream)
        writer.writerow(headers)
        for row in rows:
            _check_cancel(should_cancel)
            writer.writerow(row)

    _write_atomic(destination, write, "utf-8-sig", should_cancel)


def _write_about(destination, data, contents, technical, should_cancel):
    text = about_text(data, contents, technical)
    _write_atomic(about_path(destination), lambda stream: stream.write(text), "utf-8", should_cancel)


def write_codebook(directory, run_id, destination, progress=None, should_cancel=None, technical=False):
    """One row per theme (.csv), or the full record (.json). Format is chosen by suffix.

    A CSV gets an About this export text file beside it. Returns (theme_count, unique_ranges).
    """
    destination = _destination(directory, destination)
    suffix = destination.suffix.lower()
    if suffix not in {".json", ".csv"}:
        raise ValueError("Codebooks must use a .csv or .json filename.")
    data = build_export(directory, run_id, progress, should_cancel)
    if suffix == ".json":
        # Evidence and full provenance included; no change to the project itself.
        _write_atomic(
            destination,
            lambda stream: json.dump(
                {"metadata": data["metadata"], "themes": data["themes"]}, stream, ensure_ascii=False, indent=2
            ),
            "utf-8",
            should_cancel,
        )
    else:
        _write_csv(destination, *codebook_table(data, technical), should_cancel)
        _write_about(destination, data, ["codebook"], technical, should_cancel)
    return len(data["themes"]), data["metadata"]["unique_ranges"]


def write_coded_passages(directory, run_id, destination, progress=None, should_cancel=None, technical=False):
    """One CSV row per passage per theme, plus an About this export text file.

    Returns (unique_ranges, theme_assignments).
    """
    destination = _destination(directory, destination)
    if destination.suffix.lower() != ".csv":
        raise ValueError("Coded passages must use a .csv filename.")
    data = build_export(directory, run_id, progress, should_cancel)
    _write_csv(destination, *passages_table(data, technical), should_cancel)
    _write_about(destination, data, ["passages"], technical, should_cancel)
    return data["metadata"]["unique_ranges"], data["metadata"]["theme_assignments"]


def write_everything(directory, run_id, destination, progress=None, should_cancel=None, technical=False):
    """One Excel file with Themes, Passages and About sheets. Returns (theme_count, unique_ranges)."""
    destination = _destination(directory, destination)
    if destination.suffix.lower() != ".xlsx":
        raise ValueError("The Excel export must use an .xlsx filename.")
    data = build_export(directory, run_id, progress, should_cancel)
    progress = progress or (lambda message: None)
    progress("Writing the Excel file…")
    from qualitative_themes.export.excel import write_workbook

    sheets = [("Themes", *codebook_table(data, technical)), ("Passages", *passages_table(data, technical))]
    about = about_text(data, ["codebook", "passages"], technical)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    os.close(fd)
    try:
        write_workbook(Path(temporary), sheets, about, lambda: _check_cancel(should_cancel))
        _check_cancel(should_cancel)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return len(data["themes"]), data["metadata"]["unique_ranges"]
