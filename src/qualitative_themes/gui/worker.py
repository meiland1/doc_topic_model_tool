"""Application-owned Qt workers with a larger default stack on macOS.

Pattern used by every background task in gui/:
  - subclass WorkerThread and declare Signals (progress/result/failed/cancelled)
  - run() calls a Qt-free function from storage/ or analysis/, passing
    progress=self.progress.emit and should_cancel=self.isInterruptionRequested
  - run() never touches widgets; the window connects the signals, which Qt
    delivers on the GUI thread
  - catch AnalysisCancelled -> cancelled, other exceptions -> failed(str)
"""
import sys
from PySide6.QtCore import QThread

MAC_WORKER_STACK_BYTES = 16 * 1024 * 1024


def configure_worker_stack(thread, platform=None):
    """Give worker threads 16 MB of stack on macOS (the default is too small for torch/HDBSCAN)."""
    if (platform or sys.platform) == 'darwin' and not thread.isRunning() and thread.stackSize() == 0:
        thread.setStackSize(MAC_WORKER_STACK_BYTES)


class WorkerThread(QThread):
    """QThread with the macOS stack fix applied. Use it instead of QThread directly."""
    def __init__(self, parent=None):
        super().__init__(parent)
        configure_worker_stack(self)
