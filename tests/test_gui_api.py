"""Catch binding differences that static compilation cannot detect."""
from PySide6.QtWidgets import QTextEdit


def test_qt_highlight_selection_constructor():
    selection = QTextEdit.ExtraSelection()
    assert selection is not None
