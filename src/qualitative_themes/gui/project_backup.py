"""Modal transfer progress keeps editing disabled without blocking painting."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QVBoxLayout


from qualitative_themes.gui.worker import WorkerThread


class TransferWorker(WorkerThread):
    """Run a backup/restore function off the GUI thread; keeps `result` or `error`."""
    progress = Signal(str)

    def __init__(self, operation, source, destination, parent):
        super().__init__(parent)
        self.operation, self.source, self.destination = operation, source, destination
        self.error = None
        self.result = None

    def run(self):
        try:
            self.result = self.operation(self.source, self.destination, self.progress.emit)
        except Exception as exc:
            self.error = str(exc)


class TransferDialog(QDialog):
    """Modal progress dialog that can't be closed until the transfer finishes."""
    def __init__(self, title, operation, source, destination, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        self.message = QLabel('Preparing…')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        bar = QProgressBar()
        bar.setRange(0, 0)
        layout.addWidget(bar)
        layout.addWidget(QLabel('Please keep the application open until this finishes.'))
        self.worker = TransferWorker(operation, source, destination, self)
        self.worker.progress.connect(self.message.setText)
        self.worker.finished.connect(self.completed)

    def completed(self):
        self.worker.wait()
        self.accept()

    def reject(self):
        if not self.worker.isRunning():
            super().reject()

    def closeEvent(self, event):
        if self.worker.isRunning():
            event.ignore()
        else:
            super().closeEvent(event)


def transfer(parent, title, operation, source, destination):
    """Run operation(source, destination, progress) with a blocking progress dialog.

    Returns the operation's result; raises ValueError with its message on failure.
    """
    dialog = TransferDialog(title, operation, source, destination, parent)
    dialog.worker.start()
    dialog.exec()
    dialog.worker.wait()
    error = dialog.worker.error
    result = dialog.worker.result
    dialog.deleteLater()
    if error:
        raise ValueError(error)
    return result
