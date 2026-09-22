import asyncio
from pathlib import Path

from textual.containers import ScrollableContainer
from textual.widgets import Button, Input, Static

from reconcile.engine import Engine, Place
from reconcile.tui import (
    HELP,
    ContextModal,
    HelpModal,
    MultiPairModal,
    OverviewModal,
    ReconcileApp,
    RegexModal,
    SentinelModal,
    _diff_text,
    _display_text,
    select_after_accept,
)
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
            await pilot.press("i")
            await pilot.pause()
            assert isinstance(app.screen, OverviewModal)
            await pilot.press("escape")
            await pilot.pause()
            assert app.place.screen == "roster"
            assert not isinstance(app.screen, OverviewModal)
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
            assert "? help" in footer
            assert "Enter cells" not in footer
            assert "a cell" not in footer

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


def test_square_brackets_cycle_column_tabs(tmp_path: Path):
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
            await pilot.press("]")
            await pilot.pause()
            assert app.place.screen == "accepted"
            assert app.place.view_tab == "accepted"
            await pilot.press("]")
            await pilot.pause()
            assert app.place.screen == "equal"
            await pilot.press("]")
            await pilot.pause()
            assert app.place.screen == "all_matched"
            await pilot.press("]")
            await pilot.pause()
            assert app.place.screen == "all_matched"
            assert app.tui_error and "last tab" in app.tui_error
            await pilot.press("[")
            await pilot.pause()
            assert app.place.screen == "equal"
            await pilot.press("[")
            await pilot.pause()
            assert app.place.screen == "accepted"
            await pilot.press("[")
            await pilot.pause()
            assert app.place.screen == "pair_list"
            assert app.place.view_tab == "pending"
            await pilot.press("[")
            await pilot.pause()
            assert app.place.screen == "pair_list"
            assert app.tui_error and "first tab" in app.tui_error

    asyncio.run(_run())


