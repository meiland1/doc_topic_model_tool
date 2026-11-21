"""Old labels from the rename sheet must not come back.

Each entry is the "Current text" of a row we decided to change. The check is
case-sensitive and covers every source file, comments and docstrings included,
so the code describes the app with the same words the researcher sees.
"""
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src" / "qualitative_themes"

RETIRED = [
    "Research Workspace", "Project guide", "Create Project", "Open Project", "Import Documents",
    "Analyze selected documents", "Select All", "Clear Selection", "Remove selected documents",
    "Restore removed documents", "Advanced analysis and local naming setup", "General discovery",
    "Guided discovery", "Zero-shot topics", "Predefined topics", "Minimum similarity", "Analysis style",
    "Passage size", "Leave out interviewer turns", "Allow only the start of oversized", "Minimum evidence",
    "Keyword phrases", "Automatically name themes", "Optional naming model", "Restore Recommended Defaults",
    "Manual coding only", "Merge theme into", "Add selected text to chosen theme", "Remove selected coding",
    "Accept selected suggestion", "Model suggestion", "Researcher coding", "Browse evidence",
    "Refine Analysis", "Review pending refinement", "Analysis History", "Analysis details",
    "Local Ollama model", "Refresh installed models", "Preview new theme names", "Theme naming details",
    "Cancel task", "Regenerate machine names", "Refresh theme words", "Propose fewer themes",
    "Run fresh discovery", "Keep new theme identity", "Theme overview", "Where each theme appears",
    "Start reading", "Export reading guide…", "Uncategorized", "Build / Refresh", "Text topic tree",
    "Open evidence in", "RECENT PROJECTS", "Advanced local setup", "Add naming model…",
    "Recover a recent project", "Restore a project backup", "Automatic recovery snapshots",
    "Create recovery snapshot now", "Recover from snapshot", "Restore project backup",
]


@pytest.mark.parametrize("old", RETIRED)
def test_retired_label_is_gone(old):
    hits = [f"{path.relative_to(SOURCE)}:{number}"
            for path in SOURCE.rglob("*.py")
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if old in line]
    assert not hits, f'"{old}" is still used: ' + ", ".join(hits)
