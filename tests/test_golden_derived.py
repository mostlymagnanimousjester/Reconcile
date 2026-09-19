"""Characterization: lock current derived-state bytes and order.

These must pass on the current implementation before any kernel edit.
After a kernel edit, a failure here is a behavior change — stop and fix.
"""

from __future__ import annotations

from pathlib import Path

from reconcile.engine import Engine, Place
from reconcile.insights import extra_insights
from tests.xlsxutil import write_csv


def _engine(tmp_path: Path, a: str, b: str, keys: list[str] | None = None) -> Engine:
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, a)
    write_csv(pb, b)
    return Engine.from_paths(
        str(pa), str(pb), keys or ["id"], a_delim=",", b_delim=","
    )


def _snap_tuples(eng: Engine) -> set[tuple[str, ...]]:
    cols = [*eng.keys, "column", "val_a", "val_b"]
    return {tuple(str(r[c]) for c in cols) for r in eng.cell_snaps.to_dicts()}


def test_golden_pair_groups_order_and_next_lever(tmp_path: Path):
    """Two pairs share n; one larger n. Exact to_dicts order; next lever is row 0."""
    eng = _engine(
        tmp_path,
        "id,val\n1,Y\n2,Y\n3,Y\n4,N\n5,N\n6,Z\n7,Z\n",
        "id,val\n1,Yes\n2,Yes\n3,Yes\n4,No\n5,No\n6,Zed\n7,Zed\n",
    )
    groups = eng.pair_groups("val")
    assert groups.to_dicts() == [
        {"val_a": "Y", "val_b": "Yes", "n": 3},
        {"val_a": "N", "val_b": "No", "n": 2},
        {"val_a": "Z", "val_b": "Zed", "n": 2},
    ]
    top = groups.row(0, named=True)
    place = eng.next_lever_place(Place(column="val"))
    assert place.pair_val_a == top["val_a"]
    assert place.pair_val_b == top["val_b"]
    assert (place.pair_val_a, place.pair_val_b) == ("Y", "Yes")


def test_golden_roster_tags_bytes(tmp_path: Path):
    """Sentinel A/B/both, trim, case, numeric, ws, same-date, non-constant side."""
    eng = _engine(
        tmp_path,
        "id,sent_a,sent_b,sent_both,trim_c,case_c,num_c,ws_c,date_c,mixed,plain\n"
        "1,0,x,NA, Y,Yes,1,a\u00a0,2020-01-02,0,foo\n"
        "2,0,y,NA, Y,Yes,1,a\u00a0,2020-01-02,1,bar\n",
        "id,sent_a,sent_b,sent_both,trim_c,case_c,num_c,ws_c,date_c,mixed,plain\n"
        "1,x,,z,Y,yes,1.0,a,20200102,x,baz\n"
        "2,y,,z,Y,yes,1.0,a,20200102,y,qux\n",
    )
    by_name = {r.name: r for r in eng.roster() if r.kind == "column"}
    expected = {
        "sent_a": {
            "pending": 2,
            "accepted": 0,
            "equal": "0",
            "categorical": "yes",
            "top_pair_pct": "50%",
            "speculative": "sentinel A=0",
        },
        "sent_b": {
            "pending": 2,
            "accepted": 0,
            "equal": "0",
            "categorical": "yes",
            "top_pair_pct": "50%",
            "speculative": 'sentinel B=""',
        },
        "sent_both": {
            "pending": 2,
            "accepted": 0,
            "equal": "0",
            "categorical": "yes",
            "top_pair_pct": "100%",
            "speculative": "sentinel both A=NA B=z",
        },
        "trim_c": {
            "pending": 2,
            "accepted": 0,
            "equal": "0",
            "categorical": "yes",
            "top_pair_pct": "100%",
            "speculative": 'sentinel both A=" Y" B=Y, equal if trim, invisible/odd whitespace',
        },
        "case_c": {
            "pending": 2,
            "accepted": 0,
            "equal": "0",
            "categorical": "yes",
            "top_pair_pct": "100%",
            "speculative": "sentinel both A=Yes B=yes, equal if case-fold",
        },
        "num_c": {
            "pending": 2,
            "accepted": 0,
            "equal": "0",
            "categorical": "yes",
            "top_pair_pct": "100%",
            "speculative": "sentinel both A=1 B=1.0, equal as numbers",
        },
        "ws_c": {
            "pending": 2,
            "accepted": 0,
            "equal": "0",
            "categorical": "yes",
            "top_pair_pct": "100%",
            "speculative": 'sentinel both A="a\xa0" B=a, equal if trim, invisible/odd whitespace',
        },
        "date_c": {
            "pending": 2,
            "accepted": 0,
            "equal": "0",
            "categorical": "yes",
            "top_pair_pct": "100%",
            "speculative": "sentinel both A=2020-01-02 B=20200102, same date",
        },
        "mixed": {
            "pending": 2,
            "accepted": 0,
            "equal": "0",
            "categorical": "yes",
            "top_pair_pct": "50%",
            "speculative": "",
        },
        "plain": {
            "pending": 2,
            "accepted": 0,
            "equal": "0",
            "categorical": "yes",
            "top_pair_pct": "50%",
            "speculative": "",
        },
    }
    assert set(by_name) == set(expected)
    for name, fields in expected.items():
        row = by_name[name]
        got = {
            "pending": row.pending,
            "accepted": row.accepted,
            "equal": row.equal,
            "categorical": row.categorical,
            "top_pair_pct": row.top_pair_pct,
            "speculative": row.speculative,
        }
        assert got == fields, name
        assert "speculative:" not in row.speculative
        assert "shared value pattern" not in row.speculative


