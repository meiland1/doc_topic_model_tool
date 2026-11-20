"""The document viewer widget and the undoable "add coding" command."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QUndoCommand
from PySide6.QtWidgets import QPlainTextEdit

from qualitative_themes.storage.coding import (add_coding, remove_coding)


class SourceViewer(QPlainTextEdit):
    """Read-only text view that reports clicks, selections and number-key shortcuts.

    number_pressed(n) fires for 1-9 while text is selected (quick-code with
    the nth toolbar theme); clicked(qt_position) fires on a plain click.
    """
    clicked = Signal(int)
    number_pressed = Signal(int)
    user_selected = Signal()

    def keyReleaseEvent(self, event):
        super().keyReleaseEvent(event)
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier and self.textCursor().hasSelection():
            self.user_selected.emit()

    def keyPressEvent(self, event):
        text = event.text()
        if text in "123456789" and text and self.textCursor().hasSelection() and not event.modifiers() & (
                Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier | Qt.KeyboardModifier.AltModifier):
            self.number_pressed.emit(int(text))
            return
        super().keyPressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if not self.textCursor().hasSelection():
            self.clicked.emit(self.textCursor().position())
        else:
            self.user_selected.emit()


class AddCodingCommand(QUndoCommand):
    """Undoable "code this selection as a theme".

    redo() on the first push only saves; later redos (after an undo) also
    reload the theme list. undo() marks the coding removed, it doesn't delete it.
    """

    def __init__(self, workspace, document_id, start, end, theme_id):
        super().__init__("Code selection")
        self.workspace, self.document_id, self.start, self.end, self.theme_id = workspace, document_id, start, end, theme_id
        self.coding_id = None
        self.error = None
        self.first = True

    def redo(self):
        try:
            self.coding_id = add_coding(self.workspace.directory, self.document_id, self.start, self.end, self.theme_id)
            self.error = None
        except ValueError as exc:
            self.error = str(exc)
            self.setObsolete(True)
            return
        if not self.first:
            self.workspace.reload_themes(self.theme_id)
            self.workspace.status.setText("Coding restored.")
        self.first = False

    def undo(self):
        if self.coding_id:
            try:
                remove_coding(self.workspace.directory, self.coding_id)
            except ValueError:
                pass
            self.workspace.reload_themes(self.theme_id)
            self.workspace.status.setText("Coding undone. Ctrl+Shift+Z redoes it.")
