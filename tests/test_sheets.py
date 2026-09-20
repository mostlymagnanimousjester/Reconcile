import asyncio
from pathlib import Path

import pytest

from reconcile.cli import build_parser, engine_from_args, main
from reconcile.engine import Engine, InTuiError, Place
from reconcile.errors import HardFail
from reconcile.sheets import expand_sheets
from reconcile.tui import HELP, ReconcileApp
from tests.xlsxutil import write_xlsx, write_xlsx_sheets


def test_expand_braced_prefix_ranges_and_singles():
    assert expand_sheets("data{1-4,7}") == [
        "data1",
        "data2",
        "data3",
        "data4",
        "data7",
    ]
    assert expand_sheets("{1,2,3}") == ["1", "2", "3"]
    assert expand_sheets("data{01,02}") == ["data1", "data2"]
    assert expand_sheets("  data{1-2, 5}  ") == ["data1", "data2", "data5"]


def test_expand_exact_comma_list_no_range():
    assert expand_sheets("Jan,Feb") == ["Jan", "Feb"]
    assert expand_sheets("Jan,Feb,Mar") == ["Jan", "Feb", "Mar"]
    assert expand_sheets("data1,data2") == ["data1", "data2"]
    assert expand_sheets("1-4,7") == ["1-4", "7"]
    assert expand_sheets(" Jan , Feb ") == ["Jan", "Feb"]
    assert expand_sheets("Solo") == ["Solo"]


def test_expand_sheets_hard_fails_bad_set():
    with pytest.raises(HardFail, match="-sheets") as ei:
        expand_sheets("")
    assert "empty" in ei.value.message.lower()
    with pytest.raises(HardFail, match="-sheets"):
        expand_sheets("data{1-4")
    with pytest.raises(HardFail, match="-sheets"):
        expand_sheets("{data1,data2}")
    with pytest.raises(HardFail, match="start > end"):
        expand_sheets("data{4-1}")
    with pytest.raises(HardFail, match="duplicate"):
        expand_sheets("data{1,1}")
    with pytest.raises(HardFail, match="duplicate"):
        expand_sheets("Jan,Jan")
    with pytest.raises(HardFail, match="empty item"):
        expand_sheets("Jan,")
    with pytest.raises(HardFail, match="-sheets"):
        expand_sheets("data{1.5}")


def test_parser_accepts_sheets_and_a_sheet():
    p = build_parser()
    ns = p.parse_args(
        ["--a", "a.xlsx", "--b", "b.xlsx", "-sheets", "data{1-2}", "--keys", "id"]
    )
    assert ns.sheets == "data{1-2}"
    assert ns.a_sheet is None
    assert ns.b_sheet is None
    ns2 = p.parse_args(
        [
            "--a",
            "a.xlsx",
            "--b",
            "b.xlsx",
            "--sheets",
            "Jan,Feb",
            "--keys",
            "id",
        ]
    )
    assert ns2.sheets == "Jan,Feb"
    ns3 = p.parse_args(
        [
            "--a",
            "a.xlsx",
            "--b",
            "b.xlsx",
            "--a-sheet",
            "Foo",
            "--b-sheet",
            "Bar",
            "--keys",
            "id",
        ]
    )
    assert ns3.a_sheet == "Foo"
    assert ns3.b_sheet == "Bar"
    assert ns3.sheets is None


def test_a_sheet_b_sheet_still_work_different_names(tmp_path: Path):
    a = tmp_path / "a.xlsx"
    b = tmp_path / "b.xlsx"
    write_xlsx(a, "Foo", [["id", "val"], ["1", "Y"]])
    write_xlsx(b, "Bar", [["id", "val"], ["1", "Yes"]])
    p = build_parser()
    ns = p.parse_args(
        [
            "--a",
            str(a),
            "--b",
            str(b),
            "--a-sheet",
            "Foo",
            "--b-sheet",
            "Bar",
            "--keys",
            "id",
        ]
    )
    eng, _ = engine_from_args(ns)
    assert eng.a.sheet == "Foo"
    assert eng.b.sheet == "Bar"
    assert eng.sheet_set is None
    assert eng.pending_cells_n() == 1


