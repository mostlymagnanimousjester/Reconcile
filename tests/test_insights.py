from pathlib import Path

from reconcile.insights import cell_insights, extra_insights, parse_unambiguous_date, unmatched_key_insights


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
