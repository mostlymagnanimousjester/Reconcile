"""Textual TUI: roster home, pair-first detail, unmatched grids, mismatched columns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar

import polars as pl
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Static

from reconcile.engine import Engine, InTuiError, Place, RosterRow
from reconcile.insights import format_context_tuple

HELP = """\
KEYS  (? this help · Esc closes)

EVERYWHERE
Enter  drill (roster column → pair list, pair → cell step, modal Run / overview entry)
Esc    back (close modal → cancel roster/cell-step draft → parent screen)
a      accept the current selection (roster column / pair / cell / unmatched key / extra)
A      accept all on this screen (entire column from pair list; all unmatched keys on this side)
u      undo last accept, then focused grain
r      refresh (re-read live files; last good state on failure)
i      overview modal (counts + unmatched keys / mismatched columns). Esc closes.
?      this help
q      quit (discards unconfirmed draft)

ROSTER (column roster, table A import order)
a      accept this column in place (selection moves below; does not drill)
A      same as a (one column)
v      show/hide accepted and equal columns (default hidden)
m      same exact pair on columns
/      regex column draft (comparable names; not a view filter)
=      exact sentinel (side A|B, comparable-row constant)
Space  toggle focused column in the live column draft (ON / off)
y      confirm the live column draft; land on roster (next-below / first pending)
n / p  ERROR (no pages)

PAIR LIST (Pending tab)
Enter  cell step (pair draft of those exact strings)
a      accept this pair (selection moves below)
A      accept entire column
m      same exact pair on columns
c      context-column picker (Space standalone; 0-9 groups on that page)
U      undo entire column (pair list only; refused while a pair draft is in flight)
n / p  next / previous page
.      last pair: cell-step if already on that column's pair list; else that pair (column detail only)
Esc    roster

CELL STEP
a      accept this cell (selection moves below)
Space  toggle focused cell in the pair draft (ON / off)
y      confirm still-checked cells, then next lever
c      context-column picker (Space standalone; 0-9 groups on that page)
n / p  page
Esc    back to pair list (cancels the pair draft)

UNMATCHED KEYS / MISMATCHED COLUMNS  (open from i)
a      accept this key / extra (selection moves below)
A      accept all unmatched on this side, then next lever
n / p  page (keys)
Esc    roster

DRAFTS
At most one draft: column XOR pair cells (column: roster / regex, = sentinel; pair: cell step).
After / or =, selected columns show ON; y ACCEPT selected, Space select/deselect, Esc cancel.
After y accepts a column draft, land back on the roster (next-below / first pending).
A is refused while a pair draft is in flight (confirm or cancel first).
After / or =, m skips the column picker and uses the live ON columns (Space still toggles ON/off).
m opens a two-step modal: pick columns, then one exact pair, apply to selected columns that have it.

TABS / PLACE
Named tabs (Pending / Accepted / Equal / All matched). No keys 1–4.
Tab switch is refused while a pair draft is in flight (Esc cancels).
U is pair-list only. . is column detail only.
The column roster lists pending comparable columns in table A import order.
v shows accepted/equal columns dim (pending section, then settled), not as remaining work.
A-only / B-only keys and mismatched columns (headers on one side only) are not columns:
open them from i overview (or next lever).

NOTES
Long strings wrap in the footer pane (the grid is a one-line navigator).
This TUI never writes, opens, or copies into the source files.
Pending = 0 is the goal: edit sources elsewhere then refresh, or accept snapshots.
The speculative column shows insight text only (no speculative: prefix). Insights never change remaining counts.
Sentinel means that side is one constant on all comparable (shared-key) rows: sentinel A=0, sentinel B="", sentinel both A=x B=y.
Context columns (c) are dedicated labeled columns (ctx:Name). Groups are ctx:gN A+B (tuple of members). Pair list: top-5 unique values or tuples with pair-row counts (foo 12 | bar 4 …).

