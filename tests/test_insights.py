from pathlib import Path

from reconcile.engine import Engine
from reconcile.insights import (
    CONTEXT_TRUNCATION_MARK,
    cell_insights,
    extra_insights,
    format_top_uniques,
    parse_unambiguous_date,
    same_date_expr,
    sentinel_side_tag,
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


def test_sentinel_side_tag_a_b_or_both():
    assert sentinel_side_tag(True, False) == "speculative: sentinel A"
    assert sentinel_side_tag(False, True) == "speculative: sentinel B"
    assert sentinel_side_tag(True, True) == "speculative: sentinel both"
    assert sentinel_side_tag(False, False) is None


def test_cell_insights_flag_sentinel_on_a_b_or_both():
    tags = cell_insights("NA", "Yes")
    assert any("sentinel A" in t for t in tags)
    tags = cell_insights("Yes", "0")
    assert any("sentinel B" in t for t in tags)
    tags = cell_insights("NA", "")
    assert any("sentinel both" in t for t in tags)
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
    assert "speculative: same date" in row.speculative
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
    write_csv(pa, "id,only_a,only_b,both,plain\n1,NA,x,NA,foo\n2,NA,y,z,bar\n")
    write_csv(pb, "id,only_a,only_b,both,plain\n1,x,0,x,baz\n2,y,0,0,qux\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    by_name = {r.name: r.speculative for r in eng.roster() if r.kind == "column"}
    assert "speculative: sentinel A" in by_name["only_a"]
    assert "sentinel B" not in by_name["only_a"]
    assert "speculative: sentinel B" in by_name["only_b"]
    assert "sentinel A" not in by_name["only_b"]
    assert "speculative: sentinel both" in by_name["both"]
    assert "sentinel" not in by_name["plain"]
