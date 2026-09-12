from pathlib import Path

import polars as pl
import pytest

from reconcile.engine import Engine
from reconcile.errors import HardFail
from reconcile.excel import load_excel
from tests.xlsxutil import write_xlsx


def test_excel_text_roundtrip(tmp_path: Path):
    p = tmp_path / "a.xlsx"
    write_xlsx(p, "Sheet1", [["id", "val"], ["1", "Y"], ["2", ""]])
    df = load_excel(p, "Sheet1")
    assert list(df.columns) == ["id", "val"]
    recs = df.to_dicts()
    assert {"id": "1", "val": "Y"} in recs
    assert {"id": "2", "val": ""} in recs


def test_excel_numeric_cells_load_as_strings(tmp_path: Path):
    p = tmp_path / "a.xlsx"
    write_xlsx(
        p,
        "Sheet1",
        [["id", "Age"], ["1", "12"]],
        number_cells={(1, 1)},
    )
    df = load_excel(p, "Sheet1")
    recs = df.to_dicts()
    assert recs == [{"id": "1", "Age": "12"}]
    assert all(dt in (pl.Utf8, pl.String) for dt in df.dtypes)


def test_excel_formula_loads_cached_value(tmp_path: Path):
    p = tmp_path / "a.xlsx"
    write_xlsx(
        p,
        "Sheet1",
        [["id", "val"], ["1", "cached-42"]],
        formula_cells={(1, 1)},
    )
    df = load_excel(p, "Sheet1")
    assert df.to_dicts() == [{"id": "1", "val": "cached-42"}]


def test_excel_merges_load_without_fail(tmp_path: Path):
    p = tmp_path / "a.xlsx"
    write_xlsx(
        p,
        "Sheet1",
        [["id", "val"], ["hello", ""]],
        merges=["A2:B2"],
    )
    df = load_excel(p, "Sheet1")
    recs = df.to_dicts()
    assert recs[0]["id"] == "hello"
    assert recs[0]["val"] == ""


def test_excel_missing_sheet(tmp_path: Path):
    p = tmp_path / "a.xlsx"
    write_xlsx(p, "Sheet1", [["id", "val"], ["1", "a"]])
    with pytest.raises(HardFail, match="Missing sheet 'Nope'") as ei:
        load_excel(p, "Nope")
    msg = ei.value.message
    assert str(p.resolve()) in msg
    assert "Sheet1" in msg


def test_excel_delim_flag_illegal(tmp_path: Path):
    from reconcile.load import load_side

    p = tmp_path / "a.xlsx"
    write_xlsx(p, "Sheet1", [["id", "val"], ["1", "a"]])
    with pytest.raises(HardFail, match="--a-delim was given for Excel"):
        load_side(str(p), "Sheet1", "A", delimiter=",")


def test_excel_compare(tmp_path: Path):
    a = tmp_path / "a.xlsx"
    b = tmp_path / "b.xlsx"
    write_xlsx(a, "L", [["id", "val"], ["1", "Y"]])
    write_xlsx(b, "Sheet1", [["id", "val"], ["1", "Yes"]])
    eng = Engine.from_paths(str(a), str(b), ["id"], "L", "Sheet1")
    assert eng.pending_cells_n() == 1
    assert eng.a.sheet == "L"
