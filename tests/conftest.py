"""Keep one Qt application alive and destroy test windows deterministically."""
import gc
import os
import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def _dispose_test_windows(app):
    from PySide6.QtCore import QCoreApplication, QEvent
    # close() hides a widget; it does not necessarily destroy its native object.
    for widget in app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    gc.collect()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(scope='session', autouse=True)
def qt_test_application():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        yield None
        return
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    yield app
    _dispose_test_windows(app)
    app.quit()


@pytest.fixture(autouse=True)
def qt_test_window_cleanup(qt_test_application):
    yield
    if qt_test_application is not None:
        _dispose_test_windows(qt_test_application)