CONTEXT PICKER (c on column detail only)
Space  toggle standalone context (on without a group; same as today)
0-9    toggle the focused column's membership in group N
Enter/y confirm · Esc cancel
A column can be standalone and in several groups. Empty groups do not appear.
0-9 apply only on this page (not roster / pair list / cell step).
"""

CSS = """
Screen {
    background: #24150c;
    color: #fff3e0;
}
#banner {
    background: #7a2216;
    color: #ffd4a8;
    text-style: bold;
    height: auto;
    padding: 0 1;
}
#banner.hidden {
    display: none;
}
#banner.draft {
    background: #7a3c08;
    color: #ffe27a;
    text-style: bold;
}
#work {
    height: 1fr;
}
#pane {
    height: auto;
    max-height: 24;
    overflow-y: auto;
    padding: 0 1;
    color: #fff3e0;
    background: #2c180e;
}
#footer {
    dock: bottom;
    height: auto;
    min-height: 1;
    padding: 0 1;
    background: #3c1a0c;
    color: #ffe27a;
    text-style: bold;
}
#footer.busy {
    color: #ffc44d;
}
#tabs {
    height: 3;
    padding: 0 1;
}
Button {
    background: #c45a24;
    color: #fff8ec;
    border: tall #f0a050;
}
Button:hover, Button:focus, Button.-active {
    background: #e06a2c;
    color: #fffaf0;
    border: tall #ffc070;
}
Button.-primary, Button.primary {
    background: #c45a24;
    color: #fff8ec;
    border: tall #ffc070;
}
#tabs Button {
    background: #4a2412;
    color: #ffe27a;
    border: tall #e08a38;
}
DataTable {
    height: 1fr;
    padding: 0 1;
}
DataTable > .datatable--cursor {
    background: #d45a24;
    color: #fff8ec;
    text-style: reverse;
}
.lever {
    text-style: bold underline;
    color: #ffe27a;
}
.pending {
    text-style: bold;
    color: #ffe27a;
}
.accepted {
    color: #d2a87a;
}
.equal {
    color: #e8b898;
}
.returned {
    text-style: reverse;
    color: #ffb347;
}
.error {
    text-style: bold;
    color: #ff6644;
}
#modal {
    width: 80;
    height: auto;
    max-height: 90%;
    background: #301c10;
    border: heavy #ffb347;
    padding: 1 2;
}
#modal Input {
    margin: 1 0;
}
#help {
    height: auto;
    max-height: 36;
    overflow-y: auto;
    padding: 1;
}
.dim {
    color: #d2a87a;
}
"""


def _speculative_line(spec: str) -> str:
    """Insight text only. The column is already named speculative."""
    return spec.strip()


def _grain_involves_column(grain: tuple[Any, ...], column: str) -> bool:
    kind = grain[0]
    if kind == "column":
        return str(grain[1]) == column
    if kind == "columns":
        return column in {str(n) for n in grain[1:]}
    if kind == "pair":
        return str(grain[1]) == column
    if kind == "pairs":
        return column in {str(n) for n in grain[3:]}
    if kind == "cell":
        return str(grain[2]) == column
    return False


_Id = TypeVar("_Id")


def select_after_accept(
    old_ids: list[_Id], accepted: _Id, new_ids: list[_Id]
) -> _Id | None:
    """Keep the cursor on the former next-below row after accept hides it.

    If that item is gone too, land on the new last remaining identity.
    Empty ``new_ids`` is the empty state.
    """
    if not new_ids:
        return None
    remaining = set(new_ids)
    try:
        i = old_ids.index(accepted)
    except ValueError:
        return new_ids[0]
    for item in old_ids[i + 1 :]:
        if item in remaining:
            return item
    return new_ids[-1]


def _display_text(value: str) -> str:
    """Blank key/value cells stay visible in the pane and navigator."""
    return value if value else "(empty)"


def _key_tuples(frame: pl.DataFrame, keys: list[str]) -> set[tuple[str, ...]]:
    """Key tuples from a key-only frame (refresh deltas / small unchecked sets)."""
    if frame.is_empty() or not keys:
        return set()
    cols = [frame.get_column(k).to_list() for k in keys]
    return set(zip(*cols, strict=True))


def _diff_text(label: str, value: str, other: str, first: int) -> Text:
    t = Text()
    if label:
        t.append(f"{label} ", style="bold")
    if not value:
        t.append(_display_text(value))
        return t
    t.append(value[:first])
    if first < len(value):
        t.append(value[first], style="reverse bold")
        t.append(value[first + 1 :])
    elif len(value) != len(other):
        t.append("∎", style="reverse bold")
    return t


class HelpModal(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "Close"), Binding("question_mark", "close", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(id="modal"):
            yield Static(HELP, id="help")
            yield Static("Esc closes", classes="dim")

    def action_close(self) -> None:
        self.dismiss(None)


class OverviewModal(ModalScreen[str | None]):
    """Counts + unmatched/extras entries. Not a place.screen. Esc closes."""

    BINDINGS = [
        Binding("escape", "close", "Close", priority=True),
        Binding("enter", "pick", "Open", priority=True),
        Binding("a", "refuse", show=False, priority=True),
        Binding("A", "refuse", show=False, priority=True),
        Binding("u", "refuse", show=False, priority=True),
        Binding("U", "refuse", show=False, priority=True),
        Binding("y", "noop", show=False, priority=True),
        Binding("n", "no_pages", show=False, priority=True),
        Binding("p", "no_pages", show=False, priority=True),
        Binding("space", "noop", show=False, priority=True),
    ]

    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self.engine = engine

    def compose(self) -> ComposeResult:
        e = self.engine
        lines = ["OVERVIEW (counts)", ""]
        lines.extend(e.identity_lines())
        lines += [
            "",
            f"matched keys: {e.matched_key_count()}",
            f"pending columns {e.pending_columns_n()}",
            f"A-only keys pending {e.pending_a_only_n()}  accepted {e.accepted_a_only.height}",
            f"B-only keys pending {e.pending_b_only_n()}  accepted {e.accepted_b_only.height}",
            f"mismatched columns pending {e.pending_extras_n()}  accepted {len(e.accepted_extras)}",
            f"mismatched cells pending {e.pending_cells_n()}  accepted {e.accepted_cells.height}",
            f"remaining work items {e.pending_total()}  (exit 0 when this is 0)",
            "",
            "Enter an entry to open that list. Esc closes. a/A do not accept here.",
        ]
        with Vertical(id="modal"):
            yield Static("\n".join(lines), id="overview-body")
            table: DataTable = DataTable(cursor_type="row", id="ov-entries")
            table.add_columns("entry", "pending")
            yield table
            yield Static("Enter open list · Esc close", classes="dim")
            yield Static("", id="modal-err", classes="error")

    def on_mount(self) -> None:
        table = self.query_one("#ov-entries", DataTable)
        e = self.engine
        table.add_row("A-only keys", str(e.pending_a_only_n()), key="a_only")
        table.add_row("B-only keys", str(e.pending_b_only_n()), key="b_only")
        table.add_row("Mismatched columns", str(e.pending_extras_n()), key="extras")
        table.focus()

    def _entry_key(self) -> str | None:
        table = self.query_one("#ov-entries", DataTable)
        if table.row_count <= 0:
            return None
        row = table.get_row_at(table.cursor_row)
        label = str(row[0])
        if "A-only" in label:
            return "a_only"
        if "B-only" in label:
            return "b_only"
        return "extras"

    def action_close(self) -> None:
        self.dismiss(None)

    def action_pick(self) -> None:
        key = self._entry_key()
        if key in {"a_only", "b_only", "extras"}:
            self.dismiss(key)

    def action_refuse(self) -> None:
        self.query_one("#modal-err", Static).update("ERROR: not remaining work")

    def action_no_pages(self) -> None:
        self.query_one("#modal-err", Static).update("ERROR: no pages on this screen")

    def action_noop(self) -> None:
        return

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        self.action_pick()


class RegexModal(ModalScreen[str | None]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "ok", "Run", priority=True),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="modal"):
            yield Static("Name regex (comparable columns only). Python re.search, case-sensitive.")
            yield Input(placeholder="(?i)status|flag", id="pat")
            yield Static("Enter Run · Esc cancel without changing the draft", classes="dim")

    def on_mount(self) -> None:
        self.query_one("#pat", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_ok(self) -> None:
        self.dismiss(self.query_one("#pat", Input).value)


class SentinelModal(ModalScreen[tuple[str, str] | None]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "ok", "Run", priority=True),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="modal"):
            yield Static(
                "Sentinel = that side is one constant on all comparable (shared-key) rows. Choose exactly one side."
            )
            with Horizontal():
                yield Button("A", id="side-a")
                yield Button("B", id="side-b")
            yield Input(placeholder="exact string (empty is legal)", id="sentinel")
            yield Static(
                "Enter Run · Esc cancel. After Run: y ACCEPT selected, Space select/deselect.",
                classes="dim",
            )
            yield Static("", id="modal-err", classes="error")
        self._side: str | None = None

    def on_mount(self) -> None:
        self._side = None
        self.query_one("#sentinel", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "side-a":
            self._side = "A"
            event.button.label = "[A]"
            self.query_one("#side-b", Button).label = "B"
        elif event.button.id == "side-b":
            self._side = "B"
            event.button.label = "[B]"
            self.query_one("#side-a", Button).label = "A"
        self.query_one("#sentinel", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_ok(self) -> None:
        if self._side is None:
            self.query_one("#modal-err", Static).update("ERROR: choose Side A or Side B")
            return
        self.dismiss((self._side, self.query_one("#sentinel", Input).value))


@dataclass
class ContextPick:
    singles: list[str]
    groups: dict[int, list[str]]


class ContextModal(ModalScreen[ContextPick | None]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "ok", "OK", priority=True),
        Binding("space", "toggle", "Toggle"),
        Binding("y", "ok", "OK"),
        *[Binding(str(d), f"toggle_group({d})", show=False, priority=True) for d in range(10)],
        *[
            Binding(k, "noop", show=False, priority=True)
            for k in (
                "a",
                "q",
                "u",
                "U",
                "A",
                "n",
                "p",
                "r",
                "e",
                "o",
                "c",
                "i",
                "m",
                "slash",
                "equals",
                "full_stop",
                "question_mark",
                "question",
                ".",
                "/",
                "=",
                "?",
            )
        ],
    ]

    def __init__(
        self,
        names: list[str],
        selected: set[str],
        groups: dict[int, list[str]] | None = None,
    ) -> None:
        super().__init__()
        self.names = names
        self.selected = set(selected)
        self.groups: dict[int, set[str]] = {}
        for gid, members in (groups or {}).items():
            g = int(gid)
            if 0 <= g <= 9:
                self.groups[g] = {m for m in members if m in names}

    def compose(self) -> ComposeResult:
        with Vertical(id="modal"):
            yield Static(
                "Context columns (intersection, not keys, not this column). "
                "Space = standalone. 0-9 = groups."
            )
            table: DataTable = DataTable(cursor_type="row", id="ctx")
            table.add_columns("on", "groups", "name")
            yield table
            yield Static(
                "Enter/y confirm · Esc cancel · Space standalone · 0-9 group",
                classes="dim",
            )

    def on_mount(self) -> None:
        self._fill()
        self.query_one("#ctx", DataTable).focus()

    def _groups_label(self, name: str) -> str:
        gids = [str(g) for g in range(10) if name in self.groups.get(g, set())]
        return " · ".join(gids)

    def _fill(self) -> None:
        table = self.query_one("#ctx", DataTable)
        table.clear()
        for n in self.names:
            mark = "[x]" if n in self.selected else "[ ]"
            table.add_row(mark, self._groups_label(n), n, key=n)

    def _focused_column(self) -> str | None:
        table = self.query_one("#ctx", DataTable)
        if not self.names or table.row_count <= 0:
            return None
        row = table.get_row_at(table.cursor_row)
        return str(row[-1])

    def action_toggle(self) -> None:
        name = self._focused_column()
        if not name:
            return
        if name in self.selected:
            self.selected.discard(name)
        else:
            self.selected.add(name)
        row = self.query_one("#ctx", DataTable).cursor_row
        self._fill()
        self.query_one("#ctx", DataTable).move_cursor(row=row)

    def action_toggle_group(self, gid: int) -> None:
        name = self._focused_column()
        if not name:
            return
        gid = int(gid)
        if gid < 0 or gid > 9:
            return
        members = self.groups.setdefault(gid, set())
        if name in members:
            members.discard(name)
            if not members:
                del self.groups[gid]
        else:
            members.add(name)
        row = self.query_one("#ctx", DataTable).cursor_row
        self._fill()
        self.query_one("#ctx", DataTable).move_cursor(row=row)

    def action_noop(self) -> None:
        return

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_ok(self) -> None:
        groups: dict[int, list[str]] = {}
        for gid in range(10):
            members = self.groups.get(gid)
            if not members:
                continue
            ordered = [n for n in self.names if n in members]
            if ordered:
                groups[gid] = ordered
        self.dismiss(ContextPick(singles=sorted(self.selected), groups=groups))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()


class MultiPairModal(ModalScreen[tuple[tuple[str, ...], str, str] | None]):
    """Pick columns, then one exact pair; apply that pair to selected columns."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("enter", "ok", "Next", priority=True),
        Binding("space", "toggle", "Toggle"),
        Binding("y", "ok", "OK"),
        *[
            Binding(k, "noop", show=False, priority=True)
            for k in (
                "a",
                "q",
                "u",
                "U",
                "A",
                "n",
                "p",
                "r",
                "c",
                "i",
                "m",
                "v",
                "slash",
                "equals",
                "full_stop",
                "question_mark",
                "question",
                ".",
                "/",
                "=",
                "?",
            )
        ],
    ]

    def __init__(
        self,
        engine: Engine,
        columns: list[str],
        *,
        skip_column_pick: bool = False,
    ) -> None:
        super().__init__()
        self.engine = engine
        self.columns = list(columns)
        self.selected = set(columns)
        self.skip_column_pick = skip_column_pick
        self.phase = "pairs" if skip_column_pick else "columns"
        self._pairs: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="modal"):
            yield Static("", id="multi-title")
            table: DataTable = DataTable(cursor_type="row", id="multi")
            table.add_columns("on", "name")
            yield table
            yield Static("", id="multi-hint", classes="dim")
            yield Static("", id="modal-err", classes="error")

    def on_mount(self) -> None:
        if self.skip_column_pick:
            self._fill_pairs()
        else:
            self._fill_columns()
        self.query_one("#multi", DataTable).focus()

    def _set_err(self, msg: str = "") -> None:
        self.query_one("#modal-err", Static).update(msg)

    def _fill_columns(self) -> None:
        self.phase = "columns"
        self.query_one("#multi-title", Static).update(
            "Same pair on columns. Space toggle. Enter to pick the pair."
        )
        self.query_one("#multi-hint", Static).update(
            "Enter next · Space select/deselect · Esc cancel"
        )
        table = self.query_one("#multi", DataTable)
        table.clear(columns=True)
        table.add_columns("on", "name")
        for name in self.columns:
            mark = "[ON]" if name in self.selected else "[off]"
            table.add_row(mark, name, key=name)

    def _fill_pairs(self) -> None:
        names = [n for n in self.columns if n in self.selected]
        self._pairs, _, _ = self.engine.union_pairs(names)
        self.phase = "pairs"
        self.query_one("#multi-title", Static).update(
            f"Pick one exact pair ({len(names)} column(s)). Enter applies."
        )
        self.query_one("#multi-hint", Static).update(
            "Enter/y apply · Esc "
            + ("cancel" if self.skip_column_pick else "back to columns")
        )
        table = self.query_one("#multi", DataTable)
        table.clear(columns=True)
        table.add_columns("A", "B", "cells", "columns")
        for rec in self._pairs:
            table.add_row(
                _display_text(str(rec["val_a"])),
                _display_text(str(rec["val_b"])),
                str(rec["n"]),
                str(rec["n_cols"]),
            )

    def _focused_column(self) -> str | None:
        table = self.query_one("#multi", DataTable)
        if not self.columns or table.row_count <= 0:
            return None
        row = table.get_row_at(table.cursor_row)
        return str(row[1])

    def _focused_pair(self) -> tuple[str, str] | None:
        if not self._pairs:
            return None
        table = self.query_one("#multi", DataTable)
        i = table.cursor_row
        if i < 0 or i >= len(self._pairs):
            return None
        rec = self._pairs[i]
        return str(rec["val_a"]), str(rec["val_b"])

    def action_toggle(self) -> None:
        if self.phase != "columns":
            return
        name = self._focused_column()
        if not name:
            return
        if name in self.selected:
            self.selected.discard(name)
        else:
            self.selected.add(name)
        row = self.query_one("#multi", DataTable).cursor_row
        self._fill_columns()
        self.query_one("#multi", DataTable).move_cursor(row=row)

    def action_noop(self) -> None:
        return

    def action_cancel(self) -> None:
        if self.phase == "pairs" and not self.skip_column_pick:
            self._set_err("")
            self._fill_columns()
            self.query_one("#multi", DataTable).focus()
            return
        self.dismiss(None)

    def action_ok(self) -> None:
        if self.phase == "columns":
            names = [n for n in self.columns if n in self.selected]
            if not names:
                self._set_err("ERROR: select at least one column")
                return
            pairs, _, _ = self.engine.union_pairs(names)
            if not pairs:
                self._set_err("ERROR: no pending pairs in the selected columns")
                return
            self._set_err("")
            self._fill_pairs()
            self.query_one("#multi", DataTable).focus()
            return
        pair = self._focused_pair()
        names = tuple(n for n in self.columns if n in self.selected)
        if not pair or not names:
            self._set_err("ERROR: pick a pair")
            return
        self.dismiss((names, pair[0], pair[1]))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        self.action_ok()


