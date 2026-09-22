"""Speculative insights. Never change remaining counts or pairing."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta

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
CONTEXT_TRUNCATION_MARK = " | +{n} more"
CONTEXT_TUPLE_SEP = "|"
CONTEXT_GROUP_MEMBER_SEP = "+"


def context_header(name: str) -> str:
    """Dedicated pair/cell table header for one context column."""
    return name


def context_group_header(gid: int, members: list[str]) -> str:
    """Dedicated pair/cell table header for one context group."""
    return f"g{gid} {CONTEXT_GROUP_MEMBER_SEP.join(members)}"


def format_context_value(value: str, count: int) -> str:
    """One unique context value with its pending-row count for this pair."""
    label = value if value else "(empty)"
    return f"{label}×{count}"


def format_context_truncation(n_more: int) -> str:
    """How many unique context values were omitted after the top-N list."""
    return CONTEXT_TRUNCATION_MARK.format(n=n_more)


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
    those rows, not of the whole table. Shape: ``foo×12 | bar×4 | baz×1 | +N more``.
    """
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    shown = [format_context_value(value, n) for value, n in ranked[:limit]]
    text = CONTEXT_VALUE_SEP.join(shown)
    if len(ranked) > limit:
        text += format_context_truncation(len(ranked) - limit)
    return text


def cell_insights(val_a: str, val_b: str) -> list[str]:
    if val_a == val_b:
        return []
    tags: list[str] = []
    trim_eq = val_a.strip() == val_b.strip()
    case_eq = val_a.lower() == val_b.lower()
    both_eq = val_a.strip().lower() == val_b.strip().lower()
    if trim_eq:
        tags.append("trim")
    if case_eq:
        tags.append("case")
    if both_eq and not trim_eq and not case_eq:
        tags.append("trim+case")
    if _numeric_equal(val_a, val_b):
        tags.append("num")
    da = parse_unambiguous_date(val_a)
    db = parse_unambiguous_date(val_b)
    if da is not None and db is not None and da == db:
        tags.append("date")
    if _money_equal(val_a, val_b):
        tags.append("money")
    if _pct_equal(val_a, val_b):
        tags.append("pct")
    if _idpad_equal(val_a, val_b):
        tags.append("idpad")
    if _bool_equal(val_a, val_b):
        tags.append("bool")
    if _acctneg_equal(val_a, val_b):
        tags.append("acctneg")
    if _xlsdate_equal(val_a, val_b):
        tags.append("xlsdate")
    if _inws_equal(val_a, val_b):
        tags.append("inws")
    if _dash_equal(val_a, val_b):
        tags.append("dash")
    if _fold_equal(val_a, val_b):
        tags.append("fold")
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


_BOOL_TOKENS = frozenset({"y", "yes", "true", "t", "1", "n", "no", "false", "f", "0"})
_ASCII_DIGITS = re.compile(r"^[0-9]+$")
_DASH_RE = re.compile(r"[-\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe63\uff0d]")
_DASH_CLASS = r"[-\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe63\uff0d]"
_EXCEL_EPOCH = date(1899, 12, 30)
# 5-digit serials like 44927; exclude 8-digit YYYYMMDD from the serial path.
_SERIAL_MAX = 99999


def _money_stripped(s: str) -> str:
    return s.replace("$", "").replace("€", "").replace("£", "").replace(",", "").replace(" ", "")


def _money_equal(a: str, b: str) -> bool:
    sa, sb = _money_stripped(a), _money_stripped(b)
    if sa == a and sb == b:
        return False
    return _numeric_equal(sa, sb)


def _pct_parts(s: str) -> tuple[float | None, bool]:
    t = s.strip()
    has = t.endswith("%")
    core = t[:-1].strip() if has else t
    if core == "":
        return None, has
    try:
        val = float(core)
    except ValueError:
        return None, has
    return (val / 100.0 if has else val), has


def _pct_equal(a: str, b: str) -> bool:
    va, ha = _pct_parts(a)
    vb, hb = _pct_parts(b)
    if va is None or vb is None or not (ha or hb):
        return False
    return va == vb


def _idpad_equal(a: str, b: str) -> bool:
    if "." in a or "." in b:
        return False
    if not _ASCII_DIGITS.fullmatch(a) or not _ASCII_DIGITS.fullmatch(b):
        return False
    return int(a) == int(b)


def _bool_equal(a: str, b: str) -> bool:
    return a.lower() in _BOOL_TOKENS and b.lower() in _BOOL_TOKENS


