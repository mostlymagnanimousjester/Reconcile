"""Suggest extras: deterministic rename recipes below the extras lists."""

from __future__ import annotations

import asyncio
from pathlib import Path

import polars as pl

from reconcile.delimited import Detection
from reconcile.engine import Engine, Place
from reconcile.load import SideTable
from reconcile.suggest import (
    WHY_CASE,
    WHY_SEPARATORS,
    WHY_STRIP,
    WHY_TOKEN_SORT,
    canonical_tokens,
    format_preview,
    why_names,
)
from reconcile.tui import HELP, OverviewModal, ReconcileApp, _display_text
from tests.xlsxutil import write_csv


def _engine_from_frames(
    tmp_path: Path,
    a_data: dict[str, list[str]],
    b_data: dict[str, list[str]],
    keys: list[str] | None = None,
) -> Engine:
    det = Detection(encoding="utf8", delimiter=",", delimiter_name="comma")
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id\n1\n")
    write_csv(pb, "id\n1\n")
    a = SideTable(
        path=str(pa.resolve()),
        sheet=None,
        headers=list(a_data),
        frame=pl.DataFrame(a_data),
        detection=det,
    )
    b = SideTable(
        path=str(pb.resolve()),
        sheet=None,
        headers=list(b_data),
        frame=pl.DataFrame(b_data),
        detection=det,
    )
    return Engine(a, b, keys or ["id"])


def _pair_files(tmp_path: Path, a: str, b: str) -> tuple[Path, Path, Engine]:
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, a)
    write_csv(pb, b)
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    return pa, pb, eng


def test_canonical_tokens_strip_case_sep_sort():
    assert canonical_tokens(" ID-Cust ") == ("cust", "id")
    assert canonical_tokens("cust_id") == ("cust", "id")
    assert canonical_tokens("id cust") == ("cust", "id")


def test_why_names_stacks_all_four():
    why = why_names(" ID-Cust ", "cust_id")
    assert why == [WHY_STRIP, WHY_CASE, WHY_SEPARATORS, WHY_TOKEN_SORT]


def test_normalize_match_why_and_preview(tmp_path: Path):
    eng = _engine_from_frames(
        tmp_path,
        {
            "id": ["1", "2", "3"],
            " ID-Cust ": ["x", "y", "same"],
        },
        {
            "id": ["1", "2", "3"],
            "cust_id": ["x", "z", "same"],
        },
    )
    rows = eng.suggest_extras()
    assert len(rows) == 1
    rec = rows[0]
    assert rec["name_a"] == " ID-Cust "
    assert rec["name_b"] == "cust_id"
    assert rec["why"] == [WHY_STRIP, WHY_CASE, WHY_SEPARATORS, WHY_TOKEN_SORT]
    assert rec["shared"] == 3
    assert rec["pending"] == 1
    assert format_preview(rec["shared"], rec["pending"]) == "3 / 1"


def test_no_recipe_when_tokens_differ(tmp_path: Path):
    _, _, eng = _pair_files(
        tmp_path,
        "id,val,cust\n1,a,1\n",
        "id,val,customer_id\n1,a,1\n",
    )
    assert eng.extras_a == ["cust"]
    assert eng.extras_b == ["customer_id"]
    assert eng.suggest_extras() == []


def test_two_recipes_claiming_same_extra_both_shown(tmp_path: Path):
    _, _, eng = _pair_files(
        tmp_path,
        "id,val,cust_id\n1,a,1\n",
        "id,val,Cust ID,id_cust\n1,a,1,2\n",
    )
    rows = eng.suggest_extras()
    pairs = {(r["name_a"], r["name_b"]) for r in rows}
    assert pairs == {("cust_id", "Cust ID"), ("cust_id", "id_cust")}
    by_b = {r["name_b"]: r["why"] for r in rows}
    assert WHY_CASE in by_b["Cust ID"]
    assert WHY_SEPARATORS in by_b["Cust ID"]
    assert WHY_TOKEN_SORT in by_b["id_cust"]


def test_refresh_rename_makes_comparable_and_drops_suggest(tmp_path: Path):
    pa, pb, eng = _pair_files(
        tmp_path,
        "id,Cust_ID,leftover\n1,foo,x\n2,bar,x\n",
        "id,cust_id\n1,foo\n2,baz\n",
    )
    rows = eng.suggest_extras()
    assert len(rows) == 1
    assert rows[0]["name_a"] == "Cust_ID"
    assert rows[0]["name_b"] == "cust_id"
    assert WHY_CASE in rows[0]["why"]
    assert rows[0]["shared"] == 2
    assert rows[0]["pending"] == 1

    write_csv(pa, "id,cust_id,leftover\n1,foo,x\n2,bar,x\n")
    eng.refresh()
    assert "cust_id" in eng.comparable
    assert "Cust_ID" not in eng.extras_a
    assert eng.suggest_extras() == []
    assert eng.pending_cells.filter(pl.col("column") == "cust_id").height == 1


