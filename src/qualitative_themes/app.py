import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, Qt
from qualitative_themes.gui.main_window import MainWindow
from qualitative_themes.gui.fonts import configure_application_font
from qualitative_themes.gui.styling import apply_application_style


def main() -> int:
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    apply_application_style(app)
    configure_application_font(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
