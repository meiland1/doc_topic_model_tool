"""Plain tables for the CSV and Excel exports, and the About this export text.

build_export() (research.py) gathers the full record; this turns it into
rows a researcher can read in Excel: one row per theme for the codebook, one
row per passage per theme for coded passages. "Include technical columns"
adds IDs and character positions at the end. The JSON codebook keeps
everything and doesn't go through here.
"""
from __future__ import annotations

import re

from qualitative_themes.analysis.settings import settings_report

CODEBOOK_COLUMNS = ["Theme", "Description", "Keywords", "Passages", "Documents", "Named by",
                    "Example 1", "Example 2", "Example 3", "Comments"]
CODEBOOK_TECHNICAL = ["Theme ID", "Where the theme came from", "Document names", "Analysis ID"]
PASSAGE_COLUMNS = ["Document", "Page", "Theme", "Passage", "Coded by", "Comments"]
PASSAGE_TECHNICAL = ["Document ID", "Start character", "End character", "Source file hash", "Theme ID",
                     "Coding ID", "Document status"]

KEYWORD_NAME = re.compile(r"^Theme \d+: ")


def _thread_text(thread) -> str:
    """A comment and its replies as plain text for one cell."""
    return "\n".join([thread["body"], *("  ↳ " + reply["body"] for reply in thread["replies"])])


def _comments(threads) -> str:
    return "\n\n".join(_thread_text(thread) for thread in threads)


def named_by(entry) -> str:
    """You / AI / Your list / Keywords: who gave the theme its current name."""
    if entry["label_source"] == "researcher":
        return "You"
    if entry["label_source"] == "predefined":
        return "Your list"
    return "Keywords" if KEYWORD_NAME.match(entry["theme"]) else "AI"


def codebook_table(data, technical=False):
    """(headers, rows) with one row per theme."""
    run_id = data["metadata"]["analysis_id"] or ""
    rows = []
    for entry in data["themes"]:
        examples = [item["quote"] for item in entry["representative_evidence"]][:3]
        examples += [""] * (3 - len(examples))
        row = [entry["theme"], entry["description"], ", ".join(entry["keywords"]), entry["unique_coded_ranges"],
               entry["documents_covered"], named_by(entry), *examples, _comments(entry["theme_comments"])]
        if technical:
            row += [entry["theme_id"], entry["topic_origin"], "; ".join(entry["documents"]), run_id]
        rows.append(row)
    return CODEBOOK_COLUMNS + (CODEBOOK_TECHNICAL if technical else []), rows


def passages_table(data, technical=False):
    """(headers, rows) with one row per passage per theme, in document order."""
    rows = []
    for passage in data["passages"]:
        pages = ", ".join(map(str, passage["pages"]))
        comments = _comments(passage["comments"])
        for assignment in passage["assignments"]:
            row = [passage["document"], pages, assignment["theme"], passage["quote"],
                   "You" if assignment["source"] == "researcher" else "Suggested", comments]
            if technical:
                row += [passage["document_id"], passage["start_char"], passage["end_char"], passage["source_hash"],
                        assignment["theme_id"], assignment["assignment_id"], passage["document_status"]]
            rows.append(row)
    return PASSAGE_COLUMNS + (PASSAGE_TECHNICAL if technical else []), rows


ABOUT_COLUMNS = {
    "codebook": [
        ("Theme", "The theme's name."),
        ("Description", "The AI's or your description, if there is one."),
        ("Keywords", "Words that set this theme's passages apart, most typical first."),
        ("Passages", "How many passages are in the theme. A passage in two themes counts in both, "
                     "so the column can add up to more than the number of passages."),
        ("Documents", "How many documents have at least one passage in the theme."),
        ("Named by", "You, AI (theme naming), Your list (your own list of themes) or Keywords (no name yet)."),
        ("Example 1–3", "Passages that show the theme well."),
        ("Comments", "Comments on the theme, with replies."),
    ],
    "passages": [
        ("Document", "The document the passage comes from."),
        ("Page", "Page number(s), for PDFs and Word files that have them."),
        ("Theme", "One theme per row: a passage in two themes has two rows."),
        ("Passage", "The passage's exact text."),
        ("Coded by", "You (your coding, or a suggestion you kept) or Suggested (found by the tool, not yet "
                     "kept or rejected)."),
        ("Comments", "Comments on any part of the passage, with replies."),
    ],
}


def about_text(data, contents, technical=False) -> str:
    """The About this export text: what's in the file, the analysis settings, and project comments.

    `contents` is a list of "codebook" / "passages" saying which tables the file holds.
    """
    meta = data["metadata"]
    lines = [f"About this export: {meta['project']}", "",
             f"Analysis: {meta['analysis']}",
             f"Exported: {meta['exported_utc'][:19].replace('T', ' ')} (UTC)",
             f"Themes: {len(data['themes'])} · passages: {meta['unique_ranges']}", ""]
    if meta["analysis_id"]:
        lines.append("Suggested passages are included unless you rejected them; your own coding always is.")
    else:
        lines.append("Only your own coding is included (no analysis was chosen).")
    for kind in contents:
        lines += ["", "Columns in " + ("the codebook" if kind == "codebook" else "coded passages"), ""]
        width = max(len(name) for name, _ in ABOUT_COLUMNS[kind])
        lines += [f"  {name + ':':<{width + 1}} {text}" for name, text in ABOUT_COLUMNS[kind]]
    if technical:
        lines += ["", "Technical columns: IDs match the project database; character positions count from 0, "
                      "start included, end not."]
    if meta["analysis_id"]:
        lines += ["", settings_report(meta["settings"], meta["model_info"]),
                  "", f"Embedding model: {meta['embedding_model']} ({meta['embedding_revision']})",
                  f"Naming model: {meta['naming_model'] or 'none (keyword names)'}"]
    if meta.get("document_comments"):
        lines += ["", "Comments on documents", ""]
        lines += [f"  {c['document']}: " + _thread_text(c).replace("\n", "\n    ") for c in meta["document_comments"]]
    if meta["project_comments"]:
        lines += ["", "Comments on the whole project", ""]
        lines += ["  " + ("[pinned] " if c["pinned"] else "") + _thread_text(c).replace("\n", "\n  ")
                  for c in meta["project_comments"]]
    return "\n".join(lines) + "\n"
