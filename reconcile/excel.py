"""Excel load via Polars + fastexcel (string columns; no OOXML inspection)."""

from __future__ import annotations

from pathlib import Path

import fastexcel

from reconcile.delimited import abs_path, stringify_and_drop_empty_rows
from reconcile.errors import HardFail


def load_excel(path: str | Path, sheet_name: str) -> pl.DataFrame:
    """Load an Excel sheet as a Utf8 Polars frame. No Python list dump."""
    path = abs_path(path)
    p = Path(path)
    if not p.is_file():
        raise HardFail(f"Missing path: {path}")
    if not sheet_name:
        raise HardFail(f"Sheet name required for Excel file {path}")
    try:
        reader = fastexcel.read_excel(path)
    except Exception as exc:
        raise HardFail(f"Failed to open Excel workbook {path}: {exc}") from exc
    try:
        sheet = reader.load_sheet(
            sheet_name,
            header_row=0,
            dtypes="string",
        )
        df = sheet.to_polars()
    except fastexcel.SheetNotFoundError as exc:
        available = ", ".join(repr(n) for n in reader.sheet_names)
        raise HardFail(
            f"Missing sheet {sheet_name!r} in {path}. Available: {available}"
        ) from exc
    except Exception as exc:
        raise HardFail(
            f"Failed to read Excel sheet {sheet_name!r} in {path}: {exc}"
        ) from exc
    return stringify_and_drop_empty_rows(df)
