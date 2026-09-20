from pathlib import Path

import pytest

from reconcile.engine import Engine, InTuiError
from reconcile.insights import (
    cell_insights,
    context_group_header,
    format_context_truncation,
    format_context_tuple,
    format_sentinel_both,
    format_sentinel_insight,
    format_sentinel_value,
    format_top_uniques,
    parse_unambiguous_date,
    same_date_expr,
)
from reconcile.roster import roster_visible_insight_headers
from tests.xlsxutil import write_csv


def test_format_top_uniques_keeps_five_and_marks_truncation():
    values = (
        ["red"] * 5
        + ["blue"] * 4
        + ["green"] * 3
        + ["orange"] * 2
        + ["pink"] * 2
        + ["purple"]
        + ["yellow"]
    )
    text = format_top_uniques(values)
    assert text == f"red×5 | blue×4 | green×3 | orange×2 | pink×2{format_context_truncation(2)}"
    assert "purple" not in text
    assert "yellow" not in text
    assert format_top_uniques(["a", "a", "b"]) == "a×2 | b×1"
    assert format_top_uniques(["", "x", ""]) == "(empty)×2 | x×1"
    tuples = ["red|east"] * 3 + ["blue|west"] * 2 + ["green|west"]
    assert format_top_uniques(tuples) == "red|east×3 | blue|west×2 | green|west×1"
    assert format_context_tuple(["red", "east"]) == "red|east"
    assert format_context_tuple(["", "west"]) == "(empty)|west"
    assert context_group_header(0, ["Flag", "Region"]) == "g0 Flag+Region"


def test_format_sentinel_insight_shows_side_and_value():
    assert format_sentinel_value("") == '""'
    assert format_sentinel_value("0") == "0"
    assert format_sentinel_insight("0", None) == "sentinel A=0"
    assert format_sentinel_insight(None, "") == 'sentinel B=""'
    assert format_sentinel_insight("x", "y") == "sentinel both A=x B=y"
    assert format_sentinel_insight(None, None) is None
    assert format_sentinel_both("NA", "z") == "A=NA B=z"


def test_cell_insights_do_not_treat_token_lists_as_sentinels():
    tags = cell_insights("NA", "Yes")
    assert not any("sentinel" in t for t in tags)
    tags = cell_insights("Yes", "0")
    assert not any("sentinel" in t for t in tags)
    tags = cell_insights("NA", "")
    assert not any("sentinel" in t for t in tags)
    tags = cell_insights("Yes", "No")
    assert not any("sentinel" in t for t in tags)


def test_trim_case_numeric_whitespace():
    tags = cell_insights(" Y", "Y")
    assert any("trim" in t for t in tags)
    assert not any("whitespace" in t for t in tags)
    tags = cell_insights("Yes", "yes")
    assert "case" in tags
    tags = cell_insights("1", "1.0")
    assert "num" in tags
    tags = cell_insights("a\u00a0", "a")
    assert any("trim" in t for t in tags)
    assert not any("whitespace" in t for t in tags)


def test_same_date_unambiguous():
    tags = cell_insights("2020-01-02", "01/02/2020")
    # 01/02/2020 is US Jan 2 AND EU 1 Feb → ambiguous → no same date
    assert "date" not in tags
    tags = cell_insights("2020-01-02", "20200102")
    assert "date" in tags
    tags = cell_insights("2020-01-01", "01/01/2020")
    assert "date" in tags
    tags = cell_insights("15JAN2024", "2024-01-15")
    assert "date" in tags
    tags = cell_insights("15JAN24", "20240115")
    assert "date" in tags
    tags = cell_insights("15-JAN-2024", "2024-01-15")
    assert "date" in tags
    tags = cell_insights("JAN2024", "2024-01-01")
    assert "date" in tags
    tags = cell_insights("15JAN2024:14:30:00", "2024-01-15T14:30:00")
    assert "date" in tags


def test_ambiguous_us_eu_emits_nothing_for_date():
    assert parse_unambiguous_date("01/02/2020") is None


