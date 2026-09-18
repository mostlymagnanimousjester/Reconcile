"""Speculative insights. Never change remaining counts or pairing."""

from __future__ import annotations

import re
from datetime import datetime
from difflib import SequenceMatcher

import polars as pl

DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%Y%m%d",
)

_WS_RE = re.compile(r"[\u00a0\t\r\n]")

CONTEXT_TOP_N = 5
CONTEXT_VALUE_SEP = " | "
CONTEXT_TRUNCATION_MARK = " …"


def context_header(name: str) -> str:
    """Dedicated pair/cell table header for one context column."""
    return f"ctx:{name}"


def format_context_value(value: str, count: int) -> str:
    """One unique context value with its pair-row count."""
    label = value if value else "(empty)"
    return f"{label} {count}"

_EMPTY_SENTINELS = pl.DataFrame(
    {"column": [], "sent_a": [], "sent_b": []},
    schema={"column": pl.Utf8, "sent_a": pl.Utf8, "sent_b": pl.Utf8},
)


def first_diff(a: str, b: str) -> int:
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return n


_POLARS_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%Y%m%d",
)


def unambiguous_date_expr(col: str) -> pl.Expr:
    """Polars equivalent of parse_unambiguous_date: one unique calendar date or null."""
    parsed = [
        pl.col(col).str.to_datetime(fmt, strict=False).dt.date()
        for fmt in _POLARS_DATE_FORMATS
    ]
    lst = pl.concat_list(parsed).list.drop_nulls().list.unique()
    return pl.when(lst.list.len() == 1).then(lst.list.first()).otherwise(pl.lit(None))


def same_date_expr(col_a: str = "val_a", col_b: str = "val_b") -> pl.Expr:
    da = unambiguous_date_expr(col_a)
    db = unambiguous_date_expr(col_b)
    return da.is_not_null() & db.is_not_null() & (da == db)


def parse_unambiguous_date(s: str):
    found: list = []
    for fmt in DATE_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
        except ValueError:
            continue
        found.append(dt.date())
    uniq = set(found)
    if len(uniq) == 1:
        return next(iter(uniq))
    return None


def format_sentinel_value(value: str) -> str:
    """Compact display: empty as `""`; quote when spaces or delimiters would hide the value."""
    if value == "":
        return '""'
    if value.strip() != value or any(ch in value for ch in ' ="\''):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def format_sentinel_insight(value_a: str | None, value_b: str | None) -> str | None:
    """Side + constant value. A side is a sentinel only when it is constant."""
    if value_a is not None and value_b is not None:
        return (
            f"sentinel both A={format_sentinel_value(value_a)} "
            f"B={format_sentinel_value(value_b)}"
        )
    if value_a is not None:
        return f"sentinel A={format_sentinel_value(value_a)}"
    if value_b is not None:
        return f"sentinel B={format_sentinel_value(value_b)}"
    return None


def empty_sentinel_frame() -> pl.DataFrame:
    return _EMPTY_SENTINELS.head(0)


def column_sentinel_frame(cells: pl.DataFrame) -> pl.DataFrame:
    """Per-column constants on comparable (shared-key) rows.

    A side is a sentinel iff it has exactly one unique value on those rows.
    A-only / B-only keys are not comparable and must not be in ``cells``.
    """
    if cells.is_empty():
        return empty_sentinel_frame()
    return (
        cells.group_by("column")
        .agg(
            pl.col("val_a").n_unique().alias("n_a"),
            pl.col("val_b").n_unique().alias("n_b"),
            pl.col("val_a").first().alias("const_a"),
            pl.col("val_b").first().alias("const_b"),
        )
        .select(
            "column",
            pl.when(pl.col("n_a") == 1)
            .then(pl.col("const_a"))
            .otherwise(pl.lit(None))
            .alias("sent_a"),
            pl.when(pl.col("n_b") == 1)
            .then(pl.col("const_b"))
            .otherwise(pl.lit(None))
            .alias("sent_b"),
        )
    )


def format_top_uniques(values: list[str], limit: int = CONTEXT_TOP_N) -> str:
    """Most-occurring unique values with pair-row counts; mark truncation.

    ``values`` is one entry per counted row (the pair grain). Counts are of
    those rows, not of the whole table. Shape: ``foo 12 | bar 4 | baz 1 …``.
    """
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    shown = [format_context_value(value, n) for value, n in ranked[:limit]]
    text = CONTEXT_VALUE_SEP.join(shown)
    if len(ranked) > limit:
        text += CONTEXT_TRUNCATION_MARK
    return text


def cell_insights(val_a: str, val_b: str) -> list[str]:
    if val_a == val_b:
        return []
    tags: list[str] = []
    trim_eq = val_a.strip() == val_b.strip()
    case_eq = val_a.lower() == val_b.lower()
    both_eq = val_a.strip().lower() == val_b.strip().lower()
    if trim_eq:
        tags.append("equal if trim")
    if case_eq:
        tags.append("equal if case-fold")
    if both_eq and not trim_eq and not case_eq:
        tags.append("equal if trim+case")
    if _numeric_equal(val_a, val_b):
        tags.append("equal as numbers")
    if (
        _WS_RE.search(val_a)
        or _WS_RE.search(val_b)
        or val_a != val_a.strip()
        or val_b != val_b.strip()
    ):
        tags.append("invisible/odd whitespace")
    da = parse_unambiguous_date(val_a)
    db = parse_unambiguous_date(val_b)
    if da is not None and db is not None and da == db:
        tags.append("same date")
    return tags


def _numeric_equal(a: str, b: str) -> bool:
    try:
        fa = float(a)
        fb = float(b)
    except ValueError:
        return False
    if a.strip() == "" or b.strip() == "":
        return False
    return fa == fb


def extra_insights(name: str, other_names: list[str]) -> list[str]:
    tags: list[str] = []
    for other in other_names:
        if name == other:
            continue
        if name.strip() == other.strip() and name != other:
            tags.append("name would pair if trim")
        elif name.lower() == other.lower() and name != other:
            tags.append("name would pair if case")
        elif name.strip().lower() == other.strip().lower():
            tags.append("name would pair if trim/case")
        else:
            ratio = SequenceMatcher(None, name.lower(), other.lower()).ratio()
            if ratio >= 0.72 or name.lower() in other.lower() or other.lower() in name.lower():
                tags.append(f"near-miss {other!r}")
    # unique, keep order
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:3]


def unmatched_key_insights(
    key: tuple[str, ...], other_keys: list[tuple[str, ...]]
) -> list[str]:
    tags: list[str] = []
    folded = tuple(p.strip().lower() for p in key)
    for other in other_keys:
        if tuple(p.strip().lower() for p in other) == folded and other != key:
            if tuple(p.strip() for p in key) == tuple(p.strip() for p in other):
                tags.append("would match if trim")
            elif tuple(p.lower() for p in key) == tuple(p.lower() for p in other):
                tags.append("would match if case-fold")
            else:
                tags.append("would match if trim/case")
            break
    return tags


def is_categorical_pending(pending_a: list[str], pending_b: list[str]) -> bool:
    uniq_a = set(pending_a)
    uniq_b = set(pending_b)
    return len(uniq_a) <= 30 and len(uniq_b) <= 30 and len(uniq_a | uniq_b) <= 50
