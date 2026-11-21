"""Theme list and everything that creates, renames, merges or applies a theme.

Part of CodingWorkspace (see workspace.py): these methods use the shared
state documented there and are not meant to be used on their own.
"""

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QInputDialog, QListWidgetItem, QMessageBox)

from qualitative_themes.storage.coding import (add_coding, create_theme, list_themes,
                                                merge_themes, occurrences, remove_coding,
                                                rename_theme, themes_for_range,
                                                assignment_origin, reject_model_suggestion)
from qualitative_themes.storage.theme_keywords import theme_keyword_scores
from qualitative_themes.storage.provenance import theme_provenance, origin_text

from qualitative_themes.gui.read_code.tree_panel import _plural
from qualitative_themes.gui.read_code.viewer import AddCodingCommand
from qualitative_themes.analysis.topics import UNGROUPED_LABEL


class CodingActionsMixin:
    def visible_themes(self, fresh: bool = False):
        """Themes for the current analysis, cached until themes are reloaded.

        Listing themes walks every assignment, so it is computed once per reload
        instead of on every click (colours, toolbar, labels all reuse it).
        """
        key = (self.directory, self.run_id, frozenset(self._draft_theme_ids))
        cached = getattr(self, "_visible_cache", None)
        if not fresh and cached is not None and cached[0] == key:
            return list(cached[1])
        rows = list_themes(self.directory, self.run_id)
        visible = {row[0] for row in rows}
        rows.extend(row for row in list_themes(self.directory)
                    if row[0] in self._draft_theme_ids and row[0] not in visible)
        rows = sorted(rows, key=lambda row: row[1].lower())
        self._visible_cache = (key, rows)
        return list(rows)

    def range_theme_labels(self, doc_id, start, end):
        """Theme names for an exact passage range, from the navigation already in memory."""
        if not self.run_id:
            return themes_for_range(self.directory, doc_id, start, end, None)
        labels = {tid: label for tid, label, _ in self.visible_themes()}
        found = {labels[tid] for (tid, doc), rows in self._navigation_groups.items()
                 if doc == doc_id and tid in labels for row in rows if row[3] == start and row[4] == end}
        return sorted(found, key=str.lower)

    def refresh_theme_labels(self):
        """Update names in place without reselecting evidence or reloading draft editors."""
        rows = {tid: (label, count) for tid, label, count in self.visible_themes(fresh=True)}
        # Group rows created later (lazily) read their names from here.
        self._nav_themes = [(tid, rows[tid][0] if tid in rows else label) for tid, label in self._nav_themes]
        self.themes.blockSignals(True)
        try:
            for index in range(self.themes.count()):
                item = self.themes.item(index)
                value = rows.get(item.data(Qt.ItemDataRole.UserRole))
                if value:
                    item.setText(f'{value[0]}  ({value[1]})')
        finally:
            self.themes.blockSignals(False)
        totals: dict[str, list[int]] = {}
        for (tid, _doc), group in self._navigation_groups.items():
            entry = totals.setdefault(tid, [0, 0])
            entry[0] += 1
            entry[1] += len(group)
        for tree, nodes in self._tree_nodes.items():
            tree.blockSignals(True)
            try:
                for item in nodes.values():
                    data = item.data(0, Qt.ItemDataRole.UserRole) or {}
                    theme = data.get('theme_id')
                    if theme not in rows:
                        continue
                    name = rows[theme][0]
                    if data.get('kind') == 'theme':
                        documents, count = totals.get(theme, (0, 0))
                        detail = f"{name}\n{_plural(count, 'passage')} in {_plural(documents, 'document')}"
                    elif data.get('kind') == 'group' and tree is self.document_tree:
                        count = len(self._navigation_groups.get((theme, data['document_id']), []))
                        detail = f"{name}\n{_plural(count, 'passage')}"
                    else:
                        continue
                    item.setText(0, name)
                    item.setText(1, f"{count:,}")
                    item.setToolTip(0, detail)
                    item.setToolTip(1, detail)
                    item.setData(0, Qt.ItemDataRole.UserRole + 1, name)
            finally:
                tree.blockSignals(False)

        if 0 <= self.match_index < len(self.matches):
            assignment, doc, _name, start, end, _excerpt = self.matches[self.match_index]
            if doc == self.doc_id:
                origin = assignment_origin(self.directory, assignment)
                self.range_themes.setText(('Suggested by the tool' if origin == 'model' else 'Your coding')
                                         + ' · Themes: ' + ', '.join(self.range_theme_labels(doc, start, end)))

    def reload_themes(self, select_id: str | None = None):
        """Rebuild the theme list and trees from the database, keeping the selection and scope.

        Call after any change to themes or codings. `select_id` picks the theme to
        select afterwards (defaults to the current one).
        """
        if self.directory is None:
            return
        if select_id is None and self.themes.currentItem():
            select_id = self.themes.currentItem().data(Qt.ItemDataRole.UserRole)
        previous_navigation = dict(self._navigation) if self._navigation else None
        self.themes.blockSignals(True)
        self.themes.clear()
        rows = self.visible_themes(fresh=True)
        for theme_id, label, count in rows:
            item = QListWidgetItem(f"{label}  ({count})")
            item.setData(Qt.ItemDataRole.UserRole, theme_id)
            self.themes.addItem(item)
            if theme_id == select_id:
                self.themes.setCurrentItem(item)
        if self.themes.count() and not self.themes.currentItem():
            self.themes.setCurrentRow(0)
        self.themes.blockSignals(False)
        self.update_comment_names(themes={tid: label for tid, label, _ in rows})
        self.rebuild_navigation(rows)
        if previous_navigation and previous_navigation.get("theme_id") == self.current_theme_id():
            self._navigation = previous_navigation
            self._from_tree = True
        elif previous_navigation and previous_navigation.get("document_id") and self.current_theme_id():
            self._navigation = {**previous_navigation, "theme_id": self.current_theme_id()}
            self._from_tree = True
        elif self.organization_tabs.currentWidget() is self.document_tree and self.current_theme_id() and self.doc_id:
            self._navigation = {"kind": "group", "theme_id": self.current_theme_id(), "document_id": self.doc_id}
            self._from_tree = True
        try:
            self.select_theme(self.themes.currentItem())
        finally:
            self._from_tree = False
        self.sync_tree_selection()

    def new_theme(self, select=True):
        """Ask for a name and create an empty theme. Returns its id, or None if cancelled.

        select=False leaves the current selection alone (the caller is about to
        code the selected text as the new theme).
        """
        if self.directory is None:
            return None
        label, ok = QInputDialog.getText(self, "New theme", "Theme name")
        if not ok:
            return None
        try:
            theme_id = create_theme(self.directory, label)
        except ValueError as exc:
            QMessageBox.warning(self, "Could not create theme", str(exc))
            return None
        self._draft_theme_ids.add(theme_id)
        if select:
            self._navigation = None
            self.organization_tabs.setCurrentIndex(1)
            self.reload_themes(theme_id)
        return theme_id

    def select_theme(self, current, _previous=None):
        """Theme selection changed: load its matches into the evidence list and highlight them."""
        if not self._from_tree:
            self._navigation = {"kind": "theme", "theme_id": current.data(Qt.ItemDataRole.UserRole)} if current else None
        self.matches = []
        self.match_index = -1
        self.hits.blockSignals(True)
        self.hits.clear()
        if current and self.directory:
            theme_id = current.data(Qt.ItemDataRole.UserRole)
            provenance = theme_provenance(self.directory, theme_id, self.run_id)
            self.theme_origin_label.setText("Came from: " + origin_text(provenance))
            self.theme_origin_label.setToolTip("Supplied categories: " + ", ".join(provenance["supplied_names"]) if provenance["supplied_names"] else
                "Origin describes how this group was produced, not whether its assignments have been accepted.")
            words = theme_keyword_scores(self.directory, theme_id, self.run_id)
            self.theme_keywords_label.setText("Keywords: " + (", ".join(f"{word} ({score:.2%})" for word, score in words) if words else "No model keywords saved for this theme."))
            self.matches = occurrences(self.directory, theme_id, self.run_id)
            document = (self._navigation or {}).get("document_id")
            if document:
                self.matches = [match for match in self.matches if match[1] == document]
            for _id, _doc, name, start, end, excerpt in self.matches:
                snippet = excerpt
                self.hits.addItem(f"{name} · {start}–{end}\n{snippet}")
            document_count = len({m[1] for m in self.matches})
            self.occurrence_title.setText(f"{len(self.matches)} passages · {document_count} documents")
        else:
            self.theme_origin_label.setText("Came from: select a theme.")
            self.theme_keywords_label.setText("Keywords: Select a theme.")
            self.occurrence_title.setText("Coded passages")
        self.hits.blockSignals(False)
        self.range_themes.setText("Select a coded passage to see its themes.")
        self.show_passage_actions(None)
        if self.matches:
            scope = self._navigation or {}
            selected = next((i for i, match in enumerate(self.matches) if scope.get("kind") == "passage" and
                             match[1] == scope.get("document_id") and match[3:5] == (scope.get("start"), scope.get("end"))), 0)
            self.hits.setCurrentRow(selected)
        elif (self._navigation or {}).get("document_id"):
            index = self.document_choice.findData(self._navigation["document_id"])
            if index >= 0:
                self.document_choice.setCurrentIndex(index)
        self.highlight_matches()
        self.update_passage_position()
        self.sync_tree_selection()
        self.update_tree_strip()
        self.comments_panel.set_theme(self.current_theme_id())

    def current_theme_id(self):
        item = self.themes.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def rename_selected_theme(self):
        theme_id = self.current_theme_id()
        if not self.directory or not theme_id:
            self.status.setText("Select a theme to rename.")
            return
        old_label = next(label for tid, label, _ in self.visible_themes() if tid == theme_id)
        label, ok = QInputDialog.getText(self, "Rename theme", "New name", text=old_label)
        if ok:
            try:
                rename_theme(self.directory, theme_id, label)
                self.reload_themes(theme_id)
                self.status.setText(f"Renamed theme to {label.strip()}.")
            except ValueError as exc:
                QMessageBox.warning(self, "Could not rename theme", str(exc))

    def merge_selected_theme(self):
        source_id = self.current_theme_id()
        if not self.directory or not source_id:
            self.status.setText("Select the theme to merge.")
            return
        themes = self.visible_themes()
        source_label = next(label for tid, label, _ in themes if tid == source_id)
        choices = [(tid, label) for tid, label, _ in themes if tid != source_id]
        if not choices:
            self.status.setText("Create another theme before merging.")
            return
        displays = [f"{i+1}. {label}" for i, (_, label) in enumerate(choices)]
        target_display, ok = QInputDialog.getItem(self, "Merge themes", f"Move all work from '{source_label}' into:",
                                                 displays, 0, False)
        if not ok:
            return
        target_id, target_label = choices[displays.index(target_display)]
        if QMessageBox.question(self, "Confirm theme merge",
                                f"Merge '{source_label}' into '{target_label}'? The source theme will no longer appear.") != QMessageBox.StandardButton.Yes:
            return
        try:
            merge_themes(self.directory, source_id, target_id)
            self.reload_themes(target_id)
            self.status.setText(f"Merged {source_label} into {target_label}.")
        except ValueError as exc:
            QMessageBox.warning(self, "Could not merge themes", str(exc))

    def remove_selected_coding(self):
        if not self.directory or not 0 <= self.match_index < len(self.matches):
            self.status.setText("Select a coded passage to remove.")
            return
        coding_id = self.matches[self.match_index][0]
        theme_id = self.current_theme_id()
        try:
            if assignment_origin(self.directory, coding_id) == "model":
                _, doc, _, start, end, _ = self.matches[self.match_index]
                reject_model_suggestion(self.directory, doc, start, end, theme_id)
            else:
                remove_coding(self.directory, coding_id)
            self.reload_themes(theme_id)
            self.status.setText("Removed this passage from the selected theme; other themes remain.")
        except ValueError as exc:
            QMessageBox.warning(self, "Could not remove coding", str(exc))

    def accept_selected_suggestion(self):
        """Turn the selected model suggestion into a researcher coding (kept across later analyses)."""
        if not self.directory or not 0 <= self.match_index < len(self.matches):
            self.status.setText("Select a suggested passage first.")
            return
        _, doc, _, start, end, _ = self.matches[self.match_index]
        theme = self.current_theme_id()
        try:
            add_coding(self.directory, doc, start, end, theme)
            self.reload_themes(theme)
            self.select_range(doc, start, end)
            self.status.setText("Kept as your coding; it stays in later analyses too.")
        except ValueError as exc:
            self.status.setText(str(exc))

    def add_selection(self):
        if not self.directory or not self.doc_id or not self.themes.currentItem() or not self.viewer.isEnabled():
            self.status.setText("Choose a theme and select text in a document first.")
            return
        self.code_selection_as(self.themes.currentItem().data(Qt.ItemDataRole.UserRole))

    def code_selection_with_picker(self):
        """Edit → Code selection…: choose a theme from a list, then code the selected text."""
        if not self.directory or not self.doc_id or not self.viewer.textCursor().hasSelection():
            self.status.setText("Select some text in the document first.")
            return
        themes = [(tid, label) for tid, label, _ in self.visible_themes() if label != UNGROUPED_LABEL]
        new_choice = "+ New theme…"
        labels = [label for _, label in themes] + [new_choice]
        label, ok = QInputDialog.getItem(self, "Code selection", "Theme", labels, 0, False)
        if not ok:
            return
        if label == new_choice:
            self.code_selection_as_new_theme()
        else:
            self.code_selection_as(themes[labels.index(label)][0])

    def code_selection_as_new_theme(self):
        """'+ New theme' on the selection: name a theme, then code the selected text as it."""
        if not self.directory or not self.doc_id or not self.viewer.textCursor().hasSelection():
            self.status.setText("Select some text in the document first.")
            return
        theme_id = self.new_theme(select=False)
        if theme_id:
            self.code_selection_as(theme_id)

    def code_selection_as(self, theme_id):
        """Code the selected text as ``theme_id`` (toolbar, number keys, or the Add button). Undoable."""
        if not self.directory or not self.doc_id or not self.viewer.isEnabled():
            return
        cursor = self.viewer.textCursor()
        if not cursor.hasSelection():
            self.status.setText("Drag across document text to select a passage first.")
            return
        try:
            start, end = self.coords.canonical_range(cursor.selectionStart(), cursor.selectionEnd())
            doc_id = self.doc_id
            command = AddCodingCommand(self, doc_id, start, end, theme_id)
            self.undo_stack.push(command)
            if command.error:
                raise ValueError(command.error)
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.recent_theme_ids = [theme_id] + [t for t in self.recent_theme_ids if t != theme_id][:5]
        self.selection_toolbar.hide()
        self.reload_themes(theme_id)
        self.select_range(doc_id, start, end)
        label = next((label for tid, label, _ in self.visible_themes() if tid == theme_id), "the theme")
        self.status.setText(f"Coded the selection as {label}. Ctrl+Z undoes it.")

    # Reader layers, find, toolbar, comments ----------------------------------------

    def theme_colour_index(self):
        return {tid: index for index, (tid, label, _count) in enumerate(self.visible_themes())}

    def toolbar_themes(self):
        colours = self.theme_colour_index()
        return [(tid, label, colours[tid]) for tid, label, _ in self.visible_themes() if label != UNGROUPED_LABEL]

    def update_selection_toolbar(self):
        cursor = self.viewer.textCursor()
        if (not cursor.hasSelection() or not self.viewer.isEnabled() or not self.directory
                or not self.doc_id or self.view_mode.currentIndex() != 0):
            self.selection_toolbar.hide()
            return
        self.selection_toolbar.set_themes(self.toolbar_themes(), self.recent_theme_ids)
        self.selection_toolbar.show()
        self.selection_toolbar.layout_.activate()
        self.selection_toolbar.adjustSize()
        start = QTextCursor(self.viewer.document())
        start.setPosition(cursor.selectionStart())
        rect = self.viewer.cursorRect(start)
        bar = self.selection_toolbar
        viewport = self.viewer.viewport()
        x = max(4, min(rect.left(), viewport.width() - bar.width() - 4))
        y = rect.top() - bar.height() - 6
        if y < 4:
            y = self.viewer.cursorRect(cursor).bottom() + 6
        bar.move(QPoint(x, y))
        bar.show()
        bar.raise_()

    def code_selection_with_number(self, number):
        themes = self.toolbar_themes()
        if 1 <= number <= len(themes):
            self.code_selection_as(themes[number - 1][0])

    def comment_on_selection(self):
        target = self.selection_target()
        if target is None:
            return
        self.selection_toolbar.hide()
        self.comments_toggle.setChecked(True)
        self.comments_panel.begin_comment(target, quote=self.source_text[target.start:target.end])
