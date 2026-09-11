from pathlib import Path

import pytest

from reconcile.engine import Engine
from reconcile.errors import HardFail
from tests.xlsxutil import write_csv


def _pair(tmp: Path, a: str, b: str, keys: str = "id") -> Engine:
    pa = tmp / "a.csv"
    pb = tmp / "b.csv"
    write_csv(pa, a)
    write_csv(pb, b)
    return Engine.from_paths(
        str(pa), str(pb), keys.split(","), a_delim=",", b_delim=","
    )


def test_exact_mismatch_and_null_cast(tmp_path: Path):
    eng = _pair(
        tmp_path,
        "id,val\n1,Y\n2,\n",
        "id,val\n1,Yes\n2,\n",
    )
    assert eng.pending_total() == 1
    assert eng.pending_cells_n() == 1
    rec = eng.pending_cells.to_dicts()[0]
    assert rec["val_a"] == "Y"
    assert rec["val_b"] == "Yes"


def test_no_trim_or_casefold(tmp_path: Path):
    eng = _pair(tmp_path, "id,val\n1, Y \n", "id,val\n1,y\n")
    assert eng.pending_cells_n() == 1


def test_literal_null_is_value(tmp_path: Path):
    eng = _pair(tmp_path, "id,val\n1,null\n", "id,val\n1,NULL\n")
    assert eng.pending_cells_n() == 1


def test_key_columns_not_value_diffs(tmp_path: Path):
    eng = _pair(tmp_path, "id,val\n1,a\n", "id,val\n1,a\n")
    assert eng.pending_total() == 0
    assert "id" not in eng.comparable


def test_a_only_and_b_only(tmp_path: Path):
    eng = _pair(tmp_path, "id,val\n1,a\n2,b\n", "id,val\n1,a\n3,c\n")
    assert eng.pending_a_only_n() == 1
    assert eng.pending_b_only_n() == 1
    assert eng.pending_total() == 2


def test_extras_are_pending(tmp_path: Path):
    eng = _pair(tmp_path, "id,val,cust_id\n1,a,9\n", "id,val,customer_id\n1,a,9\n")
    assert eng.pending_extras_n() == 2
    names = {(s, n) for s, n in eng.pending_extras}
    assert ("A", "cust_id") in names
    assert ("B", "customer_id") in names


def test_delimited_duplicate_header_is_extra_not_hard_fail(tmp_path: Path):
    pa = tmp_path / "a.csv"
    pb = tmp_path / "b.csv"
    write_csv(pa, "id,val,val\n1,a,b\n")
    write_csv(pb, "id,val\n1,a\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    assert any("duplicated" in h for h in eng.a.headers)
    assert ("A", "val_duplicated_0") in eng.pending_extras or any(
        "duplicated" in n for s, n in eng.pending_extras if s == "A"
    )


def test_missing_key_column(tmp_path: Path):
    pa = tmp_path / "a.csv"
    pb = tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,a\n")
    write_csv(pb, "idx,val\n1,a\n")
    with pytest.raises(HardFail, match="Missing key column 'id' on side B"):
        Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")


def test_duplicate_keys(tmp_path: Path):
    pa = tmp_path / "a.csv"
    pb = tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,a\n1,b\n")
    write_csv(pb, "id,val\n1,a\n")
    with pytest.raises(HardFail, match=r"Duplicate key on side A: \('1',\) occurs 2"):
        Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")


def test_empty_key_legal_but_duplicate_empty_fails(tmp_path: Path):
    pa = tmp_path / "a.csv"
    pb = tmp_path / "b.csv"
    write_csv(pa, "id,val\n,a\n")
    write_csv(pb, "id,val\n,a\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    assert eng.matched_key_count() == 1
    write_csv(pa, "id,val\n,a\n,b\n")
    with pytest.raises(HardFail, match="Duplicate key on side A"):
        Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")


def test_composite_keys(tmp_path: Path):
    eng = _pair(
        tmp_path,
        "id,year,val\n1,2020,a\n1,2021,b\n",
        "id,year,val\n1,2020,a\n1,2021,c\n",
        keys="id,year",
    )
    assert eng.pending_cells_n() == 1


def test_all_empty_row_dropped_before_dup_key(tmp_path: Path):
    # trailing empty row must not become duplicate empty keys
    pa = tmp_path / "a.csv"
    pb = tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,a\n,\n")
    write_csv(pb, "id,val\n1,a\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    assert eng.pending_total() == 0


def test_roster_sort_pending_then_concentration_then_name(tmp_path: Path):
    eng = _pair(
        tmp_path,
        "id,Status,Flag\n1,Y,1\n2,Y,1\n3,Y,1\n4,N,x\n",
        "id,Status,Flag\n1,Yes,1\n2,Yes,1\n3,Yes,1\n4,No,y\n",
    )
    rows = eng.roster()
    names = [r.name for r in rows if r.kind == "column"]
    assert names[0] == "Status"
    assert names[1] == "Flag"


def test_id_vs_id_space_not_paired(tmp_path: Path):
    pa = tmp_path / "a.csv"
    pb = tmp_path / "b.csv"
    write_csv(pa, "ID,val\n1,a\n")
    write_csv(pb, "id,val\n1,a\n")
    with pytest.raises(HardFail, match="Missing key column"):
        Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
