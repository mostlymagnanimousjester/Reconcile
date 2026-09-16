"""Slice-then-materialize page helpers. Never dump a full tall frame."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import polars as pl

from reconcile.compare import _empty_df, _empty_mismatch_schema
from reconcile.insights import extra_insights

if TYPE_CHECKING:
    from reconcile.engine import Engine

PAGE_SIZE = 100


def _page_dicts(frame: pl.DataFrame) -> list[dict[str, Any]]:
    """Materialize at most PAGE_SIZE rows. Callers must slice first."""
    if frame.height > PAGE_SIZE:
        raise ValueError(
            f"refusing to materialize {frame.height} rows (PAGE_SIZE={PAGE_SIZE})"
        )
    return frame.to_dicts()


def _page(frame: pl.DataFrame, page: int) -> tuple[list[dict[str, Any]], int, int]:
    total = frame.height
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    chunk = frame.slice(page * PAGE_SIZE, PAGE_SIZE)
    return _page_dicts(chunk), page, pages


def _attach_context(eng: Engine, chunk: pl.DataFrame, column: str) -> pl.DataFrame:
    names = [
        n
        for n in eng.context_columns.get(column, [])
        if n in eng.context_pool and n != column
    ]
    if not names or chunk.is_empty() or eng.matched_a.is_empty():
        return chunk
    a_sel = eng.matched_a.select(eng.keys + names).rename({n: f"{n}__ctx_a" for n in names})
    b_sel = eng.matched_b.select(eng.keys + names).rename({n: f"{n}__ctx_b" for n in names})
    out = chunk.join(a_sel, on=eng.keys, how="left").join(b_sel, on=eng.keys, how="left")
    fills = [pl.col(f"{n}__ctx_a").fill_null("").cast(pl.Utf8) for n in names] + [
        pl.col(f"{n}__ctx_b").fill_null("").cast(pl.Utf8) for n in names
    ]
    return out.with_columns(fills)


def _page_with_context(
    eng: Engine, frame: pl.DataFrame, page: int, column: str
) -> tuple[list[dict[str, Any]], int, int]:
    total = frame.height
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    chunk = frame.slice(page * PAGE_SIZE, PAGE_SIZE)
    chunk = _attach_context(eng, chunk, column)
    return _page_dicts(chunk), page, pages


def page_index_for_key(
    frame: pl.DataFrame,
    keys: list[str],
    key: tuple[str, ...] | None,
    page_size: int = PAGE_SIZE,
) -> tuple[int, int]:
    """(page, row_on_page) for key in an already-sorted frame. No full to_dicts."""
    if frame.is_empty() or not key or len(key) != len(keys):
        return 0, 0
    expr: pl.Expr = pl.lit(True)
    for k, v in zip(keys, key):
        expr = expr & (pl.col(k) == v)
    hit = frame.with_row_index("_idx").filter(expr).select("_idx")
    if hit.height == 0:
        return 0, 0
    i = int(hit.item(0, 0))
    return i // page_size, i % page_size


def page_index_for_pair(
    eng: Engine, column: str, val_a: str, val_b: str, page_size: int = PAGE_SIZE
) -> tuple[int, int]:
    groups = eng.pair_groups(column)
    if groups.is_empty():
        return 0, 0
    hit = (
        groups.with_row_index("_idx")
        .filter((pl.col("val_a") == val_a) & (pl.col("val_b") == val_b))
        .select("_idx")
    )
    if hit.height == 0:
        return 0, 0
    i = int(hit.item(0, 0))
    return i // page_size, i % page_size


def pair_page(eng: Engine, column: str, page: int) -> tuple[list[dict[str, Any]], int, int]:
    return _page(eng.pair_groups(column), page)


def pair_cells_page(
    eng: Engine, column: str, val_a: str, val_b: str, page: int
) -> tuple[list[dict[str, Any]], int, int]:
    frame = eng.pending_cells.filter(
        (pl.col("column") == column)
        & (pl.col("val_a") == val_a)
        & (pl.col("val_b") == val_b)
    ).sort(eng.keys)
    return _page_with_context(eng, frame, page, column)


def cells_for_tab(
    eng: Engine, column: str, tab: str, page: int
) -> tuple[list[dict[str, Any]], int, int]:
    empty = _empty_df(_empty_mismatch_schema(eng.keys))
    if tab == "accepted":
        frame = eng.accepted_cells.filter(pl.col("column") == column)
    elif tab == "equal":
        if eng.matched_a.is_empty():
            frame = empty
        else:
            a = eng.matched_a.select(eng.keys + [column]).rename({column: "val_a"})
            b = eng.matched_b.select(eng.keys + [column]).rename({column: "val_b"})
            frame = a.join(b, on=eng.keys, how="inner").filter(pl.col("val_a") == pl.col("val_b"))
    elif tab == "all_matched":
        if eng.matched_a.is_empty():
            frame = empty
        else:
            a = eng.matched_a.select(eng.keys + [column]).rename({column: "val_a"})
            b = eng.matched_b.select(eng.keys + [column]).rename({column: "val_b"})
            frame = a.join(b, on=eng.keys, how="inner")
    else:
        frame = eng.pending_cells.filter(pl.col("column") == column)
    return _page_with_context(eng, frame.sort(eng.keys), page, column)


def unmatched_page(
    eng: Engine, side: str, page: int
) -> tuple[list[dict[str, Any]], int, int]:
    accepted = eng.accepted_a_only if side == "A" else eng.accepted_b_only
    frame = (eng.a_only if side == "A" else eng.b_only).sort(eng.keys)
    total = frame.height
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    chunk = frame.slice(page * PAGE_SIZE, PAGE_SIZE)
    if chunk.is_empty():
        return [], page, pages
    if accepted.is_empty():
        chunk = chunk.with_columns(pl.lit(False).alias("_accepted"))
    else:
        chunk = chunk.join(
            accepted.select(eng.keys).unique().with_columns(pl.lit(True).alias("_accepted")),
            on=eng.keys,
            how="left",
        ).with_columns(pl.col("_accepted").fill_null(False))
    ret = eng.returned_keys_df
    if ret.is_empty():
        chunk = chunk.with_columns(pl.lit(False).alias("_returned"))
    else:
        ret_keys = (
            ret.filter(pl.col("side") == side)
            .select(eng.keys)
            .unique()
            .with_columns(pl.lit(True).alias("_returned"))
        )
        chunk = chunk.join(ret_keys, on=eng.keys, how="left").with_columns(
            pl.col("_returned").fill_null(False)
        )
    return _page_dicts(chunk), page, pages


def extras_rows(eng: Engine) -> list[dict[str, Any]]:
    rows = []
    all_extras = [("A", n) for n in sorted(eng.extras_a)] + [
        ("B", n) for n in sorted(eng.extras_b)
    ]
    # Order: exact name (working rule). Spec §18: exact name. Mix sides by name then side.
    all_extras.sort(key=lambda x: (x[1], x[0]))
    others = {"A": list(eng.b.headers), "B": list(eng.a.headers)}
    for side, name in all_extras:
        pending = (side, name) in eng.pending_extras
        rows.append(
            {
                "side": side,
                "name": name,
                "pending": 1 if pending else 0,
                "accepted": 0 if pending else 1,
                "speculative": extra_insights(name, others[side]),
                "returned": (side, name) in eng.returned_extras,
            }
        )
    return rows


def context_values(
    eng: Engine, key: tuple[str, ...], column: str
) -> list[tuple[str, str, str]]:
    names = [n for n in eng.context_columns.get(column, []) if n in eng.context_pool and n != column]
    if not names:
        return []
    filt = None
    for kname, kval in zip(eng.keys, key):
        expr = pl.col(kname) == kval
        filt = expr if filt is None else (filt & expr)
    out: list[tuple[str, str, str]] = []
    if eng.matched_a.is_empty():
        return [(n, "", "") for n in names]
    ra = eng.matched_a.filter(filt)
    rb = eng.matched_b.filter(filt)
    if ra.is_empty():
        return [(n, "", "") for n in names]
    rec_a = ra.row(0, named=True)
    rec_b = rb.row(0, named=True)
    for n in names:
        out.append((n, str(rec_a.get(n, "")), str(rec_b.get(n, ""))))
    return out


def next_pending_key_in_grid(
    eng: Engine, side: str, current: tuple[str, ...]
) -> tuple[str, ...] | None:
    pending = eng.pending_a_only if side == "A" else eng.pending_b_only
    if pending.is_empty():
        return None
    pending = pending.sort(eng.keys)
    parts: list[pl.Expr] = []
    acc: pl.Expr = pl.lit(True)
    for i, k in enumerate(eng.keys):
        parts.append(acc & (pl.col(k) > current[i]))
        acc = acc & (pl.col(k) == current[i])
    nxt = pending.filter(pl.any_horizontal(parts)).head(1)
    if nxt.is_empty():
        nxt = pending.head(1)
    rec = nxt.row(0, named=True)
    return tuple(str(rec[k]) for k in eng.keys)


def next_pending_cell_in_pair(
    eng: Engine, after: tuple[str, ...]
) -> tuple[str, ...] | None:
    if eng.pair_draft_col is None:
        return None
    frame = eng.pending_cells.filter(
        (pl.col("column") == eng.pair_draft_col)
        & (pl.col("val_a") == eng.pair_draft_va)
        & (pl.col("val_b") == eng.pair_draft_vb)
    )
    if frame.is_empty():
        return None
    frame = frame.sort(eng.keys)
    parts: list[pl.Expr] = []
    acc: pl.Expr = pl.lit(True)
    for i, k in enumerate(eng.keys):
        parts.append(acc & (pl.col(k) > after[i]))
        acc = acc & (pl.col(k) == after[i])
    nxt = frame.filter(pl.any_horizontal(parts)).head(1)
    if nxt.is_empty():
        nxt = frame.head(1)
    rec = nxt.row(0, named=True)
    return tuple(str(rec[k]) for k in eng.keys)
