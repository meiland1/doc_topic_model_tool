"""Names the researcher sees in more than one place.

Tab names, menu items and the name of the ungrouped theme appear in several
files (menus, help text, messages), so they live here. Renaming one is a
one-line change. Wording used in just one place stays next to its widget.
"""
from qualitative_themes.analysis.topics import UNGROUPED_LABEL

# Tabs, left to right.
TAB_DOCUMENTS = "Documents && analysis"   # "&&" shows as "&" in Qt tab and menu text
TAB_THEMES = "Themes"
TAB_READ_CODE = "Read && code"
TAB_CHARTS = "Charts"
TAB_HELP = "Help"

# Plain-text versions for labels, messages and tooltips (no "&&").
READ_CODE = "Read & code"
DOCUMENTS = "Documents & analysis"

# The group of passages BERTopic didn't put in any theme (saved under this label).
UNGROUPED = UNGROUPED_LABEL

FIND_THEMES = "Find themes"
ADJUST_THEMES = "Adjust themes"
NO_ANALYSIS = "My coding only (no analysis)"

# File menu.
NEW_PROJECT = "New project…"
OPEN_PROJECT = "Open project…"
ADD_DOCUMENTS = "Add documents…"
BACK_UP_PROJECT = "Back up project…"
RESTORE_PROJECT = "Restore a project…"
DAILY_SAFETY_COPIES = "Daily safety copies"
MAKE_SAFETY_COPY = "Make a safety copy now"
EXPORT = "Export"
EXPORT_CODEBOOK = "Codebook (CSV or JSON)…"
EXPORT_PASSAGES = "Coded passages (CSV)…"
EXPORT_READING_GUIDE = "Reading guide (Word)…"
EXPORT_EVERYTHING = "Everything (Excel)…"
EXPORT_TECHNICAL = "Include technical columns"
CLOSE_PROJECT = "Close project"

# Edit menu.
UNDO_CODING = "Undo coding"
REDO_CODING = "Redo coding"
CODE_SELECTION = "Code selection…"
UNCODE_SELECTION = "Uncode selection"
COMMENT_ON_SELECTION = "Comment on selection"
NEW_THEME = "New theme…"
RENAME_THEME = "Rename theme…"
MERGE_THEME = "Merge into another theme…"
FIND_IN_DOCUMENT = "Find in document"
SEARCH_ALL_DOCUMENTS = "Search all documents"
