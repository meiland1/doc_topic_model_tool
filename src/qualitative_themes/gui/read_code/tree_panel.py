"""The left panel: the theme and document trees.

Trees are built lazily: rebuild_navigation() makes only the top level;
populate_children() adds groups and passages when a row is expanded.

Part of CodingWorkspace (see workspace.py): these methods use the shared
state documented there and are not meant to be used on their own.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMenu, QPushButton,
                               QHeaderView, QStackedLayout, QTabWidget, QToolButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from qualitative_themes.gui.viewer_parts import swatch_icon
from qualitative_themes.storage.navigation import navigation_groups

TREE_WIDTH = 300
STRIP_WIDTH = 52
SORTS = ("Most passages", "A–Z")


_SWATCHES = {}


def _swatch(index):
    """Theme colour dot, cached: trees can have hundreds of theme rows."""
    if index not in _SWATCHES:
        _SWATCHES[index] = swatch_icon(index)
    return _SWATCHES[index]


def _plural(count, word):
    return f"{count} {word if count == 1 else word + 's'}"


class TreePanelMixin:
    def _build_tree_panel(self, splitter):
        """Left panel: By theme / By document trees, search, sort and + New theme.

        « collapses it to a narrow strip that names the current theme (Ctrl+\\).
        """
        left = QWidget()
        self.evidence_browser = left
        self.tree_pages = QStackedLayout(left)
        full = QWidget()
        left_lay = QVBoxLayout(full)
        left_lay.setContentsMargins(0, 0, 4, 0)
        # Internal flat selection controller keeps theme actions independent of
        # which tree contains the selection. Only the two trees are displayed.
        self.themes = QListWidget(self)
        self.themes.hide()
        self.themes.currentItemChanged.connect(self.select_theme)
        top = QHBoxLayout()
        self.tree_search = QLineEdit()
        self.tree_search.setPlaceholderText("Search themes and documents")
        self.tree_search.setClearButtonEnabled(True)
        self.tree_search.setAccessibleName("Search the tree")
        self.tree_search.textChanged.connect(self.filter_tree)
        top.addWidget(self.tree_search, 1)
        self.tree_collapse_button = QToolButton()
        self.tree_collapse_button.setText("«")
        self.tree_collapse_button.setToolTip("Hide the tree to give the document more room (Ctrl+\\)")
        self.tree_collapse_button.setAccessibleName("Hide the tree")
        self.tree_collapse_button.clicked.connect(lambda: self.set_tree_collapsed(True))
        top.addWidget(self.tree_collapse_button)
        left_lay.addLayout(top)
        self.organization_tabs = QTabWidget()
        self.document_tree = QTreeWidget()
        self.theme_tree = QTreeWidget()
        for tree, label in [(self.document_tree, "By document"), (self.theme_tree, "By theme")]:
            tree.setHeaderHidden(True)
            tree.setColumnCount(2)  # name | passage count
            tree.header().setStretchLastSection(False)
            tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            tree.setAccessibleName(label + " tree")
            tree.setWordWrap(True)
            tree.currentItemChanged.connect(self.tree_selected)
            tree.itemClicked.connect(self.tree_clicked)
            tree.itemExpanded.connect(self.populate_children)
            tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            tree.customContextMenuRequested.connect(lambda point, t=tree: self.tree_menu(t, point))
            self.organization_tabs.addTab(tree, label)
        self.organization_tabs.setCurrentIndex(1)
        self.organization_tabs.currentChanged.connect(self.organization_changed)
        left_lay.addWidget(self.organization_tabs, 1)
        bottom = QHBoxLayout()
        self.tree_sort = QComboBox()
        self.tree_sort.addItems(SORTS)
        self.tree_sort.setAccessibleName("Sort the tree")
        self.tree_sort.currentIndexChanged.connect(lambda _i: self.reload_themes())
        bottom.addWidget(QLabel("Sort"))
        bottom.addWidget(self.tree_sort, 1)
        new_theme = QPushButton("+ New theme")
        new_theme.clicked.connect(lambda: self.new_theme())
        bottom.addWidget(new_theme)
        left_lay.addLayout(bottom)
        self.theme_origin_label = QLabel("Came from: select a theme.")
        self.theme_origin_label.setObjectName("quiet")
        self.theme_origin_label.setTextFormat(Qt.TextFormat.PlainText)
        self.theme_origin_label.setWordWrap(True)
        self.theme_origin_label.setAccessibleName("Selected theme origin")
        left_lay.addWidget(self.theme_origin_label)
        self.theme_keywords_label = QLabel("Keywords: Select a theme.")
        self.theme_keywords_label.setObjectName("quiet")
        self.theme_keywords_label.setTextFormat(Qt.TextFormat.PlainText)
        self.theme_keywords_label.setWordWrap(True)
        self.theme_keywords_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.theme_keywords_label.setAccessibleName("Selected theme keywords")
        self.theme_keywords_label.setToolTip("Words that set this theme's passages apart from the rest. "
            "The percentage shows how strongly each word belongs to the theme; it isn't a probability. "
            "Merged themes show each word's highest score. Keywords are separate from the theme's name.")
        left_lay.addWidget(self.theme_keywords_label)
        self.tree_pages.addWidget(full)

        strip = QWidget()
        strip_lay = QVBoxLayout(strip)
        strip_lay.setContentsMargins(2, 0, 2, 0)
        self.tree_expand_button = QToolButton()
        self.tree_expand_button.setText("»")
        self.tree_expand_button.setToolTip("Show the tree (Ctrl+\\)")
        self.tree_expand_button.setAccessibleName("Show the tree")
        self.tree_expand_button.clicked.connect(lambda: self.set_tree_collapsed(False))
        strip_lay.addWidget(self.tree_expand_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self.tree_strip_label = QLabel()
        self.tree_strip_label.setObjectName("treeStrip")
        self.tree_strip_label.setWordWrap(True)
        self.tree_strip_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        strip_lay.addWidget(self.tree_strip_label, 1)
        self.tree_pages.addWidget(strip)
        self._tree_width = TREE_WIDTH
        splitter.addWidget(left)

    def tree_collapsed(self) -> bool:
        return self.tree_pages.currentIndex() == 1

    def set_tree_collapsed(self, collapsed: bool):
        """Swap the tree for the narrow strip (or back). Selection and scope are untouched."""
        if collapsed == self.tree_collapsed():
            return
        sizes = self.workspace_splitter.sizes()
        if collapsed:
            self._tree_width = max(sizes[0], STRIP_WIDTH * 2)
            self.tree_pages.setCurrentIndex(1)
            self.evidence_browser.setFixedWidth(STRIP_WIDTH)
            sizes[1] += sizes[0] - STRIP_WIDTH
            sizes[0] = STRIP_WIDTH
            self.update_tree_strip()
        else:
            self.evidence_browser.setMinimumWidth(0)
            self.evidence_browser.setMaximumWidth(16777215)
            self.tree_pages.setCurrentIndex(0)
            sizes[1] = max(200, sizes[1] - (self._tree_width - sizes[0]))
            sizes[0] = self._tree_width
        self.workspace_splitter.setSizes(sizes)

    def toggle_tree(self):
        self.set_tree_collapsed(not self.tree_collapsed())

    def update_tree_strip(self):
        """The collapsed strip shows the current theme's colour and a short name."""
        if not self.tree_collapsed():
            return
        theme = self.current_theme_id()
        label = next((name for tid, name, _ in self.visible_themes() if tid == theme), "") if theme else ""
        short = label if len(label) <= 40 else label[:39] + "…"
        self.tree_strip_label.setText(short)
        self.tree_strip_label.setToolTip(label)

    def tree_menu(self, tree, point):
        """Right-click on a theme row: rename or merge it."""
        item = tree.itemAt(point)
        data = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if not data or not data.get("theme_id"):
            return
        tree.setCurrentItem(item)
        menu = QMenu(tree)
        menu.addAction("Rename theme…", self.rename_selected_theme)
        menu.addAction("Merge into another theme…", self.merge_selected_theme)
        menu.exec(tree.viewport().mapToGlobal(point))

    def filter_tree(self, _text=None):
        """Hide top-level rows whose name doesn't contain the search text (both trees)."""
        needle = self.tree_search.text().strip().lower()
        for tree in (self.document_tree, self.theme_tree):
            for index in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(index)
                name = item.data(0, Qt.ItemDataRole.UserRole + 1) or item.text(0)
                item.setHidden(bool(needle) and needle not in name.lower())

    @staticmethod
    def navigation_key(data):
        return (data.get("kind"), data.get("theme_id"), data.get("document_id"), data.get("start"), data.get("end"))

    def add_tree_node(self, tree, parent, label, data, name=None, colour=None, count=None, detail=None):
        """Add a row. `name` is what the search box matches; `colour` a theme colour index for the dot;
        `count` goes in the right-hand column and `detail` in the tooltip."""
        item = QTreeWidgetItem([label, "" if count is None else f"{count:,}"])
        item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        item.setData(0, Qt.ItemDataRole.UserRole, data)
        item.setData(0, Qt.ItemDataRole.UserRole + 1, name or label)
        item.setToolTip(0, detail or label)
        item.setToolTip(1, detail or label)
        if colour is not None:
            item.setIcon(0, _swatch(colour))
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
        colours = {tid: index for index, (tid, _label, _count) in enumerate(themes)}
        self._nav_colours = colours
        self._rebuilding_trees = True
        try:
            for tree in [self.document_tree, self.theme_tree]:
                expanded = {key for key, item in self._tree_nodes.get(tree, {}).items() if item.isExpanded()}
                tree.blockSignals(True)
                tree.clear()
                self._tree_nodes[tree] = {}
                by_count = self.tree_sort.currentIndex() == 0
                if tree is self.document_tree:
                    rows = []
                    for doc, name in documents:
                        groups = [(tid, label) for tid, label, _ in themes if (tid, doc) in self._navigation_groups]
                        passage_count = len({(row[3], row[4]) for tid, _ in groups for row in self._navigation_groups[(tid, doc)]})
                        rows.append((doc, name, groups, passage_count))
                    if by_count:
                        rows.sort(key=lambda row: -row[3])
                    for doc, name, groups, passage_count in rows:
                        parent = self.add_tree_node(tree, None, name, {"kind": "document", "document_id": doc}, count=passage_count,
                            detail=f"{name}\n{_plural(len(groups), 'theme')}, {_plural(passage_count, 'passage')}"
                                   "\nA passage coded to more than one theme counts once.")
                        if groups:
                            parent.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
                else:
                    rows = []
                    for tid, label, _ in themes:
                        docs = [(doc, name) for doc, name in documents if (tid, doc) in self._navigation_groups]
                        count = sum(len(self._navigation_groups[(tid, doc)]) for doc, _ in docs)
                        rows.append((tid, label, docs, count))
                    if by_count:
                        rows.sort(key=lambda row: -row[3])
                    for tid, label, docs, count in rows:
                        parent = self.add_tree_node(tree, None, label, {"kind": "theme", "theme_id": tid},
                            colour=colours.get(tid), count=count,
                            detail=f"{label}\n{_plural(count, 'passage')} in {_plural(len(docs), 'document')}")
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
        self.filter_tree()
        self.refresh_layers()

    def add_group_node(self, tree, parent, theme, document, label):
        count = len(self._navigation_groups[(theme, document)])
        colour = self._nav_colours.get(theme) if tree is self.document_tree else None
        item = self.add_tree_node(tree, parent, label, {"kind": "group", "theme_id": theme, "document_id": document},
            colour=colour, count=count, detail=f"{label}\n{_plural(count, 'passage')}")
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
            docs = [(doc, name) for doc, name in self._nav_documents if (tid, doc) in self._navigation_groups]
            if self.tree_sort.currentIndex() == 0:
                docs.sort(key=lambda row: -len(self._navigation_groups[(tid, row[0])]))
            for doc, name in docs:
                self.add_group_node(tree, item, tid, doc, name)
            return
        if kind != "group":
            return
        for number, (_aid, doc, _name, start, end, preview) in enumerate(self._navigation_groups.get((data["theme_id"], data["document_id"]), []), 1):
            short = " ".join(preview.split())
            short = short if len(short) <= 120 else short[:119] + "…"
            node = self.add_tree_node(tree, item, f"Passage {number}\n{short}",
                {"kind": "passage", "theme_id": data["theme_id"], "document_id": doc, "start": start, "end": end})
            node.setToolTip(0, preview)

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
