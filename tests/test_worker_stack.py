from PySide6.QtCore import QThread
from qualitative_themes.gui.worker import WorkerThread, configure_worker_stack, MAC_WORKER_STACK_BYTES


def test_mac_default_stack_and_explicit_override():
    worker = QThread()
    configure_worker_stack(worker, 'darwin')
    assert worker.stackSize() == MAC_WORKER_STACK_BYTES
    worker.setStackSize(32 * 1024 * 1024)
    configure_worker_stack(worker, 'darwin')
    assert worker.stackSize() == 32 * 1024 * 1024


def test_other_platform_defaults_unchanged():
    for platform in ('win32', 'linux'):
        worker = QThread()
        configure_worker_stack(worker, platform)
        assert worker.stackSize() == 0


def test_owned_workers_use_shared_base():
    import importlib
    for name in ('analysis_setup', 'history', 'main_window', 'new_project', 'project_backup', 'refinement', 'visualizations'):
        module = importlib.import_module('qualitative_themes.gui.' + name)
        for value in vars(module).values():
            if isinstance(value, type) and value.__module__ == module.__name__ and issubclass(value, QThread):
                assert issubclass(value, WorkerThread)


def test_worker_can_start_finish_and_restart(qt_test_application):
    class Example(WorkerThread):
        def run(self):
            self.completed = True
    worker = Example()
    configure_worker_stack(worker, 'darwin')
    for _ in range(2):
        worker.completed = False
        worker.start()
        assert worker.wait(5000)
        assert worker.completed
