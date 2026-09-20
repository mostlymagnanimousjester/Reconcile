"""Excel ``-sheets`` / ``--sheets`` grammar and workbook presence checks."""

from __future__ import annotations

import re
from pathlib import Path

from reconcile.delimited import abs_path
from reconcile.errors import HardFail
from reconcile.excel import list_excel_sheets

_INT = re.compile(r"^-?\d+$")
_RANGE = re.compile(r"^(-?\d+)-(-?\d+)$")


def expand_sheets(raw: str, flag: str = "-sheets") -> list[str]:
    """Expand a ``-sheets`` SET into ordered sheet names.

    Braces: ``PREFIX{N or start-end, …}`` with no zero-padding
    (``data{1-4,7}`` → ``data1``…``data4``, ``data7``).
    No braces: exact comma list (``Jan,Feb``).
    """
    if raw is None:
        raise HardFail(f"{flag} is empty")
    text = raw.strip()
    if not text:
        raise HardFail(f"{flag} is empty: {raw!r}")
    if "{" in text or "}" in text:
        names = _expand_braced(text, flag, raw)
    else:
        names = _expand_name_list(text, flag, raw)
    _reject_duplicates(names, flag, raw)
    return names


def _fail(flag: str, raw: str, detail: str) -> HardFail:
    return HardFail(f"{flag} {raw!r}: {detail}")


def _expand_name_list(text: str, flag: str, raw: str) -> list[str]:
    parts = [p.strip() for p in text.split(",")]
    if not parts or any(p == "" for p in parts):
        raise _fail(flag, raw, "empty item")
    if any("{" in p or "}" in p for p in parts):
        raise _fail(flag, raw, "braces are only valid as PREFIX{N or start-end, …}")
    return parts


def _expand_braced(text: str, flag: str, raw: str) -> list[str]:
    if text.count("{") != 1 or text.count("}") != 1:
        raise _fail(flag, raw, "braces must be exactly one {…} suffix")
    open_at = text.index("{")
    if not text.endswith("}") or open_at > text.index("}"):
        raise _fail(flag, raw, "braces must be exactly one {…} suffix")
    prefix = text[:open_at]
    inner = text[open_at + 1 : -1]
    items = [p.strip() for p in inner.split(",")]
    if not items or any(i == "" for i in items):
        raise _fail(flag, raw, "empty item")
    names: list[str] = []
    for item in items:
        names.extend(prefix + str(n) for n in _parse_brace_item(item, flag, raw))
    return names


def _parse_brace_item(item: str, flag: str, raw: str) -> list[int]:
    if _INT.fullmatch(item):
        return [int(item, 10)]
    m = _RANGE.fullmatch(item)
    if m:
        start = int(m.group(1), 10)
        end = int(m.group(2), 10)
        if start > end:
            raise _fail(flag, raw, f"start > end in {item!r}")
        return list(range(start, end + 1))
    raise _fail(
        flag,
        raw,
        f"item {item!r} is not an integer N or start-end (use a name list without braces)",
    )


def _reject_duplicates(names: list[str], flag: str, raw: str) -> None:
    seen: set[str] = set()
    for name in names:
        if name in seen:
            raise _fail(flag, raw, f"duplicate name after expansion: {name!r}")
        seen.add(name)


def require_sheets_present(path: str | Path, names: list[str]) -> None:
    """HardFail if any name is missing. Lists sheets only; does not data-load."""
    path = abs_path(path)
    available = list_excel_sheets(path)
    have = set(available)
    missing = [n for n in names if n not in have]
    if not missing:
        return
    shown = ", ".join(repr(n) for n in available)
    raise HardFail(
        f"Missing sheet {missing[0]!r} in {path}. Available: {shown}"
    )
