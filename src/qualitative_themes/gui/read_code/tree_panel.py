"""The left panel: the theme and document trees.

Trees are built lazily: rebuild_navigation() makes only the top level;
populate_children() adds groups and passages when a row is expanded.

Part of CodingWorkspace (see workspace.py): these methods use the shared
state documented there and are not meant to be used on their own.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QLabel, QListWidget, QPushButton, QTabWidget, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from qualitative_themes.storage.navigation import navigation_groups


class TreePanelMixin:
    def _build_tree_panel(self, splitter):
        """Left panel: theme/document trees and theme actions."""
        # Topic Window organized by Documents or by Themes
        left = QWidget()
        self.evidence_browser = left
        left_lay = QVBoxLayout(left)
        left_lay.addWidget(QLabel("Passages"))
        # Internal flat selection controller keeps theme actions independent of
        # which tree contains the selection. Only the two trees are displayed.
        self.themes = QListWidget(self)
        self.themes.hide()
        self.themes.currentItemChanged.connect(self.select_theme)
        self.organization_tabs = QTabWidget()
        self.document_tree = QTreeWidget()
        self.theme_tree = QTreeWidget()
        for tree, label in [(self.document_tree, "By Document"), (self.theme_tree, "By Theme")]:
            tree.setHeaderHidden(True)
            tree.setAccessibleName(label + " evidence tree")
            tree.setUniformRowHeights(True)
            tree.currentItemChanged.connect(self.tree_selected)
            tree.itemClicked.connect(self.tree_clicked)
            tree.itemExpanded.connect(self.populate_children)
            self.organization_tabs.addTab(tree, label)
        self.organization_tabs.setCurrentIndex(1)
        self.organization_tabs.currentChanged.connect(self.organization_changed)
        left_lay.addWidget(self.organization_tabs)
        tree_help = QLabel("Expand groups to browse passages. Select a document to open it, a theme/document group to highlight its evidence, or a passage to highlight only that excerpt.")
        tree_help.setObjectName("workspaceHelp")
        tree_help.setWordWrap(True)
        left_lay.addWidget(tree_help)
        self.theme_origin_label = QLabel("Came from: select a theme.")
        self.theme_origin_label.setTextFormat(Qt.TextFormat.PlainText)
        self.theme_origin_label.setWordWrap(True)
        self.theme_origin_label.setAccessibleName("Selected theme origin")
        left_lay.addWidget(self.theme_origin_label)
        self.theme_keywords_label = QLabel("Keywords: Select a theme.")
        self.theme_keywords_label.setTextFormat(Qt.TextFormat.PlainText)
        self.theme_keywords_label.setWordWrap(True)
        self.theme_keywords_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.theme_keywords_label.setAccessibleName("Selected theme keywords")
        self.theme_keywords_label.setToolTip("Words that set this theme's passages apart from the rest. "
            "The percentage shows how strongly each word belongs to the theme; it isn't a probability. "
            "Merged themes show each word's highest score. Keywords are separate from the theme's name.")
        left_lay.addWidget(self.theme_keywords_label)
        new_theme = QPushButton("+ New theme")
        new_theme.clicked.connect(self.new_theme)
        left_lay.addWidget(new_theme)
        rename = QPushButton("Rename theme…")
        rename.clicked.connect(self.rename_selected_theme)
        left_lay.addWidget(rename)
        merge = QPushButton("Merge into another theme…")
        merge.clicked.connect(self.merge_selected_theme)
        left_lay.addWidget(merge)
        splitter.addWidget(left)

    def toggle_evidence_browser(self, visible):
        if visible:
            self.evidence_browser.show()
            if self._browser_sizes:
                self.workspace_splitter.setSizes(self._browser_sizes)
        else:
            self._browser_sizes = self.workspace_splitter.sizes()
            self.evidence_browser.hide()
        self.browse_toggle.setText("Hide passage list" if visible else "Show passage list")

    @staticmethod
    def navigation_key(data):
        return (data.get("kind"), data.get("theme_id"), data.get("document_id"), data.get("start"), data.get("end"))

    def add_tree_node(self, tree, parent, label, data):
        item = QTreeWidgetItem([label])
        item.setData(0, Qt.ItemDataRole.UserRole, data)
        item.setToolTip(0, label)
        if parent is None:
            tree.addTopLevelItem(item)
        else:
            parent.addChild(item)
        self._tree_nodes[tree][self.navigation_key(data)] = item
        return item

    def rebuild_navigation(self, themes):
        """Recompute navigation_groups() and rebuild both trees for `themes`.

        Only the top level (themes / documents) is built here. Group rows and
        passage rows are created when a branch is expanded or navigated to
        (populate_children), so big analyses don't build tens of thousands of
        rows after every coding change.
        """
        documents = [(self.document_choice.itemData(i), self.document_choice.itemText(i)) for i in range(self.document_choice.count())]
        self._navigation_groups = navigation_groups(self.directory, {tid for tid, _, _ in themes}, {doc for doc, _ in documents}, self.run_id)
        self._nav_themes = [(tid, label) for tid, label, _ in themes]
        self._nav_documents = documents
        self._rebuilding_trees = True
        try:
            for tree in [self.document_tree, self.theme_tree]:
                expanded = {key for key, item in self._tree_nodes.get(tree, {}).items() if item.isExpanded()}
                tree.blockSignals(True)
                tree.clear()
                self._tree_nodes[tree] = {}
                if tree is self.document_tree:
                    for doc, name in documents:
                        groups = [(tid, label) for tid, label, _ in themes if (tid, doc) in self._navigation_groups]
                        passage_count = len({(row[3], row[4]) for tid, _ in groups for row in self._navigation_groups[(tid, doc)]})
                        parent = self.add_tree_node(tree, None,
                            f"{name} ({len(groups)} {'theme' if len(groups) == 1 else 'themes'} · {passage_count} {'passage' if passage_count == 1 else 'passages'})",
                            {"kind": "document", "document_id": doc})
                        parent.setToolTip(0, parent.text(0) + "\nCounts distinct themes and unique source ranges in this document; a range assigned to multiple themes counts once.")
                        if groups:
                            parent.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
                else:
                    for tid, label, _ in themes:
                        docs = [(doc, name) for doc, name in documents if (tid, doc) in self._navigation_groups]
                        count = sum(len(self._navigation_groups[(tid, doc)]) for doc, _ in docs)
                        parent = self.add_tree_node(tree, None,
                            f"{label} ({len(docs)} {'document' if len(docs) == 1 else 'documents'} · {count} {'passage' if count == 1 else 'passages'})",
                            {"kind": "theme", "theme_id": tid})
                        if docs:
                            parent.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
                # Re-open what was open before; parents first so their rows exist.
                depth = {"theme": 0, "document": 0, "group": 1}
                for key in sorted(expanded, key=lambda k: depth.get(k[0], 2)):
                    item = self.find_navigation_node(tree, {"kind": key[0], "theme_id": key[1], "document_id": key[2],
                                                            "start": key[3], "end": key[4]})
                    if item:
                        self.populate_children(item)
                        item.setExpanded(True)
                tree.blockSignals(False)
        finally:
            self._rebuilding_trees = False
        self.refresh_layers()

    def add_group_node(self, tree, parent, theme, document, label):
        count = len(self._navigation_groups[(theme, document)])
        item = self.add_tree_node(tree, parent, f"{label} ({count} {'passage' if count == 1 else 'passages'})",
            {"kind": "group", "theme_id": theme, "document_id": document})
        item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)

    def populate_children(self, item):
        """Create a branch's rows the first time it's needed. Safe to call repeatedly.

        theme/document rows get their group rows; group rows get their passages.
        """
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if item.childCount():
            return
        tree = item.treeWidget()
        kind = data.get("kind")
        if kind == "document":
            doc = data["document_id"]
            for tid, label in self._nav_themes:
                if (tid, doc) in self._navigation_groups:
                    self.add_group_node(tree, item, tid, doc, label)
            return
        if kind == "theme":
            tid = data["theme_id"]
            for doc, name in self._nav_documents:
                if (tid, doc) in self._navigation_groups:
                    self.add_group_node(tree, item, tid, doc, name)
            return
        if kind != "group":
            return
        for number, (_aid, doc, _name, start, end, preview) in enumerate(self._navigation_groups.get((data["theme_id"], data["document_id"]), []), 1):
            node = self.add_tree_node(tree, item, f"Passage {number}: {preview}",
                {"kind": "passage", "theme_id": data["theme_id"], "document_id": doc, "start": start, "end": end})
            node.setToolTip(0, f"Source characters [{start}, {end})\n{preview}")

    def tree_clicked(self, item, _column):
        # Reactivate an already selected node after a document-dropdown change.
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data and (data != self._navigation or data.get("document_id") not in (None, self.doc_id)):
            self.tree_selected(item)

    def tree_selected(self, current, _previous=None):
        if self._rebuilding_trees or current is None:
            return
        data = current.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        self._navigation = dict(data)
        self._from_tree = True
        try:
            theme = data.get("theme_id")
            self.themes.blockSignals(True)
            self.themes.setCurrentRow(-1)
            if theme:
                for index in range(self.themes.count()):
                    if self.themes.item(index).data(Qt.ItemDataRole.UserRole) == theme:
                        self.themes.setCurrentRow(index)
                        break
            self.themes.blockSignals(False)
            self.select_theme(self.themes.currentItem())
            if data.get("kind") == "document":
                index = self.document_choice.findData(data["document_id"])
                if index >= 0:
                    self.document_choice.setCurrentIndex(index)
                    self.load_document()
                    self.status.setText("Document opened. Select a theme or passage in the tree to highlight it.")
        finally:
            self._from_tree = False
        self.sync_tree_selection()

    def organization_changed(self, _index):
        tree = self.organization_tabs.currentWidget()
        if not self._tree_nodes.get(tree):
            return
        data = dict(self._navigation or {})
        if tree is self.document_tree and data.get("kind") == "theme":
            theme = data.get("theme_id")
            doc = self.doc_id
            if (theme, doc) in self._navigation_groups:
                data = {"kind": "group", "theme_id": theme, "document_id": doc}
            elif (theme, doc) not in self._navigation_groups:
                # Empty themes have no document branch until evidence is coded.
                self.sync_tree_selection()
                return
        item = self.find_navigation_node(tree, data)
        if item:
            tree.setCurrentItem(item)
            self.tree_selected(item)
        else:
            tree.blockSignals(True)
            tree.setCurrentItem(None)
            tree.blockSignals(False)

    # Kept for callers and tests written before group rows became lazy too.

    def populate_passages(self, item):
        self.populate_children(item)

    def find_navigation_node(self, tree, data):
        """The row for `data`, creating its branch first if it hasn't been opened yet."""
        if data.get("kind") in ("group", "passage"):
            top = ({"kind": "theme", "theme_id": data.get("theme_id")} if tree is self.theme_tree
                   else {"kind": "document", "document_id": data.get("document_id")})
            top_item = self._tree_nodes.get(tree, {}).get(self.navigation_key(top))
            if top_item:
                self.populate_children(top_item)
        if data.get("kind") == "passage":
            group = {"kind": "group", "theme_id": data.get("theme_id"), "document_id": data.get("document_id")}
            parent = self._tree_nodes.get(tree, {}).get(self.navigation_key(group))
            if parent:
                self.populate_passages(parent)
        return self._tree_nodes.get(tree, {}).get(self.navigation_key(data))

    def sync_tree_selection(self):
        tree = self.organization_tabs.currentWidget()
        data = self._navigation or {}
        item = self.find_navigation_node(tree, data)
        if item is None and tree is self.document_tree and data.get("theme_id") and self.doc_id:
            item = self.find_navigation_node(tree, {"kind": "group", "theme_id": data["theme_id"], "document_id": self.doc_id})
        tree.blockSignals(True)
        tree.setCurrentItem(item)
        if item:
            parent = item.parent()
            while parent:
                parent.setExpanded(True)
                parent = parent.parent()
            tree.scrollToItem(item)
        tree.blockSignals(False)

    def open_group(self, theme_id, document_id):
        """Show one theme's passages within one document (used by the overview heatmap)."""
        self.organization_tabs.setCurrentWidget(self.theme_tree)
        self.reload_themes(theme_id)
        data = {"kind": "group", "theme_id": theme_id, "document_id": document_id}
        item = self.find_navigation_node(self.theme_tree, data)
        if item is not None:
            self.theme_tree.setCurrentItem(item)
            self.tree_selected(item)

    def open_passage(self, theme_id, document_id, start, end):
        self.open_group(theme_id, document_id)
        self.view_mode.setCurrentIndex(0)
        self.select_range(document_id, start, end)
