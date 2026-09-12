import asyncio
from pathlib import Path

from reconcile.engine import Engine
from reconcile.tui import HELP, ReconcileApp, SentinelModal
from textual.widgets import Input
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
            assert app.engine.place.screen == "roster"
            pending_before = app.engine.pending_total()
            app.query_one("#grid").focus()
            await pilot.pause()
            if app.engine.place.screen == "roster":
                app.action_drill()
                await pilot.pause()
            await pilot.pause()
            assert app.engine.place.screen in {"pair_list", "a_only", "b_only", "extras"}
            await pilot.press("escape")
            await pilot.pause()
            assert app.engine.place.screen == "roster"
            await pilot.press("escape")
            await pilot.pause()
            assert app.engine.place.screen == "overview"
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


def test_tui_sentinel_draft_from_equals_modal(tmp_path: Path):
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
            assert not app.engine.draft_in_flight()
            modal._side = "A"
            modal.query_one("#sentinel", Input).value = "NA"
            modal.action_ok()
            await pilot.pause()
            assert app.engine.column_draft == {"s"}
            assert "mixed" not in app.engine.column_draft
            assert "ok" not in app.engine.column_draft
            app.query_one("#grid").focus()
            await pilot.pause()
            await pilot.press("slash")
            await pilot.pause()
            footer = str(app.query_one("#footer").render())
            assert "draft" in footer
            banner = str(app.query_one("#banner").render())
            assert "confirm or cancel" in banner

    asyncio.run(_run())
