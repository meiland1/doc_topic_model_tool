"""Choose an installed scalable UI font before creating application widgets."""
import sys
from PySide6.QtGui import QFont, QFontDatabase


def configure_application_font(app):
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    candidates = (["Segoe UI", "Arial"] if sys.platform == "win32" else []) + [
        font.family(), "Noto Sans", "DejaVu Sans", "Liberation Sans", "Arial"]
    installed = {family.casefold(): family for family in QFontDatabase.families()}
    candidates += sorted(installed.values())
    for candidate in dict.fromkeys(candidates):
        family = installed.get(candidate.casefold())
        if family and QFontDatabase.isSmoothlyScalable(family):
            font.setFamily(family)
            break
    font.setStyleHint(QFont.StyleHint.SansSerif)
    if font.pointSizeF() <= 0:
        font.setPointSizeF(10.0)
    app.setFont(font)
    return font
