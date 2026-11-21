"""The Everything (Excel) export: Themes, Passages and About sheets in one .xlsx file."""
from __future__ import annotations

from pathlib import Path

EXCEL_CELL_LIMIT = 32767
WIDE = {"Passage": 80, "Example 1": 50, "Example 2": 50, "Example 3": 50, "Comments": 40, "Description": 40,
        "Keywords": 30}


def _cell_text(value):
    """Excel can't hold control characters or more than 32,767 characters in a cell."""
    if not isinstance(value, str):
        return value
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    value = ILLEGAL_CHARACTERS_RE.sub("", value)
    if len(value) > EXCEL_CELL_LIMIT:
        note = " … [cut: Excel holds up to 32,767 characters in a cell; the CSV export has it all]"
        value = value[:EXCEL_CELL_LIMIT - len(note)] + note
    return value


def write_workbook(path: Path, sheets, about: str, check_cancel=lambda: None) -> None:
    """Write `sheets` [(name, headers, rows), …] and an About sheet (one line per row) to `path`.

    Text is always stored as text, so a passage that starts with "=" is never
    read as a formula.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter
    book = Workbook()
    book.remove(book.active)
    bold, wrap = Font(bold=True), Alignment(wrap_text=True, vertical="top")
    for name, headers, rows in sheets:
        sheet = book.create_sheet(name)
        sheet.append(headers)
        for cell in sheet[1]:
            cell.font = bold
        for row in rows:
            check_cancel()
            sheet.append([_cell_text(value) for value in row])
        for cell in sheet.iter_cols(min_row=2, values_only=False):
            for item in cell:
                if item.data_type == "f":
                    item.data_type = "s"
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for index, header in enumerate(headers, start=1):
            letter = get_column_letter(index)
            sheet.column_dimensions[letter].width = WIDE.get(header, max(12, len(header) + 2))
            if header in WIDE:
                for item in sheet[letter][1:]:
                    item.alignment = wrap
    sheet = book.create_sheet("About")
    sheet.column_dimensions["A"].width = 120
    for line in about.splitlines():
        sheet.append([_cell_text(line)])
        if line.startswith("="):
            sheet.cell(sheet.max_row, 1).data_type = "s"
    book.save(path)
