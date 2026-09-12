"""Compare contract, pending/accept snapshots, refresh, and .recon.zip."""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

import polars as pl

from reconcile.delimited import abs_path
from reconcile.errors import HardFail, format_key_tuple
from reconcile.insights import (
    cell_insights,
    column_pattern_insight,
    extra_insights,
    first_diff,
    is_categorical_pending,
    unmatched_key_insights,
)
from reconcile.load import SideTable, load_side

PAGE_SIZE = 100
SCHEMA_VERSION = 1

ScreenName = Literal[
    "roster",
    "overview",
    "pair_list",
    "cell_step",
    "accepted",
    "equal",
    "all_matched",
    "a_only",
    "b_only",
    "extras",
]


@dataclass(frozen=True)
class CellSnap:
    key: tuple[str, ...]
    column: str
    val_a: str
    val_b: str


@dataclass(frozen=True)
class UnmatchedSnap:
    side: str
    key: tuple[str, ...]
    row: dict[str, str]


@dataclass(frozen=True)
class ExtraSnap:
    side: str
    name: str


@dataclass
class Place:
    screen: ScreenName = "roster"
    column: str | None = None
    extra_side: str | None = None
    extra_name: str | None = None
    view_tab: str = "pending"
    roster_filter: str = ""
    last_pair: tuple[str, str, str] | None = None  # column, val_a, val_b
    pair_val_a: str | None = None
    pair_val_b: str | None = None
    page: int = 0
    focused_name: str | None = None
    focused_key: tuple[str, ...] | None = None


@dataclass
class RosterRow:
    kind: str  # column / A-only / B-only / extra
    name: str
    side: str  # A / B / —
    pending: int
    concentration: float  # 0..1, or 0 for non-columns
    top_pair_pct: str
    accepted: int
    equal: str
    categorical: str
    speculative: str
    returned: bool = False


@dataclass
class RefreshDelta:
    pending_before: int
    pending_after: int
    accepted_before: int
    accepted_after: int
    returned: int
    message: str


class InTuiError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


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


def _struct_key(keys: list[str]) -> pl.Expr:
    return pl.struct(keys).alias("_key")


def _empty_cell_snaps(keys: list[str]) -> pl.DataFrame:
    return _empty_df(_empty_mismatch_schema(keys))


def _cell_snaps_from_json(keys: list[str], cells: list[dict[str, Any]]) -> pl.DataFrame:
    if not cells:
        return _empty_cell_snaps(keys)
    data: dict[str, list[str]] = {k: [] for k in keys}
    data["column"] = []
    data["val_a"] = []
    data["val_b"] = []
    for c in cells:
        key = list(c.get("key") or [])
        for i, k in enumerate(keys):
            data[k].append(str(key[i]) if i < len(key) else "")
        data["column"].append(str(c.get("column", "")))
        data["val_a"].append(str(c.get("val_a", "")))
        data["val_b"].append(str(c.get("val_b", "")))
    return pl.DataFrame(data).unique()


def _key_eq_expr(keys: list[str], key: tuple[str, ...]) -> pl.Expr:
    expr: pl.Expr = pl.lit(True)
    for k, v in zip(keys, key):
        expr = expr & (pl.col(k) == v)
    return expr


