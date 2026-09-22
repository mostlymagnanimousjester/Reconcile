"""Low-risk cache / deferral contracts. Compare and pairing stay exact."""

from __future__ import annotations

import asyncio
from pathlib import Path

import polars as pl

from reconcile.engine import Engine, Place
from reconcile.pages import (
    PAGE_SIZE,
    _all_matched_frame,
    _attach_pair_context_summaries,
    _equal_frame,
    _pair_cells_frame,
)
from tests.xlsxutil import write_csv


def _eng(tmp: Path, a: str, b: str, keys: str = "id") -> Engine:
    pa, pb = tmp / "a.csv", tmp / "b.csv"
    write_csv(pa, a)
    write_csv(pb, b)
    return Engine.from_paths(
        str(pa), str(pb), keys.split(","), a_delim=",", b_delim=","
    )


def test_pending_counts_after_accept_without_roster_paint(tmp_path: Path):
    eng = _eng(
        tmp_path,
        "id,Status,Flag\n1,Y,1\n",
        "id,Status,Flag\n1,Yes,2\n",
    )
    assert eng._roster_cache_valid is False
    assert eng._roster_cache == []
    assert eng.pending_columns_n() == 2
    assert eng._pending_by_col["Status"] == 1
    assert eng._pending_by_col["Flag"] == 1
    eng.accept_column("Status")
    assert eng._roster_cache_valid is False
    assert eng._roster_cache == []
    assert eng.pending_columns_n() == 1
    assert eng._pending_by_col.get("Status", 0) == 0
    assert eng._pending_by_col["Flag"] == 1
    place = eng.next_lever_place(Place(column="Status"))
    assert place.screen == "pair_list"
    assert place.column == "Flag"
    rows = eng.roster()
    assert eng._roster_cache_valid is True
    assert next(r for r in rows if r.name == "Status").pending == 0
    assert next(r for r in rows if r.name == "Flag").pending == 1


