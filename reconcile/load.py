"""Load one side (delimited or Excel) into a string Polars frame."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import polars as pl

from reconcile.delimited import Detection, abs_path, load_delimited
from reconcile.errors import HardFail
from reconcile.excel import load_excel

EXCEL_SUFFIXES = {".xlsx", ".xlsm"}


@dataclass
class SideTable:
    path: str
    sheet: str | None
    headers: list[str]
    frame: pl.DataFrame
    detection: Detection | None  # None for Excel


def is_excel_path(path: str) -> bool:
    return Path(path).suffix.lower() in EXCEL_SUFFIXES


def _check_unique_headers(headers: list[str], side: str) -> None:
    counts: dict[str, int] = {}
    for name in headers:
        counts[name] = counts.get(name, 0) + 1
    dups = [name for name, n in counts.items() if n > 1]
    if dups:
        raise HardFail(f"Duplicate column name on side {side}: {dups[0]!r}")


def _frame_from_rows(headers: list[str], rows: list[list[str]]) -> pl.DataFrame:
    if not headers:
        raise HardFail("Header row is required")
    schema = {h: pl.Utf8 for h in headers}
    if not rows:
        return pl.DataFrame({h: [] for h in headers}).cast(schema)
    return pl.DataFrame(rows, schema=headers, orient="row", strict=True).cast(schema)


def load_side(
    path: str, sheet: str | None, side: str, delimiter: str | None = None
) -> SideTable:
    path = abs_path(path)
    if not Path(path).is_file():
        raise HardFail(f"Missing path: {path}")
    excel = is_excel_path(path)
    flag = "a" if side == "A" else "b"
    if excel and delimiter is not None:
        raise HardFail(f"--{flag}-delim was given for Excel file {path}")
    if excel and not sheet:
        raise HardFail(f"--{flag}-sheet is required for Excel file {path}")
    if not excel and sheet:
        raise HardFail(
            f"Sheet {sheet!r} was given for non-Excel file {path}"
        )
    if excel:
        headers, rows = load_excel(path, sheet)
        detection = None
    else:
        parsed = load_delimited(path, delimiter=delimiter)
        headers, rows, detection = parsed.headers, parsed.rows, parsed.detection
    _check_unique_headers(headers, side)
    frame = _frame_from_rows(headers, rows)
    return SideTable(
        path=path,
        sheet=sheet,
        headers=headers,
        frame=frame,
        detection=detection,
    )
