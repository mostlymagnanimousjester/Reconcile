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


def test_roster_shared_columns_follow_table_a_import_order(tmp_path: Path):
    """A import order wins even when a later column has more pending."""
    eng = _pair(
        tmp_path,
        "id,Status,Flag\n1,Y,1\n2,Y,2\n3,Y,3\n4,N,4\n",
        "id,Status,Flag\n1,Yes,9\n2,Y,8\n3,Y,7\n4,N,6\n",
    )
    # Status: 1 pending; Flag: 4 pending. Old pending-desc sort would put Flag first.
    rows = eng.roster()
    names = [r.name for r in rows if r.kind == "column"]
    assert names == ["Status", "Flag"]
    vis = eng.visible_column_roster()
    assert [r.name for r in vis] == ["Status", "Flag"]
    assert eng.comparable == ["Status", "Flag"]


def test_id_vs_id_space_not_paired(tmp_path: Path):
    pa = tmp_path / "a.csv"
    pb = tmp_path / "b.csv"
    write_csv(pa, "ID,val\n1,a\n")
    write_csv(pb, "id,val\n1,a\n")
    with pytest.raises(HardFail, match="Missing key column"):
        Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")


def test_equal_shared_rows_auto_hide_column_despite_unmatched(tmp_path: Path):
    """A-only / B-only keys must not keep an all-equal shared column on the roster."""
    eng = _pair(
        tmp_path,
        "id,val,Status\n1,same,Y\n2,onlyA,Y\n",
        "id,val,Status\n1,same,Yes\n3,onlyB,Yes\n",
    )
    assert eng.pending_a_only_n() == 1
    assert eng.pending_b_only_n() == 1
    assert eng.pending_cells_n() == 1
    vis = eng.visible_column_roster()
    names = [r.name for r in vis]
    assert "val" not in names
    assert "Status" in names
    assert all(r.kind == "column" for r in vis)
    assert all(r.pending > 0 for r in vis)
    full_kinds = {r.kind for r in eng.roster()}
    assert "A-only" in full_kinds
    assert "B-only" in full_kinds


def test_sole_key_column_named_underscore_key(tmp_path: Path):
    eng = _pair(
        tmp_path,
        "_key,val\n1,a\n2,c\n",
        "_key,val\n1,b\n3,c\n",
        keys="_key",
    )
    assert eng.pending_total() != 0
    assert eng.pending_cells_n() == 1
    rec = eng.pending_cells.to_dicts()[0]
    assert rec["_key"] == "1"
    assert rec["column"] == "val"
    assert rec["val_a"] == "a"
    assert rec["val_b"] == "b"
    assert eng.pending_a_only_n() == 1
    assert eng.pending_a_only["_key"].to_list() == ["2"]
    assert eng.pending_b_only_n() == 1
    assert eng.pending_b_only["_key"].to_list() == ["3"]
    assert eng.matched_key_count() == 1


def test_composite_key_including_underscore_key(tmp_path: Path):
    eng = _pair(
        tmp_path,
        "id,_key,val\n1,k,a\n2,k,c\n",
        "id,_key,val\n1,k,b\n3,k,c\n",
        keys="id,_key",
    )
    assert eng.pending_cells_n() == 1
    rec = eng.pending_cells.to_dicts()[0]
    assert rec["id"] == "1"
    assert rec["_key"] == "k"
    assert rec["column"] == "val"
    assert rec["val_a"] == "a"
    assert rec["val_b"] == "b"
    assert eng.pending_a_only.select("id", "_key").to_dicts() == [{"id": "2", "_key": "k"}]
    assert eng.pending_b_only.select("id", "_key").to_dicts() == [{"id": "3", "_key": "k"}]


def test_value_column_named_underscore_key(tmp_path: Path):
    eng = _pair(tmp_path, "id,_key\n1,a\n", "id,_key\n1,b\n")
    assert eng.pending_cells_n() == 1
    rec = eng.pending_cells.to_dicts()[0]
    assert rec["column"] == "_key"
    assert rec["val_a"] == "a"
    assert rec["val_b"] == "b"
    assert eng.pending_a_only_n() == 0
    assert eng.pending_b_only_n() == 0


def test_internal_join_name_extends_past_key_collision(tmp_path: Path):
    from reconcile.compare import _JOIN_STRUCT, _join_struct_name

    assert _join_struct_name(["id"]) == _JOIN_STRUCT
    extended = _JOIN_STRUCT + "_"
    assert _join_struct_name([_JOIN_STRUCT, extended]) == _JOIN_STRUCT + "__"
    eng = _pair(
        tmp_path,
        f"{_JOIN_STRUCT},val\n1,a\n2,c\n",
        f"{_JOIN_STRUCT},val\n1,b\n3,c\n",
        keys=_JOIN_STRUCT,
    )
    assert eng.pending_cells_n() == 1
    rec = eng.pending_cells.to_dicts()[0]
    assert rec[_JOIN_STRUCT] == "1"
    assert rec["val_a"] == "a"
    assert rec["val_b"] == "b"
    assert eng.pending_a_only[_JOIN_STRUCT].to_list() == ["2"]
    assert eng.pending_b_only[_JOIN_STRUCT].to_list() == ["3"]


def test_visible_column_roster_excludes_unmatched_and_extras(tmp_path: Path):
    eng = _pair(
        tmp_path,
        "id,val,cust\n1,a,1\n2,onlyA,2\n",
        "id,val\n1,b\n",
    )
    vis = eng.visible_column_roster()
    assert [r.name for r in vis] == ["val"]
    assert not any(r.kind in ("A-only", "B-only", "extra") for r in vis)
    assert any(r.kind == "A-only" for r in eng.roster())
    assert any(r.kind == "extra" and r.name == "cust" for r in eng.roster())
