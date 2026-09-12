from pathlib import Path

import pytest

from reconcile.engine import Engine, InTuiError
from tests.xlsxutil import write_csv


def test_accept_cell_and_undo(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n2,N\n")
    write_csv(pb, "id,val\n1,Yes\n2,No\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    assert eng.pending_cells_n() == 2
    rec = eng.pending_cells.sort("id").to_dicts()[0]
    eng.accept_cell(eng.key_of(rec), "val", rec["val_a"], rec["val_b"])
    assert eng.pending_cells_n() == 1
    eng.undo_cell(("1",), "val")
    assert eng.pending_cells_n() == 2


def test_accept_column_is_snapshot_not_standing_ignore(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    eng.accept_column("val")
    assert eng.pending_cells_n() == 0
    write_csv(pa, "id,val\n1,Y\n2,N\n")
    write_csv(pb, "id,val\n1,Yes\n2,No\n")
    eng.refresh()
    # original pair still accepted; new mismatch pending
    assert eng.pending_cells_n() == 1
    rec = eng.pending_cells.to_dicts()[0]
    assert rec["id"] == "2"


def test_accept_pair(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n2,Y\n3,N\n")
    write_csv(pb, "id,val\n1,Yes\n2,Yes\n3,No\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    n = eng.accept_pair("val", "Y", "Yes")
    assert n == 2
    assert eng.pending_cells_n() == 1


def test_unmatched_accept_and_refresh_row_change(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,a\n9,z\n")
    write_csv(pb, "id,val\n1,a\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    assert eng.pending_a_only_n() == 1
    eng.accept_unmatched("A", ("9",))
    assert eng.pending_a_only_n() == 0
    write_csv(pa, "id,val\n1,a\n9,CHANGED\n")
    eng.refresh()
    assert eng.pending_a_only_n() == 1  # row text changed


def test_unmatched_drops_when_now_matched(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,a\n9,z\n")
    write_csv(pb, "id,val\n1,a\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    eng.accept_unmatched("A", ("9",))
    write_csv(pb, "id,val\n1,a\n9,z\n")
    eng.refresh()
    assert eng.pending_a_only_n() == 0
    assert eng.a_only.height == 0


def test_extra_accept_and_drops_when_both_sides(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val,cust\n1,a,1\n")
    write_csv(pb, "id,val\n1,a\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    assert eng.pending_extras_n() == 1
    eng.accept_extra("A", "cust")
    assert eng.pending_extras_n() == 0
    write_csv(pb, "id,val,cust\n1,a,1\n")
    eng.refresh()
    assert eng.pending_extras_n() == 0
    assert not eng.extras_a and not eng.extras_b


def test_cell_changes_return_to_pending(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    eng.accept_column("val")
    write_csv(pa, "id,val\n1,Y2\n")
    delta = eng.refresh()
    assert eng.pending_cells_n() == 1
    assert delta.returned == 1


def test_equal_after_refresh_drops(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    eng.accept_column("val")
    write_csv(pb, "id,val\n1,Y\n")
    eng.refresh()
    assert eng.pending_cells_n() == 0
    assert eng.accepted_cells.height == 0


def test_regex_draft_pending_only(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag,ok\n1,Y,1,a\n")
    write_csv(pb, "id,Status,Flag,ok\n1,Yes,2,a\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    n = eng.start_regex_draft("(?i)stat|flag")
    assert n == 2
    assert eng.column_draft == {"Status", "Flag"}
    assert "ok" not in eng.column_draft
    eng.confirm_column_draft()
    assert eng.pending_cells_n() == 0


def test_regex_draft_is_name_based_not_values(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,alpha,beta\n1,NA,x\n")
    write_csv(pb, "id,alpha,beta\n1,y,NA\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    n = eng.start_regex_draft("^NA$")
    assert n == 0
    assert eng.column_draft == set()
    n = eng.start_regex_draft("alp")
    assert n == 1
    assert eng.column_draft == {"alpha"}


def _sentinel_fixture(tmp_path: Path) -> Engine:
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(
        pa,
        "id,all_a,mixed,ok,blank_a\n1,——,——,same,\n2,——,x,same,\n",
    )
    write_csv(
        pb,
        "id,all_a,mixed,ok,blank_a\n1,x,y,same,x\n2,y,z,same,y\n",
    )
    return Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")


def test_sentinel_drafts_columns_where_all_pending_a_equal(tmp_path: Path):
    eng = _sentinel_fixture(tmp_path)
    n = eng.start_sentinel_draft("A", "——")
    assert n == 1
    assert eng.column_draft == {"all_a"}
    eng.cancel_drafts()
    n = eng.start_sentinel_draft("B", "——")
    assert n == 0
    assert eng.column_draft == set()


def test_sentinel_drafts_columns_where_all_pending_b_equal(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,all_b,mixed\n1,x,p\n2,y,q\n")
    write_csv(pb, "id,all_b,mixed\n1,——,——\n2,——,z\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    n = eng.start_sentinel_draft("B", "——")
    assert n == 1
    assert eng.column_draft == {"all_b"}
    eng.cancel_drafts()
    n = eng.start_sentinel_draft("A", "——")
    assert n == 0


def test_sentinel_excludes_mixed_pending_values(tmp_path: Path):
    eng = _sentinel_fixture(tmp_path)
    n = eng.start_sentinel_draft("A", "——")
    assert "mixed" not in eng.column_draft
    assert n == 1


def test_sentinel_excludes_zero_pending_columns(tmp_path: Path):
    eng = _sentinel_fixture(tmp_path)
    n = eng.start_sentinel_draft("A", "same")
    assert n == 0
    assert "ok" not in eng.column_draft
    n = eng.start_sentinel_draft("A", "——")
    assert "ok" not in eng.column_draft
    assert n == 1


def test_sentinel_empty_string_is_legal(tmp_path: Path):
    eng = _sentinel_fixture(tmp_path)
    n = eng.start_sentinel_draft("A", "")
    assert n == 1
    assert eng.column_draft == {"blank_a"}
    eng.cancel_drafts()
    n = eng.start_sentinel_draft("A", "   ")
    assert n == 0
    write_csv(tmp_path / "a.csv", "id,pad\n1, x\n")
    write_csv(tmp_path / "b.csv", "id,pad\n1,y\n")
    eng = Engine.from_paths(
        str(tmp_path / "a.csv"), str(tmp_path / "b.csv"), ["id"], a_delim=",", b_delim=","
    )
    n = eng.start_sentinel_draft("A", " x")
    assert n == 1
    eng.cancel_drafts()
    n = eng.start_sentinel_draft("A", "x")
    assert n == 0


def test_equals_no_longer_evals_polars(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,——\n")
    write_csv(pb, "id,val\n1,x\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    expr = '(pl.col("s") == "——").all()'
    n = eng.start_sentinel_draft("A", expr)
    assert n == 0
    assert eng.column_draft == set()
    n = eng.start_sentinel_draft("A", "——")
    assert n == 1
    assert eng.column_draft == {"val"}
    assert not hasattr(eng, "start_polars_draft")
    assert not hasattr(eng, "_assert_safe_polars")


def test_sentinel_refuses_without_side_and_leaves_draft(tmp_path: Path):
    eng = _sentinel_fixture(tmp_path)
    with pytest.raises(InTuiError, match="Side A or Side B"):
        eng.start_sentinel_draft("", "——")
    assert not eng.draft_in_flight()
    eng.start_sentinel_draft("A", "——")
    assert eng.column_draft == {"all_a"}
    with pytest.raises(InTuiError, match="confirm or cancel"):
        eng.start_sentinel_draft("A", "")
    assert eng.column_draft == {"all_a"}


def test_sentinel_xor_with_regex_and_pair_draft(tmp_path: Path):
    eng = _sentinel_fixture(tmp_path)
    eng.start_regex_draft("all_a")
    with pytest.raises(InTuiError, match="confirm or cancel"):
        eng.start_sentinel_draft("A", "——")
    eng.cancel_drafts()
    eng.start_sentinel_draft("A", "——")
    with pytest.raises(InTuiError, match="confirm or cancel"):
        eng.start_regex_draft("mixed")
    eng.cancel_drafts()
    n = eng.start_pair_draft("all_a", "——", "x")
    assert n == 1
    with pytest.raises(InTuiError, match="confirm or cancel"):
        eng.start_sentinel_draft("A", "——")


def test_refresh_error_keeps_last_state(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    eng.accept_column("val")
    assert eng.pending_total() == 0
    write_csv(pa, "THIS IS NOT,A,VALID\n1\n")
    with pytest.raises(InTuiError, match="ERROR"):
        eng.refresh()
    assert eng.pending_total() == 0
    assert eng.accepted_cells.height == 1


def test_next_lever_after_column_accept(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag\n1,Y,1\n2,Y,1\n")
    write_csv(pb, "id,Status,Flag\n1,Yes,2\n2,Yes,1\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    eng.accept_column("Status")
    place = eng.next_lever_place(eng.place)
    # Flag still has pending; next lever is that pair list
    assert place.screen == "pair_list"
    assert place.column == "Flag"


def test_session_place_restored(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    eng.place.screen = "pair_list"
    eng.place.column = "val"
    eng.place.roster_filter = "val"
    z = tmp_path / "job.recon.zip"
    eng.export_zip(str(z))
    loaded = Engine.from_session(str(z))
    assert loaded.place.column == "val"
    assert loaded.place.roster_filter == "val"


def test_session_zip_roundtrip(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    eng.accept_column("val")
    z = tmp_path / "job.recon.zip"
    eng.export_zip(str(z))
    loaded = Engine.from_session(str(z))
    assert loaded.pending_total() == 0
    assert loaded.keys == ["id"]
    assert loaded.a.path == eng.a.path
    assert Path(loaded.a.path).is_absolute()
    assert Path(loaded.b.path).is_absolute()


def test_session_zip_from_relative_paths_is_absolute(tmp_path: Path, monkeypatch):
    import json
    import zipfile

    write_csv(tmp_path / "a.csv", "id,val\n1,Y\n")
    write_csv(tmp_path / "b.csv", "id,val\n1,Yes\n")
    monkeypatch.chdir(tmp_path)
    eng = Engine.from_paths("a.csv", "b.csv", ["id"], a_delim=",", b_delim=",")
    z = Path("job.recon.zip")
    eng.export_zip(str(z))
    with zipfile.ZipFile(z) as zf:
        man = json.loads(zf.read("manifest.json"))
    assert Path(man["a_path"]).is_absolute()
    assert Path(man["b_path"]).is_absolute()
    assert man["a_path"] == str((tmp_path / "a.csv").resolve())
    loaded = Engine.from_session("job.recon.zip")
    assert loaded.a.path == man["a_path"]
    assert loaded.b.path == man["b_path"]
