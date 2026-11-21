"""Getting around: the project switcher, the Back card, Close project and document ticks."""

from unittest import mock

from PySide6.QtCore import Qt

from qualitative_themes.storage.database import create_project
from qualitative_themes.storage.documents import import_document


def _window_with_project(tmp_path, documents=0):
    from qualitative_themes.gui.main_window import MainWindow
    project = create_project(tmp_path / "study", "Study")
    for index in range(documents):
        source = tmp_path / f"interview_{index}.txt"
        source.write_text(f"Interview {index}. We talked about rent and buses.\n\nMore talk.", encoding="utf-8")
        import_document(project.directory, source)
    window = MainWindow()
    window.open_recent(str(project.directory))
    return window


def test_start_screen_and_back_keeps_the_project_loaded(tmp_path):
    window = _window_with_project(tmp_path)
    opened = window.project
    with mock.patch("qualitative_themes.gui.project_session.open_project") as reopen:
        window.show_hub()
        assert window.hub.back_card.isVisibleTo(window.hub)
        assert "Study" in window.hub.back_title.text()
        window.back_to_project()
    reopen.assert_not_called()
    assert window.project is opened
    assert window.pages.currentWidget() is window.project_page


def test_switcher_names_the_project_and_lists_the_ways_out(tmp_path):
    window = _window_with_project(tmp_path)
    assert window.title_bar.switcher.isVisibleTo(window.title_bar)
    assert "Study" in window.title_bar.switcher.text()
    window.title_bar.fill_menu()
    labels = [action.text() for action in window.title_bar.menu.actions() if action.text()]
    assert any("Open another project" in label for label in labels)
    assert any("New project" in label for label in labels)
    assert any("Start screen" in label for label in labels)


def test_close_project_leaves_nothing_open(tmp_path):
    window = _window_with_project(tmp_path)
    window.close_project()
    assert window.project is None
    assert window.pages.currentWidget() is window.hub
    assert not window.title_bar.switcher.isVisibleTo(window.title_bar)
    assert not window.hub.back_card.isVisibleTo(window.hub)
    assert not any(action.isEnabled() for action in window.project_actions)
    window.back_to_project()  # nothing to go back to
    assert window.pages.currentWidget() is window.hub


def test_ticks_survive_a_refresh_and_gate_find_themes(tmp_path):
    window = _window_with_project(tmp_path, documents=2)
    assert window.document_list.count() == 2
    window.document_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    window.refresh_documents()
    states = [window.document_list.item(i).checkState() for i in range(2)]
    assert states.count(Qt.CheckState.Unchecked) == 1
    assert len(window.checked_document_items()) == 1
    window.set_all_checked(False)
    assert not window.analyze_button.isEnabled()
    window.set_all_checked(True)
    assert len(window.checked_document_items()) == 2


def test_quick_guide_without_a_project_only_offers_starting(tmp_path):
    from qualitative_themes.gui.main_window import MainWindow
    window = MainWindow()
    window.show_quick_guide()
    assert window.pages.currentWidget() is window.quick_guide
