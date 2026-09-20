# Strict data reconciliation TUI

Investigation TUI for **two-sided** data reconciliation. Compare is **exact raw text** (the only cast is true null → `""`). Walk remaining differences, accept known variation as in-session snapshots, and/or edit the source files in another tool and refresh until **pending = 0**.

This is **not** an audit, sign-off, or certification tool. It **never writes, patches, opens, or copies into** the files passed as `--a` / `--b`.

See [REQUIREMENTS.md](REQUIREMENTS.md) for the full spec.

## Install

Python **3.13**. From the repo root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Linux / macOS:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Dependencies: Polars, fastexcel, Textual.

## Run

User-facing command is the script (not a console-script name).

PowerShell:

```powershell
python Reconcile.py --a C:\data\left.csv --b C:\data\right.csv --keys id,year
python Reconcile.py --a C:\data\left.csv --b C:\data\right.csv --a-delim tilde --keys id
python Reconcile.py --a C:\data\left.xlsx --b C:\data\right.xlsx --a-sheet Sheet1 --b-sheet Sheet1 --keys id
python Reconcile.py --a C:\data\left.dat --b C:\data\right.txt --a-delim pipe --b-delim tilde --keys id
python Reconcile.py --a C:\data\left.csv --b C:\data\right.csv --a-encoding windows-1252 --keys id
```

Linux / macOS:

```bash
python Reconcile.py --a ./tests/fixtures/left.csv --b ./tests/fixtures/right.csv --keys id,year
python Reconcile.py --a ./left.csv --b ./right.csv --a-delim tilde --keys id
python Reconcile.py --a ./left.dat --b ./right.txt --a-delim pipe --b-delim tilde --keys id
python Reconcile.py --a ./left.csv --b ./right.csv --a-encoding windows-1252 --keys id
```

`--keys` is a single comma-separated list. Surrounding spaces on each name are stripped; there is no quoting.

CLI paths (`--a`, `--b`) may be relative to the invocation cwd; they are resolved immediately and **only absolute paths** are stored in the in-memory job identity.

Excel sides require `--a-sheet` / `--b-sheet`. Delimiter and encoding flags are illegal on Excel sides. Excel is loaded with fastexcel as string columns: stored/cached values as-is (formulas are not evaluated). Merged cells are allowed; secondary merge cells may be empty strings. A `.csv` side (extension case-insensitive) defaults to comma when `--a-delim` / `--b-delim` is omitted; the flag still overrides. Other delimited files (`.txt`, `.dat`, no extension, …) **require** `--a-delim` / `--b-delim` (`comma`, `tilde`, `pipe`, `tab`, or the literal character `,` `~` `|` / tab). No sniffing and no `.txt`→tilde default. Encoding defaults to UTF-8 (`utf8`); override with `--a-encoding` / `--b-encoding` (`utf8`, `windows-1252`; `utf8-lossy` / `windows-1252-lossy` as explicit opt-in). The resolved delimiter character (including the `.csv` comma default) is frozen in in-memory job identity and reused on refresh.

## In the TUI

**Happy path:** launch lands on the **column roster** (shared comparable columns in table A import order) → knock columns down with `a` in place, or `Enter` to inspect a pile (`a` on the pair / cell / key / extra that is selected) → edit sources in another tool, save, `r` to refresh. Repeat until remaining work is 0. Regex `:`, sentinel `=`, and Equal / All-matched tabs (`[` / `]`) are real and behind glass. A-only keys, B-only keys, and mismatched columns (headers on one side only) are remaining work from the **overview modal** (`i`) or next lever — they are not column-roster rows.

Home is the **column roster** of pending comparable columns, in **table A import order** (the original A header order). Columns whose shared rows are all equal (or already accepted) are hidden by default. `v` toggles them back in, dim, with a compact **status** (`pending` / `accepted` / `equal`) so they do not look like remaining work: pending section first, then settled, each still in A order. `Enter` drills in; `Esc` pops one layer (modal close with no draft change; pair-draft cancel; child screen back to roster **and** cancel a column draft). Roster `Esc` stays when idle. `i` opens the overview modal (counts + unmatched rows / mismatched columns); `Esc` closes it. `a` always accepts the **current selection**: a roster column (stays on the roster), a pair, a cell, one unmatched key, or one mismatched column. After `a` removes or hides the current row, selection moves to the item that was **below** it (or the new last remaining item; empty state if none). It does not jump to the top or drill as a side effect. `A` is bulk on the pair list (entire column) and unmatched-key grids (all on this side). **Roster `A` is ERROR** (use `a`). Extras `A` is one extra (same as `a`). `m` (roster / pair list) accepts the **same exact pair** on selected columns: pick columns, pick one pair from the grouped union, apply to every selected column that has it. After `:` / `/` or `=`, `m` uses the live ON columns (no second column picker). `r` re-reads the live files; `q` quits; `?` opens the **help modal** (Esc closes; Up/Down scroll). There is no roster filter box: `:` is regex column draft (`/` is a deprecated alias).

The footer is status, not a cheat-sheet: **pending columns**, **unmatched rows**, **mismatched columns**, page when paged, `working…` while a real wait is in flight, refresh delta, and `? help`. Pair list adds `. repeat · u undo`. Draft recipes live on the **banner**. Full bindings live in the `?` modal, grouped by screen. It does not sum cells + rows + header names into one “cells” figure.

Column detail is always a **paged pair list** (the table is a navigator; the pane shows the focused pair’s full `A:` / `B:` strings). `c` picks **context columns** here or on the cell step. Space toggles a standalone column (`ctx:Name`). `0`–`9` toggle the focused picker’s membership in group N (`ctx:gN Flag+Region`); those keys apply only on the context page. A column can be standalone and in several groups. Empty groups do not appear. Each pair shows the 5 most occurring unique values (or group tuples) **with pair-row counts** (`foo 12 | bar 4 | baz 1 …` if more exist). Counts are for that pair’s pending rows, not the whole table. `Enter` opens the cell step as a pair draft. One draft in flight: **column XOR pair**. `y` confirms the live draft (**ERROR if none**). After `=` / `:` confirm (`y`), land back on the roster (selection moves to the column that was below, or the first remaining pending). `A` is refused while a pair draft is in flight. Named tabs switch Pending / Accepted / Equal / All matched with `[` / `]`; there are no keys `1`–`4`. `.` from that column’s pair list starts a cell-step draft; from another column’s pair list it focuses the last pair first (Enter to draft). `U` undoes the entire column from the pair list.

On the roster, `:` drafts comparable columns whose **names** match a Python regex (`/` is the same command). `=` drafts pending comparable columns where the chosen side is a **sentinel**: pick side with `a` or `b`, then type the constant (raw text; no trim, no regex, no expression; empty `""` is legal). A-only / B-only keys do not participate. Both fill a column draft (all `[ON]`); the banner says **y ACCEPT selected**; `Space` select/deselect (`[ON]`/`[off]`); `m` applies the same-pair flow to the ON columns; `Esc` cancels. Speculative tags show the side and constant (`sentinel A=0`, `sentinel B=""`, `sentinel both A=x B=y`) without a `speculative:` prefix. Insights cannot accept. Compare stays exact raw text. Long compare / refresh / sentinel / regex / roster rebuild shows `working…` in the footer until the wait finishes.

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Quit with remaining pending = 0 |
| `1` | Quit with pending remaining |
| `2` | Hard fail (load/parse/schema). Message on stderr includes raw identifiers. |

Once the TUI is up, refresh/draft errors stay in the TUI and keep the last good state.

## Tests

```bash
python -m pytest
```
