"""Textual TUI: roster home, pair-first detail, unmatched grids, extras."""

from __future__ import annotations

from typing import Any

import polars as pl
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Static

from reconcile.engine import Engine, InTuiError, Place, RosterRow
from reconcile.errors import HardFail

HELP = """\
KEYS (same everywhere; type in a field when focused)

Enter  drill (roster row, pair → cell step, modal Run)
Esc    back (close modal → cancel roster/cell-step draft → parent; Overview Esc → roster)
Space  toggle focused column/cell in the current draft
a      accept focused grain now (refused on Accepted / Equal / All matched)
A      accept entire column (only when no pair draft) / all unmatched on this side
y      confirm the live draft (column XOR pair cells; all-unchecked pair draft stays)
u      undo last accept, then focused grain.
       Roster u on A-only / B-only undoes all unmatched on that side (same grain as roster a).
U      undo entire column (cell step only)
r      refresh (re-read live files; last good state on failure)
.      repeat last pair as a new draft (column detail only: pair list / cell step)
/      regex column draft (roster)     =  exact sentinel (side A|B, pending values)
c      context-column picker (cell step)
n / p  next / previous page
e      export .recon.zip     o  open zip (refused while a draft is in flight)
q      quit (discards unconfirmed draft)
?      this help

At most one draft: column (roster / regex, = sentinel) XOR pair cells (cell step).
A is refused while a pair draft is in flight (confirm or cancel first).
Named tabs (Pending / Accepted / Equal / All matched). No keys 1–4.
Tab switch is refused while a pair draft is in flight (Esc cancels).
U is cell-step only. . is column detail only. Overview Esc returns to the roster.
Long strings wrap in the footer pane (the grid is a one-line navigator).

This TUI never writes, opens, or copies into the source files.
Pending = 0 is the goal: edit sources elsewhere then refresh, or accept snapshots.
Insights are labeled speculative and never change remaining counts.
"""

CSS = """
Screen {
    background: #1a120c;
    color: #f4efe4;
}
#banner {
    background: #3a1510;
    color: #ffcc99;
    text-style: bold;
    height: auto;
    padding: 0 1;
}
#banner.hidden {
    display: none;
}
#filter-row {
    height: 3;
    padding: 0 1;
}
#filter-row Input {
    background: #2a2218;
    color: #f4efe4;
    border: tall #6a5a40;
}
#work {
    height: 1fr;
}
#pane {
    height: auto;
    max-height: 24;
    overflow-y: auto;
    padding: 0 1;
    color: #f4efe4;
    background: #22180f;
}
#footer {
    dock: bottom;
    height: auto;
    min-height: 1;
    padding: 0 1;
    background: #2a1a10;
    color: #ffe566;
    text-style: bold;
}
#tabs {
    height: 3;
    padding: 0 1;
}
Button {
    background: #5a4030;
    color: #fff8e8;
    border: tall #8a6040;
}
Button:hover, Button:focus, Button.-active {
    background: #6a5040;
    color: #fff8e8;
    border: tall #c08040;
}
Button.-primary, Button.primary {
    background: #5a4030;
    color: #fff8e8;
    border: tall #c08040;
}
#tabs Button {
    background: #3a2a18;
    color: #ffe566;
    border: tall #8a6040;
}
DataTable {
    height: 1fr;
}
DataTable > .datatable--cursor {
    background: #5a4030;
    color: #fff8e8;
    text-style: reverse;
}
.lever {
    text-style: bold underline;
    color: #ffe566;
}
.pending {
    text-style: bold;
    color: #ffe566;
}
.accepted {
    color: #8a8070;
}
.returned {
    text-style: reverse;
    color: #ffaa33;
}
.error {
    text-style: bold;
    color: #ff5533;
}
#modal {
    width: 80;
    height: auto;
    max-height: 90%;
    background: #2a1c12;
    border: heavy #ffaa33;
    padding: 1 2;
}
#modal Input {
    margin: 1 0;
}
#help {
    height: auto;
    max-height: 32;
    padding: 1;
}
.dim {
    color: #8a8070;
}
"""


def _speculative_line(spec: str) -> str:
    s = spec.strip()
    if not s:
        return ""
    if "speculative" in s.lower():
        return s
    return f"speculative: {s}"


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
            yield Static("Esc to close", classes="dim")

    def action_close(self) -> None:
        self.dismiss(None)