def test_sheets_and_a_sheet_mutually_exclusive(tmp_path: Path, capsys):
    a = tmp_path / "a.xlsx"
    b = tmp_path / "b.xlsx"
    write_xlsx(a, "Foo", [["id", "val"], ["1", "a"]])
    write_xlsx(b, "Foo", [["id", "val"], ["1", "a"]])
    code = main(
        [
            "--a",
            str(a),
            "--b",
            str(b),
            "-sheets",
            "Foo",
            "--a-sheet",
            "Foo",
            "--b-sheet",
            "Foo",
            "--keys",
            "id",
        ]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "mutually exclusive" in err
    assert "--a-sheet" in err


def test_sheets_requires_excel(tmp_path: Path, capsys):
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    a.write_text("id,val\n1,a\n", encoding="utf-8")
    b.write_text("id,val\n1,a\n", encoding="utf-8")
    code = main(
        ["--a", str(a), "--b", str(b), "-sheets", "Jan,Feb", "--keys", "id"]
    )
    assert code == 2
    assert "Excel" in capsys.readouterr().err


def _equal_pair(tmp_path: Path, names: list[str], val: str = "x") -> tuple[Path, Path]:
    sheets = {n: [["id", "val"], ["1", val]] for n in names}
    a = tmp_path / "left.xlsx"
    b = tmp_path / "right.xlsx"
    write_xlsx_sheets(a, sheets)
    write_xlsx_sheets(b, {n: [["id", "val"], ["1", val]] for n in names})
    return a, b


def test_missing_sheet_hard_fail_preflight(tmp_path: Path, capsys):
    a = tmp_path / "left.xlsx"
    b = tmp_path / "right.xlsx"
    write_xlsx_sheets(
        a,
        {
            "data1": [["id", "val"], ["1", "a"]],
            "data2": [["id", "val"], ["1", "b"]],
        },
    )
    write_xlsx_sheets(b, {"data1": [["id", "val"], ["1", "a"]]})
    code = main(
        ["--a", str(a), "--b", str(b), "-sheets", "data{1-2}", "--keys", "id"]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "data2" in err
    assert str(b.resolve()) in err


def test_sheets_loads_first_pair_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    loaded: list[str] = []
    from reconcile import load as load_mod

    real = load_mod.load_excel

    def spy(path, sheet_name):
        loaded.append(sheet_name)
        return real(path, sheet_name)

    monkeypatch.setattr(load_mod, "load_excel", spy)
    a, b = _equal_pair(tmp_path, ["data1", "data2", "data3"])
    eng = Engine.from_paths(
        str(a), str(b), ["id"], sheet_set=["data1", "data2", "data3"]
    )
    assert loaded == ["data1", "data1"]
    assert eng.a.sheet == "data1"
    assert eng.b.sheet == "data1"
    assert eng.sheet_set == ["data1", "data2", "data3"]
    assert eng.sheet_index == 0
    assert eng.sheet_remaining_work() == 0


def test_s_refused_when_pending(tmp_path: Path):
    a = tmp_path / "left.xlsx"
    b = tmp_path / "right.xlsx"
    write_xlsx_sheets(
        a,
        {
            "data1": [["id", "val"], ["1", "Y"]],
            "data2": [["id", "val"], ["1", "x"]],
        },
    )
    write_xlsx_sheets(
        b,
        {
            "data1": [["id", "val"], ["1", "Yes"]],
            "data2": [["id", "val"], ["1", "x"]],
        },
    )
    eng = Engine.from_paths(str(a), str(b), ["id"], sheet_set=["data1", "data2"])
    assert eng.sheet_remaining_work() > 0
    with pytest.raises(InTuiError, match="remaining work"):
        eng.advance_sheet()
    assert eng.a.sheet == "data1"
    assert eng.pending_cells_n() == 1


def test_s_refused_when_draft(tmp_path: Path):
    a, b = _equal_pair(tmp_path, ["data1", "data2"])
    eng = Engine.from_paths(str(a), str(b), ["id"], sheet_set=["data1", "data2"])
    # Force a column draft while remaining work is 0 so the draft gate is hit.
    eng.column_draft = {"val"}
    with pytest.raises(InTuiError, match="draft"):
        eng.advance_sheet()
    assert eng.a.sheet == "data1"


def test_s_advances_when_remaining_zero(tmp_path: Path):
    a = tmp_path / "left.xlsx"
    b = tmp_path / "right.xlsx"
    write_xlsx_sheets(
        a,
        {
            "data1": [["id", "val"], ["1", "same"]],
            "data2": [["id", "val"], ["1", "Y"]],
        },
    )
    write_xlsx_sheets(
        b,
        {
            "data1": [["id", "val"], ["1", "same"]],
            "data2": [["id", "val"], ["1", "Yes"]],
        },
    )
    eng = Engine.from_paths(str(a), str(b), ["id"], sheet_set=["data1", "data2"])
    assert eng.a.sheet == "data1"
    assert eng.pending_cells_n() == 0
    eng.cell_snaps  # leftover snaps must not survive
    eng.advance_sheet()
    assert eng.a.sheet == "data2"
    assert eng.b.sheet == "data2"
    assert eng.sheet_index == 1
    assert eng.pending_cells_n() == 1
    assert eng.cell_snaps.height == 0
    assert not eng.column_draft
    assert eng.pair_draft_col is None
    assert eng.last_grain is None


def test_s_last_sheet_errors(tmp_path: Path):
    a, b = _equal_pair(tmp_path, ["Jan", "Feb"])
    eng = Engine.from_paths(str(a), str(b), ["id"], sheet_set=["Jan", "Feb"])
    eng.advance_sheet()
    assert eng.a.sheet == "Feb"
    with pytest.raises(InTuiError, match="no next sheet"):
        eng.advance_sheet()
    assert eng.a.sheet == "Feb"


def test_s_without_sheets_errors(tmp_path: Path):
    a = tmp_path / "a.xlsx"
    b = tmp_path / "b.xlsx"
    write_xlsx(a, "Foo", [["id", "val"], ["1", "x"]])
    write_xlsx(b, "Bar", [["id", "val"], ["1", "x"]])
    eng = Engine.from_paths(str(a), str(b), ["id"], "Foo", "Bar")
    with pytest.raises(InTuiError, match="-sheets"):
        eng.advance_sheet()


def test_help_documents_s_next_sheet():
    assert "next sheet" in HELP
    assert "-sheets" in HELP
    assert "Does not steal A" in HELP


def test_tui_s_footer_and_advance(tmp_path: Path):
    a = tmp_path / "left.xlsx"
    b = tmp_path / "right.xlsx"
    write_xlsx_sheets(
        a,
        {
            "data1": [["id", "val"], ["1", "same"]],
            "data2": [["id", "val"], ["1", "Y"]],
        },
    )
    write_xlsx_sheets(
        b,
        {
            "data1": [["id", "val"], ["1", "same"]],
            "data2": [["id", "val"], ["1", "Yes"]],
        },
    )
    eng = Engine.from_paths(str(a), str(b), ["id"], sheet_set=["data1", "data2"])
    app = ReconcileApp(eng, Place())

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            footer = str(app.query_one("#footer").render())
            assert "sheet 1/2 data1" in footer
            assert "S next sheet" in footer
            assert app.place.screen == "roster"
            await pilot.press("S")
            await pilot.pause()
            assert app.engine.a.sheet == "data2"
            assert app.place.screen == "roster"
            assert app.engine.pending_cells_n() == 1
            footer = str(app.query_one("#footer").render())
            assert "sheet 2/2 data2" in footer
            assert "S next sheet" not in footer
            app.action_next_sheet()
            await pilot.pause()
            assert app.tui_error and "no next sheet" in app.tui_error
            assert app.engine.a.sheet == "data2"

    asyncio.run(_run())


def test_tui_s_refused_when_pending_roster_a_and_tabs_unchanged(tmp_path: Path):
    a = tmp_path / "left.xlsx"
    b = tmp_path / "right.xlsx"
    write_xlsx_sheets(
        a,
        {
            "data1": [["id", "val"], ["1", "Y"]],
            "data2": [["id", "val"], ["1", "x"]],
        },
    )
    write_xlsx_sheets(
        b,
        {
            "data1": [["id", "val"], ["1", "Yes"]],
            "data2": [["id", "val"], ["1", "x"]],
        },
    )
    eng = Engine.from_paths(str(a), str(b), ["id"], sheet_set=["data1", "data2"])
    app = ReconcileApp(eng)

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            footer = str(app.query_one("#footer").render())
            assert "sheet 1/2 data1" in footer
            assert "S next sheet" not in footer
            app.action_next_sheet()
            await pilot.pause()
            assert app.tui_error and "remaining work" in app.tui_error
            assert app.engine.a.sheet == "data1"
            app.query_one("#grid").focus()
            app.action_accept_all()
            await pilot.pause()
            assert app.tui_error and "on the roster use a" in app.tui_error
            assert app.engine.a.sheet == "data1"
            app.action_drill()
            await pilot.pause()
            assert app.place.screen == "pair_list"
            await pilot.press("]")
            await pilot.pause()
            assert app.place.screen == "accepted"
            assert app.engine.a.sheet == "data1"

    asyncio.run(_run())