def _acctneg_norm(s: str) -> tuple[str, bool]:
    t = s.strip()
    if len(t) >= 2 and t.startswith("(") and t.endswith(")"):
        return "-" + t[1:-1].strip(), True
    return t, False


def _acctneg_equal(a: str, b: str) -> bool:
    na, pa = _acctneg_norm(a)
    nb, pb = _acctneg_norm(b)
    if not pa and not pb:
        return False
    return _numeric_equal(na, nb)


def _excel_serial_date(s: str):
    if parse_unambiguous_date(s) is not None:
        return None
    if not _ASCII_DIGITS.fullmatch(s):
        return None
    n = int(s)
    if n < 0 or n > _SERIAL_MAX:
        return None
    return _EXCEL_EPOCH + timedelta(days=n)


def _xlsdate_equal(a: str, b: str) -> bool:
    da = parse_unambiguous_date(a)
    db = parse_unambiguous_date(b)
    sa = _excel_serial_date(a)
    sb = _excel_serial_date(b)
    res_a = da or sa
    res_b = db or sb
    if res_a is None or res_b is None or res_a != res_b:
        return False
    if da is not None and db is not None:
        return False
    return sa is not None or sb is not None


def _inws_equal(a: str, b: str) -> bool:
    return re.sub(r"\s+", " ", a) == re.sub(r"\s+", " ", b)


def _dash_equal(a: str, b: str) -> bool:
    return _DASH_RE.sub("-", a) == _DASH_RE.sub("-", b)


def _fold_text(s: str) -> str:
    nfkc = unicodedata.normalize("NFKC", s)
    return "".join(
        c for c in unicodedata.normalize("NFD", nfkc) if not unicodedata.category(c).startswith("M")
    )


def _fold_equal(a: str, b: str) -> bool:
    return _fold_text(a) == _fold_text(b)


def _float_expr(expr: pl.Expr) -> pl.Expr:
    stripped = expr.str.strip_chars()
    return (
        pl.when(stripped != "")
        .then(stripped.cast(pl.Float64, strict=False))
        .otherwise(pl.lit(None, dtype=pl.Float64))
    )


def money_expr(col_a: str = "val_a", col_b: str = "val_b") -> pl.Expr:
    na = (
        pl.col(col_a)
        .str.replace_all(r"[$€£]", "")
        .str.replace_all(",", "")
        .str.replace_all(" ", "")
    )
    nb = (
        pl.col(col_b)
        .str.replace_all(r"[$€£]", "")
        .str.replace_all(",", "")
        .str.replace_all(" ", "")
    )
    fa, fb = _float_expr(na), _float_expr(nb)
    stripped = (na != pl.col(col_a)) | (nb != pl.col(col_b))
    return stripped & fa.is_not_null() & fb.is_not_null() & (fa == fb)


def pct_expr(col_a: str = "val_a", col_b: str = "val_b") -> pl.Expr:
    def _parts(col: str) -> tuple[pl.Expr, pl.Expr]:
        raw = pl.col(col).str.strip_chars()
        has = raw.str.ends_with("%")
        core = pl.when(has).then(raw.str.strip_suffix("%").str.strip_chars()).otherwise(raw)
        num = _float_expr(core)
        return pl.when(has).then(num / 100.0).otherwise(num), has

    va, ha = _parts(col_a)
    vb, hb = _parts(col_b)
    return (ha | hb) & va.is_not_null() & vb.is_not_null() & (va == vb)


def idpad_expr(col_a: str = "val_a", col_b: str = "val_b") -> pl.Expr:
    a, b = pl.col(col_a), pl.col(col_b)
    digits = a.str.contains(r"^[0-9]+$") & b.str.contains(r"^[0-9]+$")
    return digits & (a.cast(pl.Int64, strict=False) == b.cast(pl.Int64, strict=False))


def bool_expr(col_a: str = "val_a", col_b: str = "val_b") -> pl.Expr:
    tokens = list(_BOOL_TOKENS)
    return pl.col(col_a).str.to_lowercase().is_in(tokens) & pl.col(col_b).str.to_lowercase().is_in(
        tokens
    )


def acctneg_expr(col_a: str = "val_a", col_b: str = "val_b") -> pl.Expr:
    def _norm(col: str) -> tuple[pl.Expr, pl.Expr]:
        raw = pl.col(col).str.strip_chars()
        is_paren = raw.str.contains(r"^\(.*\)$")
        inner = raw.str.strip_prefix("(").str.strip_suffix(")").str.strip_chars()
        return pl.when(is_paren).then(pl.lit("-") + inner).otherwise(raw), is_paren

    na, pa = _norm(col_a)
    nb, pb = _norm(col_b)
    fa, fb = _float_expr(na), _float_expr(nb)
    return (pa | pb) & fa.is_not_null() & fb.is_not_null() & (fa == fb)


