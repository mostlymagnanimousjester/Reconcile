"""Engine facade: session zip, refresh, load wiring, and re-exports."""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

import polars as pl

from reconcile.delimited import abs_path
from reconcile.errors import HardFail
from reconcile.insights import (
    cell_insights,
    first_diff,
)
from reconcile.load import SideTable, load_side

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


from reconcile import compare as compare_mod  # noqa: E402
from reconcile import pages as pages_mod  # noqa: E402
from reconcile import roster as roster_mod  # noqa: E402
from reconcile import snaps as snaps_mod  # noqa: E402
from reconcile.compare import _empty_df  # noqa: E402
from reconcile.snaps import (  # noqa: E402
    _cell_snaps_from_json,
    _empty_cell_snaps,
    _unmatched_snaps_from_json,
)

PAGE_SIZE = pages_mod.PAGE_SIZE
_page = pages_mod._page
_page_dicts = pages_mod._page_dicts


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
        self.last_grain: tuple[Any, ...] | None = None
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
        compare_mod.rebuild_frames(self)
        compare_mod.sort_unmatched(self)
        self._apply_snapshots()

    def _apply_snapshots(self) -> None:
        snaps_mod.apply_snapshots(self)

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
        return compare_mod.equal_count(self, column)

    def matched_key_count(self) -> int:
        return self.matched_a.height

    # --- roster ---

    def roster(self, name_filter: str = "") -> list[RosterRow]:
        return roster_mod.roster(self, name_filter)

    def _roster_agg_maps(self) -> tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, dict[str, Any]]]:
        return roster_mod._roster_agg_maps(self)

    def _build_roster_cache(self) -> list[RosterRow]:
        return roster_mod._build_roster_cache(self)

    def is_categorical(self, column: str) -> bool:
        return roster_mod.is_categorical(self, column)

    def _unmatched_side_tags(self, side: str) -> str:
        return roster_mod._unmatched_side_tags(self, side)

    # --- pair list ---

    def pair_groups(self, column: str) -> pl.DataFrame:
        return compare_mod.pair_groups(self, column)

    def pair_page(self, column: str, page: int) -> tuple[list[dict[str, Any]], int, int]:
        return pages_mod.pair_page(self, column, page)

    def page_index_for_pair(self, column: str, val_a: str, val_b: str) -> tuple[int, int]:
        return pages_mod.page_index_for_pair(self, column, val_a, val_b)

    def place_from_last_pair(
        self, last_pair: tuple[str, str, str] | None, roster_filter: str = ""
    ) -> Place | None:
        return roster_mod.place_from_last_pair(self, last_pair, roster_filter)

    def pair_cells_page(
        self, column: str, val_a: str, val_b: str, page: int
    ) -> tuple[list[dict[str, Any]], int, int]:
        return pages_mod.pair_cells_page(self, column, val_a, val_b, page)

    def cells_for_tab(self, column: str, tab: str, page: int) -> tuple[list[dict[str, Any]], int, int]:
        return pages_mod.cells_for_tab(self, column, tab, page)

    def unmatched_page(self, side: str, page: int) -> tuple[list[dict[str, Any]], int, int]:
        return pages_mod.unmatched_page(self, side, page)

    def extras_rows(self) -> list[dict[str, Any]]:
        return pages_mod.extras_rows(self)

    def context_values(self, key: tuple[str, ...], column: str) -> list[tuple[str, str, str]]:
        return pages_mod.context_values(self, key, column)

    def cell_insights(self, val_a: str, val_b: str) -> list[str]:
        return cell_insights(val_a, val_b)

    def first_diff(self, a: str, b: str) -> int:
        return first_diff(a, b)

    def column_has_returned(self, column: str) -> bool:
        return snaps_mod.column_has_returned(self, column)

    def cell_is_returned(self, key: tuple[str, ...], column: str) -> bool:
        return snaps_mod.cell_is_returned(self, key, column)

    def pair_has_returned(self, column: str, val_a: str, val_b: str) -> bool:
        return snaps_mod.pair_has_returned(self, column, val_a, val_b)

    def side_has_returned(self, side: str) -> bool:
        return snaps_mod.side_has_returned(self, side)

    def key_is_returned(self, side: str, key: tuple[str, ...]) -> bool:
        return snaps_mod.key_is_returned(self, side, key)

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

    def pair_draft_key_frame(self) -> pl.DataFrame:
        schema = {k: pl.Utf8 for k in self.keys}
        if self.pair_draft_col is None:
            return _empty_df(schema)
        return self.pending_cells.filter(
            (pl.col("column") == self.pair_draft_col)
            & (pl.col("val_a") == self.pair_draft_va)
            & (pl.col("val_b") == self.pair_draft_vb)
        ).select(self.keys)

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
        if not hits:
            raise InTuiError("ERROR: regex matched 0 pending columns")
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
            raise InTuiError("ERROR: sentinel matched 0 pending columns")
        try:
            hits_df = (
                pending.group_by("column")
                .agg((pl.col(val_col) == pl.lit(sentinel)).all().alias("hit"))
                .filter(pl.col("hit"))
            )
        except Exception as exc:
            raise InTuiError(f"ERROR: sentinel scan error: {exc}") from exc
        hits = hits_df.get_column("column").to_list()
        if not hits:
            raise InTuiError("ERROR: sentinel matched 0 pending columns")
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
        return n

    # --- accept ---

    def accept_column(self, column: str) -> int:
        return snaps_mod.accept_column(self, column)

    def accept_pair(self, column: str, val_a: str, val_b: str) -> int:
        return snaps_mod.accept_pair(self, column, val_a, val_b)

    def accept_cell(self, key: tuple[str, ...], column: str, val_a: str, val_b: str) -> int:
        return snaps_mod.accept_cell(self, key, column, val_a, val_b)

    def confirm_column_draft(self) -> int:
        return snaps_mod.confirm_column_draft(self)

    def confirm_pair_draft(self, unchecked: set[tuple[str, ...]] | None = None) -> int:
        return snaps_mod.confirm_pair_draft(self, unchecked)

    def accept_unmatched(self, side: str, key: tuple[str, ...]) -> int:
        return snaps_mod.accept_unmatched(self, side, key)

    def accept_all_unmatched(self, side: str) -> int:
        return snaps_mod.accept_all_unmatched(self, side)

    def accept_extra(self, side: str, name: str) -> int:
        return snaps_mod.accept_extra(self, side, name)

    # --- undo ---

    def undo_column(self, column: str) -> int:
        return snaps_mod.undo_column(self, column)

    def undo_cell(self, key: tuple[str, ...], column: str) -> int:
        return snaps_mod.undo_cell(self, key, column)

    def undo_pair(self, column: str, val_a: str, val_b: str) -> int:
        return snaps_mod.undo_pair(self, column, val_a, val_b)

    def undo_unmatched(self, side: str, key: tuple[str, ...] | None = None) -> int:
        return snaps_mod.undo_unmatched(self, side, key)

    def undo_extra(self, side: str, name: str) -> int:
        return snaps_mod.undo_extra(self, side, name)

    def remember_grain(self, grain: tuple[Any, ...], n: int) -> None:
        if n > 0:
            self.last_grain = grain

    def undo_grain(self, grain: tuple[Any, ...]) -> int:
        kind = grain[0]
        if kind == "column":
            return self.undo_column(str(grain[1]))
        if kind == "columns":
            total = 0
            for name in grain[1:]:
                total += self.undo_column(str(name))
            return total
        if kind == "pair":
            return self.undo_pair(str(grain[1]), str(grain[2]), str(grain[3]))
        if kind == "cell":
            key = grain[1]
            if not isinstance(key, tuple):
                return 0
            return self.undo_cell(key, str(grain[2]))
        if kind == "unmatched":
            key = grain[2]
            if not isinstance(key, tuple):
                return 0
            return self.undo_unmatched(str(grain[1]), key)
        if kind == "unmatched_all":
            return self.undo_unmatched(str(grain[1]))
        if kind == "extra":
            return self.undo_extra(str(grain[1]), str(grain[2]))
        return 0

    def undo_last_grain(self) -> int:
        g = self.last_grain
        if not g:
            return 0
        n = self.undo_grain(g)
        if n:
            self.last_grain = None
        return n

    # --- next lever ---

    def next_lever_place(self, current: Place) -> Place:
        return roster_mod.next_lever_place(self, current)

    def _first_pending_key(self, side: str) -> tuple[str, ...] | None:
        return roster_mod._first_pending_key(self, side)

    def next_pending_key_in_grid(self, side: str, current: tuple[str, ...]) -> tuple[str, ...] | None:
        return pages_mod.next_pending_key_in_grid(self, side, current)

    def next_pending_cell_in_pair(self, after: tuple[str, ...]) -> tuple[str, ...] | None:
        return pages_mod.next_pending_cell_in_pair(self, after)

    def page_index_for_key(
        self, frame: pl.DataFrame, key: tuple[str, ...] | None
    ) -> tuple[int, int]:
        return pages_mod.page_index_for_key(frame, self.keys, key)

    def page_index_for_pair_key(self, key: tuple[str, ...] | None) -> tuple[int, int]:
        if self.pair_draft_col is None or not key:
            return 0, 0
        frame = self.pending_cells.filter(
            (pl.col("column") == self.pair_draft_col)
            & (pl.col("val_a") == self.pair_draft_va)
            & (pl.col("val_b") == self.pair_draft_vb)
        ).sort(self.keys)
        return pages_mod.page_index_for_key(frame, self.keys, key)

    def page_index_for_unmatched_key(
        self, side: str, key: tuple[str, ...] | None
    ) -> tuple[int, int]:
        frame = (self.a_only if side == "A" else self.b_only).sort(self.keys)
        return pages_mod.page_index_for_key(frame, self.keys, key)

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
        return delta

    def prune_place(self, place: Place, pair_draft_active: bool = False) -> Place:
        p = place
        if p.column and p.column not in self.comparable:
            return Place(screen="roster", roster_filter=p.roster_filter, last_pair=p.last_pair)
        if p.screen in ("pair_list", "cell_step", "accepted", "equal", "all_matched"):
            if not p.column or p.column not in self.comparable:
                return Place(screen="roster", roster_filter=p.roster_filter, last_pair=p.last_pair)
        if p.screen == "cell_step" and not pair_draft_active:
            p = replace(p, screen="pair_list")
        if p.screen == "a_only" and self.a_only.is_empty():
            return Place(screen="roster", roster_filter=p.roster_filter, last_pair=p.last_pair)
        if p.screen == "b_only" and self.b_only.is_empty():
            return Place(screen="roster", roster_filter=p.roster_filter, last_pair=p.last_pair)
        if p.screen == "extras":
            if not self.extras_a and not self.extras_b:
                return Place(screen="roster", roster_filter=p.roster_filter, last_pair=p.last_pair)
        return p

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

    def to_manifest(self, place: Place | None = None) -> dict[str, Any]:
        p = place or Place()
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
                "screen": p.screen,
                "column": p.column,
                "extra_side": p.extra_side,
                "extra_name": p.extra_name,
                "view_tab": p.view_tab,
                "roster_filter": p.roster_filter,
                "last_pair": list(p.last_pair) if p.last_pair else None,
                "detail_step": "cell_step" if p.screen == "cell_step" else "pair_list",
            },
        }

    def export_zip(self, path: str, place: Place | None = None) -> None:
        path = abs_path(path)
        if not path.endswith(".recon.zip"):
            if path.endswith(".zip"):
                path = path[: -4] + ".recon.zip"
            else:
                path = path + ".recon.zip"
            path = abs_path(path)
        manifest = json.dumps(self.to_manifest(place), indent=2)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", manifest)

    @classmethod
    def from_session(cls, path: str) -> tuple[Engine, Place]:
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
        place_man = man.get("place") or {}
        last = place_man.get("last_pair")
        last_t: tuple[str, str, str] | None = None
        if last and len(last) == 3:
            last_t = (str(last[0]), str(last[1]), str(last[2]))
        screen = place_man.get("screen") or "roster"
        if place_man.get("detail_step") == "cell_step" or screen == "cell_step":
            screen = "pair_list"  # drafts are not persisted
        place = Place(
            screen=screen if screen != "cell_step" else "pair_list",
            column=place_man.get("column"),
            extra_side=place_man.get("extra_side"),
            extra_name=place_man.get("extra_name"),
            view_tab=place_man.get("view_tab") or "pending",
            roster_filter=place_man.get("roster_filter") or "",
            last_pair=last_t,
        )
        place = eng.prune_place(place, pair_draft_active=False)
        if last_t:
            landed = eng.place_from_last_pair(last_t, roster_filter=place.roster_filter)
            if landed is not None:
                place = landed
        return eng, place

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
