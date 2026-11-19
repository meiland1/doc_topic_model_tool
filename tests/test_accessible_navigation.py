from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QBoxLayout


def test_guide_keyboard_order_and_activation(qt_test_application, tmp_path):
    from qualitative_themes.gui.project_guide import ProjectGuide
    guide = ProjectGuide()
    guide.set_project(SimpleNamespace(name='Practice', directory=tmp_path), True)
    guide.show()
    qt_test_application.processEvents()
    actions = []
    guide.action_requested.connect(actions.append)
    guide.buttons['add'].setFocus()
    for key in ['check', 'topics', 'code', 'export']:
        QTest.keyClick(qt_test_application.focusWidget(), Qt.Key.Key_Tab)
        assert qt_test_application.focusWidget() is guide.buttons[key]
    QTest.keyClick(guide.buttons['export'], Qt.Key.Key_Space)
    assert actions == ['export']
    QTest.keyClick(guide.buttons['export'], Qt.Key.Key_Backtab)
    assert qt_test_application.focusWidget() is guide.buttons['code']
    guide.close()


def test_hub_keyboard_advanced_and_large_text_scroll(qt_test_application):
    from qualitative_themes.gui.hub import Hub
    hub = Hub()
    hub.setFont(QFont('Segoe UI', 20))
    hub.resize(800, 600)
    hub.show()
    qt_test_application.processEvents()
    assert hub.columns.direction() == QBoxLayout.Direction.TopToBottom
    hub.advanced_toggle.setFocus()
    QTest.keyClick(hub.advanced_toggle, Qt.Key.Key_Space)
    assert not hub.setup.isHidden()
    qt_test_application.processEvents()
    hub.scroll.ensureWidgetVisible(hub.setup.add_model_button)
    qt_test_application.processEvents()
    assert hub.scroll.verticalScrollBar().maximum() > 0
    viewport = hub.scroll.viewport()
    point = hub.setup.add_model_button.mapTo(viewport, hub.setup.add_model_button.rect().center())
    assert viewport.rect().contains(point)
    hub.close()


def test_setup_text_fields_allow_tab_navigation(tmp_path, monkeypatch, qt_test_application):
    from qualitative_themes.analysis.token_budget import TokenBudget
    from qualitative_themes.storage.database import create_project
    from qualitative_themes.storage.documents import import_document
    from qualitative_themes.gui.analysis_setup import AnalysisSetupDialog
    budget = TokenBudget(SimpleNamespace(encode=lambda text, **kw: text.split()), 256, {})
    monkeypatch.setattr('qualitative_themes.analysis.token_budget.load_token_budget', lambda *a, **kw: budget)
    project = create_project(tmp_path / 'p', 'Keyboard')
    source = tmp_path / 'text.txt'
    source.write_text('A short paragraph.\n\nAnother paragraph.')
    doc = import_document(project.directory, source).document_id
    dialog = AnalysisSetupDialog(project.directory, [(doc, 'text.txt')])
    dialog.show()
    dialog.advanced_toggle.setChecked(True)
    qt_test_application.processEvents()
    dialog.ignored.setFocus()
    QTest.keyClick(dialog.ignored, Qt.Key.Key_Tab)
    assert qt_test_application.focusWidget() is dialog.phrases
    assert dialog.ignored.toPlainText() == ''
    assert dialog.topic_editor.tabChangesFocus()
    QTest.keyClick(dialog.phrases, Qt.Key.Key_Backtab)
    assert qt_test_application.focusWidget() is dialog.ignored
    dialog.reject()
