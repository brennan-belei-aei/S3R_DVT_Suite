"""Helpers for analysis outputs: opening results and formatting Excel workbooks."""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Callable, Optional


def open_with_default_app(path, on_error: Optional[Callable[[Exception], None]] = None) -> None:
    """Open a file with its default application. Failures are ignored unless on_error is given."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:
        if on_error is not None:
            on_error(exc)


def size_workbook_columns(writer, max_width: int = 55) -> None:
    """Freeze the header row, add filters and fit column widths (10..max_width) on every sheet."""
    for worksheet in writer.book.worksheets:
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions
        for cells in worksheet.columns:
            width = max(len(str(cell.value)) if cell.value is not None else 0 for cell in cells)
            worksheet.column_dimensions[cells[0].column_letter].width = min(max(width + 2, 10), max_width)
