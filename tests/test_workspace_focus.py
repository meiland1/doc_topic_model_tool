"""View switching and target-bound note drafts."""
from qualitative_themes.gui.coding_workspace import CodingWorkspace
from qualitative_themes.storage.database import create_project
from qualitative_themes.storage.documents import import_document
from qualitative_themes.storage.coding import create_theme, add_coding, list_notes


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
    assert not w.add_selection_button.isEnabled()
    assert w.hits.item(0).text().endswith(w.matches[0][-1])
    w.step(-1)
    assert w.match_index == 0
    w.step(1)
    w.view_mode.setCurrentIndex(0)
    assert w.viewer.textCursor().selectedText() == selected
    assert w.add_selection_button.isEnabled()
    assert '2 of 2' in w.passage_position.text()
    w.close()


def test_passage_draft_restores_without_cross_target_save(qt_test_application, tmp_path):
    w, directory, doc, theme = workspace(tmp_path)
    first = w.matches[0][0]; second = w.matches[1][0]
    w.note_editor.setPlainText('First passage draft')
    w.hits.setCurrentRow(1)
    assert not w.note_editor.toPlainText()
    w.note_editor.setPlainText('Second passage note'); w.save_new_note()
    assert list_notes(directory, coding_id=second)[0][1] == 'Second passage note'
    assert not list_notes(directory, coding_id=first)
    w.hits.setCurrentRow(0)
    assert w.note_editor.toPlainText() == 'First passage draft'
    w.save_new_note()
    assert list_notes(directory, coding_id=first)[0][1] == 'First passage draft'
    w.close()


def test_note_scope_and_drawer_preserve_drafts(qt_test_application, tmp_path):
    w, directory, doc, theme = workspace(tmp_path)
    w.note_scope.setCurrentText('Theme'); w.note_editor.setPlainText('Theme draft')
    w.notes_toggle.setChecked(True); w.notes_toggle.setChecked(False)
    assert w.notes_drawer.isHidden()
    w.note_scope.setCurrentText('Document')
    assert not w.note_editor.toPlainText()
    w.note_editor.setPlainText('Document note'); w.save_new_note()
    w.note_scope.setCurrentText('Theme')
    assert w.note_editor.toPlainText() == 'Theme draft'
    assert not list_notes(directory, theme_id=theme)
    w.save_new_note()
    assert list_notes(directory, theme_id=theme)[0][1] == 'Theme draft'
    assert list_notes(directory, document_id=doc)[0][1] == 'Document note'
    w.close()


def test_edit_draft_keeps_original_note_id(qt_test_application, tmp_path):
    w, directory, doc, theme = workspace(tmp_path)
    w.note_editor.setPlainText('Original'); w.save_new_note()
    w.note_list.setCurrentRow(0)
    w.note_editor.setPlainText('Edited')
    w.hits.setCurrentRow(1); w.hits.setCurrentRow(0)
    assert w.note_editor.toPlainText() == 'Edited'
    w.save_note_edit()
    rows = list_notes(directory, coding_id=w.matches[0][0])
    assert len(rows) == 1 and rows[0][1] == 'Edited'
    w.close()
