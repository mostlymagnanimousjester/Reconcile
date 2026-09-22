"""Roster cache, vectorized tags, and next-lever place."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

import polars as pl

from reconcile.engine import Place, RosterRow
from reconcile.insights import (
    acctneg_expr,
    bool_expr,
    dash_expr,
    fold_expr,
    format_sentinel_both,
    format_sentinel_value,
    idpad_expr,
    inws_expr,
    money_expr,
    pct_expr,
    roster_stat_date_exprs,
    roster_stat_serial_exprs,
    same_date_from_materialized,
    xlsdate_from_materialized,
)

if TYPE_CHECKING:
    from reconcile.engine import Engine


def _sync_pair_draft_cache(
    eng: Engine, *, dirty_columns: set[str] | None = None
) -> None:
    if eng.pair_draft_col is None:
        eng._pair_draft_cells = None
        eng._pair_draft_n = 0
        eng._pair_draft_triple = None
        return
    triple = (eng.pair_draft_col, eng.pair_draft_va or "", eng.pair_draft_vb or "")
    col_clean = (
        dirty_columns is not None and eng.pair_draft_col not in dirty_columns
    )
    if (
        col_clean
        and getattr(eng, "_pair_draft_triple", None) == triple
        and eng._pair_draft_cells is not None
    ):
        return
    frame = eng.pending_cells.filter(
        (pl.col("column") == eng.pair_draft_col)
        & (pl.col("val_a") == eng.pair_draft_va)
        & (pl.col("val_b") == eng.pair_draft_vb)
    ).sort(eng.keys)
    eng._pair_draft_cells = frame
    eng._pair_draft_n = frame.height
    eng._pair_draft_triple = triple


def _invalidate_page_caches(eng: Engine, dirty_columns: set[str] | None) -> None:
    """Pop per-column page caches. ``None`` = full rebuild; empty = none."""
    from reconcile.pages import invalidate_page_caches

    invalidate_page_caches(eng, dirty_columns)


def refresh_derived(
    eng: Engine, *, dirty_columns: set[str] | None = None
) -> None:
    """Recompute eager derived caches after snaps or a full rebuild.

    Fast maps (pending / pairs / accepted / mismatch) run every time so
    footer counts stay correct. Insight stats recompute only for dirty
    columns when the cache is already warm. A full reload (``None``) drops
    the cache so the next roster paint computes every column. The roster
    row list still waits for the first ``roster()`` / ``column_roster()``.
    """
    _sync_pair_draft_cache(eng, dirty_columns=dirty_columns)
    _invalidate_page_caches(eng, dirty_columns)
    _roster_fast_maps(eng)
    _sync_insight_stats(eng, dirty_columns)
    from reconcile.pages import invalidate_unmatched_key_caches

    invalidate_unmatched_key_caches(eng)
    eng._roster_cache = []
    eng._roster_cache_valid = False


def _ensure_roster_cache(eng: Engine) -> None:
    if getattr(eng, "_roster_cache_valid", False):
        return
    eng._roster_cache = _build_roster_cache(eng)
    eng._roster_cache_valid = True


def roster(eng: Engine, name_filter: str = "") -> list[RosterRow]:
    _ensure_roster_cache(eng)
    rows = list(eng._roster_cache)
    if name_filter:
        needle = name_filter.lower()
        rows = [r for r in rows if needle in r.name.lower()]
    return rows


def column_roster(
    eng: Engine, name_filter: str = "", include_settled: bool = False
) -> list[RosterRow]:
    """Comparable columns. Default hides accepted / all-equal (pending 0).

    Columns whose *shared* (matched-key) rows are all equal have pending 0 and
    are treated as auto-accepted: there is nothing to review. A-only / B-only
    keys never contribute cell pending, so they cannot keep such a column on
    the roster. Accepted columns (pending 0 after snapshots) are hidden too
    unless ``include_settled``. Unmatched-key and extra rows are remaining
    work, not columns.
    """
    rows = [r for r in roster(eng, name_filter) if r.kind == "column"]
    pending = [r for r in rows if r.pending > 0]
    if not include_settled:
        return pending
    # Visual sections when v is on: pending first, then accepted/equal.
    # Each section stays in table A import order (eng.comparable).
    settled = [r for r in rows if r.pending <= 0]
    return pending + settled


def visible_column_roster(eng: Engine, name_filter: str = "") -> list[RosterRow]:
    """Pending comparable columns only (accepted / all-equal hidden)."""
    return column_roster(eng, name_filter, include_settled=False)


def _empty_pair_groups() -> pl.DataFrame:
    return pl.DataFrame(
        {"column": [], "val_a": [], "val_b": [], "n": []},
        schema={
            "column": pl.Utf8,
            "val_a": pl.Utf8,
            "val_b": pl.Utf8,
            "n": pl.UInt32,
        },
    )


def _pair_groups_by_column(frame: pl.DataFrame) -> dict[str, pl.DataFrame]:
    """Per-column slices in the same order as the wide pair frame."""
    if frame.is_empty():
        return {}
    parts = frame.partition_by("column", maintain_order=True, as_dict=True)
    out: dict[str, pl.DataFrame] = {}
    for key, part in parts.items():
        col = key[0] if isinstance(key, tuple) else key
        out[str(col)] = part.select("val_a", "val_b", "n")
    return out


def returned_names_in_import_order(
    comparable: list[str], uniq: pl.DataFrame
) -> list[str]:
    """Returned column names in table A import order.

    ``uniq`` is the distinct returned ``column`` values. Membership is one
    semi-join, not a Python loop of per-name filters.
    """
    if not comparable or uniq.is_empty():
        return []
    order = pl.DataFrame(
        {"column": comparable, "_ord": list(range(len(comparable)))}
    )
    hit = order.join(uniq, on="column", how="semi").sort("_ord")
    if hit.is_empty():
        return []
    col = hit.get_column("column")
    return [str(col[i]) for i in range(hit.height)]


def _roster_fast_maps(eng: Engine) -> None:
    """Pending / pair groups / accepted / mismatch — needed immediately after accept."""
    pending_lf = eng.pending_cells.lazy()
    pending_lf_count = pending_lf.group_by("column").len()
    pairs_lf = (
        pending_lf.group_by(["column", "val_a", "val_b"])
        .len()
        .rename({"len": "n"})
        .sort(["column", "n", "val_a", "val_b"], descending=[False, True, False, False])
    )
    accepted_lf = eng.accepted_cells.lazy().group_by("column").len()
    mismatch_lf = eng.mismatches.lazy().group_by("column").len()
    pending_df, pairs_df, accepted_df, mismatch_df = pl.collect_all(
        [pending_lf_count, pairs_lf, accepted_lf, mismatch_lf]
    )
    if pairs_df.is_empty():
        eng._pair_groups_df = _empty_pair_groups()
    else:
        eng._pair_groups_df = pairs_df.select(
            pl.col("column").cast(pl.Utf8),
            pl.col("val_a").cast(pl.Utf8),
            pl.col("val_b").cast(pl.Utf8),
            pl.col("n").cast(pl.UInt32),
        )
    pending_by_col: dict[str, int] = {}
    accepted_by_col: dict[str, int] = {}
    mismatch_n: dict[str, int] = {}
    top: dict[str, tuple[str, str, int]] = {}
    if not pending_df.is_empty():
        for rec in pending_df.to_dicts():
            pending_by_col[str(rec["column"])] = int(rec["len"])
    if not accepted_df.is_empty():
        for rec in accepted_df.to_dicts():
            accepted_by_col[str(rec["column"])] = int(rec["len"])
    if not mismatch_df.is_empty():
        for rec in mismatch_df.to_dicts():
            mismatch_n[str(rec["column"])] = int(rec["len"])
    if not eng._pair_groups_df.is_empty():
        for rec in (
            eng._pair_groups_df.group_by("column", maintain_order=True).first().to_dicts()
        ):
            top[str(rec["column"])] = (str(rec["val_a"]), str(rec["val_b"]), int(rec["n"]))
    returned: set[str] = set()
    if not eng.returned_cells_df.is_empty():
        uniq = eng.returned_cells_df.select(pl.col("column").cast(pl.Utf8)).unique()
        returned = set(returned_names_in_import_order(list(eng.comparable), uniq))
    eng._pair_groups_by_col = _pair_groups_by_column(eng._pair_groups_df)
    eng._pending_by_col = pending_by_col
    eng._accepted_by_col = accepted_by_col
    eng._mismatch_n = mismatch_n
    eng._top_pair = top
    eng._returned_columns = returned


def _insight_stats_agg() -> list[pl.Expr]:
    """Same ``.all()`` predicates as the pre-cache roster, dates parsed once."""
    return [
        pl.len().alias("pending"),
        (pl.col("val_a").str.strip_chars() == pl.col("val_b").str.strip_chars()).all().alias("trim"),
        (pl.col("val_a").str.to_lowercase() == pl.col("val_b").str.to_lowercase()).all().alias("case"),
        (
            pl.col("val_a").str.strip_chars().str.to_lowercase()
            == pl.col("val_b").str.strip_chars().str.to_lowercase()
        ).all().alias("both"),
        (
            pl.col("val_a").cast(pl.Float64, strict=False).is_not_null()
            & pl.col("val_b").cast(pl.Float64, strict=False).is_not_null()
            & (pl.col("val_a").str.strip_chars() != "")
            & (pl.col("val_b").str.strip_chars() != "")
            & (
                pl.col("val_a").cast(pl.Float64, strict=False)
                == pl.col("val_b").cast(pl.Float64, strict=False)
            )
        ).all().alias("numeric"),
        (
            pl.col("val_a").str.contains(r"[\u00a0\t\r\n]")
            | pl.col("val_b").str.contains(r"[\u00a0\t\r\n]")
            | (pl.col("val_a") != pl.col("val_a").str.strip_chars())
            | (pl.col("val_b") != pl.col("val_b").str.strip_chars())
        ).all().alias("ws"),
        same_date_from_materialized().all().alias("same_date"),
        money_expr().all().alias("money"),
        pct_expr().all().alias("pct"),
        idpad_expr().all().alias("idpad"),
        bool_expr().all().alias("bool"),
        acctneg_expr().all().alias("acctneg"),
        xlsdate_from_materialized().all().alias("xlsdate"),
        inws_expr().all().alias("inws"),
        dash_expr().all().alias("dash"),
        fold_expr().all().alias("fold"),
        pl.col("val_a").n_unique().alias("n_a"),
        pl.col("val_b").n_unique().alias("n_b"),
    ]


def _compute_insight_stats(
    eng: Engine, columns: set[str] | None
) -> dict[str, dict[str, Any]]:
    """Display-only insight flags for every pending column, or ``columns``."""
    if eng.pending_cells.is_empty():
        return {}
    lf = eng.pending_cells.lazy()
    if columns is not None:
        if not columns:
            return {}
        lf = lf.filter(pl.col("column").is_in(list(columns)))
    stats_df = (
        lf.with_columns(*roster_stat_date_exprs())
        .with_columns(*roster_stat_serial_exprs())
        .group_by("column")
        .agg(*_insight_stats_agg())
        .collect()
    )
    if stats_df.is_empty():
        return {}
    small = stats_df.filter((pl.col("n_a") <= 30) & (pl.col("n_b") <= 30))
    if small.is_empty():
        stats_df = stats_df.with_columns(pl.lit(None, dtype=pl.UInt32).alias("n_ab"))
    else:
        names = small.get_column("column")
        nab = (
            eng.pending_cells.filter(pl.col("column").is_in(names.implode()))
            .group_by("column")
            .agg(
                pl.col("val_a").unique().sort().alias("ua"),
                pl.col("val_b").unique().sort().alias("ub"),
            )
            .with_columns(
                pl.col("ua")
                .list.concat(pl.col("ub"))
                .list.unique()
                .list.len()
                .alias("n_ab"),
            )
            .select("column", "n_ab")
        )
        stats_df = stats_df.join(nab, on="column", how="left")
    col_stats: dict[str, dict[str, Any]] = {}
    for rec in stats_df.to_dicts():
        col_stats[rec["column"]] = rec
    return col_stats


def _sync_insight_stats(eng: Engine, dirty_columns: set[str] | None) -> None:
    """Keep ``_col_stats`` across roster invalidation.

    ``None`` is a full reload: recompute when the cache is warm, otherwise
    leave it cold for the first roster paint. A dirty set recomputes those
    columns and drops keys whose pending went to 0.
    """
    if dirty_columns is None:
        if eng._col_stats_warm:
            eng._col_stats = _compute_insight_stats(eng, None)
            eng._col_stats_warm = True
        else:
            eng._col_stats = {}
            eng._col_stats_warm = False
        return
    if not eng._col_stats_warm:
        return
    pending = eng._pending_by_col
    for col in list(eng._col_stats):
        if pending.get(col, 0) <= 0:
            del eng._col_stats[col]
    still = {c for c in dirty_columns if pending.get(c, 0) > 0}
    for col in dirty_columns:
        if col not in still:
            eng._col_stats.pop(col, None)
    if still:
        eng._col_stats.update(_compute_insight_stats(eng, still))


def _roster_insight_stats(eng: Engine) -> dict[str, dict[str, Any]]:
    """Merge cached per-column stats. First paint computes whatever is missing."""
    if not eng._col_stats_warm:
        eng._col_stats = _compute_insight_stats(eng, None)
        eng._col_stats_warm = True
        return eng._col_stats
    missing = {
        col
        for col in eng.comparable
        if eng._pending_by_col.get(col, 0) > 0 and col not in eng._col_stats
    }
    if missing:
        eng._col_stats.update(_compute_insight_stats(eng, missing))
    return eng._col_stats


def _roster_agg_maps(
    eng: Engine,
) -> tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, dict[str, Any]]]:
    _roster_fast_maps(eng)
    col_stats = _roster_insight_stats(eng)
    top_pair = {col: n for col, (*_, n) in eng._top_pair.items()}
    return eng._pending_by_col, eng._accepted_by_col, top_pair, col_stats


SENTINEL_COLS = (
    ("const A", "sent_a"),
    ("const B", "sent_b"),
    ("const both", "sent_both"),
)
CHECK_COLS = (
    ("trim", "trim"),
    ("case", "case"),
    ("trim+case", "trim_case"),
    ("num", "num"),
    ("ws", "ws"),
    ("date", "date"),
    ("money", "money"),
    ("pct", "pct"),
    ("idpad", "idpad"),
    ("bool", "bool"),
    ("acctneg", "acctneg"),
    ("xlsdate", "xlsdate"),
    ("inws", "inws"),
    ("dash", "dash"),
    ("fold", "fold"),
)
CHECK_HEADERS = {h for h, _ in CHECK_COLS}
CHECK_ATTR = {h: attr for h, attr in CHECK_COLS}
# RosterRow attribute → stats key from the ``.all()`` aggregation.
CHECK_STAT = {
    "trim": "trim",
    "case": "case",
    "trim_case": "both",
    "num": "numeric",
    "ws": "ws",
    "date": "same_date",
    "money": "money",
    "pct": "pct",
    "idpad": "idpad",
    "bool": "bool",
    "acctneg": "acctneg",
    "xlsdate": "xlsdate",
    "inws": "inws",
    "dash": "dash",
    "fold": "fold",
}


def roster_visible_insight_headers(rows: list[RosterRow]) -> list[tuple[str, str]]:
    """Headers kept by pending comparable rows only (not settled / A-only / extras)."""
    pending = [r for r in rows if r.kind == "column" and r.pending > 0]
    visible: list[tuple[str, str]] = []
    for header, attr in SENTINEL_COLS:
        if any(getattr(r, attr) for r in pending):
            visible.append((header, attr))
    for header, attr in CHECK_COLS:
        if any(getattr(r, attr) == "y" for r in pending):
            visible.append((header, attr))
    return visible


def check_column_pending_hits(eng: Engine, header: str) -> list[str]:
    """Pending comparable names whose cell in that check column is y.

    Uses warm ``_col_stats`` when present. A cold cache computes stats
    instead of building every roster row to read one check.
    """
    attr = CHECK_ATTR.get(header)
    if attr is None:
        return []
    stat_key = CHECK_STAT.get(attr)
    if stat_key is None:
        return []
    stats = _roster_insight_stats(eng)
    hits: list[str] = []
    for col in eng.comparable:
        if eng._pending_by_col.get(col, 0) <= 0:
            continue
        st = stats.get(col)
        if not st:
            continue
        if bool(st[stat_key]):
            hits.append(col)
    return hits


def _yn(flag: bool) -> str:
    return "y" if flag else "n"


def _sentinel_cells(sent_a: str | None, sent_b: str | None) -> tuple[str, str, str]:
    a = format_sentinel_value(sent_a) if sent_a is not None else ""
    b = format_sentinel_value(sent_b) if sent_b is not None else ""
    both = format_sentinel_both(sent_a, sent_b) if sent_a is not None and sent_b is not None else ""
    return a, b, both


def _sentinel_map(eng: Engine) -> dict[str, tuple[str | None, str | None]]:
    out: dict[str, tuple[str | None, str | None]] = {}
    frame = getattr(eng, "column_sentinels", None)
    if frame is None or frame.is_empty():
        return out
    for rec in frame.to_dicts():
        out[str(rec["column"])] = (rec.get("sent_a"), rec.get("sent_b"))
    return out


def _build_roster_cache(eng: Engine) -> list[RosterRow]:
    pending_by_col = eng._pending_by_col
    accepted_by_col = eng._accepted_by_col
    col_stats = _roster_insight_stats(eng)
    top_pair = {col: n for col, (*_, n) in eng._top_pair.items()}
    sentinels = _sentinel_map(eng)
    rows: list[RosterRow] = []
    matched_n = eng.matched_a.height
    mismatch_n = eng._mismatch_n
    for col in eng.comparable:
        pend = pending_by_col.get(col, 0)
        acc = accepted_by_col.get(col, 0)
        conc = (top_pair.get(col, 0) / pend) if pend else 0.0
        pct = f"{conc * 100:.0f}%" if pend else "—"
        st = col_stats.get(col)
        raw_a, raw_b = sentinels.get(col, (None, None))
        cell_a, cell_b, cell_both = _sentinel_cells(raw_a, raw_b)
        if not pend or st is None:
            cat = "—"
            blank = "n" if pend == 0 else ""
            yn = {attr: blank for _, attr in CHECK_COLS}
        else:
            n_a, n_b = int(st["n_a"]), int(st["n_b"])
            if n_a > 30 or n_b > 30:
                cat = "no"
            else:
                n_ab = int(st["n_ab"])
                cat = "yes" if n_ab <= 50 else "no"
            yn = {
                "trim": _yn(bool(st["trim"])),
                "case": _yn(bool(st["case"])),
                "trim_case": _yn(bool(st["both"])),
                "num": _yn(bool(st["numeric"])),
                "ws": _yn(bool(st["ws"])),
                "date": _yn(bool(st["same_date"])),
                "money": _yn(bool(st["money"])),
                "pct": _yn(bool(st["pct"])),
                "idpad": _yn(bool(st["idpad"])),
                "bool": _yn(bool(st["bool"])),
                "acctneg": _yn(bool(st["acctneg"])),
                "xlsdate": _yn(bool(st["xlsdate"])),
                "inws": _yn(bool(st["inws"])),
                "dash": _yn(bool(st["dash"])),
                "fold": _yn(bool(st["fold"])),
            }
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
                speculative="",
                returned=col in eng._returned_columns,
                sent_a=cell_a,
                sent_b=cell_b,
                sent_both=cell_both,
                **yn,
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
                speculative="",
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
                speculative="",
                returned=eng.side_has_returned("B"),
            )
        )
    all_extras = [("A", n) for n in eng.extras_a] + [("B", n) for n in eng.extras_b]
    eng._extra_tags = {}
    for side, name in all_extras:
        pend = 1 if (side, name) in eng.pending_extras else 0
        acc = 1 if (side, name) in eng.accepted_extras else 0
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
                speculative="",
                returned=(side, name) in eng.returned_extras,
            )
        )
    # Comparable columns stay in table A import order (eng.comparable).
    # A-only / B-only / extras follow as remaining-work leftovers, not resorted.
    return rows


def is_categorical(eng: Engine, column: str) -> bool:
    _ensure_roster_cache(eng)
    for row in eng._roster_cache:
        if row.kind == "column" and row.name == column:
            return row.categorical == "yes"
    return False


def _first_pending_key(eng: Engine, side: str) -> tuple[str, ...] | None:
    pending = eng.pending_a_only if side == "A" else eng.pending_b_only
    if pending.is_empty():
        return None
    rec = pending.head(1).row(0, named=True)
    return tuple(str(rec[k]) for k in eng.keys)


def _cached_pair_n(eng: Engine, col: str, va: str, vb: str) -> int | None:
    """Pending pair height from pair groups or the pair-cell cache.

    ``None`` means neither cache can answer (caller may scan).
    """
    by_col = getattr(eng, "_pair_groups_by_col", None)
    if isinstance(by_col, dict):
        groups = by_col.get(col)
        if groups is None or groups.is_empty():
            return 0
        hit = groups.filter((pl.col("val_a") == va) & (pl.col("val_b") == vb)).select("n")
        if hit.is_empty():
            return 0
        return int(hit.item())
    cached = getattr(eng, "_pair_cells_cache", {}).get((col, va, vb))
    if cached is not None:
        return int(cached.height)
    return None


def place_from_last_pair(
    eng: Engine, last_pair: tuple[str, str, str] | None, roster_filter: str = ""
) -> Place | None:
    if not last_pair:
        return None
    col, va, vb = last_pair
    if col not in eng.comparable:
        return None
    n = _cached_pair_n(eng, col, va, vb)
    if n is None:
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
        top = eng._top_pair.get(current.column)
        if top:
            return replace(
                current,
                screen="pair_list",
                column=current.column,
                pair_val_a=top[0],
                pair_val_b=top[1],
                page=0,
                view_tab="pending",
                roster_filter="",
            )
    for col in eng.comparable:
        if eng._pending_by_col.get(col, 0) <= 0:
            continue
        hit = eng._top_pair.get(col)
        va = vb = None
        if hit:
            va, vb = hit[0], hit[1]
        return Place(
            screen="pair_list",
            column=col,
            pair_val_a=va,
            pair_val_b=vb,
            roster_filter="",
            last_pair=current.last_pair,
            view_tab="pending",
            focused_name=col,
        )
    if eng.pending_a_only_n() > 0:
        fk = _first_pending_key(eng, "A")
        return Place(
            screen="a_only",
            roster_filter="",
            last_pair=current.last_pair,
            focused_key=fk,
            focused_name="A-only keys",
        )
    if eng.pending_b_only_n() > 0:
        fk = _first_pending_key(eng, "B")
        return Place(
            screen="b_only",
            roster_filter="",
            last_pair=current.last_pair,
            focused_key=fk,
            focused_name="B-only keys",
        )
    for side, name in [("A", n) for n in eng.extras_a] + [("B", n) for n in eng.extras_b]:
        if (side, name) not in eng.pending_extras:
            continue
        return Place(
            screen="extras",
            roster_filter="",
            last_pair=current.last_pair,
            extra_side=side,
            extra_name=name,
            focused_name=name,
        )
    return Place(
        screen="roster",
        roster_filter="",
        last_pair=current.last_pair,
    )
