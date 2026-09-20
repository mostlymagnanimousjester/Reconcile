"""Speculative insights. Never change remaining counts or pairing."""

from __future__ import annotations

from datetime import datetime

import polars as pl

# Python and Polars lists stay in lockstep. %b forms are SAS English months
# (JAN…DEC). Parsing substitutes those tokens for numbers so %b is not
# locale-dependent (do not rely on French/German strptime / Polars %b).
DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%Y%m%d",
    "%d%b%Y",
    "%d%b%y",
    "%d-%b-%Y",
    "%m/%d/%y",
    "%d/%m/%y",
    "%b%Y",
    "%b%y",
    "%d%b%Y:%H:%M:%S",
    "%d%b%y:%H:%M:%S",
)

_POLARS_DATE_FORMATS = DATE_FORMATS

_SAS_MONTH_NUM = {
    "JAN": "01",
    "FEB": "02",
    "MAR": "03",
    "APR": "04",
    "MAY": "05",
    "JUN": "06",
    "JUL": "07",
    "AUG": "08",
    "SEP": "09",
    "OCT": "10",
    "NOV": "11",
    "DEC": "12",
}

_FMT_B_TO_NUMERIC = {
    "%d%b%Y": "%d%m%Y",
    "%d%b%y": "%d%m%y",
    "%d-%b-%Y": "%d-%m-%Y",
    "%b%Y": "%m%Y",
    "%b%y": "%m%y",
    "%d%b%Y:%H:%M:%S": "%d%m%Y:%H:%M:%S",
    "%d%b%y:%H:%M:%S": "%d%m%y:%H:%M:%S",
}

CONTEXT_TOP_N = 5
CONTEXT_VALUE_SEP = " | "
CONTEXT_TRUNCATION_MARK = " …"
CONTEXT_TUPLE_SEP = "|"
CONTEXT_GROUP_MEMBER_SEP = "+"


def context_header(name: str) -> str:
    """Dedicated pair/cell table header for one context column."""
    return f"ctx:{name}"


def context_group_header(gid: int, members: list[str]) -> str:
    """Dedicated pair/cell table header for one context group."""
    return f"ctx:g{gid} {CONTEXT_GROUP_MEMBER_SEP.join(members)}"


def format_context_value(value: str, count: int) -> str:
    """One unique context value with its pair-row count."""
    label = value if value else "(empty)"
    return f"{label} {count}"


def format_context_tuple(parts: list[str]) -> str:
    """One group's tuple: member values in stable order, empty as (empty)."""
    return CONTEXT_TUPLE_SEP.join(p if p else "(empty)" for p in parts)

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


def _format_width(fmt: str) -> int:
    """Character width of a numeric/literal date format (no locale %b)."""
    widths = {"Y": 4, "y": 2, "m": 2, "d": 2, "H": 2, "M": 2, "S": 2}
    n = 0
    i = 0
    while i < len(fmt):
        if fmt[i] == "%" and i + 1 < len(fmt) and fmt[i + 1] in widths:
            n += widths[fmt[i + 1]]
            i += 2
        else:
            n += 1
            i += 1
    return n


def _with_sas_months(s: str) -> str:
    """Replace English 3-letter month tokens with 01–12 (any case)."""
    out: list[str] = []
    i = 0
    upper = s.upper()
    while i < len(s):
        trip = upper[i : i + 3]
        num = _SAS_MONTH_NUM.get(trip)
        if num is not None:
            out.append(num)
            i += 3
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def _sas_month_normalized_expr(col: str) -> pl.Expr:
    expr = pl.col(col)
    for name, num in _SAS_MONTH_NUM.items():
        expr = expr.str.replace(name, num, literal=True)
        expr = expr.str.replace(name.title(), num, literal=True)
        expr = expr.str.replace(name.lower(), num, literal=True)
    return expr


def unambiguous_date_expr(col: str) -> pl.Expr:
    """Polars equivalent of parse_unambiguous_date: one unique calendar date or null."""
    raw = pl.col(col)
    named = _sas_month_normalized_expr(col)
    parsed = []
    for fmt in _POLARS_DATE_FORMATS:
        if "%b" in fmt:
            num_fmt = _FMT_B_TO_NUMERIC[fmt]
            src = named
        else:
            num_fmt = fmt
            src = raw
        width = _format_width(num_fmt)
        parsed.append(
            pl.when(src.str.len_chars() == width)
            .then(src.str.to_datetime(num_fmt, strict=False).dt.date())
            .otherwise(pl.lit(None))
        )
    lst = pl.concat_list(parsed).list.drop_nulls().list.unique()
    return pl.when(lst.list.len() == 1).then(lst.list.first()).otherwise(pl.lit(None))


def same_date_expr(col_a: str = "val_a", col_b: str = "val_b") -> pl.Expr:
    da = unambiguous_date_expr(col_a)
    db = unambiguous_date_expr(col_b)
    return da.is_not_null() & db.is_not_null() & (da == db)


def parse_unambiguous_date(s: str):
    found: list = []
    normalized = _with_sas_months(s)
    for fmt in DATE_FORMATS:
        target = normalized if "%b" in fmt else s
        parse_fmt = _FMT_B_TO_NUMERIC.get(fmt, fmt)
        if len(target) != _format_width(parse_fmt):
            continue
        try:
            dt = datetime.strptime(target, parse_fmt)
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


def format_sentinel_both(value_a: str, value_b: str) -> str:
    """Roster `sent both` cell: A=x B=y using format_sentinel_value on each side."""
    return f"A={format_sentinel_value(value_a)} B={format_sentinel_value(value_b)}"


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


def is_categorical_pending(pending_a: list[str], pending_b: list[str]) -> bool:
    uniq_a = set(pending_a)
    uniq_b = set(pending_b)
    return len(uniq_a) <= 30 and len(uniq_b) <= 30 and len(uniq_a | uniq_b) <= 50