def test_roster_same_date_all_pending_and_sas_formats(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(
        pa,
        "id,all_iso,mixed,date9,date7,date11,monyy,dt\n"
        "1,2020-01-02,2020-01-02,15JAN2024,15JAN24,15-JAN-2024,JAN2024,15JAN2024:14:30:00\n"
        "2,2020-01-02,nope,15JAN2024,15JAN24,15-JAN-2024,JAN2024,15JAN2024:14:30:00\n",
    )
    write_csv(
        pb,
        "id,all_iso,mixed,date9,date7,date11,monyy,dt\n"
        "1,20200102,20200102,2024-01-15,20240115,2024-01-15,2024-01-01,2024-01-15T14:30:00\n"
        "2,20200102,nope2,2024-01-15,20240115,2024-01-15,2024-01-01,2024-01-15T14:30:00\n",
    )
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    by_name = {r.name: r for r in eng.roster() if r.kind == "column"}
    assert by_name["all_iso"].date == "y"
    assert by_name["mixed"].date == "n"
    assert by_name["date9"].date == "y"
    assert by_name["date7"].date == "y"
    assert by_name["date11"].date == "y"
    assert by_name["monyy"].date == "y"
    assert by_name["dt"].date == "y"
    import polars as pl

    pending = eng.pending_cells.filter(pl.col("column") == "all_iso")
    assert pending.select(same_date_expr().all()).item() is True
    mixed = eng.pending_cells.filter(pl.col("column") == "mixed")
    assert mixed.select(same_date_expr().all()).item() is False


def test_roster_ambiguous_us_eu_date_not_same_date(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,d\n1,2020-01-02\n")
    write_csv(pb, "id,d\n1,01/02/2020\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    row = next(r for r in eng.roster() if r.name == "d")
    assert row.date == "n"


def test_roster_speculation_flags_sentinel_on_a_b_or_both(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,only_a,only_b,both,plain\n1,0,x,NA,foo\n2,0,y,NA,bar\n")
    write_csv(pb, "id,only_a,only_b,both,plain\n1,x,,z,baz\n2,y,,z,qux\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    by_name = {r.name: r for r in eng.roster() if r.kind == "column"}
    assert by_name["only_a"].sent_a == "0"
    assert by_name["only_a"].sent_b == ""
    assert by_name["only_a"].sent_both == ""
    assert by_name["only_b"].sent_b == '""'
    assert by_name["only_b"].sent_a == ""
    assert by_name["only_b"].sent_both == ""
    assert by_name["both"].sent_a == "NA"
    assert by_name["both"].sent_b == "z"
    assert by_name["both"].sent_both == "A=NA B=z"
    assert by_name["plain"].sent_a == ""
    assert by_name["plain"].sent_b == ""
    headers = {h for h, _ in roster_visible_insight_headers(eng.column_roster())}
    assert "const A" in headers
    assert "const B" in headers
    assert "const both" in headers

    pb2 = tmp_path / "b_only.csv"
    pa2 = tmp_path / "a_only_b.csv"
    write_csv(pa2, "id,only_b\n1,x\n2,y\n")
    write_csv(pb2, "id,only_b\n1,\n2,\n")
    eng_b = Engine.from_paths(str(pa2), str(pb2), ["id"], a_delim=",", b_delim=",")
    hide = {h for h, _ in roster_visible_insight_headers(eng_b.column_roster())}
    assert "const B" in hide
    assert "const A" not in hide
    assert "const both" not in hide


def test_sentinel_uses_comparable_rows_not_unmatched_keys(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,col\n1,0\n2,0\n99,999\n")
    write_csv(pb, "id,col\n1,x\n2,y\n88,888\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    row = next(r for r in eng.roster() if r.name == "col")
    assert row.sent_a == "0"
    assert "999" not in row.sent_a
    assert "888" not in row.sent_a
    assert row.sent_b == ""
    assert eng.pending_a_only_n() == 1
    assert eng.pending_b_only_n() == 1
    n = eng.start_sentinel_draft("A", "0")
    assert n == 1
    assert eng.column_draft == {"col"}
    eng.cancel_drafts()
    with pytest.raises(InTuiError, match="0 pending"):
        eng.start_sentinel_draft("A", "999")


def test_non_constant_side_is_not_sentinel(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    # pending A is all 0, but an equal comparable row has A=1
    write_csv(pa, "id,col\n1,1\n2,0\n3,0\n")
    write_csv(pb, "id,col\n1,1\n2,x\n3,y\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    row = next(r for r in eng.roster() if r.name == "col")
    assert row.sent_a == ""
    assert row.sent_b == ""
    assert row.sent_both == ""
    with pytest.raises(InTuiError, match="0 pending"):
        eng.start_sentinel_draft("A", "0")


def test_roster_insight_text_has_no_speculative_prefix(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,d,flag\n1,2020-01-02,Y\n2,nope,N\n")
    write_csv(pb, "id,d,flag\n1,20200102,Yes\n2,nope2,No\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    for row in eng.roster():
        if row.kind != "column":
            continue
        assert "speculative:" not in row.speculative
        assert "speculative:" not in row.sent_a
        assert "speculative:" not in row.sent_both
        assert "shared value pattern" not in row.speculative
        assert "pattern" not in row.speculative


def test_shared_value_pattern_insight_is_gone(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,flag\n1,Y\n2,N\n")
    write_csv(pb, "id,flag\n1,Yes\n2,No\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    row = next(r for r in eng.roster() if r.name == "flag")
    assert "shared value pattern" not in row.speculative
    assert "pattern" not in row.speculative
    assert row.sent_a == ""
    assert row.trim == "n"
    from reconcile import insights

    assert not hasattr(insights, "column_pattern_insight")
