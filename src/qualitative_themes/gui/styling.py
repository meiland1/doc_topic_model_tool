"""Application-wide light theme, loaded as package data for source and installs."""
from importlib.resources import files
from PySide6.QtGui import QColor, QPalette


def apply_application_style(app):
    stylesheet = files('qualitative_themes.gui').joinpath('styles/light.qss').read_text(encoding='utf-8')
    app.setStyle('Fusion')
    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: '#f3f6f8', QPalette.ColorRole.WindowText: '#203044',
        QPalette.ColorRole.Base: '#ffffff', QPalette.ColorRole.AlternateBase: '#f3f7f9',
        QPalette.ColorRole.Text: '#203044', QPalette.ColorRole.Button: '#ffffff',
        QPalette.ColorRole.ButtonText: '#203044', QPalette.ColorRole.Highlight: '#cce8e4',
        QPalette.ColorRole.HighlightedText: '#133d38', QPalette.ColorRole.ToolTipBase: '#173448',
        QPalette.ColorRole.ToolTipText: '#ffffff', QPalette.ColorRole.PlaceholderText: '#66788b',
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    for role in [QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText]:
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor('#758393'))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet)
    return stylesheet