def test_refresh_returned_names_follow_a_import_order(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,note,val,zzz\n1,a,Y,1\n")
    write_csv(pb, "id,note,val,zzz\n1,b,Yes,2\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    assert eng.comparable == ["note", "val", "zzz"]
    eng.accept_column("note")
    eng.accept_column("val")
    eng.accept_column("zzz")
    write_csv(pb, "id,note,val,zzz\n1,b,Yep,9\n")
    delta = eng.refresh()
    tail = delta.message.split("returned", 1)[1]
    assert "val" in tail
    assert "zzz" in tail
    assert "note" not in tail
    assert tail.index("val") < tail.index("zzz")


def test_pair_list_and_cell_step_paging_stay_ordered(tmp_path: Path):
    n = PAGE_SIZE + 20
    a_lines = ["id,val,ctx"] + [f"{i},A{i:03d},c{i}" for i in range(n)]
    b_lines = ["id,val,ctx"] + [f"{i},B{i:03d},d{i}" for i in range(n)]
    eng = _eng(tmp_path, "\n".join(a_lines) + "\n", "\n".join(b_lines) + "\n")
    eng.context_columns["val"] = ["ctx"]
    groups = eng.pair_groups("val")
    assert groups.height == n
    recs0, page0, pages = eng.pair_page("val", 0)
    recs1, page1, _ = eng.pair_page("val", 1)
    assert page0 == 0
    assert page1 == 1
    assert pages == 2
    assert len(recs0) == PAGE_SIZE
    assert len(recs1) == 20
    ordered = list(zip(groups["val_a"].to_list(), groups["val_b"].to_list(), groups["n"].to_list()))
    assert [(r["val_a"], r["val_b"], r["n"]) for r in recs0] == ordered[:PAGE_SIZE]
    assert [(r["val_a"], r["val_b"], r["n"]) for r in recs1] == ordered[PAGE_SIZE:]
    full_ctx = _attach_pair_context_summaries(eng, "val", groups)
    by_pair = {
        (r["val_a"], r["val_b"]): r["ctx__ctx"]
        for r in full_ctx.to_dicts()
    }
    for rec in recs1:
        assert rec["ctx__ctx"] == by_pair[(rec["val_a"], rec["val_b"])]
    cells, cpage, cpages = eng.pair_cells_page("val", recs1[0]["val_a"], recs1[0]["val_b"], 0)
    assert cpage == 0
    assert cpages == 1
    assert len(cells) == 1
    assert cells[0]["val_a"] == recs1[0]["val_a"]


def test_suggest_extras_cache_and_tui_rows(tmp_path: Path):
    eng = _eng(
        tmp_path,
        "id,val,cust_id\n1,a,1\n",
        "id,val,Cust ID\n1,a,2\n",
    )
    rows = eng.suggest_extras()
    assert len(rows) == 1
    assert rows[0]["name_a"] == "cust_id"
    assert rows[0]["name_b"] == "Cust ID"
    assert eng.suggest_extras() is rows
    write_csv(tmp_path / "a.csv", "id,val,cust_id\n1,a,1\n")
    write_csv(tmp_path / "b.csv", "id,val,Cust ID\n1,a,2\n")
    eng.refresh()
    assert eng._suggest_extras_cache is None
    again = eng.suggest_extras()
    assert again is not rows
    assert len(again) == 1


def test_union_pairs_cache_and_m_accept(tmp_path: Path):
    eng = _eng(
        tmp_path,
        "id,qty,amt,note\n1,,,x\n2,,,y\n",
        "id,qty,amt,note\n1,0,0,x\n2,0,0,z\n",
    )
    cols = ["qty", "amt", "note"]
    first, _, _ = eng.union_pairs(cols)
    empty_pair = next(r for r in first if r["val_a"] == "" and r["val_b"] == "0")
    assert empty_pair["n"] == 4
    assert empty_pair["n_cols"] == 2
    cached = eng._union_pairs_cache[frozenset(cols)]
    again, _, _ = eng.union_pairs(cols)
    assert again == first
    assert eng._union_pairs_cache[frozenset(cols)] is cached
    n = eng.accept_pair_across_columns(cols, "", "0")
    assert n == 4
    assert frozenset(cols) not in eng._union_pairs_cache
    leftover, _, _ = eng.union_pairs(cols)
    assert leftover[0]["val_a"] == "y"
    assert leftover[0]["val_b"] == "z"
    assert leftover[0]["n"] == 1
    assert leftover[0]["n_cols"] == 1
    assert next(r for r in eng.roster() if r.name == "qty").pending == 0
    assert next(r for r in eng.roster() if r.name == "amt").pending == 0
    assert next(r for r in eng.roster() if r.name == "note").pending == 1


def test_pair_ctx_invalidates_only_accepted_column(tmp_path: Path):
    eng = _eng(
        tmp_path,
        "id,val,note,ctx\n1,Y,a,east\n2,N,b,west\n",
        "id,val,note,ctx\n1,Yes,A,east\n2,No,B,west\n",
    )
    eng.context_columns["val"] = ["ctx"]
    eng.context_columns["note"] = ["ctx"]
    eng.pair_page("val", 0)
    eng.pair_page("note", 0)
    assert "val" in eng._pair_ctx_by_col
    assert "note" in eng._pair_ctx_by_col
    eng.accept_pair("val", "Y", "Yes")
    assert "val" not in eng._pair_ctx_by_col
    assert "note" in eng._pair_ctx_by_col


def test_pair_draft_cache_skips_when_other_column_accepted(tmp_path: Path):
    eng = _eng(
        tmp_path,
        "id,val,note\n1,Y,a\n2,Y,b\n",
        "id,val,note\n1,Yes,A\n2,Yes,B\n",
    )
    n = eng.start_pair_draft("val", "Y", "Yes")
    assert n == 2
    cached = eng._pair_draft_cells
    assert cached is not None
    eng.accept_pair("note", "a", "A")
    assert eng._pair_draft_cells is cached
    assert eng._pair_draft_n == 2
    eng.accept_cell(("1",), "val", "Y", "Yes")
    assert eng._pair_draft_cells is not cached
    assert eng._pair_draft_n == 1
    frame = _pair_cells_frame(eng, "val", "Y", "Yes")
    assert frame.height == 1


def test_equal_frame_derived_from_sorted_all_matched(tmp_path: Path):
    eng = _eng(
        tmp_path,
        "id,val\n1,Y\n2,same\n3,Z\n",
        "id,val\n1,Yes\n2,same\n3,Zed\n",
    )
    all_m = _all_matched_frame(eng, "val")
    eq = _equal_frame(eng, "val")
    assert list(all_m["id"].to_list()) == ["1", "2", "3"]
    assert eq.height == 1
    assert eq["id"].to_list() == ["2"]
    assert eq["val_a"].to_list() == ["same"]
    recs, _, _ = eng.cells_for_tab("val", "equal", 0)
    assert recs[0]["id"] == "2"
    recs_all, _, _ = eng.cells_for_tab("val", "all_matched", 0)
    assert [r["id"] for r in recs_all] == ["1", "2", "3"]
    assert ("val", "equal") in eng._tab_frames
    assert ("val", "all_matched") in eng._tab_frames


def _check_yn(row) -> tuple:
    from reconcile.roster import CHECK_COLS

    return tuple(getattr(row, attr) for _, attr in CHECK_COLS)


def test_insight_stats_keep_other_columns_after_accept(tmp_path: Path):
    eng = _eng(
        tmp_path,
        "id,status,when,note\n1,Y,2020-01-02,a\n2,N,2020-01-02,b\n",
        "id,status,when,note\n1,Yes,20200102,A\n2,No,20200102,B\n",
    )
    before = {r.name: _check_yn(r) for r in eng.roster() if r.kind == "column"}
    when_stats = eng._col_stats["when"]
    note_stats = eng._col_stats["note"]
    assert eng._col_stats_warm is True
    eng.accept_pair("status", "Y", "Yes")
    assert eng._col_stats.get("when") is when_stats
    assert eng._col_stats.get("note") is note_stats
    assert "status" in eng._col_stats
    after = {r.name: _check_yn(r) for r in eng.roster() if r.kind == "column"}
    assert after["when"] == before["when"]
    assert after["note"] == before["note"]


def test_insight_yn_after_accept_matches_full_recompute(tmp_path: Path):
    a = "id,status,when,note\n1,Y,2020-01-02,a\n2,N,2020-01-02,b\n"
    b = "id,status,when,note\n1,Yes,20200102,A\n2,No,20200102,B\n"
    eng = _eng(tmp_path, a, b)
    eng.roster()
    eng.accept_pair("status", "Y", "Yes")
    got = {r.name: _check_yn(r) for r in eng.roster() if r.kind == "column"}
    other = tmp_path / "full"
    other.mkdir()
    fresh = _eng(other, a, b)
    fresh.accept_pair("status", "Y", "Yes")
    expect = {r.name: _check_yn(r) for r in fresh.roster() if r.kind == "column"}
    assert got == expect
    assert got["when"][5] == "y"  # date
    eng.accept_column("status")
    assert "status" not in eng._col_stats
    assert eng._col_stats.get("when") is not None


def test_check_column_draft_without_roster_rows(tmp_path: Path):
    eng = _eng(
        tmp_path,
        'id,cash,fee,note\n1,"$1,234","€2 000",foo\n2,"€2 000","$3,000",bar\n',
        "id,cash,fee,note\n1,1234,2000,baz\n2,2000,3000,qux\n",
    )
    assert eng._col_stats_warm is False
    assert eng._roster_cache_valid is False
    n = eng.start_check_column_draft("money")
    assert n == 2
    assert eng.column_draft == {"cash", "fee"}
    assert eng._roster_cache_valid is False
    assert eng._col_stats_warm is True
    eng.cancel_drafts()
    paint = tmp_path / "paint"
    paint.mkdir()
    painted = _eng(
        paint,
        'id,cash,fee,note\n1,"$1,234","€2 000",foo\n2,"€2 000","$3,000",bar\n',
        "id,cash,fee,note\n1,1234,2000,baz\n2,2000,3000,qux\n",
    )
    y_names = [r.name for r in painted.visible_column_roster() if r.money == "y"]
    assert y_names == ["cash", "fee"]


def test_pair_groups_slice_is_cached_and_page_order_holds(tmp_path: Path):
    eng = _eng(
        tmp_path,
        "id,val,ctx\n1,Y,east\n2,N,west\n",
        "id,val,ctx\n1,Yes,east\n2,No,west\n",
    )
    eng.context_columns["val"] = ["ctx"]
    groups = eng.pair_groups("val")
    assert groups is eng._pair_groups_by_col["val"]
    recs, _, _ = eng.pair_page("val", 0)
    ordered = list(
        zip(groups["val_a"].to_list(), groups["val_b"].to_list(), groups["n"].to_list())
    )
    assert [(r["val_a"], r["val_b"], r["n"]) for r in recs] == ordered
    assert recs[0]["ctx__ctx"]


def test_cell_snap_keys_track_accept_and_undo(tmp_path: Path):
    eng = _eng(
        tmp_path,
        "id,val\n1,Y\n2,N\n",
        "id,val\n1,Yes\n2,No\n",
    )
    assert eng._cell_snap_keys.height == 0
    n = eng.accept_cell(("1",), "val", "Y", "Yes")
    assert n == 1
    assert eng._cell_snap_keys.height == eng.cell_snaps.height == 1
    assert eng.pending_cells.filter(pl.col("id") == "1").height == 0
    assert eng.accepted_cells.filter(pl.col("id") == "1").height == 1
    assert eng.pending_cells.filter(pl.col("id") == "2").height == 1
    undone = eng.undo_cell(("1",), "val")
    assert undone == 1
    assert eng._cell_snap_keys.height == eng.cell_snaps.height == 0
    assert eng.pending_cells.height == 2
    assert eng.accepted_cells.height == 0


def test_start_pair_draft_reuses_pair_cell_cache(tmp_path: Path):
    eng = _eng(
        tmp_path,
        "id,val\n1,Y\n2,Y\n",
        "id,val\n1,Yes\n2,Yes\n",
    )
    eng.pair_cells_page("val", "Y", "Yes", 0)
    cached = eng._pair_cells_cache[("val", "Y", "Yes")]
    n = eng.start_pair_draft("val", "Y", "Yes")
    assert n == cached.height == 2
    assert eng._pair_draft_cells is cached
    assert list(eng._pair_draft_cells["id"].to_list()) == ["1", "2"]


def test_place_from_last_pair_uses_groups_and_zero_is_none(tmp_path: Path):
    eng = _eng(
        tmp_path,
        "id,val,note\n1,Y,a\n2,N,b\n",
        "id,val,note\n1,Yes,A\n2,No,B\n",
    )
    placed = eng.place_from_last_pair(("val", "Y", "Yes"), roster_filter="v")
    assert placed is not None
    assert placed.screen == "pair_list"
    assert placed.column == "val"
    assert placed.pair_val_a == "Y"
    assert placed.pair_val_b == "Yes"
    assert placed.roster_filter == "v"
    assert eng.place_from_last_pair(("val", "nope", "nope")) is None
    eng.accept_pair("val", "Y", "Yes")
    assert eng.place_from_last_pair(("val", "Y", "Yes")) is None
    assert eng.place_from_last_pair(("note", "a", "A")) is not None


def test_unmatched_flags_survive_paging(tmp_path: Path):
    n = PAGE_SIZE + 20
    a_lines = ["id,note"] + [f"{i:03d},n{i}" for i in range(n)]
    eng = _eng(tmp_path, "\n".join(a_lines) + "\n", "id,note\n")
    eng.accept_unmatched("A", ("000",))
    eng.accept_unmatched("A", ("050",))
    eng.accept_unmatched("A", ("100",))
    eng.accept_unmatched("A", ("105",))
    changed = ["id,note"] + [
        f"{i:03d},{'changed' if i in (0, 105) else f'n{i}'}" for i in range(n)
    ]
    write_csv(tmp_path / "a.csv", "\n".join(changed) + "\n")
    eng.refresh()
    recs0, page0, pages = eng.unmatched_page("A", 0)
    accepted_keys = eng._unmatched_accepted_keys
    returned_keys = eng._unmatched_returned_keys
    assert accepted_keys is not None and returned_keys is not None
    acc = accepted_keys["A"]
    ret = returned_keys["A"]
    recs1, page1, _ = eng.unmatched_page("A", 1)
    assert eng._unmatched_accepted_keys is not None
    assert eng._unmatched_accepted_keys["A"] is acc
    assert eng._unmatched_returned_keys is not None
    assert eng._unmatched_returned_keys["A"] is ret
    again, _, _ = eng.unmatched_page("A", 0)
    assert page0 == 0 and page1 == 1 and pages == 2

    def flags(recs: list[dict]) -> dict[str, tuple[bool, bool]]:
        return {r["id"]: (bool(r["_accepted"]), bool(r["_returned"])) for r in recs}

    f0 = flags(recs0)
    f1 = flags(recs1)
    assert f0["000"] == (False, True)
    assert f0["050"] == (True, False)
    assert f1["100"] == (True, False)
    assert f1["105"] == (False, True)
    assert flags(again)["000"] == f0["000"]
    assert flags(again)["050"] == f0["050"]


def test_noop_render_keeps_roster_cursor_and_draft_toggle_updates(tmp_path: Path):
    from reconcile.tui import ReconcileApp

    eng = _eng(
        tmp_path,
        "id,alpha,beta\n1,Y,1\n",
        "id,alpha,beta\n1,Yes,2\n",
    )
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#grid")
            assert table.row_count >= 2
            table.move_cursor(row=1)
            await pilot.pause()
            app.render_all()
            await pilot.pause()
            assert table.cursor_row == 1
            app.engine.start_regex_draft("alpha|beta")
            app.render_all()
            await pilot.pause()
            names = [r.name for r in app._table_keys if r is not None]
            beta_i = names.index("beta")
            assert "[ON]" in str(table.get_row_at(beta_i)[0])
            table.move_cursor(row=beta_i)
            await pilot.press("space")
            await pilot.pause()
            assert "beta" not in app.engine.column_draft
            assert "[off]" in str(table.get_row_at(beta_i)[0])
            assert "[ON]" in str(table.get_row_at(names.index("alpha"))[0])

    asyncio.run(_run())


def test_pair_page_navigation_rows(tmp_path: Path):
    from reconcile.tui import ReconcileApp

    n = PAGE_SIZE + 5
    eng = _eng(
        tmp_path,
        "id,val\n" + "".join(f"{i:03d},A{i:03d}\n" for i in range(n)),
        "id,val\n" + "".join(f"{i:03d},B{i:03d}\n" for i in range(n)),
    )
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.place.screen = "pair_list"
            app.place.column = "val"
            app.place.page = 0
            app.render_all()
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            assert app.place.page == 1
            table = app.query_one("#grid")
            assert table.row_count == 5
            first = str(table.get_row_at(0)[0])
            assert "A" in first
            await pilot.press("p")
            await pilot.pause()
            assert app.place.page == 0
            assert table.row_count == PAGE_SIZE

    asyncio.run(_run())


def test_key_named_underscore_key_still_partitions(tmp_path: Path):
    eng = _eng(
        tmp_path,
        "_key,val,note\n1,a,x\n2,c,y\n3,e,z\n",
        "_key,val,note\n1,b,x\n4,c,w\n3,e,z\n",
        keys="_key",
    )
    assert eng.pending_cells_n() == 1
    rec = eng.pending_cells.to_dicts()[0]
    assert rec["_key"] == "1"
    assert rec["column"] == "val"
    assert rec["val_a"] == "a"
    assert rec["val_b"] == "b"
    assert eng.pending_a_only["_key"].to_list() == ["2"]
    assert eng.pending_b_only["_key"].to_list() == ["4"]
    assert eng.matched_key_count() == 2
    assert eng.equal_count("note") == 2
    assert eng.equal_count("val") == 1
