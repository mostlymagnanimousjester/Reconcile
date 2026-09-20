"""Engine facade: refresh, load wiring, and re-exports."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Literal

import polars as pl

from reconcile.errors import HardFail
from reconcile.insights import (
    cell_insights,
    first_diff,
)
from reconcile.load import SideTable, load_side

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
    speculative: str = ""
    returned: bool = False
    sent_a: str = ""
    sent_b: str = ""
    sent_both: str = ""
    trim: str = ""
    case: str = ""
    trim_case: str = ""
    num: str = ""
    ws: str = ""
    date: str = ""


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
from reconcile import suggest as suggest_mod  # noqa: E402
from reconcile.compare import _empty_df  # noqa: E402
from reconcile.snaps import _empty_cell_snaps  # noqa: E402

PAGE_SIZE = pages_mod.PAGE_SIZE
_page = pages_mod._page
_page_dicts = pages_mod._page_dicts


class Engine:
    """In-memory reconcile state. Polars owns the frames; TUI asks for pages."""

    def __init__(self, side_a: SideTable, side_b: SideTable, keys: list[str]) -> None:
        self.a = side_a
        self.b = side_b
        self.keys = keys
        self.cell_snaps: pl.DataFrame = _empty_cell_snaps(keys)
        self.unmatched_snaps_a: pl.DataFrame | None = None
        self.unmatched_snaps_b: pl.DataFrame | None = None
        self.extra_snaps: list[ExtraSnap] = []
        self.context_columns: dict[str, list[str]] = {}
        # focus column → {group id 0-9 → member columns in table A import order}
        self.context_groups: dict[str, dict[int, list[str]]] = {}
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
        self._pair_draft_cells: pl.DataFrame | None = None
        self._pair_draft_n: int = 0
        self._pair_groups_df: pl.DataFrame = pl.DataFrame(
            {"column": [], "val_a": [], "val_b": [], "n": []},
            schema={
                "column": pl.Utf8,
                "val_a": pl.Utf8,
                "val_b": pl.Utf8,
                "n": pl.UInt32,
            },
        )
        self._top_pair: dict[str, tuple[str, str, int]] = {}
        self._pending_by_col: dict[str, int] = {}
        self._accepted_by_col: dict[str, int] = {}
        self._mismatch_n: dict[str, int] = {}
        self._col_stats: dict[str, dict[str, Any]] = {}
        self._returned_columns: set[str] = set()
        self._extra_tags: dict[tuple[str, str], list[str]] = {}
        self._pair_ctx_by_col: dict[str, pl.DataFrame] = {}
        self._equal_by_col: dict[str, pl.DataFrame] = {}
        self._all_matched_by_col: dict[str, pl.DataFrame] = {}
        self.sheet_set: list[str] | None = None
        self.sheet_index: int = 0
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
        sheet_set: list[str] | None = None,
        sheet_index: int = 0,
    ) -> Engine:
        if sheet_set:
            from reconcile.delimited import abs_path
            from reconcile.sheets import require_sheets_present

            a_path = abs_path(a_path)
            b_path = abs_path(b_path)
            require_sheets_present(a_path, sheet_set)
            require_sheets_present(b_path, sheet_set)
            name = sheet_set[sheet_index]
            a_sheet = b_sheet = name
        side_a = load_side(
            a_path, a_sheet, "A", delimiter=a_delim, encoding=a_encoding
        )
        side_b = load_side(
            b_path, b_sheet, "B", delimiter=b_delim, encoding=b_encoding
        )
        eng = cls(side_a, side_b, keys)
        if sheet_set:
            eng.sheet_set = list(sheet_set)
            eng.sheet_index = sheet_index
        return eng

    def sheet_remaining_work(self) -> int:
        """Pending columns + unmatched rows + extras (the S gate)."""
        return (
            self.pending_columns_n()
            + self.unmatched_rows_n()
            + self.pending_extras_n()
        )

    def advance_sheet(self) -> None:
        """Load the next same-named pair. Clean slate; keep last good on failure."""
        if self.sheet_set is None:
            raise InTuiError(
                "ERROR: S is next sheet only when launched with -sheets"
            )
        if self.draft_in_flight():
            if self.pair_draft_col is not None:
                raise InTuiError("ERROR: confirm or cancel the pair draft first")
            raise InTuiError("ERROR: confirm or cancel the current draft first")
        if self.sheet_remaining_work() != 0:
            raise InTuiError("ERROR: remaining work on this sheet is not 0")
        if self.sheet_index >= len(self.sheet_set) - 1:
            raise InTuiError("ERROR: no next sheet")
        next_name = self.sheet_set[self.sheet_index + 1]
        try:
            side_a = load_side(self.a.path, next_name, "A")
            side_b = load_side(self.b.path, next_name, "B")
        except HardFail as exc:
            raise InTuiError(f"ERROR: {exc.message}") from exc
        keys = self.keys
        sheet_set = self.sheet_set
        next_index = self.sheet_index + 1
        self.__init__(side_a, side_b, keys)
        self.sheet_set = sheet_set
        self.sheet_index = next_index

    def _rebuild(self) -> None:
        compare_mod.rebuild_frames(self)
        compare_mod.sort_unmatched(self)
        self._apply_snapshots()
        pages_mod.invalidate_equal_caches(self)

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

    def pending_columns_n(self) -> int:
        return sum(1 for r in self._roster_cache if r.kind == "column" and r.pending > 0)

    def unmatched_rows_n(self) -> int:
        return self.pending_a_only_n() + self.pending_b_only_n()

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

    def visible_column_roster(self, name_filter: str = "") -> list[RosterRow]:
        return roster_mod.visible_column_roster(self, name_filter)

    def column_roster(self, name_filter: str = "", include_settled: bool = False) -> list[RosterRow]:
        return roster_mod.column_roster(self, name_filter, include_settled)

    def _roster_agg_maps(self) -> tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, dict[str, Any]]]:
        return roster_mod._roster_agg_maps(self)

    def _build_roster_cache(self) -> list[RosterRow]:
        return roster_mod._build_roster_cache(self)

    def is_categorical(self, column: str) -> bool:
        return roster_mod.is_categorical(self, column)

    # --- pair list ---

    def pair_groups(self, column: str) -> pl.DataFrame:
        return compare_mod.pair_groups(self, column)

    def pair_page(self, column: str, page: int) -> tuple[list[dict[str, Any]], int, int]:
        return pages_mod.pair_page(self, column, page)

    def next_pair_below(
        self, column: str, val_a: str, val_b: str
    ) -> tuple[str, str] | None:
        return pages_mod.next_pair_below(self, column, val_a, val_b)

    def union_pairs(
        self, columns: list[str], page: int = 0
    ) -> tuple[list[dict[str, Any]], int, int]:
        return pages_mod.union_pairs(self, columns, page)

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

    def suggest_extras(self) -> list[dict[str, Any]]:
        return suggest_mod.suggest_extras(self)

    def context_values(
        self, key: tuple[str, ...] | None, column: str
    ) -> list[tuple[str, str, str]]:
        return pages_mod.context_values(self, key, column)

    def context_singles(self, column: str) -> list[str]:
        return pages_mod.context_singles(self, column)

    def context_group_list(self, column: str) -> list[tuple[int, list[str]]]:
        return pages_mod.context_group_list(self, column)

    def context_views(self, column: str) -> list[pages_mod.ContextView]:
        return pages_mod.context_views(self, column)

    def first_pending_key_in_pair(
        self, column: str, val_a: str, val_b: str
    ) -> tuple[str, ...] | None:
        return pages_mod.first_pending_key_in_pair(self, column, val_a, val_b)

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

    def pairs_returned_mask(self, column: str, pairs: list[tuple[str, str]]) -> list[bool]:
        return pages_mod.pairs_returned_mask(self, column, pairs)

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
        return self._pair_draft_n

    def pair_draft_key_frame(self) -> pl.DataFrame:
        schema = {k: pl.Utf8 for k in self.keys}
        if self.pair_draft_col is None or self._pair_draft_cells is None:
            return _empty_df(schema)
        return self._pair_draft_cells.select(self.keys)

    def cancel_drafts(self) -> None:
        self.column_draft = set()
        self.clear_pair_draft()

    def clear_pair_draft(self) -> None:
        self.pair_draft_col = None
        self.pair_draft_va = None
        self.pair_draft_vb = None
        self._pair_draft_cells = None
        self._pair_draft_n = 0

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
            if self._pending_by_col.get(name, 0) > 0:
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
            if self._pending_by_col.get(col, 0) > 0:
                hits.append(col)
        if not hits:
            raise InTuiError("ERROR: regex matched 0 pending columns")
        self.column_draft = set(hits)
        return len(hits)

    def sentinel_hits(self, side: str, sentinel: str) -> list[str]:
        """Pending comparable columns whose chosen side is that comparable-row constant."""
        val_col = "sent_a" if side == "A" else "sent_b"
        sent = self.column_sentinels
        if sent.is_empty() or self.pending_cells.is_empty():
            return []
        pending_cols = self.pending_cells.group_by("column").len().select("column")
        if self.comparable:
            pending_cols = pending_cols.filter(
                pl.col("column").is_in(list(self.comparable))
            )
        else:
            return []
        hits_df = sent.filter(pl.col(val_col) == pl.lit(sentinel)).join(
            pending_cols, on="column", how="inner"
        )
        if hits_df.is_empty():
            return []
        return hits_df.get_column("column").to_list()

    def start_sentinel_draft(self, side: str, sentinel: str) -> int:
        if self.draft_in_flight():
            raise InTuiError("ERROR: confirm or cancel the current draft first")
        if side not in ("A", "B"):
            raise InTuiError("ERROR: sentinel scan requires Side A or Side B")
        try:
            hits = self.sentinel_hits(side, sentinel)
        except Exception as exc:
            raise InTuiError(f"ERROR: sentinel scan error: {exc}") from exc
        if not hits:
            raise InTuiError("ERROR: sentinel matched 0 pending columns")
        self.column_draft = set(hits)
        return len(hits)

    def start_pair_draft(self, column: str, val_a: str, val_b: str) -> int:
        if self.column_draft:
            raise InTuiError("ERROR: confirm or cancel the column draft first")
        if self.pair_draft_col is not None:
            raise InTuiError("ERROR: confirm or cancel the current pair draft first")
        frame = self.pending_cells.filter(
            (pl.col("column") == column)
            & (pl.col("val_a") == val_a)
            & (pl.col("val_b") == val_b)
        ).sort(self.keys)
        n = frame.height
        if n == 0:
            return 0
        self.pair_draft_col = column
        self.pair_draft_va = val_a
        self.pair_draft_vb = val_b
        self._pair_draft_cells = frame
        self._pair_draft_n = n
        return n

    # --- accept ---

    def accept_column(self, column: str) -> int:
        return snaps_mod.accept_column(self, column)

    def accept_pair(self, column: str, val_a: str, val_b: str) -> int:
        return snaps_mod.accept_pair(self, column, val_a, val_b)

    def accept_pair_across_columns(
        self, columns: list[str], val_a: str, val_b: str
    ) -> int:
        return snaps_mod.accept_pair_across_columns(self, columns, val_a, val_b)

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
        if kind == "pairs":
            va, vb = str(grain[1]), str(grain[2])
            total = 0
            for name in grain[3:]:
                total += self.undo_pair(str(name), va, vb)
            return total
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
        if not self.grain_still_applies(g):
            self.last_grain = None
            return 0
        n = self.undo_grain(g)
        self.last_grain = None
        return n

    def grain_still_applies(self, grain: tuple[Any, ...] | None) -> bool:
        """True iff last_grain still matches a live accepted diff (not an orphan snap)."""
        if not grain:
            return False
        kind = grain[0]
        if kind == "column":
            if self.accepted_cells.is_empty():
                return False
            return self.accepted_cells.filter(pl.col("column") == str(grain[1])).height > 0
        if kind == "columns":
            if self.accepted_cells.is_empty():
                return False
            names = [str(n) for n in grain[1:]]
            if not names:
                return False
            return self.accepted_cells.filter(pl.col("column").is_in(names)).height > 0
        if kind == "pair":
            if self.accepted_cells.is_empty():
                return False
            return (
                self.accepted_cells.filter(
                    (pl.col("column") == str(grain[1]))
                    & (pl.col("val_a") == str(grain[2]))
                    & (pl.col("val_b") == str(grain[3]))
                ).height
                > 0
            )
        if kind == "pairs":
            if self.accepted_cells.is_empty():
                return False
            names = [str(n) for n in grain[3:]]
            if not names:
                return False
            return (
                self.accepted_cells.filter(
                    pl.col("column").is_in(names)
                    & (pl.col("val_a") == str(grain[1]))
                    & (pl.col("val_b") == str(grain[2]))
                ).height
                > 0
            )
        if kind == "cell":
            key = grain[1]
            if not isinstance(key, tuple) or self.accepted_cells.is_empty():
                return False
            expr = pl.col("column") == str(grain[2])
            for k, v in zip(self.keys, key):
                expr = expr & (pl.col(k) == v)
            return self.accepted_cells.filter(expr).height > 0
        if kind == "unmatched":
            key = grain[2]
            if not isinstance(key, tuple):
                return False
            frame = self.accepted_a_only if str(grain[1]) == "A" else self.accepted_b_only
            if frame.is_empty():
                return False
            expr = pl.lit(True)
            for k, v in zip(self.keys, key):
                expr = expr & (pl.col(k) == v)
            return frame.filter(expr).height > 0
        if kind == "unmatched_all":
            frame = self.accepted_a_only if str(grain[1]) == "A" else self.accepted_b_only
            return frame.height > 0
        if kind == "extra":
            return (str(grain[1]), str(grain[2])) in self.accepted_extras
        return False

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
        frame = self._pair_draft_cells
        if frame is None:
            return 0, 0
        return pages_mod.page_index_for_key(frame, self.keys, key)

    def page_index_for_unmatched_key(
        self, side: str, key: tuple[str, ...] | None
    ) -> tuple[int, int]:
        frame = self.a_only if side == "A" else self.b_only
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
                if name in self.comparable and self._pending_by_col.get(name, 0) > 0:
                    keep.add(name)
            self.column_draft = keep
        if self.pair_draft_col is not None:
            if self.pair_draft_height() == 0:
                self.clear_pair_draft()
        returned_n = returned_cells.height + returned_keys.height + len(self.returned_extras)
        delta = RefreshDelta(
            pending_before=before_pending,
            pending_after=self.pending_total(),
            accepted_before=before_acc,
            accepted_after=self.accepted_total(),
            returned=returned_n,
            message=(
                f"remaining work {before_pending}→{self.pending_total()}, "
                f"accepted {before_acc}→{self.accepted_total()}, "
                f"{returned_n} returned to pending"
            ),
        )
        self.last_refresh_delta = delta
        if self.last_grain is not None and not self.grain_still_applies(self.last_grain):
            self.last_grain = None
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
        if p.screen == "overview":
            return Place(
                screen="roster",
                roster_filter=p.roster_filter,
                last_pair=p.last_pair,
                focused_name=p.focused_name,
            )
        return p

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
