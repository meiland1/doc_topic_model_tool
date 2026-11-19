"""New project wizard: folder naming, file expansion, and create-and-import."""
from pathlib import Path

from qualitative_themes.gui.new_project import NewProjectDialog, available_folder, expand_paths, folder_name
from qualitative_themes.storage.documents import list_documents

CORPUS = Path(__file__).resolve().parents[1] / "examples" / "smoke_corpus"


def test_folder_names_are_safe_and_never_reuse_a_full_folder(tmp_path):
    assert folder_name('Clinic: "access"?') == "Clinic access"
    assert folder_name("...") == "Untitled project"
    (tmp_path / "Study").mkdir()
    (tmp_path / "Study" / "x.txt").write_text("x")
    assert available_folder(tmp_path, "Study").name == "Study (2)"
    (tmp_path / "Empty").mkdir()
    assert available_folder(tmp_path, "Empty").name == "Empty"


def test_folders_expand_to_supported_visible_files(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.png").write_bytes(b"")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "c.txt").write_text("c")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "d.docx").write_bytes(b"")
    names = [p.name for p in expand_paths([tmp_path, tmp_path / "a.txt"])]
    assert names == ["a.txt", "d.docx"]


def test_wizard_creates_project_and_imports_documents(tmp_path, qt_test_application):
    dialog = NewProjectDialog(parent_folder=tmp_path)
    dialog.name.setText("Smoke study")
    assert Path(dialog.location.text()) == tmp_path / "Smoke study"
    dialog.add_paths([CORPUS])
    assert dialog.table.rowCount() == 3
    dialog.primary_clicked()
    dialog._worker.wait(20000)
    qt_test_application.processEvents()
    assert dialog.project is not None and dialog._worker is None
    assert len(list_documents(dialog.project.directory)) == 3
    assert dialog.primary.text().startswith("Open project")
    statuses = {dialog.table.item(row, 2).text() for row in range(3)}
    assert statuses == {"Ready"}
