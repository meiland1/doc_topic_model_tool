"""Start screen: recent projects, setup panel, opening projects from the hub."""

from qualitative_themes.runtime import app_settings, health
from qualitative_themes.storage.database import create_project


def test_recent_projects_are_remembered_most_recent_first(tmp_path):
    for name in ["a", "b", "a"]:
        app_settings.remember_project(tmp_path / name, name.upper())
    names = [row["name"] for row in app_settings.recent_projects()]
    assert names[:2] == ["A", "B"] and names.count("A") == 1
    app_settings.forget_project(tmp_path / "a")
    assert "A" not in [row["name"] for row in app_settings.recent_projects()]


def test_window_starts_on_hub_and_switches_to_project(tmp_path):
    from qualitative_themes.gui.main_window import MainWindow
    window = MainWindow()
    assert window.pages.currentWidget() is window.hub
    project = create_project(tmp_path / "study", "Study")
    window.open_recent(str(project.directory))
    assert window.pages.currentWidget() is window.project_page
    assert app_settings.recent_projects()[0]["name"] == "Study"
    window.show_hub()
    assert window.pages.currentWidget() is window.hub
    assert "Study" in window.hub.recent.item(0).text()


def test_setup_summary_states():
    ok = health.Check("a", "A", True, "")
    optional_missing = health.Check("b", "B", False, "", required=False)
    required_missing = health.Check("c", "C", False, "")
    assert health.summary([ok]) == "ready"
    assert health.summary([ok, optional_missing]) == "limited"
    assert health.summary([ok, required_missing]) == "needs_repair"