class Engine:
    """In-memory session. Polars owns the frames; TUI asks for pages."""

    def __init__(self, side_a: SideTable, side_b: SideTable, keys: list[str]) -> None:
        self.a = side_a
        self.b = side_b
        self.keys = keys
        self.cell_snaps: pl.DataFrame = _empty_cell_snaps(keys)
        self.unmatched_snaps: list[UnmatchedSnap] = []
        self.extra_snaps: list[ExtraSnap] = []
        self.context_columns: dict[str, list[str]] = {}
        self.place = Place()
        self.column_draft: set[str] = set()
        self.pair_draft_keys: set[tuple[str, ...]] | None = None
        self.pair_draft_col: str | None = None
        self.pair_draft_va: str | None = None
        self.pair_draft_vb: str | None = None
        self.returned_cells: set[tuple[tuple[str, ...], str]] = set()
        self.returned_keys: set[tuple[str, tuple[str, ...]]] = set()
        self.returned_extras: set[tuple[str, str]] = set()
        self.last_refresh_delta: RefreshDelta | None = None
        self.tui_error: str | None = None
        self._rebuild()

    # --- construction ---

    @classmethod
    def from_paths(
        cls,
        a_path: str,
        b_path: str,
        keys: list[str],
        a_sheet: str | None = None,
        b_sheet: str | None = None,
        a_delim: str | None = None,
        b_delim: str | None = None,
        a_encoding: str | None = None,
        b_encoding: str | None = None,
    ) -> Engine:
        side_a = load_side(
            a_path, a_sheet, "A", delimiter=a_delim, encoding=a_encoding
        )
        side_b = load_side(
            b_path, b_sheet, "B", delimiter=b_delim, encoding=b_encoding
        )
        eng = cls(side_a, side_b, keys)
        return eng

    def _rebuild(self) -> None:
        _dup_headers_already_checked(self.a.headers, "A")
        _dup_headers_already_checked(self.b.headers, "B")
        _check_keys_exist(self.a.headers, self.keys, "A")
        _check_keys_exist(self.b.headers, self.keys, "B")
        _check_duplicate_keys(self.a.frame, self.keys, "A")
        _check_duplicate_keys(self.b.frame, self.keys, "B")

        a_names = set(self.a.headers)
        b_names = set(self.b.headers)
        self.intersection = [n for n in self.a.headers if n in b_names]
        self.comparable = [n for n in self.intersection if n not in self.keys]
        self.extras_a = [n for n in self.a.headers if n not in b_names]
        self.extras_b = [n for n in self.b.headers if n not in a_names]
        self.context_pool = [n for n in self.intersection if n not in self.keys]

        a_k = self.a.frame.select(self.keys).with_columns(_struct_key(self.keys))
        b_k = self.b.frame.select(self.keys).with_columns(_struct_key(self.keys))
        a_only_keys = a_k.join(b_k, on="_key", how="anti").drop("_key")
        b_only_keys = b_k.join(a_k, on="_key", how="anti").drop("_key")
        matched_keys = a_k.join(b_k, on="_key", how="inner").drop("_key")

        if a_only_keys.is_empty():
            self.a_only = self.a.frame.head(0)
        else:
            self.a_only = self.a.frame.join(a_only_keys, on=self.keys, how="inner")
        if b_only_keys.is_empty():
            self.b_only = self.b.frame.head(0)
        else:
            self.b_only = self.b.frame.join(b_only_keys, on=self.keys, how="inner")

        if matched_keys.is_empty():
            self.matched_a = self.a.frame.head(0)
            self.matched_b = self.b.frame.head(0)
        else:
            self.matched_a = self.a.frame.join(matched_keys, on=self.keys, how="inner")
            self.matched_b = self.b.frame.join(matched_keys, on=self.keys, how="inner")

        schema = _empty_mismatch_schema(self.keys)
        parts: list[pl.DataFrame] = []
        if not self.matched_a.is_empty() and self.comparable:
            a_vals = self.matched_a.select(self.keys + self.comparable)
            b_renames = {c: f"{c}__b" for c in self.comparable}
            b_vals = self.matched_b.select(self.keys + self.comparable).rename(b_renames)
            joined = a_vals.join(b_vals, on=self.keys, how="inner")
            for col in self.comparable:
                chunk = (
                    joined.filter(pl.col(col) != pl.col(f"{col}__b"))
                    .select(
                        [*self.keys, pl.lit(col).alias("column"), pl.col(col).alias("val_a"), pl.col(f"{col}__b").alias("val_b")]
                    )
                )
                parts.append(chunk)
        if parts:
            self.mismatches = pl.concat(parts, how="vertical")
        else:
            self.mismatches = _empty_df(schema)

        self._apply_snapshots()
        self._sort_unmatched()

    def _sort_unmatched(self) -> None:
        if not self.a_only.is_empty():
            self.a_only = self.a_only.sort(self.keys)
        if not self.b_only.is_empty():
            self.b_only = self.b_only.sort(self.keys)

    def _apply_snapshots(self) -> None:
        self.pending_cells = self._pending_cells()
        self.accepted_cells = self._accepted_cells_current()
        self.pending_a_only, self.accepted_a_only = self._split_unmatched("A")
        self.pending_b_only, self.accepted_b_only = self._split_unmatched("B")
        self.pending_extras, self.accepted_extras = self._split_extras()

    def _pending_cells(self) -> pl.DataFrame:
        if self.mismatches.is_empty() or self.cell_snaps.is_empty():
            return self.mismatches
        return self.mismatches.join(
            self.cell_snaps, on=[*self.keys, "column", "val_a", "val_b"], how="anti"
        )

    def _accepted_cells_current(self) -> pl.DataFrame:
        if self.mismatches.is_empty() or self.cell_snaps.is_empty():
            return self.mismatches.head(0)
        return self.mismatches.join(
            self.cell_snaps, on=[*self.keys, "column", "val_a", "val_b"], how="inner"
        )

    def _row_matches(self, frame_row: dict[str, Any], snap_row: dict[str, str]) -> bool:
        for col, val in frame_row.items():
            sval = snap_row.get(col, "")
            if str(val) != sval:
                return False
        return True

    def _split_unmatched(self, side: str) -> tuple[pl.DataFrame, pl.DataFrame]:
        frame = self.a_only if side == "A" else self.b_only
        snaps = [s for s in self.unmatched_snaps if s.side == side]
        if frame.is_empty():
            return frame, frame
        if not snaps:
            return frame, frame.head(0)
        accepted_idx: list[int] = []
        rows = frame.to_dicts()
        for i, rec in enumerate(rows):
            key = tuple(str(rec[k]) for k in self.keys)
            for snap in snaps:
                if snap.key == key and self._row_matches(rec, snap.row):
                    accepted_idx.append(i)
                    break
        if not accepted_idx:
            return frame, frame.head(0)
        acc_set = set(accepted_idx)
        pending_mask = [i not in acc_set for i in range(len(rows))]
        # Prefer Polars filter via row index
        idx = pl.DataFrame({"i": list(range(len(rows)))})
        frame_i = frame.with_row_index("_i")
        pending = frame_i.filter(pl.col("_i").is_in([i for i, p in enumerate(pending_mask) if p])).drop("_i")
        accepted = frame_i.filter(pl.col("_i").is_in(accepted_idx)).drop("_i")
        return pending, accepted

    def _split_extras(self) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        current = [("A", n) for n in self.extras_a] + [("B", n) for n in self.extras_b]
        accepted_set = {(s.side, s.name) for s in self.extra_snaps}
        pending = [x for x in current if x not in accepted_set]
        accepted = [x for x in current if x in accepted_set]
        return pending, accepted

    # --- counts ---

    def pending_cells_n(self) -> int:
        return self.pending_cells.height

    def pending_a_only_n(self) -> int:
        return self.pending_a_only.height

    def pending_b_only_n(self) -> int:
        return self.pending_b_only.height

    def pending_extras_n(self) -> int:
        return len(self.pending_extras)

    def pending_total(self) -> int:
        return (
            self.pending_cells_n()
            + self.pending_a_only_n()
            + self.pending_b_only_n()
            + self.pending_extras_n()
        )

    def accepted_total(self) -> int:
        return (
            self.accepted_cells.height
            + self.accepted_a_only.height
            + self.accepted_b_only.height
            + len(self.accepted_extras)
        )

    def equal_count(self, column: str) -> int:
        if column not in self.comparable or self.matched_a.is_empty():
            return 0
        a = self.matched_a[column]
        b = self.matched_b[column]
        return int((a == b).sum())

    def matched_key_count(self) -> int:
        return self.matched_a.height

    # --- roster ---

    def roster(self, name_filter: str = "") -> list[RosterRow]:
        rows: list[RosterRow] = []
        pending_by_col = {}
        accepted_by_col = {}
        if not self.pending_cells.is_empty():
            for rec in self.pending_cells.group_by("column").len().to_dicts():
                pending_by_col[rec["column"]] = rec["len"]
        if not self.accepted_cells.is_empty():
            for rec in self.accepted_cells.group_by("column").len().to_dicts():
                accepted_by_col[rec["column"]] = rec["len"]
        top_pair: dict[str, int] = {}
        if not self.pending_cells.is_empty():
            grouped = (
                self.pending_cells.group_by(["column", "val_a", "val_b"])
                .len()
                .sort(["column", "len"], descending=[False, True])
            )
            for rec in grouped.group_by("column").first().to_dicts():
                top_pair[rec["column"]] = rec["len"]

        for col in self.comparable:
            pend = pending_by_col.get(col, 0)
            acc = accepted_by_col.get(col, 0)
            conc = (top_pair.get(col, 0) / pend) if pend else 0.0
            pct = f"{conc * 100:.0f}%" if pend else "—"
            cat = self._categorical_label(col)
            tags = self._column_tags(col)
            returned = any(c == col for _k, c in self.returned_cells)
            rows.append(
                RosterRow(
                    kind="column",
                    name=col,
                    side="—",
                    pending=pend,
                    concentration=conc,
                    top_pair_pct=pct,
                    accepted=acc,
                    equal=str(self.equal_count(col)),
                    categorical=cat,
                    speculative=tags,
                    returned=returned,
                )
            )
        if self.a_only.height > 0:
            rows.append(
                RosterRow(
                    kind="A-only",
                    name="A-only keys",
                    side="A",
                    pending=self.pending_a_only_n(),
                    concentration=0.0,
                    top_pair_pct="—",
                    accepted=self.accepted_a_only.height,
                    equal="—",
                    categorical="—",
                    speculative=self._unmatched_side_tags("A"),
                    returned=any(s == "A" for s, _k in self.returned_keys),
                )
            )
        if self.b_only.height > 0:
            rows.append(
                RosterRow(
                    kind="B-only",
                    name="B-only keys",
                    side="B",
                    pending=self.pending_b_only_n(),
                    concentration=0.0,
                    top_pair_pct="—",
                    accepted=self.accepted_b_only.height,
                    equal="—",
                    categorical="—",
                    speculative=self._unmatched_side_tags("B"),
                    returned=any(s == "B" for s, _k in self.returned_keys),
                )
            )
        all_extras = [("A", n) for n in self.extras_a] + [("B", n) for n in self.extras_b]
        other_a = list(self.b.headers)
        other_b = list(self.a.headers)
        for side, name in all_extras:
            pend = 1 if (side, name) in self.pending_extras else 0
            acc = 1 if (side, name) in self.accepted_extras else 0
            others = other_a if side == "A" else other_b
            tags = ", ".join(extra_insights(name, others)[:2])
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
                    speculative=tags,
                    returned=(side, name) in self.returned_extras,
                )
            )
        rows.sort(key=lambda r: (-r.pending, -r.concentration, r.name))
        if name_filter:
            needle = name_filter.lower()
            rows = [r for r in rows if needle in r.name.lower()]
        return rows

    def _categorical_label(self, column: str) -> str:
        pending = self.pending_cells.filter(pl.col("column") == column)
        if pending.is_empty():
            return "—"
        a = pending["val_a"].to_list()
        b = pending["val_b"].to_list()
        return "yes" if is_categorical_pending(a, b) else "no"

    def is_categorical(self, column: str) -> bool:
        return self._categorical_label(column) == "yes"

    def _column_tags(self, column: str) -> str:
        pending = self.pending_cells.filter(pl.col("column") == column)
        if pending.is_empty():
            return ""
        tags: list[str] = []
        # sample up to 200 cells for cell-level insights present
        sample = pending.head(200)
        seen: set[str] = set()
        for rec in sample.to_dicts():
            for t in cell_insights(rec["val_a"], rec["val_b"]):
                if t not in seen:
                    seen.add(t)
                    tags.append(t)
            if len(tags) >= 3:
                break
        pat = column_pattern_insight(pending)
        if pat and pat not in tags:
            tags.append(pat)
        return ", ".join(tags[:3])

    def _unmatched_side_tags(self, side: str) -> str:
        pending = self.pending_a_only if side == "A" else self.pending_b_only
        other = self.pending_b_only if side == "A" else self.pending_a_only
        if pending.is_empty() or other.is_empty():
            return ""
        other_keys = [
            tuple(str(rec[k]) for k in self.keys) for rec in other.head(200).to_dicts()
        ]
        for rec in pending.head(50).to_dicts():
            key = tuple(str(rec[k]) for k in self.keys)
            tags = unmatched_key_insights(key, other_keys)
            if tags:
                return tags[0]
        return ""

    # --- pair list ---

    def pair_groups(self, column: str) -> pl.DataFrame:
        pending = self.pending_cells.filter(pl.col("column") == column)
        if pending.is_empty():
            return pl.DataFrame(
                {"val_a": [], "val_b": [], "n": []},
                schema={"val_a": pl.Utf8, "val_b": pl.Utf8, "n": pl.UInt32},
            )
        return (
            pending.group_by(["val_a", "val_b"])
            .len()
            .rename({"len": "n"})
            .sort(["n", "val_a", "val_b"], descending=[True, False, False])
        )

    def pair_page(self, column: str, page: int) -> tuple[list[dict[str, Any]], int, int]:
        groups = self.pair_groups(column)
        total = groups.height
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(0, min(page, pages - 1))
        start = page * PAGE_SIZE
        chunk = groups.slice(start, PAGE_SIZE)
        return chunk.to_dicts(), page, pages

    def pair_matrix(self, column: str) -> tuple[list[str], list[str], list[list[int]]]:
        groups = self.pair_groups(column)
        a_vals = sorted(groups["val_a"].unique().to_list()) if not groups.is_empty() else []
        b_vals = sorted(groups["val_b"].unique().to_list()) if not groups.is_empty() else []
        counts = {(r["val_a"], r["val_b"]): int(r["n"]) for r in groups.to_dicts()}
        matrix = [[counts.get((a, b), 0) for b in b_vals] for a in a_vals]
        return a_vals, b_vals, matrix

    def pair_cells_page(
        self, column: str, val_a: str, val_b: str, page: int, only_keys: set[tuple[str, ...]] | None = None
    ) -> tuple[list[dict[str, Any]], int, int]:
        frame = self.pending_cells.filter(
            (pl.col("column") == column)
            & (pl.col("val_a") == val_a)
            & (pl.col("val_b") == val_b)
        ).sort(self.keys)
        if only_keys is not None:
            keep = []
            for rec in frame.to_dicts():
                key = tuple(str(rec[k]) for k in self.keys)
                if key in only_keys:
                    keep.append(rec)
            # rebuild from keep for paging of checked+unchecked? Grid shows the pair draft set (all originally matching).
        recs = frame.to_dicts()
        if only_keys is not None:
            recs = [r for r in recs if tuple(str(r[k]) for k in self.keys) in only_keys or True]
            # grid shows the draft universe: original pair cells still pending
        total = len(recs)
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(0, min(page, pages - 1))
        chunk = recs[page * PAGE_SIZE : page * PAGE_SIZE + PAGE_SIZE]
        return chunk, page, pages

    def cells_for_tab(self, column: str, tab: str, page: int) -> tuple[list[dict[str, Any]], int, int]:
        if tab == "accepted":
            frame = self.accepted_cells.filter(pl.col("column") == column)
        elif tab == "equal":
            if self.matched_a.is_empty():
                recs: list[dict[str, Any]] = []
            else:
                a = self.matched_a.select(self.keys + [column]).rename({column: "val_a"})
                b = self.matched_b.select(self.keys + [column]).rename({column: "val_b"})
                joined = a.join(b, on=self.keys, how="inner").filter(pl.col("val_a") == pl.col("val_b"))
                recs = joined.sort(self.keys).to_dicts()
                total = len(recs)
                pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
                page = max(0, min(page, pages - 1))
                return recs[page * PAGE_SIZE : page * PAGE_SIZE + PAGE_SIZE], page, pages
            frame = _empty_df(_empty_mismatch_schema(self.keys))
        elif tab == "all_matched":
            if self.matched_a.is_empty():
                recs = []
            else:
                a = self.matched_a.select(self.keys + [column]).rename({column: "val_a"})
                b = self.matched_b.select(self.keys + [column]).rename({column: "val_b"})
                recs = a.join(b, on=self.keys, how="inner").sort(self.keys).to_dicts()
                total = len(recs)
                pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
                page = max(0, min(page, pages - 1))
                return recs[page * PAGE_SIZE : page * PAGE_SIZE + PAGE_SIZE], page, pages
            frame = _empty_df(_empty_mismatch_schema(self.keys))
        else:
            frame = self.pending_cells.filter(pl.col("column") == column)
        recs = frame.sort(self.keys).to_dicts()
        total = len(recs)
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(0, min(page, pages - 1))
        return recs[page * PAGE_SIZE : page * PAGE_SIZE + PAGE_SIZE], page, pages

    def unmatched_page(self, side: str, page: int) -> tuple[list[dict[str, Any]], int, int]:
        pending = self.pending_a_only if side == "A" else self.pending_b_only
        accepted = self.accepted_a_only if side == "A" else self.accepted_b_only
        # show pending then? Spec: order composite key tuple. Full set of unmatched (pending + accepted) currently on that side.
        frame = (self.a_only if side == "A" else self.b_only).sort(self.keys)
        recs = frame.to_dicts()
        total = len(recs)
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(0, min(page, pages - 1))
        chunk = recs[page * PAGE_SIZE : page * PAGE_SIZE + PAGE_SIZE]
        accepted_keys = {
            tuple(str(r[k]) for k in self.keys) for r in accepted.to_dicts()
        }
        for rec in chunk:
            rec["_accepted"] = tuple(str(rec[k]) for k in self.keys) in accepted_keys
            rec["_returned"] = (
                side,
                tuple(str(rec[k]) for k in self.keys),
            ) in self.returned_keys
        return chunk, page, pages

    def extras_rows(self) -> list[dict[str, Any]]:
        rows = []
        all_extras = [("A", n) for n in sorted(self.extras_a)] + [
            ("B", n) for n in sorted(self.extras_b)
        ]
        # Order: exact name (working rule). Spec §18: exact name. Mix sides by name then side.
        all_extras.sort(key=lambda x: (x[1], x[0]))
        others = {"A": list(self.b.headers), "B": list(self.a.headers)}
        for side, name in all_extras:
            pending = (side, name) in self.pending_extras
            rows.append(
                {
                    "side": side,
                    "name": name,
                    "pending": 1 if pending else 0,
                    "accepted": 0 if pending else 1,
                    "speculative": extra_insights(name, others[side]),
                    "returned": (side, name) in self.returned_extras,
                }
            )
        return rows

    def context_values(self, key: tuple[str, ...], column: str) -> list[tuple[str, str, str]]:
        names = [n for n in self.context_columns.get(column, []) if n in self.context_pool and n != column]
        if not names:
            return []
        filt = None
        for kname, kval in zip(self.keys, key):
            expr = pl.col(kname) == kval
            filt = expr if filt is None else (filt & expr)
        out: list[tuple[str, str, str]] = []
        if self.matched_a.is_empty():
            return [(n, "", "") for n in names]
        ra = self.matched_a.filter(filt)
        rb = self.matched_b.filter(filt)
        if ra.is_empty():
            return [(n, "", "") for n in names]
        rec_a = ra.row(0, named=True)
        rec_b = rb.row(0, named=True)
        for n in names:
            out.append((n, str(rec_a.get(n, "")), str(rec_b.get(n, ""))))
        return out

    def cell_insights(self, val_a: str, val_b: str) -> list[str]:
        return cell_insights(val_a, val_b)

    def first_diff(self, a: str, b: str) -> int:
        return first_diff(a, b)

    def key_of(self, rec: dict[str, Any]) -> tuple[str, ...]:
        return tuple(str(rec[k]) for k in self.keys)

    def is_unmatched_pending(self, side: str, key: tuple[str, ...]) -> bool:
        frame = self.pending_a_only if side == "A" else self.pending_b_only
        if frame.is_empty():
            return False
        for rec in frame.to_dicts():
            if self.key_of(rec) == key:
                return True
        return False

    # --- drafts ---

    def draft_in_flight(self) -> bool:
        return bool(self.column_draft) or self.pair_draft_keys is not None

    def column_draft_n(self) -> int:
        return len(self.column_draft)

    def cancel_drafts(self) -> None:
        self.column_draft = set()
        self.pair_draft_keys = None
        self.pair_draft_col = None
        self.pair_draft_va = None
        self.pair_draft_vb = None

    def toggle_column_draft(self, name: str) -> None:
        if not self.column_draft and self.pair_draft_keys is None:
            return
        if self.pair_draft_keys is not None:
            return
        if name not in self.comparable:
            return
        if name in self.column_draft:
            self.column_draft.discard(name)
        else:
            pend = self.pending_cells.filter(pl.col("column") == name).height
            if pend > 0:
                self.column_draft.add(name)

    def start_regex_draft(self, pattern: str) -> int:
        if self.draft_in_flight():
            raise InTuiError("ERROR: confirm or cancel the current draft first")
        if not pattern:
            raise InTuiError("ERROR: empty regex pattern")
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise InTuiError(f"ERROR: invalid regex {pattern!r}: {exc}") from exc
        hits = []
        for col in self.comparable:
            if rx.search(col) is None:
                continue
            n = self.pending_cells.filter(pl.col("column") == col).height
            if n > 0:
                hits.append(col)
        self.column_draft = set(hits)
        return len(hits)

    def start_sentinel_draft(self, side: str, sentinel: str) -> int:
        if self.draft_in_flight():
            raise InTuiError("ERROR: confirm or cancel the current draft first")
        if side not in ("A", "B"):
            raise InTuiError("ERROR: sentinel scan requires Side A or Side B")
        val_col = "val_a" if side == "A" else "val_b"
        pending = self.pending_cells
        if self.comparable:
            pending = pending.filter(pl.col("column").is_in(list(self.comparable)))
        else:
            pending = pending.head(0)
        # group_by omits zero-pending columns, so empty-series .all() cannot draft them
        if pending.is_empty():
            self.column_draft = set()
            return 0
        try:
            hits_df = (
                pending.group_by("column")
                .agg((pl.col(val_col) == pl.lit(sentinel)).all().alias("hit"))
                .filter(pl.col("hit"))
            )
        except Exception as exc:
            raise InTuiError(f"ERROR: sentinel scan error: {exc}") from exc
        hits = hits_df.get_column("column").to_list()
        self.column_draft = set(hits)
        return len(hits)

    def start_pair_draft(self, column: str, val_a: str, val_b: str) -> int:
        if self.column_draft:
            raise InTuiError("ERROR: confirm or cancel the column draft first")
        if self.pair_draft_keys is not None:
            raise InTuiError("ERROR: confirm or cancel the current pair draft first")
        frame = self.pending_cells.filter(
            (pl.col("column") == column)
            & (pl.col("val_a") == val_a)
            & (pl.col("val_b") == val_b)
        )
        if frame.is_empty():
            return 0
        keys = {self.key_of(r) for r in frame.to_dicts()}
        self.pair_draft_keys = set(keys)
        self.pair_draft_col = column
        self.pair_draft_va = val_a
        self.pair_draft_vb = val_b
        self.place.last_pair = (column, val_a, val_b)
        return len(keys)

    def toggle_pair_cell(self, key: tuple[str, ...]) -> None:
        if self.pair_draft_keys is None:
            return
        if key in self.pair_draft_keys:
            self.pair_draft_keys.discard(key)
        else:
            self.pair_draft_keys.add(key)

    # --- accept ---

    def _cell_snap_cols(self) -> list[str]:
        return [*self.keys, "column", "val_a", "val_b"]

    def _vstack_cell_snaps(self, frame: pl.DataFrame) -> int:
        cols = self._cell_snap_cols()
        if frame.is_empty():
            self._apply_snapshots()
            return 0
        frame = frame.select(cols)
        if self.cell_snaps.is_empty():
            new = frame.unique()
        else:
            new = frame.join(self.cell_snaps, on=cols, how="anti")
        n = new.height
        if n:
            self.cell_snaps = (
                new.unique()
                if self.cell_snaps.is_empty()
                else pl.concat([self.cell_snaps, new], how="vertical").unique()
            )
        self._apply_snapshots()
        return n

    def accept_column(self, column: str) -> int:
        n = self._vstack_cell_snaps(self.pending_cells.filter(pl.col("column") == column))
        self.column_draft.discard(column)
        return n

    def accept_pair(self, column: str, val_a: str, val_b: str) -> int:
        frame = self.pending_cells.filter(
            (pl.col("column") == column)
            & (pl.col("val_a") == val_a)
            & (pl.col("val_b") == val_b)
        )
        n = self._vstack_cell_snaps(frame)
        self.place.last_pair = (column, val_a, val_b)
        return n

    def accept_cell(self, key: tuple[str, ...], column: str, val_a: str, val_b: str) -> int:
        data: dict[str, list[str]] = {k: [v] for k, v in zip(self.keys, key)}
        data["column"] = [column]
        data["val_a"] = [val_a]
        data["val_b"] = [val_b]
        n = self._vstack_cell_snaps(pl.DataFrame(data))
        if self.pair_draft_keys is not None:
            self.pair_draft_keys.discard(key)
        return n

    def confirm_column_draft(self) -> int:
        names = [n for n in list(self.column_draft)]
        total = 0
        for name in names:
            total += self.accept_column(name)
        self.column_draft = set()
        return total

    def confirm_pair_draft(self) -> int:
        if self.pair_draft_keys is None or self.pair_draft_col is None:
            return 0
        col, va, vb = self.pair_draft_col, self.pair_draft_va, self.pair_draft_vb
        snaps = []
        frame = self.pending_cells.filter(
            (pl.col("column") == col)
            & (pl.col("val_a") == va)
            & (pl.col("val_b") == vb)
        )
        for rec in frame.to_dicts():
            key = self.key_of(rec)
            if key in self.pair_draft_keys:
                snaps.append(rec)
        if snaps:
            n = self._vstack_cell_snaps(pl.DataFrame(snaps))
        else:
            n = 0
            self._apply_snapshots()
        self.place.last_pair = (col, va, vb)
        self.pair_draft_keys = None
        self.pair_draft_col = None
        self.pair_draft_va = None
        self.pair_draft_vb = None
        return n

    def accept_unmatched(self, side: str, key: tuple[str, ...]) -> int:
        frame = self.pending_a_only if side == "A" else self.pending_b_only
        for rec in frame.to_dicts():
            if self.key_of(rec) == key:
                row = {c: str(rec[c]) for c in rec if not c.startswith("_")}
                snap = UnmatchedSnap(side, key, row)
                if snap not in self.unmatched_snaps:
                    self.unmatched_snaps.append(snap)
                self._apply_snapshots()
                return 1
        return 0

    def accept_all_unmatched(self, side: str) -> int:
        frame = self.pending_a_only if side == "A" else self.pending_b_only
        n = 0
        for rec in frame.to_dicts():
            key = self.key_of(rec)
            row = {c: str(rec[c]) for c in rec if not c.startswith("_")}
            snap = UnmatchedSnap(side, key, row)
            if snap not in self.unmatched_snaps:
                self.unmatched_snaps.append(snap)
                n += 1
        self._apply_snapshots()
        return n

    def accept_extra(self, side: str, name: str) -> int:
        snap = ExtraSnap(side, name)
        if (side, name) not in self.pending_extras:
            return 0
        if snap not in self.extra_snaps:
            self.extra_snaps.append(snap)
        self._apply_snapshots()
        return 1

    # --- undo ---

    def undo_column(self, column: str) -> int:
        before = self.cell_snaps.height
        self.cell_snaps = self.cell_snaps.filter(pl.col("column") != column)
        self._apply_snapshots()
        return before - self.cell_snaps.height

    def undo_cell(self, key: tuple[str, ...], column: str) -> int:
        before = self.cell_snaps.height
        expr = (pl.col("column") == column) & _key_eq_expr(self.keys, key)
        self.cell_snaps = self.cell_snaps.filter(~expr)
        self._apply_snapshots()
        return before - self.cell_snaps.height

    def undo_pair(self, column: str, val_a: str, val_b: str) -> int:
        before = self.cell_snaps.height
        self.cell_snaps = self.cell_snaps.filter(
            ~(
                (pl.col("column") == column)
                & (pl.col("val_a") == val_a)
                & (pl.col("val_b") == val_b)
            )
        )
        self._apply_snapshots()
        return before - self.cell_snaps.height

    def undo_unmatched(self, side: str, key: tuple[str, ...] | None = None) -> int:
        before = len(self.unmatched_snaps)
        if key is None:
            self.unmatched_snaps = [s for s in self.unmatched_snaps if s.side != side]
        else:
            self.unmatched_snaps = [
                s for s in self.unmatched_snaps if not (s.side == side and s.key == key)
            ]
        self._apply_snapshots()
        return before - len(self.unmatched_snaps)

    def undo_extra(self, side: str, name: str) -> int:
        before = len(self.extra_snaps)
        self.extra_snaps = [
            s for s in self.extra_snaps if not (s.side == side and s.name == name)
        ]
        self._apply_snapshots()
        return before - len(self.extra_snaps)

    # --- next lever ---

    def next_lever_place(self, current: Place) -> Place:
        if current.column and current.column in self.comparable:
            groups = self.pair_groups(current.column)
            if not groups.is_empty():
                top = groups.row(0, named=True)
                return replace(
                    current,
                    screen="pair_list",
                    column=current.column,
                    pair_val_a=top["val_a"],
                    pair_val_b=top["val_b"],
                    page=0,
                    view_tab="pending",
                )
        roster = self.roster(current.roster_filter)
        for row in roster:
            if row.pending <= 0:
                continue
            if row.kind == "column":
                groups = self.pair_groups(row.name)
                va = vb = None
                if not groups.is_empty():
                    top = groups.row(0, named=True)
                    va, vb = top["val_a"], top["val_b"]
                return Place(
                    screen="pair_list",
                    column=row.name,
                    pair_val_a=va,
                    pair_val_b=vb,
                    roster_filter=current.roster_filter,
                    last_pair=current.last_pair,
                    view_tab="pending",
                    focused_name=row.name,
                )
            if row.kind == "A-only":
                recs, _, _ = self.unmatched_page("A", 0)
                fk = self.key_of(recs[0]) if recs else None
                return Place(
                    screen="a_only",
                    roster_filter=current.roster_filter,
                    last_pair=current.last_pair,
                    focused_key=fk,
                    focused_name="A-only keys",
                )
            if row.kind == "B-only":
                recs, _, _ = self.unmatched_page("B", 0)
                fk = self.key_of(recs[0]) if recs else None
                return Place(
                    screen="b_only",
                    roster_filter=current.roster_filter,
                    last_pair=current.last_pair,
                    focused_key=fk,
                    focused_name="B-only keys",
                )
            if row.kind == "extra":
                return Place(
                    screen="roster",
                    roster_filter=current.roster_filter,
                    last_pair=current.last_pair,
                    extra_side=row.side,
                    extra_name=row.name,
                    focused_name=row.name,
                )
        return Place(
            screen="roster",
            roster_filter=current.roster_filter,
            last_pair=current.last_pair,
        )

    def next_pending_key_in_grid(self, side: str, current: tuple[str, ...]) -> tuple[str, ...] | None:
        recs, _, _ = self.unmatched_page(side, 0)
        # full pending keys in key order
        pending = self.pending_a_only if side == "A" else self.pending_b_only
        keys = [self.key_of(r) for r in pending.sort(self.keys).to_dicts()]
        if current in keys:
            i = keys.index(current)
            if i + 1 < len(keys):
                return keys[i + 1]
            return keys[0] if keys else None
        return keys[0] if keys else None

    def next_pending_cell_in_pair(self, after: tuple[str, ...]) -> tuple[str, ...] | None:
        if self.pair_draft_keys is None:
            return None
        ordered = sorted(self.pair_draft_keys)
        if after in ordered:
            i = ordered.index(after)
            if i + 1 < len(ordered):
                return ordered[i + 1]
        return ordered[0] if ordered else None

    # --- refresh ---

    def refresh(self) -> RefreshDelta:
        before_pending = self.pending_total()
        before_acc = self.accepted_total()
        prev_accepted = self.cell_snaps
        try:
            new_a = load_side(
                self.a.path,
                self.a.sheet,
                "A",
                delimiter=(
                    None if self.a.detection is None else self.a.detection.delimiter
                ),
                encoding=(
                    None if self.a.detection is None else self.a.detection.encoding
                ),
            )
            new_b = load_side(
                self.b.path,
                self.b.sheet,
                "B",
                delimiter=(
                    None if self.b.detection is None else self.b.detection.delimiter
                ),
                encoding=(
                    None if self.b.detection is None else self.b.detection.encoding
                ),
            )
            tmp = Engine(new_a, new_b, self.keys)
        except HardFail as exc:
            raise InTuiError(f"ERROR: {exc.message}") from exc
        # prune snaps that no longer apply by rebuilding on new data
        tmp.cell_snaps = self.cell_snaps
        tmp.unmatched_snaps = list(self.unmatched_snaps)
        tmp.extra_snaps = list(self.extra_snaps)
        tmp.context_columns = dict(self.context_columns)
        tmp.place = self.place
        tmp._rebuild()
        # returned to pending: previously accepted (and matching then) that are pending now
        returned_cells: set[tuple[tuple[str, ...], str]] = set()
        if not tmp.pending_cells.is_empty() and not prev_accepted.is_empty():
            hit = tmp.pending_cells.join(
                prev_accepted.select([*self.keys, "column"]).unique(),
                on=[*self.keys, "column"],
                how="inner",
            )
            if not hit.is_empty():
                for rec in hit.select([*self.keys, "column"]).unique().to_dicts():
                    returned_cells.add(
                        (tuple(str(rec[k]) for k in self.keys), rec["column"])
                    )
        returned_keys: set[tuple[str, tuple[str, ...]]] = set()
        for side, frame in (("A", tmp.pending_a_only), ("B", tmp.pending_b_only)):
            for rec in frame.to_dicts():
                key = tmp.key_of(rec)
                for s in self.unmatched_snaps:
                    if s.side == side and s.key == key:
                        if not tmp._row_matches(rec, s.row):
                            returned_keys.add((side, key))
                        break
        returned_extras: set[tuple[str, str]] = set()
        # extras don't return except by vanishing/reappearing; spec has no "back to pending" for extras except —
        self.a = tmp.a
        self.b = tmp.b
        self.cell_snaps = tmp.cell_snaps
        self.unmatched_snaps = tmp.unmatched_snaps
        self.extra_snaps = tmp.extra_snaps
        self.context_columns = tmp.context_columns
        self._rebuild()
        # prune drafts
        if self.column_draft:
            keep = set()
            for name in self.column_draft:
                if name in self.comparable:
                    n = self.pending_cells.filter(pl.col("column") == name).height
                    if n > 0:
                        keep.add(name)
            self.column_draft = keep
        if self.pair_draft_keys is not None:
            col, va, vb = self.pair_draft_col, self.pair_draft_va, self.pair_draft_vb
            frame = self.pending_cells.filter(
                (pl.col("column") == col)
                & (pl.col("val_a") == va)
                & (pl.col("val_b") == vb)
            )
            still = {self.key_of(r) for r in frame.to_dicts()}
            self.pair_draft_keys &= still
            if not self.pair_draft_keys:
                self.pair_draft_keys = None
                self.pair_draft_col = None
                self.pair_draft_va = None
                self.pair_draft_vb = None
        self.returned_cells = returned_cells
        self.returned_keys = returned_keys
        self.returned_extras = returned_extras
        returned_n = len(returned_cells) + len(returned_keys) + len(returned_extras)
        delta = RefreshDelta(
            pending_before=before_pending,
            pending_after=self.pending_total(),
            accepted_before=before_acc,
            accepted_after=self.accepted_total(),
            returned=returned_n,
            message=(
                f"pending {before_pending}→{self.pending_total()}, "
                f"accepted {before_acc}→{self.accepted_total()}, "
                f"{returned_n} returned to pending"
            ),
        )
        self.last_refresh_delta = delta
        self.tui_error = None
        return delta

    def prune_place(self) -> None:
        p = self.place
        if p.column and p.column not in self.comparable:
            self.place = Place(screen="roster", roster_filter=p.roster_filter, last_pair=p.last_pair)
            return
        if p.screen in ("pair_list", "cell_step", "accepted", "equal", "all_matched"):
            if not p.column or p.column not in self.comparable:
                self.place = Place(screen="roster", roster_filter=p.roster_filter, last_pair=p.last_pair)
                return
        if p.screen == "cell_step":
            if self.pair_draft_keys is None:
                self.place = replace(p, screen="pair_list")
        if p.screen == "a_only" and self.a_only.is_empty():
            self.place = Place(screen="roster", roster_filter=p.roster_filter, last_pair=p.last_pair)
        if p.screen == "b_only" and self.b_only.is_empty():
            self.place = Place(screen="roster", roster_filter=p.roster_filter, last_pair=p.last_pair)
        if p.screen == "extras":
            if not self.extras_a and not self.extras_b:
                self.place = Place(screen="roster", roster_filter=p.roster_filter, last_pair=p.last_pair)

    # --- session zip ---

    def _snapshots_to_manifest(self) -> dict[str, Any]:
        cells: list[dict[str, Any]] = []
        if not self.cell_snaps.is_empty():
            for rec in self.cell_snaps.to_dicts():
                cells.append(
                    {
                        "key": [rec[k] for k in self.keys],
                        "column": rec["column"],
                        "val_a": rec["val_a"],
                        "val_b": rec["val_b"],
                    }
                )
        return {"cells": cells}

    def to_manifest(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "a_path": abs_path(self.a.path),
            "b_path": abs_path(self.b.path),
            "a_sheet": self.a.sheet,
            "b_sheet": self.b.sheet,
            "keys": list(self.keys),
            "a_detection": None
            if self.a.detection is None
            else {
                "encoding": self.a.detection.encoding,
                "delimiter": self.a.detection.delimiter,
                "delimiter_name": self.a.detection.delimiter_name,
            },
            "b_detection": None
            if self.b.detection is None
            else {
                "encoding": self.b.detection.encoding,
                "delimiter": self.b.detection.delimiter,
                "delimiter_name": self.b.detection.delimiter_name,
            },
            "context_columns": self.context_columns,
            "snapshots": {
                "cells": self._snapshots_to_manifest()["cells"],
                "unmatched": [
                    {"side": s.side, "key": list(s.key), "row": s.row}
                    for s in self.unmatched_snaps
                ],
                "extras": [{"side": s.side, "name": s.name} for s in self.extra_snaps],
            },
            "place": {
                "screen": self.place.screen,
                "column": self.place.column,
                "extra_side": self.place.extra_side,
                "extra_name": self.place.extra_name,
                "view_tab": self.place.view_tab,
                "roster_filter": self.place.roster_filter,
                "last_pair": list(self.place.last_pair) if self.place.last_pair else None,
                "detail_step": "cell_step" if self.place.screen == "cell_step" else "pair_list",
            },
        }

    def export_zip(self, path: str) -> None:
        path = abs_path(path)
        if not path.endswith(".recon.zip"):
            if path.endswith(".zip"):
                path = path[: -4] + ".recon.zip"
            else:
                path = path + ".recon.zip"
            path = abs_path(path)
        manifest = json.dumps(self.to_manifest(), indent=2)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", manifest)

    @classmethod
    def from_session(cls, path: str) -> Engine:
        path = abs_path(path)
        if not Path(path).is_file():
            raise HardFail(f"Missing path: {path}")
        try:
            with zipfile.ZipFile(path) as zf:
                raw = zf.read("manifest.json")
        except KeyError as exc:
            raise HardFail(f"Session zip {path} has no manifest.json") from exc
        except zipfile.BadZipFile as exc:
            raise HardFail(f"Not a valid .recon.zip: {path}") from exc
        try:
            man = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise HardFail(f"Invalid manifest.json in {path}") from exc
        keys = list(man.get("keys") or [])
        if not keys:
            raise HardFail(f"Session {path} has no keys")
        a_det = man.get("a_detection") or {}
        b_det = man.get("b_detection") or {}
        a_delim = a_det.get("delimiter") if a_det else None
        b_delim = b_det.get("delimiter") if b_det else None
        a_encoding = a_det.get("encoding") if a_det else None
        b_encoding = b_det.get("encoding") if b_det else None
        eng = cls.from_paths(
            man["a_path"],
            man["b_path"],
            keys,
            man.get("a_sheet"),
            man.get("b_sheet"),
            a_delim=a_delim,
            b_delim=b_delim,
            a_encoding=a_encoding,
            b_encoding=b_encoding,
        )
        snaps = man.get("snapshots") or {}
        eng.cell_snaps = _cell_snaps_from_json(keys, snaps.get("cells") or [])
        eng.unmatched_snaps = [
            UnmatchedSnap(u["side"], tuple(u["key"]), dict(u.get("row") or {}))
            for u in snaps.get("unmatched") or []
        ]
        eng.extra_snaps = [
            ExtraSnap(e["side"], e["name"]) for e in snaps.get("extras") or []
        ]
        eng.context_columns = {
            k: list(v) for k, v in (man.get("context_columns") or {}).items()
        }
        eng._rebuild()
        place = man.get("place") or {}
        last = place.get("last_pair")
        last_t = tuple(last) if last and len(last) == 3 else None
        screen = place.get("screen") or "roster"
        if place.get("detail_step") == "cell_step" and screen in ("pair_list", "cell_step"):
            screen = "pair_list"  # drafts are not persisted
        eng.place = Place(
            screen=screen if screen != "cell_step" else "pair_list",
            column=place.get("column"),
            extra_side=place.get("extra_side"),
            extra_name=place.get("extra_name"),
            view_tab=place.get("view_tab") or "pending",
            roster_filter=place.get("roster_filter") or "",
            last_pair=last_t,
        )
        eng.prune_place()
        return eng

    def identity_lines(self) -> list[str]:
        a_det = (
            f"encoding {self.a.detection.encoding}, delimiter {self.a.detection.delimiter_name}"
            if self.a.detection
            else "Excel"
        )
        b_det = (
            f"encoding {self.b.detection.encoding}, delimiter {self.b.detection.delimiter_name}"
            if self.b.detection
            else "Excel"
        )
        a_sheet = f" sheet {self.a.sheet!r}" if self.a.sheet else ""
        b_sheet = f" sheet {self.b.sheet!r}" if self.b.sheet else ""
        return [
            f"A: {self.a.path}{a_sheet} ({a_det})",
            f"B: {self.b.path}{b_sheet} ({b_det})",
            f"keys: {', '.join(self.keys)}",
        ]
