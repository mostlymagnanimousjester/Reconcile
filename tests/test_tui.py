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
