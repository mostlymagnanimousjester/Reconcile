"""Slice-then-materialize page helpers. Never dump a full tall frame."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import polars as pl

from reconcile.compare import _empty_df, _empty_mismatch_schema
from reconcile.insights import (
    CONTEXT_TOP_N,
    CONTEXT_TRUNCATION_MARK,
    CONTEXT_VALUE_SEP,
    extra_insights,
)

if TYPE_CHECKING:
    from reconcile.engine import Engine

PAGE_SIZE = 100


def invalidate_equal_caches(eng: Engine) -> None:
    """Equals / all-matched frames depend on matched rows, not snaps."""
    eng._equal_by_col = {}
    eng._all_matched_by_col = {}


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
    needed = chunk.select(eng.keys).unique()
    a_sel = (
        eng.matched_a.join(needed, on=eng.keys, how="semi")
        .select(eng.keys + names)
        .rename({n: f"{n}__ctx_a" for n in names})
    )
    b_sel = (
        eng.matched_b.join(needed, on=eng.keys, how="semi")
        .select(eng.keys + names)
        .rename({n: f"{n}__ctx_b" for n in names})
    )
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
    chunk = _attach_cell_returned(eng, chunk, column)
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


def _context_names(eng: Engine, column: str) -> list[str]:
    return [
        n
        for n in eng.context_columns.get(column, [])
        if n in eng.context_pool and n != column
    ]


def _attach_pair_context_summaries(
    eng: Engine, column: str, pair_chunk: pl.DataFrame
) -> pl.DataFrame:
    """Top-N unique context values per pair, with pair-row counts.

    Count is how many pending cells (keys) of this pair carry that value on
    either side. A value on both A and B of the same row counts once.
    Call after the pair-list slice. One summary column per context name.
    """
    names = _context_names(eng, column)
    if not names:
        return pair_chunk
    empty_cols = [pl.lit("").alias(f"{n}__ctx") for n in names]
    if pair_chunk.is_empty() or eng.matched_a.is_empty() or eng.pending_cells.is_empty():
        return pair_chunk.with_columns(empty_cols)
    scoped = eng.pending_cells.filter(pl.col("column") == column).join(
        pair_chunk.select("val_a", "val_b").unique(),
        on=["val_a", "val_b"],
        how="inner",
    )
    if scoped.is_empty():
        return pair_chunk.with_columns(empty_cols)
    scoped = _attach_context(eng, scoped, column)
    key_cols = list(eng.keys)
    parts: list[pl.DataFrame] = []
    for name in names:
        for side in ("a", "b"):
            col = f"{name}__ctx_{side}"
            if col not in scoped.columns:
                continue
            parts.append(
                scoped.select(
                    "val_a",
                    "val_b",
                    *key_cols,
                    pl.lit(name).alias("_ctx"),
                    pl.col(col).fill_null("").cast(pl.Utf8).alias("_val"),
                )
            )
    if not parts:
        return pair_chunk.with_columns(empty_cols)
    long = pl.concat(parts).unique(
        subset=["val_a", "val_b", "_ctx", "_val", *key_cols],
        maintain_order=True,
    )
    ranked = (
        long.group_by(["val_a", "val_b", "_ctx", "_val"])
        .len()
        .sort(["val_a", "val_b", "_ctx", "len", "_val"], descending=[False, False, False, True, False])
        .with_columns(pl.col("_val").cum_count().over(["val_a", "val_b", "_ctx"]).alias("_rank"))
    )
    nuniq = long.group_by(["val_a", "val_b", "_ctx"]).agg(
        pl.col("_val").n_unique().alias("_nuniq")
    )
    top = (
        ranked.filter(pl.col("_rank") <= CONTEXT_TOP_N)
        .sort(["val_a", "val_b", "_ctx", "_rank"])
        .with_columns(
            pl.concat_str(
                [
                    pl.when(pl.col("_val") == "")
                    .then(pl.lit("(empty)"))
                    .otherwise(pl.col("_val")),
                    pl.lit(" "),
                    pl.col("len").cast(pl.Utf8),
                ]
            ).alias("_shown")
        )
    )
    summarized = (
        top.group_by(["val_a", "val_b", "_ctx"], maintain_order=True)
        .agg(pl.col("_shown").alias("_vals"))
        .join(nuniq, on=["val_a", "val_b", "_ctx"])
        .with_columns(
            pl.when(pl.col("_nuniq") > CONTEXT_TOP_N)
            .then(pl.col("_vals").list.join(CONTEXT_VALUE_SEP) + pl.lit(CONTEXT_TRUNCATION_MARK))
            .otherwise(pl.col("_vals").list.join(CONTEXT_VALUE_SEP))
            .alias("_summary")
        )
    )
    out = pair_chunk
    for name in names:
        one = summarized.filter(pl.col("_ctx") == name).select(
            "val_a", "val_b", pl.col("_summary").alias(f"{name}__ctx")
        )
        out = out.join(one, on=["val_a", "val_b"], how="left").with_columns(
            pl.col(f"{name}__ctx").fill_null("")
        )
    return out


def _pair_ctx_frame(eng: Engine, column: str) -> pl.DataFrame:
    cached = eng._pair_ctx_by_col.get(column)
    if cached is not None:
        return cached
    groups = eng.pair_groups(column)
    computed = _attach_pair_context_summaries(eng, column, groups)
    eng._pair_ctx_by_col[column] = computed
    return computed


def pair_page(eng: Engine, column: str, page: int) -> tuple[list[dict[str, Any]], int, int]:
    groups = eng.pair_groups(column)
    total = groups.height
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    chunk = groups.slice(page * PAGE_SIZE, PAGE_SIZE)
    ctx = _pair_ctx_frame(eng, column)
    extra = [c for c in ctx.columns if c not in ("val_a", "val_b", "n", "column")]
    if extra and not chunk.is_empty():
        chunk = chunk.join(ctx.select(["val_a", "val_b", *extra]), on=["val_a", "val_b"], how="left")
        chunk = chunk.with_columns([pl.col(c).fill_null("") for c in extra])
    return _page_dicts(chunk), page, pages


def _equal_frame(eng: Engine, column: str) -> pl.DataFrame:
    cached = eng._equal_by_col.get(column)
    if cached is not None:
        return cached
    empty = _empty_df(_empty_mismatch_schema(eng.keys))
    if eng.matched_a.is_empty() or column not in eng.comparable:
        eng._equal_by_col[column] = empty
        return empty
    a = eng.matched_a.select(eng.keys + [column]).rename({column: "val_a"})
    b = eng.matched_b.select(eng.keys + [column]).rename({column: "val_b"})
    frame = a.join(b, on=eng.keys, how="inner").filter(pl.col("val_a") == pl.col("val_b"))
    eng._equal_by_col[column] = frame
    return frame


def _all_matched_frame(eng: Engine, column: str) -> pl.DataFrame:
    cached = eng._all_matched_by_col.get(column)
    if cached is not None:
        return cached
    empty = _empty_df(_empty_mismatch_schema(eng.keys))
    if eng.matched_a.is_empty() or column not in eng.comparable:
        eng._all_matched_by_col[column] = empty
        return empty
    a = eng.matched_a.select(eng.keys + [column]).rename({column: "val_a"})
    b = eng.matched_b.select(eng.keys + [column]).rename({column: "val_b"})
    frame = a.join(b, on=eng.keys, how="inner")
    eng._all_matched_by_col[column] = frame
    return frame


def _pair_cells_frame(eng: Engine, column: str, val_a: str, val_b: str) -> pl.DataFrame:
    cached = eng._pair_draft_cells
    if (
        cached is not None
        and eng.pair_draft_col == column
        and eng.pair_draft_va == val_a
        and eng.pair_draft_vb == val_b
    ):
        return cached
    return eng.pending_cells.filter(
        (pl.col("column") == column)
        & (pl.col("val_a") == val_a)
        & (pl.col("val_b") == val_b)
    ).sort(eng.keys)


def pair_cells_page(
    eng: Engine, column: str, val_a: str, val_b: str, page: int
) -> tuple[list[dict[str, Any]], int, int]:
    return _page_with_context(eng, _pair_cells_frame(eng, column, val_a, val_b), page, column)


def cells_for_tab(
    eng: Engine, column: str, tab: str, page: int
) -> tuple[list[dict[str, Any]], int, int]:
    if tab == "accepted":
        frame = eng.accepted_cells.filter(pl.col("column") == column)
    elif tab == "equal":
        frame = _equal_frame(eng, column)
    elif tab == "all_matched":
        frame = _all_matched_frame(eng, column)
    else:
        frame = eng.pending_cells.filter(pl.col("column") == column)
    return _page_with_context(eng, frame.sort(eng.keys), page, column)


def pairs_returned_mask(
    eng: Engine, column: str, pairs: list[tuple[str, str]]
) -> list[bool]:
    if not pairs:
        return []
    frame = pl.DataFrame(
        {"val_a": [a for a, _ in pairs], "val_b": [b for _, b in pairs]},
        schema={"val_a": pl.Utf8, "val_b": pl.Utf8},
    )
    out = _pairs_returned_frame(eng, column, frame)
    return out.get_column("_returned").to_list()


def _pairs_returned_frame(
    eng: Engine, column: str, pair_frame: pl.DataFrame
) -> pl.DataFrame:
    empty_flag = pair_frame.with_columns(pl.lit(False).alias("_returned"))
    if (
        pair_frame.is_empty()
        or eng.returned_cells_df.is_empty()
        or eng.pending_cells.is_empty()
    ):
        return empty_flag
    pending = eng.pending_cells.filter(pl.col("column") == column)
    ret = eng.returned_cells_df.filter(pl.col("column") == column)
    if pending.is_empty() or ret.is_empty():
        return empty_flag
    scoped = pending.join(
        pair_frame.select("val_a", "val_b").unique(),
        on=["val_a", "val_b"],
        how="inner",
    )
    if scoped.is_empty():
        return empty_flag
    hits = (
        scoped.join(ret, on=eng.keys, how="inner")
        .select("val_a", "val_b")
        .unique()
        .with_columns(pl.lit(True).alias("_returned"))
    )
    return pair_frame.join(hits, on=["val_a", "val_b"], how="left").with_columns(
        pl.col("_returned").fill_null(False)
    )


def _attach_cell_returned(
    eng: Engine, chunk: pl.DataFrame, column: str
) -> pl.DataFrame:
    if chunk.is_empty() or eng.returned_cells_df.is_empty():
        return chunk.with_columns(pl.lit(False).alias("_returned"))
    ret = (
        eng.returned_cells_df.filter(pl.col("column") == column)
        .select(eng.keys)
        .unique()
        .with_columns(pl.lit(True).alias("_returned"))
    )
    if ret.is_empty():
        return chunk.with_columns(pl.lit(False).alias("_returned"))
    return chunk.join(ret, on=eng.keys, how="left").with_columns(
        pl.col("_returned").fill_null(False)
    )


def unmatched_page(
    eng: Engine, side: str, page: int
) -> tuple[list[dict[str, Any]], int, int]:
    accepted = eng.accepted_a_only if side == "A" else eng.accepted_b_only
    frame = eng.a_only if side == "A" else eng.b_only
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
    tags_map = eng._extra_tags
    for side, name in all_extras:
        pending = (side, name) in eng.pending_extras
        tags = tags_map.get((side, name))
        if tags is None:
            tags = extra_insights(name, others[side])
        rows.append(
            {
                "side": side,
                "name": name,
                "pending": 1 if pending else 0,
                "accepted": 0 if pending else 1,
                "speculative": tags[:3],
                "returned": (side, name) in eng.returned_extras,
            }
        )
    return rows


def context_values(
    eng: Engine, key: tuple[str, ...] | None, column: str
) -> list[tuple[str, str, str]]:
    names = [n for n in eng.context_columns.get(column, []) if n in eng.context_pool and n != column]
    if not names:
        return []
    if not key or len(key) != len(eng.keys):
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


def first_pending_key_in_pair(
    eng: Engine, column: str, val_a: str, val_b: str
) -> tuple[str, ...] | None:
    frame = _pair_cells_frame(eng, column, val_a, val_b)
    if frame.is_empty():
        return None
    rec = frame.head(1).row(0, named=True)
    return tuple(str(rec[k]) for k in eng.keys)


def next_pending_key_in_grid(
    eng: Engine, side: str, current: tuple[str, ...]
) -> tuple[str, ...] | None:
    pending = eng.pending_a_only if side == "A" else eng.pending_b_only
    if pending.is_empty():
        return None
    parts: list[pl.Expr] = []
    acc: pl.Expr = pl.lit(True)
    for i, k in enumerate(eng.keys):
        parts.append(acc & (pl.col(k) > current[i]))
        acc = acc & (pl.col(k) == current[i])
    nxt = pending.filter(pl.any_horizontal(parts)).head(1)
    if nxt.is_empty():
        # Accepted the last pending key: stay on the new last remaining.
        nxt = pending.tail(1)
    rec = nxt.row(0, named=True)
    return tuple(str(rec[k]) for k in eng.keys)


def next_pending_cell_in_pair(
    eng: Engine, after: tuple[str, ...]
) -> tuple[str, ...] | None:
    if eng.pair_draft_col is None:
        return None
    frame = _pair_cells_frame(
        eng, eng.pair_draft_col, eng.pair_draft_va or "", eng.pair_draft_vb or ""
    )
    if frame.is_empty():
        return None
    parts: list[pl.Expr] = []
    acc: pl.Expr = pl.lit(True)
    for i, k in enumerate(eng.keys):
        parts.append(acc & (pl.col(k) > after[i]))
        acc = acc & (pl.col(k) == after[i])
    nxt = frame.filter(pl.any_horizontal(parts)).head(1)
    if nxt.is_empty():
        # Accepted the last pending cell: stay on the new last remaining.
        nxt = frame.tail(1)
    rec = nxt.row(0, named=True)
    return tuple(str(rec[k]) for k in eng.keys)


def next_pair_below(
    eng: Engine, column: str, val_a: str, val_b: str
) -> tuple[str, str] | None:
    """Pair that was below ``(val_a, val_b)``, or the new last remaining.

    Call **before** accepting the current pair. Does not materialize the
    full pair list — only the chosen neighbor row.
    """
    groups = eng.pair_groups(column)
    if groups.is_empty():
        return None
    hit = (
        groups.with_row_index("_idx")
        .filter((pl.col("val_a") == val_a) & (pl.col("val_b") == val_b))
        .select("_idx")
    )
    if hit.is_empty():
        return None
    i = int(hit.item(0, 0))
    below = groups.slice(i + 1, 1)
    if below.height:
        rec = below.row(0, named=True)
        return str(rec["val_a"]), str(rec["val_b"])
    if i > 0:
        rec = groups.slice(i - 1, 1).row(0, named=True)
        return str(rec["val_a"]), str(rec["val_b"])
    return None


def union_pairs(
    eng: Engine, columns: list[str], page: int = 0
) -> tuple[list[dict[str, Any]], int, int]:
    """Grouped exact ``(val_a, val_b)`` union across pending cells of ``columns``."""
    empty = pl.DataFrame(
        {"val_a": [], "val_b": [], "n": [], "n_cols": []},
        schema={
            "val_a": pl.Utf8,
            "val_b": pl.Utf8,
            "n": pl.UInt32,
            "n_cols": pl.UInt32,
        },
    )
    if not columns or eng.pending_cells.is_empty():
        return _page(empty, page)
    frame = eng.pending_cells.filter(pl.col("column").is_in(list(columns)))
    if frame.is_empty():
        return _page(empty, page)
    grouped = (
        frame.group_by(["val_a", "val_b"])
        .agg(pl.len().alias("n"), pl.col("column").n_unique().alias("n_cols"))
        .sort(["n", "val_a", "val_b"], descending=[True, False, False])
    )
    return _page(grouped, page)
