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
    extra_insights,
    first_diff,
)
from reconcile.load import SideTable, load_side

PAGE_SIZE = 100
SCHEMA_VERSION = 1


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


def _unmatched_snaps_from_json(
    keys: list[str],
    items: list[dict[str, Any]],
    side: str,
    template: pl.DataFrame,
) -> pl.DataFrame:
    rows = [u for u in items if u.get("side") == side]
    if not rows:
        return template.head(0)
    recs: list[dict[str, str]] = []
    for u in rows:
        rec = {c: "" for c in template.columns}
        rec.update({str(k): str(v) for k, v in dict(u.get("row") or {}).items()})
        key = list(u.get("key") or [])
        for i, kname in enumerate(keys):
            if i < len(key) and kname in rec:
                rec[kname] = str(key[i])
        recs.append(rec)
    data = {c: [r.get(c, "") for r in recs] for c in template.columns}
    return pl.DataFrame(data)


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
        self.unmatched_snaps_a: pl.DataFrame | None = None
        self.unmatched_snaps_b: pl.DataFrame | None = None
        self.extra_snaps: list[ExtraSnap] = []
        self.context_columns: dict[str, list[str]] = {}
        self.place = Place()
        self.column_draft: set[str] = set()
        self.pair_draft_col: str | None = None
        self.pair_draft_va: str | None = None
        self.pair_draft_vb: str | None = None
        self.returned_cells_df: pl.DataFrame = _empty_df(
            {**{k: pl.Utf8 for k in keys}, "column": pl.Utf8}
        )
        self.returned_keys_df: pl.DataFrame = _empty_df(
            {"side": pl.Utf8, **{k: pl.Utf8 for k in keys}}
        )
        self.returned_extras: set[tuple[str, str]] = set()
        self.last_refresh_delta: RefreshDelta | None = None
        self.tui_error: str | None = None
        self._roster_cache: list[RosterRow] = []
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
        if self.matched_a.is_empty() or not self.comparable:
            self.mismatches = _empty_df(schema)
        else:
            a_long = self.matched_a.select(self.keys + self.comparable).unpivot(
                index=self.keys, on=self.comparable, variable_name="column", value_name="val_a"
            )
            b_long = self.matched_b.select(self.keys + self.comparable).unpivot(
                index=self.keys, on=self.comparable, variable_name="column", value_name="val_b"
            )
            self.mismatches = a_long.join(b_long, on=[*self.keys, "column"], how="inner").filter(
                pl.col("val_a") != pl.col("val_b")
            )
        self._apply_snapshots()
        self._sort_unmatched()

    def _ensure_unmatched_snap_frames(self) -> None:
        if self.unmatched_snaps_a is None or (
            self.unmatched_snaps_a.is_empty()
            and list(self.unmatched_snaps_a.columns) != list(self.a_only.columns)
        ):
            self.unmatched_snaps_a = self.a_only.head(0)
        if self.unmatched_snaps_b is None or (
            self.unmatched_snaps_b.is_empty()
            and list(self.unmatched_snaps_b.columns) != list(self.b_only.columns)
        ):
            self.unmatched_snaps_b = self.b_only.head(0)

    def _sort_unmatched(self) -> None:
        if not self.a_only.is_empty():
            self.a_only = self.a_only.sort(self.keys)
        if not self.b_only.is_empty():
            self.b_only = self.b_only.sort(self.keys)

    def _apply_snapshots(self) -> None:
        self._ensure_unmatched_snap_frames()
        self.pending_cells = self._pending_cells()
        self.accepted_cells = self._accepted_cells_current()
        self.pending_a_only, self.accepted_a_only = self._split_unmatched("A")
        self.pending_b_only, self.accepted_b_only = self._split_unmatched("B")
        self.pending_extras, self.accepted_extras = self._split_extras()
        self._roster_cache = self._build_roster_cache()

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

    def _split_unmatched(self, side: str) -> tuple[pl.DataFrame, pl.DataFrame]:
        frame = self.a_only if side == "A" else self.b_only
        snaps = self.unmatched_snaps_a if side == "A" else self.unmatched_snaps_b
        if frame.is_empty():
            return frame, frame
        if snaps is None or snaps.is_empty():
            return frame, frame.head(0)
        if set(snaps.columns) != set(frame.columns):
            # Schema drift: full-row identity cannot match (not a key-only ignore).
            return frame, frame.head(0)
        snaps = snaps.select(list(frame.columns))
        cols = list(frame.columns)
        pending = frame.join(snaps, on=cols, how="anti")
        accepted = frame.join(snaps, on=cols, how="inner")
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
        if column not in self.comparable:
            return 0
        mismatch_n = 0
        if not self.mismatches.is_empty():
            mismatch_n = self.mismatches.filter(pl.col("column") == column).height
        return self.matched_a.height - mismatch_n

    def matched_key_count(self) -> int:
        return self.matched_a.height

    # --- roster ---

    def roster(self, name_filter: str = "") -> list[RosterRow]:
        rows = list(self._roster_cache)
        if name_filter:
            needle = name_filter.lower()
            rows = [r for r in rows if needle in r.name.lower()]
        return rows

    def _roster_agg_maps(self) -> tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, dict[str, Any]]]:
        pending_by_col: dict[str, int] = {}
        accepted_by_col: dict[str, int] = {}
        top_pair: dict[str, int] = {}
        col_stats: dict[str, dict[str, Any]] = {}
        if not self.pending_cells.is_empty():
            for rec in self.pending_cells.group_by("column").len().to_dicts():
                pending_by_col[rec["column"]] = rec["len"]
            grouped = (
                self.pending_cells.group_by(["column", "val_a", "val_b"])
                .len()
                .sort(["column", "len"], descending=[False, True])
            )
            for rec in grouped.group_by("column").first().to_dicts():
                top_pair[rec["column"]] = rec["len"]
            stats = self.pending_cells.group_by("column").agg(
                (pl.col("val_a").str.strip_chars() == pl.col("val_b").str.strip_chars()).any().alias("trim"),
                (pl.col("val_a").str.to_lowercase() == pl.col("val_b").str.to_lowercase()).any().alias("case"),
                (
                    pl.col("val_a").str.strip_chars().str.to_lowercase()
                    == pl.col("val_b").str.strip_chars().str.to_lowercase()
                ).any().alias("both"),
                (
                    pl.col("val_a").cast(pl.Float64, strict=False).is_not_null()
                    & pl.col("val_b").cast(pl.Float64, strict=False).is_not_null()
                    & (pl.col("val_a").str.strip_chars() != "")
                    & (pl.col("val_b").str.strip_chars() != "")
                    & (
                        pl.col("val_a").cast(pl.Float64, strict=False)
                        == pl.col("val_b").cast(pl.Float64, strict=False)
                    )
                ).any().alias("numeric"),
                (
                    pl.col("val_a").str.contains(r"[\u00a0\t\r\n]")
                    | pl.col("val_b").str.contains(r"[\u00a0\t\r\n]")
                    | (pl.col("val_a") != pl.col("val_a").str.strip_chars())
                    | (pl.col("val_b") != pl.col("val_b").str.strip_chars())
                ).any().alias("ws"),
                pl.col("val_a").n_unique().alias("n_a"),
                pl.col("val_b").n_unique().alias("n_b"),
                pl.col("val_a").unique().sort().alias("ua"),
                pl.col("val_b").unique().sort().alias("ub"),
            ).with_columns(
                pl.col("ua").list.concat(pl.col("ub")).list.unique().list.len().alias("n_ab"),
                (pl.col("ua") != pl.col("ub")).alias("pattern"),
            ).drop("ua", "ub")
            for rec in stats.to_dicts():
                col_stats[rec["column"]] = rec
        if not self.accepted_cells.is_empty():
            for rec in self.accepted_cells.group_by("column").len().to_dicts():
                accepted_by_col[rec["column"]] = rec["len"]
        return pending_by_col, accepted_by_col, top_pair, col_stats

    def _build_roster_cache(self) -> list[RosterRow]:
        pending_by_col, accepted_by_col, top_pair, col_stats = self._roster_agg_maps()
        rows: list[RosterRow] = []
        matched_n = self.matched_a.height
        mismatch_n: dict[str, int] = {}
        if not self.mismatches.is_empty():
            for rec in self.mismatches.group_by("column").len().to_dicts():
                mismatch_n[rec["column"]] = rec["len"]
        for col in self.comparable:
            pend = pending_by_col.get(col, 0)
            acc = accepted_by_col.get(col, 0)
            conc = (top_pair.get(col, 0) / pend) if pend else 0.0
            pct = f"{conc * 100:.0f}%" if pend else "—"
            st = col_stats.get(col)
            if not pend or st is None:
                cat = "—"
                tags = ""
            else:
                n_a, n_b, n_ab = int(st["n_a"]), int(st["n_b"]), int(st["n_ab"])
                cat = "yes" if n_a <= 30 and n_b <= 30 and n_ab <= 50 else "no"
                tag_bits: list[str] = []
                if st["trim"]:
                    tag_bits.append("speculative: equal if trim")
                if st["case"]:
                    tag_bits.append("speculative: equal if case-fold")
                if st["both"] and not st["trim"] and not st["case"]:
                    tag_bits.append("speculative: equal if trim+case")
                if st["numeric"]:
                    tag_bits.append("speculative: equal as numbers")
                if st["ws"]:
                    tag_bits.append("speculative: invisible/odd whitespace")
                if st["pattern"] and n_a <= 6 and n_b <= 6:
                    tag_bits.append("speculative: shared value pattern")
                tags = ", ".join(tag_bits[:3])
            equal = str(matched_n - mismatch_n.get(col, 0))
            rows.append(
                RosterRow(
                    kind="column",
                    name=col,
                    side="—",
                    pending=pend,
                    concentration=conc,
                    top_pair_pct=pct,
                    accepted=acc,
                    equal=equal,
                    categorical=cat,
                    speculative=tags,
                    returned=self.column_has_returned(col),
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
                    returned=self.side_has_returned("A"),
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
                    returned=self.side_has_returned("B"),
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
        return rows

    def is_categorical(self, column: str) -> bool:
        for row in self._roster_cache:
            if row.kind == "column" and row.name == column:
                return row.categorical == "yes"
        return False

    def _unmatched_side_tags(self, side: str) -> str:
        pending = self.pending_a_only if side == "A" else self.pending_b_only
        other = self.pending_b_only if side == "A" else self.pending_a_only
        if pending.is_empty() or other.is_empty():
            return ""
        trim_cols = [pl.col(k).str.strip_chars().alias(k) for k in self.keys]
        if not pending.select(trim_cols).join(other.select(trim_cols), on=self.keys, how="inner").is_empty():
            return "speculative: would match if trim"
        case_cols = [pl.col(k).str.to_lowercase().alias(k) for k in self.keys]
        if not pending.select(case_cols).join(other.select(case_cols), on=self.keys, how="inner").is_empty():
            return "speculative: would match if case-fold"
        fold_cols = [pl.col(k).str.strip_chars().str.to_lowercase().alias(k) for k in self.keys]
        if not pending.select(fold_cols).join(other.select(fold_cols), on=self.keys, how="inner").is_empty():
            return "speculative: would match if trim/case"
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
        return _page(self.pair_groups(column), page)

    def pair_matrix(self, column: str) -> tuple[list[str], list[str], list[list[int]]]:
        groups = self.pair_groups(column)
        a_vals = sorted(groups["val_a"].unique().to_list()) if not groups.is_empty() else []
        b_vals = sorted(groups["val_b"].unique().to_list()) if not groups.is_empty() else []
        counts = {(r["val_a"], r["val_b"]): int(r["n"]) for r in groups.to_dicts()}
        matrix = [[counts.get((a, b), 0) for b in b_vals] for a in a_vals]
        return a_vals, b_vals, matrix

    def pair_cells_page(
        self, column: str, val_a: str, val_b: str, page: int
    ) -> tuple[list[dict[str, Any]], int, int]:
        frame = self.pending_cells.filter(
            (pl.col("column") == column)
            & (pl.col("val_a") == val_a)
            & (pl.col("val_b") == val_b)
        ).sort(self.keys)
        return _page(frame, page)

    def cells_for_tab(self, column: str, tab: str, page: int) -> tuple[list[dict[str, Any]], int, int]:
        empty = _empty_df(_empty_mismatch_schema(self.keys))
        if tab == "accepted":
            frame = self.accepted_cells.filter(pl.col("column") == column)
        elif tab == "equal":
            if self.matched_a.is_empty():
                frame = empty
            else:
                a = self.matched_a.select(self.keys + [column]).rename({column: "val_a"})
                b = self.matched_b.select(self.keys + [column]).rename({column: "val_b"})
                frame = a.join(b, on=self.keys, how="inner").filter(pl.col("val_a") == pl.col("val_b"))
        elif tab == "all_matched":
            if self.matched_a.is_empty():
                frame = empty
            else:
                a = self.matched_a.select(self.keys + [column]).rename({column: "val_a"})
                b = self.matched_b.select(self.keys + [column]).rename({column: "val_b"})
                frame = a.join(b, on=self.keys, how="inner")
        else:
            frame = self.pending_cells.filter(pl.col("column") == column)
        return _page(frame.sort(self.keys), page)

    def unmatched_page(self, side: str, page: int) -> tuple[list[dict[str, Any]], int, int]:
        accepted = self.accepted_a_only if side == "A" else self.accepted_b_only
        frame = (self.a_only if side == "A" else self.b_only).sort(self.keys)
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
                accepted.select(self.keys).unique().with_columns(pl.lit(True).alias("_accepted")),
                on=self.keys,
                how="left",
            ).with_columns(pl.col("_accepted").fill_null(False))
        ret = self.returned_keys_df
        if ret.is_empty():
            chunk = chunk.with_columns(pl.lit(False).alias("_returned"))
        else:
            ret_keys = (
                ret.filter(pl.col("side") == side)
                .select(self.keys)
                .unique()
                .with_columns(pl.lit(True).alias("_returned"))
            )
            chunk = chunk.join(ret_keys, on=self.keys, how="left").with_columns(
                pl.col("_returned").fill_null(False)
            )
        return _page_dicts(chunk), page, pages

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

    def column_has_returned(self, column: str) -> bool:
        if self.returned_cells_df.is_empty():
            return False
        return self.returned_cells_df.filter(pl.col("column") == column).height > 0

    def cell_is_returned(self, key: tuple[str, ...], column: str) -> bool:
        if self.returned_cells_df.is_empty():
            return False
        return (
            self.returned_cells_df.filter(
                (pl.col("column") == column) & _key_eq_expr(self.keys, key)
            ).height
            > 0
        )

    def side_has_returned(self, side: str) -> bool:
        if self.returned_keys_df.is_empty():
            return False
        return self.returned_keys_df.filter(pl.col("side") == side).height > 0

    def key_is_returned(self, side: str, key: tuple[str, ...]) -> bool:
        if self.returned_keys_df.is_empty():
            return False
        return (
            self.returned_keys_df.filter(
                (pl.col("side") == side) & _key_eq_expr(self.keys, key)
            ).height
            > 0
        )

    def key_of(self, rec: dict[str, Any]) -> tuple[str, ...]:
        return tuple(str(rec[k]) for k in self.keys)

    # --- drafts ---

    def draft_in_flight(self) -> bool:
        return bool(self.column_draft) or self.pair_draft_col is not None

    def column_draft_n(self) -> int:
        return len(self.column_draft)

    def pair_draft_height(self) -> int:
        if self.pair_draft_col is None:
            return 0
        return self.pending_cells.filter(
            (pl.col("column") == self.pair_draft_col)
            & (pl.col("val_a") == self.pair_draft_va)
            & (pl.col("val_b") == self.pair_draft_vb)
        ).height

    def cancel_drafts(self) -> None:
        self.column_draft = set()
        self.clear_pair_draft()

    def clear_pair_draft(self) -> None:
        self.pair_draft_col = None
        self.pair_draft_va = None
        self.pair_draft_vb = None

    def toggle_column_draft(self, name: str) -> None:
        if not self.column_draft and self.pair_draft_col is None:
            return
        if self.pair_draft_col is not None:
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
        if self.pair_draft_col is not None:
            raise InTuiError("ERROR: confirm or cancel the current pair draft first")
        n = self.pending_cells.filter(
            (pl.col("column") == column)
            & (pl.col("val_a") == val_a)
            & (pl.col("val_b") == val_b)
        ).height
        if n == 0:
            return 0
        self.pair_draft_col = column
        self.pair_draft_va = val_a
        self.pair_draft_vb = val_b
        self.place.last_pair = (column, val_a, val_b)
        return n

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
        return n

    def confirm_column_draft(self) -> int:
        names = [n for n in list(self.column_draft)]
        total = 0
        for name in names:
            total += self.accept_column(name)
        self.column_draft = set()
        return total

    def confirm_pair_draft(self, unchecked: set[tuple[str, ...]] | None = None) -> int:
        if self.pair_draft_col is None:
            return 0
        col, va, vb = self.pair_draft_col, self.pair_draft_va, self.pair_draft_vb
        frame = self.pending_cells.filter(
            (pl.col("column") == col)
            & (pl.col("val_a") == va)
            & (pl.col("val_b") == vb)
        )
        if unchecked:
            exc = pl.DataFrame(
                {k: [key[i] for key in unchecked] for i, k in enumerate(self.keys)}
            )
            frame = frame.join(exc, on=self.keys, how="anti")
        n = self._vstack_cell_snaps(frame)
        self.place.last_pair = (col, va, vb)
        self.pair_draft_col = None
        self.pair_draft_va = None
        self.pair_draft_vb = None
        return n

    def _unmatched_attr(self, side: str) -> str:
        return "unmatched_snaps_a" if side == "A" else "unmatched_snaps_b"

    def _vstack_unmatched(self, side: str, frame: pl.DataFrame) -> int:
        attr = self._unmatched_attr(side)
        snaps: pl.DataFrame | None = getattr(self, attr)
        if frame.is_empty():
            self._apply_snapshots()
            return 0
        if snaps is None or snaps.is_empty():
            setattr(self, attr, frame.unique())
            n = frame.height
        elif set(snaps.columns) == set(frame.columns):
            snaps = snaps.select(list(frame.columns))
            new = frame.join(snaps, on=list(frame.columns), how="anti")
            n = new.height
            if n:
                setattr(self, attr, pl.concat([snaps, new], how="vertical").unique())
        else:
            n = frame.height
            setattr(self, attr, pl.concat([snaps, frame], how="diagonal").unique())
        self._apply_snapshots()
        return n

    def accept_unmatched(self, side: str, key: tuple[str, ...]) -> int:
        pending = self.pending_a_only if side == "A" else self.pending_b_only
        row = pending.filter(_key_eq_expr(self.keys, key))
        if row.is_empty():
            return 0
        return 1 if self._vstack_unmatched(side, row) else 0

    def accept_all_unmatched(self, side: str) -> int:
        pending = self.pending_a_only if side == "A" else self.pending_b_only
        return self._vstack_unmatched(side, pending)

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
        attr = self._unmatched_attr(side)
        snaps: pl.DataFrame | None = getattr(self, attr)
        if snaps is None or snaps.is_empty():
            return 0
        before = snaps.height
        if key is None:
            setattr(self, attr, snaps.head(0))
        else:
            setattr(self, attr, snaps.filter(~_key_eq_expr(self.keys, key)))
        self._apply_snapshots()
        return before - getattr(self, attr).height

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
                fk = self._first_pending_key("A")
                return Place(
                    screen="a_only",
                    roster_filter=current.roster_filter,
                    last_pair=current.last_pair,
                    focused_key=fk,
                    focused_name="A-only keys",
                )
            if row.kind == "B-only":
                fk = self._first_pending_key("B")
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

    def _first_pending_key(self, side: str) -> tuple[str, ...] | None:
        pending = self.pending_a_only if side == "A" else self.pending_b_only
        if pending.is_empty():
            return None
        rec = pending.sort(self.keys).head(1).row(0, named=True)
        return tuple(str(rec[k]) for k in self.keys)

    def next_pending_key_in_grid(self, side: str, current: tuple[str, ...]) -> tuple[str, ...] | None:
        pending = self.pending_a_only if side == "A" else self.pending_b_only
        if pending.is_empty():
            return None
        pending = pending.sort(self.keys)
        parts: list[pl.Expr] = []
        acc: pl.Expr = pl.lit(True)
        for i, k in enumerate(self.keys):
            parts.append(acc & (pl.col(k) > current[i]))
            acc = acc & (pl.col(k) == current[i])
        nxt = pending.filter(pl.any_horizontal(parts)).head(1)
        if nxt.is_empty():
            nxt = pending.head(1)
        rec = nxt.row(0, named=True)
        return tuple(str(rec[k]) for k in self.keys)

    def next_pending_cell_in_pair(self, after: tuple[str, ...]) -> tuple[str, ...] | None:
        if self.pair_draft_col is None:
            return None
        frame = self.pending_cells.filter(
            (pl.col("column") == self.pair_draft_col)
            & (pl.col("val_a") == self.pair_draft_va)
            & (pl.col("val_b") == self.pair_draft_vb)
        )
        if frame.is_empty():
            return None
        frame = frame.sort(self.keys)
        parts: list[pl.Expr] = []
        acc: pl.Expr = pl.lit(True)
        for i, k in enumerate(self.keys):
            parts.append(acc & (pl.col(k) > after[i]))
            acc = acc & (pl.col(k) == after[i])
        nxt = frame.filter(pl.any_horizontal(parts)).head(1)
        if nxt.is_empty():
            nxt = frame.head(1)
        rec = nxt.row(0, named=True)
        return tuple(str(rec[k]) for k in self.keys)

    # --- refresh ---

    def _reload_side(self, table: SideTable, side: str) -> SideTable:
        return load_side(
            table.path,
            table.sheet,
            side,
            delimiter=(None if table.detection is None else table.detection.delimiter),
            encoding=(None if table.detection is None else table.detection.encoding),
        )

    def refresh(self) -> RefreshDelta:
        before_pending = self.pending_total()
        before_acc = self.accepted_total()
        prev_cell_snaps = self.cell_snaps
        prev_unmatched_a = self.unmatched_snaps_a
        prev_unmatched_b = self.unmatched_snaps_b
        try:
            new_a = self._reload_side(self.a, "A")
            new_b = self._reload_side(self.b, "B")
        except HardFail as exc:
            raise InTuiError(f"ERROR: {exc.message}") from exc
        old_a, old_b = self.a, self.b
        self.a, self.b = new_a, new_b
        try:
            self._rebuild()
        except HardFail as exc:
            self.a, self.b = old_a, old_b
            self._rebuild()
            raise InTuiError(f"ERROR: {exc.message}") from exc
        returned_cells = self.pending_cells.head(0).select([*self.keys, "column"])
        if not self.pending_cells.is_empty() and not prev_cell_snaps.is_empty():
            returned_cells = (
                self.pending_cells.join(
                    prev_cell_snaps.select([*self.keys, "column"]).unique(),
                    on=[*self.keys, "column"],
                    how="inner",
                )
                .select([*self.keys, "column"])
                .unique()
            )
        parts: list[pl.DataFrame] = []
        for side, pending, prev_snaps in (
            ("A", self.pending_a_only, prev_unmatched_a),
            ("B", self.pending_b_only, prev_unmatched_b),
        ):
            if pending.is_empty() or prev_snaps is None or prev_snaps.is_empty():
                continue
            if not all(k in prev_snaps.columns for k in self.keys):
                continue
            hit = pending.join(
                prev_snaps.select(self.keys).unique(), on=self.keys, how="inner"
            )
            if hit.is_empty():
                continue
            parts.append(
                hit.select(self.keys)
                .unique()
                .with_columns(pl.lit(side).alias("side"))
            )
        if parts:
            returned_keys = pl.concat(parts, how="vertical").select(
                ["side", *self.keys]
            )
        else:
            returned_keys = _empty_df({"side": pl.Utf8, **{k: pl.Utf8 for k in self.keys}})
        self.returned_cells_df = returned_cells
        self.returned_keys_df = returned_keys
        self.returned_extras = set()
        if self.column_draft:
            keep = set()
            for name in self.column_draft:
                if name in self.comparable:
                    n = self.pending_cells.filter(pl.col("column") == name).height
                    if n > 0:
                        keep.add(name)
            self.column_draft = keep
        if self.pair_draft_col is not None:
            col, va, vb = self.pair_draft_col, self.pair_draft_va, self.pair_draft_vb
            n = self.pending_cells.filter(
                (pl.col("column") == col)
                & (pl.col("val_a") == va)
                & (pl.col("val_b") == vb)
            ).height
            if n == 0:
                self.pair_draft_col = None
                self.pair_draft_va = None
                self.pair_draft_vb = None
        returned_n = returned_cells.height + returned_keys.height + len(self.returned_extras)
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
            if self.pair_draft_col is None:
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
        unmatched: list[dict[str, Any]] = []
        for side, snaps in (("A", self.unmatched_snaps_a), ("B", self.unmatched_snaps_b)):
            if snaps is None or snaps.is_empty():
                continue
            for rec in snaps.to_dicts():
                unmatched.append(
                    {
                        "side": side,
                        "key": [rec[k] for k in self.keys],
                        "row": {c: str(rec[c]) for c in rec},
                    }
                )
        return {"cells": cells, "unmatched": unmatched}

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
                **self._snapshots_to_manifest(),
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
        um = snaps.get("unmatched") or []
        eng.unmatched_snaps_a = _unmatched_snaps_from_json(keys, um, "A", eng.a_only)
        eng.unmatched_snaps_b = _unmatched_snaps_from_json(keys, um, "B", eng.b_only)
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