class ReconcileApp(App[int]):
    CSS = CSS
    TITLE = "Reconcile"
    AUTO_FOCUS = "#grid"
    BINDINGS = [
        Binding("enter", "drill", "Enter", show=False, priority=True),
        Binding("escape", "back", "Back", show=False, priority=True),
        Binding("space", "toggle", "Toggle", show=False),
        Binding("a", "accept", "Accept", show=False),
        Binding("A", "accept_all", "Accept all", show=False),
        Binding("y", "confirm", "Confirm", show=False),
        Binding("u", "undo", "Undo", show=False),
        Binding("U", "undo_column", "Undo column", show=False),
        Binding("r", "refresh", "Refresh", show=False),
        Binding("n", "page_next", "Next", show=False),
        Binding("p", "page_prev", "Prev", show=False),
        Binding("q", "quit_app", "Quit", show=False),
        Binding("c", "context", "Context", show=False),
        Binding("slash", "regex", "Regex", show=False),
        Binding("equals", "sentinel", "Sentinel", show=False),
        Binding("full_stop", "repeat_pair", "Repeat", show=False),
        Binding("question_mark", "help", "Help", show=False),
        Binding("question", "help", "Help", show=False),
        Binding("i", "overview", "Overview", show=False),
        Binding("v", "toggle_accepted", "Show accepted", show=False),
        Binding("m", "multi_pair", "Same pair", show=False),
        Binding(".", "repeat_pair", "Repeat", show=False),
        Binding("/", "regex", "Regex", show=False),
        Binding("=", "sentinel", "Sentinel", show=False),
        Binding("?", "help", "Help", show=False),
    ]

    def __init__(self, engine: Engine, place: Place | None = None) -> None:
        super().__init__()
        self.engine = engine
        self.place = place or Place()
        self.pair_draft_unchecked: set[tuple[str, ...]] = set()
        self.tui_error: str | None = None
        self._roster_index = 0
        self._table_keys: list[Any] = []
        self._mounted_screen: str | None = None
        self._page_count = 1
        self._draft_n = 0
        self.show_accepted_columns = False
        self._busy_note: str | None = None

    def draft_in_flight(self) -> bool:
        return self.engine.draft_in_flight()

    def compose(self) -> ComposeResult:
        yield Static("", id="banner", classes="hidden")
        yield Vertical(id="work")
        yield Static("", id="pane")
        yield Static("", id="footer")

    def on_mount(self) -> None:
        if self.place.screen in {"roster", "overview"} or not self.place.screen:
            self.place.screen = "roster"
        self.render_all()
        self.call_after_refresh(self.set_focus_work)

    def _in_input(self) -> bool:
        return isinstance(self.focused, Input)

    def _modal_active(self) -> bool:
        return isinstance(self.screen, ModalScreen)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # A modal is a real layer: no app verb leaks through (accept/quit/regex/…).
        # Disabled bindings are skipped; the modal's own keys then run.
        if isinstance(self.screen, ModalScreen):
            return False
        if self._in_input() and action not in {"back", "drill"}:
            return False
        return True

    def set_error(self, msg: str | None) -> None:
        self.tui_error = msg
        self._paint_banner()

    def _paint_banner(self) -> None:
        banner = self.query_one("#banner", Static)
        if self.tui_error:
            banner.update(
                self.tui_error if self.tui_error.startswith("ERROR") else f"ERROR: {self.tui_error}"
            )
            banner.set_class(False, "hidden")
            banner.remove_class("draft")
            banner.add_class("error")
            return
        if self.engine.column_draft:
            n = self._draft_n
            banner.update(
                f"{n} column(s) selected for accept. "
                "y ACCEPT selected · m same pair · Space select/deselect · a this column · Esc cancel"
            )
            banner.set_class(False, "hidden")
            banner.remove_class("error")
            banner.add_class("draft")
            return
        banner.update("")
        banner.set_class(True, "hidden")
        banner.remove_class("error")
        banner.remove_class("draft")

    def set_focus_work(self) -> None:
        if self.query("#grid"):
            grid = self.query_one("#grid", DataTable)
            grid.focus()
            if self.place.focused_key and self.place.screen in {
                "cell_step",
                "a_only",
                "b_only",
                "accepted",
                "equal",
                "all_matched",
            }:
                self._move_cursor_to_focused_key(grid)

    def render_all(self) -> None:
        e = self.engine
        if e.pair_draft_col is not None:
            self._draft_n = e.pair_draft_height()
        elif e.column_draft:
            self._draft_n = e.column_draft_n()
        else:
            self._draft_n = 0
        layout = self._work_layout_key()
        if layout != self._mounted_screen:
            self._remount_work()
            self._mounted_screen = layout
        else:
            self._refill_work()
        self._render_pane()
        self._render_footer()
        self._paint_banner()
        self.call_after_refresh(self.set_focus_work)

    def _work_layout_key(self) -> str:
        return self.place.screen

    def _render_footer(self) -> None:
        e = self.engine
        p = self.place
        bits: list[str] = []
        if self._busy_note:
            bits.append(self._busy_note)
        bits.extend(
            [
                f"pending columns {e.pending_columns_n()}",
                f"unmatched rows {e.unmatched_rows_n()}",
                f"mismatched columns {e.pending_extras_n()}",
            ]
        )
        footer = self.query_one("#footer", Static)
        footer.set_class(bool(self._busy_note), "busy")
        # Live set only. Never show column-draft N on the cell step (pair XOR).
        # Esc on pair list does not cancel a column draft — don't claim it does.
        if p.screen == "cell_step" and e.pair_draft_col is not None:
            n = max(0, self._draft_n - len(self.pair_draft_unchecked))
            bits.append(f"draft {n}  y confirm  Esc cancel  Space toggle  c context")
        elif e.column_draft and p.screen == "roster":
            bits.append(
                f"draft {self._draft_n}  y ACCEPT selected  m same pair  Space select/deselect"
            )
        elif e.column_draft:
            bits.append(
                f"draft {self._draft_n}  y ACCEPT on roster  m same pair  Esc back (draft stays)"
            )
        elif e.pair_draft_col is not None:
            n = max(0, self._draft_n - len(self.pair_draft_unchecked))
            bits.append(f"draft {n}  y confirm  Esc cancel  Space toggle")
        if p.screen == "pair_list":
            bits.append("pair list")
        elif p.screen == "cell_step":
            bits.append("cell step")
        if p.page is not None and p.screen in {
            "pair_list",
            "cell_step",
            "accepted",
            "equal",
            "all_matched",
            "a_only",
            "b_only",
        }:
            bits.append(f"page {p.page + 1}/{self._page_count}")
        if e.last_refresh_delta:
            bits.append(e.last_refresh_delta.message)
        if p.focused_key:
            bits.append("key " + ", ".join(_display_text(x) for x in p.focused_key))
        spec = ""
        if p.screen in ("pair_list", "cell_step") and p.pair_val_a is not None:
            tags = e.cell_insights(p.pair_val_a, p.pair_val_b or "")
            if tags:
                spec = "  " + ", ".join(tags)
        bits.append("? help")
        footer.update(" · ".join(bits) + spec)

    def _run_busy(self, work) -> None:
        """Paint 'working…' for a real wait, then run work after the next refresh.

        Instant actions are not wrapped, so the footer does not flicker.
        """
        self._busy_note = "working…"
        try:
            self._render_footer()
        except Exception:
            pass

        def _go() -> None:
            try:
                work()
            finally:
                self._busy_note = None
                try:
                    self._render_footer()
                except Exception:
                    pass

        later = getattr(self, "call_later", None)
        if self.is_running and callable(later):
            later(_go)
        else:
            _go()

    def _render_pane(self) -> None:
        p = self.place
        pane = self.query_one("#pane", Static)
        pair = None
        if p.screen == "cell_step" and p.pair_val_a is not None:
            pair = (p.pair_val_a, p.pair_val_b or "")
        elif p.screen == "pair_list":
            pair = self._focused_pair()
            if pair is None and p.pair_val_a is not None:
                pair = (p.pair_val_a, p.pair_val_b or "")
        if pair is not None:
            va, vb = pair
            first = self.engine.first_diff(va, vb)
            t = Text()
            t.append_text(_diff_text("A:", va, vb, first))
            t.append("\n")
            t.append_text(_diff_text("B:", vb, va, first))
            if p.column:
                if p.screen == "cell_step":
                    key = p.focused_key
                    if not key or len(key) != len(self.engine.keys):
                        rec = self._focused_rec()
                        if rec:
                            key = self.engine.key_of(rec)
                    ctx = self.engine.context_values(key, p.column)
                    for name, a, b in ctx:
                        t.append(f"\n{name}  A|{a}  B|{b}")
                elif p.screen == "pair_list":
                    rec = self._focused_rec()
                    for view in self.engine.context_views(p.column):
                        summary = ""
                        if rec:
                            summary = str(rec.get(view.pair_key) or "")
                        t.append(f"\n\n{view.header[4:] if view.header.startswith('ctx:') else view.header}\n")
                        t.append(summary if summary else "(none)")
            pane.update(t)
        elif p.screen in ("a_only", "b_only"):
            rec = self._focused_rec()
            if rec:
                t = Text()
                for name, val in rec.items():
                    if str(name).startswith("_"):
                        continue
                    t.append(f"{name}: {_display_text(str(val))}\n")
                pane.update(t)
            else:
                pane.update("")
        elif p.screen == "extras":
            rec = self._focused_rec()
            if rec:
                tags = rec.get("speculative") or []
                spec = ", ".join(tags) if isinstance(tags, list) else str(tags)
                t = Text()
                t.append(f"Side {rec.get('side', '')}\n", style="bold")
                t.append(str(rec.get("name", "")))
                if spec:
                    line = _speculative_line(spec)
                    t.append(f"\n{line}", style="dim")
                pane.update(t)
            else:
                pane.update("")
        else:
            pane.update("")

    def _work_body(self):
        screen = self.place.screen
        if screen == "roster":
            return self._roster_table()
        if screen == "pair_list":
            return self._pair_view()
        if screen in ("cell_step", "accepted", "equal", "all_matched"):
            return self._cell_grid()
        if screen == "a_only":
            return self._unmatched("A")
        if screen == "b_only":
            return self._unmatched("B")
        if screen == "extras":
            return self._extras()
        return Static("Empty.")

    def _remount_work(self) -> None:
        work = self.query_one("#work", Vertical)
        for child in list(work.children):
            child.remove()
        body = self._work_body()
        # remove() is deferred. Roster and extras both use #grid as the work
        # child; wrapping a bare DataTable avoids DuplicateIds on the sibling.
        if isinstance(body, DataTable):
            body = Vertical(body)
        work.mount(body)

    def _refill_work(self) -> None:
        if not self.query("#grid"):
            self._remount_work()
            return
        screen = self.place.screen
        table = self.query_one("#grid", DataTable)
        if screen == "roster":
            self._fill_roster(table)
            return
        if screen == "pair_list":
            self._fill_pair_view(table)
            return
        if screen in ("cell_step", "accepted", "equal", "all_matched"):
            self._fill_cell_grid(table)
            return
        if screen == "a_only":
            self._fill_unmatched(table, "A")
            return
        if screen == "b_only":
            self._fill_unmatched(table, "B")
            return
        if screen == "extras":
            self._fill_extras(table)
            return
        self._remount_work()
        self._mounted_screen = self._work_layout_key()

    def _sync_columns(self, table: DataTable, headers: list[str]) -> None:
        current = [str(col.label) for col in table.columns.values()]
        if current != headers:
            table.clear(columns=True)
            self._add_columns(table, headers)
        else:
            table.clear()

    def _set_col_title(self, text: str) -> None:
        if self.query("#col-title"):
            self.query_one("#col-title", Static).update(text)

    def _refresh_tab_bar(self, current: str, pending_n: int | None = None) -> None:
        if not self.query("#tabs"):
            return
        pending_label = f"Pending {pending_n}" if pending_n is not None else "Pending"
        labels = [
            ("pending", pending_label),
            ("accepted", "Accepted"),
            ("equal", "Equal"),
            ("all_matched", "All matched"),
        ]
        for key, label in labels:
            btn = self.query_one(f"#tab-{key}", Button)
            btn.label = f"[{label}]" if key == current else label

    def _roster_headers(self) -> list[str]:
        headers: list[str] = []
        if self.engine.column_draft:
            headers.append("draft")
        headers.append("name")
        if self.show_accepted_columns:
            headers.append("status")
        headers.extend(["pending", "top-pair %", "equal", "cat", "speculative"])
        return headers

    def _context_names(self, column: str) -> list[str]:
        return self.engine.context_singles(column)

    def _cell_ctx_text(self, rec: dict[str, Any], view) -> str:
        if view.group_id is None:
            n = view.members[0]
            return f"A|{rec.get(f'{n}__ctx_a', '')}  B|{rec.get(f'{n}__ctx_b', '')}"
        a = format_context_tuple(
            [str(rec.get(f"{m}__ctx_a", "") or "") for m in view.members]
        )
        b = format_context_tuple(
            [str(rec.get(f"{m}__ctx_b", "") or "") for m in view.members]
        )
        return f"A|{a}  B|{b}"

    def _column_status(self, row: RosterRow) -> str:
        if row.pending > 0:
            return "pending"
        if row.accepted > 0:
            return "accepted"
        return "equal"

    def _add_columns(self, table: DataTable, headers: list[str]) -> None:
        for h in headers:
            if h == "pending":
                table.add_column(h, width=8)
            elif h == "status":
                table.add_column(h, width=10)
            elif h.startswith("ctx:"):
                table.add_column(h, width=max(18, min(36, len(h) + 16)))
            else:
                table.add_column(h)

    def _roster_table(self) -> DataTable:
        table: DataTable = DataTable(cursor_type="row", id="grid", zebra_stripes=False)
        self._add_columns(table, self._roster_headers())
        self._fill_roster(table)
        return table

    def _fill_roster(self, table: DataTable) -> None:
        headers = self._roster_headers()
        self._sync_columns(table, headers)
        rows = self.engine.column_roster(
            self.place.roster_filter, include_settled=self.show_accepted_columns
        )
        self._table_keys = []
        draft = bool(self.engine.column_draft)
        headers = self._roster_headers()
        if not rows:
            empty = [""] * len(headers)
            name_i = headers.index("name")
            if self.show_accepted_columns:
                empty[name_i] = (
                    "(no comparable columns — i overview for unmatched rows & mismatched columns)"
                )
            else:
                empty[name_i] = (
                    "(no pending columns — v show accepted · i overview for unmatched rows & mismatched columns)"
                )
            if "pending" in headers:
                empty[headers.index("pending")] = "0"
            table.add_row(*empty)
            self._table_keys = [None]
            return
        first_pending_i = next((i for i, r in enumerate(rows) if r.pending > 0), None)
        for i, r in enumerate(rows):
            settled = r.pending <= 0
            if draft:
                if settled:
                    check: str | Text = ""
                elif r.name in self.engine.column_draft:
                    check = Text("[ON]", style="bold")
                else:
                    check = Text("[off]", style="dim")
            styles = []
            if first_pending_i is not None and i == first_pending_i:
                styles.append("bold underline")
            if r.returned:
                styles.append("reverse")
            if settled:
                styles.append("dim")
            elif draft and r.name in self.engine.column_draft:
                styles.append("bold")
            elif draft:
                styles.append("dim")
            elif not settled:
                styles.append("bold #ffe27a")
            label = Text(r.name, style=" ".join(styles) if styles else "")
            cells: list[Any] = []
            if draft:
                cells.append(check)
            cells.append(label)
            if self.show_accepted_columns:
                status = self._column_status(r)
                if status == "pending":
                    cells.append(Text(status, style="bold #ffe27a"))
                elif status == "accepted":
                    cells.append(Text(status, style="dim #d2a87a"))
                else:
                    cells.append(Text(status, style="dim #e8b898"))
            cells.extend(
                [str(int(r.pending)), r.top_pair_pct, r.equal, r.categorical, r.speculative]
            )
            key = (r.kind, r.name, r.side)
            table.add_row(*cells, key=str(key))
            self._table_keys.append(r)
        idx = 0
        focus_name = self.place.focused_name
        for i, r in enumerate(rows):
            if focus_name and r.name == focus_name:
                idx = i
                break
        table.move_cursor(row=idx)

    def _tab_bar(self, current: str, pending_n: int | None = None) -> Horizontal:
        pending_label = f"Pending {pending_n}" if pending_n is not None else "Pending"
        labels = [
            ("pending", pending_label),
            ("accepted", "Accepted"),
            ("equal", "Equal"),
            ("all_matched", "All matched"),
        ]
        buttons = [
            Button(f"[{label}]" if key == current else label, id=f"tab-{key}")
            for key, label in labels
        ]
        return Horizontal(*buttons, id="tabs")

    def _column_pending_n(self, col: str) -> int:
        row = next(
            (r for r in self.engine.roster() if r.kind == "column" and r.name == col),
            None,
        )
        return int(row.pending) if row else 0

    def _pair_view(self) -> Vertical:
        col = self.place.column or ""
        if not col:
            return Vertical(Static("No column.", id="col-title"))
        pending_n = self._column_pending_n(col)
        table: DataTable = DataTable(cursor_type="row", id="grid")
        self._fill_pair_list(table, col)
        return Vertical(
            Static(f"COLUMN {col}   pending {pending_n}", id="col-title"),
            self._tab_bar("pending", pending_n),
            table,
        )

    def _fill_pair_view(self, table: DataTable) -> None:
        col = self.place.column or ""
        pending_n = self._column_pending_n(col)
        self._set_col_title(f"COLUMN {col}   pending {pending_n}")
        self._refresh_tab_bar("pending", pending_n)
        self._fill_pair_list(table, col)

    def _fill_pair_list(self, table: DataTable, col: str) -> None:
        views = self.engine.context_views(col)
        ctx_headers = [v.header for v in views]
        headers = ["A", "B", "pending", *ctx_headers, "speculative"]
        self._sync_columns(table, headers)
        recs, page, pages = self.engine.pair_page(col, self.place.page)
        self.place.page = page
        self._page_count = pages
        self._table_keys = []
        if not recs:
            table.add_row("(no pending pairs)", "", "0", *([""] * len(views)), "")
            self._table_keys = [None]
            return
        focus = 0
        want_a, want_b = self.place.pair_val_a, self.place.pair_val_b
        returned_hits = self.engine.pairs_returned_mask(
            col, [(rec["val_a"], rec["val_b"]) for rec in recs]
        )
        for i, rec in enumerate(recs):
            va, vb = rec["val_a"], rec["val_b"]
            tags = ", ".join(self.engine.cell_insights(va, vb))
            returned = bool(returned_hits[i]) if i < len(returned_hits) else False
            style = "reverse" if returned else "bold"
            label_a = Text(_display_text(va), style=style)
            label_b = Text(_display_text(vb), style=style)
            ctx_cells = [str(rec.get(v.pair_key) or "") for v in views]
            table.add_row(label_a, label_b, str(int(rec["n"])), *ctx_cells, tags)
            self._table_keys.append(rec)
            if want_a is not None and va == want_a and vb == want_b:
                focus = i
        table.move_cursor(row=focus)
        rec = recs[focus]
        self.place.pair_val_a, self.place.pair_val_b = rec["val_a"], rec["val_b"]

    def _move_cursor_to_focused_key(self, table: DataTable) -> None:
        want = self.place.focused_key
        if not want:
            return
        for i, rec in enumerate(self._table_keys):
            if isinstance(rec, dict) and self.engine.key_of(rec) == want:
                table.move_cursor(row=i)
                return

    def _follow_focused_key_page(self, lookup) -> None:
        """Jump page from focused_key only when that key is not on the current page.

        After an accept, the handler already sets page; this still lands a newly
        assigned focused_key. n/p clear focused_key so render cannot yank back.
        """
        want = self.place.focused_key
        if not want:
            return
        key_page, _ = lookup(want)
        if key_page != self.place.page:
            self.place.page = key_page

    def _cell_grid(self) -> Vertical:
        col = self.place.column or ""
        tab = self.place.view_tab
        title = {
            "pending": "Pending (cell step)",
            "accepted": "Accepted",
            "equal": "Equal",
            "all_matched": "All matched",
        }.get(tab, tab)
        table: DataTable = DataTable(cursor_type="row", id="grid")
        self._fill_cell_rows(table, col)
        tab_current = tab if self.place.screen != "cell_step" else "pending"
        pending_n = self._column_pending_n(col)
        return Vertical(
            Static(f"COLUMN {col}   {title}   pending {pending_n}", id="col-title"),
            self._tab_bar(tab_current, pending_n),
            table,
        )

    def _fill_cell_grid(self, table: DataTable) -> None:
        col = self.place.column or ""
        tab = self.place.view_tab
        title = {
            "pending": "Pending (cell step)",
            "accepted": "Accepted",
            "equal": "Equal",
            "all_matched": "All matched",
        }.get(tab, tab)
        pending_n = self._column_pending_n(col)
        self._set_col_title(f"COLUMN {col}   {title}   pending {pending_n}")
        tab_current = tab if self.place.screen != "cell_step" else "pending"
        self._refresh_tab_bar(tab_current, pending_n)
        self._fill_cell_rows(table, col)

    def _fill_cell_rows(self, table: DataTable, col: str) -> None:
        tab = self.place.view_tab
        views = self.engine.context_views(col)
        ctx_headers = [v.header for v in views]
        headers = [*self.engine.keys, "A", "B", *ctx_headers, "speculative"]
        self._sync_columns(table, headers)
        self._table_keys = []
        if self.place.screen == "cell_step":
            va, vb = self.place.pair_val_a or "", self.place.pair_val_b or ""
            self._follow_focused_key_page(self.engine.page_index_for_pair_key)
            recs, page, pages = self.engine.pair_cells_page(col, va, vb, self.place.page)
            self.place.page = page
            self._page_count = pages
            draft = self.engine.pair_draft_col is not None
            if not recs:
                table.add_row(*([""] * (len(headers) - 1)), "(no pending cells for this pair)")
                self._table_keys = [None]
                return
            focus_i = 0
            if self.place.focused_key:
                for i, rec in enumerate(recs):
                    if self.engine.key_of(rec) == self.place.focused_key:
                        focus_i = i
                        break
            for i, rec in enumerate(recs):
                key = self.engine.key_of(rec)
                mark = ""
                if draft:
                    mark = "[x] " if key not in self.pair_draft_unchecked else "[ ] "
                returned = bool(rec.get("_returned"))
                tags = ", ".join(self.engine.cell_insights(rec["val_a"], rec["val_b"]))
                key_cells = [_display_text(str(rec[k])) for k in self.engine.keys]
                ctx_cells = [self._cell_ctx_text(rec, v) for v in views]
                if i == focus_i:
                    fd = self.engine.first_diff(rec["val_a"], rec["val_b"])
                    va_t = Text(mark)
                    va_t.append_text(_diff_text("", rec["val_a"], rec["val_b"], fd))
                    vb_t = _diff_text("", rec["val_b"], rec["val_a"], fd)
                else:
                    va_t = Text(
                        mark + rec["val_a"],
                        style="reverse" if returned or (draft and key not in self.pair_draft_unchecked) else "bold",
                    )
                    vb_t = rec["val_b"]
                table.add_row(*key_cells, va_t, vb_t, *ctx_cells, tags)
                self._table_keys.append(rec)
            self._move_cursor_to_focused_key(table)
            return
        recs, page, pages = self.engine.cells_for_tab(col, tab, self.place.page)
        self.place.page = page
        self._page_count = pages
        if not recs:
            table.add_row(*([""] * (len(headers) - 1)), "(empty)")
            self._table_keys = [None]
            return
        focus_i = 0
        if self.place.focused_key:
            for i, rec in enumerate(recs):
                if self.engine.key_of(rec) == self.place.focused_key:
                    focus_i = i
                    break
        for i, rec in enumerate(recs):
            tags = ""
            if rec.get("val_a") != rec.get("val_b"):
                tags = ", ".join(self.engine.cell_insights(rec["val_a"], rec["val_b"]))
            key_cells = [_display_text(str(rec[k])) for k in self.engine.keys]
            ctx_cells = [self._cell_ctx_text(rec, v) for v in views]
            if i == focus_i and rec.get("val_a") != rec.get("val_b"):
                fd = self.engine.first_diff(str(rec["val_a"]), str(rec["val_b"]))
                va_t = _diff_text("", str(rec["val_a"]), str(rec["val_b"]), fd)
                vb_t = _diff_text("", str(rec["val_b"]), str(rec["val_a"]), fd)
            else:
                style = "dim" if rec.get("val_a") == rec.get("val_b") else "bold"
                va_t = Text(str(rec["val_a"]), style=style)
                vb_t = str(rec["val_b"])
            table.add_row(*key_cells, va_t, vb_t, *ctx_cells, tags)
            self._table_keys.append(rec)
        self._move_cursor_to_focused_key(table)

    def _unmatched(self, side: str) -> Vertical:
        table: DataTable = DataTable(cursor_type="row", id="grid")
        pending_n = (
            self.engine.pending_a_only_n() if side == "A" else self.engine.pending_b_only_n()
        )
        self._fill_unmatched(table, side)
        return Vertical(
            Static(
                f"{side}-only keys  pending {pending_n}  (raw columns on this side, including extras)",
                id="col-title",
            ),
            table,
        )

    def _fill_unmatched(self, table: DataTable, side: str) -> None:
        self._follow_focused_key_page(
            lambda key: self.engine.page_index_for_unmatched_key(side, key)
        )
        recs, page, pages = self.engine.unmatched_page(side, self.place.page)
        self.place.page = page
        self._page_count = pages
        frame = self.engine.a_only if side == "A" else self.engine.b_only
        cols = list(frame.columns)
        headers = ["st", *cols]
        current = [str(col.label) for col in table.columns.values()]
        if current != headers:
            table.clear(columns=True)
            table.add_columns(*headers)
        else:
            table.clear()
        self._table_keys = []
        if not recs:
            table.add_row(" ", *([""] * len(cols) or ["(none)"]))
            self._table_keys = [None]
        else:
            for rec in recs:
                st = "acc" if rec.get("_accepted") else "pend"
                style = "dim" if rec.get("_accepted") else "bold"
                if rec.get("_returned"):
                    style = "reverse"
                key_names = set(self.engine.keys)
                vals = [
                    Text(
                        _display_text(str(rec[c])) if c in key_names else str(rec[c]),
                        style=style,
                    )
                    for c in cols
                ]
                table.add_row(st, *vals)
                self._table_keys.append(rec)
            self._move_cursor_to_focused_key(table)
        pending_n = (
            self.engine.pending_a_only_n() if side == "A" else self.engine.pending_b_only_n()
        )
        self._set_col_title(
            f"{side}-only keys  pending {pending_n}  (raw columns on this side, including extras)"
        )

    def _extras(self) -> Vertical:
        table: DataTable = DataTable(cursor_type="row", id="grid")
        self._fill_extras(table)
        return Vertical(
            Static(
                f"Mismatched columns  pending {self.engine.pending_extras_n()}  (header on one side only)",
                id="col-title",
            ),
            table,
        )

    def _fill_extras(self, table: DataTable) -> None:
        self._sync_columns(table, ["side", "name", "pending", "speculative"])
        rows = self.engine.extras_rows()
        self._table_keys = []
        if not rows:
            table.add_row("—", "(no extras)", "0", "")
            self._table_keys = [None]
        else:
            for rec in rows:
                style = "reverse" if rec["returned"] else ("bold" if rec["pending"] else "dim")
                table.add_row(
                    rec["side"],
                    Text(rec["name"], style=style),
                    str(int(rec["pending"])),
                    ", ".join(rec["speculative"]),
                )
                self._table_keys.append(rec)
            idx = 0
            want_side, want_name = self.place.extra_side, self.place.extra_name
            if want_name:
                for i, rec in enumerate(rows):
                    if rec["name"] != want_name:
                        continue
                    if want_side and rec["side"] != want_side:
                        continue
                    idx = i
                    break
            table.move_cursor(row=idx)
        self._set_col_title(
            f"Mismatched columns  pending {self.engine.pending_extras_n()}  (header on one side only)"
        )

    def _focused_roster(self) -> RosterRow | None:
        if not self._table_keys:
            return None
        if not self.query("#grid"):
            return None
        table = self.query_one("#grid", DataTable)
        i = table.cursor_row
        if i < 0 or i >= len(self._table_keys):
            return None
        row = self._table_keys[i]
        return row if isinstance(row, RosterRow) else None

    def _focused_rec(self) -> dict[str, Any] | None:
        if not self._table_keys:
            return None
        if not self.query("#grid"):
            return None
        table = self.query_one("#grid", DataTable)
        i = table.cursor_row
        if i < 0 or i >= len(self._table_keys):
            return None
        rec = self._table_keys[i]
        return rec if isinstance(rec, dict) else None

    def _focused_pair(self) -> tuple[str, str] | None:
        rec = self._focused_rec()
        if rec and "val_a" in rec:
            return rec["val_a"], rec["val_b"]
        return None

    def action_help(self) -> None:
        self.push_screen(HelpModal())

    def action_overview(self) -> None:
        if self._modal_active():
            return

        def done(entry: str | None) -> None:
            if not entry:
                self.set_focus_work()
                return
            p = self.place
            if entry == "a_only":
                self.place = Place(screen="a_only", roster_filter=p.roster_filter, last_pair=p.last_pair)
            elif entry == "b_only":
                self.place = Place(screen="b_only", roster_filter=p.roster_filter, last_pair=p.last_pair)
            else:
                self.place = Place(screen="extras", roster_filter=p.roster_filter, last_pair=p.last_pair)
            self.set_error(None)
            self.render_all()
            self.set_focus_work()

        self.push_screen(OverviewModal(self.engine), done)

    def action_quit_app(self) -> None:
        self.engine.cancel_drafts()
        self.pair_draft_unchecked = set()
        code = 0 if self.engine.pending_total() == 0 else 1
        self.exit(code)

    def action_back(self) -> None:
        e = self.engine
        p = self.place
        if p.screen == "cell_step":
            e.clear_pair_draft()
            self.pair_draft_unchecked = set()
            self.place = Place(
                screen="pair_list",
                column=p.column,
                roster_filter=p.roster_filter,
                last_pair=p.last_pair,
                view_tab="pending",
            )
        elif self.engine.column_draft and p.screen == "roster":
            self.engine.column_draft = set()
        elif p.screen in ("pair_list", "a_only", "b_only", "extras", "accepted", "equal", "all_matched"):
            self.place = Place(
                screen="roster",
                roster_filter=p.roster_filter,
                last_pair=p.last_pair,
                focused_name=p.column or p.focused_name,
            )
        elif p.screen == "overview":
            self.place = Place(
                screen="roster",
                roster_filter=p.roster_filter,
                last_pair=p.last_pair,
                focused_name=p.focused_name,
            )
        # roster Esc: stay (overview is i, not a screen)
        self.render_all()
        self.set_focus_work()

    def action_drill(self) -> None:
        e = self.engine
        p = self.place
        try:
            if p.screen == "roster":
                row = self._focused_roster()
                if not row:
                    return
                if row.kind == "column":
                    if row.pending > 0:
                        self.place = Place(
                            screen="pair_list",
                            column=row.name,
                            roster_filter=p.roster_filter,
                            last_pair=p.last_pair,
                            focused_name=row.name,
                        )
                    elif row.accepted > 0:
                        self.place = Place(
                            screen="accepted",
                            column=row.name,
                            roster_filter=p.roster_filter,
                            last_pair=p.last_pair,
                            focused_name=row.name,
                            view_tab="accepted",
                        )
                    else:
                        self.place = Place(
                            screen="equal",
                            column=row.name,
                            roster_filter=p.roster_filter,
                            last_pair=p.last_pair,
                            focused_name=row.name,
                            view_tab="equal",
                        )
                else:
                    raise InTuiError("ERROR: column roster only drills comparable columns")
            elif p.screen == "pair_list":
                if e.column_draft:
                    raise InTuiError("ERROR: confirm or cancel the column draft first")
                pair = self._focused_pair()
                if not pair or not p.column:
                    raise InTuiError("ERROR: no pending pairs")
                n = e.start_pair_draft(p.column, pair[0], pair[1])
                if n == 0:
                    self.set_error("ERROR: that pair has no pending cells")
                    return
                self.pair_draft_unchecked = set()
                last = (p.column, pair[0], pair[1])
                focused = e.first_pending_key_in_pair(p.column, pair[0], pair[1])
                self.place = Place(
                    screen="cell_step",
                    column=p.column,
                    pair_val_a=pair[0],
                    pair_val_b=pair[1],
                    roster_filter=p.roster_filter,
                    last_pair=last,
                    view_tab="pending",
                    focused_key=focused,
                )
            self.set_error(None)
        except InTuiError as exc:
            self.set_error(exc.message)
            self._render_footer()
            return
        self.render_all()
        self.set_focus_work()

    def action_toggle(self) -> None:
        e = self.engine
        p = self.place
        if p.screen == "roster":
            if not e.column_draft:
                self.set_error("ERROR: no column draft to toggle")
                return
            row = self._focused_roster()
            if row and row.kind == "column":
                self.engine.toggle_column_draft(row.name)
                if not self.engine.column_draft:
                    self.set_error("ERROR: draft empty — cancelled")
                    self.render_all()
                    self.set_focus_work()
                    return
        elif p.screen == "cell_step":
            rec = self._focused_rec()
            if rec:
                key = e.key_of(rec)
                if key in self.pair_draft_unchecked:
                    self.pair_draft_unchecked.discard(key)
                else:
                    self.pair_draft_unchecked.add(key)
        else:
            self.set_error("ERROR: Space toggles a live draft")
            return
        self.render_all()
        self.set_focus_work()

    def action_accept(self) -> None:
        e = self.engine
        p = self.place
        try:
            if p.screen == "overview":
                raise InTuiError("ERROR: not remaining work")
            if p.screen in ("accepted", "equal", "all_matched"):
                raise InTuiError("ERROR: not remaining work; switch to Pending")
            if p.screen == "roster":
                row = self._focused_roster()
                if not row:
                    return
                if row.kind != "column":
                    raise InTuiError("ERROR: column roster accepts columns only")
                if row.pending == 0:
                    raise InTuiError("ERROR: no pending cells in this column")
                old_names = [
                    r.name
                    for r in e.column_roster(
                        p.roster_filter, include_settled=self.show_accepted_columns
                    )
                ]
                n = e.accept_column(row.name)
                e.remember_grain(("column", row.name), n)
                self._stay_on_roster_after_column(row.name, old_names)
            elif p.screen == "pair_list":
                if e.column_draft:
                    raise InTuiError("ERROR: confirm or cancel the column draft first")
                pair = self._focused_pair()
                if not pair or not p.column:
                    raise InTuiError("ERROR: no pending pairs")
                nxt = e.next_pair_below(p.column, pair[0], pair[1])
                n = e.accept_pair(p.column, pair[0], pair[1])
                if n == 0:
                    raise InTuiError("ERROR: no pending pairs")
                last = (p.column, pair[0], pair[1])
                e.remember_grain(("pair", p.column, pair[0], pair[1]), n)
                page = 0
                if nxt:
                    page, _ = e.page_index_for_pair(p.column, nxt[0], nxt[1])
                self.place = Place(
                    screen="pair_list",
                    column=p.column,
                    pair_val_a=nxt[0] if nxt else None,
                    pair_val_b=nxt[1] if nxt else None,
                    page=page,
                    roster_filter=p.roster_filter,
                    last_pair=last,
                    view_tab="pending",
                    focused_name=p.column,
                )
            elif p.screen == "cell_step":
                rec = self._focused_rec()
                if rec and p.column:
                    key = e.key_of(rec)
                    n = e.accept_cell(key, p.column, rec["val_a"], rec["val_b"])
                    e.remember_grain(("cell", key, p.column), n)
                    self.pair_draft_unchecked.discard(key)
                    nxt = e.next_pending_cell_in_pair(key)
                    if nxt is None:
                        e.clear_pair_draft()
                        self.pair_draft_unchecked = set()
                        self.place = Place(
                            screen="pair_list",
                            column=p.column,
                            roster_filter=p.roster_filter,
                            last_pair=p.last_pair,
                            view_tab="pending",
                            focused_name=p.column,
                        )
                    else:
                        self.place.focused_key = nxt
                        page, _ = e.page_index_for_pair_key(nxt)
                        self.place.page = page
            elif p.screen == "a_only":
                rec = self._focused_rec()
                if rec:
                    key = e.key_of(rec)
                    n = e.accept_unmatched("A", key)
                    if n == 0:
                        raise InTuiError("ERROR: that key is not pending")
                    e.remember_grain(("unmatched", "A", key), n)
                    nxt = e.next_pending_key_in_grid("A", key)
                    self.place.focused_key = nxt
                    if nxt:
                        page, _ = e.page_index_for_unmatched_key("A", nxt)
                        self.place.page = page
            elif p.screen == "b_only":
                rec = self._focused_rec()
                if rec:
                    key = e.key_of(rec)
                    n = e.accept_unmatched("B", key)
                    if n == 0:
                        raise InTuiError("ERROR: that key is not pending")
                    e.remember_grain(("unmatched", "B", key), n)
                    nxt = e.next_pending_key_in_grid("B", key)
                    self.place.focused_key = nxt
                    if nxt:
                        page, _ = e.page_index_for_unmatched_key("B", nxt)
                        self.place.page = page
            elif p.screen == "extras":
                rec = self._focused_rec()
                if rec:
                    old_ids = [(r["side"], r["name"]) for r in e.extras_rows()]
                    accepted = (rec["side"], rec["name"])
                    n = e.accept_extra(rec["side"], rec["name"])
                    if n == 0:
                        raise InTuiError("ERROR: extra is not pending")
                    e.remember_grain(("extra", rec["side"], rec["name"]), n)
                    new_ids = [(r["side"], r["name"]) for r in e.extras_rows()]
                    nxt = select_after_accept(old_ids, accepted, new_ids)
                    self.place = Place(
                        screen="extras",
                        roster_filter=p.roster_filter,
                        last_pair=p.last_pair,
                        extra_side=nxt[0] if nxt else None,
                        extra_name=nxt[1] if nxt else None,
                        focused_name=nxt[1] if nxt else None,
                    )
            self.set_error(None)
        except InTuiError as exc:
            self.set_error(exc.message)
            return
        self.render_all()
        self.set_focus_work()

    def action_accept_all(self) -> None:
        e = self.engine
        p = self.place
        try:
            if p.screen in ("accepted", "equal", "all_matched", "overview"):
                raise InTuiError("ERROR: not remaining work")
            if e.pair_draft_col is not None and p.screen in ("pair_list", "cell_step"):
                raise InTuiError("ERROR: confirm or cancel the pair draft first")
            if e.column_draft and p.screen in ("pair_list", "cell_step"):
                raise InTuiError("ERROR: confirm or cancel the column draft first")
            if p.screen == "roster":
                row = self._focused_roster()
                if not row:
                    return
                if row.kind != "column":
                    raise InTuiError("ERROR: column roster accepts columns only")
                if row.pending == 0:
                    raise InTuiError("ERROR: no pending cells in this column")
                old_names = [
                    r.name
                    for r in e.column_roster(
                        p.roster_filter, include_settled=self.show_accepted_columns
                    )
                ]
                n = e.accept_column(row.name)
                e.remember_grain(("column", row.name), n)
                self._stay_on_roster_after_column(row.name, old_names)
            elif p.screen in ("pair_list", "cell_step"):
                if p.column:
                    n_pend = e.pending_cells.filter(pl.col("column") == p.column).height
                    if n_pend == 0:
                        raise InTuiError("ERROR: no pending cells in this column")
                    n = e.accept_column(p.column)
                    e.remember_grain(("column", p.column), n)
                    self.place = e.next_lever_place(Place(column=p.column, roster_filter=p.roster_filter, last_pair=p.last_pair))
            elif p.screen == "a_only":
                n = e.accept_all_unmatched("A")
                if n == 0:
                    raise InTuiError("ERROR: no pending A-only keys")
                e.remember_grain(("unmatched_all", "A"), n)
                self.place = e.next_lever_place(p)
            elif p.screen == "b_only":
                n = e.accept_all_unmatched("B")
                if n == 0:
                    raise InTuiError("ERROR: no pending B-only keys")
                e.remember_grain(("unmatched_all", "B"), n)
                self.place = e.next_lever_place(p)
            elif p.screen == "extras":
                rec = self._focused_rec()
                if rec:
                    old_ids = [(r["side"], r["name"]) for r in e.extras_rows()]
                    accepted = (rec["side"], rec["name"])
                    n = e.accept_extra(rec["side"], rec["name"])
                    if n == 0:
                        raise InTuiError("ERROR: extra is not pending")
                    e.remember_grain(("extra", rec["side"], rec["name"]), n)
                    new_ids = [(r["side"], r["name"]) for r in e.extras_rows()]
                    nxt = select_after_accept(old_ids, accepted, new_ids)
                    self.place = Place(
                        screen="extras",
                        roster_filter=p.roster_filter,
                        last_pair=p.last_pair,
                        extra_side=nxt[0] if nxt else None,
                        extra_name=nxt[1] if nxt else None,
                        focused_name=nxt[1] if nxt else None,
                    )
            self.set_error(None)
        except InTuiError as exc:
            self.set_error(exc.message)
            return
        self.render_all()
        self.set_focus_work()

    def _stay_on_roster_after_column(
        self, accepted_name: str, old_names: list[str] | None = None
    ) -> None:
        """Roster `a` accepts in place: hide the column (unless v) and stay home."""
        p = self.place
        rows = self.engine.column_roster(
            p.roster_filter, include_settled=self.show_accepted_columns
        )
        new_names = [r.name for r in rows]
        nxt = select_after_accept(old_names or new_names, accepted_name, new_names)
        self.place = Place(
            screen="roster",
            roster_filter=p.roster_filter,
            last_pair=p.last_pair,
            focused_name=nxt,
        )

    def action_toggle_accepted(self) -> None:
        if self._modal_active():
            return
        if self.place.screen != "roster":
            self.set_error("ERROR: show accepted is a roster toggle")
            return
        self.show_accepted_columns = not self.show_accepted_columns
        self.set_error(None)
        self.render_all()
        self.set_focus_work()

    def action_confirm(self) -> None:
        e = self.engine
        p = self.place
        try:
            if e.pair_draft_col is not None:
                col, va, vb = e.pair_draft_col, e.pair_draft_va, e.pair_draft_vb
                n = e.confirm_pair_draft(self.pair_draft_unchecked)
                e.remember_grain(("pair", col, va or "", vb or ""), n)
                self.pair_draft_unchecked = set()
                last = (col, va or "", vb or "")
                self.place = e.next_lever_place(
                    Place(column=col, roster_filter=p.roster_filter, last_pair=last)
                )
            elif e.column_draft:
                if p.screen != "roster":
                    raise InTuiError("ERROR: confirm or cancel the column draft first")
                old_names = [
                    r.name
                    for r in e.column_roster(
                        p.roster_filter, include_settled=self.show_accepted_columns
                    )
                ]
                drafted = [name for name in old_names if name in e.column_draft]
                if not drafted:
                    drafted = sorted(e.column_draft)
                names = tuple(drafted)

                def _confirm_columns() -> None:
                    n = e.confirm_column_draft()
                    e.remember_grain(("columns", *names), n)
                    self._stay_on_roster_after_column(drafted[-1], old_names)
                    self.set_error(None)
                    self.render_all()
                    self.set_focus_work()

                self._run_busy(_confirm_columns)
                return
            self.set_error(None)
        except InTuiError as exc:
            self.set_error(exc.message)
            return
        self.render_all()
        self.set_focus_work()

    def _undo_focused(self) -> int:
        e = self.engine
        p = self.place
        if p.screen == "roster":
            row = self._focused_roster()
            if not row:
                return 0
            if row.kind == "column":
                return e.undo_column(row.name)
            return 0
        if p.screen == "pair_list":
            pair = self._focused_pair()
            if pair and p.column:
                return e.undo_pair(p.column, pair[0], pair[1])
            return 0
        if p.screen == "cell_step":
            rec = self._focused_rec()
            if rec and p.column:
                return e.undo_cell(e.key_of(rec), p.column)
            return 0
        if p.screen == "a_only":
            rec = self._focused_rec()
            if rec:
                return e.undo_unmatched("A", e.key_of(rec))
            return 0
        if p.screen == "b_only":
            rec = self._focused_rec()
            if rec:
                return e.undo_unmatched("B", e.key_of(rec))
            return 0
        if p.screen == "extras":
            rec = self._focused_rec()
            if rec:
                return e.undo_extra(rec["side"], rec["name"])
            return 0
        return 0

    def action_undo(self) -> None:
        e = self.engine
        try:
            if e.last_grain is not None:
                grain = e.last_grain
                n = e.undo_last_grain()
                if n == 0:
                    raise InTuiError("ERROR: nothing snapshotted to undo")
                self._focus_grain(grain)
            else:
                n = self._undo_focused()
                if n == 0:
                    raise InTuiError("ERROR: nothing snapshotted to undo")
            self.set_error(None)
        except InTuiError as exc:
            self.set_error(exc.message)
            return
        self.render_all()
        self.set_focus_work()

    def _focus_grain(self, grain: tuple[Any, ...]) -> None:
        """After undo-last, stay/return to that grain if it still exists as a place."""
        p = self.place
        rf, lp = p.roster_filter, p.last_pair
        kind = grain[0]
        if kind == "cell":
            key, col = grain[1], str(grain[2])
            if p.screen == "cell_step" and p.column == col and isinstance(key, tuple):
                self.place.focused_key = key
                return
            self.place = Place(
                screen="pair_list",
                column=col,
                roster_filter=rf,
                last_pair=lp,
                focused_name=col,
            )
        elif kind == "pair":
            col, va, vb = str(grain[1]), str(grain[2]), str(grain[3])
            self.place = Place(
                screen="pair_list",
                column=col,
                pair_val_a=va,
                pair_val_b=vb,
                roster_filter=rf,
                last_pair=lp,
                focused_name=col,
            )
        elif kind == "column":
            col = str(grain[1])
            self.place = Place(
                screen="pair_list",
                column=col,
                roster_filter=rf,
                last_pair=lp,
                focused_name=col,
            )
        elif kind == "columns":
            self.place = Place(screen="roster", roster_filter=rf, last_pair=lp)
        elif kind == "pairs":
            va, vb = str(grain[1]), str(grain[2])
            col = str(grain[3]) if len(grain) > 3 else (p.column or "")
            self.place = Place(
                screen="pair_list",
                column=col or p.column,
                pair_val_a=va,
                pair_val_b=vb,
                roster_filter=rf,
                last_pair=lp,
                focused_name=col or p.column,
            )
        elif kind == "unmatched":
            side, key = str(grain[1]), grain[2]
            self.place = Place(
                screen="a_only" if side == "A" else "b_only",
                roster_filter=rf,
                last_pair=lp,
                focused_key=key if isinstance(key, tuple) else None,
            )
        elif kind == "unmatched_all":
            side = str(grain[1])
            self.place = Place(
                screen="a_only" if side == "A" else "b_only",
                roster_filter=rf,
                last_pair=lp,
            )
        elif kind == "extra":
            self.place = Place(
                screen="extras",
                extra_side=str(grain[1]),
                extra_name=str(grain[2]),
                roster_filter=rf,
                last_pair=lp,
                focused_name=str(grain[2]),
            )

    def action_undo_column(self) -> None:
        p = self.place
        if self.engine.pair_draft_col is not None:
            self.set_error("ERROR: confirm or cancel the pair draft first")
            return
        if p.screen != "pair_list":
            self.set_error("ERROR: U undoes the entire column on the pair list only")
            return
        if not p.column:
            self.set_error("ERROR: U undoes the entire column on the pair list only")
            return
        n = self.engine.undo_column(p.column)
        if n == 0:
            self.set_error("ERROR: nothing snapshotted to undo")
            return
        g = self.engine.last_grain
        if g is not None and _grain_involves_column(g, p.column):
            self.engine.last_grain = None
        self.set_error(None)
        self.render_all()
        self.set_focus_work()

    def _sync_pair_draft_after_refresh(self, frozen: pl.DataFrame) -> None:
        """New matching keys stay unchecked; vanished keys leave the draft set."""
        e = self.engine
        if e.pair_draft_col is None:
            self.pair_draft_unchecked = set()
            return
        live = e.pair_draft_key_frame()
        keys = e.keys
        still: set[tuple[str, ...]] = set()
        if self.pair_draft_unchecked:
            data = {k: [key[i] for key in self.pair_draft_unchecked] for i, k in enumerate(keys)}
            still = _key_tuples(pl.DataFrame(data).join(live, on=keys, how="inner"), keys)
        appeared = live.join(frozen, on=keys, how="anti")
        still |= _key_tuples(appeared, keys)
        self.pair_draft_unchecked = still

    def action_refresh(self) -> None:
        def _refresh() -> None:
            try:
                frozen = None
                if self.engine.pair_draft_col is not None:
                    frozen = self.engine.pair_draft_key_frame()
                self.engine.refresh()
                if frozen is not None:
                    self._sync_pair_draft_after_refresh(frozen)
                p = self.engine.prune_place(self.place, self.engine.pair_draft_col is not None)
                self.place = p
                still = True
                if p.screen in ("pair_list", "cell_step", "accepted", "equal", "all_matched"):
                    if not p.column or p.column not in self.engine.comparable:
                        still = False
                if p.screen == "cell_step" and self.engine.pair_draft_col is None:
                    still = False
                    self.place.screen = "pair_list"
                if not still:
                    self.place = self.engine.next_lever_place(p)
                self.set_error(None)
            except InTuiError as exc:
                self.set_error(exc.message)
            self.render_all()
            self.set_focus_work()

        self._run_busy(_refresh)

    def _is_paged_screen(self) -> bool:
        return self.place.screen in {
            "pair_list",
            "cell_step",
            "accepted",
            "equal",
            "all_matched",
            "a_only",
            "b_only",
        }

    def action_page_next(self) -> None:
        if not self._is_paged_screen():
            self.set_error("ERROR: no pages on this screen")
            return
        if self.place.page + 1 >= self._page_count:
            self.set_error("ERROR: last page")
            return
        self.place.page += 1
        # Stop render from following the accepted row back onto its old page.
        self.place.focused_key = None
        self.set_error(None)
        self.render_all()
        self.set_focus_work()

    def action_page_prev(self) -> None:
        if not self._is_paged_screen():
            self.set_error("ERROR: no pages on this screen")
            return
        if self.place.page <= 0:
            self.set_error("ERROR: first page")
            return
        self.place.page -= 1
        # Stop render from following the accepted row back onto its old page.
        self.place.focused_key = None
        self.set_error(None)
        self.render_all()
        self.set_focus_work()

    def action_regex(self) -> None:
        if self._in_input():
            return
        if self.place.screen != "roster":
            self.set_error("ERROR: regex column draft is only on the roster")
            return
        if self.draft_in_flight():
            self.set_error("ERROR: confirm or cancel the current draft first")
            self.render_all()
            return

        def done(pat: str | None) -> None:
            if pat is None:
                return

            def _run() -> None:
                try:
                    self.engine.start_regex_draft(pat)
                    self.place.focused_name = sorted(self.engine.column_draft)[0]
                    self.set_error(None)
                except InTuiError as exc:
                    self.set_error(exc.message)
                self.render_all()
                self.set_focus_work()

            self._run_busy(_run)

        self.push_screen(RegexModal(), done)

    def action_sentinel(self) -> None:
        if self._in_input():
            return
        if self.place.screen != "roster":
            self.set_error("ERROR: exact sentinel is only on the roster")
            return
        if self.draft_in_flight():
            self.set_error("ERROR: confirm or cancel the current draft first")
            self.render_all()
            return

        def done(result: tuple[str, str] | None) -> None:
            if result is None:
                return
            side, sentinel = result

            def _run() -> None:
                try:
                    self.engine.start_sentinel_draft(side, sentinel)
                    self.place.focused_name = sorted(self.engine.column_draft)[0]
                    self.set_error(None)
                except InTuiError as exc:
                    self.set_error(exc.message)
                self.render_all()
                self.set_focus_work()

            self._run_busy(_run)

        self.push_screen(SentinelModal(), done)

    def action_repeat_pair(self) -> None:
        e = self.engine
        p = self.place
        if p.screen not in ("pair_list", "cell_step"):
            self.set_error("ERROR: repeat last pair is only on column detail")
            return
        if self.draft_in_flight():
            self.set_error("ERROR: confirm or cancel the current draft first")
            self.render_all()
            return
        lp = self.place.last_pair
        if not lp:
            self.set_error("ERROR: no last pair to repeat")
            self.render_all()
            return
        col, va, vb = lp
        if col not in e.comparable:
            self.set_error("ERROR: last-pair column is gone")
            self.render_all()
            return
        if not (p.screen == "pair_list" and p.column == col):
            landed = e.place_from_last_pair(lp, roster_filter=p.roster_filter)
            if landed is None:
                self.place = e.next_lever_place(
                    Place(column=col, roster_filter=p.roster_filter, last_pair=lp)
                )
            else:
                self.place = landed
            self.set_error(None)
            self.render_all()
            self.set_focus_work()
            return
        try:
            n = e.start_pair_draft(col, va, vb)
        except InTuiError as exc:
            self.set_error(exc.message)
            self.render_all()
            return
        if n == 0:
            self.place = e.next_lever_place(Place(column=col, roster_filter=self.place.roster_filter, last_pair=lp))
        else:
            self.pair_draft_unchecked = set()
            focused = e.first_pending_key_in_pair(col, va, vb)
            self.place = Place(
                screen="cell_step",
                column=col,
                pair_val_a=va,
                pair_val_b=vb,
                roster_filter=self.place.roster_filter,
                last_pair=lp,
                view_tab="pending",
                focused_key=focused,
            )
        self.set_error(None)
        self.render_all()
        self.set_focus_work()

    def action_context(self) -> None:
        e = self.engine
        if self.place.screen not in {
            "pair_list",
            "cell_step",
            "accepted",
            "equal",
            "all_matched",
        } or not self.place.column:
            self.set_error("ERROR: context columns are only on column detail")
            return
        col = self.place.column
        names = [n for n in e.context_pool if n != col]
        selected = set(e.context_columns.get(col, []))
        groups = e.context_groups.get(col, {})

        def done(result: ContextPick | None) -> None:
            if result is None:
                return
            e.context_columns[col] = result.singles
            e.context_groups[col] = result.groups
            e._pair_ctx_by_col.pop(col, None)
            self.render_all()
            self.set_focus_work()

        self.push_screen(ContextModal(names, selected, groups), done)

    def action_multi_pair(self) -> None:
        if self._in_input() or self._modal_active():
            return
        p = self.place
        if p.screen not in ("roster", "pair_list"):
            self.set_error("ERROR: same-pair accept is only on the roster or pair list")
            return
        if self.engine.pair_draft_col is not None:
            self.set_error("ERROR: confirm or cancel the current draft first")
            self.render_all()
            return
        skip_column_pick = bool(self.engine.column_draft)
        if skip_column_pick:
            on_names = set(self.engine.column_draft)
            columns = [
                r.name
                for r in self.engine.visible_column_roster(p.roster_filter)
                if r.name in on_names
            ]
            if not columns:
                self.set_error("ERROR: no ON columns in the current draft for same-pair accept")
                self.render_all()
                return
        else:
            columns = [r.name for r in self.engine.visible_column_roster(p.roster_filter)]
            if not columns:
                self.set_error("ERROR: no pending columns for same-pair accept")
                return
        if skip_column_pick:
            pairs, _, _ = self.engine.union_pairs(columns)
            if not pairs:
                self.set_error("ERROR: no pending pairs in the selected columns")
                self.render_all()
                return

        def done(result: tuple[tuple[str, ...], str, str] | None) -> None:
            if result is None:
                self.set_focus_work()
                return
            names, va, vb = result
            try:
                self._apply_multi_pair(list(names), va, vb)
                self.set_error(None)
            except InTuiError as exc:
                self.set_error(exc.message)
            self.render_all()
            self.set_focus_work()

        self.push_screen(
            MultiPairModal(
                self.engine, columns, skip_column_pick=skip_column_pick
            ),
            done,
        )

    def _apply_multi_pair(self, columns: list[str], val_a: str, val_b: str) -> None:
        e = self.engine
        p = self.place
        old_names = [
            r.name
            for r in e.column_roster(
                p.roster_filter, include_settled=self.show_accepted_columns
            )
        ]
        nxt_pair = None
        if p.screen == "pair_list" and p.column and p.pair_val_a is not None:
            nxt_pair = e.next_pair_below(p.column, p.pair_val_a, p.pair_val_b or "")
        n = e.accept_pair_across_columns(columns, val_a, val_b)
        if n == 0:
            raise InTuiError("ERROR: that pair is not pending on the selected columns")
        if e.column_draft:
            still_pending = {
                r.name for r in e.visible_column_roster(p.roster_filter)
            }
            e.column_draft = {name for name in e.column_draft if name in still_pending}
        e.remember_grain(("pairs", val_a, val_b, *columns), n)
        if p.screen == "pair_list" and p.column:
            if p.pair_val_a == val_a and (p.pair_val_b or "") == val_b:
                page = 0
                if nxt_pair:
                    page, _ = e.page_index_for_pair(p.column, nxt_pair[0], nxt_pair[1])
                self.place = Place(
                    screen="pair_list",
                    column=p.column,
                    pair_val_a=nxt_pair[0] if nxt_pair else None,
                    pair_val_b=nxt_pair[1] if nxt_pair else None,
                    page=page,
                    roster_filter=p.roster_filter,
                    last_pair=(p.column, val_a, val_b),
                    view_tab="pending",
                    focused_name=p.column,
                )
            else:
                self.place.last_pair = (p.column, val_a, val_b)
            return
        new_names = [
            r.name
            for r in e.column_roster(
                p.roster_filter, include_settled=self.show_accepted_columns
            )
        ]
        if p.focused_name and p.focused_name in new_names:
            self.place = Place(
                screen="roster",
                roster_filter=p.roster_filter,
                last_pair=p.last_pair,
                focused_name=p.focused_name,
            )
            return
        gone = next((n for n in old_names if n not in new_names), None)
        self._stay_on_roster_after_column(gone or (p.focused_name or ""), old_names)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if self.place.screen in {"roster", "pair_list"}:
            event.stop()
            self.action_drill()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if self.place.screen == "pair_list":
            pair = self._focused_pair()
            if pair:
                self.place.pair_val_a, self.place.pair_val_b = pair
                self._render_pane()
                self._render_footer()
            return
        if self.place.screen in ("a_only", "b_only", "extras"):
            self._render_pane()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if not bid.startswith("tab-"):
            return
        tab = bid[4:]
        p = self.place
        if not p.column:
            return
        if self.engine.pair_draft_col is not None:
            self.set_error("ERROR: confirm or cancel the pair draft first")
            event.stop()
            self._render_footer()
            return
        if tab == "pending":
            self.place.view_tab = "pending"
            self.place.screen = "pair_list"
        else:
            self.place.view_tab = tab
            self.place.screen = tab  # accepted / equal / all_matched
        event.stop()
        self.render_all()
        self.set_focus_work()