def test_square_brackets_error_on_roster(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#grid").focus()
            await pilot.press("]")
            await pilot.pause()
            assert app.place.screen == "roster"
            assert app.tui_error and "column tabs" in app.tui_error
            await pilot.press("[")
            await pilot.pause()
            assert app.place.screen == "roster"
            assert app.tui_error and "column tabs" in app.tui_error

    asyncio.run(_run())


def test_square_brackets_refused_during_pair_draft(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
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
            app.query_one("#grid").focus()
            await pilot.press("]")
            await pilot.pause()
            assert app.place.screen == "cell_step"
            assert app.engine.pair_draft_col == "val"
            assert app.tui_error and "pair draft" in app.tui_error
            await pilot.press("[")
            await pilot.pause()
            assert app.place.screen == "cell_step"
            assert app.engine.pair_draft_col == "val"
            assert app.tui_error and "pair draft" in app.tui_error

    asyncio.run(_run())


def test_help_says_exact_sentinel_not_polars_selector():
    assert "Polars selector" not in HELP
    assert "exact sentinel" in HELP
    assert "regex column draft" in HELP
    assert "a/b side" in HELP or "a A or b B" in HELP


def test_help_xor_and_no_digit_tab_keys():
    assert "column XOR pair" in HELP
    assert "No keys 1–4" in HELP or "No keys 1-4" in HELP
    assert "0-9" in HELP
    assert "context picker" in HELP.lower() or "CONTEXT PICKER" in HELP
    assert "this page" in HELP.lower()
    assert ":" in HELP
    assert "[" in HELP
    assert "]" in HELP
    xor = next(line for line in HELP.splitlines() if "At most one draft" in line)
    assert "/" in xor
    assert "=" in xor
    assert ":" in xor
    assert "Y" in xor
    lower = HELP.lower()
    assert "1 pending" not in lower
    assert "2 accepted" not in lower
    assert "3 equal" not in lower
    assert "4 all" not in lower
    assert "--session" not in HELP
    assert "recon.zip" not in HELP.lower()
    assert "export .recon" not in HELP.lower()
    assert "open zip" not in HELP.lower()


def test_help_power_user_grain():
    assert "overview modal" in HELP.lower()
    assert "pair list only" in HELP or "pair-list only" in HELP
    assert "column detail only" in HELP
    assert "column roster" in HELP.lower()
    assert "refused while a pair draft" in HELP
    assert "undo last accept" in HELP
    assert "y accept" in HELP
    assert "current selection" in HELP.lower()
    assert "v      roster" in HELP or "v shows" in HELP.lower() or "show/hide accepted" in HELP.lower()
    assert "in place" in HELP.lower()
    assert "same exact pair" in HELP.lower() or "m      accept" in HELP
    assert "same as a (one column)" not in HELP
    assert "Roster A is ERROR" in HELP or "on the roster use a" in HELP
    assert "no draft to confirm" in HELP
    assert "next sheet" in HELP
    assert "-sheets" in HELP
    assert "y = all pending cells" in HELP
    assert "money" in HELP
    assert "xlsdate" in HELP
    assert "fold" in HELP
    assert "Y      draft" in HELP or "Y all y" in HELP
    assert "focused check" in HELP


def test_roster_pane_hides_yn_legend(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,a\n2,b\n")
    write_csv(pb, "id,val\n1,c\n2,d\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            pane = app.query_one("#pane")
            assert not pane.has_class("hidden")
            text = str(pane.render())
            assert "y = all pending" not in text
            assert "A:" in text and "a" in text
            assert "B:" in text and "c" in text
            assert "1 of 2 pending" in text
            assert "Enter this pair" in text
            assert "a this column" in text

    asyncio.run(_run())


def test_roster_pane_keeps_sentinel_value(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,0\n2,0\n")
    write_csv(pb, "id,val\n1,1\n2,2\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            pane = app.query_one("#pane")
            assert not pane.has_class("hidden")
            text = str(pane.render())
            assert "const A:" in text
            assert "0" in text
            assert "y = all pending" not in text
            assert "1 of 2 pending" in text
            assert "Enter this pair" in text

    asyncio.run(_run())


def test_roster_pane_largest_pair_enter_keeps_list_order(tmp_path: Path):
    """Largest pending pair is in the pane; Enter focuses it; list order stays count desc."""
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,aa\n2,aa\n3,aa\n4,zz\n")
    write_csv(pb, "id,val\n1,AA\n2,AA\n3,AA\n4,ZZ\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    assert eng.top_pending_pair("val") == ("aa", "AA", 3)
    assert eng.pair_groups("val").to_dicts() == [
        {"val_a": "aa", "val_b": "AA", "n": 3},
        {"val_a": "zz", "val_b": "ZZ", "n": 1},
    ]
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            pane = str(app.query_one("#pane").render())
            assert "A:" in pane and "aa" in pane
            assert "B:" in pane and "AA" in pane
            assert "3 of 4 pending" in pane
            assert "Enter this pair" in pane
            assert "zz" not in pane
            assert "largest pending pair" in HELP
            app.query_one("#grid").focus()
            await pilot.press("enter")
            await pilot.pause()
            assert app.place.screen == "pair_list"
            assert app.place.pair_val_a == "aa"
            assert app.place.pair_val_b == "AA"
            assert app.place.page == 0
            table = app.query_one("#grid")
            assert table.cursor_row == 0
            assert app._table_keys[0]["val_a"] == "aa"
            assert app._table_keys[0]["val_b"] == "AA"
            assert app._table_keys[1]["val_a"] == "zz"
            assert app._table_keys[1]["n"] == 1
            assert eng.pair_groups("val").to_dicts() == [
                {"val_a": "aa", "val_b": "AA", "n": 3},
                {"val_a": "zz", "val_b": "ZZ", "n": 1},
            ]

    asyncio.run(_run())


def test_roster_pane_follows_column_and_a_accepts_in_place(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,alpha,beta\n1,aa,ww\n2,aa,yy\n3,aa,zz\n4,zz,xx\n")
    write_csv(pb, "id,alpha,beta\n1,AA,WW\n2,AA,YY\n3,AA,ZZ\n4,ZZ,XX\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    assert eng.top_pending_pair("alpha") == ("aa", "AA", 3)
    assert eng.top_pending_pair("beta") == ("ww", "WW", 1)
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#grid").focus()
            await pilot.pause()
            pane = str(app.query_one("#pane").render())
            assert "aa" in pane and "3 of 4 pending" in pane
            await pilot.press("down")
            await pilot.pause()
            row = app._focused_roster()
            assert row is not None and row.name == "beta"
            pane = str(app.query_one("#pane").render())
            assert "ww" in pane
            assert "WW" in pane
            assert "1 of 4 pending" in pane
            assert "3 of 4 pending" not in pane
            pending_before = app.engine.pending_cells_n()
            await pilot.press("a")
            await pilot.pause()
            assert app.place.screen == "roster"
            assert app.engine.pending_cells_n() == pending_before - 4
            assert next(r for r in app.engine.roster() if r.name == "beta").pending == 0
            assert next(r for r in app.engine.roster() if r.name == "alpha").pending == 4

    asyncio.run(_run())


def test_visible_glyphs_and_first_diff():
    assert _display_text("") == "(empty)"
    assert _display_text("a ") == "a·"
    assert _display_text(" a") == "·a"
    assert _display_text("a b") == "a b"
    assert _display_text("a\u00a0b") == "a␣b"
    assert _display_text("\t") == "→"
    assert _display_text("a\nb") == "a↵b"
    marked = _diff_text("A:", "ab", "ac", 1)
    assert marked.plain == "A: ab"
    assert any(span.start == 4 and "reverse" in str(span.style) for span in marked.spans)


def test_roster_pane_one_pair_names_the_grain(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
    app = ReconcileApp(Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=","))

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            pane = str(app.query_one("#pane").render())
            assert "this column is one pair" in pane
            assert "1 pending" in pane
            assert "Enter this pair" in pane
            assert "a this column" in pane

    asyncio.run(_run())


def test_roster_pane_shows_invisible_characters(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,a \n")
    write_csv(pb, "id,val\n1,a\u00a0\n")
    app = ReconcileApp(Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=","))

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            pane = str(app.query_one("#pane").render())
            assert "·" in pane
            assert "␣" in pane

    asyncio.run(_run())


def test_empty_roster_names_remaining_noun(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,a\n")
    write_csv(pb, "id,val\n1,a\n")
    app = ReconcileApp(Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=","))

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            pane = str(app.query_one("#pane").render())
            assert pane.strip() == "pending is 0"

    asyncio.run(_run())


def test_unmatched_pane_leads_with_key(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,year,val\n1,2020,only\n")
    write_csv(pb, "id,year,val\n")
    app = ReconcileApp(Engine.from_paths(str(pa), str(pb), ["id", "year"], a_delim=",", b_delim=","))

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.place.screen = "a_only"
            app.render_all()
            await pilot.pause()
            pane = str(app.query_one("#pane").render())
            assert pane.startswith("A-only key")
            assert "id: 1" in pane
            assert "year: 2020" in pane
            assert "a this key · A this side" in pane
            assert pane.index("id: 1") < pane.index("val:")

    asyncio.run(_run())


def test_cell_step_pane_leads_with_key_and_checked(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n2,Y\n")
    write_csv(pb, "id,val\n1,Yes\n2,Yes\n")
    app = ReconcileApp(Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=","))

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.engine.start_pair_draft("val", "Y", "Yes")
            app.place.screen = "cell_step"
            app.place.column = "val"
            app.place.pair_val_a = "Y"
            app.place.pair_val_b = "Yes"
            app.place.focused_key = ("1",)
            app.render_all()
            await pilot.pause()
            pane = str(app.query_one("#pane").render())
            assert pane.startswith("id 1")
            assert "2 of 2 checked · a this cell · y confirm" in pane
            footer = str(app.query_one("#footer").render())
            assert "a this cell" not in footer

    asyncio.run(_run())


def test_extras_pane_names_suggest_recipe(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val,Status Code\n1,Y,a\n")
    write_csv(pb, "id,val,status_code\n1,Yes,b\n")
    app = ReconcileApp(Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=","))

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.place.screen = "extras"
            app.render_all()
            await pilot.pause()
            pane = str(app.query_one("#pane").render())
            assert "Suggest:" in pane
            assert "Status Code" in pane
            assert "status_code" in pane
            assert "rename then r" in pane
            assert "a this extra" in pane

    asyncio.run(_run())


def test_pair_list_pane_names_share_and_keys(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,aa\n2,aa\n3,aa\n4,zz\n")
    write_csv(pb, "id,val\n1,AA\n2,AA\n3,AA\n4,ZZ\n")
    app = ReconcileApp(Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=","))

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_drill()
            await pilot.pause()
            pane = str(app.query_one("#pane").render())
            assert "3 of 4 pending · a this pair · A this column · Enter cells" in pane

    asyncio.run(_run())


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
            banner = str(app.query_one("#banner").render())
            assert "2 column" in banner
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
            assert app.tui_error and "column draft" in app.tui_error
            assert "draft stays" not in str(app.query_one("#footer").render())
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
            banner = str(app.query_one("#banner").render())
            assert "draft 2 cells" in banner
            assert "y accept" in banner
            footer = str(app.query_one("#footer").render())
            assert "? help" in footer
            assert "U column" not in footer
            assert "cell step" not in footer

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


def test_roster_a_stays_on_roster_after_accepting_one_of_two(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag\n1,Y,1\n2,Y,1\n")
    write_csv(pb, "id,Status,Flag\n1,Yes,2\n2,Yes,1\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.place.focused_name = "Flag"
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            table = app.query_one("#grid")
            names = [r.name for r in app._table_keys if r is not None]
            flag_i = names.index("Flag")
            table.move_cursor(row=flag_i)
            row = app._focused_roster()
            assert row is not None and row.name == "Flag"
            app.action_accept()
            await pilot.pause()
            assert app.place.screen == "roster"
            assert next(r for r in app.engine.roster() if r.name == "Flag").pending == 0
            assert next(r for r in app.engine.roster() if r.name == "Status").pending > 0

    asyncio.run(_run())


def test_roster_A_errors_does_not_accept(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag\n1,Y,1\n")
    write_csv(pb, "id,Status,Flag\n1,Yes,2\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            pending = app.engine.pending_cells_n()
            app.query_one("#grid").focus()
            await pilot.press("A")
            await pilot.pause()
            assert app.place.screen == "roster"
            assert app.engine.pending_cells_n() == pending
            assert app.tui_error and "on the roster use a" in app.tui_error
            assert next(r for r in app.engine.roster() if r.name == "Status").pending > 0

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
            app.place.screen = "extras"
            app.place.extra_name = "cust"
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            rec = app._focused_rec()
            assert rec is not None and rec["name"] == "cust"
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
            assert app.place.screen == "roster"
            assert app.place.focused_name == "Flag"
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
            assert app.place.screen == "roster"
            assert app.engine.pending_cells_n() == 0

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
            assert not isinstance(app.focused, Input)
            modal.action_ok()
            await pilot.pause()
            assert isinstance(app.screen, SentinelModal)
            assert "ERROR" in str(modal.query_one("#modal-err").render())
            assert not app.draft_in_flight()
            await pilot.press("a")
            await pilot.pause()
            assert modal._side == "A"
            modal.query_one("#sentinel").value = "NA"
            modal.action_ok()
            await pilot.pause()
            assert not isinstance(app.screen, SentinelModal)
            assert app.engine.column_draft == {"s"}

    asyncio.run(_run())


def test_sentinel_b_selects_side_b(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,s\n1,x\n2,y\n")
    write_csv(pb, "id,s\n1,NA\n2,NA\n")
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
            await pilot.press("b")
            await pilot.pause()
            assert modal._side == "B"
            modal.query_one("#sentinel").value = "NA"
            modal.action_ok()
            await pilot.pause()
            assert not isinstance(app.screen, SentinelModal)
            assert app.engine.column_draft == {"s"}

    asyncio.run(_run())


def test_sentinel_input_letter_a_is_literal(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,s\n1,x\n2,y\n")
    write_csv(pb, "id,s\n1,NA\n2,NA\n")
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
            await pilot.press("b")
            await pilot.pause()
            assert modal._side == "B"
            inp = modal.query_one("#sentinel")
            assert app.focused is inp
            await pilot.press("N", "A")
            await pilot.pause()
            assert inp.value == "NA"
            assert modal._side == "B"
            assert isinstance(app.screen, SentinelModal)

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
            assert app.place.screen == "roster"
            assert app.place.focused_name == "Flag"
            assert next(r for r in app.engine.roster() if r.name == "Status").pending == 0
            app.action_undo()
            await pilot.pause()
            assert app.place.screen == "pair_list"
            assert app.place.column == "Status"
            assert next(r for r in app.engine.roster() if r.name == "Status").pending == 2
            assert app.engine.last_grain is None

    asyncio.run(_run())


def test_overview_is_modal_not_a_screen(tmp_path: Path):
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
            assert app.place.screen == "roster"
            assert not isinstance(app.screen, OverviewModal)
            app.action_overview()
            await pilot.pause()
            assert isinstance(app.screen, OverviewModal)
            assert app.place.screen == "roster"
            await pilot.press("escape")
            await pilot.pause()
            assert app.place.screen == "roster"
            assert not isinstance(app.screen, OverviewModal)

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


def test_zero_pending_column_is_hidden_on_roster(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,ok\n1,Y,same\n")
    write_csv(pb, "id,Status,ok\n1,Yes,same\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            names = [r.name for r in app.engine.visible_column_roster()]
            assert "Status" in names
            assert "ok" not in names
            shown = [r.name for r in app._table_keys if r is not None]
            assert "Status" in shown
            assert "ok" not in shown

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


def test_pair_list_place_focuses_pair(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n2,Y\n3,N\n")
    write_csv(pb, "id,val\n1,Yes\n2,Yes\n3,No\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    place = Place(
        screen="pair_list",
        column="val",
        last_pair=("val", "N", "No"),
        pair_val_a="N",
        pair_val_b="No",
    )
    app = ReconcileApp(eng, place)

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


def test_a_only_place_kept_even_if_last_pair_exists(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n9,onlyA\n")
    write_csv(pb, "id,val\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    last = ("val", "Y", "Yes")
    app = ReconcileApp(
        eng, Place(screen="a_only", last_pair=last, focused_key=("9",))
    )

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.place.screen == "a_only"
            assert app.place.last_pair == last
            assert app.engine.pending_a_only_n() == 1
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
            assert app.place.screen == "roster"
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
            assert app.place.screen == "roster"
            assert app.place.focused_name == "Flag"
            app.place.screen = "pair_list"
            app.place.column = "Status"
            app.render_all()
            await pilot.pause()
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


def test_U_from_pair_list_undoes_column(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n2,N\n")
    write_csv(pb, "id,val\n1,Yes\n2,No\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.engine.accept_pair("val", "N", "No")
            app.place.screen = "pair_list"
            app.place.column = "val"
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            assert app.engine.accepted_cells.height == 1
            await pilot.press("U")
            await pilot.pause()
            assert app.engine.accepted_cells.height == 0
            assert next(r for r in app.engine.roster() if r.name == "val").pending == 2
            assert app.place.screen == "pair_list"
            assert app.engine.pair_draft_col is None
            footer = str(app.query_one("#footer").render())
            assert "? help" in footer
            assert "U column" not in footer

    asyncio.run(_run())


def test_cell_step_footer_does_not_advertise_U(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
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
            assert "U column" not in footer
            banner = str(app.query_one("#banner").render())
            assert "y accept" in banner

    asyncio.run(_run())


def test_cell_a_after_uncheck_draft_n_matches_remaining(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n2,Y\n3,Y\n")
    write_csv(pb, "id,val\n1,Yes\n2,Yes\n3,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            n = app.engine.start_pair_draft("val", "Y", "Yes")
            assert n == 3
            app.place.screen = "cell_step"
            app.place.column = "val"
            app.place.pair_val_a = "Y"
            app.place.pair_val_b = "Yes"
            app.place.focused_key = ("1",)
            app.pair_draft_unchecked = {("1",)}
            app.render_all()
            await pilot.pause()
            banner = str(app.query_one("#banner").render())
            assert "draft 2" in banner
            app.query_one("#grid").focus()
            await pilot.press("a")
            await pilot.pause()
            assert ("1",) not in app.pair_draft_unchecked
            assert app.engine.pair_draft_height() == 2
            banner = str(app.query_one("#banner").render())
            assert "draft 2" in banner
            assert app.engine.pending_cells_n() == 2

    asyncio.run(_run())


def test_pair_list_footer_has_repeat_and_undo_only(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.place.screen = "pair_list"
            app.place.column = "val"
            app.render_all()
            await pilot.pause()
            footer = str(app.query_one("#footer").render())
            assert ". repeat" in footer
            assert "u undo" in footer
            assert "? help" in footer
            assert "y ACCEPT" not in footer
            assert "Enter" not in footer
            assert "U column" not in footer

    asyncio.run(_run())


def test_help_modal_not_footer_cheat_sheet(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            footer = str(app.query_one("#footer").render())
            assert "? help" in footer
            assert "Enter drill" not in footer
            assert "regex column draft" not in footer
            assert len(app.query("#filter")) == 0
            await pilot.press("question_mark")
            await pilot.pause()
            assert isinstance(app.screen, HelpModal)
            body = str(app.screen.query_one("#help").render())
            assert "ROSTER" in body
            assert "PAIR LIST" in body
            assert "CELL STEP" in body
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, HelpModal)
            assert app.place.screen == "roster"

    asyncio.run(_run())


def test_help_scrolls_with_down(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            await pilot.press("question_mark")
            await pilot.pause()
            assert isinstance(app.screen, HelpModal)
            scroll = app.screen.query_one("#help-scroll", ScrollableContainer)
            before = scroll.scroll_offset.y
            await pilot.press("down")
            await pilot.pause()
            assert scroll.scroll_offset.y > before

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


def test_y_with_no_draft_errors(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#grid").focus()
            pending = app.engine.pending_cells_n()
            await pilot.press("y")
            await pilot.pause()
            assert app.place.screen == "roster"
            assert not app.engine.column_draft
            assert app.engine.pair_draft_col is None
            assert app.engine.pending_cells_n() == pending
            assert app.tui_error and "no draft to confirm" in app.tui_error
            app.action_drill()
            await pilot.pause()
            assert app.place.screen == "pair_list"
            await pilot.press("y")
            await pilot.pause()
            assert app.place.screen == "pair_list"
            assert app.engine.pair_draft_col is None
            assert app.engine.pending_cells_n() == pending
            assert app.tui_error and "no draft to confirm" in app.tui_error

    asyncio.run(_run())


def test_esc_from_pair_list_cancels_column_draft(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag\n1,Y,1\n")
    write_csv(pb, "id,Status,Flag\n1,Yes,2\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.engine.start_regex_draft("Status|Flag")
            assert app.engine.column_draft == {"Status", "Flag"}
            app.place.focused_name = "Status"
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            app.action_drill()
            await pilot.pause()
            assert app.place.screen == "pair_list"
            assert app.engine.column_draft == {"Status", "Flag"}
            await pilot.press("escape")
            await pilot.pause()
            assert app.place.screen == "roster"
            assert not app.engine.column_draft

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


def test_regex_modal_unfocused_a_does_not_accept(tmp_path: Path):
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
            pending = app.engine.pending_cells_n()
            await pilot.press("slash")
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, RegexModal)
            modal.query_one("#pat").blur()
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            assert isinstance(app.screen, RegexModal)
            assert app.place.screen == "roster"
            assert not app.engine.column_draft
            assert app.engine.pending_cells_n() == pending
            assert app.engine.accepted_cells.height == 0

    asyncio.run(_run())


def test_colon_opens_regex_modal(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status\n1,Y\n")
    write_csv(pb, "id,Status\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#grid").focus()
            await pilot.pause()
            await pilot.press("colon")
            await pilot.pause()
            assert isinstance(app.screen, RegexModal)
            app.screen.query_one("#pat").value = "Status"
            await pilot.press("enter")
            await pilot.pause()
            assert not isinstance(app.screen, RegexModal)
            assert app.place.screen == "roster"
            assert app.engine.column_draft == {"Status"}

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


def test_context_values_empty_or_wrong_arity_key_is_empty(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag\n1,Y,1\n")
    write_csv(pb, "id,Status,Flag\n1,Yes,2\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    eng.context_columns["Status"] = ["Flag"]
    assert eng.context_values(None, "Status") == []
    assert eng.context_values((), "Status") == []
    assert eng.context_values(("1", "extra"), "Status") == []
    got = eng.context_values(("1",), "Status")
    assert got == [("Flag", "1", "2")]


def test_cell_step_context_with_focused_key_none_does_not_crash(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag\n1,Y,1\n2,Y,3\n")
    write_csv(pb, "id,Status,Flag\n1,Yes,2\n2,Yes,4\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    eng.context_columns["Status"] = ["Flag"]
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            n = app.engine.start_pair_draft("Status", "Y", "Yes")
            assert n == 2
            app.place.screen = "cell_step"
            app.place.column = "Status"
            app.place.pair_val_a = "Y"
            app.place.pair_val_b = "Yes"
            app.place.focused_key = None
            app.render_all()
            await pilot.pause()
            assert app.place.screen == "cell_step"
            pane = str(app.query_one("#pane").render())
            assert "A:" in pane

    asyncio.run(_run())


def test_np_on_cell_step_with_context_does_not_crash(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag\n" + "".join(f"{i:03d},Y,A{i:03d}\n" for i in range(120)))
    write_csv(pb, "id,Status,Flag\n" + "".join(f"{i:03d},Yes,B{i:03d}\n" for i in range(120)))
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    eng.context_columns["Status"] = ["Flag"]
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            n = app.engine.start_pair_draft("Status", "Y", "Yes")
            assert n == 120
            app.place.screen = "cell_step"
            app.place.column = "Status"
            app.place.pair_val_a = "Y"
            app.place.pair_val_b = "Yes"
            app.place.focused_key = ("000",)
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            await pilot.press("n")
            await pilot.pause()
            assert app.place.screen == "cell_step"
            assert app.place.focused_key is None
            pane = str(app.query_one("#pane").render())
            assert "A:" in pane
            await pilot.press("p")
            await pilot.pause()
            assert app.place.screen == "cell_step"
            assert app.place.page == 0

    asyncio.run(_run())


def test_enter_cell_step_sets_focused_key(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag\n1,Y,1\n2,Y,3\n")
    write_csv(pb, "id,Status,Flag\n1,Yes,2\n2,Yes,4\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#grid").focus()
            app.action_drill()
            await pilot.pause()
            assert app.place.screen == "pair_list"
            await pilot.press("enter")
            await pilot.pause()
            assert app.place.screen == "cell_step"
            assert app.place.focused_key == ("1",)
            assert app.engine.pair_draft_col == "Status"

    asyncio.run(_run())


def test_overview_a_shows_error(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#grid").focus()
            app.action_overview()
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, OverviewModal)
            pending = app.engine.pending_total()
            await pilot.press("a")
            await pilot.pause()
            assert isinstance(app.screen, OverviewModal)
            assert app.place.screen == "roster"
            assert app.engine.pending_total() == pending
            err = str(modal.query_one("#modal-err").render())
            assert "ERROR" in err
            assert "overview is counts only" in err
            assert "Pending" not in err
            await pilot.press("A")
            await pilot.pause()
            assert isinstance(app.screen, OverviewModal)
            assert app.place.screen == "roster"
            assert app.engine.pending_total() == pending
            err = str(modal.query_one("#modal-err").render())
            assert "ERROR" in err

    asyncio.run(_run())


def test_n_on_roster_and_overview_does_not_bump_page(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#grid").focus()
            assert app.place.page == 0
            await pilot.press("n")
            await pilot.pause()
            assert app.place.screen == "roster"
            assert app.place.page == 0
            assert app.tui_error and "ERROR" in app.tui_error
            await pilot.press("escape")
            await pilot.pause()
            assert app.place.screen == "roster"
            app.action_overview()
            await pilot.pause()
            assert isinstance(app.screen, OverviewModal)
            await pilot.press("n")
            await pilot.pause()
            assert isinstance(app.screen, OverviewModal)
            assert app.place.screen == "roster"
            assert app.place.page == 0
            err = str(app.screen.query_one("#modal-err").render())
            assert "ERROR" in err

    asyncio.run(_run())


def test_last_page_n_stays_no_wrap(tmp_path: Path):
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
            app.query_one("#grid").focus()
            await pilot.press("n")
            await pilot.pause()
            assert app.place.page == 1
            footer = str(app.query_one("#footer").render())
            assert "page 2/2" in footer
            await pilot.press("n")
            await pilot.pause()
            assert app.place.page == 1
            assert app.place.screen == "cell_step"
            assert app.tui_error and "last page" in app.tui_error

    asyncio.run(_run())


def test_dot_from_other_column_pair_list_focuses_last_pair(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag\n1,Y,1\n2,N,1\n")
    write_csv(pb, "id,Status,Flag\n1,Yes,2\n2,No,2\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.place.last_pair = ("Status", "Y", "Yes")
            app.place.screen = "pair_list"
            app.place.column = "Flag"
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            await pilot.press(".")
            await pilot.pause()
            assert app.place.screen == "pair_list"
            assert app.place.column == "Status"
            assert app.place.pair_val_a == "Y"
            assert app.place.pair_val_b == "Yes"
            assert app.engine.pair_draft_col is None
            pane = str(app.query_one("#pane").render())
            assert "Y" in pane
            assert "Yes" in pane

    asyncio.run(_run())


def test_dot_on_same_column_pair_list_starts_cell_step(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n2,N\n")
    write_csv(pb, "id,val\n1,Yes\n2,No\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.place.last_pair = ("val", "Y", "Yes")
            app.place.screen = "pair_list"
            app.place.column = "val"
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            await pilot.press(".")
            await pilot.pause()
            assert app.place.screen == "cell_step"
            assert app.engine.pair_draft_col == "val"
            assert app.place.pair_val_a == "Y"
            assert app.place.pair_val_b == "Yes"

    asyncio.run(_run())


def test_empty_pair_values_show_empty_marker(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,\n")
    write_csv(pb, "id,val\n1,x\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.place.screen = "pair_list"
            app.place.column = "val"
            app.render_all()
            await pilot.pause()
            table = app.query_one("#grid")
            row = table.get_row_at(0)
            assert "(empty)" in str(row[0])
            pane = str(app.query_one("#pane").render())
            assert "(empty)" in pane

    asyncio.run(_run())


def test_enter_on_extra_from_roster_does_not_crash(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val,cust\n1,a,1\n")
    write_csv(pb, "id,val\n1,a\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_overview()
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, OverviewModal)
            modal.query_one("#ov-entries").move_cursor(row=2)
            modal.action_pick()
            await pilot.pause()
            assert app.place.screen == "extras"
            assert app.query("#grid")
            rec = app._focused_rec()
            assert rec is not None and rec["name"] == "cust"
            await pilot.press("escape")
            await pilot.pause()
            assert app.place.screen == "roster"
            names = [r.name for r in app.engine.visible_column_roster()]
            assert "cust" not in names
            assert "A-only keys" not in names

    asyncio.run(_run())


def test_enter_on_zzz_extra_then_a_snaps_zzz_not_aaa(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val,aaa,zzz\n1,a,1,2\n")
    write_csv(pb, "id,val\n1,a\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.place.screen = "extras"
            app.place.extra_name = "zzz"
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            rec = app._focused_rec()
            assert rec is not None and rec["name"] == "zzz"
            await pilot.press("a")
            await pilot.pause()
            assert ("A", "zzz") in app.engine.accepted_extras
            assert ("A", "aaa") in app.engine.pending_extras

    asyncio.run(_run())


def test_extras_place_focus(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val,aaa,zzz\n1,a,1,2\n")
    write_csv(pb, "id,val\n1,a\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(
        eng, Place(screen="extras", extra_side="A", extra_name="zzz")
    )

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.place.screen == "extras"
            assert app.place.extra_name == "zzz"
            rec = app._focused_rec()
            assert rec is not None and rec["name"] == "zzz" and rec["side"] == "A"

    asyncio.run(_run())


def test_e_and_o_do_not_open_zip(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.place.screen == "roster"
            await pilot.press("e")
            await pilot.pause()
            assert app.place.screen == "roster"
            assert app.screen.id == "_default"
            await pilot.press("o")
            await pilot.pause()
            assert app.place.screen == "roster"
            assert app.screen.id == "_default"
            assert not hasattr(app, "action_export")
            assert not hasattr(app, "action_open_zip")

    asyncio.run(_run())


def test_p_on_first_page_after_a_keeps_next_cell(tmp_path: Path):
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
            app.place.focused_key = ("050",)
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            await pilot.press("a")
            await pilot.pause()
            assert app.place.focused_key == ("051",)
            assert app._focused_rec()["id"] == "051"
            await pilot.press("p")
            await pilot.pause()
            assert app.place.page == 0
            assert app.place.screen == "cell_step"
            assert app.tui_error and "first page" in app.tui_error
            assert app.place.focused_key == ("051",)
            assert app._focused_rec()["id"] == "051"

    asyncio.run(_run())


def test_a_and_enter_on_empty_pair_list_error(tmp_path: Path):
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
            await pilot.press("a")
            await pilot.pause()
            assert app.place.screen == "roster"
            assert app.place.focused_name == "Flag"
            app.place.screen = "pair_list"
            app.place.column = "Status"
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            pending = app.engine.pending_total()
            await pilot.press("a")
            await pilot.pause()
            assert app.place.screen == "pair_list"
            assert app.place.column == "Status"
            assert app.engine.pending_total() == pending
            assert app.tui_error and "pending" in app.tui_error
            await pilot.press("enter")
            await pilot.pause()
            assert app.place.screen == "pair_list"
            assert app.engine.pair_draft_col is None
            assert app.tui_error and "pending" in app.tui_error

    asyncio.run(_run())


def test_a_on_accepted_unmatched_key_errors(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,a\n2,b\n")
    write_csv(pb, "id,val\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.place.screen = "a_only"
            app.place.focused_name = "A-only keys"
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            assert app.place.screen == "a_only"
            await pilot.press("a")
            await pilot.pause()
            assert app.engine.pending_a_only_n() == 1
            grain = app.engine.last_grain
            app.query_one("#grid").move_cursor(row=0)
            await pilot.pause()
            rec = app._focused_rec()
            assert rec is not None and rec.get("_accepted")
            await pilot.press("a")
            await pilot.pause()
            assert app.place.screen == "a_only"
            assert app.engine.pending_a_only_n() == 1
            assert app.engine.last_grain == grain
            assert app.tui_error and "pending" in app.tui_error

    asyncio.run(_run())


def test_context_modal_enter_confirms_selection(tmp_path: Path):
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
            await pilot.press("space")
            await pilot.pause()
            assert "Flag" in modal.selected
            await pilot.press("enter")
            await pilot.pause()
            assert not isinstance(app.screen, ContextModal)
            assert app.place.screen == "cell_step"
            assert app.engine.context_columns.get("Status") == ["Flag"]
            assert app.engine.pair_draft_col == "Status"

    asyncio.run(_run())


def test_roster_hides_accepted_column_and_unmatched_rows(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,ok,cust\n1,Y,same,1\n2,onlyA,same,2\n")
    write_csv(pb, "id,Status,ok\n1,Yes,same\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#grid")
            shown = [r.name for r in app._table_keys if r is not None]
            assert "Status" in shown
            assert "ok" not in shown
            assert not any(r.kind in ("A-only", "B-only", "extra") for r in app._table_keys if r)
            app.place.focused_name = "Status"
            app.render_all()
            await pilot.pause()
            a_only_before = app.engine.pending_a_only_n()
            extras_before = app.engine.pending_extras_n()
            app.query_one("#grid").focus()
            app.action_accept()
            await pilot.pause()
            vis = [r.name for r in app.engine.visible_column_roster()]
            assert "Status" not in vis
            assert app.place.screen == "roster"
            assert app.engine.pending_a_only_n() == a_only_before
            assert app.engine.pending_extras_n() == extras_before
            shown = [r.name for r in app._table_keys if r is not None]
            assert "Status" not in shown

    asyncio.run(_run())


def test_roster_a_accepts_column_not_unmatched_grain(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status\n1,Y\n2,onlyA\n")
    write_csv(pb, "id,Status\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.engine.pending_a_only_n() == 1
            app.query_one("#grid").focus()
            row = app._focused_roster()
            assert row is not None and row.kind == "column" and row.name == "Status"
            app.action_accept()
            await pilot.pause()
            assert app.engine.pending_cells_n() == 0
            assert app.engine.pending_a_only_n() == 1
            assert app.engine.last_grain is not None
            assert app.engine.last_grain[0] == "column"
            assert app.place.screen == "roster"

    asyncio.run(_run())


def test_sentinel_draft_makes_accept_and_toggle_obvious(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,all_a,mixed\n1,——,——\n2,——,x\n")
    write_csv(pb, "id,all_a,mixed\n1,x,y\n2,y,z\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.engine.start_sentinel_draft("A", "——")
            app.render_all()
            await pilot.pause()
            banner = str(app.query_one("#banner").render())
            assert "y accept" in banner
            assert "Space toggle" in banner
            footer = str(app.query_one("#footer").render())
            assert "y accept" not in footer
            assert "a grain" not in footer
            table = app.query_one("#grid")
            marks = [str(table.get_row_at(i)[0]) for i in range(table.row_count)]
            assert any("[ON]" in m for m in marks)
            assert any("[off]" in m for m in marks)
            app.place.focused_name = "mixed"
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            app.action_toggle()
            await pilot.pause()
            assert "mixed" in app.engine.column_draft
            row = next(
                i
                for i, r in enumerate(app.engine.visible_column_roster())
                if r.name == "mixed"
            )
            assert "[ON]" in str(table.get_row_at(row)[0])
            app.place.focused_name = "mixed"
            app.render_all()
            await pilot.pause()
            app.action_toggle()
            await pilot.pause()
            assert "mixed" not in app.engine.column_draft
            banner = str(app.query_one("#banner").render())
            assert "y accept" in banner

    asyncio.run(_run())


def test_overview_enter_opens_a_only(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,a\n2,onlyA\n")
    write_csv(pb, "id,val\n1,a\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            vis = [r.name for r in app.engine.visible_column_roster()]
            assert vis == []
            table = app.query_one("#grid")
            shown = str(table.get_row_at(0)[0])
            assert "No pending columns" in shown
            pane = str(app.query_one("#pane").render())
            assert "unmatched keys 1 — i" in pane
            app.action_overview()
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, OverviewModal)
            body = str(modal.query_one("#overview-body").render())
            assert "A-only keys pending 1" in body
            modal.query_one("#ov-entries").move_cursor(row=0)
            modal.action_pick()
            await pilot.pause()
            assert app.place.screen == "a_only"
            assert app.engine.pending_a_only_n() == 1

    asyncio.run(_run())


def test_roster_a_accepts_column_without_leaving_roster(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag\n1,Y,1\n")
    write_csv(pb, "id,Status,Flag\n1,Yes,2\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.place.screen == "roster"
            app.query_one("#grid").focus()
            focused = app._focused_roster()
            assert focused is not None and focused.kind == "column"
            accepted_name = focused.name
            assert focused.pending > 0
            await pilot.press("a")
            await pilot.pause()
            assert app.place.screen == "roster"
            assert next(r for r in app.engine.roster() if r.name == accepted_name).pending == 0
            shown = [r.name for r in app._table_keys if r is not None]
            assert accepted_name not in shown
            leftover = [r.name for r in app.engine.visible_column_roster()]
            if leftover:
                assert app.place.focused_name == leftover[0]

    asyncio.run(_run())


def test_roster_v_toggles_accepted_columns(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,ok\n1,Y,same\n")
    write_csv(pb, "id,Status,ok\n1,Yes,same\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            shown = [r.name for r in app._table_keys if r is not None]
            assert shown == ["Status"]
            footer = str(app.query_one("#footer").render())
            assert "? help" in footer
            assert "Enter drill" not in footer
            table = app.query_one("#grid")
            labels = [str(col.label) for col in table.columns.values()]
            assert "accepted" not in labels
            assert "accept" not in labels
            assert "status" not in labels
            app.query_one("#grid").focus()
            app.action_accept()
            await pilot.pause()
            assert app.place.screen == "roster"
            assert "Status" not in [r.name for r in app._table_keys if r is not None]
            await pilot.press("v")
            await pilot.pause()
            assert app.show_accepted_columns is True
            names = [r.name for r in app._table_keys if r is not None]
            assert "Status" in names
            assert "ok" in names
            table = app.query_one("#grid")
            labels = [str(col.label) for col in table.columns.values()]
            assert "status" in labels
            assert "accepted" not in labels
            footer = str(app.query_one("#footer").render())
            assert "? help" in footer
            assert "v hide accepted" not in footer
            status_i = labels.index("status")
            by_name = {}
            for i, r in enumerate(app._table_keys):
                if r is not None:
                    by_name[r.name] = str(table.get_row_at(i)[status_i])
            assert by_name["Status"] == "accepted"
            assert by_name["ok"] == "equal"
            await pilot.press("v")
            await pilot.pause()
            assert app.show_accepted_columns is False
            assert "Status" not in [r.name for r in app._table_keys if r is not None]
            assert "ok" not in [r.name for r in app._table_keys if r is not None]

    asyncio.run(_run())


def test_a_accepts_grain_on_pair_cell_unmatched_and_extra(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,cust\n1,Y,1\n2,onlyA,2\n")
    write_csv(pb, "id,Status\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#grid").focus()
            app.action_drill()
            await pilot.pause()
            assert app.place.screen == "pair_list"
            pending_before = app.engine.pending_cells_n()
            await pilot.press("a")
            await pilot.pause()
            assert app.engine.pending_cells_n() == pending_before - 1
            assert app.engine.last_grain is not None
            assert app.engine.last_grain[0] == "pair"

            app.place = Place(screen="cell_step", column="Status", pair_val_a="Y", pair_val_b="Yes")
            # Status already accepted via pair; rebuild a cell-step case on a fresh extra/unmatched.
            app.place = Place(screen="a_only")
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            assert app.engine.pending_a_only_n() == 1
            await pilot.press("a")
            await pilot.pause()
            assert app.engine.pending_a_only_n() == 0
            assert app.engine.last_grain[0] == "unmatched"

            app.place = Place(screen="extras", extra_name="cust", extra_side="A")
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            assert app.engine.pending_extras_n() == 1
            await pilot.press("a")
            await pilot.pause()
            assert app.engine.pending_extras_n() == 0
            assert app.engine.last_grain[0] == "extra"

    asyncio.run(_run())


def test_a_on_cell_step_accepts_cell(tmp_path: Path):
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
            app.query_one("#grid").focus()
            pending_before = app.engine.pending_cells_n()
            await pilot.press("a")
            await pilot.pause()
            assert app.engine.pending_cells_n() == pending_before - 1
            assert app.engine.last_grain is not None
            assert app.engine.last_grain[0] == "cell"
            assert app.place.screen == "cell_step"

    asyncio.run(_run())


def test_footer_counts_are_split_nouns_not_lumped_cells(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,cust\n1,Y,1\n2,onlyA,2\n")
    write_csv(pb, "id,Status\n1,Yes\n3,onlyB\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            footer = str(app.query_one("#footer").render())
            assert "pending columns 1" in footer
            assert "unmatched keys 2" in footer
            assert "mismatched columns 1" in footer
            assert "cells " not in footer
            assert "extras " not in footer
            assert "? help" in footer
            assert "Enter drill" not in footer
            assert "v show accepted" not in footer
            assert "m same pair" not in footer
            app.action_overview()
            await pilot.pause()
            body = str(app.screen.query_one("#overview-body").render())
            assert "mismatched columns pending 1" in body
            assert "extras pending" not in body
            table = app.screen.query_one("#ov-entries")
            labels = [str(table.get_row_at(i)[0]) for i in range(table.row_count)]
            assert "Mismatched columns" in labels
            assert "Schema extras" not in labels

    asyncio.run(_run())


def test_select_after_accept_below_then_last():
    assert select_after_accept(["a", "b", "c"], "a", ["b", "c"]) == "b"
    assert select_after_accept(["a", "b", "c"], "c", ["a", "b"]) == "b"
    assert select_after_accept(["a"], "a", []) is None


def test_roster_a_selects_former_next_below(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag,Note\n1,Y,1,a\n")
    write_csv(pb, "id,Status,Flag,Note\n1,Yes,2,b\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert [r.name for r in app.engine.visible_column_roster()] == [
                "Status",
                "Flag",
                "Note",
            ]
            app.place.focused_name = "Status"
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            app.action_accept()
            await pilot.pause()
            assert app.place.screen == "roster"
            assert app.place.focused_name == "Flag"
            shown = [r.name for r in app._table_keys if r is not None]
            assert shown == ["Flag", "Note"]
            app.place.focused_name = "Note"
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            app.action_accept()
            await pilot.pause()
            assert app.place.screen == "roster"
            assert app.place.focused_name == "Flag"
            leftover = [r.name for r in app.engine.visible_column_roster()]
            assert leftover == ["Flag"]

    asyncio.run(_run())


def test_pair_list_a_selects_former_next_below(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n2,N\n3,Z\n")
    write_csv(pb, "id,val\n1,Yes\n2,No\n3,Zed\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    groups = eng.pair_groups("val").to_dicts()
    first = (groups[0]["val_a"], groups[0]["val_b"])
    second = (groups[1]["val_a"], groups[1]["val_b"])
    last = (groups[2]["val_a"], groups[2]["val_b"])
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.place.screen = "pair_list"
            app.place.column = "val"
            app.place.pair_val_a, app.place.pair_val_b = first
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            app.action_accept()
            await pilot.pause()
            assert app.place.screen == "pair_list"
            assert app.place.column == "val"
            assert (app.place.pair_val_a, app.place.pair_val_b) == second
            assert next(r for r in app.engine.roster() if r.name == "val").pending == 2
            app.place.pair_val_a, app.place.pair_val_b = last
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            app.action_accept()
            await pilot.pause()
            assert app.place.screen == "pair_list"
            assert app.place.column == "val"
            assert (app.place.pair_val_a, app.place.pair_val_b) == second
            assert next(r for r in app.engine.roster() if r.name == "val").pending == 1

    asyncio.run(_run())


def test_roster_v_and_a_stay_still_work_with_a_order(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,ok\n1,Y,same\n")
    write_csv(pb, "id,Status,ok\n1,Yes,same\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert [r.name for r in app._table_keys if r is not None] == ["Status"]
            app.query_one("#grid").focus()
            app.action_accept()
            await pilot.pause()
            assert app.place.screen == "roster"
            assert "Status" not in [r.name for r in app._table_keys if r is not None]
            app.action_toggle_accepted()
            await pilot.pause()
            names = [r.name for r in app._table_keys if r is not None]
            assert names == ["Status", "ok"] or names[0] == "Status"
            statuses = []
            table = app.query_one("#grid")
            labels = [str(col.label) for col in table.columns.values()]
            status_i = labels.index("status")
            for i, r in enumerate(app._table_keys):
                if r is not None:
                    statuses.append((r.name, str(table.get_row_at(i)[status_i])))
            by_name = dict(statuses)
            assert by_name["Status"] == "accepted"
            assert by_name["ok"] == "equal"

    asyncio.run(_run())


def test_multi_column_pair_accept_empty_to_zero(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,qty,amt,note\n1,,,x\n2,,,y\n")
    write_csv(pb, "id,qty,amt,note\n1,0,0,x\n2,0,0,z\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert [r.name for r in app.engine.visible_column_roster()] == [
                "qty",
                "amt",
                "note",
            ]
            footer = str(app.query_one("#footer").render())
            assert "? help" in footer
            assert "m same pair" not in footer
            app.query_one("#grid").focus()
            await pilot.press("m")
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, MultiPairModal)
            assert modal.phase == "pairs"
            pairs = modal._pairs
            idx = next(
                i
                for i, rec in enumerate(pairs)
                if rec["val_a"] == "" and rec["val_b"] == "0"
            )
            modal.query_one("#multi").move_cursor(row=idx)
            modal.action_ok()
            await pilot.pause()
            assert not isinstance(app.screen, MultiPairModal)
            assert app.place.screen == "roster"
            assert next(r for r in app.engine.roster() if r.name == "qty").pending == 0
            assert next(r for r in app.engine.roster() if r.name == "amt").pending == 0
            assert next(r for r in app.engine.roster() if r.name == "note").pending == 1
            leftover = [r for r in app.engine.pending_cells.to_dicts() if r["column"] == "note"]
            assert leftover[0]["val_a"] == "y"
            assert leftover[0]["val_b"] == "z"
            shown = [r.name for r in app._table_keys if r is not None]
            assert shown == ["note"]

    asyncio.run(_run())


def test_equals_y_returns_to_roster_and_selects_next_below(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,all_a,keep\n1,——,x\n2,——,y\n")
    write_csv(pb, "id,all_a,keep\n1,a,1\n2,b,2\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.engine.start_sentinel_draft("A", "——")
            app.place.focused_name = "all_a"
            app.render_all()
            await pilot.pause()
            assert app.place.screen == "roster"
            assert app.engine.column_draft == {"all_a"}
            app.query_one("#grid").focus()
            app.action_confirm()
            await pilot.pause()
            assert not app.engine.column_draft
            assert app.place.screen == "roster"
            leftover = [r.name for r in app.engine.visible_column_roster()]
            assert leftover == ["keep"]
            assert app.place.focused_name == "keep"
            shown = [r.name for r in app._table_keys if r is not None]
            assert shown == ["keep"]
            footer = str(app.query_one("#footer").render())
            assert "working…" not in footer

    asyncio.run(_run())


def test_pair_list_context_top5_unique_values_and_truncation(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    flags = (
        ["red"] * 3
        + ["blue"] * 2
        + ["green"]
        + ["orange"]
        + ["pink"]
        + ["purple"]
        + ["yellow"]
    )
    a_rows = ["id,val,Flag"] + [f"{i},{ 'Y' if i < 10 else 'N'},{flags[i] if i < 10 else 'z'}" for i in range(12)]
    b_rows = ["id,val,Flag"] + [
        f"{i},{ 'Yes' if i < 10 else 'No'},{flags[i] if i < 10 else 'z'}" for i in range(12)
    ]
    write_csv(pa, "\n".join(a_rows) + "\n")
    write_csv(pb, "\n".join(b_rows) + "\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    eng.context_columns["val"] = ["Flag"]
    recs, _, _ = eng.pair_page("val", 0)
    y_yes = next(r for r in recs if r["val_a"] == "Y" and r["val_b"] == "Yes")
    ctx = y_yes["Flag__ctx"]
    assert ctx.startswith("red×3 | blue×2 | green×1 | orange×1 | pink×1")
    assert ctx.endswith("+2 more")
    assert "purple" not in ctx
    assert "yellow" not in ctx
    n_yes = next(r for r in recs if r["val_a"] == "N" and r["val_b"] == "No")
    assert n_yes["Flag__ctx"] == "z×2"
    assert "+2 more" not in n_yes["Flag__ctx"]

    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.place.screen = "pair_list"
            app.place.column = "val"
            app.place.pair_val_a = "Y"
            app.place.pair_val_b = "Yes"
            app.render_all()
            await pilot.pause()
            table = app.query_one("#grid")
            labels = [str(col.label) for col in table.columns.values()]
            assert "Flag" in labels
            assert "ctx:Flag" not in labels
            flag_i = labels.index("Flag")
            shown = str(table.get_row_at(0)[flag_i])
            assert "red×3" in shown
            assert "purple" not in shown
            pane = str(app.query_one("#pane").render())
            assert "Flag" in pane
            assert "red ×3" in pane
            assert "+2 more" in pane
            app.action_context()
            await pilot.pause()
            assert isinstance(app.screen, ContextModal)
            await pilot.press("escape")
            await pilot.pause()
            assert app.place.screen == "pair_list"

    asyncio.run(_run())


def test_busy_indicator_shows_for_wait_not_instant_accept(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag\n1,Y,1\n")
    write_csv(pb, "id,Status,Flag\n1,Yes,2\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app._busy_note = "working…"
            app._render_footer()
            footer = str(app.query_one("#footer").render())
            assert "working…" in footer
            app._busy_note = None
            app._render_footer()
            app.query_one("#grid").focus()
            app.action_accept()
            await pilot.pause()
            footer = str(app.query_one("#footer").render())
            assert "working…" not in footer
            assert app.place.screen == "roster"
            app.action_refresh()
            await pilot.pause()
            footer = str(app.query_one("#footer").render())
            assert "working…" not in footer

    asyncio.run(_run())


def test_roster_speculative_column_has_no_prefix(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,only_a\n1,0\n2,0\n")
    write_csv(pb, "id,only_a\n1,x\n2,y\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#grid")
            labels = [str(col.label) for col in table.columns.values()]
            assert "speculative" not in labels
            assert "const A" in labels
            assert "const B" not in labels
            sent_i = labels.index("const A")
            shown = str(table.get_row_at(0)[sent_i])
            assert shown == "0"
            assert "speculative:" not in shown
            assert "shared value pattern" not in shown

    asyncio.run(_run())


def test_roster_check_headers_hide_all_n_and_show_when_matching(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, 'id,money,plain\n1,"$1,234",foo\n2,"€2 000",bar\n')
    write_csv(pb, "id,money,plain\n1,1234,baz\n2,2000,qux\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _match() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            labels = [str(col.label) for col in app.query_one("#grid").columns.values()]
            assert "money" in labels
            assert "plain" not in labels
            money_i = labels.index("money")
            by_name = {r.name: i for i, r in enumerate(app._table_keys) if r is not None}
            shown = str(app.query_one("#grid").get_row_at(by_name["money"])[money_i]).strip()
            assert shown == "y"
            plain_shown = str(app.query_one("#grid").get_row_at(by_name["plain"])[money_i]).strip()
            assert plain_shown == "n"

    asyncio.run(_match())

    pa2, pb2 = tmp_path / "a2.csv", tmp_path / "b2.csv"
    write_csv(pa2, "id,val\n1,foo\n2,bar\n")
    write_csv(pb2, "id,val\n1,baz\n2,qux\n")
    hidden = ReconcileApp(
        Engine.from_paths(str(pa2), str(pb2), ["id"], a_delim=",", b_delim=",")
    )

    async def _hidden() -> None:
        async with hidden.run_test() as pilot:
            await pilot.pause()
            labels = [str(col.label) for col in hidden.query_one("#grid").columns.values()]
            for name in (
                "trim",
                "num",
                "date",
                "money",
                "pct",
                "idpad",
                "bool",
                "acctneg",
                "xlsdate",
                "inws",
                "dash",
                "fold",
            ):
                assert name not in labels

    asyncio.run(_hidden())


def test_regex_draft_m_uses_on_columns_not_all_pending(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,s1,s2,mixed\n1,0,0,0\n2,0,0,0\n3,0,0,z\n")
    write_csv(pb, "id,s1,s2,mixed\n1,x,x,x\n2,x,x,x\n3,x,x,y\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.engine.start_regex_draft("s1|s2")
            app.render_all()
            await pilot.pause()
            assert app.engine.column_draft == {"s1", "s2"}
            app.query_one("#grid").focus()
            app.action_multi_pair()
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, MultiPairModal)
            assert modal.skip_column_pick is True
            assert modal.phase == "pairs"
            assert set(modal.columns) == {"s1", "s2"}
            idx = next(
                i
                for i, rec in enumerate(modal._pairs)
                if rec["val_a"] == "0" and rec["val_b"] == "x"
            )
            modal.query_one("#multi").move_cursor(row=idx)
            modal.action_ok()
            await pilot.pause()
            assert not isinstance(app.screen, MultiPairModal)
            assert next(r for r in app.engine.roster() if r.name == "s1").pending == 0
            assert next(r for r in app.engine.roster() if r.name == "s2").pending == 0
            mixed = [r for r in app.engine.pending_cells.to_dicts() if r["column"] == "mixed"]
            assert any(r["val_a"] == "0" and r["val_b"] == "x" for r in mixed)
            leftover = [r.name for r in app.engine.visible_column_roster()]
            assert leftover == ["mixed"]

    asyncio.run(_run())


def test_sentinel_draft_m_uses_on_columns(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,s1,s2,mixed\n1,0,0,0\n2,0,0,0\n3,0,0,z\n")
    write_csv(pb, "id,s1,s2,mixed\n1,x,x,x\n2,x,x,x\n3,x,x,y\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.engine.start_sentinel_draft("A", "0")
            app.render_all()
            await pilot.pause()
            assert app.engine.column_draft == {"s1", "s2"}
            banner = str(app.query_one("#banner").render())
            assert "y accept" in banner
            app.query_one("#grid").focus()
            app.action_multi_pair()
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, MultiPairModal)
            assert modal.skip_column_pick is True
            assert set(modal.columns) == {"s1", "s2"}
            idx = next(
                i
                for i, rec in enumerate(modal._pairs)
                if rec["val_a"] == "0" and rec["val_b"] == "x"
            )
            modal.query_one("#multi").move_cursor(row=idx)
            modal.action_ok()
            await pilot.pause()
            mixed = [r for r in app.engine.pending_cells.to_dicts() if r["column"] == "mixed"]
            assert any(r["val_a"] == "0" and r["val_b"] == "x" for r in mixed)
            leftover = [r.name for r in app.engine.visible_column_roster()]
            assert leftover == ["mixed"]

    asyncio.run(_run())


def test_pair_list_m_applies_this_pair_without_column_picker(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,qty,amt,note\n1,,,x\n2,,,y\n")
    write_csv(pb, "id,qty,amt,note\n1,0,0,x\n2,0,0,z\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.place.screen = "pair_list"
            app.place.column = "qty"
            app.place.pair_val_a = ""
            app.place.pair_val_b = "0"
            app.render_all()
            await pilot.pause()
            app.query_one("#grid").focus()
            app.action_multi_pair()
            await pilot.pause()
            assert not isinstance(app.screen, MultiPairModal)
            assert next(r for r in app.engine.roster() if r.name == "qty").pending == 0
            assert next(r for r in app.engine.roster() if r.name == "amt").pending == 0
            assert next(r for r in app.engine.roster() if r.name == "note").pending == 1
            assert app.engine.last_grain is not None
            assert app.engine.last_grain[0] == "pairs"
            app.action_undo()
            await pilot.pause()
            assert next(r for r in app.engine.roster() if r.name == "qty").pending == 2
            assert next(r for r in app.engine.roster() if r.name == "amt").pending == 2
            assert next(r for r in app.engine.roster() if r.name == "note").pending == 1
            assert app.engine.last_grain is None

    asyncio.run(_run())


def test_u_errors_when_nothing_accepted(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n")
    write_csv(pb, "id,val\n1,Yes\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.engine.last_grain is None
            app.action_undo()
            await pilot.pause()
            assert app.tui_error and "nothing accepted to undo" in app.tui_error
            assert app.engine.pending_cells_n() == 1

    asyncio.run(_run())


def test_pending_displays_match_engine(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,cust\n1,Y,1\n2,Y,2\n3,onlyA,3\n")
    write_csv(pb, "id,Status\n1,Yes\n2,Yes\n4,onlyB\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#grid")
            labels = [str(col.label) for col in table.columns.values()]
            pend_i = labels.index("pending")
            row = next(r for r in app._table_keys if r is not None)
            shown = str(table.get_row_at(0)[pend_i]).strip()
            assert shown == str(row.pending)
            assert int(shown) == next(
                r.pending for r in app.engine.roster() if r.name == row.name
            )
            footer = str(app.query_one("#footer").render())
            assert f"pending columns {app.engine.pending_columns_n()}" in footer
            app.action_overview()
            await pilot.pause()
            ov = app.screen.query_one("#ov-entries")
            ov_labels = [str(col.label) for col in ov.columns.values()]
            ov_pend_i = ov_labels.index("pending")
            by_entry = {
                str(ov.get_row_at(i)[0]): int(str(ov.get_row_at(i)[ov_pend_i]))
                for i in range(ov.row_count)
            }
            assert by_entry["A-only keys"] == app.engine.pending_a_only_n() == 1
            assert by_entry["B-only keys"] == app.engine.pending_b_only_n() == 1
            assert by_entry["Mismatched columns"] == app.engine.pending_extras_n() == 1
            await pilot.press("escape")
            await pilot.pause()
            app.query_one("#grid").focus()
            app.action_drill()
            await pilot.pause()
            assert app.place.screen == "pair_list"
            tab = app.query_one("#tab-pending", Button)
            assert f"Pending {row.pending}" in str(tab.label)
            statics = " ".join(str(s.render()) for s in app.query(Static))
            assert f"{row.pending} pending" in statics
            pair_table = app.query_one("#grid")
            pair_labels = [str(col.label) for col in pair_table.columns.values()]
            pair_pend_i = pair_labels.index("pending")
            rec = app._table_keys[0]
            assert str(pair_table.get_row_at(0)[pair_pend_i]).strip() == str(int(rec["n"]))
            assert int(rec["n"]) == row.pending
            app.action_accept()
            await pilot.pause()
            leftover = next(r.pending for r in app.engine.roster() if r.name == "Status")
            assert leftover == 0
            assert app.place.screen == "roster"
            statics = " ".join(str(s.render()) for s in app.query(Static))
            assert "pending columns 0" in statics

    asyncio.run(_run())


def test_pair_list_context_columns_are_labeled_and_separate(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(
        pa,
        "id,val,Flag,Region\n"
        "1,Y,red,east\n2,Y,red,east\n3,Y,blue,west\n4,Y,green,west\n",
    )
    write_csv(
        pb,
        "id,val,Flag,Region\n"
        "1,Yes,red,east\n2,Yes,red,east\n3,Yes,blue,west\n4,Yes,green,west\n",
    )
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    eng.context_columns["val"] = ["Flag", "Region"]
    recs, _, _ = eng.pair_page("val", 0)
    rec = recs[0]
    assert rec["Flag__ctx"] == "red×2 | blue×1 | green×1"
    assert rec["Region__ctx"] == "east×2 | west×2"
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.place.screen = "pair_list"
            app.place.column = "val"
            app.render_all()
            await pilot.pause()
            table = app.query_one("#grid")
            labels = [str(col.label) for col in table.columns.values()]
            assert "Flag" in labels
            assert "Region" in labels
            assert "ctx:Flag" not in labels
            flag_i = labels.index("Flag")
            region_i = labels.index("Region")
            assert flag_i != region_i
            flag_cell = str(table.get_row_at(0)[flag_i])
            region_cell = str(table.get_row_at(0)[region_i])
            assert "red×2" in flag_cell
            assert "east×2" in region_cell
            assert "east" not in flag_cell
            assert "red" not in region_cell
            pane = str(app.query_one("#pane").render())
            assert "Flag" in pane
            assert "Region" in pane
            assert pane.index("Flag") != pane.index("Region")

    asyncio.run(_run())


def _group_fixture(tmp_path: Path) -> Engine:
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    rows_a = ["id,val,Flag,Region,Zone"]
    rows_b = ["id,val,Flag,Region,Zone"]
    # 8 unique Flag+Region tuples; two pairs of 2, six singles → top-5 + …
    data = [
        ("1", "red", "east", "N"),
        ("2", "red", "east", "N"),
        ("3", "blue", "west", "S"),
        ("4", "blue", "west", "S"),
        ("5", "green", "west", "S"),
        ("6", "orange", "east", "N"),
        ("7", "pink", "east", "N"),
        ("8", "purple", "west", "S"),
        ("9", "yellow", "east", "N"),
        ("10", "brown", "west", "S"),
    ]
    for i, flag, region, zone in data:
        rows_a.append(f"{i},Y,{flag},{region},{zone}")
        rows_b.append(f"{i},Yes,{flag},{region},{zone}")
    write_csv(pa, "\n".join(rows_a) + "\n")
    write_csv(pb, "\n".join(rows_b) + "\n")
    return Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")


def test_pair_list_context_group_pair_and_triplet_top5(tmp_path: Path):
    eng = _group_fixture(tmp_path)
    eng.context_columns["val"] = ["Flag"]
    eng.context_groups["val"] = {
        0: ["Flag", "Region"],
        1: ["Flag", "Region", "Zone"],
        5: [],
    }
    recs, _, _ = eng.pair_page("val", 0)
    rec = recs[0]
    assert rec["Flag__ctx"].startswith("blue×2 | red×2")
    assert rec["Flag__ctx"].endswith("+3 more")
    assert rec["g0__gctx"] == (
        "blue|west×2 | red|east×2 | brown|west×1 | green|west×1 | orange|east×1 | +3 more"
    )
    assert rec["g1__gctx"] == (
        "blue|west|S×2 | red|east|N×2 | brown|west|S×1 | green|west|S×1 | orange|east|N×1 | +3 more"
    )
    assert "g5__gctx" not in rec
    assert "pink|east" not in rec["g0__gctx"]
    views = eng.context_views("val")
    headers = [v.header for v in views]
    assert "Flag" in headers
    assert "g0 Flag+Region" in headers
    assert "g1 Flag+Region+Zone" in headers
    assert not any(v.header.startswith("g5") for v in views)


def test_empty_context_group_omitted_and_single_still_works(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(
        pa,
        "id,val,Flag,Region\n1,Y,red,east\n2,Y,blue,west\n",
    )
    write_csv(
        pb,
        "id,val,Flag,Region\n1,Yes,red,east\n2,Yes,blue,west\n",
    )
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    eng.context_columns["val"] = ["Flag"]
    eng.context_groups["val"] = {3: []}
    recs, _, _ = eng.pair_page("val", 0)
    rec = recs[0]
    assert rec["Flag__ctx"] == "blue×1 | red×1"
    assert "g3__gctx" not in rec
    assert [v.header for v in eng.context_views("val")] == ["Flag"]


def test_same_column_in_two_groups_counts_independently(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(
        pa,
        "id,val,Flag,Region,Zone\n1,Y,red,east,N\n2,Y,red,west,S\n",
    )
    write_csv(
        pb,
        "id,val,Flag,Region,Zone\n1,Yes,red,east,N\n2,Yes,red,west,S\n",
    )
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    eng.context_groups["val"] = {0: ["Flag", "Region"], 2: ["Flag", "Zone"]}
    recs, _, _ = eng.pair_page("val", 0)
    rec = recs[0]
    assert rec["g0__gctx"] == "red|east×1 | red|west×1"
    assert rec["g2__gctx"] == "red|N×1 | red|S×1"


def test_pair_ctx_cache_invalidates_when_groups_change(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(
        pa,
        "id,val,Flag,Region\n1,Y,red,east\n2,Y,blue,west\n",
    )
    write_csv(
        pb,
        "id,val,Flag,Region\n1,Yes,red,east\n2,Yes,blue,west\n",
    )
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    eng.context_columns["val"] = ["Flag"]
    first, _, _ = eng.pair_page("val", 0)
    assert "g0__gctx" not in first[0]
    assert "val" in eng._pair_ctx_by_col
    eng.context_groups["val"] = {0: ["Flag", "Region"]}
    stale, _, _ = eng.pair_page("val", 0)
    assert "g0__gctx" not in stale[0]
    eng._pair_ctx_by_col.pop("val", None)
    fresh, _, _ = eng.pair_page("val", 0)
    assert fresh[0]["g0__gctx"] == "blue|west×1 | red|east×1"


def test_cell_step_group_context_is_per_row_tuple(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val,Flag,Region\n1,Y,red,east\n")
    write_csv(pb, "id,val,Flag,Region\n1,Yes,blue,west\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    eng.context_columns["val"] = ["Flag"]
    eng.context_groups["val"] = {0: ["Flag", "Region"]}
    recs, _, _ = eng.pair_cells_page("val", "Y", "Yes", 0)
    rec = recs[0]
    assert rec["Flag__ctx_a"] == "red"
    assert rec["Flag__ctx_b"] == "blue"
    assert rec["Region__ctx_a"] == "east"
    assert rec["Region__ctx_b"] == "west"
    assert eng.context_values(("1",), "val") == [
        ("Flag", "red", "blue"),
        ("g0 Flag+Region", "red|east", "blue|west"),
    ]
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.engine.start_pair_draft("val", "Y", "Yes")
            app.place.screen = "cell_step"
            app.place.column = "val"
            app.place.pair_val_a = "Y"
            app.place.pair_val_b = "Yes"
            app.place.focused_key = ("1",)
            app.render_all()
            await pilot.pause()
            table = app.query_one("#grid")
            labels = [str(col.label) for col in table.columns.values()]
            assert "Flag" in labels
            assert "g0 Flag+Region" in labels
            g_i = labels.index("g0 Flag+Region")
            assert str(table.get_row_at(0)[g_i]) == "A: red|east  B: blue|west"
            pane = str(app.query_one("#pane").render())
            assert "g0 Flag+Region" in pane
            assert "A: red|east" in pane
            assert "B: blue|west" in pane

    asyncio.run(_run())


def test_context_modal_0_9_toggles_group_membership(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag,Region\n1,Y,red,east\n")
    write_csv(pb, "id,Status,Flag,Region\n1,Yes,blue,west\n")
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
            assert modal.names[0] == "Flag"
            await pilot.press("0")
            await pilot.pause()
            await pilot.press("2")
            await pilot.pause()
            assert "Flag" in modal.groups[0]
            assert "Flag" in modal.groups[2]
            table = modal.query_one("#ctx")
            assert "0 · 2" in str(table.get_row_at(0)[1])
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("0")
            await pilot.pause()
            assert "Region" in modal.groups[0]
            assert "Flag" in modal.groups[0]
            await pilot.press("enter")
            await pilot.pause()
            assert not isinstance(app.screen, ContextModal)
            assert app.engine.context_columns.get("Status") == []
            assert app.engine.context_groups["Status"][0] == ["Flag", "Region"]
            assert app.engine.context_groups["Status"][2] == ["Flag"]
            assert 1 not in app.engine.context_groups["Status"]
            assert "Status" not in app.engine._pair_ctx_by_col
            recs, _, _ = app.engine.pair_page("Status", 0)
            assert recs[0]["g0__gctx"] == "blue|west×1 | red|east×1"
            assert recs[0]["g2__gctx"] == "blue×1 | red×1"
            assert "Flag__ctx" not in recs[0]

    asyncio.run(_run())


def test_context_modal_space_and_groups_together(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,Status,Flag,Region\n1,Y,red,east\n")
    write_csv(pb, "id,Status,Flag,Region\n1,Yes,red,east\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.place.screen = "pair_list"
            app.place.column = "Status"
            app.render_all()
            await pilot.pause()
            await pilot.press("c")
            await pilot.pause()
            modal = app.screen
            assert isinstance(modal, ContextModal)
            await pilot.press("space")
            await pilot.pause()
            await pilot.press("0")
            await pilot.pause()
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("0")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert app.engine.context_columns["Status"] == ["Flag"]
            assert app.engine.context_groups["Status"][0] == ["Flag", "Region"]
            table = app.query_one("#grid")
            labels = [str(col.label) for col in table.columns.values()]
            assert "Flag" in labels
            assert "g0 Flag+Region" in labels
            assert labels.index("Flag") < labels.index("g0 Flag+Region")

    asyncio.run(_run())


def _money_two_pending_app(tmp_path: Path) -> ReconcileApp:
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(
        pa,
        'id,cash,fee,note\n1,"$1,234","€2 000",foo\n2,"€2 000","$3,000",bar\n',
    )
    write_csv(pb, "id,cash,fee,note\n1,1234,2000,baz\n2,2000,3000,qux\n")
    return ReconcileApp(
        Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    )


async def _arrow_to_roster_header(pilot, app: ReconcileApp, header: str) -> None:
    table = app.query_one("#grid")
    labels = [str(col.label) for col in table.columns.values()]
    assert header in labels
    want = labels.index(header)
    await pilot.press("home")
    await pilot.pause()
    for _ in range(want):
        await pilot.press("right")
        await pilot.pause()
    labels = [str(col.label) for col in table.columns.values()]
    assert labels[table.cursor_column] == header


def test_Y_on_money_drafts_two_then_y_accepts_one_grain(tmp_path: Path):
    app = _money_two_pending_app(tmp_path)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#grid")
            assert table.cursor_type == "cell"
            labels = [str(col.label) for col in table.columns.values()]
            assert "money" in labels
            footer = str(app.query_one("#footer").render())
            assert "Y all y in this check" in footer
            app.query_one("#grid").focus()
            await pilot.press("Y")
            await pilot.pause()
            assert not app.engine.column_draft
            assert app.tui_error and "Y selects y-rows of a check column" in app.tui_error
            await _arrow_to_roster_header(pilot, app, "money")
            await pilot.press("Y")
            await pilot.pause()
            assert app.engine.column_draft == {"cash", "fee"}
            assert "note" not in app.engine.column_draft
            banner = str(app.query_one("#banner").render())
            assert "2 column" in banner
            assert "y accept" in banner
            assert "Space toggle" in banner
            pending_before = app.engine.pending_cells_n()
            note_pending = next(r for r in app.engine.roster() if r.name == "note").pending
            await pilot.press("y")
            await pilot.pause()
            assert not app.engine.column_draft
            assert next(r for r in app.engine.roster() if r.name == "cash").pending == 0
            assert next(r for r in app.engine.roster() if r.name == "fee").pending == 0
            assert next(r for r in app.engine.roster() if r.name == "note").pending == note_pending
            assert app.engine.pending_cells_n() == pending_before - 4
            assert app.engine.last_grain is not None
            assert app.engine.last_grain[0] == "columns"
            await pilot.press("u")
            await pilot.pause()
            assert next(r for r in app.engine.roster() if r.name == "cash").pending > 0
            assert next(r for r in app.engine.roster() if r.name == "fee").pending > 0
            assert app.engine.pending_cells_n() == pending_before

    asyncio.run(_run())


def test_Y_on_name_errors_no_draft(tmp_path: Path):
    app = _money_two_pending_app(tmp_path)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#grid")
            labels = [str(col.label) for col in table.columns.values()]
            assert labels[table.cursor_column] == "name"
            app.query_one("#grid").focus()
            await pilot.press("Y")
            await pilot.pause()
            assert not app.engine.column_draft
            assert app.tui_error and "Y selects y-rows of a check column" in app.tui_error

    asyncio.run(_run())


def test_Y_when_no_money_column_visible_errors(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,foo\n2,bar\n")
    write_csv(pb, "id,val\n1,baz\n2,qux\n")
    app = ReconcileApp(
        Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    )

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            labels = [str(col.label) for col in app.query_one("#grid").columns.values()]
            assert "money" not in labels
            for name in (
                "trim",
                "case",
                "num",
                "date",
                "pct",
                "idpad",
                "bool",
                "acctneg",
                "xlsdate",
                "inws",
                "dash",
                "fold",
            ):
                assert name not in labels
            footer = str(app.query_one("#footer").render())
            assert "Y all y in this check" not in footer
            app.query_one("#grid").focus()
            await pilot.press("Y")
            await pilot.pause()
            assert not app.engine.column_draft
            assert app.tui_error and "Y selects y-rows of a check column" in app.tui_error

    asyncio.run(_run())


def test_Y_refused_while_column_draft_live(tmp_path: Path):
    app = _money_two_pending_app(tmp_path)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.engine.start_regex_draft("cash|note")
            app.render_all()
            await pilot.pause()
            assert app.engine.column_draft == {"cash", "note"}
            app.query_one("#grid").focus()
            await _arrow_to_roster_header(pilot, app, "money")
            await pilot.press("Y")
            await pilot.pause()
            assert app.engine.column_draft == {"cash", "note"}
            assert app.tui_error and "confirm or cancel" in app.tui_error

    asyncio.run(_run())


def test_Y_does_not_steal_pair_list_A_or_roster_a(tmp_path: Path):
    app = _money_two_pending_app(tmp_path)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#grid").focus()
            pending = app.engine.pending_cells_n()
            app.action_drill()
            await pilot.pause()
            assert app.place.screen == "pair_list"
            await pilot.press("Y")
            await pilot.pause()
            assert app.place.screen == "pair_list"
            assert not app.engine.column_draft
            assert app.engine.pending_cells_n() == pending
            assert app.tui_error and "Y selects y-rows" in app.tui_error
            await pilot.press("escape")
            await pilot.pause()
            assert app.place.screen == "roster"
            app.place.focused_name = "note"
            app.render_all()
            await pilot.pause()
            names = [r.name for r in app._table_keys if r is not None]
            app.query_one("#grid").move_cursor(row=names.index("note"))
            app.query_one("#grid").focus()
            await pilot.press("a")
            await pilot.pause()
            assert next(r for r in app.engine.roster() if r.name == "note").pending == 0
            assert next(r for r in app.engine.roster() if r.name == "cash").pending > 0

    asyncio.run(_run())


def test_Y_space_deselect_before_confirm(tmp_path: Path):
    app = _money_two_pending_app(tmp_path)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#grid").focus()
            await _arrow_to_roster_header(pilot, app, "money")
            await pilot.press("Y")
            await pilot.pause()
            assert app.engine.column_draft == {"cash", "fee"}
            names = [r.name for r in app._table_keys if r is not None]
            fee_i = names.index("fee")
            app.query_one("#grid").move_cursor(row=fee_i)
            await pilot.press("space")
            await pilot.pause()
            assert app.engine.column_draft == {"cash"}
            await pilot.press("y")
            await pilot.pause()
            assert not app.engine.column_draft
            assert next(r for r in app.engine.roster() if r.name == "cash").pending == 0
            assert next(r for r in app.engine.roster() if r.name == "fee").pending > 0

    asyncio.run(_run())


def test_digits_on_pair_list_do_not_set_context_groups(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val,Flag\n1,Y,red\n")
    write_csv(pb, "id,val,Flag\n1,Yes,blue\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.place.screen = "pair_list"
            app.place.column = "val"
            app.render_all()
            await pilot.pause()
            await pilot.press("0")
            await pilot.pause()
            await pilot.press("9")
            await pilot.pause()
            assert app.place.screen == "pair_list"
            assert not app.engine.context_groups.get("val")
            assert not isinstance(app.screen, ContextModal)

    asyncio.run(_run())
