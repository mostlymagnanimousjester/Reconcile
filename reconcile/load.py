"""Load one side (delimited or Excel) into a string Polars frame."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import polars as pl

from reconcile.delimited import (
    DEFAULT_ENCODING,
    Detection,
    abs_path,
    load_delimited,
)
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


def load_side(
    path: str,
    sheet: str | None,
    side: str,
    delimiter: str | None = None,
    encoding: str | None = None,
) -> SideTable:
    path = abs_path(path)
    excel = is_excel_path(path)
    flag = "a" if side == "A" else "b"
    if excel and delimiter is not None:
        raise HardFail(f"--{flag}-delim was given for Excel file {path}")
    if excel and encoding is not None:
        raise HardFail(f"--{flag}-encoding was given for Excel file {path}")
    if not excel and delimiter is None:
        raise HardFail(f"--{flag}-delim is required for delimited file {path}")
    if not excel and encoding is None:
        encoding = DEFAULT_ENCODING
    if excel and not sheet:
        raise HardFail(f"--{flag}-sheet is required for Excel file {path}")
    if not excel and sheet:
        raise HardFail(
            f"Sheet {sheet!r} was given for non-Excel file {path}"
        )
    if not Path(path).is_file():
        raise HardFail(f"Missing path: {path}")
    if excel:
        frame = load_excel(path, sheet)
        detection = None
        _check_unique_headers(list(frame.columns), side)
    else:
        parsed = load_delimited(
            path, delimiter, encoding=encoding, side=side
        )
        frame = parsed.frame
        detection = parsed.detection
    headers = list(frame.columns)
    return SideTable(
        path=path,
        sheet=sheet,
        headers=headers,
        frame=frame,
        detection=detection,
    )
