from pathlib import Path

import pytest

from reconcile.engine import Engine, InTuiError, Place
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
    with pytest.raises(InTuiError, match="0 pending"):
        eng.start_regex_draft("^NA$")
    assert not eng.draft_in_flight()
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
    with pytest.raises(InTuiError, match="0 pending"):
        eng.start_sentinel_draft("B", "——")
    assert not eng.draft_in_flight()


def test_sentinel_drafts_columns_where_all_pending_b_equal(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,all_b,mixed\n1,x,p\n2,y,q\n")
    write_csv(pb, "id,all_b,mixed\n1,——,——\n2,——,z\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    n = eng.start_sentinel_draft("B", "——")
    assert n == 1
    assert eng.column_draft == {"all_b"}
    eng.cancel_drafts()
    with pytest.raises(InTuiError, match="0 pending"):
        eng.start_sentinel_draft("A", "——")
    assert not eng.draft_in_flight()


def test_sentinel_excludes_mixed_pending_values(tmp_path: Path):
    eng = _sentinel_fixture(tmp_path)
    n = eng.start_sentinel_draft("A", "——")
    assert "mixed" not in eng.column_draft
    assert n == 1


def test_sentinel_excludes_zero_pending_columns(tmp_path: Path):
    eng = _sentinel_fixture(tmp_path)
    with pytest.raises(InTuiError, match="0 pending"):
        eng.start_sentinel_draft("A", "same")
    assert not eng.draft_in_flight()
    n = eng.start_sentinel_draft("A", "——")
    assert "ok" not in eng.column_draft
    assert n == 1


def test_sentinel_empty_string_is_legal(tmp_path: Path):
    eng = _sentinel_fixture(tmp_path)
    n = eng.start_sentinel_draft("A", "")
    assert n == 1
    assert eng.column_draft == {"blank_a"}
    eng.cancel_drafts()
    with pytest.raises(InTuiError, match="0 pending"):
        eng.start_sentinel_draft("A", "   ")
    assert not eng.draft_in_flight()
    write_csv(tmp_path / "a.csv", "id,pad\n1, x\n")
    write_csv(tmp_path / "b.csv", "id,pad\n1,y\n")
    eng = Engine.from_paths(
        str(tmp_path / "a.csv"), str(tmp_path / "b.csv"), ["id"], a_delim=",", b_delim=","
    )
    n = eng.start_sentinel_draft("A", " x")
    assert n == 1
    eng.cancel_drafts()
    with pytest.raises(InTuiError, match="0 pending"):
        eng.start_sentinel_draft("A", "x")
    assert not eng.draft_in_flight()


def test_equals_no_longer_evals_polars(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,——\n")
    write_csv(pb, "id,val\n1,x\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    expr = '(pl.col("s") == "——").all()'
    with pytest.raises(InTuiError, match="0 pending"):
        eng.start_sentinel_draft("A", expr)
    assert not eng.draft_in_flight()
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


def test_y_confirms_live_draft_xor(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag\n1,Y,1\n2,Y,2\n")
    write_csv(pb, "id,Status,Flag\n1,Yes,9\n2,Yes,8\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    eng.start_regex_draft("Status|Flag")
    assert eng.column_draft == {"Status", "Flag"}
    with pytest.raises(InTuiError, match="column draft"):
        eng.start_pair_draft("Status", "Y", "Yes")
    n = eng.confirm_column_draft()
    assert n == 4
    assert not eng.draft_in_flight()
    assert eng.pending_cells_n() == 0
    write_csv(pa, "id,Status,Flag\n1,Y,1\n2,Y,2\n")
    write_csv(pb, "id,Status,Flag\n1,Yes,9\n2,Yes,8\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    n = eng.start_pair_draft("Status", "Y", "Yes")
    assert n == 2
    assert not eng.column_draft
    eng.confirm_pair_draft()
    flag = next(r for r in eng.roster() if r.name == "Flag")
    assert flag.pending == 2
    assert not eng.draft_in_flight()


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
    place = eng.next_lever_place(Place())
    # Flag still has pending; next lever is that pair list
    assert place.screen == "pair_list"
    assert place.column == "Flag"


def test_next_lever_ignores_roster_filter(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag\n1,Y,1\n2,Y,1\n")
    write_csv(pb, "id,Status,Flag\n1,Yes,2\n2,Yes,1\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    eng.accept_column("Flag")
    place = eng.next_lever_place(Place(column="Flag", roster_filter="Flag"))
    assert place.screen == "pair_list"
    assert place.column == "Status"
    assert place.roster_filter == ""


def test_page_index_for_key_polars(tmp_path: Path):
    from reconcile.pages import PAGE_SIZE, page_index_for_key

    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n" + "".join(f"{i:03d},Y\n" for i in range(120)))
    write_csv(pb, "id,val\n" + "".join(f"{i:03d},Yes\n" for i in range(120)))
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    frame = eng.pending_cells.sort(eng.keys)
    page, row = page_index_for_key(frame, eng.keys, ("099",))
    assert page == 0
    assert row == 99
    page, row = page_index_for_key(frame, eng.keys, ("100",))
    assert page == 1
    assert row == 0
    assert PAGE_SIZE == 100
    page, row = eng.page_index_for_key(frame, ("000",))
    assert (page, row) == (0, 0)


def test_context_columns_attached_after_slice(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(
        pa,
        "id,val,other\n" + "".join(f"{i:03d},Y,A{i:03d}\n" for i in range(120)),
    )
    write_csv(
        pb,
        "id,val,other\n" + "".join(f"{i:03d},Yes,B{i:03d}\n" for i in range(120)),
    )
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    eng.context_columns["val"] = ["other"]
    recs, page, pages = eng.pair_cells_page("val", "Y", "Yes", 1)
    assert page == 1
    assert pages == 2
    assert len(recs) == 20
    assert recs[0]["other__ctx_a"] == "A100"
    assert recs[0]["other__ctx_b"] == "B100"
    recs0, _, _ = eng.cells_for_tab("val", "pending", 0)
    assert recs0[0]["other__ctx_a"] == "A000"


def test_pair_returned_is_per_pair_not_whole_column(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n2,Y\n3,N\n")
    write_csv(pb, "id,val\n1,Yes\n2,Yes\n3,No\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    eng.accept_pair("val", "Y", "Yes")
    write_csv(pa, "id,val\n1,Y2\n2,Y2\n3,N\n")
    write_csv(pb, "id,val\n1,Yes2\n2,Yes2\n3,No\n")
    eng.refresh()
    assert eng.pair_has_returned("val", "Y2", "Yes2")
    assert not eng.pair_has_returned("val", "N", "No")
    assert eng.column_has_returned("val")


def test_accept_column_refused_during_pair_draft(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n2,N\n")
    write_csv(pb, "id,val\n1,Yes\n2,No\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    n = eng.start_pair_draft("val", "Y", "Yes")
    assert n == 1
    pending = eng.pending_cells_n()
    with pytest.raises(InTuiError, match="pair draft"):
        eng.accept_column("val")
    assert eng.pair_draft_col == "val"
    assert eng.pending_cells_n() == pending


def test_confirm_pair_draft_all_unchecked_stays(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n2,Y\n")
    write_csv(pb, "id,val\n1,Yes\n2,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    eng.start_pair_draft("val", "Y", "Yes")
    pending = eng.pending_cells_n()
    with pytest.raises(InTuiError, match="all unchecked"):
        eng.confirm_pair_draft({("1",), ("2",)})
    assert eng.pair_draft_col == "val"
    assert eng.pending_cells_n() == pending


def test_undo_last_grain_after_pair_accept(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag\n1,Y,1\n")
    write_csv(pb, "id,Status,Flag\n1,Yes,2\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    n = eng.accept_pair("Status", "Y", "Yes")
    eng.remember_grain(("pair", "Status", "Y", "Yes"), n)
    place = eng.next_lever_place(Place(column="Status"))
    assert place.column == "Flag"
    assert next(r for r in eng.roster() if r.name == "Status").pending == 0
    undone = eng.undo_last_grain()
    assert undone == 1
    assert eng.last_grain is None
    assert next(r for r in eng.roster() if r.name == "Status").pending == 1


def test_refresh_clears_stale_last_grain(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    n = eng.accept_cell(("1",), "val", "Y", "Yes")
    eng.remember_grain(("cell", ("1",), "val"), n)
    assert eng.last_grain is not None
    write_csv(pa, "id,val\n1,Y2\n")
    write_csv(pb, "id,val\n1,Yes2\n")
    eng.refresh()
    assert eng.last_grain is None
    pending_before = eng.pending_cells_n()
    undone = eng.undo_last_grain()
    assert undone == 0
    assert eng.pending_cells_n() == pending_before


def test_accepted_column_hidden_from_visible_roster(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag\n1,Y,1\n")
    write_csv(pb, "id,Status,Flag\n1,Yes,2\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    assert {r.name for r in eng.visible_column_roster()} == {"Status", "Flag"}
    eng.accept_column("Status")
    vis = eng.visible_column_roster()
    assert [r.name for r in vis] == ["Flag"]
    settled = next(r for r in eng.roster() if r.name == "Status")
    assert settled.pending == 0
    shown = eng.column_roster(include_settled=True)
    assert [r.name for r in shown] == ["Flag", "Status"]
    status = next(r for r in shown if r.name == "Status")
    assert status.pending == 0
    assert status.accepted > 0


def test_prune_place_maps_overview_screen_to_roster(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    restored = eng.prune_place(Place(screen="overview", roster_filter="val"))
    assert restored.screen == "roster"
    assert restored.roster_filter == "val"
