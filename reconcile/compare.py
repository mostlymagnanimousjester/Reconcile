"""Join, unpivot mismatches, and rebuild of compare frames."""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from reconcile.errors import HardFail, format_key_tuple
from reconcile.insights import column_sentinel_frame, empty_sentinel_frame

if TYPE_CHECKING:
    from reconcile.engine import Engine


def _empty_mismatch_schema(keys: list[str]) -> dict[str, pl.DataType]:
    schema: dict[str, pl.DataType] = {k: pl.Utf8 for k in keys}
    schema["column"] = pl.Utf8
    schema["val_a"] = pl.Utf8
    schema["val_b"] = pl.Utf8
    return schema


def _empty_df(schema: dict[str, pl.DataType]) -> pl.DataFrame:
    return pl.DataFrame({k: [] for k in schema}).cast(schema)


def _dup_headers_already_checked(headers: list[str], side: str) -> None:
    counts: dict[str, int] = {}
    for h in headers:
        counts[h] = counts.get(h, 0) + 1
    dups = [h for h, n in counts.items() if n > 1]
    if dups:
        raise HardFail(f"Duplicate column name on side {side}: {dups[0]!r}")


def _check_keys_exist(headers: list[str], keys: list[str], side: str) -> None:
    missing = [k for k in keys if k not in headers]
    if missing:
        raise HardFail(f"Missing key column {missing[0]!r} on side {side}")


def _check_duplicate_keys(df: pl.DataFrame, keys: list[str], side: str) -> None:
    if df.is_empty():
        return
    counted = df.group_by(keys).agg(pl.len().alias("n")).filter(pl.col("n") > 1)
    if counted.is_empty():
        return
    row = counted.row(0, named=True)
    key = tuple(str(row[k]) for k in keys)
    n = int(row["n"])
    raise HardFail(
        f"Duplicate key on side {side}: {format_key_tuple(key)} occurs {n} times"
    )


# Temporary struct used only to join key tuples. A user column may be named
# `_key`; this name must not be that, or the join overwrites and then drops it.
_JOIN_STRUCT = "__reconcile_join_struct__"


def _join_struct_name(keys: list[str]) -> str:
    name = _JOIN_STRUCT
    occupied = set(keys)
    while name in occupied:
        name += "_"
    return name


def _struct_key(keys: list[str], name: str) -> pl.Expr:
    return pl.struct(keys).alias(name)


def rebuild_frames(eng: Engine) -> None:
    _dup_headers_already_checked(eng.a.headers, "A")
    _dup_headers_already_checked(eng.b.headers, "B")
    _check_keys_exist(eng.a.headers, eng.keys, "A")
    _check_keys_exist(eng.b.headers, eng.keys, "B")
    _check_duplicate_keys(eng.a.frame, eng.keys, "A")
    _check_duplicate_keys(eng.b.frame, eng.keys, "B")

    a_names = set(eng.a.headers)
    b_names = set(eng.b.headers)
    eng.intersection = [n for n in eng.a.headers if n in b_names]
    eng.comparable = [n for n in eng.intersection if n not in eng.keys]
    eng.extras_a = [n for n in eng.a.headers if n not in b_names]
    eng.extras_b = [n for n in eng.b.headers if n not in a_names]
    eng.context_pool = [n for n in eng.intersection if n not in eng.keys]

    join_name = _join_struct_name(eng.keys)
    a_k = eng.a.frame.select(eng.keys).with_columns(_struct_key(eng.keys, join_name))
    b_k = eng.b.frame.select(eng.keys).with_columns(_struct_key(eng.keys, join_name))
    a_only_keys = a_k.join(b_k, on=join_name, how="anti").drop(join_name)
    b_only_keys = b_k.join(a_k, on=join_name, how="anti").drop(join_name)
    matched_keys = a_k.join(b_k, on=join_name, how="inner").drop(join_name)

    if a_only_keys.is_empty():
        eng.a_only = eng.a.frame.head(0)
    else:
        eng.a_only = eng.a.frame.join(a_only_keys, on=eng.keys, how="inner")
    if b_only_keys.is_empty():
        eng.b_only = eng.b.frame.head(0)
    else:
        eng.b_only = eng.b.frame.join(b_only_keys, on=eng.keys, how="inner")

    if matched_keys.is_empty():
        eng.matched_a = eng.a.frame.head(0)
        eng.matched_b = eng.b.frame.head(0)
    else:
        eng.matched_a = eng.a.frame.join(matched_keys, on=eng.keys, how="inner")
        eng.matched_b = eng.b.frame.join(matched_keys, on=eng.keys, how="inner")

    schema = _empty_mismatch_schema(eng.keys)
    if eng.matched_a.is_empty() or not eng.comparable:
        eng.mismatches = _empty_df(schema)
        eng.column_sentinels = empty_sentinel_frame()
    else:
        a_long = eng.matched_a.select(eng.keys + eng.comparable).unpivot(
            index=eng.keys, on=eng.comparable, variable_name="column", value_name="val_a"
        )
        b_long = eng.matched_b.select(eng.keys + eng.comparable).unpivot(
            index=eng.keys, on=eng.comparable, variable_name="column", value_name="val_b"
        )
        comparable_cells = a_long.join(b_long, on=[*eng.keys, "column"], how="inner")
        eng.mismatches = comparable_cells.filter(pl.col("val_a") != pl.col("val_b"))
        eng.column_sentinels = column_sentinel_frame(comparable_cells)


def sort_unmatched(eng: Engine) -> None:
    if not eng.a_only.is_empty():
        eng.a_only = eng.a_only.sort(eng.keys)
    if not eng.b_only.is_empty():
        eng.b_only = eng.b_only.sort(eng.keys)


def pair_groups(eng: Engine, column: str) -> pl.DataFrame:
    empty = pl.DataFrame(
        {"val_a": [], "val_b": [], "n": []},
        schema={"val_a": pl.Utf8, "val_b": pl.Utf8, "n": pl.UInt32},
    )
    cached = getattr(eng, "_pair_groups_df", None)
    if cached is None or cached.is_empty():
        return empty
    hit = cached.filter(pl.col("column") == column).select("val_a", "val_b", "n")
    if hit.is_empty():
        return empty
    return hit


def equal_count(eng: Engine, column: str) -> int:
    if column not in eng.comparable:
        return 0
    mismatch_n = 0
    if not eng.mismatches.is_empty():
        mismatch_n = eng.mismatches.filter(pl.col("column") == column).height
    return eng.matched_a.height - mismatch_n