def excel_serial_date_expr(col: str) -> pl.Expr:
    raw = pl.col(col)
    is_serial = raw.str.contains(r"^[0-9]+$")
    n = raw.cast(pl.Int64, strict=False)
    in_range = n.is_not_null() & (n >= 0) & (n <= _SERIAL_MAX)
    not_date = unambiguous_date_expr(col).is_null()
    return (
        pl.when(is_serial & in_range & not_date)
        .then(pl.lit(_EXCEL_EPOCH) + pl.duration(days=n))
        .otherwise(pl.lit(None))
    )


def xlsdate_expr(col_a: str = "val_a", col_b: str = "val_b") -> pl.Expr:
    da = unambiguous_date_expr(col_a)
    db = unambiguous_date_expr(col_b)
    sa = excel_serial_date_expr(col_a)
    sb = excel_serial_date_expr(col_b)
    ra = pl.coalesce(da, sa)
    rb = pl.coalesce(db, sb)
    return ra.is_not_null() & rb.is_not_null() & (ra == rb) & (sa.is_not_null() | sb.is_not_null())


def roster_stat_date_exprs(
    col_a: str = "val_a", col_b: str = "val_b"
) -> tuple[pl.Expr, pl.Expr]:
    """One calendar parse per side for the roster stats plan.

    Pair and cell hints keep ``same_date_expr`` / ``xlsdate_expr``.
    """
    return (
        unambiguous_date_expr(col_a).alias("_date_a"),
        unambiguous_date_expr(col_b).alias("_date_b"),
    )


def roster_stat_serial_exprs(
    col_a: str = "val_a",
    col_b: str = "val_b",
    date_a: str = "_date_a",
    date_b: str = "_date_b",
) -> tuple[pl.Expr, pl.Expr]:
    """Excel serials from dates already materialized on the row."""

    def _one(col: str, date_col: str, alias: str) -> pl.Expr:
        raw = pl.col(col)
        is_serial = raw.str.contains(r"^[0-9]+$")
        n = raw.cast(pl.Int64, strict=False)
        in_range = n.is_not_null() & (n >= 0) & (n <= _SERIAL_MAX)
        not_date = pl.col(date_col).is_null()
        return (
            pl.when(is_serial & in_range & not_date)
            .then(pl.lit(_EXCEL_EPOCH) + pl.duration(days=n))
            .otherwise(pl.lit(None))
            .alias(alias)
        )

    return _one(col_a, date_a, "_ser_a"), _one(col_b, date_b, "_ser_b")


def same_date_from_materialized(
    date_a: str = "_date_a", date_b: str = "_date_b"
) -> pl.Expr:
    da = pl.col(date_a)
    db = pl.col(date_b)
    return da.is_not_null() & db.is_not_null() & (da == db)


def xlsdate_from_materialized(
    date_a: str = "_date_a",
    date_b: str = "_date_b",
    ser_a: str = "_ser_a",
    ser_b: str = "_ser_b",
) -> pl.Expr:
    da = pl.col(date_a)
    db = pl.col(date_b)
    sa = pl.col(ser_a)
    sb = pl.col(ser_b)
    ra = pl.coalesce(da, sa)
    rb = pl.coalesce(db, sb)
    return ra.is_not_null() & rb.is_not_null() & (ra == rb) & (sa.is_not_null() | sb.is_not_null())


def inws_expr(col_a: str = "val_a", col_b: str = "val_b") -> pl.Expr:
    return pl.col(col_a).str.replace_all(r"\s+", " ") == pl.col(col_b).str.replace_all(r"\s+", " ")


def dash_expr(col_a: str = "val_a", col_b: str = "val_b") -> pl.Expr:
    return pl.col(col_a).str.replace_all(_DASH_CLASS, "-") == pl.col(col_b).str.replace_all(
        _DASH_CLASS, "-"
    )


def fold_expr(col_a: str = "val_a", col_b: str = "val_b") -> pl.Expr:
    def _norm(col: str) -> pl.Expr:
        return (
            pl.col(col)
            .str.normalize("NFKC")
            .str.normalize("NFD")
            .str.replace_all(r"\p{M}", "")
        )

    return _norm(col_a) == _norm(col_b)


def is_categorical_pending(pending_a: list[str], pending_b: list[str]) -> bool:
    uniq_a = set(pending_a)
    uniq_b = set(pending_b)
    return len(uniq_a) <= 30 and len(uniq_b) <= 30 and len(uniq_a | uniq_b) <= 50
