"""Roster cache, vectorized tags, and next-lever place."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import polars as pl

from reconcile.engine import Place, RosterRow
from reconcile.insights import extra_insights, same_date_expr

if TYPE_CHECKING:
    from reconcile.engine import Engine


def roster(eng: Engine, name_filter: str = "") -> list[RosterRow]:
    rows = list(eng._roster_cache)
    if name_filter:
        needle = name_filter.lower()
        rows = [r for r in rows if needle in r.name.lower()]
    return rows


def visible_column_roster(eng: Engine, name_filter: str = "") -> list[RosterRow]:
    """Pending comparable columns only.

    Columns whose *shared* (matched-key) rows are all equal have pending 0 and
    are treated as auto-accepted: there is nothing to review. A-only / B-only
    keys never contribute cell pending, so they cannot keep such a column on
    the roster. Accepted columns (pending 0 after snapshots) are hidden too.
    Unmatched-key and extra rows are remaining work, not columns.
    """
    return [r for r in roster(eng, name_filter) if r.kind == "column" and r.pending > 0]


def _roster_agg_maps(
    eng: Engine,
) -> tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, dict[str, Any]]]:
    pending_by_col: dict[str, int] = {}
    accepted_by_col: dict[str, int] = {}
    top_pair: dict[str, int] = {}
    col_stats: dict[str, dict[str, Any]] = {}
    if not eng.pending_cells.is_empty():
        for rec in eng.pending_cells.group_by("column").len().to_dicts():
            pending_by_col[rec["column"]] = rec["len"]
        grouped = (
            eng.pending_cells.group_by(["column", "val_a", "val_b"])
            .len()
            .sort(["column", "len"], descending=[False, True])
        )
        for rec in grouped.group_by("column").first().to_dicts():
            top_pair[rec["column"]] = rec["len"]
        stats = eng.pending_cells.group_by("column").agg(
            (pl.col("val_a").str.strip_chars() == pl.col("val_b").str.strip_chars()).any().alias("trim"),
            (pl.col("val_a").str.to_lowercase() == pl.col("val_b").str.to_lowercase()).any().alias("case"),
            (
                pl.col("val_a").str.strip_chars().str.to_lowercase()
                == pl.col("val_b").str.strip_chars().str.to_lowercase()
            ).any().alias("both"),
            (
                pl.col("val_a").cast(pl.Float64, strict=False).is_not_null()
                & pl.col("val_b").cast(pl.Float64, strict=False).is_not_null()
                & (pl.col("val_a").str.strip_chars() != "")
                & (pl.col("val_b").str.strip_chars() != "")
                & (
                    pl.col("val_a").cast(pl.Float64, strict=False)
                    == pl.col("val_b").cast(pl.Float64, strict=False)
                )
            ).any().alias("numeric"),
            (
                pl.col("val_a").str.contains(r"[\u00a0\t\r\n]")
                | pl.col("val_b").str.contains(r"[\u00a0\t\r\n]")
                | (pl.col("val_a") != pl.col("val_a").str.strip_chars())
                | (pl.col("val_b") != pl.col("val_b").str.strip_chars())
            ).any().alias("ws"),
            same_date_expr().any().alias("same_date"),
            pl.col("val_a").n_unique().alias("n_a"),
            pl.col("val_b").n_unique().alias("n_b"),
            pl.col("val_a").unique().sort().alias("ua"),
            pl.col("val_b").unique().sort().alias("ub"),
        ).with_columns(
            pl.col("ua").list.concat(pl.col("ub")).list.unique().list.len().alias("n_ab"),
            (pl.col("ua") != pl.col("ub")).alias("pattern"),
        ).drop("ua", "ub")
        for rec in stats.to_dicts():
            col_stats[rec["column"]] = rec
    if not eng.accepted_cells.is_empty():
        for rec in eng.accepted_cells.group_by("column").len().to_dicts():
            accepted_by_col[rec["column"]] = rec["len"]
    return pending_by_col, accepted_by_col, top_pair, col_stats


def _unmatched_side_tags(eng: Engine, side: str) -> str:
    pending = eng.pending_a_only if side == "A" else eng.pending_b_only
    other = eng.pending_b_only if side == "A" else eng.pending_a_only
    if pending.is_empty() or other.is_empty():
        return ""
    trim_cols = [pl.col(k).str.strip_chars().alias(k) for k in eng.keys]
    if not pending.select(trim_cols).join(other.select(trim_cols), on=eng.keys, how="inner").is_empty():
        return "speculative: would match if trim"
    case_cols = [pl.col(k).str.to_lowercase().alias(k) for k in eng.keys]
    if not pending.select(case_cols).join(other.select(case_cols), on=eng.keys, how="inner").is_empty():
        return "speculative: would match if case-fold"
    fold_cols = [pl.col(k).str.strip_chars().str.to_lowercase().alias(k) for k in eng.keys]
    if not pending.select(fold_cols).join(other.select(fold_cols), on=eng.keys, how="inner").is_empty():
        return "speculative: would match if trim/case"
    return ""


def _build_roster_cache(eng: Engine) -> list[RosterRow]:
    pending_by_col, accepted_by_col, top_pair, col_stats = _roster_agg_maps(eng)
    rows: list[RosterRow] = []
    matched_n = eng.matched_a.height
    mismatch_n: dict[str, int] = {}
    if not eng.mismatches.is_empty():
        for rec in eng.mismatches.group_by("column").len().to_dicts():
            mismatch_n[rec["column"]] = rec["len"]
    for col in eng.comparable:
        pend = pending_by_col.get(col, 0)
        acc = accepted_by_col.get(col, 0)
        conc = (top_pair.get(col, 0) / pend) if pend else 0.0
        pct = f"{conc * 100:.0f}%" if pend else "—"
        st = col_stats.get(col)
        if not pend or st is None:
            cat = "—"
            tags = ""
        else:
            n_a, n_b, n_ab = int(st["n_a"]), int(st["n_b"]), int(st["n_ab"])
            cat = "yes" if n_a <= 30 and n_b <= 30 and n_ab <= 50 else "no"
            tag_bits: list[str] = []
            if st["trim"]:
                tag_bits.append("speculative: equal if trim")
            if st["case"]:
                tag_bits.append("speculative: equal if case-fold")
            if st["both"] and not st["trim"] and not st["case"]:
                tag_bits.append("speculative: equal if trim+case")
            if st["numeric"]:
                tag_bits.append("speculative: equal as numbers")
            if st["ws"]:
                tag_bits.append("speculative: invisible/odd whitespace")
            if st["same_date"]:
                tag_bits.append("speculative: same date")
            if st["pattern"] and n_a <= 6 and n_b <= 6:
                tag_bits.append("speculative: shared value pattern")
            tags = ", ".join(tag_bits[:3])
        equal = str(matched_n - mismatch_n.get(col, 0))
        rows.append(
            RosterRow(
                kind="column",
                name=col,
                side="—",
                pending=pend,
                concentration=conc,
                top_pair_pct=pct,
                accepted=acc,
                equal=equal,
                categorical=cat,
                speculative=tags,
                returned=eng.column_has_returned(col),
            )
        )
    if eng.a_only.height > 0:
        rows.append(
            RosterRow(
                kind="A-only",
                name="A-only keys",
                side="A",
                pending=eng.pending_a_only_n(),
                concentration=0.0,
                top_pair_pct="—",
                accepted=eng.accepted_a_only.height,
                equal="—",
                categorical="—",
                speculative=_unmatched_side_tags(eng, "A"),
                returned=eng.side_has_returned("A"),
            )
        )
    if eng.b_only.height > 0:
        rows.append(
            RosterRow(
                kind="B-only",
                name="B-only keys",
                side="B",
                pending=eng.pending_b_only_n(),
                concentration=0.0,
                top_pair_pct="—",
                accepted=eng.accepted_b_only.height,
                equal="—",
                categorical="—",
                speculative=_unmatched_side_tags(eng, "B"),
                returned=eng.side_has_returned("B"),
            )
        )
    all_extras = [("A", n) for n in eng.extras_a] + [("B", n) for n in eng.extras_b]
    other_a = list(eng.b.headers)
    other_b = list(eng.a.headers)
    for side, name in all_extras:
        pend = 1 if (side, name) in eng.pending_extras else 0
        acc = 1 if (side, name) in eng.accepted_extras else 0
        others = other_a if side == "A" else other_b
        tags = ", ".join(extra_insights(name, others)[:2])
        rows.append(
            RosterRow(
                kind="extra",
                name=name,
                side=side,
                pending=pend,
                concentration=0.0,
                top_pair_pct="—",
                accepted=acc,
                equal="—",
                categorical="—",
                speculative=tags,
                returned=(side, name) in eng.returned_extras,
            )
        )
    rows.sort(key=lambda r: (-r.pending, -r.concentration, r.name))
    return rows


def is_categorical(eng: Engine, column: str) -> bool:
    for row in eng._roster_cache:
        if row.kind == "column" and row.name == column:
            return row.categorical == "yes"
    return False


def _first_pending_key(eng: Engine, side: str) -> tuple[str, ...] | None:
    pending = eng.pending_a_only if side == "A" else eng.pending_b_only
    if pending.is_empty():
        return None
    rec = pending.sort(eng.keys).head(1).row(0, named=True)
    return tuple(str(rec[k]) for k in eng.keys)


def place_from_last_pair(
    eng: Engine, last_pair: tuple[str, str, str] | None, roster_filter: str = ""
) -> Place | None:
    if not last_pair:
        return None
    col, va, vb = last_pair
    if col not in eng.comparable:
        return None
    n = eng.pending_cells.filter(
        (pl.col("column") == col) & (pl.col("val_a") == va) & (pl.col("val_b") == vb)
    ).height
    if n == 0:
        return None
    page, _ = eng.page_index_for_pair(col, va, vb)
    return Place(
        screen="pair_list",
        column=col,
        pair_val_a=va,
        pair_val_b=vb,
        page=page,
        last_pair=last_pair,
        view_tab="pending",
        focused_name=col,
        roster_filter=roster_filter,
    )


def next_lever_place(eng: Engine, current: Place) -> Place:
    # Home must show the work we jumped to: never re-apply a filter that
    # would hide the focused remaining-work row.
    if current.column and current.column in eng.comparable:
        groups = eng.pair_groups(current.column)
        if not groups.is_empty():
            top = groups.row(0, named=True)
            return replace(
                current,
                screen="pair_list",
                column=current.column,
                pair_val_a=top["val_a"],
                pair_val_b=top["val_b"],
                page=0,
                view_tab="pending",
                roster_filter="",
            )
    rows = list(eng._roster_cache)
    for row in rows:
        if row.pending <= 0:
            continue
        if row.kind == "column":
            groups = eng.pair_groups(row.name)
            va = vb = None
            if not groups.is_empty():
                top = groups.row(0, named=True)
                va, vb = top["val_a"], top["val_b"]
            return Place(
                screen="pair_list",
                column=row.name,
                pair_val_a=va,
                pair_val_b=vb,
                roster_filter="",
                last_pair=current.last_pair,
                view_tab="pending",
                focused_name=row.name,
            )
        if row.kind == "A-only":
            fk = _first_pending_key(eng, "A")
            return Place(
                screen="a_only",
                roster_filter="",
                last_pair=current.last_pair,
                focused_key=fk,
                focused_name="A-only keys",
            )
        if row.kind == "B-only":
            fk = _first_pending_key(eng, "B")
            return Place(
                screen="b_only",
                roster_filter="",
                last_pair=current.last_pair,
                focused_key=fk,
                focused_name="B-only keys",
            )
        if row.kind == "extra":
            return Place(
                screen="extras",
                roster_filter="",
                last_pair=current.last_pair,
                extra_side=row.side,
                extra_name=row.name,
                focused_name=row.name,
            )
    return Place(
        screen="roster",
        roster_filter="",
        last_pair=current.last_pair,
    )
