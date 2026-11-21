"""Comments column beside the reader.

A comment can be on selected text, a theme, a document or the whole project
(storage.comments.Target). The column shows one filter at a time (this
document, this theme, the whole project) with pinned threads above it whatever
the filter. Typing is autosaved as a draft. No names are shown: a project has
one researcher.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget)

from qualitative_themes.gui.read_code.comment_card import ThreadCard, short

from qualitative_themes.storage import comments as store
from qualitative_themes.storage.comments import Target

FILTERS = [("document", "This document"), ("theme", "This theme"), ("project", "Whole project")]
COMMENT_ON = [("passage", "Selected text"), ("theme", "This theme"), ("document", "This document"),
              ("project", "Whole project")]
EMPTY = {"document": "No comments on this document yet. Select text and choose Comment, or use Comment on below.",
         "theme": "No comments on this theme yet.",
         "project": "No comments in this project yet.",
         "no_theme": "Select a theme to see its comments."}


class CommentsPanel(QWidget):
    """The comments column.

    The workspace keeps it informed: set_document() and set_theme() when they
    change, `theme_names` / `document_names` for labels, and
    `selection_target()` (a callable returning a passage Target or None) for
    "Comment on: Selected text". `pending` is the Target being written; its text
    is autosaved as a draft. open_target(Target) asks the workspace to show
    what a thread is on; changed() fires after any edit; count_changed(n) gives
    the open threads in the current filter.
    """
    open_target = Signal(object)
    changed = Signal()
    count_changed = Signal(int)
    close_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.directory: Path | None = None
        self.document_id: str | None = None
        self.theme_id: str | None = None
        self.theme_names: dict[str, str] = {}
        self.document_names: dict[str, str] = {}
        self.selection_target = lambda: None
        self.pending: Target | None = None
        self.draft_id: str | None = None
        self.cards: list[ThreadCard] = []
        self._sections: list[QWidget] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 0, 0)
        header = QHBoxLayout()
        self.title = QLabel("Comments")
        self.title.setObjectName("setupName")
        header.addWidget(self.title)
        header.addStretch()
        close = QToolButton()
        close.setText("×")
        close.setAccessibleName("Close comments")
        close.setToolTip("Close comments")
        close.clicked.connect(self.close_requested)
        header.addWidget(close)
        layout.addLayout(header)
        show_row = QHBoxLayout()
        show_row.addWidget(QLabel("Show"))
        self.filter_choice = QComboBox()
        for key, label in FILTERS:
            self.filter_choice.addItem(label, key)
        self.filter_choice.setAccessibleName("Show comments on")
        self.filter_choice.currentIndexChanged.connect(lambda _i: self.reload())
        show_row.addWidget(self.filter_choice, 1)
        layout.addLayout(show_row)
        self.show_resolved = QCheckBox("Show resolved")
        self.show_resolved.toggled.connect(lambda _c: self.reload())
        layout.addWidget(self.show_resolved)
        on_row = QHBoxLayout()
        on_row.addWidget(QLabel("Comment on"))
        self.comment_on = QComboBox()
        for key, label in COMMENT_ON:
            self.comment_on.addItem(label, key)
        self.comment_on.setAccessibleName("Comment on")
        on_row.addWidget(self.comment_on, 1)
        add = QPushButton("Add")
        add.setObjectName("smallButton")
        add.setAccessibleName("Add a comment")
        add.clicked.connect(self.add_chosen)
        on_row.addWidget(add)
        layout.addLayout(on_row)
        self.composer = QFrame()
        self.composer.setObjectName("commentComposer")
        composer = QVBoxLayout(self.composer)
        composer.setContentsMargins(10, 8, 10, 8)
        self.composer_quote = QLabel()
        self.composer_quote.setObjectName("commentQuote")
        self.composer_quote.setWordWrap(True)
        composer.addWidget(self.composer_quote)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("Write a comment… (saved as a draft while you type)")
        self.editor.setMaximumHeight(90)
        self.editor.setAccessibleName("New comment")
        composer.addWidget(self.editor)
        row = QHBoxLayout()
        self.key_quote = QCheckBox("Mark as key quote")
        row.addWidget(self.key_quote)
        row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setObjectName("smallButton")
        cancel.clicked.connect(self.cancel_comment)
        save = QPushButton("Comment")
        save.setProperty("primary", True)
        save.setObjectName("smallButton")
        save.clicked.connect(self.save_comment)
        row.addWidget(cancel)
        row.addWidget(save)
        composer.addLayout(row)
        self.composer.hide()
        layout.addWidget(self.composer)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.list_widget = QWidget()
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(8)
        self.list_layout.addStretch()
        self.scroll.setWidget(self.list_widget)
        layout.addWidget(self.scroll, 1)
        self.empty = QLabel(EMPTY["document"])
        self.empty.setObjectName("quiet")
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)
        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.setInterval(800)
        self._autosave.timeout.connect(self.save_draft)
        self.editor.textChanged.connect(self._autosave.start)
        self.key_quote.toggled.connect(lambda _checked: self._autosave.start())
        self.save_status = QLabel("")
        self.save_status.setWordWrap(True)
        layout.addWidget(self.save_status)
        self.editor.textChanged.connect(lambda: self.save_status.setText("Saving draft…"))

    # Labels -------------------------------------------------------------------
    def describe(self, target: Target, quote: str = "", limit: int = 160) -> str:
        """What a comment is on, in words, for card and composer headings."""
        here = target.document_id == self.document_id and self.filter_key() == "document"
        document = self.document_names.get(target.document_id, "a removed document")
        if target.scope == "project":
            return "On the whole project"
        if target.scope == "theme":
            return "On theme: " + self.theme_names.get(target.theme_id, "a theme no longer shown")
        if target.scope == "document":
            return "On this document" if here else "On document: " + document
        text = "“" + short(quote, limit) + "”"
        return text if here else f"{document} · {text}"

    # Data ---------------------------------------------------------------------
    def filter_key(self) -> str:
        return self.filter_choice.currentData()

    def set_filter(self, key: str):
        index = self.filter_choice.findData(key)
        if index >= 0:
            self.filter_choice.setCurrentIndex(index)

    def _filter_args(self):
        """Keyword arguments for store.list_comments, or None if the filter has nothing to show."""
        key = self.filter_key()
        if key == "document":
            return {"document_id": self.document_id} if self.document_id else None
        if key == "theme":
            return {"theme_id": self.theme_id} if self.theme_id else None
        return {}

    def set_document(self, directory, document_id):
        """Switch documents. Saves any draft first; returns False (and stays put) if it can't."""
        if not self.flush_pending():
            return False
        self.directory, self.document_id = (Path(directory) if directory else None), document_id
        self.pending, self.draft_id = None, None
        self.composer.hide()
        self.reload()
        self.restore_draft()
        return True

    def set_theme(self, theme_id):
        """The selected theme changed. The open draft, if any, is left alone."""
        if theme_id == self.theme_id:
            return
        self.theme_id = theme_id
        if self.filter_key() == "theme":
            self.reload()

    def reload(self):
        for widget in [*self.cards, *self._sections]:
            widget.setParent(None)
            widget.deleteLater()
        self.cards, self._sections = [], []
        args = self._filter_args() if self.directory else None
        if not self.directory:
            self.title.setText("Comments")
            self.empty.setText(EMPTY["document"])
            self.empty.show()
            self.count_changed.emit(0)
            return
        # With nothing to filter on (This theme, no theme selected) only pinned threads show:
        # a document id that can't exist matches nothing else.
        threads = store.list_comments(self.directory, include_resolved=self.show_resolved.isChecked(),
                                      pinned_too=True, **(args if args is not None else {"document_id": ""}))
        pinned = [thread for thread in threads if thread[0].pinned]
        others = [thread for thread in threads if not thread[0].pinned] if args is not None else []
        if pinned:
            self._add_section("Pinned")
            for root, replies in pinned:
                self._add_card(root, replies)
            if others:
                self._add_section(dict(FILTERS)[self.filter_key()])
        for root, replies in others:
            self._add_card(root, replies)
        if args is None:
            open_count = resolved = 0
        else:
            open_count = store.open_count(self.directory, **args)
            resolved = store.resolved_count(self.directory, **args)
        self.show_resolved.setText(f"Show resolved ({resolved})")
        self.title.setText(f"Comments · {open_count} open")
        key = "no_theme" if args is None and self.filter_key() == "theme" else self.filter_key()
        self.empty.setText(EMPTY[key])
        self.empty.setVisible(not others)
        self.count_changed.emit(open_count)

    def _add_section(self, text):
        label = QLabel(text)
        label.setObjectName("commentSection")
        self.list_layout.insertWidget(self.list_layout.count() - 1, label)
        self._sections.append(label)

    def _add_card(self, root, replies):
        card = ThreadCard(self, root, replies)
        self.list_layout.insertWidget(self.list_layout.count() - 1, card)
        self.cards.append(card)

    def restore_draft(self):
        if not self.directory:
            return
        saved = store.drafts(self.directory, self.document_id)
        if saved:
            draft = saved[0]
            self.begin_comment(draft.target, quote=draft.quote, draft_id=draft.id, body=draft.body,
                               key_quote=draft.key_quote)

    # Actions --------------------------------------------------------------------
    def add_chosen(self):
        """The Add button: start a comment on whatever 'Comment on' names."""
        key = self.comment_on.currentData()
        if key == "passage":
            target = self.selection_target()
            problem = "Select some text in the document first."
        elif key == "theme":
            target = Target.theme(self.theme_id) if self.theme_id else None
            problem = "Select a theme first."
        elif key == "document":
            target = Target.document(self.document_id) if self.document_id else None
            problem = "Open a document first."
        else:
            target, problem = Target.project(), ""
        if target is None:
            self.save_status.setText(problem)
            return
        quote = ""
        if target.scope == "passage" and self.directory:
            from qualitative_themes.storage.documents import get_document
            quote = get_document(self.directory, target.document_id)[1][target.start:target.end]
        self.begin_comment(target, quote=quote)

    def begin_comment(self, target: Target, quote: str = "", draft_id=None, body="", key_quote=False):
        """Open the composer for `target`. Any draft already open is saved first."""
        if not self.flush_pending():
            return
        self.pending = target
        self.draft_id = draft_id
        self.composer_quote.setText(self.describe(target, quote) + ("   (restored draft)" if draft_id else ""))
        self.editor.blockSignals(True)
        self.editor.setPlainText(body)
        self.editor.blockSignals(False)
        self.key_quote.setVisible(target.scope == "passage")
        self.key_quote.setChecked(key_quote)
        self.composer.show()
        self.editor.setFocus()

    def flush_pending(self):
        """Save the current draft now. Returns False if it couldn't be saved.

        Everything that leaves the document (switching documents/projects,
        backup, closing) calls this and stops if it returns False.
        """
        self._autosave.stop()
        return self.save_draft()

    def save_draft(self):
        """Store the editor text as a draft comment. True if saved (or nothing to save)."""
        if not self.pending or not self.directory:
            return True
        body = self.editor.toPlainText()
        try:
            if self.draft_id:
                store.update_comment(self.directory, self.draft_id, body, key_quote=self.key_quote.isChecked())
            elif body.strip():
                self.draft_id = store.add_comment(self.directory, self.pending, body, draft=True,
                                                  key_quote=self.key_quote.isChecked())
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.save_status.setText(f"Could not save draft: {exc}. Your text is still here; try again before leaving.")
            return False
        self.save_status.setText("Draft saved" if body.strip() else "")
        return True

    def save_comment(self):
        if not self.pending or not self.directory:
            return
        self._autosave.stop()
        body = self.editor.toPlainText().strip()
        if not body:
            return
        try:
            if self.draft_id:
                store.update_comment(self.directory, self.draft_id, body, key_quote=self.key_quote.isChecked(), publish=True)
            else:
                store.add_comment(self.directory, self.pending, body, key_quote=self.key_quote.isChecked())
        except (ValueError, OSError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Could not save comment", str(exc))
            return
        self.pending, self.draft_id = None, None
        self.editor.clear()
        self.save_status.setText("Comment saved")
        self.composer.hide()
        self.reload()
        self.changed.emit()

    def cancel_comment(self):
        self._autosave.stop()
        if self.draft_id and self.directory:
            store.delete_comment(self.directory, self.draft_id)
        self.pending, self.draft_id = None, None
        self.editor.clear()
        self.save_status.clear()
        self.composer.hide()

    def reply(self, root_id, text):
        store.add_comment(self.directory, Target.project(), text, parent_id=root_id)
        self.reload()
        self.changed.emit()

    def edit(self, comment_id, body):
        try:
            store.update_comment(self.directory, comment_id, body)
        except ValueError as exc:
            QMessageBox.warning(self, "Could not save the edit", str(exc))
            return
        self.reload()
        self.changed.emit()

    def delete(self, comment):
        what = "this reply" if comment.parent_id else "this comment and its replies"
        answer = QMessageBox.question(self, "Delete comment", f"Delete {what}? This can't be undone.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        store.delete_comment(self.directory, comment.id)
        self.reload()
        self.changed.emit()

    def resolve(self, root_id, resolved):
        store.set_resolved(self.directory, root_id, resolved)
        self.reload()
        self.changed.emit()

    def pin(self, root_id, pinned):
        store.set_pinned(self.directory, root_id, pinned)
        self.reload()
        self.changed.emit()

    def follow(self, first_visible_char: int):
        """Keep the column roughly level with the text: scroll to the first thread here at or after it."""
        if self.filter_key() != "document":
            return
        for card in self.cards:
            root = card.root
            if root.scope == "passage" and root.document_id == self.document_id and not root.pinned \
                    and root.end >= first_visible_char:
                self.scroll.verticalScrollBar().setValue(card.y())
                return
