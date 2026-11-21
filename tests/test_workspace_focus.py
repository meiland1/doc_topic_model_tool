"""View switching keeps the selection; renaming keeps an open comment draft."""
from qualitative_themes.gui.read_code import CodingWorkspace
from qualitative_themes.storage.database import create_project
from qualitative_themes.storage.documents import import_document
from qualitative_themes.storage.coding import create_theme, add_coding
from qualitative_themes.storage.comments import Target


def workspace(tmp_path):
    directory = create_project(tmp_path/'project', 'Focus').directory
    source = tmp_path/'source.txt'
    source.write_text(
        'Clinic access matters. Travel costs prevent visits. Café 😀 remains exact.', 
        encoding='utf-8',
        )
    doc = import_document(directory, source).document_id
    theme = create_theme(directory, 'Health')
    add_coding(directory, doc, 0, 21, theme)
    add_coding(directory, doc, 22, 49, theme)
    w = CodingWorkspace(); w.set_project(directory)
    return w, directory, doc, theme


def test_switch_preserves_source_range_and_navigation(qt_test_application, tmp_path):
    w, directory, doc, theme = workspace(tmp_path)
    w.hits.setCurrentRow(1)
    selected = w.viewer.textCursor().selectedText()
    w.view_mode.setCurrentIndex(1)
    assert w.evidence_views.currentWidget() is w.hits
    assert w.hits.item(0).text().endswith(w.matches[0][-1])
    w.step(-1)
    assert w.match_index == 0
    w.step(1)
    w.view_mode.setCurrentIndex(0)
    assert w.viewer.textCursor().selectedText() == selected
    assert '2 of 2' in w.passage_position.text()
    w.close()


def test_naming_refresh_preserves_selection_and_drafts(qt_test_application, tmp_path):
    from qualitative_themes.storage.coding import rename_theme
    w, directory, doc, theme = workspace(tmp_path)
    w.hits.setCurrentRow(1)
    selected = w.viewer.textCursor().selectedText()
    navigation = dict(w._navigation or {})
    w.comments_panel.begin_comment(Target.passage(doc, 22, 49), quote='Travel costs prevent visits.')
    w.comments_panel.editor.setPlainText('Comment being written')
    rename_theme(directory, theme, 'Researcher label')
    w.refresh_theme_labels()
    assert w.viewer.textCursor().selectedText() == selected
    assert (w._navigation or {}) == navigation
    assert w.match_index == 1
    assert w.comments_panel.editor.toPlainText() == 'Comment being written'
    assert w.comments_panel.pending == Target.passage(doc, 22, 49)
    assert 'Researcher label' in w.themes.currentItem().text()
    assert 'Researcher label' in w.range_themes.text()
    w.close()
