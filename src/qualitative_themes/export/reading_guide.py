"""Export a printable reading guide: per theme, where to read and the passages to start with."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from qualitative_themes.analysis.overview import build_overview, reading_queue
from qualitative_themes.storage import comments as comment_store
from qualitative_themes.storage.database import connect
from qualitative_themes.storage.history import analysis_title

MAX_PASSAGES = 12


def _page_lookup(directory):
    """({doc: [(start, end, page)]}, {doc: text}) for page numbers and quotes."""
    pages = {}
    with connect(directory) as conn:
        for doc, page, start, end in conn.execute("SELECT document_id, page_number, start_char, end_char FROM document_pages "
                                                  "WHERE page_number IS NOT NULL"):
            pages.setdefault(doc, []).append((start, end, page))
        texts = dict(conn.execute("SELECT id, canonical_text FROM documents"))
    return pages, texts


def export_reading_guide(directory: Path, run_id: str | None, destination: Path) -> int:
    """Write a .docx guide. Returns the number of themes included.

    Uses the same overview and reading queue as the Overview screen, so the
    order matches what the researcher sees in the app. Overwrites `destination`.
    """
    from docx import Document
    from docx.shared import Pt, RGBColor
    import json

    directory = Path(directory)
    overview = build_overview(directory, run_id)
    pages, texts = _page_lookup(directory)
    title = "Manual coding"
    if run_id:
        with connect(directory) as conn:
            row = conn.execute("SELECT settings_json, completed_at FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
        title = analysis_title(json.loads(row[0]), row[1])
    document = Document()
    document.styles["Normal"].font.size = Pt(10.5)
    document.add_heading("Reading guide", 0)
    intro = document.add_paragraph()
    intro.add_run(f"{title} · exported {date.today():%B %d, %Y}").italic = True
    document.add_paragraph("Themes are preliminary pointers produced by a model. Read the passages in context; "
                           "core passages fit their theme most closely, borderline ones least. "
                           "Similarity is not confidence.")
    if overview["coverage"]:
        cover = overview["coverage"]
        document.add_paragraph(f"Reviewed so far: {cover['reviewed']} of {cover['passages']} passages "
                               f"({cover['share']:.0%}).")
    comments_by_doc = {}
    for doc, _name in overview["documents"]:
        comments_by_doc[doc] = [root for root, _ in comment_store.list_threads(directory, doc, include_resolved=True)
                                if root.scope == "passage"]
    for theme in overview["themes"]:
        document.add_heading(theme["label"], 1)
        meta = f"{theme['passages']} passages in {theme['documents']} documents"
        if theme["keywords"]:
            meta += " · keywords: " + ", ".join(theme["keywords"])
        document.add_paragraph(meta).runs[0].font.color.rgb = RGBColor(0x52, 0x64, 0x78)
        for root, _replies in comment_store.list_comments(directory, theme_id=theme["theme_id"], include_resolved=True):
            if root.scope == "theme":
                note = document.add_paragraph()
                note.add_run("Comment on this theme: ").italic = True
                note.add_run(root.body)
        queue = reading_queue(directory, run_id, [theme["theme_id"]])
        by_doc = {}
        for item in queue:
            by_doc.setdefault(item["document"], []).append(item)
        where = document.add_paragraph("Where to read: ")
        where.add_run("; ".join(f"{name} ({len(items)})" for name, items in
                                sorted(by_doc.items(), key=lambda kv: -len(kv[1]))))
        for item in queue[:MAX_PASSAGES]:
            text = texts.get(item["document_id"], "")[item["start"]:item["end"]]
            page = next((p for s, e, p in pages.get(item["document_id"], []) if s <= item["start"] < e), None)
            label = (item["fit"] or {}).get("label", "")
            paragraph = document.add_paragraph(style="List Bullet")
            heading = paragraph.add_run(item["document"] + (f", p. {page}" if page else "")
                                        + (f" · {label}" if label else "") + ": ")
            heading.bold = True
            quote = " ".join(text.split())
            paragraph.add_run("“" + (quote[:600] + "…" if len(quote) > 600 else quote) + "”")
            for comment in comments_by_doc.get(item["document_id"], []):
                if comment.start < item["end"] and item["start"] < comment.end:
                    note = document.add_paragraph(style="List Bullet 2")
                    note.add_run("Comment: ").italic = True
                    note.add_run(comment.body)
        if len(queue) > MAX_PASSAGES:
            document.add_paragraph().add_run(f"…and {len(queue) - MAX_PASSAGES} more passages in the app.").italic = True
    destination = Path(destination)
    temporary = destination.with_suffix(".partial.docx")
    document.save(temporary)
    temporary.replace(destination)
    return len(overview["themes"])
