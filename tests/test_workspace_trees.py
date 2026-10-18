"""Offscreen navigation checks with exact source ranges and no model downloads."""
import pytest
from PySide6.QtCore import Qt
from qualitative_themes.gui.coding_workspace import CodingWorkspace
from qualitative_themes.analysis.topics import analyze, AnalysisSettings, TopicOutput
from qualitative_themes.storage.database import create_project, connect
from qualitative_themes.storage.documents import import_document
from qualitative_themes.storage.coding import create_theme, add_coding, reject_model_suggestion, merge_themes, rename_theme

@pytest.fixture
def tree_workspace(tmp_path, qt_test_application):
    directory=create_project(tmp_path/'project','Trees').directory
    docs=[]
    for i in range(3):
        source=tmp_path/f'document{i}.txt'
        source.write_text('\n\n'.join([f'Document {i} clinic 😀 evidence one.',f'Document {i} clinic evidence two.',f'Document {i} orbit evidence three.']),encoding='utf-8')
        docs.append(import_document(directory,source).document_id)
    def fake(passages,*args):
        return TopicOutput([0,0,1,0,1,1],{0:[('clinic',.4)],1:[('space',.5)]})
    run,_=analyze(directory,'Paragraphs',docs[:2],AnalysisSettings(min_topic_size=2),backend=fake)
    with connect(directory) as conn:
        themes={topic:theme for topic,theme in conn.execute('SELECT DISTINCT bertopic_id,theme_id FROM model_suggestions WHERE run_id=?',(run,))}
    workspace=CodingWorkspace();workspace.set_project(directory)
    yield workspace,directory,docs,themes,run
    workspace.close()


def choose(workspace, tree, data):
    workspace.organization_tabs.setCurrentWidget(tree)
    item=workspace.find_navigation_node(tree,data)
    assert item is not None,data
    tree.setCurrentItem(item)
    workspace.tree_selected(item)  # Also exercises re-clicking a selected item.
    return item


def test_document_theme_and_single_passage_highlights(tree_workspace):
    w,directory,docs,themes,run=tree_workspace
    choose(w,w.document_tree,{'kind':'document','document_id':docs[0]})
    assert w.doc_id==docs[0]
    assert not w.viewer.extraSelections()
    assert w.current_theme_id() is None
    group=choose(w,w.document_tree,{'kind':'group','theme_id':themes[0],'document_id':docs[0]})
    assert len(w.matches)==2
    assert len(w.viewer.extraSelections())==2
    assert all(m[1]==docs[0] for m in w.matches)
    w.populate_passages(group)
    passage=group.child(1)
    data=passage.data(0,Qt.ItemDataRole.UserRole)
    choose(w,w.document_tree,data)
    assert len(w.viewer.extraSelections())==1
    selected=w.viewer.textCursor().selectedText()
    assert 'evidence two' in selected
    assert w.coords.canonical_range(w.viewer.textCursor().selectionStart(),w.viewer.textCursor().selectionEnd())==(data['start'],data['end'])
    choose(w,w.document_tree,{'kind':'group','theme_id':themes[0],'document_id':docs[0]})
    assert len(w.viewer.extraSelections())==2


def test_theme_document_and_passage_views_agree(tree_workspace):
    w,directory,docs,themes,run=tree_workspace
    choose(w,w.theme_tree,{'kind':'theme','theme_id':themes[0]})
    assert len(w.matches)==3
    assert {m[1] for m in w.matches}==set(docs[:2])
    group=choose(w,w.theme_tree,{'kind':'group','theme_id':themes[0],'document_id':docs[1]})
    assert len(w.matches)==1 and w.doc_id==docs[1]
    w.populate_passages(group)
    data=group.child(0).data(0,Qt.ItemDataRole.UserRole)
    choose(w,w.theme_tree,data)
    selected=w.viewer.textCursor().selectedText()
    w.organization_tabs.setCurrentWidget(w.document_tree)
    assert w.document_tree.currentItem().data(0,Qt.ItemDataRole.UserRole)==data
    assert w.viewer.textCursor().selectedText()==selected
    assert len(w.viewer.extraSelections())==1
    w.organization_tabs.setCurrentWidget(w.theme_tree)
    assert w.theme_tree.currentItem().data(0,Qt.ItemDataRole.UserRole)==data


