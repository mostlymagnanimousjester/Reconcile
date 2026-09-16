import asyncio
from pathlib import Path

from reconcile.engine import Engine
from reconcile.tui import HELP, ReconcileApp, SentinelModal
from tests.xlsxutil import write_csv


def test_tui_launches_against_fixture(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val,Status\n1,a,Y\n2,b,N\n")
    write_csv(pb, "id,val,Status\n1,a,Yes\n3,c,N\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            footer = str(app.query_one("#footer").render())
            assert "pending" in footer
            assert app.engine.pending_total() > 0
            assert app.place.screen == "roster"
            pending_before = app.engine.pending_total()
            app.query_one("#grid").focus()
            await pilot.pause()
            if app.place.screen == "roster":
                app.action_drill()
                await pilot.pause()
            await pilot.pause()
            assert app.place.screen in {"pair_list", "a_only", "b_only", "extras"}
            await pilot.press("escape")
            await pilot.pause()
            assert app.place.screen == "roster"
            await pilot.press("escape")
            await pilot.pause()
            assert app.place.screen == "overview"
            await pilot.press("question_mark")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert pending_before == app.engine.pending_total()

    asyncio.run(_run())


def test_help_says_exact_sentinel_not_polars_selector():
    assert "Polars selector" not in HELP
    assert "exact sentinel" in HELP
    assert "regex column draft" in HELP


def test_slash_then_pair_y_does_not_accept_columns(tmp_path: Path):
    """Engine owns the column draft. Pair Enter/y while it is live must not snapshot columns."""
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag\n1,Y,1\n2,Y,2\n")
    write_csv(pb, "id,Status,Flag\n1,Yes,9\n2,Yes,8\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert not hasattr(app, "column_draft")
            app.query_one("#grid").focus()
            await pilot.pause()
            app.action_regex()
            await pilot.pause()
            modal = app.screen
            modal.query_one("#pat").value = "Status|Flag"
            modal.action_ok()
            await pilot.pause()
            assert app.engine.column_draft == {"Status", "Flag"}
            assert app.draft_in_flight() is True
            assert app.engine.draft_in_flight() is True
            footer = str(app.query_one("#footer").render())
            assert "draft 2" in footer
            pending_before = app.engine.pending_cells_n()
            app.action_drill()
            await pilot.pause()
            assert app.place.screen == "pair_list"
            app.action_drill()
            await pilot.pause()
            assert app.engine.pair_draft_col is None
            assert app.place.screen == "pair_list"
            assert app.tui_error and "column draft" in app.tui_error
            assert app.engine.pending_cells_n() == pending_before
            app.action_confirm()
            await pilot.pause()
            assert app.engine.pending_cells_n() == pending_before
            assert app.engine.column_draft == {"Status", "Flag"}
            footer = str(app.query_one("#footer").render())
            assert "draft 2" in footer
            assert app.place.screen != "cell_step"

    asyncio.run(_run())


def test_cell_step_footer_shows_pair_draft_not_column_n(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag\n1,Y,1\n2,Y,2\n")
    write_csv(pb, "id,Status,Flag\n1,Yes,9\n2,Yes,8\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#grid").focus()
            n = app.engine.start_pair_draft("Status", "Y", "Yes")
            assert n == 2
            app.place.screen = "cell_step"
            app.place.column = "Status"
            app.place.pair_val_a = "Y"
            app.place.pair_val_b = "Yes"
            app.render_all()
            await pilot.pause()
            footer = str(app.query_one("#footer").render())
            assert "draft 2" in footer
            assert "cell step" in footer

    asyncio.run(_run())


def test_categorical_top_row_a_accepts_pair(tmp_path: Path):
    """Categorical columns stay on the paged pair list; a on the top row accepts that pair."""
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    lines_a = ["id,Status"] + [f"{i},Y" for i in range(8)] + ["8,N"]
    lines_b = ["id,Status"] + [f"{i},Yes" for i in range(8)] + ["8,No"]
    write_csv(pa, "\n".join(lines_a) + "\n")
    write_csv(pb, "\n".join(lines_b) + "\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    status = next(r for r in eng.roster() if r.name == "Status")
    assert status.categorical == "yes"
    assert not hasattr(eng, "pair_matrix")
    assert not hasattr(ReconcileApp, "_pair_matrix")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#grid").focus()
            app.action_drill()
            await pilot.pause()
            assert app.place.screen == "pair_list"
            table = app.query_one("#grid")
            assert table.cursor_type == "row"
            pane = str(app.query_one("#pane").render())
            assert "A:" in pane
            assert "B:" in pane
            assert "Y" in pane
            assert "Yes" in pane
            pending_before = app.engine.pending_cells_n()
            app.action_accept()
            await pilot.pause()
            assert app.engine.pending_cells_n() == pending_before - 8
            left = next(r for r in app.engine.roster() if r.name == "Status")
            assert left.pending == 1

    asyncio.run(_run())


def test_filter_does_not_hide_next_lever(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag\n1,Y,1\n2,Y,1\n")
    write_csv(pb, "id,Status,Flag\n1,Yes,2\n2,Yes,1\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            filt = app.query_one("#filter")
            filt.value = "Flag"
            app.place.roster_filter = "Flag"
            app.place.focused_name = "Flag"
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            row = app._focused_roster()
            assert row is not None and row.name == "Flag"
            app.action_accept()
            await pilot.pause()
            assert app.place.screen == "pair_list"
            assert app.place.column == "Status"
            assert app.place.roster_filter == "Flag"

    asyncio.run(_run())


def test_roster_A_on_extra_accepts_like_a(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val,cust\n1,a,1\n")
    write_csv(pb, "id,val\n1,a\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.place.focused_name = "cust"
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            row = app._focused_roster()
            assert row is not None and row.kind == "extra"
            app.action_accept_all()
            await pilot.pause()
            assert app.engine.pending_extras_n() == 0

    asyncio.run(_run())


def test_cell_a_moves_cursor_and_page_to_focused_key(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n" + "".join(f"{i:03d},Y\n" for i in range(120)))
    write_csv(pb, "id,val\n" + "".join(f"{i:03d},Yes\n" for i in range(120)))
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            n = app.engine.start_pair_draft("val", "Y", "Yes")
            assert n == 120
            app.place.screen = "cell_step"
            app.place.column = "val"
            app.place.pair_val_a = "Y"
            app.place.pair_val_b = "Yes"
            app.place.focused_key = ("099",)
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            assert app.place.page == 0
            assert app.query_one("#grid").cursor_row == 99
            assert app._focused_rec()["id"] == "099"
            app.place.focused_key = ("100",)
            app.render_all()
            await pilot.pause()
            assert app.place.page == 1
            assert app.query_one("#grid").cursor_row == 0
            assert app._focused_rec()["id"] == "100"
            app.action_accept()
            await pilot.pause()
            assert app.place.screen == "cell_step"
            assert app.place.focused_key == ("101",)
            assert app.place.page == 1
            assert app._focused_rec()["id"] == "101"

    asyncio.run(_run())


def test_pair_exhausted_returns_to_this_column_pair_list(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag\n1,Y,1\n")
    write_csv(pb, "id,Status,Flag\n1,Yes,2\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.engine.start_pair_draft("Status", "Y", "Yes")
            app.place.screen = "cell_step"
            app.place.column = "Status"
            app.place.pair_val_a = "Y"
            app.place.pair_val_b = "Yes"
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            app.action_accept()
            await pilot.pause()
            assert app.place.screen == "pair_list"
            assert app.place.column == "Status"
            assert app.engine.pair_draft_col is None
            assert next(r for r in app.engine.roster() if r.name == "Flag").pending == 1

    asyncio.run(_run())


def test_drafted_column_a_stays_on_roster_until_last(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag\n1,Y,1\n2,Y,2\n")
    write_csv(pb, "id,Status,Flag\n1,Yes,9\n2,Yes,8\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.engine.start_regex_draft("Status|Flag")
            app.place.focused_name = "Status"
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            app.action_accept()
            await pilot.pause()
            assert app.place.screen == "roster"
            assert app.engine.column_draft == {"Flag"}
            app.action_accept()
            await pilot.pause()
            assert not app.engine.column_draft
            assert app.place.screen != "roster" or app.engine.pending_cells_n() == 0

    asyncio.run(_run())


def test_tui_equals_opens_sentinel_modal_and_refuses_without_side(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,s,mixed,ok\n1,NA,NA,a\n2,NA,x,a\n")
    write_csv(pb, "id,s,mixed,ok\n1,x,y,a\n2,y,z,a\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#grid").focus()
            await pilot.pause()
            await pilot.press("equals")
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, SentinelModal)
            modal.action_ok()
            await pilot.pause()
            assert isinstance(app.screen, SentinelModal)
            assert not app.draft_in_flight()
            modal.action_cancel()
            await pilot.pause()
            assert not isinstance(app.screen, SentinelModal)
            assert not app.draft_in_flight()

    asyncio.run(_run())
