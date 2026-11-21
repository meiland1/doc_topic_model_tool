"""Shared stylesheet resources and compact workspace interactions."""
from qualitative_themes.gui.read_code import CodingWorkspace
from qualitative_themes.gui.styling import apply_application_style
from qualitative_themes.storage.database import create_project
from qualitative_themes.storage.documents import import_document
from qualitative_themes.storage.coding import create_theme, add_coding


def test_tree_collapse_preserves_passage_selection(qt_test_application, tmp_path):
    directory=create_project(tmp_path/'project','Collapse').directory
    source=tmp_path/'source.txt';source.write_text('A clinic provides medication and treatment.\n\nPatients describe travel costs.')
    doc=import_document(directory,source).document_id
    theme=create_theme(directory,'Healthcare access');add_coding(directory,doc,0,42,theme)
    workspace=CodingWorkspace();workspace.set_project(directory);workspace.resize(1300,850);workspace.show()
    qt_test_application.processEvents()
    tree=workspace.theme_tree
    group=workspace.find_navigation_node(tree,{'kind':'group','theme_id':theme,'document_id':doc})
    workspace.populate_passages(group)
    workspace.tree_selected(group.child(0));before=workspace.viewer.textCursor().selectedText()
    selected=workspace._navigation.copy();width=workspace.viewer.width()
    workspace.tree_collapse_button.click();qt_test_application.processEvents()
    assert workspace.tree_collapsed() and workspace.evidence_browser.width()<=60
    assert workspace.viewer.width()>width
    assert 'Healthcare access' in workspace.tree_strip_label.text()
    assert workspace._navigation==selected and workspace.viewer.textCursor().selectedText()==before
    view=workspace.capture_view();assert view['tree_collapsed'] is True
    workspace.toggle_tree();qt_test_application.processEvents()
    assert not workspace.tree_collapsed() and workspace.evidence_browser.width()>60
    assert workspace._navigation==selected and len(workspace.viewer.extraSelections())==1
    workspace.restore_view(view);assert workspace.tree_collapsed()
    workspace.close()


def test_action_menus_forward_actions_and_follow_button_state(qt_test_application):
    workspace=CodingWorkspace();seen=[]
    action=next(action for action,button in workspace._menu_links if button is workspace.history_button)
    workspace.history_button.clicked.connect(lambda:seen.append('history'))
    workspace.sync_action_menus();assert not action.isEnabled()
    action.trigger();assert not seen
    workspace.history_button.setEnabled(True)
    workspace.sync_action_menus();assert action.isEnabled()
    action.trigger();assert seen==['history']
    workspace.history_button.setEnabled(False);workspace.sync_action_menus();assert not action.isEnabled()
    assert workspace.history_button.isHidden()  # reached from the ⋯ menu
    assert not workspace.refine_button.isHidden()  # Adjust themes sits in the bar
    workspace.close()


def test_naming_options_remain_available_from_menu(qt_test_application):
    workspace=CodingWorkspace()
    workspace.naming_options_action.setChecked(True)
    assert workspace.model_toggle.isChecked()
    assert not workspace.naming_options.isHidden()
    workspace.naming_options_action.setChecked(False)
    assert workspace.naming_options.isHidden()
    workspace.close()


def test_application_stylesheet_loads_from_package_and_preserves_text(qt_test_application):
    app=qt_test_application;old_sheet=app.styleSheet();old_palette=app.palette()
    try:
        sheet=apply_application_style(app)
        assert '#0f766e' in sheet and 'QTreeWidget' in sheet
        assert app.styleSheet()==sheet
        workspace=CodingWorkspace();workspace.viewer.setPlainText('Café 😀\nExact source evidence.')
        workspace.show();app.processEvents()
        assert workspace.viewer.toPlainText()=='Café 😀\nExact source evidence.'
        workspace.close()
    finally:
        app.setStyleSheet(old_sheet);app.setPalette(old_palette)