class PathModal(ModalScreen[str | None]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "ok", "OK", priority=True),
    ]

    def __init__(self, title: str, placeholder: str) -> None:
        super().__init__()
        self.title_text = title
        self.placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(id="modal"):
            yield Static(self.title_text)
            yield Input(placeholder=self.placeholder, id="path")
            yield Static("Enter confirm · Esc cancel", classes="dim")

    def on_mount(self) -> None:
        self.query_one("#path", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_ok(self) -> None:
        self.dismiss(self.query_one("#path", Input).value.strip() or None)


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
            yield Static("Exact sentinel on pending values on one side. Choose exactly one side.")
            with Horizontal():
                yield Button("A", id="side-a")
                yield Button("B", id="side-b")
            yield Input(placeholder="exact string (empty is legal)", id="sentinel")
            yield Static(
                "Enter Run · Esc cancel. Run is refused until a side is selected. No trim.",
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


class ContextModal(ModalScreen[list[str] | None]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "ok", "OK"),
        Binding("space", "toggle", "Toggle"),
        Binding("y", "ok", "OK"),
    ]

    def __init__(self, names: list[str], selected: set[str]) -> None:
        super().__init__()
        self.names = names
        self.selected = set(selected)

    def compose(self) -> ComposeResult:
        with Vertical(id="modal"):
            yield Static("Context columns (intersection, not keys, not this column). Space toggle.")
            table: DataTable = DataTable(cursor_type="row", id="ctx")
            table.add_columns("on", "name")
            yield table
            yield Static("Enter/y confirm · Esc cancel", classes="dim")

    def on_mount(self) -> None:
        self._fill()
        self.query_one("#ctx", DataTable).focus()

    def _fill(self) -> None:
        table = self.query_one("#ctx", DataTable)
        table.clear()
        for n in self.names:
            mark = "[x]" if n in self.selected else "[ ]"
            table.add_row(mark, n, key=n)

    def _name(self) -> str | None:
        table = self.query_one("#ctx", DataTable)
        if not self.names:
            return None
        row = table.get_row_at(table.cursor_row)
        return str(row[1])

    def action_toggle(self) -> None:
        name = self._name()
        if not name:
            return
        if name in self.selected:
            self.selected.discard(name)
        else:
            self.selected.add(name)
        row = self.query_one("#ctx", DataTable).cursor_row
        self._fill()
        self.query_one("#ctx", DataTable).move_cursor(row=row)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_ok(self) -> None:
        self.dismiss(sorted(self.selected))


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
        Binding("e", "export", "Export", show=False),
        Binding("o", "open_zip", "Open", show=False),
        Binding("q", "quit_app", "Quit", show=False),
        Binding("c", "context", "Context", show=False),
        Binding("slash", "regex", "Regex", show=False),
        Binding("equals", "sentinel", "Sentinel", show=False),
        Binding("full_stop", "repeat_pair", "Repeat", show=False),
        Binding("question_mark", "help", "Help", show=False),
        Binding("question", "help", "Help", show=False),
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

    def draft_in_flight(self) -> bool:
        return self.engine.draft_in_flight()

    def compose(self) -> ComposeResult:
        yield Static("", id="banner", classes="hidden")
        with Horizontal(id="filter-row"):
            yield Static("filter ", classes="dim")
            yield Input(placeholder="substring on name (always on; / is regex draft)", id="filter")
        yield Vertical(id="work")
        yield Static("", id="pane")
        yield Static("", id="footer")

    def on_mount(self) -> None:
        if self.place.screen == "roster" or not self.place.screen:
            self.place.screen = "roster"
        filt = self.query_one("#filter", Input)
        filt.value = self.place.roster_filter
        self.render_all()
        self.call_after_refresh(self.set_focus_work)

    def _in_input(self) -> bool:
        focused = self.focused
        if not isinstance(focused, Input):
            return False
        if focused.id == "filter" and self.place.screen != "roster":
            return False
        return True

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if self._in_input() and action not in {"back", "drill"}:
            return False
        return True

    def set_error(self, msg: str | None) -> None:
        banner = self.query_one("#banner", Static)
        if msg:
            banner.update(msg if msg.startswith("ERROR") else f"ERROR: {msg}")
            banner.set_class(False, "hidden")
            banner.add_class("error")
        else:
            banner.update("")
            banner.set_class(True, "hidden")
        self.tui_error = msg

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
        elif self.query("#filter-row"):
            pass

    def render_all(self) -> None:
        self._sync_filter_visibility()
        screen = self.place.screen
        if screen != self._mounted_screen:
            self._remount_work()
            self._mounted_screen = screen
        else:
            self._refill_work()
        self._render_pane()
        self._render_footer()
        if self.tui_error:
            self.set_error(self.tui_error)
        self.call_after_refresh(self.set_focus_work)

    def _sync_filter_visibility(self) -> None:
        row = self.query_one("#filter-row")
        row.display = self.place.screen == "roster"
        if self.place.screen != "roster":
            return
        filt = self.query_one("#filter", Input)
        focused = self.focused
        typing = isinstance(focused, Input) and focused.id == "filter"
        if not typing and filt.value != self.place.roster_filter:
            filt.value = self.place.roster_filter

    def _now_keys(self) -> str:
        p = self.place
        e = self.engine
        if p.screen == "cell_step" and e.pair_draft_col is not None:
            return "Space toggle  y confirm  Esc cancel  a cell  c context  U column  ? help  q quit"
        if e.column_draft and p.screen == "roster":
            return "Space toggle  y confirm  Esc cancel  a grain  ? help  q quit"
        if e.column_draft:
            return "y confirm on roster  Esc back (draft stays)  ? help  q quit"
        if p.screen == "roster":
            return "Enter drill  a grain  A column  / regex  = sentinel  Esc overview  ? help  q quit"
        if p.screen == "pair_list":
            return "Enter cells  a pair  A column  n/p page  Esc roster  ? help  q quit"
        if p.screen == "cell_step":
            return "a cell  c context  U column  Esc pairs  ? help  q quit"
        if p.screen in ("accepted", "equal", "all_matched"):
            return "n/p page  Esc roster  ? help  q quit"
        if p.screen in ("a_only", "b_only"):
            return "a key  A all  n/p page  Esc roster  ? help  q quit"
        if p.screen == "extras":
            return "a extra  Esc roster  ? help  q quit"
        if p.screen == "overview":
            return "Enter list  Esc roster  ? help  q quit"
        return "? help  q quit"

    def _render_footer(self) -> None:
        e = self.engine
        p = self.place
        bits = [
            f"pending {e.pending_total()}",
            f"cells {e.pending_cells_n()}",
            f"A-only {e.pending_a_only_n()}",
            f"B-only {e.pending_b_only_n()}",
            f"extras {e.pending_extras_n()}",
        ]
        if e.pending_total() == 0:
            bits[0] = "pending 0"
        # Live set only. Never show column-draft N on the cell step (pair XOR).
        # Esc on pair list does not cancel a column draft — don't claim it does.
        if p.screen == "cell_step" and e.pair_draft_col is not None:
            n = max(0, e.pair_draft_height() - len(self.pair_draft_unchecked))
            bits.append(f"draft {n}  y confirm  Esc cancel  Space toggle  c context  U column")
        elif e.column_draft and p.screen == "roster":
            bits.append(f"draft {e.column_draft_n()}  y confirm  Esc cancel  Space toggle")
        elif e.column_draft:
            bits.append(f"draft {e.column_draft_n()}  y confirm on roster  Esc back (draft stays)")
        elif e.pair_draft_col is not None:
            n = max(0, e.pair_draft_height() - len(self.pair_draft_unchecked))
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
        hint = "  " + self._now_keys()
        self.query_one("#footer", Static).update(" · ".join(bits) + spec + hint)

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
            if p.screen == "cell_step" and p.column:
                ctx = self.engine.context_values(p.focused_key or (), p.column)
                for name, a, b in ctx:
                    t.append(f"\n{name}  A|{a}  B|{b}")
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
        elif p.screen == "overview":
            pane.update("")
        else:
            pane.update("")

    def _work_body(self):
        screen = self.place.screen
        if screen == "roster":
            return self._roster_table()
        if screen == "overview":
            return self._overview()
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
        work.mount(self._work_body())

    def _refill_work(self) -> None:
        if not self.query("#grid"):
            self._remount_work()
            return
        screen = self.place.screen
        if screen == "roster":
            self._fill_roster(self.query_one("#grid", DataTable))
            return
        # Same screen, new data: rebuild the existing grid in place.
        self._remount_work()
        self._mounted_screen = screen

    def _roster_table(self) -> DataTable:
        table: DataTable = DataTable(cursor_type="row", id="grid", zebra_stripes=False)
        table.add_columns(" ", "kind", "name", "side", "pending", "top-pair %", "accepted", "equal", "cat", "speculative")
        self._fill_roster(table)
        return table

    def _fill_roster(self, table: DataTable) -> None:
        table.clear()
        rows = self.engine.roster(self.place.roster_filter)
        self._table_keys = []
        if not rows:
            table.add_row("", "", "(no rows — empty filter or no remaining-work kinds)", "—", "0", "—", "0", "—", "—", "")
            self._table_keys = [None]
            return
        for i, r in enumerate(rows):
            check = " "
            if self.engine.column_draft:
                if r.kind == "column":
                    check = "[x]" if r.name in self.engine.column_draft else "[ ]"
            name = r.name
            styles = []
            if i == 0:
                styles.append("bold underline")
            if r.returned:
                styles.append("reverse")
            if r.pending == 0:
                styles.append("dim")
            label = Text(name, style=" ".join(styles) if styles else "")
            key = (r.kind, r.name, r.side)
            table.add_row(
                check,
                r.kind,
                label,
                r.side,
                str(r.pending),
                r.top_pair_pct,
                str(r.accepted),
                r.equal,
                r.categorical,
                r.speculative,
                key=str(key),
            )
            self._table_keys.append(r)
        idx = 0
        focus_name = self.place.focused_name
        extra = (self.place.extra_side, self.place.extra_name)
        for i, r in enumerate(rows):
            if focus_name and r.name == focus_name:
                if r.kind == "extra" and extra[0] and r.side != extra[0]:
                    continue
                idx = i
                break
        table.move_cursor(row=idx)

    def _overview(self) -> Static:
        e = self.engine
        lines = ["OVERVIEW (counts — not home)", ""]
        lines.extend(e.identity_lines())
        lines += [
            "",
            f"matched keys: {e.matched_key_count()}",
            f"A-only keys pending {e.pending_a_only_n()}  accepted {e.accepted_a_only.height}",
            f"B-only keys pending {e.pending_b_only_n()}  accepted {e.accepted_b_only.height}",
            f"extras pending {e.pending_extras_n()}  accepted {len(e.accepted_extras)}",
            f"mismatched cells pending {e.pending_cells_n()}  accepted {e.accepted_cells.height}",
            f"remaining pending total {e.pending_total()}",
            "",
            "Enter on A-only / B-only / Schema extras below, or Esc back to the roster.",
            "A-only keys · B-only keys · Schema extras (same lists as roster Enter)",
        ]
        body = "\n".join(lines)
        # compact entry table
        table: DataTable = DataTable(cursor_type="row", id="grid")
        table.add_columns("entry")
        table.add_row("A-only keys", key="a_only")
        table.add_row("B-only keys", key="b_only")
        table.add_row("Schema extras", key="extras")
        return Vertical(Static(body), table)

    def _tab_bar(self, current: str) -> Horizontal:
        labels = [
            ("pending", "Pending"),
            ("accepted", "Accepted"),
            ("equal", "Equal"),
            ("all_matched", "All matched"),
        ]
        buttons = [
            Button(f"[{label}]" if key == current else label, id=f"tab-{key}")
            for key, label in labels
        ]
        return Horizontal(*buttons, id="tabs")

    def _pair_view(self) -> Vertical:
        col = self.place.column or ""
        if not col:
            return Vertical(Static("No column."))
        return Vertical(Static(f"COLUMN {col}"), self._tab_bar("pending"), self._pair_list(col))

    def _pair_list(self, col: str) -> DataTable:
        table: DataTable = DataTable(cursor_type="row", id="grid")
        table.add_columns("A", "B", "pending", "speculative")
        recs, page, pages = self.engine.pair_page(col, self.place.page)
        self.place.page = page
        self._page_count = pages
        self._table_keys = []
        if not recs:
            table.add_row("(no pending pairs)", "", "0", "")
            self._table_keys = [None]
            return table
        focus = 0
        want_a, want_b = self.place.pair_val_a, self.place.pair_val_b
        for i, rec in enumerate(recs):
            va, vb = rec["val_a"], rec["val_b"]
            tags = ", ".join(self.engine.cell_insights(va, vb))
            returned = self.engine.pair_has_returned(col, va, vb)
            style = "reverse" if returned else "bold"
            label_a = Text(va, style=style)
            label_b = Text(vb, style=style)
            table.add_row(label_a, label_b, str(rec["n"]), tags)
            self._table_keys.append(rec)
            if want_a is not None and va == want_a and vb == want_b:
                focus = i
        table.move_cursor(row=focus)
        rec = recs[focus]
        self.place.pair_val_a, self.place.pair_val_b = rec["val_a"], rec["val_b"]
        return table

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
        ctx_names = [
            n
            for n in self.engine.context_columns.get(col, [])
            if n in self.engine.context_pool and n != col
        ]
        headers = [*self.engine.keys, "A", "B", *ctx_names, "speculative"]
        table.add_columns(*headers)
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
            else:
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
                    returned = self.engine.cell_is_returned(key, col)
                    tags = ", ".join(self.engine.cell_insights(rec["val_a"], rec["val_b"]))
                    key_cells = [_display_text(str(rec[k])) for k in self.engine.keys]
                    ctx_cells = [
                        f"A|{rec.get(f'{n}__ctx_a', '')}  B|{rec.get(f'{n}__ctx_b', '')}"
                        for n in ctx_names
                    ]
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
        else:
            recs, page, pages = self.engine.cells_for_tab(col, tab, self.place.page)
            self.place.page = page
            self._page_count = pages
            if not recs:
                table.add_row(*([""] * (len(headers) - 1)), "(empty)")
                self._table_keys = [None]
            else:
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
                    ctx_cells = [
                        f"A|{rec.get(f'{n}__ctx_a', '')}  B|{rec.get(f'{n}__ctx_b', '')}"
                        for n in ctx_names
                    ]
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
        tab_current = tab if self.place.screen != "cell_step" else "pending"
        return Vertical(Static(f"COLUMN {col}   {title}"), self._tab_bar(tab_current), table)

    def _unmatched(self, side: str) -> Vertical:
        self._follow_focused_key_page(
            lambda key: self.engine.page_index_for_unmatched_key(side, key)
        )
        recs, page, pages = self.engine.unmatched_page(side, self.place.page)
        self.place.page = page
        self._page_count = pages
        frame = self.engine.a_only if side == "A" else self.engine.b_only
        cols = list(frame.columns)
        table: DataTable = DataTable(cursor_type="row", id="grid")
        table.add_columns("st", *cols)
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
        return Vertical(
            Static(f"{side}-only keys  (raw columns on this side, including extras)"),
            table,
        )

    def _extras(self) -> DataTable:
        table: DataTable = DataTable(cursor_type="row", id="grid")
        table.add_columns("side", "name", "pending", "speculative")
        rows = self.engine.extras_rows()
        self._table_keys = []
        if not rows:
            table.add_row("—", "(no extras)", "0", "")
            self._table_keys = [None]
            return table
        for rec in rows:
            style = "reverse" if rec["returned"] else ("bold" if rec["pending"] else "dim")
            table.add_row(
                rec["side"],
                Text(rec["name"], style=style),
                str(rec["pending"]),
                ", ".join(rec["speculative"]),
            )
            self._table_keys.append(rec)
        return table

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

    def action_quit_app(self) -> None:
        self.engine.cancel_drafts()
        self.pair_draft_unchecked = set()
        code = 0 if self.engine.pending_total() == 0 else 1
        self.exit(code)

    def action_back(self) -> None:
        if self._in_input():
            self.set_focus_work()
            self.query_one("#grid").focus() if self.query("#grid") else None
            return
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
        elif p.screen == "roster":
            self.place = Place(screen="overview", roster_filter=p.roster_filter, last_pair=p.last_pair)
        elif p.screen == "overview":
            self.place = Place(
                screen="roster",
                roster_filter=p.roster_filter,
                last_pair=p.last_pair,
                focused_name=p.focused_name,
            )
        self.render_all()
        self.set_focus_work()

    def action_drill(self) -> None:
        if self._in_input():
            # Enter in filter: keep filter, return to list
            self.place.roster_filter = self.query_one("#filter", Input).value
            self.set_focus_work()
            self.render_all()
            self.set_focus_work()
            return
        e = self.engine
        p = self.place
        try:
            if p.screen == "roster":
                row = self._focused_roster()
                if not row:
                    return
                if row.kind == "column":
                    self.place = Place(
                        screen="pair_list",
                        column=row.name,
                        roster_filter=p.roster_filter,
                        last_pair=p.last_pair,
                        focused_name=row.name,
                    )
                elif row.kind == "A-only":
                    self.place = Place(screen="a_only", roster_filter=p.roster_filter, last_pair=p.last_pair)
                elif row.kind == "B-only":
                    self.place = Place(screen="b_only", roster_filter=p.roster_filter, last_pair=p.last_pair)
                elif row.kind == "extra":
                    self.place = Place(
                        screen="extras",
                        extra_side=row.side,
                        extra_name=row.name,
                        roster_filter=p.roster_filter,
                        last_pair=p.last_pair,
                    )
            elif p.screen == "overview":
                table = self.query_one("#grid", DataTable)
                key = table.get_row_at(table.cursor_row)
                label = str(key[0])
                if "A-only" in label:
                    self.place = Place(screen="a_only", roster_filter=p.roster_filter, last_pair=p.last_pair)
                elif "B-only" in label:
                    self.place = Place(screen="b_only", roster_filter=p.roster_filter, last_pair=p.last_pair)
                else:
                    self.place = Place(screen="extras", roster_filter=p.roster_filter, last_pair=p.last_pair)
            elif p.screen == "pair_list":
                if e.column_draft:
                    raise InTuiError("ERROR: confirm or cancel the column draft first")
                pair = self._focused_pair()
                if not pair or not p.column:
                    return
                n = e.start_pair_draft(p.column, pair[0], pair[1])
                if n == 0:
                    self.set_error("ERROR: that pair has no pending cells")
                    return
                self.pair_draft_unchecked = set()
                last = (p.column, pair[0], pair[1])
                self.place = Place(
                    screen="cell_step",
                    column=p.column,
                    pair_val_a=pair[0],
                    pair_val_b=pair[1],
                    roster_filter=p.roster_filter,
                    last_pair=last,
                    view_tab="pending",
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
            if p.screen in ("accepted", "equal", "all_matched"):
                raise InTuiError("ERROR: not remaining work; switch to Pending")
            if p.screen == "roster":
                row = self._focused_roster()
                if not row:
                    return
                if row.kind == "column":
                    if row.pending == 0:
                        raise InTuiError("ERROR: no pending cells in this column")
                    n = e.accept_column(row.name)
                    e.remember_grain(("column", row.name), n)
                    if e.column_draft:
                        nxt_name = next(
                            (
                                r.name
                                for r in e._roster_cache
                                if r.kind == "column" and r.name in e.column_draft
                            ),
                            None,
                        )
                        self.place = Place(
                            screen="roster",
                            roster_filter=p.roster_filter,
                            last_pair=p.last_pair,
                            focused_name=nxt_name,
                        )
                    else:
                        self.place = e.next_lever_place(
                            Place(column=row.name, roster_filter=p.roster_filter, last_pair=p.last_pair)
                        )
                elif row.kind == "A-only":
                    if row.pending == 0:
                        raise InTuiError("ERROR: no pending A-only keys")
                    n = e.accept_all_unmatched("A")
                    e.remember_grain(("unmatched_all", "A"), n)
                    self.place = e.next_lever_place(Place(screen="a_only", roster_filter=p.roster_filter, last_pair=p.last_pair))
                elif row.kind == "B-only":
                    if row.pending == 0:
                        raise InTuiError("ERROR: no pending B-only keys")
                    n = e.accept_all_unmatched("B")
                    e.remember_grain(("unmatched_all", "B"), n)
                    self.place = e.next_lever_place(Place(screen="b_only", roster_filter=p.roster_filter, last_pair=p.last_pair))
                elif row.kind == "extra":
                    if row.pending == 0:
                        raise InTuiError("ERROR: extra is not pending")
                    n = e.accept_extra(row.side, row.name)
                    e.remember_grain(("extra", row.side, row.name), n)
                    self.place = e.next_lever_place(
                        Place(screen="extras", extra_side=row.side, extra_name=row.name, roster_filter=p.roster_filter, last_pair=p.last_pair)
                    )
            elif p.screen == "pair_list":
                if e.column_draft:
                    raise InTuiError("ERROR: confirm or cancel the column draft first")
                pair = self._focused_pair()
                if pair and p.column:
                    n = e.accept_pair(p.column, pair[0], pair[1])
                    last = (p.column, pair[0], pair[1])
                    e.remember_grain(("pair", p.column, pair[0], pair[1]), n)
                    self.place = e.next_lever_place(Place(column=p.column, roster_filter=p.roster_filter, last_pair=last))
            elif p.screen == "cell_step":
                rec = self._focused_rec()
                if rec and p.column:
                    key = e.key_of(rec)
                    n = e.accept_cell(key, p.column, rec["val_a"], rec["val_b"])
                    e.remember_grain(("cell", key, p.column), n)
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
                    e.remember_grain(("unmatched", "B", key), n)
                    nxt = e.next_pending_key_in_grid("B", key)
                    self.place.focused_key = nxt
                    if nxt:
                        page, _ = e.page_index_for_unmatched_key("B", nxt)
                        self.place.page = page
            elif p.screen == "extras":
                rec = self._focused_rec()
                if rec:
                    n = e.accept_extra(rec["side"], rec["name"])
                    e.remember_grain(("extra", rec["side"], rec["name"]), n)
                    self.place = e.next_lever_place(p)
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
            if p.screen in ("accepted", "equal", "all_matched"):
                raise InTuiError("ERROR: switch to Pending to accept the column")
            if e.pair_draft_col is not None and p.screen in ("pair_list", "cell_step"):
                raise InTuiError("ERROR: confirm or cancel the pair draft first")
            if p.screen == "roster":
                row = self._focused_roster()
                if not row:
                    return
                if row.kind == "column":
                    if row.pending == 0:
                        raise InTuiError("ERROR: no pending cells in this column")
                    n = e.accept_column(row.name)
                    e.remember_grain(("column", row.name), n)
                    if e.column_draft:
                        nxt_name = next(
                            (
                                r.name
                                for r in e._roster_cache
                                if r.kind == "column" and r.name in e.column_draft
                            ),
                            None,
                        )
                        self.place = Place(
                            screen="roster",
                            roster_filter=p.roster_filter,
                            last_pair=p.last_pair,
                            focused_name=nxt_name,
                        )
                    else:
                        self.place = e.next_lever_place(
                            Place(column=row.name, roster_filter=p.roster_filter, last_pair=p.last_pair)
                        )
                elif row.kind == "A-only":
                    if row.pending == 0:
                        raise InTuiError("ERROR: no pending A-only keys")
                    n = e.accept_all_unmatched("A")
                    e.remember_grain(("unmatched_all", "A"), n)
                    self.place = e.next_lever_place(Place(screen="a_only", roster_filter=p.roster_filter, last_pair=p.last_pair))
                elif row.kind == "B-only":
                    if row.pending == 0:
                        raise InTuiError("ERROR: no pending B-only keys")
                    n = e.accept_all_unmatched("B")
                    e.remember_grain(("unmatched_all", "B"), n)
                    self.place = e.next_lever_place(Place(screen="b_only", roster_filter=p.roster_filter, last_pair=p.last_pair))
                elif row.kind == "extra":
                    if row.pending == 0:
                        raise InTuiError("ERROR: extra is not pending")
                    n = e.accept_extra(row.side, row.name)
                    e.remember_grain(("extra", row.side, row.name), n)
                    self.place = e.next_lever_place(
                        Place(
                            screen="extras",
                            extra_side=row.side,
                            extra_name=row.name,
                            roster_filter=p.roster_filter,
                            last_pair=p.last_pair,
                        )
                    )
            elif p.screen in ("pair_list", "cell_step"):
                if p.column:
                    n = e.accept_column(p.column)
                    e.remember_grain(("column", p.column), n)
                    self.place = e.next_lever_place(Place(column=p.column, roster_filter=p.roster_filter, last_pair=p.last_pair))
            elif p.screen == "a_only":
                n = e.accept_all_unmatched("A")
                e.remember_grain(("unmatched_all", "A"), n)
                self.place = e.next_lever_place(p)
            elif p.screen == "b_only":
                n = e.accept_all_unmatched("B")
                e.remember_grain(("unmatched_all", "B"), n)
                self.place = e.next_lever_place(p)
            elif p.screen == "extras":
                rec = self._focused_rec()
                if rec:
                    n = e.accept_extra(rec["side"], rec["name"])
                    e.remember_grain(("extra", rec["side"], rec["name"]), n)
                    self.place = e.next_lever_place(p)
            self.set_error(None)
        except InTuiError as exc:
            self.set_error(exc.message)
            return
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
                names = tuple(sorted(e.column_draft))
                n = e.confirm_column_draft()
                e.remember_grain(("columns", *names), n)
                self.place = e.next_lever_place(p)
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
            if row.kind == "A-only":
                return e.undo_unmatched("A")
            if row.kind == "B-only":
                return e.undo_unmatched("B")
            if row.kind == "extra":
                return e.undo_extra(row.side, row.name)
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
        if p.screen != "cell_step":
            self.set_error("ERROR: U undoes the entire column on the cell step only")
            return
        if not p.column:
            self.set_error("ERROR: U undoes the entire column on the cell step only")
            return
        n = self.engine.undo_column(p.column)
        if n == 0:
            self.set_error("ERROR: nothing snapshotted to undo")
            return
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

    def action_page_next(self) -> None:
        self.place.page += 1
        # Stop render from following the accepted row back onto its old page.
        self.place.focused_key = None
        self.render_all()
        self.set_focus_work()

    def action_page_prev(self) -> None:
        self.place.page = max(0, self.place.page - 1)
        self.place.focused_key = None
        self.render_all()
        self.set_focus_work()

    def action_regex(self) -> None:
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
            try:
                self.engine.start_regex_draft(pat)
                self.set_error(None)
            except InTuiError as exc:
                self.set_error(exc.message)
            self.render_all()
            self.set_focus_work()

        self.push_screen(RegexModal(), done)

    def action_sentinel(self) -> None:
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
            try:
                self.engine.start_sentinel_draft(side, sentinel)
                self.set_error(None)
            except InTuiError as exc:
                self.set_error(exc.message)
            self.render_all()
            self.set_focus_work()

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
            self.place = Place(
                screen="cell_step",
                column=col,
                pair_val_a=va,
                pair_val_b=vb,
                roster_filter=self.place.roster_filter,
                last_pair=lp,
                view_tab="pending",
            )
        self.set_error(None)
        self.render_all()
        self.set_focus_work()

    def action_context(self) -> None:
        e = self.engine
        if self.place.screen != "cell_step" or not self.place.column:
            self.set_error("ERROR: context columns are only on the cell step")
            return
        col = self.place.column
        names = [n for n in e.context_pool if n != col]
        selected = set(e.context_columns.get(col, []))

        def done(result: list[str] | None) -> None:
            if result is None:
                return
            e.context_columns[col] = result
            self.render_all()
            self.set_focus_work()

        self.push_screen(ContextModal(names, selected), done)

    def action_export(self) -> None:
        def done(path: str | None) -> None:
            if not path:
                return
            try:
                self.engine.export_zip(path, self.place)
                self.set_error(None)
                self.query_one("#banner", Static).update(f"Exported {path}")
                self.query_one("#banner", Static).set_class(False, "hidden")
            except Exception as exc:
                self.set_error(f"ERROR: export failed: {exc}")
            self.render_all()

        self.push_screen(PathModal("Export .recon.zip (confirmed snapshots only; no drafts)", "job.recon.zip"), done)

    def action_open_zip(self) -> None:
        if self.draft_in_flight():
            self.set_error("ERROR: confirm or cancel the current draft first")
            self.render_all()
            return

        def done(path: str | None) -> None:
            if not path:
                return
            try:
                new, place = Engine.from_session(path)
            except (HardFail, InTuiError) as exc:
                msg = getattr(exc, "message", str(exc))
                self.set_error(f"ERROR: {msg}" if not str(msg).startswith("ERROR") else str(msg))
                self.render_all()
                return
            except Exception as exc:
                self.set_error(f"ERROR: {exc}")
                self.render_all()
                return
            self.engine = new
            self.place = place
            self.pair_draft_unchecked = set()
            self._mounted_screen = None
            self.set_error(None)
            self.render_all()
            self.set_focus_work()

        self.push_screen(PathModal("Open .recon.zip (live-rereads sources)", "job.recon.zip"), done)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if self.place.screen in {"roster", "overview", "pair_list"}:
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

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter":
            self.place.roster_filter = event.value
            # re-render roster list only when on roster; keep typing focus
            if self.place.screen == "roster":
                focused = self.focused
                if self.query("#grid"):
                    self._fill_roster(self.query_one("#grid", DataTable))
                self._render_footer()
                if isinstance(focused, Input):
                    event.input.focus()
