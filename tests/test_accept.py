from pathlib import Path

import pytest

from reconcile.engine import Engine, InTuiError
from tests.xlsxutil import write_csv


def test_accept_cell_and_undo(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n2,N\n")
    write_csv(pb, "id,val\n1,Yes\n2,No\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"])
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
    eng = Engine.from_paths(str(pa), str(pb), ["id"])
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
    eng = Engine.from_paths(str(pa), str(pb), ["id"])
    n = eng.accept_pair("val", "Y", "Yes")
    assert n == 2
    assert eng.pending_cells_n() == 1


def test_unmatched_accept_and_refresh_row_change(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,a\n9,z\n")
    write_csv(pb, "id,val\n1,a\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"])
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
    eng = Engine.from_paths(str(pa), str(pb), ["id"])
    eng.accept_unmatched("A", ("9",))
    write_csv(pb, "id,val\n1,a\n9,z\n")
    eng.refresh()
    assert eng.pending_a_only_n() == 0
    assert eng.a_only.height == 0


def test_extra_accept_and_drops_when_both_sides(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val,cust\n1,a,1\n")
    write_csv(pb, "id,val\n1,a\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"])
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
    eng = Engine.from_paths(str(pa), str(pb), ["id"])
    eng.accept_column("val")
    write_csv(pa, "id,val\n1,Y2\n")
    delta = eng.refresh()
    assert eng.pending_cells_n() == 1
    assert delta.returned == 1


def test_equal_after_refresh_drops(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"])
    eng.accept_column("val")
    write_csv(pb, "id,val\n1,Y\n")
    eng.refresh()
    assert eng.pending_cells_n() == 0
    assert eng.accepted_cells.height == 0


def test_regex_draft_pending_only(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag,ok\n1,Y,1,a\n")
    write_csv(pb, "id,Status,Flag,ok\n1,Yes,2,a\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"])
    n = eng.start_regex_draft("(?i)stat|flag")
    assert n == 2
    assert "ok" not in eng.column_draft
    eng.confirm_column_draft()
    assert eng.pending_cells_n() == 0


def test_polars_rejects_other_names(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,a\n")
    write_csv(pb, "id,val\n1,b\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"])
    with pytest.raises(InTuiError, match="only use pl"):
        eng.start_polars_draft("A", '(pl.col("a") == "x").all()')


def test_polars_selector_one_side(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,——\n2,——\n")
    write_csv(pb, "id,val\n1,x\n2,y\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"])
    n = eng.start_polars_draft("A", '(pl.col("s") == "——").all()')
    assert n == 1
    eng.cancel_drafts()
    n = eng.start_polars_draft("B", '(pl.col("s") == "——").all()')
    assert n == 0


def test_refresh_error_keeps_last_state(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"])
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
    eng = Engine.from_paths(str(pa), str(pb), ["id"])
    eng.accept_column("Status")
    place = eng.next_lever_place(eng.place)
    # Flag still has pending; next lever is that pair list
    assert place.screen == "pair_list"
    assert place.column == "Flag"


def test_session_place_restored(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"])
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
    eng = Engine.from_paths(str(pa), str(pb), ["id"])
    eng.accept_column("val")
    z = tmp_path / "job.recon.zip"
    eng.export_zip(str(z))
    loaded = Engine.from_session(str(z))
    assert loaded.pending_total() == 0
    assert loaded.keys == ["id"]
    assert loaded.a.path == eng.a.path