def test_golden_confirm_column_draft_matches_two_accept_column(tmp_path: Path):
    a = "id,Status,Flag\n1,Y,1\n2,Y,2\n"
    b = "id,Status,Flag\n1,Yes,9\n2,Yes,8\n"

    drafted = _engine(tmp_path, a, b)
    drafted.column_draft = {"Status", "Flag"}
    n_draft = drafted.confirm_column_draft()

    one = _engine(tmp_path, a, b)
    n_one = one.accept_column("Status")
    two = _engine(tmp_path, a, b)
    n_two = two.accept_column("Flag")
    sequential = _engine(tmp_path, a, b)
    n_seq = sequential.accept_column("Status") + sequential.accept_column("Flag")

    assert n_draft == n_seq == n_one + n_two == 4
    assert drafted.pending_cells.height == sequential.pending_cells.height == 0
    assert drafted.accepted_cells.height == sequential.accepted_cells.height == 4
    assert drafted.cell_snaps.height == sequential.cell_snaps.height == 4
    assert _snap_tuples(drafted) == _snap_tuples(sequential)
    assert _snap_tuples(drafted) == _snap_tuples(one) | _snap_tuples(two)


def test_golden_extras_tags_slice(tmp_path: Path):
    eng = _engine(
        tmp_path,
        "id,val,Customer_ID ,cust\n1,a,1,2\n",
        "id,val,customer_id,customer,CUST\n1,a,1,2,3\n",
    )
    others = {"A": list(eng.b.headers), "B": list(eng.a.headers)}
    extras = eng.extras_rows()
    assert extras, "expected extras rows"
    for rec in extras:
        tags = extra_insights(rec["name"], others[rec["side"]])
        assert rec["speculative"] == tags[:3]
        roster = next(
            r
            for r in eng.roster()
            if r.kind == "extra" and r.name == rec["name"] and r.side == rec["side"]
        )
        assert roster.speculative == ", ".join(tags[:2])
    # Lock a 3-tag extra so extras UI [:3] vs roster [:2] cannot drift.
    cust = next(r for r in extras if r["name"] == "cust" and r["side"] == "A")
    assert cust["speculative"] == [
        "near-miss 'customer_id'",
        "near-miss 'customer'",
        "name would pair if case",
    ]
    roster_cust = next(
        r for r in eng.roster() if r.kind == "extra" and r.name == "cust" and r.side == "A"
    )
    assert roster_cust.speculative == (
        "near-miss 'customer_id', near-miss 'customer'"
    )


def test_golden_pair_page_context_flag_truncation(tmp_path: Path):
    flags = (
        ["red"] * 3
        + ["blue"] * 2
        + ["green"]
        + ["orange"]
        + ["pink"]
        + ["purple"]
        + ["yellow"]
    )
    a_rows = ["id,val,Flag"] + [
        f"{i},{'Y' if i < 10 else 'N'},{flags[i] if i < 10 else 'z'}" for i in range(12)
    ]
    b_rows = ["id,val,Flag"] + [
        f"{i},{'Yes' if i < 10 else 'No'},{flags[i] if i < 10 else 'z'}" for i in range(12)
    ]
    eng = _engine(tmp_path, "\n".join(a_rows) + "\n", "\n".join(b_rows) + "\n")
    eng.context_columns["val"] = ["Flag"]
    recs, page, pages = eng.pair_page("val", 0)
    assert page == 0
    assert pages == 1
    assert recs == [
        {
            "val_a": "Y",
            "val_b": "Yes",
            "n": 10,
            "Flag__ctx": "red 3 | blue 2 | green 1 | orange 1 | pink 1 …",
        },
        {"val_a": "N", "val_b": "No", "n": 2, "Flag__ctx": "z 2"},
    ]


def test_golden_pair_page_context_flag_region(tmp_path: Path):
    eng = _engine(
        tmp_path,
        "id,val,Flag,Region\n"
        "1,Y,red,east\n2,Y,red,east\n3,Y,blue,west\n4,Y,green,west\n",
        "id,val,Flag,Region\n"
        "1,Yes,red,east\n2,Yes,red,east\n3,Yes,blue,west\n4,Yes,green,west\n",
    )
    eng.context_columns["val"] = ["Flag", "Region"]
    recs, _, _ = eng.pair_page("val", 0)
    assert recs == [
        {
            "val_a": "Y",
            "val_b": "Yes",
            "n": 4,
            "Flag__ctx": "red 2 | blue 1 | green 1",
            "Region__ctx": "east 2 | west 2",
        }
    ]


def test_golden_returned_matrix_matches_per_pair(tmp_path: Path):
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_csv(pa, "id,val\n1,Y\n2,Y\n3,N\n")
    write_csv(pb, "id,val\n1,Yes\n2,Yes\n3,No\n")
    eng = Engine.from_paths(str(pa), str(pb), ["id"], a_delim=",", b_delim=",")
    eng.accept_pair("val", "Y", "Yes")
    write_csv(pa, "id,val\n1,Y2\n2,Y2\n3,N\n")
    write_csv(pb, "id,val\n1,Yes2\n2,Yes2\n3,No\n")
    eng.refresh()
    pairs = [(r["val_a"], r["val_b"]) for r in eng.pair_groups("val").to_dicts()]
    per_pair = {p for p in pairs if eng.pair_has_returned("val", p[0], p[1])}
    assert per_pair == {("Y2", "Yes2")}
    mask_fn = getattr(eng, "pairs_returned_mask", None)
    if mask_fn is not None:
        batch = mask_fn("val", pairs)
        assert set(p for p, hit in zip(pairs, batch) if hit) == per_pair
    else:
        assert per_pair == {p for p in pairs if eng.pair_has_returned("val", p[0], p[1])}
