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


def first_diff(a: str, b: str) -> int:
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return n


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
    return [f"speculative: {t}" for t in tags]


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
            tags.append("speculative: name would pair if trim")
        elif name.lower() == other.lower() and name != other:
            tags.append("speculative: name would pair if case")
        elif name.strip().lower() == other.strip().lower():
            tags.append("speculative: name would pair if trim/case")
        else:
            ratio = SequenceMatcher(None, name.lower(), other.lower()).ratio()
            if ratio >= 0.72 or name.lower() in other.lower() or other.lower() in name.lower():
                tags.append(f"speculative: near-miss {other!r}")
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
                tags.append("speculative: would match if trim")
            elif tuple(p.lower() for p in key) == tuple(p.lower() for p in other):
                tags.append("speculative: would match if case-fold")
            else:
                tags.append("speculative: would match if trim/case")
            break
    return tags


def column_pattern_insight(pending: pl.DataFrame) -> str | None:
    """Shared value pattern on pending cells (column-level)."""
    if pending.is_empty():
        return None
    a_vals = pending["val_a"].unique()
    b_vals = pending["val_b"].unique()
    if a_vals.len() <= 6 and b_vals.len() <= 6:
        a_set = set(a_vals.to_list())
        b_set = set(b_vals.to_list())
        if a_set != b_set:
            return "speculative: shared value pattern"
    return None


def is_categorical_pending(pending_a: list[str], pending_b: list[str]) -> bool:
    uniq_a = set(pending_a)
    uniq_b = set(pending_b)
    return len(uniq_a) <= 30 and len(uniq_b) <= 30 and len(uniq_a | uniq_b) <= 50
