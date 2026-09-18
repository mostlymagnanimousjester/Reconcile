from pathlib import Path

import pytest

from reconcile.engine import Engine, InTuiError
from reconcile.insights import (
    CONTEXT_TRUNCATION_MARK,
    cell_insights,
    extra_insights,
    format_sentinel_insight,
    format_sentinel_value,
    format_top_uniques,
    parse_unambiguous_date,
    same_date_expr,
    unmatched_key_insights,
)
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
    assert text == f"red · blue · green · orange · pink{CONTEXT_TRUNCATION_MARK}"
    assert "purple" not in text
    assert "yellow" not in text
    assert format_top_uniques(["a", "a", "b"]) == "a · b"
    assert format_top_uniques(["", "x", ""]) == "(empty) · x"


def test_format_sentinel_insight_shows_side_and_value():
    assert format_sentinel_value("") == '""'
    assert format_sentinel_value("0") == "0"
    assert format_sentinel_insight("0", None) == "sentinel A=0"
    assert format_sentinel_insight(None, "") == 'sentinel B=""'
    assert format_sentinel_insight("x", "y") == "sentinel both A=x B=y"
    assert format_sentinel_insight(None, None) is None


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
    tags = cell_insights("Yes", "yes")
    assert any("case-fold" in t for t in tags)
    tags = cell_insights("1", "1.0")
    assert any("numbers" in t for t in tags)
    tags = cell_insights("a\u00a0", "a")
    assert any("whitespace" in t for t in tags)


def test_same_date_unambiguous():
    tags = cell_insights("2020-01-02", "01/02/2020")
    # 01/02/2020 is US Jan 2 AND EU 1 Feb → ambiguous → no same date
    assert not any("same date" in t for t in tags)
    tags = cell_insights("2020-01-02", "20200102")
    assert any("same date" in t for t in tags)
    tags = cell_insights("2020-01-01", "01/01/2020")
    assert any("same date" in t for t in tags)


def test_ambiguous_us_eu_emits_nothing_for_date():
    assert parse_unambiguous_date("01/02/2020") is None


def test_extra_near_miss():
    tags = extra_insights("cust_id", ["customer_id", "val"])
    assert any("near-miss" in t or "speculative" in t for t in tags)


def test_unmatched_trim():
    tags = unmatched_key_insights((" A ",), [("A",)])
    assert tags


def test_roster_same_date_via_polars_any(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,d,other\n1,2020-01-02,x\n2,nope,y\n")
    write_csv(pb, "id,d,other\n1,20200102,x\n2,nope2,y\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    row = next(r for r in eng.roster() if r.name == "d")
    assert "same date" in row.speculative
    assert "speculative:" not in row.speculative
    import polars as pl

    pending = eng.pending_cells.filter(pl.col("column") == "d")
    assert pending.select(same_date_expr().any()).item() is True


def test_roster_ambiguous_us_eu_date_not_same_date(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,d\n1,2020-01-02\n")
    write_csv(pb, "id,d\n1,01/02/2020\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    row = next(r for r in eng.roster() if r.name == "d")
    assert "same date" not in row.speculative


def test_roster_speculation_flags_sentinel_on_a_b_or_both(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,only_a,only_b,both,plain\n1,0,x,NA,foo\n2,0,y,NA,bar\n")
    write_csv(pb, "id,only_a,only_b,both,plain\n1,x,,z,baz\n2,y,,z,qux\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    by_name = {r.name: r.speculative for r in eng.roster() if r.kind == "column"}
    assert "sentinel A=0" in by_name["only_a"]
    assert "sentinel B" not in by_name["only_a"]
    assert 'sentinel B=""' in by_name["only_b"]
    assert "sentinel A" not in by_name["only_b"]
    assert by_name["both"] == "sentinel both A=NA B=z"
    assert "speculative:" not in by_name["both"]
    assert "sentinel" not in by_name["plain"]


def test_sentinel_uses_comparable_rows_not_unmatched_keys(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,col\n1,0\n2,0\n99,999\n")
    write_csv(pb, "id,col\n1,x\n2,y\n88,888\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    row = next(r for r in eng.roster() if r.name == "col")
    assert "sentinel A=0" in row.speculative
    assert "999" not in row.speculative
    assert "888" not in row.speculative
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
    assert "sentinel" not in row.speculative
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
    from reconcile import insights

    assert not hasattr(insights, "column_pattern_insight")
