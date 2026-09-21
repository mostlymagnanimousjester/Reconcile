# Shipped simplifications

- **Roster pane shows the largest pending pair (shipped).** Focused column: raw `A` / `B` and that pair’s pending count, above the footer, plus any const values. `Enter` opens the pair list on that pair. Pair-list order stays count desc, then `valA`, `valB`. Roster `a` still accepts the column.

- **`m` is pair-only (shipped).** No column-picker. `/`, `=`, and Space already choose columns. On the pair list, `m` applies **this pair** to the live column draft, or to every pending column that has that pair. On the roster, only the pair-picker (the union). Purpose: `""` → `0` (and similar) without a two-step wizard. Inspection tabs (Accepted / Equal / All matched) stay.
- **`u` always undoes the last accept, as one unit (shipped).** After a pair `a`, `u` restores that pair. After column `A` or draft `y`, `u` restores those columns. After pair-only `m` applies e.g. `""` → `0` on eight columns, `u` reverses that apply on all eight. If nothing has been accepted yet, `u` errors (same idea as `y` with no draft).
- **`U` stays “undo this column”** on the pair list only — the inverse of `A`. That is the only other undo. `u` does not fall back to the focused row.
- **`-sheets` (shipped CLI).** Sequential same-named Excel sheet pairs. Full usage contract: [REQUIREMENTS.md §12.1](REQUIREMENTS.md). Short form below. `--a-sheet` / `--b-sheet` remain the single-pair path (not replaced).
- **Last-accept `u` does not undo `S`.** `advance_sheet` is a clean-slate reload (`__init__`) that drops the previous sheet’s snaps and place. Restoring one `S` would need a second engine snapshot store and fights that grain. Do not break `S`.

## Shipped CLI: `-sheets`

Two Excel sheet paths, mutually exclusive:

- **Single pair:** `--a-sheet Foo --b-sheet Bar` (names may differ). Still required when `-sheets` is not used.
- **Same-named sequence:** `-sheets` / `--sheets`. One shared name set. Do not also pass `--a-sheet` / `--b-sheet` (HardFail).

```powershell
python Reconcile.py --a C:\data\left.xlsx --b C:\data\right.xlsx --a-sheet Foo --b-sheet Bar --keys id
python Reconcile.py --a C:\data\left.xlsx --b C:\data\right.xlsx -sheets data{1-4,7} --keys id
```

Both sides must be `.xlsx` / `.xlsm` for `-sheets`. Not CSV. Each expanded name must exist in **both** workbooks; missing name = HardFail / refuse to start that pair.

**Grammar:** braces = prefix + integer items (`N` or inclusive `start-end`), no padding (`data{1-4,7}` → `data1`…`data4`, `data7`). No braces = exact comma list (`Jan,Feb` or `data1,data2`). `{data1,data2}` is illegal (items are not integers).

**Load / advance:** first pair only at startup (same TUI as one A/B compare). **`S` next sheet** when no draft. Remaining work 0 advances immediately. Remaining work > 0 opens a confirm: `y` or `S` leaves those differences unaccepted (not snapshotted); `Esc` stays. Reset place to roster. `A` stays bulk on **this screen** (pair list / unmatched); roster `A` stays ERROR. `]` stays tabs. Last sheet: `S` → `no next sheet` ERROR, no invented name. Exit codes stay per loaded sheet.

## Actionable insights (shipped)

Roster `speculative` is gone on comparable-column rows. Full contract: [REQUIREMENTS.md §10](REQUIREMENTS.md) and [§15.2](REQUIREMENTS.md).

**Sentinel columns** (`sent A` / `sent B` / `sent both`): existing sentinel definition; cell is the value (`0`, `""`, `A=x B=y`). Hide a header if no pending comparable row has that kind.

**Check columns** (`trim` / `case` / `trim+case` / `num` / `ws` / `date`): `y` only if **every** pending cell satisfies the predicate (`.all()`). Hide if every pending row is `n`.

**Dates:** `DATE_FORMATS` / `_POLARS_DATE_FORMATS` include SAS DATE9/7/11, MMDDYY8, DDMMYY8, MONYY, DATETIME, plus the original ISO / slash / compact forms. Unambiguous rule unchanged (`01/02/2020` rejected). Pair/cell `same date` uses the same parsers.

**Pair/cell:** per-pair transform tags; exclusive leftover for trim+case; no whitespace tag when trim already applies; no sentinel in `cell_insights`.

**A-only / B-only / extras:** empty `speculative`. `unmatched_key_insights`, `extra_insights`, and `_unmatched_side_tags` are deleted.

**Non-goals (still):** no ML, no near-miss, no `speculative:` prefix, no accept-by-insight-group. `=` stays sentinel, `:` stays name regex.

## Suggest extras (shipped)

A **rename recipe for the source files**, then `r`. The TUI does **not** bind, map, or rename headers. `a` on an extra still means “this extra is fine,” not “pair these two.”

**Where (extras screen only).** The A-not-B / B-not-A names live on `place.screen == "extras"` — `App._extras` / `_fill_extras` in `reconcile/tui.py`. Title: **Mismatched columns**. Data: `engine.extras_a` / `engine.extras_b` (`compare.py`), painted as one `#grid` (`side`, `name`, `pending`, `speculative`). Opened from `i` (`OverviewModal` “Mismatched columns” → extras) or next-lever. `OverviewModal` is counts + three jump entries only — **do not** put Suggest there. Not the roster. Not a new `place.screen`.

Put the Suggest block in the same `_extras()` `Vertical`, **below** that extras table (below the A-not-B and B-not-A names). If you split `#grid` into two lists, Suggest still sits under both. `_refill_work`’s extras branch must refresh the block, not only `#grid`.

**A row** is one extra A name, one extra B name, **why**, **preview**. Why is only these deterministic normalizers (stack as needed; name the ones that fired): strip, case, `_`/` `/`-` as one separator class, token sort (split on that class, sort). No Levenshtein. No `near-miss`. Do not revive `extra_insights` or extras-row `speculative` tags.

**Preview:** pretend those two headers were already the same comparable name. Report shared (inner-join) keys that would compare, and pending cell count (raw `!=` on those keys, null→`""`).

**Collisions:** if two recipes claim the same extra, show both. The tool never picks. The file decides.

**Action:** copy/see the target exact header, rename it in the workbook, `r`. Then it is a normal comparable — or it isn’t, and you look again. No apply-in-tool. No clipboard-into-source (the TUI still never writes the workbooks).

**Empty:** omit the block when no recipe hits. Header-only; schema-sized. Do not `to_dicts()` the value frames to score names.
