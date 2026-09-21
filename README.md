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
python Reconcile.py --a C:\data\left.xlsx --b C:\data\right.xlsx --a-sheet Foo --b-sheet Bar --keys id
python Reconcile.py --a C:\data\left.xlsx --b C:\data\right.xlsx -sheets data{1-4,7} --keys id
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

Excel sides use one of two sheet paths (mutually exclusive):

- **Single pair:** `--a-sheet` / `--b-sheet` (required per Excel side). Names may differ (`--a-sheet Foo --b-sheet Bar`).
- **Same-named sequence:** `-sheets` / `--sheets` (`data{1-4,7}` → `data1`…`data4`, `data7`; or a plain list `Jan,Feb`). Do **not** also pass `--a-sheet` / `--b-sheet`. Each expanded name must exist in both workbooks. Only the first pair loads; `S` advances when no draft is open. If that sheet still has pending columns, unmatched keys, or mismatched columns, `S` asks you to leave them unaccepted (`y` or `S` again; `Esc` stays) and does not snapshot them. See [REQUIREMENTS.md §12.1](REQUIREMENTS.md).

Delimiter and encoding flags are illegal on Excel sides. Excel is loaded with fastexcel as string columns: stored/cached values as-is (formulas are not evaluated). Merged cells are allowed; secondary merge cells may be empty strings. A `.csv` side (extension case-insensitive) defaults to comma when `--a-delim` / `--b-delim` is omitted; the flag still overrides. Other delimited files (`.txt`, `.dat`, no extension, …) **require** `--a-delim` / `--b-delim` (`comma`, `tilde`, `pipe`, `tab`, or the literal character `,` `~` `|` / tab). No sniffing and no `.txt`→tilde default. Encoding defaults to UTF-8 (`utf8`); override with `--a-encoding` / `--b-encoding` (`utf8`, `windows-1252`; `utf8-lossy` / `windows-1252-lossy` as explicit opt-in). The resolved delimiter character (including the `.csv` comma default) is frozen in in-memory job identity and reused on refresh.

## In the TUI

**Happy path:** launch lands on the **column roster** (shared comparable columns in table A import order) → knock columns down with `a` in place, or `Enter` to inspect a pile (`a` on the pair / cell / key / extra that is selected) → edit sources in another tool, save, `r` to refresh. Repeat until remaining work is 0. Regex `:`, sentinel `=`, and Equal / All-matched tabs (`[` / `]`) are real and behind glass. A-only keys, B-only keys, and mismatched columns (headers on one side only) are remaining work from the **overview modal** (`i`) or next lever — they are not column-roster rows.

Home is the **column roster** of pending comparable columns, in **table A import order** (the original A header order). Columns whose shared rows are all equal (or already accepted) are hidden by default. `v` toggles them back in, dim, with a compact **status** (`pending` / `accepted` / `equal`) so they do not look like remaining work: pending section first, then settled, each still in A order. `Enter` drills into the focused column’s largest pending pair (the roster pane shows that pair’s raw A and B strings and its count); `a` on the roster still accepts the whole column. `Esc` pops one layer (modal close with no draft change; pair-draft cancel; child screen back to roster **and** cancel a column draft). Roster `Esc` stays when idle. `i` opens the overview modal (counts + unmatched keys / mismatched columns); `Esc` closes it. `a` always accepts the **current selection**: a roster column (stays on the roster), a pair, a cell, one unmatched key, or one mismatched column. After `a` removes or hides the current row, selection moves to the item that was **below** it (or the new last remaining item; empty state if none). It does not jump to the top or drill as a side effect. `A` is bulk on the pair list (entire column) and unmatched-key grids (all on this side). **Roster `A` is ERROR** (use `a`). Extras `A` is one extra (same as `a`). `m` is pair-only: on the pair list it applies **this pair** to the live column draft, or to every pending column that has it; on the roster it opens the pair picker (the union) with no column picker. After `:` / `/` or `=`, `m` uses the live ON columns. `u` undoes the last accept as one unit (ERROR if nothing accepted yet). `U` undoes this column on the pair list only. `r` re-reads the live files; `q` quits; `?` opens the **help modal** (Esc closes; Up/Down scroll). There is no roster filter box: `:` is regex column draft (`/` is a deprecated alias).

The footer is status, not a cheat-sheet: counts (**pending columns**, **unmatched keys**, **mismatched columns**) on the left; page / sheet / `? help` on the right; refresh delta on its own line (`remaining work 40→12`). Pair list adds `. repeat · u undo`. Draft recipes live on the **banner**. Full bindings live in the `?` modal, grouped by screen. It does not sum cells + rows + header names into one “cells” figure.

Column detail is always a **paged pair list** (the table is a navigator; the pane shows the focused pair’s full `A:` / `B:` strings). `c` picks **context columns** here or on the cell step. Space toggles a standalone column (`Flag`). `0`–`9` toggle the focused picker’s membership in group N (`gN Flag+Region`); those keys apply only on the context page. A column can be standalone and in several groups. Empty groups do not appear. Each pair shows the 5 most occurring unique values (or group tuples) **with pending-row counts for this pair** (`foo×12 | bar×4 | +N more` if more exist). Counts are for that pair’s pending rows, not the whole table. `Enter` opens the cell step as a pair draft. One draft in flight: **column XOR pair**. `y` confirms the live draft (**ERROR if none**). After `=` / `:` confirm (`y`), land back on the roster (selection moves to the column that was below, or the first remaining pending). `A` is refused while a pair draft is in flight. Named tabs switch Pending / Accepted / Equal / All matched with `[` / `]`; there are no keys `1`–`4`. `.` from that column’s pair list starts a cell-step draft; from another column’s pair list it focuses the last pair first (Enter to draft). `U` undoes the entire column from the pair list.

On **Mismatched columns** (`i` → extras), **Suggest** may appear below the A-not-B / B-not-A lists when two extras are the same name after strip / case / `_`/` `/`-` separators / token sort. Title: **Suggest — rename in source files, then r**. Columns: A, B, **normalizers**, **keys (shared / still different)**. Not a TUI mapping. `a` still accepts the focused extra. Suggest is not focusable.

On the roster, `:` drafts comparable columns whose **names** match a Python regex (`/` is the same command). `=` drafts pending comparable columns where the chosen side is a **sentinel**: pick side with `a` or `b`, then type the constant (raw text; no trim, no regex, no expression; empty `""` is legal). A-only / B-only keys do not participate. Both fill a column draft (all `[ON]`); the banner says **y accept · Space toggle · Esc cancel**; `Space` toggles (`[ON]`/`[off]`); `m` applies the same pair to the ON columns; `Esc` cancels. Roster insight columns: `const A` / `const B` / `const both` (values; hidden if unused) and `trim` / `case` / `trim+case` / `num` / `ws` / `date` (`y` if every pending cell matches; hidden if all `n`). Pair/cell keep per-pair **hints** (`trim`, `case`, `date`). Insights cannot accept. Compare stays exact raw text. Long compare / refresh / sentinel / regex / roster rebuild shows `working…` in the footer until the wait finishes.

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
