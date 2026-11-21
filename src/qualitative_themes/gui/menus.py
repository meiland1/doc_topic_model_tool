"""The File and Edit menus.

Everything project-level (create, open, add documents, back up, restore,
export) lives in File. Edit repeats what the Read & code toolbar and tree
already offer, for people who look in menus first. Items that need an open
project are switched on and off by update_menus().
"""
import sys

from PySide6.QtGui import QAction, QKeySequence

from qualitative_themes.gui import strings


# Ctrl+Q codes a selection, as in NVivo. On a Mac, Qt turns "Ctrl" into Cmd and
# Cmd+Q is Quit, so there it uses the Control key ("Meta" in Qt) instead.
CODE_KEY = "Meta+Q" if sys.platform == "darwin" else "Ctrl+Q"
UNCODE_KEY = "Meta+Shift+Q" if sys.platform == "darwin" else "Ctrl+Shift+Q"


def build_menus(window):
    """Add File and Edit to the window's menu bar.

    Pre: window.workspace exists. Post: window.project_actions lists the
    actions that need an open project; window.recovery is set up.
    """
    bar = window.menuBar()
    file_menu = bar.addMenu("&File")
    window.file_menu = file_menu
    window.project_actions = []

    def add(menu, text, handler, shortcut=None, needs_project=False):
        action = QAction(text, window)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(lambda _checked=False: handler())
        menu.addAction(action)
        if needs_project:
            window.project_actions.append(action)
        return action

    add(file_menu, strings.NEW_PROJECT, window.create, QKeySequence.StandardKey.New)
    add(file_menu, strings.OPEN_PROJECT, window.open, QKeySequence.StandardKey.Open)
    window.add_documents_action = add(file_menu, strings.ADD_DOCUMENTS, window.import_files, "Ctrl+I", True)
    file_menu.addSeparator()
    window.backup_action = add(file_menu, strings.BACK_UP_PROJECT, window.back_up_project, needs_project=True)
    add(file_menu, strings.RESTORE_PROJECT, window.restore_project)
    from qualitative_themes.gui.recovery import RecoveryController
    window.recovery = RecoveryController(window, file_menu)
    file_menu.addSeparator()
    export_menu = file_menu.addMenu(strings.EXPORT)
    window.export_menu = export_menu
    add(export_menu, strings.EXPORT_CODEBOOK, lambda: window.start_research_export("codebook"), needs_project=True)
    add(export_menu, strings.EXPORT_PASSAGES, lambda: window.start_research_export("passages"), needs_project=True)
    add(export_menu, strings.EXPORT_READING_GUIDE, window.overview.export_guide, needs_project=True)
    file_menu.addSeparator()
    add(file_menu, strings.CLOSE_PROJECT, window.close_project, needs_project=True)
    # Quit keeps the system shortcut only on a Mac (Cmd+Q); elsewhere Ctrl+Q codes a selection.
    add(file_menu, "Quit", window.close, QKeySequence.StandardKey.Quit if sys.platform == "darwin" else None)

    workspace = window.workspace
    workspace.disable_own_shortcuts()
    edit_menu = bar.addMenu("&Edit")
    window.edit_menu = edit_menu
    in_workspace = lambda method: (lambda: window.run_in_workspace(method))
    add(edit_menu, strings.UNDO_CODING, workspace.undo_stack.undo, QKeySequence.StandardKey.Undo, True)
    add(edit_menu, strings.REDO_CODING, workspace.undo_stack.redo, QKeySequence.StandardKey.Redo, True)
    edit_menu.addSeparator()
    add(edit_menu, strings.CODE_SELECTION, in_workspace(workspace.code_selection_with_picker), CODE_KEY, True)
    add(edit_menu, strings.UNCODE_SELECTION, in_workspace(workspace.remove_selected_coding), UNCODE_KEY, True)
    add(edit_menu, strings.COMMENT_ON_SELECTION, in_workspace(workspace.comment_on_selection), "Ctrl+M", True)
    edit_menu.addSeparator()
    add(edit_menu, strings.NEW_THEME, in_workspace(workspace.new_theme), "Ctrl+Shift+N", True)
    add(edit_menu, strings.RENAME_THEME, in_workspace(workspace.rename_selected_theme), "F2", True)
    add(edit_menu, strings.MERGE_THEME, in_workspace(workspace.merge_selected_theme), needs_project=True)
    edit_menu.addSeparator()
    add(edit_menu, strings.FIND_IN_DOCUMENT, in_workspace(workspace.open_find), QKeySequence.StandardKey.Find, True)
    add(edit_menu, strings.SEARCH_ALL_DOCUMENTS, window.open_search, "Ctrl+Shift+F", True)
    update_menus(window)


def update_menus(window):
    """Enable the project-only items when a project is open and nothing is running."""
    available = window.project is not None and not window.busy
    for action in window.project_actions:
        action.setEnabled(available)
