"""One comment thread in the comments column: the comment, its replies, and what you can do to them.

The card calls back into its CommentsPanel (pin, edit, delete, reply, resolve)
and asks it to show what the thread is on when clicked.
"""
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget


def _when(stamp: str) -> str:
    try:
        moment = datetime.fromisoformat(stamp).astimezone()
    except ValueError:
        return ""
    today = datetime.now().astimezone().date()
    if moment.date() == today:
        return moment.strftime("%H:%M")
    return f"{moment:%b} {moment.day}"


def short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


class ThreadCard(QFrame):
    """One thread (root comment + replies) with pin, reply, edit, resolve and delete."""
    def __init__(self, panel, root, replies):
        super().__init__()
        self.panel, self.root = panel, root
        self.setObjectName("commentCard")
        self.setProperty("resolved", root.state == "resolved")
        self.setProperty("keyQuote", root.key_quote)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        top = QHBoxLayout()
        what = QLabel(panel.describe(root.target, root.quote, 140))
        what.setObjectName("commentQuote")
        what.setWordWrap(True)
        top.addWidget(what, 1)
        pin = QPushButton("Unpin" if root.pinned else "Pin")
        pin.setObjectName("linkButton")
        pin.setAccessibleName("Unpin this comment" if root.pinned else "Pin this comment to the top")
        pin.clicked.connect(lambda: panel.pin(root.id, not root.pinned))
        top.addWidget(pin, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)
        for comment in [root, *replies]:
            edited = comment.updated_at[:19] != comment.created_at[:19]
            header = QHBoxLayout()
            meta = QLabel(" · ".join(part for part in (
                ("↳ reply" if comment.parent_id else ""), _when(comment.created_at),
                ("edited" if edited else ""), ("key quote" if comment.key_quote else "")) if part))
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
        super().mousePressEvent(event)
        # Going to the target can redraw the panel, which deletes this card; let
        # the click finish first.
        target, panel = self.root.target, self.panel
        QTimer.singleShot(0, lambda: panel.open_target.emit(target))

    def send_reply(self):
        text = self.reply_field.text().strip()
        if text:
            self.panel.reply(self.root.id, text)
