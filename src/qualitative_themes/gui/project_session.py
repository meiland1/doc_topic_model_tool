"""Opening, creating, switching and closing projects; backups and restore.

Part of MainWindow (see main_window.py); not meant to be used on its own.
"""
import traceback
from pathlib import Path

from PySide6.QtWidgets import (QDialog, QFileDialog, QMessageBox, QInputDialog)

from qualitative_themes.domain.models import Project
from qualitative_themes.storage.database import open_project
from qualitative_themes.analysis.segment import DEFAULT_UNIT, UNITS
from qualitative_themes.storage.preferences import load_setup




class ProjectSessionMixin:
    def show_hub(self):
        self.hub.reload_recent()
        self.hub.refresh_setup()
        self.pages.setCurrentWidget(self.hub)

    def start_practice(self):
        if self.blocked_by_task("opening a practice project"):
            return
        if not self.flush_comments():
            return
        from qualitative_themes.storage.practice import create_practice_project
        try:
            self._show_project(create_practice_project())
            self.tabs.setCurrentWidget(self.project_guide)
        except Exception as exc:
            traceback.print_exc()
            QMessageBox.warning(self, 'Could not create practice project', str(exc))

    def guide_action(self, action):
        if not self.project:
            return
        if action == 'practice':
            self.start_practice()
        elif action == 'add':
            self.tabs.setCurrentIndex(0)
            self.import_button.click()
        elif action in {'check', 'topics'}:
            if action == 'topics' and self.workspace.run_id:
                self.tabs.setCurrentWidget(self.overview)
            else:
                self.tabs.setCurrentIndex(0)
                if self.document_list.count() and not self.document_list.selectedItems():
                    self.document_list.setCurrentRow(0)
                self.document_list.setFocus()
        else:
            self.tabs.setCurrentWidget(self.workspace)
            if action == 'export':
                self.workspace.export_button.setFocus()

    def open_recent(self, path):
        if self.blocked_by_task("switching projects"):
            return
        from qualitative_themes.runtime import app_settings
        try:
            self._show_project(open_project(Path(path)))
        except Exception as exc:
            traceback.print_exc()
            if QMessageBox.question(self, "Could not open project",
                    f"{exc}\n\nThe folder may have been moved or renamed. Remove it from recent projects?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
                app_settings.forget_project(Path(path))
                self.hub.reload_recent()

    def _show_project(self, project: Project):
        """Switch the UI to `project` and restore its saved setup and layout.

        Pre: no background task is running (callers check `busy`).
        Post: every tab points at the new project. Returns early without
        switching if an unsaved comment draft can't be saved.
        """
        if not self.flush_comments():
            return
        self.save_reading_position()
        self.project = project
        self.backup_action.setEnabled(True)
        self.pages.setCurrentWidget(self.project_page)
        try:
            from qualitative_themes.runtime import app_settings
            app_settings.remember_project(project.directory, project.name)
        except OSError:
            traceback.print_exc()
        self.analysis_settings, unit = load_setup(project.directory)
        self.analysis_unit = unit if unit in UNITS else DEFAULT_UNIT
        self.ollama_model_choice.clear()
        if self.analysis_settings.ollama_model:
            self.ollama_model_choice.addItem(self.analysis_settings.ollama_model, self.analysis_settings.ollama_model)
        self.setWindowTitle(f"{project.name} — Topic Model Tool")
        self.status.setText(f"Project: {project.name}\nLocation: {project.directory}")
        self.import_button.setEnabled(True)
        self.import_status.setText("Project ready. Import PDF, DOCX, or TXT documents.")
        self.refresh_documents()
        self.workspace.set_project(project.directory)
        self.overview.set_run(project.directory, self.workspace.run_id)
        self.visualizations.set_project(project.directory)
        self.recovery.project_changed(project.directory)
        from qualitative_themes.storage.practice import is_practice_project
        practice = is_practice_project(project.directory)
        self.project_guide.set_project(project, practice)
        if practice or not self.document_list.count():
            self.tabs.setCurrentWidget(self.project_guide)
        from qualitative_themes.storage.preferences import load_workspace_view
        view = load_workspace_view(project.directory)
        self.workspace.restore_view(view)
        tab = view.get('tab')
        if isinstance(tab, str):
            for index in range(self.tabs.count()):
                if self.tabs.tabText(index) == tab:
                    self.tabs.setCurrentIndex(index)
                    break
        self.update_naming_controls()

    def flush_comments(self):
        if self.workspace.comments_panel.flush_pending():
            return True
        QMessageBox.warning(self, "Comment draft not saved",
                            "Your comment could not be saved. Return to Comments and try again before leaving or backing up.")
        return False

    def save_reading_position(self):
        if not self.project or self.workspace.directory != self.project.directory:
            return
        from qualitative_themes.storage.preferences import save_workspace_view
        try:
            view = self.workspace.capture_view()
            view['tab'] = self.tabs.tabText(self.tabs.currentIndex())
            save_workspace_view(self.project.directory, view)
        except Exception:
            # UI preferences must not prevent closing or opening a research project.
            traceback.print_exc()

    def back_up_project(self):
        if not self.project:
            return
        if self.blocked_by_task("backing up"):
            return
        if not self.flush_comments():
            return
        from datetime import datetime
        from qualitative_themes.storage.backup import create_backup
        from qualitative_themes.gui.project_backup import transfer
        suggested = self.project.directory.parent / f"project-backup-{datetime.now():%Y%m%d-%H%M%S}.tmtbackup"
        path, _ = QFileDialog.getSaveFileName(self, "Save project backup outside the project folder",
                                             str(suggested), "Project backup (*.tmtbackup)")
        if not path:
            return
        if not Path(path).suffix:
            path += '.tmtbackup'
        try:
            transfer(self, "Backing up project", create_backup, self.project.directory, Path(path))
        except Exception as exc:
            QMessageBox.warning(self, "Could not back up project", str(exc))
            return
        QMessageBox.information(self, "Backup saved", f"Verified project backup saved to:\n{path}\n\n"
                                "It includes source documents and saved research work. Store it somewhere appropriate "
                                "for your data; the backup is not encrypted.")

    def restore_project_backup(self, archive=None):
        if self.blocked_by_task("restoring a backup"):
            return
        if not self.flush_comments():
            return
        from qualitative_themes.storage.backup import restore_backup
        from qualitative_themes.gui.project_backup import transfer
        if isinstance(archive, (str, Path)):
            path = str(archive)
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Choose a project backup", "", "Project backup (*.tmtbackup)")
        if not path:
            return
        parent = QFileDialog.getExistingDirectory(self, "Choose where to create the restored project folder")
        if not parent:
            return
        name, accepted = QInputDialog.getText(self, "Restored project folder", "New folder name:", text="Restored project")
        if not accepted:
            return
        name = name.strip()
        if not name or name in {'.', '..'} or any(c in name for c in '/\\:*?"<>|') or name.endswith('.'):
            QMessageBox.warning(self, "Invalid folder name", "Enter a simple folder name without path separators.")
            return
        destination = Path(parent) / name
        try:
            transfer(self, "Restoring project", restore_backup, Path(path), destination)
        except Exception as exc:
            QMessageBox.warning(self, "Could not restore project", str(exc))
            return
        if QMessageBox.question(self, "Project restored", f"Restored to:\n{destination}\n\nOpen this copy now?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            try:
                self._show_project(open_project(destination))
            except Exception as exc:
                QMessageBox.warning(self, "Restored but could not open", str(exc))

    def create(self):
        if self.blocked_by_task("switching projects"):
            return
        from qualitative_themes.gui.new_project import NewProjectDialog
        dialog = NewProjectDialog(self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        project = dialog.project
        dialog.deleteLater()
        if project is not None:
            self._show_project(project)
            self.tabs.setCurrentIndex(0)
            if self.document_list.count():
                self.import_status.setText("Documents imported. Check the extracted text on the right, then select "
                                           "documents and choose Analyze.")
        elif accepted:
            self.show_hub()

    def open(self):
        if self.blocked_by_task("switching projects"):
            return
        folder = QFileDialog.getExistingDirectory(self, "Open a project folder")
        if folder:
            try:
                self._show_project(open_project(Path(folder)))
            except Exception as exc:
                traceback.print_exc()
                QMessageBox.warning(self, "Could not open project", str(exc))