def test_passage_nodes_are_lazy_and_analysis_scope_excludes_other_docs(tree_workspace):
    w,directory,docs,themes,run=tree_workspace
    assert w.document_tree.topLevelItemCount()==2
    assert all(key[2]!=docs[2] for nodes in w._tree_nodes.values() for key in nodes)
    groups=[item for key,item in w._tree_nodes[w.theme_tree].items() if key[0]=='group']
    assert all(item.childCount()==0 for item in groups)
    item=groups[0];item.setExpanded(True)
    assert item.childCount()==len(w._navigation_groups[(item.data(0,Qt.ItemDataRole.UserRole)['theme_id'],item.data(0,Qt.ItemDataRole.UserRole)['document_id'])])


def test_manual_scope_rejections_and_empty_theme(tree_workspace):
    w,directory,docs,themes,run=tree_workspace
    match=w.matches[0];theme=w.current_theme_id()
    add_coding(directory,match[1],match[3],match[4],theme)
    empty=create_theme(directory,'New empty code')
    w.run_choice.setCurrentIndex(w.run_choice.findData(None))
    assert w.document_tree.topLevelItemCount()==3
    roots={item.data(0,Qt.ItemDataRole.UserRole)['theme_id'] for item in [w.theme_tree.topLevelItem(i) for i in range(w.theme_tree.topLevelItemCount())]}
    assert roots=={theme,empty}
    choose(w,w.theme_tree,{'kind':'theme','theme_id':empty})
    assert not w.matches and not w.viewer.extraSelections()
    w.reload_runs(run)
    group=choose(w,w.document_tree,{'kind':'group','theme_id':themes[0],'document_id':docs[0]})
    old=len(w.matches);removed=w.matches[-1]
    reject_model_suggestion(directory,removed[1],removed[3],removed[4],themes[0])
    w.reload_themes(themes[0])
    assert len(w.matches)==old-1
    assert all(m[3:5]!=removed[3:5] for m in w.matches)


def test_tree_actions_update_rename_merge_and_research_notes(tree_workspace):
    w,directory,docs,themes,run=tree_workspace
    group=choose(w,w.theme_tree,{'kind':'group','theme_id':themes[0],'document_id':docs[0]})
    w.populate_passages(group)
    data=group.child(0).data(0,Qt.ItemDataRole.UserRole)
    choose(w,w.theme_tree,data)
    w.note_editor.setPlainText('Exact passage memo');w.save_new_note()
    assert w.note_list.count()==1
    assert len(w.viewer.extraSelections())==1
    rename_theme(directory,themes[0],'Care access');w.reload_themes(themes[0])
    root=w.find_navigation_node(w.theme_tree,{'kind':'theme','theme_id':themes[0]})
    assert root.text(0).startswith('Care access')
    merge_themes(directory,themes[1],themes[0]);w.reload_themes(themes[0])
    assert w.theme_tree.topLevelItemCount()==1
    assert sum(len(rows) for (theme,doc),rows in w._navigation_groups.items())==6


def test_document_click_clears_previous_theme_and_highlight(tree_workspace):
    w,directory,docs,themes,run=tree_workspace
    choose(w,w.theme_tree,{'kind':'theme','theme_id':themes[0]})
    assert w.viewer.extraSelections()
    choose(w,w.document_tree,{'kind':'document','document_id':docs[1]})
    assert w.doc_id==docs[1] and not w.matches
    assert not w.viewer.extraSelections()
    assert not w.viewer.textCursor().hasSelection()
    assert w.current_theme_id() is None
    assert not w.themes.isVisible()


def test_dropdown_tracks_document_scope_and_reclick_restores_tree_document(tree_workspace):
    w,directory,docs,themes,run=tree_workspace
    group=choose(w,w.document_tree,{'kind':'group','theme_id':themes[0],'document_id':docs[0]})
    w.document_choice.setCurrentIndex(w.document_choice.findData(docs[1]))
    assert w.doc_id==docs[1] and len(w.matches)==1
    assert all(match[1]==docs[1] for match in w.matches)
    w.tree_clicked(group,0)
    assert w.doc_id==docs[0] and len(w.matches)==2


def test_exact_unicode_passage_highlight_and_next_stays_single(tree_workspace):
    w,directory,docs,themes,run=tree_workspace
    group=choose(w,w.theme_tree,{'kind':'group','theme_id':themes[0],'document_id':docs[0]})
    w.populate_passages(group)
    data=group.child(0).data(0,Qt.ItemDataRole.UserRole)
    choose(w,w.theme_tree,data)
    assert '😀' in w.viewer.textCursor().selectedText()
    assert len(w.viewer.extraSelections())==1
    w.step(1)
    assert 'evidence two' in w.viewer.textCursor().selectedText()
    assert len(w.viewer.extraSelections())==1
    assert w.theme_tree.currentItem().data(0,Qt.ItemDataRole.UserRole)['start']==w.matches[w.match_index][3]
