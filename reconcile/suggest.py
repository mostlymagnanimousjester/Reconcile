"""Deterministic rename recipes for extras (schema-sized names only).

Not a mapping. The TUI never binds or renames; these rows tell the user
which exact header to change in the workbook, then `r`.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import polars as pl

if TYPE_CHECKING:
    from reconcile.engine import Engine

# One separator class: underscore, space, hyphen.
_SEP_RE = re.compile(r"[_\s-]+")

WHY_STRIP = "strip"
WHY_CASE = "case"
WHY_SEPARATORS = "separators"
WHY_TOKEN_SORT = "token sort"


def canonical_tokens(name: str) -> tuple[str, ...]:
    """Strip, casefold, split on the separator class, sort tokens."""
    folded = name.strip().casefold()
    return tuple(sorted(t for t in _SEP_RE.split(folded) if t))


def why_names(name_a: str, name_b: str) -> list[str]:
    """Which stacked normalizers fired to make the two extras match."""
    why: list[str] = []
    if name_a != name_a.strip() or name_b != name_b.strip():
        why.append(WHY_STRIP)
    a = name_a.strip()
    b = name_b.strip()
    if a != a.casefold() or b != b.casefold():
        why.append(WHY_CASE)
    a = a.casefold()
    b = b.casefold()
    a_ord = tuple(t for t in _SEP_RE.split(a) if t)
    b_ord = tuple(t for t in _SEP_RE.split(b) if t)
    if a != b:
        a_seps = tuple(_SEP_RE.findall(a))
        b_seps = tuple(_SEP_RE.findall(b))
        if a_seps != b_seps:
            why.append(WHY_SEPARATORS)
    if a_ord != b_ord and sorted(a_ord) == sorted(b_ord):
        why.append(WHY_TOKEN_SORT)
    return why


def preview_pair(eng: Engine, name_a: str, name_b: str) -> tuple[int, int]:
    """Shared inner-join keys and raw `!=` pending count (null→\"\").

    Compares only those two columns on already-matched keys. Does not
    materialize the value frames.
    """
    if (
        eng.matched_a.is_empty()
        or name_a not in eng.matched_a.columns
        or name_b not in eng.matched_b.columns
    ):
        return 0, 0
    joined = eng.matched_a.select([*eng.keys, name_a]).join(
        eng.matched_b.select([*eng.keys, name_b]),
        on=eng.keys,
        how="inner",
    )
    shared = joined.height
    if shared == 0:
        return 0, 0
    va = pl.col(name_a).fill_null("").cast(pl.Utf8)
    vb = pl.col(name_b).fill_null("").cast(pl.Utf8)
    pending = joined.filter(va != vb).height
    return shared, pending


def format_why(why: list[str]) -> str:
    return ", ".join(why)


def format_preview(shared: int, pending: int) -> str:
    return f"{shared} / {pending}"


def suggest_extras(eng: Engine) -> list[dict[str, Any]]:
    """All extra-A × extra-B pairs whose canonical tokens match.

    Collisions (two recipes claiming the same extra) are all returned.
    The tool never picks. Omit callers should hide the block when empty.
    """
    cached = getattr(eng, "_suggest_extras_cache", None)
    if cached is not None:
        return cached
    extras_a = list(eng.extras_a)
    extras_b = list(eng.extras_b)
    if not extras_a or not extras_b:
        eng._suggest_extras_cache = []
        return []
    by_canon: dict[tuple[str, ...], list[str]] = {}
    for name in extras_b:
        canon = canonical_tokens(name)
        if not canon:
            continue
        by_canon.setdefault(canon, []).append(name)
    rows: list[dict[str, Any]] = []
    for name_a in extras_a:
        canon = canonical_tokens(name_a)
        if not canon:
            continue
        for name_b in by_canon.get(canon, []):
            shared, pending = preview_pair(eng, name_a, name_b)
            rows.append(
                {
                    "name_a": name_a,
                    "name_b": name_b,
                    "why": why_names(name_a, name_b),
                    "shared": shared,
                    "pending": pending,
                }
            )
    eng._suggest_extras_cache = rows
    return rows
