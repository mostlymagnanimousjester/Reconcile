from pathlib import Path

import pytest

from reconcile.engine import Engine
from reconcile.errors import HardFail
from reconcile.excel import load_excel
from tests.xlsxutil import write_xlsx


def test_excel_text_roundtrip(tmp_path: Path):
    p = tmp_path / "a.xlsx"
    write_xlsx(p, "Sheet1", [["id", "val"], ["1", "Y"], ["2", ""]])
    headers, rows = load_excel(p, "Sheet1")
    assert headers == ["id", "val"]
    assert ["1", "Y"] in rows
    # empty cell → "" and all-empty rows dropped (row 3 not present)


def test_excel_non_text_hard_fail(tmp_path: Path):
    p = tmp_path / "a.xlsx"
    write_xlsx(
        p,
        "Sheet1",
        [["id", "Age"], ["1", "12"]],
        number_cells={(1, 1)},
    )
    with pytest.raises(HardFail, match="Non-text Excel"):
        load_excel(p, "Sheet1")


def test_excel_formula_hard_fail(tmp_path: Path):
    p = tmp_path / "a.xlsx"
    write_xlsx(
        p,
        "Sheet1",
        [["id", "val"], ["1", "a"]],
        formula_cells={(1, 1)},
    )
    with pytest.raises(HardFail, match="formula"):
        load_excel(p, "Sheet1")


def test_excel_merged_hard_fail(tmp_path: Path):
    p = tmp_path / "a.xlsx"
    write_xlsx(p, "Sheet1", [["id", "val"], ["1", "a"]], merges=["A1:B1"])
    with pytest.raises(HardFail, match="Merged cells"):
        load_excel(p, "Sheet1")


def test_excel_missing_sheet(tmp_path: Path):
    p = tmp_path / "a.xlsx"
    write_xlsx(p, "Sheet1", [["id", "val"], ["1", "a"]])
    with pytest.raises(HardFail, match="Missing sheet 'Nope'"):
        load_excel(p, "Nope")


def test_excel_compare(tmp_path: Path):
    a = tmp_path / "a.xlsx"
    b = tmp_path / "b.xlsx"
    write_xlsx(a, "L", [["id", "val"], ["1", "Y"]])
    write_xlsx(b, "Sheet1", [["id", "val"], ["1", "Yes"]])
    eng = Engine.from_paths(str(a), str(b), ["id"], "L", "Sheet1")
    assert eng.pending_cells_n() == 1
    assert eng.a.sheet == "L"
