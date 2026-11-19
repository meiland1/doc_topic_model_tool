"""Comments column beside the reader: threads anchored to text, replies, resolve, autosaved drafts."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from qualitative_themes.storage import comments as store


def _when(stamp: str) -> str:
    try:
        moment = datetime.fromisoformat(stamp).astimezone()
    except ValueError:
        return ""
    today = datetime.now().astimezone().date()
    if moment.date() == today:
        return moment.strftime("%H:%M")
    return f"{moment:%b} {moment.day}"


class ThreadCard(QFrame):
    """One thread (root comment + replies) with reply/edit/resolve/delete controls."""
    def __init__(self, panel: "CommentsPanel", root, replies):
        super().__init__()
        self.panel, self.root = panel, root
        self.setObjectName("commentCard")
        self.setProperty("resolved", root.state == "resolved")
        self.setProperty("keyQuote", root.key_quote)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        quote = QLabel("“" + " ".join(root.quote.split())[:140] + ("…" if len(root.quote) > 140 else "") + "”")
        quote.setObjectName("commentQuote")
        quote.setWordWrap(True)
        layout.addWidget(quote)
        for comment in [root, *replies]:
            edited = comment.updated_at[:19] != comment.created_at[:19]
            header = QHBoxLayout()
            meta = QLabel((comment.author or "You") + " · " + _when(comment.created_at)
                          + (" · edited" if edited else "") + ("  ·  key quote" if comment.key_quote else ""))
            meta.setObjectName("commentMeta")
            header.addWidget(meta, 1)
            edit = QPushButton("Edit")
            edit.setObjectName("linkButton")
            edit.setAccessibleName("Edit comment")
            delete = QPushButton("Delete")
            delete.setObjectName("linkButton")
            delete.setAccessibleName("Delete comment" if comment.parent_id else "Delete thread")
            header.addWidget(edit)
            header.addWidget(delete)
            body = QLabel(comment.body)
            body.setWordWrap(True)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            editor = QPlainTextEdit(comment.body)
            editor.setMaximumHeight(90)
            editor.hide()
            buttons = QHBoxLayout()
            save = QPushButton("Save")
            save.setObjectName("smallButton")
            save.setProperty("primary", True)
            cancel = QPushButton("Cancel")
            cancel.setObjectName("smallButton")
            buttons.addStretch()
            buttons.addWidget(cancel)
            buttons.addWidget(save)
            button_row = QWidget()
            button_row.setLayout(buttons)
            button_row.hide()
            edit.clicked.connect(lambda _c=False, b=body, e=editor, r=button_row: self.start_edit(b, e, r))
            cancel.clicked.connect(lambda _c=False, b=body, e=editor, r=button_row: self.stop_edit(b, e, r))
            save.clicked.connect(lambda _c=False, cid=comment.id, e=editor: panel.edit(cid, e.toPlainText()))
            delete.clicked.connect(lambda _c=False, c=comment: panel.delete(c))
            layout.addLayout(header)
            layout.addWidget(body)
            layout.addWidget(editor)
            layout.addWidget(button_row)
        actions = QHBoxLayout()
        self.reply_field = QLineEdit()
        self.reply_field.setPlaceholderText("Reply…")
        self.reply_field.returnPressed.connect(self.send_reply)
        actions.addWidget(self.reply_field, 1)
        resolve = QPushButton("Reopen" if root.state == "resolved" else "Resolve")
        resolve.setObjectName("smallButton")
        resolve.clicked.connect(lambda: panel.resolve(root.id, root.state != "resolved"))
        actions.addWidget(resolve)
        layout.addLayout(actions)

    @staticmethod
    def start_edit(body, editor, buttons):
        editor.setPlainText(body.text())
        body.hide()
        editor.show()
        buttons.show()
        editor.setFocus()

    @staticmethod
    def stop_edit(body, editor, buttons):
        editor.hide()
        buttons.hide()
        body.show()

    def mousePressEvent(self, event):
        self.panel.open_range.emit(self.root.start, self.root.end)
        super().mousePressEvent(event)

    def send_reply(self):
        text = self.reply_field.text().strip()
        if text:
            self.panel.reply(self.root.id, text)


class CommentsPanel(QWidget):
    """Comment column for the open document.

    `pending` is the (start, end, quote) being commented on; text typed for it
    is autosaved as a draft comment. open_range(start, end) asks the
    workspace to select a thread's text; changed() fires after any edit.
    """
    open_range = Signal(int, int)
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.directory: Path | None = None
        self.document_id: str | None = None
        self.pending: tuple[int, int, str] | None = None
        self.draft_id: str | None = None
        self.cards: list[ThreadCard] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 0, 0)
        header = QHBoxLayout()
        self.title = QLabel("Comments")
        self.title.setObjectName("setupName")
        header.addWidget(self.title)
        header.addStretch()
        self.show_resolved = QCheckBox("Show resolved")
        self.show_resolved.toggled.connect(lambda _c: self.reload())
        header.addWidget(self.show_resolved)
        layout.addLayout(header)
        self.composer = QFrame()
        self.composer.setObjectName("commentComposer")
        composer = QVBoxLayout(self.composer)
        composer.setContentsMargins(10, 8, 10, 8)
        self.composer_quote = QLabel()
        self.composer_quote.setObjectName("commentQuote")
        self.composer_quote.setWordWrap(True)
        composer.addWidget(self.composer_quote)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("Add a comment… (saved as a draft while you type)")
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
        self.empty = QLabel("Select text and choose Comment to add a note beside it.")
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

    # Data ---------------------------------------------------------------------
    def set_document(self, directory, document_id):
        """Switch documents. Callers must flush_pending() first (the workspace does)."""
        if not self.flush_pending():
            return False
        self.directory, self.document_id = (Path(directory) if directory else None), document_id
        self.pending, self.draft_id = None, None
        self.composer.hide()
        self.reload()
        self.restore_draft()
        return True

    def reload(self):
        for card in self.cards:
            card.setParent(None)
            card.deleteLater()
        self.cards = []
        if not self.directory or not self.document_id:
            self.title.setText("Comments")
            self.empty.show()
            return
        threads = store.list_threads(self.directory, self.document_id, self.show_resolved.isChecked())
        for root, replies in threads:
            card = ThreadCard(self, root, replies)
            self.list_layout.insertWidget(self.list_layout.count() - 1, card)
            self.cards.append(card)
        resolved = store.resolved_count(self.directory, self.document_id)
        self.show_resolved.setText(f"Show resolved ({resolved})")
        open_count = sum(1 for root, _ in threads if root.state != "resolved")
        self.title.setText(f"Comments · {open_count} open")
        self.empty.setVisible(not threads)

    def restore_draft(self):
        if not self.directory or not self.document_id:
            return
        saved = store.drafts(self.directory, self.document_id)
        if saved:
            draft = saved[0]
            self.begin_comment(draft.start, draft.end, draft.quote, draft_id=draft.id, body=draft.body,
                               key_quote=draft.key_quote)

    # Actions --------------------------------------------------------------------
    def begin_comment(self, start, end, quote, draft_id=None, body="", key_quote=False):
        if not self.flush_pending():
            return
        self.pending = (start, end, quote)
        self.draft_id = draft_id
        self.composer_quote.setText("“" + " ".join(quote.split())[:160] + ("…" if len(quote) > 160 else "") + "”"
                                    + ("   (restored draft)" if draft_id else ""))
        self.editor.blockSignals(True)
        self.editor.setPlainText(body)
        self.editor.blockSignals(False)
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
        if not self.pending or not self.directory or not self.document_id:
            return True
        body = self.editor.toPlainText()
        try:
            if self.draft_id:
                store.update_comment(self.directory, self.draft_id, body, key_quote=self.key_quote.isChecked())
            elif body.strip():
                start, end, _ = self.pending
                self.draft_id = store.add_comment(self.directory, self.document_id, start, end, body, draft=True,
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
                start, end, _ = self.pending
                store.add_comment(self.directory, self.document_id, start, end, body,
                                  key_quote=self.key_quote.isChecked())
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
        store.add_comment(self.directory, self.document_id, 0, 0, text, parent_id=root_id)
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

    def follow(self, first_visible_char: int):
        """Keep the column roughly level with the text: scroll to the first thread at or after it."""
        for card in self.cards:
            if card.root.end >= first_visible_char:
                self.scroll.ensureWidgetVisible(card, 0, 0)
                bar = self.scroll.verticalScrollBar()
                bar.setValue(card.y())
                return