def test_help_says_suggest_is_rename_then_r():
    assert "Suggest" in HELP
    assert "rename in source files, then r" in HELP
    assert "not a mapping" in HELP.lower()
    assert "#grid" not in HELP


def test_tui_omits_suggest_block_when_no_recipe(tmp_path: Path):
    _, _, eng = _pair_files(
        tmp_path,
        "id,val,cust\n1,a,1\n",
        "id,val,customer_id\n1,a,1\n",
    )
    app = ReconcileApp(eng, Place(screen="extras"))

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.place.screen == "extras"
            assert app.query("#grid")
            assert not app.query("#suggest-block")
            assert not app.query("#suggest")

    asyncio.run(_run())


def test_tui_suggest_below_grid_not_on_overview(tmp_path: Path):
    _, _, eng = _pair_files(
        tmp_path,
        "id,val,cust_id\n1,a,1\n",
        "id,val,Cust ID\n1,a,2\n",
    )
    app = ReconcileApp(eng, Place(screen="extras"))

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.place.screen == "extras"
            assert app.query("#grid")
            assert app.query("#suggest-block")
            suggest = app.query_one("#suggest")
            assert suggest.can_focus is False
            # Suggest sits in the extras Vertical, below #grid.
            extras = app.query_one("#grid").parent
            ids = [c.id for c in extras.children]
            assert ids.index("grid") < ids.index("suggest-block")
            title = str(app.query_one("#suggest-title").render())
            assert "rename" in title.lower()
            assert "then r" in title
            row = suggest.get_row_at(0)
            assert str(row[0]) == _display_text("cust_id")
            assert str(row[1]) == _display_text("Cust ID")
            assert "case" in str(row[2])
            assert " / " in str(row[3])
            labels = [str(col.label) for col in suggest.columns.values()]
            assert "normalizers" in labels
            assert "keys (shared / still different)" in labels

            app.action_overview()
            await pilot.pause()
            assert isinstance(app.screen, OverviewModal)
            labels = [str(app.screen.query_one("#ov-entries").get_row_at(i)[0]) for i in range(3)]
            assert "Suggest" not in labels
            assert "Mismatched columns" in labels

    asyncio.run(_run())


def test_suggest_grid_uses_display_text_for_edge_space(tmp_path: Path):
    eng = _engine_from_frames(
        tmp_path,
        {"id": ["1"], "val": ["a"], "cust_id": ["1"]},
        {"id": ["1"], "val": ["b"], "Cust ID ": ["2"]},
    )
    app = ReconcileApp(eng, Place(screen="extras"))

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            suggest = app.query_one("#suggest")
            row = suggest.get_row_at(0)
            assert str(row[0]) == _display_text("cust_id")
            assert str(row[1]) == _display_text("Cust ID ")
            assert str(row[1]).endswith("·")

    asyncio.run(_run())


def test_a_accepts_extra_not_suggestion(tmp_path: Path):
    _, _, eng = _pair_files(
        tmp_path,
        "id,val,aaa,cust_id\n1,a,1,9\n",
        "id,val,Cust ID\n1,a,2\n",
    )
    app = ReconcileApp(
        eng, Place(screen="extras", extra_side="A", extra_name="aaa")
    )

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query("#suggest-block")
            rec = app._focused_rec()
            assert rec is not None and rec["name"] == "aaa"
            app.query_one("#grid").focus()
            await pilot.press("a")
            await pilot.pause()
            assert ("A", "aaa") in app.engine.accepted_extras
            assert ("A", "cust_id") in app.engine.pending_extras
            assert ("B", "Cust ID") in app.engine.pending_extras
            # Suggestion is not an extra; pairing did not happen.
            assert "cust_id" not in app.engine.comparable
            assert app.engine.suggest_extras()
            focused = app._focused_rec()
            assert focused is not None
            assert focused["name"] != "aaa"

    asyncio.run(_run())


def test_tui_refresh_drops_suggest_after_rename(tmp_path: Path):
    pa, pb, eng = _pair_files(
        tmp_path,
        "id,Cust_ID,leftover\n1,foo,x\n2,bar,x\n",
        "id,cust_id\n1,foo\n2,baz\n",
    )
    app = ReconcileApp(eng, Place(screen="extras"))

    async def _run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.place.screen == "extras"
            assert app.query("#suggest-block")
            write_csv(pa, "id,cust_id,leftover\n1,foo,x\n2,bar,x\n")
            app.action_refresh()
            await pilot.pause()
            assert app.engine.suggest_extras() == []
            assert "cust_id" in app.engine.comparable
            assert app.place.screen == "extras"
            assert app.query("#grid")
            assert not app.query("#suggest-block")

    asyncio.run(_run())
