import asyncio
from pathlib import Path

from textual.widgets import Button

from reconcile.engine import Engine, Place
from reconcile.tui import HELP, ContextModal, HelpModal, ReconcileApp, RegexModal, SentinelModal
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


def test_footer_page_n_over_m(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n" + "".join(f"{i:03d},Y\n" for i in range(120)))
    write_csv(pb, "id,val\n" + "".join(f"{i:03d},Yes\n" for i in range(120)))
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.engine.start_pair_draft("val", "Y", "Yes")
            app.place.screen = "cell_step"
            app.place.column = "val"
            app.place.pair_val_a = "Y"
            app.place.pair_val_b = "Yes"
            app.render_all()
            await pilot.pause()
            footer = str(app.query_one("#footer").render())
            assert "page 1/2" in footer
            app.action_page_next()
            await pilot.pause()
            footer = str(app.query_one("#footer").render())
            assert "page 2/2" in footer
            assert "Enter cells" in footer or "a cell" in footer or "? help" in footer

    asyncio.run(_run())


def test_keys_1_to_4_do_not_switch_tabs(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#grid").focus()
            app.action_drill()
            await pilot.pause()
            assert app.place.screen == "pair_list"
            await pilot.press("2")
            await pilot.pause()
            assert app.place.screen == "pair_list"
            await pilot.press("3")
            await pilot.pause()
            assert app.place.screen == "pair_list"
            n = app.engine.start_pair_draft("val", "Y", "Yes")
            assert n == 1
            app.place.screen = "cell_step"
            app.place.column = "val"
            app.place.pair_val_a = "Y"
            app.place.pair_val_b = "Yes"
            app.render_all()
            await pilot.pause()
            app.query_one("#tab-accepted", Button).press()
            await pilot.pause()
            assert app.place.screen == "cell_step"
            assert app.engine.pair_draft_col == "val"
            assert app.tui_error and "pair draft" in app.tui_error

    asyncio.run(_run())


def test_help_says_exact_sentinel_not_polars_selector():
    assert "Polars selector" not in HELP
    assert "exact sentinel" in HELP
    assert "regex column draft" in HELP


def test_help_xor_and_no_digit_tab_keys():
    assert "column XOR pair" in HELP
    assert "No keys 1–4" in HELP or "No keys 1-4" in HELP
    xor = next(line for line in HELP.splitlines() if "At most one draft" in line)
    assert "/" in xor
    assert "=" in xor
    lower = HELP.lower()
    assert "1 pending" not in lower
    assert "2 accepted" not in lower
    assert "3 equal" not in lower
    assert "4 all" not in lower


def test_help_power_user_grain():
    assert "Overview Esc" in HELP
    assert "cell step only" in HELP
    assert "column detail only" in HELP
    assert "A-only / B-only undoes all unmatched" in HELP
    assert "refused while a pair draft" in HELP
    assert "undo last accept" in HELP


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
            assert "draft stays" in footer
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
            assert app.place.roster_filter == ""

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


def test_n_after_cell_a_changes_page(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n" + "".join(f"{i:03d},Y\n" for i in range(250)))
    write_csv(pb, "id,val\n" + "".join(f"{i:03d},Yes\n" for i in range(250)))
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            n = app.engine.start_pair_draft("val", "Y", "Yes")
            assert n == 250
            app.place.screen = "cell_step"
            app.place.column = "val"
            app.place.pair_val_a = "Y"
            app.place.pair_val_b = "Yes"
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            assert app.place.page == 0
            app.action_accept()
            await pilot.pause()
            assert app.place.screen == "cell_step"
            assert app.place.focused_key == ("001",)
            assert app.place.page == 0
            app.action_page_next()
            await pilot.pause()
            assert app.place.page == 1
            footer = str(app.query_one("#footer").render())
            assert "page 2/3" in footer

    asyncio.run(_run())


def test_refresh_pair_draft_new_key_not_accepted_on_y(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n2,Y\n")
    write_csv(pb, "id,val\n1,Yes\n2,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            n = app.engine.start_pair_draft("val", "Y", "Yes")
            assert n == 2
            app.place.screen = "cell_step"
            app.place.column = "val"
            app.place.pair_val_a = "Y"
            app.place.pair_val_b = "Yes"
            app.render_all()
            await pilot.pause()
            write_csv(pa, "id,val\n1,Y\n2,Y\n3,Y\n")
            write_csv(pb, "id,val\n1,Yes\n2,Yes\n3,Yes\n")
            app.action_refresh()
            await pilot.pause()
            assert app.engine.pair_draft_col == "val"
            assert ("3",) in app.pair_draft_unchecked
            app.action_confirm()
            await pilot.pause()
            assert app.engine.pending_cells_n() == 1
            assert app.engine.pending_cells.get_column("id").to_list() == ["3"]

    asyncio.run(_run())


def test_refresh_pair_draft_new_key_accepted_if_checked(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n2,Y\n")
    write_csv(pb, "id,val\n1,Yes\n2,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.engine.start_pair_draft("val", "Y", "Yes")
            app.place.screen = "cell_step"
            app.place.column = "val"
            app.place.pair_val_a = "Y"
            app.place.pair_val_b = "Yes"
            app.render_all()
            await pilot.pause()
            write_csv(pa, "id,val\n1,Y\n2,Y\n3,Y\n")
            write_csv(pb, "id,val\n1,Yes\n2,Yes\n3,Yes\n")
            app.action_refresh()
            await pilot.pause()
            assert ("3",) in app.pair_draft_unchecked
            app.pair_draft_unchecked.discard(("3",))
            app.action_confirm()
            await pilot.pause()
            assert app.engine.pending_cells_n() == 0

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
            assert "ERROR" in str(modal.query_one("#modal-err").render())
            assert not app.draft_in_flight()
            modal.action_cancel()
            await pilot.pause()
            assert not isinstance(app.screen, SentinelModal)
            assert not app.draft_in_flight()

    asyncio.run(_run())


def test_A_refused_during_pair_draft(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n2,N\n")
    write_csv(pb, "id,val\n1,Yes\n2,No\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            n = app.engine.start_pair_draft("val", "Y", "Yes")
            assert n == 1
            app.place.screen = "cell_step"
            app.place.column = "val"
            app.place.pair_val_a = "Y"
            app.place.pair_val_b = "Yes"
            app.render_all()
            await pilot.pause()
            pending = app.engine.pending_cells_n()
            app.action_accept_all()
            await pilot.pause()
            assert app.engine.pair_draft_col == "val"
            assert app.engine.pending_cells_n() == pending
            assert app.tui_error and "pair draft" in app.tui_error

    asyncio.run(_run())


def test_u_undoes_last_accept_after_next_lever(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag\n1,Y,1\n2,Y,1\n")
    write_csv(pb, "id,Status,Flag\n1,Yes,2\n2,Yes,1\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#grid").focus()
            app.action_drill()
            await pilot.pause()
            assert app.place.screen == "pair_list"
            assert app.place.column == "Status"
            app.action_accept()
            await pilot.pause()
            assert app.place.column == "Flag"
            assert next(r for r in app.engine.roster() if r.name == "Status").pending == 0
            app.action_undo()
            await pilot.pause()
            assert next(r for r in app.engine.roster() if r.name == "Status").pending == 2
            assert app.engine.last_grain is None

    asyncio.run(_run())


def test_overview_esc_returns_to_roster(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.place.screen == "roster"
            app.action_back()
            await pilot.pause()
            assert app.place.screen == "overview"
            app.action_back()
            await pilot.pause()
            assert app.place.screen == "roster"

    asyncio.run(_run())


def test_zero_hit_regex_and_sentinel_error(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            from reconcile.engine import InTuiError

            try:
                app.engine.start_regex_draft("^zzzz$")
            except InTuiError as exc:
                app.set_error(exc.message)
            await pilot.pause()
            assert app.tui_error and "0 pending" in app.tui_error
            assert not app.draft_in_flight()
            try:
                app.engine.start_sentinel_draft("A", "no-such-sentinel")
            except InTuiError as exc:
                app.set_error(exc.message)
            await pilot.pause()
            assert app.tui_error and "0 pending" in app.tui_error
            assert not app.draft_in_flight()

    asyncio.run(_run())


def test_dot_refused_on_roster(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.place.last_pair = ("val", "Y", "Yes")
            app.action_repeat_pair()
            await pilot.pause()
            assert app.place.screen == "roster"
            assert app.tui_error and "column detail" in app.tui_error
            assert app.engine.pair_draft_col is None

    asyncio.run(_run())


def test_zero_pending_roster_a_stays(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,ok\n1,Y,same\n")
    write_csv(pb, "id,Status,ok\n1,Yes,same\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.place.focused_name = "ok"
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            row = app._focused_roster()
            assert row is not None and row.name == "ok" and row.pending == 0
            pending = app.engine.pending_total()
            app.action_accept()
            await pilot.pause()
            assert app.place.screen == "roster"
            assert app.engine.pending_total() == pending
            assert app.tui_error and "pending" in app.tui_error
            app.action_accept_all()
            await pilot.pause()
            assert app.place.screen == "roster"
            assert app.engine.pending_total() == pending

    asyncio.run(_run())


def test_y_all_unchecked_does_not_next_lever(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n2,Y\n")
    write_csv(pb, "id,val\n1,Yes\n2,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            n = app.engine.start_pair_draft("val", "Y", "Yes")
            assert n == 2
            app.place.screen = "cell_step"
            app.place.column = "val"
            app.place.pair_val_a = "Y"
            app.place.pair_val_b = "Yes"
            app.pair_draft_unchecked = {("1",), ("2",)}
            app.render_all()
            await pilot.pause()
            pending = app.engine.pending_cells_n()
            app.action_confirm()
            await pilot.pause()
            assert app.place.screen == "cell_step"
            assert app.engine.pair_draft_col == "val"
            assert app.engine.pending_cells_n() == pending
            assert app.tui_error and "unchecked" in app.tui_error

    asyncio.run(_run())


def test_zip_restores_last_pair_focus(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n2,Y\n3,N\n")
    write_csv(pb, "id,val\n1,Yes\n2,Yes\n3,No\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    z = tmp_path / "job.recon.zip"
    eng.export_zip(str(z), Place(screen="roster", last_pair=("val", "N", "No")))
    loaded, place = Engine.from_session(str(z))
    app = ReconcileApp(loaded, place)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.place.screen == "pair_list"
            assert app.place.column == "val"
            assert app.place.pair_val_a == "N"
            assert app.place.pair_val_b == "No"
            pane = str(app.query_one("#pane").render())
            assert "N" in pane
            assert "No" in pane
            assert app.engine.pair_draft_col is None

    asyncio.run(_run())


def test_unmatched_last_a_then_u_restores_that_key(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,a\n2,b\n")
    write_csv(pb, "id,val\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.place.screen = "a_only"
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            app.action_accept()
            await pilot.pause()
            app.action_accept()
            await pilot.pause()
            assert app.engine.pending_a_only_n() == 0
            assert app.place.focused_key is None
            app.action_undo()
            await pilot.pause()
            assert app.engine.pending_a_only_n() == 1
            assert app.engine.pending_a_only.get_column("id").to_list() == ["2"]
            assert app.engine.last_grain is None

    asyncio.run(_run())


def test_u_after_next_lever_prefers_last_grain_even_if_new_pair_has_snaps(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag\n1,Y,1\n2,Y,1\n")
    write_csv(pb, "id,Status,Flag\n1,Yes,2\n2,Yes,2\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            n = app.engine.accept_cell(("1",), "Flag", "1", "2")
            app.engine.remember_grain(("cell", ("1",), "Flag"), n)
            app.place.screen = "pair_list"
            app.place.column = "Status"
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            assert app.place.column == "Status"
            app.action_accept()
            await pilot.pause()
            assert app.place.column == "Flag"
            assert next(r for r in app.engine.roster() if r.name == "Status").pending == 0
            assert next(r for r in app.engine.roster() if r.name == "Flag").accepted == 1
            app.action_undo()
            await pilot.pause()
            assert next(r for r in app.engine.roster() if r.name == "Status").pending == 2
            assert next(r for r in app.engine.roster() if r.name == "Flag").accepted == 1
            assert app.engine.last_grain is None

    asyncio.run(_run())


def test_A_refused_on_pair_list_during_column_draft(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag\n1,Y,1\n2,Y,2\n")
    write_csv(pb, "id,Status,Flag\n1,Yes,9\n2,Yes,8\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.engine.start_regex_draft("Status|Flag")
            assert app.engine.column_draft == {"Status", "Flag"}
            app.place.screen = "pair_list"
            app.place.column = "Status"
            app.render_all()
            await pilot.pause()
            pending = app.engine.pending_cells_n()
            app.action_accept_all()
            await pilot.pause()
            assert app.engine.column_draft == {"Status", "Flag"}
            assert app.engine.pending_cells_n() == pending
            assert app.place.screen == "pair_list"
            assert app.tui_error and "column draft" in app.tui_error

    asyncio.run(_run())


def test_context_modal_q_does_not_quit(tmp_path: Path):
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
            app.action_context()
            await pilot.pause()
            assert isinstance(app.screen, ContextModal)
            await pilot.press("q")
            await pilot.pause()
            assert isinstance(app.screen, ContextModal)
            await pilot.press("a")
            await pilot.pause()
            assert isinstance(app.screen, ContextModal)
            assert app.engine.pending_cells_n() == 2

    asyncio.run(_run())


def test_A_on_zero_pending_pair_list_stays(tmp_path: Path):
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
            pending = app.engine.pending_total()
            app.action_accept_all()
            await pilot.pause()
            assert app.place.screen == "pair_list"
            assert app.place.column == "Status"
            assert app.engine.pending_total() == pending
            assert app.tui_error and "pending" in app.tui_error
            assert next(r for r in app.engine.roster() if r.name == "Flag").pending == 1

    asyncio.run(_run())


def test_U_refused_during_pair_draft(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n2,N\n")
    write_csv(pb, "id,val\n1,Yes\n2,No\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.engine.accept_pair("val", "N", "No")
            n = app.engine.start_pair_draft("val", "Y", "Yes")
            assert n == 1
            app.place.screen = "cell_step"
            app.place.column = "val"
            app.place.pair_val_a = "Y"
            app.place.pair_val_b = "Yes"
            app.render_all()
            await pilot.pause()
            pending = app.engine.pending_cells_n()
            accepted = app.engine.accepted_cells.height
            app.action_undo_column()
            await pilot.pause()
            assert app.engine.pair_draft_col == "val"
            assert app.engine.pending_cells_n() == pending
            assert app.engine.accepted_cells.height == accepted
            assert app.tui_error and "pair draft" in app.tui_error

    asyncio.run(_run())


def test_uncheck_last_column_cancels_draft(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag\n1,Y,1\n")
    write_csv(pb, "id,Status,Flag\n1,Yes,2\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.engine.start_regex_draft("Status")
            app.place.focused_name = "Status"
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            pending = app.engine.pending_cells_n()
            app.action_toggle()
            await pilot.pause()
            assert not app.engine.column_draft
            assert app.tui_error and "draft empty" in app.tui_error
            app.action_confirm()
            await pilot.pause()
            assert app.engine.pending_cells_n() == pending
            assert app.place.screen == "roster"

    asyncio.run(_run())


def test_regex_modal_escape_closes_without_app_back(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#grid").focus()
            await pilot.pause()
            await pilot.press("slash")
            await pilot.pause()
            assert isinstance(app.screen, RegexModal)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, RegexModal)
            assert app.place.screen == "roster"
            assert not app.draft_in_flight()

    asyncio.run(_run())


def test_regex_modal_enter_runs(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag\n1,Y,1\n")
    write_csv(pb, "id,Status,Flag\n1,Yes,2\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#grid").focus()
            await pilot.pause()
            await pilot.press("slash")
            await pilot.pause()
            assert isinstance(app.screen, RegexModal)
            app.screen.query_one("#pat").value = "Status"
            await pilot.press("enter")
            await pilot.pause()
            assert not isinstance(app.screen, RegexModal)
            assert app.place.screen == "roster"
            assert app.engine.column_draft == {"Status"}

    asyncio.run(_run())


def test_help_escape_closes_help_stays_roster(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.place.screen == "roster"
            await pilot.press("question_mark")
            await pilot.pause()
            assert isinstance(app.screen, HelpModal)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, HelpModal)
            assert app.place.screen == "roster"

    asyncio.run(_run())


def test_context_escape_closes_picker_keeps_cell_step_draft(tmp_path: Path):
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
            await pilot.press("c")
            await pilot.pause()
            assert isinstance(app.screen, ContextModal)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, ContextModal)
            assert app.place.screen == "cell_step"
            assert app.engine.pair_draft_col == "Status"

    asyncio.run(_run())


def test_context_modal_space_toggles_without_typeerror(tmp_path: Path):
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
            await pilot.press("c")
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, ContextModal)
            assert "Flag" not in modal.selected
            await pilot.press("space")
            await pilot.pause()
            assert isinstance(app.screen, ContextModal)
            assert "Flag" in modal.selected
            await pilot.press("space")
            await pilot.pause()
            assert "Flag" not in modal.selected

    asyncio.run(_run())
