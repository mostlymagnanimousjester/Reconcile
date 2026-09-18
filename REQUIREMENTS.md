# Strict data reconciliation TUI — requirements

This document is the locked v1 spec. The implementation in this repository follows it.

This document consolidates decisions from the requirements conversation.

---

## 1. Purpose

An investigation TUI for two-sided data reconciliation.

The user loads two sources (A and B), inspects exact differences, and either:

- shrinks remaining differences to zero by editing sources **outside this TUI** and refreshing, or
- accepts known variations in-session (column, exact value-pair, unmatched key, extra) until remaining **pending** count is 0.

This TUI **never acts on the sources**. It only reads them (load/refresh). This is **not** an audit, sign-off, or certification tool. It does not produce a signed report as a product goal.

---

## 2. Non-goals (v1)

- More than two sides
- Databases, APIs, JSON, clipboard, or stdin as inputs
- Fuzzy key matching or fuzzy joins
- Casting, trimming, type coercion, or formula evaluation as part of compare
- In-TUI file browser or path/key editing after launch
- Changing paths, sheets, or key columns inside a running session
- A “New job” action (quit and relaunch)
- File watching (refresh is manual)
- Blanket “ignore this column forever” (acceptances are snapshots; see §9)
- Stacking regex and sentinel into one column-accept draft
- Accept-by-insight-group
- **Do not implement:** copy pending keys/values to the clipboard (or any other export) in order to take them into the source files
- **Do not implement:** write, patch, save, create, or otherwise mutate files under `--a` / `--b`; open those files in Excel or an editor from the TUI; “fix this cell in the workbook.” Load and refresh are **read-only**. The user edits sources only in other tools, then refreshes.
- **Do not implement:** a separate biggest-lever widget, a refresh jump-list overlay, a roster sort-cycle key, or a filter-mode key. Those were cut for fluency (§15).

---

## 3. Compare contract (inviolable)

Compare is **exact raw text**.

- A cell matches iff the two strings are identical.
- The only allowed cast: **true null → `""`**.
- No trim, case fold, numeric coerce, date parse, Unicode normalize, or other transform may affect match/mismatch, remaining counts, or accept/pending state.
- Literal strings such as `"null"`, `"NULL"`, `"NA"`, `"None"` are ordinary values.

**Speculative insights** (§10) may *describe* why two unequal strings look related. They must never change the contract, remaining-diff counts, or pairing.

Every insight shown in the UI must be labeled **`speculative`**.

---

## 4. Sides and inputs

Always exactly two sides: **A** and **B**.

Each side is one of:

- a flat delimited file, or
- an Excel sheet identified as **workbook path + sheet name**.

Allowed combinations:

- two delimited files
- two Excel sheets (one workbook or two)
- mixed (delimited vs Excel)

Formats: delimited text; `.xlsx`; `.xlsm` (macros ignored; data only).

Excel reads: **Polars + fastexcel** only (no OOXML/ElementTree gate). Do not evaluate or follow formulas. Extract **stored/cached values as-is**. Load with `dtypes="string"` (or equivalent fastexcel API) so numbers, bools, and dates stringify rather than hard-fail.

### 4.1 Headers

Header row is required on row 1 of every delimited file and every Excel sheet used. Column names are the header strings as read (exact). Delimited headers have **no** custom embedded-quote handling: names are whatever Polars `read_csv` produces.

### 4.2 Column names

Pairing is by **exact column name** (case-sensitive, space-sensitive: `ID` ≠ `id` ≠ `ID `).

Delimited duplicate headers are **not** a hard fail. Polars may rename later copies (e.g. `val_duplicated_0`). Pairing still uses those exact resulting names, so a renamed copy shows as an extra / missing comparable name.

Excel duplicate column names on one side: **hard fail**.

### 4.3 Excel types

Excel is loaded as **text columns** via fastexcel (`load_sheet(..., dtypes="string")` or equivalent). Numbers, bools, and dates stringify. Do not evaluate or follow formulas; use the stored/cached value as-is. There is no per-cell XML type/formula refuse and no post-load “dtype must be string” hard-fail: after `dtypes="string"` the frame is already Utf8; still `fill_null("")` and drop all-empty rows in Polars.

Blank Excel cells are true nulls → `""`.

Merged cells are **not** a hard fail. Secondary cells in a merge may be empty strings.

Password-protected or OLE workbooks: no custom zip/XML detection. Surface fastexcel/calamine errors as **hard fail** with the absolute path (message quality may be worse than a dedicated detector).

A named sheet that does not exist: **hard fail**, via fastexcel (`SheetNotFoundError` or similar), with path, sheet name, and available sheets if the library provides them. Hidden sheets are usable only if the sheet name is passed on the CLI.

---

## 5. Load rules

Order of operations:

1. Read the source (delimited or Excel).
2. Cast true nulls to `""`.
3. **Drop rows where every column is `""`** (load rule, not a value cast). Needed so trailing blank Excel rows do not become duplicate empty keys.
4. Then apply keys and compare.

Delimited read is `pl.read_csv` (§6.3). Excel stays on the Utf8 Polars frame (`fill_null("")`); do not dump `iter_rows()` to Python lists. Shared unification is `pl.DataFrame`, not `list[list[str]]`. Drop all-empty rows **in Polars**.

Rows where key columns are `""` but other columns have text are kept. Duplicate `""` keys still hard-fail.

**Ragged delimited rows** follow Polars as-is: too few fields are padded with `""`; too many fields raise Polars `ComputeError` (byte offset, not a 1-based record-number message). Trailing blank lines may disappear as all-empty rows after the drop. There is no path+record-number ragged copy.

There is no “header must have ≥ 2 fields” rule (that was a CSV-detector leftover).

**All-empty-key rows that are not all-empty rows** are kept and keyed as `""`.

---

## 6. Encoding and delimiters (delimited files)

Excel paths are not this path. Encoding flags and delimiter flags on an Excel side are **illegal** (hard fail).

There is **no in-TUI delimiter or encoding override**. After the first successful run, the chosen delimiter and encoding are frozen in job identity and reused on refresh (reuse, do not re-detect).

Must work on **Windows**. Default encoding is UTF-8 (ASCII ⊂ UTF-8). Non-UTF-8 files need an explicit CLI override on the next invocation after a UTF-8 hard fail.

### 6.1 Delimiter

Valid values (CLI and stored identity): **comma** (`,`), **tilde** (`~`), **pipe** (`|`), **tab** (`\t`). Semicolon is **not** a candidate.

CLI tokens: case-insensitive names `comma`, `tilde`, `pipe`, `tab`; or the literal character `,` `~` `|` / a tab character / the two-character escape `\t`.

Unknown CLI values: **hard fail** (exit 2) with the flag name, the raw value, and the list of valid values on stderr.

If a side’s path has a `.csv` extension (case-insensitive) and `--a-delim` / `--b-delim` is **not** specified for that side, the delimiter defaults to **comma**. `--a-delim` / `--b-delim` still overrides when given.

Non-`.csv` delimited files (`.txt`, `.dat`, no extension, etc.) **require** `--a-delim` / `--b-delim` for that side. No sniff. No other extension defaults (including no `.txt` → tilde). Missing flag on a non-`.csv` delimited side = hard fail (exit 2) with the flag name and the absolute path.

`--a-delim` / `--b-delim` on an Excel side: **hard fail**.

The character actually used (CLI override or `.csv` comma default) is what is frozen in in-memory job identity.

### 6.2 Encoding (default UTF-8; CLI override)

Default for delimited files is Polars **`utf8`**. There is no BOM→UTF-8→cp1252 detection ladder and no never-latin-1 special-case as the default path. A UTF-8 BOM is still valid UTF-8 (Polars strips it).

CLI override per side, for the next invocation after a UTF-8 hard fail: `--a-encoding` / `--b-encoding`. Default `utf8`. Allowed Polars-native tokens:

- `utf8` (default)
- `windows-1252`
- `utf8-lossy` / `windows-1252-lossy` — explicit opt-in only, never default

Unknown encoding token: **hard fail** with the flag name, the raw value, and the valid list. Do **not** accept `latin1` (prefer `windows-1252`; Polars does not need a latin-1 alias for cp1252). Encoding flags on an Excel side: **hard fail**.

Store the chosen encoding in in-memory job identity like the delimiter.

### 6.3 Delimited ingest (Polars)

Delimited ingest is **`pl.read_csv`** with:

- `infer_schema=False`
- `empty_string_is_null=False`
- `quote_char='"'`
- `has_header=True`
- `separator` from the resolved delimiter (CLI `--*-delim`, or comma when the side is `.csv` and the flag was omitted)
- `encoding` from default UTF-8 or `--*-encoding`
- `glob=False`

Do **not** set `ignore_errors=True` or `truncate_ragged_lines=True`.

Then null→`""` if needed and drop all-empty rows **in Polars**. `pl.read_csv` **is** the frame: no `orient="row"` list round-trip, no stdlib `csv.reader` / `parse_records` / `csv.Sniffer` / `decode_bytes` ladder.

Parse errors (unclosed quotes, long-ragged lines, invalid UTF-8, etc.) are Polars `ComputeError` (byte offset, not 1-based record, not old §16 ragged templates). Surface the Polars message plus the **absolute path** and **side** so the user can retry with `--*-encoding` if it was encoding.

Excel encoding is not a separate concern; fastexcel supplies cell strings (`dtypes="string"`). Excel stays on the Utf8 Polars frame (`fill_null("")`, drop all-empty rows in Polars). Shared unification is `pl.DataFrame`, not `list[list[str]]`. Password/OLE and missing-sheet failures come from fastexcel/calamine, wrapped as `HardFail` with the absolute path (and sheet name / available sheets when the library provides them).

---

## 7. Keys and join

- Keys are **required**, specified on the CLI as a single comma-separated `--keys` value (e.g. `--keys id,year`). No repeated `--key` flags.
- Composite keys are allowed. Order is left-to-right in `--keys`. Names are split on commas only; there is no quoting. After split, surrounding whitespace on each name is stripped; internal spaces are kept (`--keys id, year` → `id`, `year`). Column names that contain a comma are not supported.
- Every key column must exist on **both** sides (exact names) or **hard fail**.
- Duplicate key on a side (after empty-row drop): **hard fail**.
- A key value of `""` is legal. Multiple rows with key `""` on one side: hard fail.
- Key present only in A or only in B: **not** a fail; those rows are review items (A-only / B-only).
- For matched keys, key columns are **identity only**. They are not listed as value diffs.

Join: inner match on the composite key as a tuple of raw strings. A-only and B-only are the set differences.

---

## 8. Columns and what is compared

Let:

- **Intersection** = names present on both sides (exact string)
- **Comparable columns** = intersection minus key columns
- **Extras** = names in A not B, and names in B not A

**Comparable columns** are the only columns whose values are compared for matched keys.

**Extras** are schema differences, surfaced for review (not a hard fail).

### 8.1 How A and B are denoted

Sides are always **A** and **B**, matching `--a` / `--b`. The TUI chrome uses uppercase `A` and `B`. That label is **not** part of the column name and is never concatenated into the header string used for pairing or snapshots.

| What | How it is shown | What is stored / compared |
|---|---|---|
| Comparable column (on both sides) | The **exact header** once (roster `name`). Values in two columns headed `A` and `B` | Pairing and snapshots use the exact header. Values are the two raw strings |
| Context columns on detail | Context **exact header**, with value pair `A` \| `B` | Display only |
| Selected cell footer | Lines prefixed `A:` and `B:` then the full raw string | Same strings as the grid |
| Extra (in A not B, or B not A) | **Exact header** plus a **Side** field `A` or `B`. Do **not** rename to `A.cust_id` | Snapshot `(side, name)` with `name` = exact header, `side` = `A` or `B` |
| A-only / B-only row grid | Headers are exact names. The screen *is* the side; extras of that side appear as additional columns with those exact headers | Row snapshot on that side |
| Exact-value sentinel scan | **One side** (`A` or `B`) plus one exact raw string. Drafts comparable columns where every pending value on that side equals the sentinel. Not a name prefix. `.all()` cannot target both sides (those rows would not be pending) |

The same exact name cannot be an extra on both sides (that would be intersection, hence comparable). Two extras with different names, one on A and one on B, stay two roster rows (`kind` `extra`) and two rows on **Schema extras**, each with its `Side`.

Speculative near-misses on extras may *suggest* that `cust_id` (Side `A`) is like `customer_id` (Side `B`). That does not rename or re-pair them.

Missing **key** column on either side: hard fail.

After the first successful run, the user cannot change paths, sheets, or key columns. File *contents* may change on refresh (§9, §12).

On refresh, non-key schema drift:

- New both-sides name → new comparable column (new roster row)
- Name only on one side → extra
- Vanished extra → drops
- Missing key column → in-TUI error, **keep last good state** (if the TUI is already up)

---

## 9. Diffs, pending, accepted

Remaining work is the **pending** set. The job is “done” when pending count is 0 (every difference is gone from the sources, or accepted).

### 9.1 Kinds of difference

| Kind | Meaning | Counts toward remaining pending as |
|---|---|---|
| Cell mismatch | Matched key; comparable column; `A` string ≠ `B` string | 1 per pending cell |
| A-only key | Key tuple exists only on A | 1 per pending key |
| B-only key | Key tuple exists only on B | 1 per pending key |
| Extra column | Name on one side only | 1 per pending extra |

**Remaining pending total** is the sum of those four. Exit `0` requires this total to be 0. Unmatched keys and extras are remaining work, not a side room.

### 9.2 Accept actions

| Action | Effect |
|---|---|
| Accept one cell | Snapshot this `(key, column, valA, valB)` |
| Accept entire column | Bulk-accept **all current pending** cell mismatches in that column, each as its own snapshot. Does **not** mean “never compare this column again.” Immediate from roster or detail on a **single** column |
| Confirm drafted columns | Same snapshot rule as accept entire column, applied to every **still-checked** column in the in-flight **column** draft (§9.5). Then the draft is empty |
| Accept entire pair | Snapshot every current pending cell in this column with exact `valA`, `valB`. Immediate from the pair list (`a`). Stay on that pair list; selection moves to the pair that was **below** (or the new last remaining pair / empty). Does **not** next-lever |
| Confirm drafted pair | Snapshot every **still-checked** pending cell in the in-flight **pair** draft (§9.6). Then the draft is empty; then **next lever** |
| Accept same pair on columns | Modal (`m` on roster / pair list): pick target columns, pick one exact `(valA, valB)` from the grouped union, snapshot that pair on every selected column that still has it. Same grain identity as pair accept (exact strings, not fuzzy). Stay on the current screen; selection follows the below/last-remaining rule when the focused row vanishes |
| Accept one unmatched key | Snapshot `(side, key, row snapshot)`. On the unmatched-key grid, then the next pending key in that grid (not next lever) |
| Accept all unmatched keys on a side | Bulk snapshot of current A-only or B-only keys. Immediate from the roster row or `A` on that grid. Then **next lever** |
| Accept one extra column | Snapshot `(side, column name)`. Immediate from Schema extras. Stay on that list; selection moves to the extra that was **below** (or the new last remaining / empty). Does **not** next-lever |
| Undo | That snapshot returns to pending (same session) |

Accept entire column is available immediately from the **roster** (on a comparable-column row) and from **column detail** (one column, no draft).

Batch column accept uses a **column draft** on the roster only (§9.5). Pair accept uses a **pair draft** on column detail only (§9.6). At most **one** draft in flight in the session (column XOR pair). Insights cannot accept. There is no “accept this insight group.”

### 9.3 Acceptance identity and refresh

Acceptances are **snapshots of a specific difference**, not a standing ignore rule.

Sources are updatable. The user **manually refreshes**. Re-read live files at the frozen paths/sheets, re-join, re-compare, re-apply snapshots.

| Acceptance | Stays accepted | Back to pending | Drops (not a diff) |
|---|---|---|---|
| Cell `(key, column, valA, valB)` | Still matched; same column; both strings unchanged | Same key+column, valA or valB changed | Key gone, or values now equal, or column gone |
| Column (bulk) | Each covered cell still matches its snapshot | Changed cells; **new** mismatches in that column are pending (not auto-accepted) | Cells that are no longer diffs |
| Unmatched key `(side, key, row snapshot)` | Still only on that side; row strings unchanged | Still only on that side; any cell text changed | Key now on both sides, or key gone |
| Extra `(side, name)` | Still only on that side | — | Now on both sides, or name gone |

Undo is in-session.

All reconcile state lives in memory for this process only. There is no export/reimport (§13).

### 9.4 Refresh UX

Refresh is manual.

On success: recompute counts and pages; show a short **delta in the footer** (e.g. pending `40→12`, accepted `10→8`, `3 returned to pending`). **Stay put** if that screen/column/pair still exists. If it vanished, **next lever** (§15.9). Else the roster.

Do **not** open a jump-list overlay. Rows that **returned to pending** are marked in the lists already on screen (reverse video / standout, red-lens safe §15.8) until the next successful refresh replaces the set.

In-flight **column** draft: drop names that vanished or now have pending 0; do not re-run regex or sentinel. In-flight **pair** draft: drop cells that are no longer pending or whose `valA`/`valB` changed; if none remain, the pair draft is cancelled (back to the pair list).

On failure (locked file, parse error, missing key column, Excel fastexcel/calamine error, etc.): **keep the last successful in-memory state**, show the error in the TUI, do not exit. This is standard behavior, not a special case. Error text must include the same raw identifiers as §16 (keys, column names, types, paths).

### 9.5 Power-user batch column accept

On the **roster**, a power user can select many comparable columns, review a **draft**, then Confirm. Selectors never accept by themselves. Compare stays exact raw text. Unmatched-key and extra rows are never part of a column draft.

**Single-column accept stays immediate.** It does not wait for Confirm and does not require a draft.

#### Draft

One in-session **column draft**: a set of comparable names (non-key A∩B). Default empty.

| Rule | |
|---|---|
| After a selector Run | Draft := hits that currently have **at least one pending** mismatch, all **checked**. User then unchecks exceptions |
| Zero pending | Column is already reconciled for remaining-work. **Not** drafted. No snapshots. Do not treat an empty pending series `.all()` as true. **Not** a standing ignore: after refresh, new pending cells are pending |
| Toggle | Space flips the focused **comparable-column** roster row in/out of the draft (only while a draft is in flight). No-op on `A-only` / `B-only` / `extra` rows |
| Confirm (`y`) | For each still-checked name, accept-entire-column (pending snapshots only). Then draft empty. Undo is **per column**, not one bundle. Then **next lever** (§15.9) |
| Cancel (`Esc` on roster) | Draft empty; no accepts |
| In-flight | At most one draft in the session. Confirm or Cancel before another regex/sentinel Run, and before starting a **pair** draft (§9.6) |
| Quit | Draft is discarded. Unconfirmed drafts never survive quit. Confirmed snapshots live only in memory until quit |

Refresh does not re-run the selector. Drop from draft: name gone, or pending count now 0. New comparable columns are not added.

If the user **immediately** accepts a column that is in the draft (`a` on roster), that column is snapshotted now and **removed** from the draft; other drafted names remain. Immediate accept of the last drafted column (or immediate `a`/`A` with no draft) also goes to **next lever** (§15.9).

#### Selectors (independent)

Regex and sentinel do **not** stack, union, or intersect. Each Run starts from an empty draft. Do not stack regex and sentinel into one draft.

**Name regex** (roster `/`):

- Applied to exact comparable names only (not keys, not extras, not values).
- Python `re.search`, case-sensitive; user may put `(?i)` or `^` `$` in the pattern.
- Empty or invalid pattern: in-TUI error, draft unchanged.
- **Zero hits** (no pending comparable name matches): in-TUI ERROR, no draft started, stay.

**Exact-value sentinel** (roster `=`):

The pending universe is `A ≠ B`. An `.all()` predicate on the **same** constant therefore cannot be true on **both** sides: if every pending `A` and every pending `B` were `——`, those cells would be equal and not pending. So the scan is **single-side**.

- User must choose **Side `A` or Side `B`** (modal tabs, same denotation as §8.1). Required; no default that means “both.” Run is refused until a side is selected.
- One exact string (raw text; no trim, no regex, no expression). Empty sentinel `""` is **legal** (every pending value on that side is the empty string).
- Per comparable column: that side’s values on **pending mismatches only** (raw strings, nulls already `""`). Draft the column iff it has **at least one pending** mismatch **and** every pending value on the chosen side is **exactly** that string (Python/Polars string equality).
- Implemented in **Polars**: pending cells grouped by column, `(side == sentinel).all()`. Zero-pending columns are never grouped and are **never** drafted (do not treat empty-series `.all()` as true).
- Invalid side or other engine error: in-TUI, draft unchanged.
- **Zero hits**: in-TUI ERROR, no draft started, stay. Sentinel Enter with no side selected: ERROR on the modal.

Choosing Side `B` does not look at A, and vice versa.

While a draft is in flight, `/` and `=` are disabled (or error: confirm or cancel first).

#### UX (roster)

- Full roster stays visible (not a drafted-only list). Drafted rows show a check.
- Opening `/` opens a regex modal. Opening `=` opens a sentinel modal with **Side tabs `A` | `B`** plus one exact-string field; `Enter` Runs; `Esc` closes the modal without changing the draft. Run is refused until a side is selected.
- After Run, footer shows `draft N` plus Confirm / Cancel / toggle.
- Column **detail** has no regex/sentinel. Detail accept column remains immediate (`A`). Pair accept is §9.6, and is refused while a **column** draft is in flight.

### 9.6 Exact pending-pair accept

On **column detail** (Pending view), pending mismatches for that column are grouped by exact raw `(valA, valB)`. Those groups are a first-class **exact** object (not speculative). Accepting a pair writes the same per-cell snapshots as `a` on each remaining drafted cell.

This is how a known recode (`Y` vs `Yes`) is accepted without accepting the whole column and burying unrelated pending cells.

**Not** accept-by-insight: the pair is two exact strings. Speculative chips on a pair (trim, date, …) must not be the accept target.

#### Pair list

Computed in Polars from **pending** cells of the current column only. Always a **paged list** of pairs (100). Sort: **count descending**, then `valA` raw, then `valB` raw. There is no categorical count matrix.

Each row: exact `A` string, exact `B` string, pending count. The DataTable is a navigator; the pane shows the focused pair’s full `A:` / `B:` strings with wrap. Side denotation is the two columns headed `A` and `B` (§8.1), not a name prefix.

Pair list is shown only on the **Pending** view tab. Hidden on Accepted / Equal / All matched.

#### Pair draft

Selecting a pair (not Confirm) fills a **pair draft**: every current pending cell in this column whose values are exactly those two strings, **all checked**. The cell grid shows that set (100-row pages, key-tuple order). The user unchecks exceptions, then Confirm.

| Rule | |
|---|---|
| After selecting a pair | Draft := matching pending cells, all checked. If that set is empty, do not start a draft (already reconciled for that pair) |
| Toggle | Space flips the focused **grid** row in/out of the draft |
| Confirm (`y`) | Snapshot every still-checked cell `(key, column, valA, valB)`. Undo is **per cell**, not one bundle. All-unchecked: in-TUI ERROR, stay, draft live (no next lever). Else draft empty, remember this pair as **last pair**, then **next lever** (§15.9) |
| Cancel (`Esc`) | Draft empty; **back to the pair list** (not the roster). No accepts |
| In-flight | Session has no other draft. Confirm or Cancel before another pair, and before roster regex/sentinel |
| Zip / quit | Pair draft is **never** persisted. Confirmed cell snapshots only |

Immediate `a` on a drafted cell: snapshot that cell now; drop it from the pair draft.

Immediate `A` (whole column) is **refused** while a pair draft is in flight (in-TUI ERROR: confirm or cancel first). `A` does **not** `accept_column` in that state — that would bury Space-unchecked exceptions. `A` = whole column only when no pair draft is live.

`.` **Repeat last pair** (column detail only: pair list / cell step; no pair draft in flight): if already on that column’s pair list, start a pair draft for the remembered `(column, valA, valB)` if that column still exists and any pending cells still have those exact strings. Does **not** auto-accept. If currently on **another** column’s pair list, jump to that column’s Pending pair list focused on the last pair; do not open the cell-step draft until `Enter`. New pending rows with that pair after refresh are included only when `.` is pressed from that column’s pair list. If none remain, go to next lever. If the last-pair **column is gone**: in-TUI ERROR (do not silently next-lever). Refused if a **column** draft is in flight. Refused on roster / Overview / unmatched / extras.

Confirm (`y`) with every pair-draft cell **unchecked**: in-TUI ERROR (nothing to confirm / all unchecked). Stay on the cell step; the draft stays live. Do not next-lever.

Refresh: §9.4. New pending cells that happen to have the same two strings are **not** auto-drafted or auto-accepted.

#### UX (detail: one thing at a time)

Pending view shows the **pair list only** (always the paged list). No cell grid on this step.

- `Enter` on a pair opens the **cell step**: that pair’s pending rows as a pair draft, all checked. First-difference caret and context columns exist only on this step.
- `Esc` on the cell step cancels the draft and returns to the pair list.
- `Esc` on the pair list returns to the roster.
- `a` on the pair list **immediately** accepts that entire pair (all current pending cells with those exact strings). Happy path for a clean recode: top pair, `a`.
- Refused if a **column** draft is in flight.

---

## 10. Speculative insights (view/filter only)

Computed in Polars from already-established exact diffs (or from extra/unmatched sets as noted). Labeled **`speculative`**. Never change remaining counts.

They may be used as **filters/explanations**, not as accept actions.

### 10.1 On matched-key cell mismatches

- Equal if trim
- Equal if case-fold
- Equal if trim+case
- Both numeric-looking and equal as numbers (`1` vs `1.0`)
- Invisible/odd whitespace (NBSP, tabs, trailing space)
- Shared value pattern (e.g. A always `Y`/`N`, B always `Yes`/`No`)
- **Same calendar date** (see §10.4)

### 10.2 On extras

- Name would pair if trim/case
- Near-miss names (`cust_id` vs `customer_id`)

### 10.3 On A-only / B-only keys

- Would match the other side if trim/case on the key strings

### 10.4 Dates

Try a fixed list:

- `YYYY-MM-DD`
- `YYYY-MM-DDTHH:MM:SS`
- `M/D/YYYY`
- `D/M/YYYY`
- `YYYYMMDD`

Emit `speculative: same date` only when each side parses **unambiguously** to the same calendar date. If both US and EU parses are possible, emit **nothing**.

### 10.5 Categorical statistic (not an accept path, not a layout switch)

A comparable column is treated as **categorical** when, among **pending** cells for that column:

- `nunique(A) ≤ 30`, and
- `nunique(B) ≤ 30`, and
- `nunique(A ∪ B) ≤ 50`

That heuristic is a **roster statistic** (`cat` yes/no) only. Pair list layout is always the paged list (§9.6). Pair **counts** are exact. Pair **accept** is §9.6, not this section.

Speculative chips may still annotate a pair (trim, same-date, …). Those labels must not be the accept target.

These thresholds and the date format list in §10.4 are locked for v1.

### 10.6 Not v1

- Fuzzy unmatched-key matching (Levenshtein, “this row looks like that row”)
- Cross-column value hunting
- Date-format guessing beyond §10.4
- Accept-by-insight-group
- Clipboard or any TUI action that writes or opens the source files (§2)

---

## 11. Architecture: Polars owns the data

Stack: **Python 3.13**, Polars, fastexcel, Textual. Must run on Windows. Launch from **PowerShell** via `Reconcile.py` (see §12, §17).

Polars owns **load, join, compare, remaining sets, snapshots, paging, roster aggregates, accept/undo, and refresh**. Python is not the paging engine.

Python may hold **only**:

1. a page of ≤100 dicts **after** `.slice`
2. a small draft checkbox set (column names, or pair-draft **exceptions**, not the checked universe)
3. keymap / Place / error banner
4. schema-sized roster handoff after cache build
5. zip ser/de once per export/open
6. two-string `cell_insights` / `first_diff` for paint
7. name regex on comparable names
8. extra-name near-miss on headers
9. one HardFail `.row(0)` (duplicate-key identity)

Anything else that `to_dicts()`s a full frame on a hot path is a defect. The TUI must **not** convert full frames to Python objects.

- Batch sentinel scan (§9.5) runs **in Polars**: pending cells grouped by column, all values on the chosen side == the exact sentinel (`.all()` per column; do not use a Polars `=` selector). Pending-pair lists (§9.6) are a Polars `group_by` of exact `valA`, `valB`. Do not pull full columns into Python to test predicates.
- Cell/key lists are paged at **100**. Roster is schema-sized (one row per comparable column plus unmatched/extra remaining-work rows) and may be materialized **once per snapshot apply**.

Kernel split (facade still `reconcile.engine.Engine`; TUI imports the facade, not compare internals): `compare.py` (join / unpivot), `snaps.py` (accept/undo tables), `pages.py` (slice then materialize), `roster.py` (cache / next lever). Do not split load / delimited / excel / cli. Keep `insights.py`.

---

## 12. CLI

The TUI does not collect paths, sheets, or keys. Job identity is CLI only.

Invoked from PowerShell:

```powershell
python Reconcile.py --a C:\data\left.csv --b C:\data\right.csv --keys id,year
python Reconcile.py --a C:\data\left.csv --b C:\data\right.csv --a-delim tilde --keys id
python Reconcile.py --a .\left.dat --b .\right.txt --a-delim pipe --b-delim tilde --keys id
python Reconcile.py --a C:\data\left.csv --b C:\data\right.csv --a-encoding windows-1252 --keys id
```

| Flag | Meaning |
|---|---|
| `--a PATH` | Side A file. May be relative to cwd; **absolute path stored** in memory |
| `--b PATH` | Side B file (same path rule) |
| `--a-sheet NAME` | Required if A is `.xlsx`/`.xlsm` |
| `--b-sheet NAME` | Required if B is `.xlsx`/`.xlsm` |
| `--a-delim VALUE` | Delimiter for side A if it is a delimited file (§6.1). Defaults to comma when A is `.csv`. **Required** for other delimited extensions. Per-side so mixed jobs work. |
| `--b-delim VALUE` | Delimiter for side B if it is a delimited file. Defaults to comma when B is `.csv`. **Required** for other delimited extensions. |
| `--a-encoding VALUE` | Encoding for side A delimited file (§6.2). Default `utf8`. |
| `--b-encoding VALUE` | Encoding for side B delimited file. Default `utf8`. |
| `--keys NAMES` | Comma-separated key column names, in order. Exclusive form; not repeatable `--key`. |

Rules:

- `--a`, `--b`, and `--keys` are required. `--keys` must contain at least one non-empty name. Sheet flags required per Excel side. Delim flags **required** per non-`.csv` delimited side (§6.1); optional on `.csv` (default comma). Encoding flags optional (default `utf8`).
- Duplicate names inside `--keys`, or an empty segment (e.g. `id,,year`): hard fail.
- Key name not on both sides: hard fail (stderr+exit at initial load).
- Paths on `--a` and `--b` **may be relative to the invocation cwd**. Immediately resolve with the equivalent of `Path(p).expanduser().resolve()` (absolute, normalized) and **store only absolute paths** in in-memory job identity and TUI Overview. If a relative path does not exist, hard fail with the **resolved absolute path** in the error text.

Initial load/parse/schema/dup-key/unknown-delimiter/unknown-encoding/missing-delim/missing-path failures **before the TUI is up**: message on **stderr** including **raw identifiers** (§16), process **exit**.

Once the TUI is up: errors stay in the TUI with last good state (§9.4). Stderr+exit only if Textual cannot start.

Quit and relaunch to compare a different pair of sources or different keys.

---

## 13. No persistence

There is **no** `--session` flag, `.recon.zip`, or TUI export/open of reconcile state. Identity, confirmed snapshots, place, drafts, and context columns live **in memory** for the current process only. Quit discards everything. Relaunch with CLI identity flags. Refresh re-reads the live sources at the frozen in-memory paths (and frozen delimiter/encoding).

---

## 14. Exit codes

| Code | When |
|---|---|
| `0` | User quits and remaining **pending** is 0 (including all-accepted) |
| `1` | User quits and pending remains |
| `2` | Hard fail (load/parse/dup keys/unknown delimiter/unknown encoding/missing delim/Excel read error/etc.) |

---

## 15. TUI screens

Do not use the word “summary” for two different screens. Names below are canonical.

**Happy path (the product):** launch → **column roster** (shared comparable columns in table A import order) → `a` accepts that column **in place**, or `Enter` inspects the pile then `a` on the selected grain → remaining unmatched rows / mismatched columns from the **overview modal** (`i`) or next lever → `r` after the user saves the workbook in another tool. Regex `/`, sentinel `=`, Equal/All-matched tabs: real, behind glass. A-only keys, B-only keys, and mismatched columns (headers on one side only) are remaining work opened from the overview or via next lever — they are not column-roster rows.

A footer/status line is **always visible** (§15.6).

**Home is the work.** After a successful initial load, land on the **roster**, not Overview. Overview is a modal (`i`), not a screen. `last_pair` is kept in memory for `.` repeat when it still exists.

### 15.1 Overview (modal, not a screen)

Quiet counts and job identity. **Not a `place.screen`.** Open with `i` from any work screen (Help-style modal). `Esc` closes the modal and returns focus to the same screen. A leftover `place.screen=overview` maps to the roster.

Shows:

- Frozen job identity (absolute paths, sheets, keys, encoding/delimiter)
- Exact remaining counts (pending vs accepted): pending columns, A-only keys, B-only keys, mismatched columns (headers on one side only), mismatched cells, **remaining work items** (the grain sum used for exit `0`)
- Entry points: **A-only keys**, **B-only keys**, **Mismatched columns** (`Enter` opens that list)
- Speculative chips only as secondary, labeled `speculative`

`a` / `A` on Overview: in-TUI ERROR (not remaining work), not a silent no-op. `n` / `p` ERROR (no pages).

No separate biggest-lever widget. The roster’s first **pending column** (A import order) is that lever.

### 15.2 Column roster (home)

The roster is **pending comparable columns**. A column whose *shared* (matched-key) rows are all equal has nothing to review and is **auto-accepted / hidden**, even when A-only or B-only keys exist (unmatched keys are not cell diffs). After the user accepts a column, pending 0 → hidden **and the cursor stays on the roster**, focused on the column that was **below** the accepted row (or the new last remaining column / empty). `v` toggles accepted / equal columns back onto the list (default: hidden). When shown they are dim, with a compact **status** (`pending` / `accepted` / `equal`), so they do not look like remaining work: pending section first, then settled. Enter still drills for inspection.

A-only keys, B-only keys, and mismatched columns **must not** appear on this table looking like comparable columns. Open them from `i` overview or next lever.

One row per pending comparable column (`kind` `column`, pending > 0), plus settled comparable columns when `v` is on. `name` is the exact header. Pending count is mismatched cells in that column.

**Concentration** (columns only): pending count of the column’s largest exact `(valA, valB)` pair divided by that column’s pending count (0 if pending is 0). Shown as **top-pair %**.

**One order (not a key):** table A import order (`eng.comparable` / original A headers, keys excluded). No pending/concentration sort and no sort-cycle. When `v` is on, keep two visual sections (pending, then accepted/equal); each section is still A import order.

Row 1 of **pending** work is the first remaining *column* in A import order (settled rows never take the lever chrome). Clearing comparable columns is not “done” while unmatched rows or mismatched columns remain (footer counts + overview / next lever).

**Filter box (always visible):** case-insensitive substring on column `name`. View only; does not change pairing or drafts. Focus the box to type; `Esc` returns focus to the list (does not have to clear the text). Empty box = all pending columns (and settled, if `v` is on). `/` is **not** this filter; `/` opens regex → column **draft**.

Columns on the roster:

- **draft** (`[ON]` / `[off]` while a column draft is in flight; omitted otherwise — not an “accept” column)
- name
- **status** (`pending` / `accepted` / `equal`) only while `v` is showing settled columns
- pending count
- **top-pair %**
- equal count
- categorical yes/no
- compact speculative tags

There is no separate **accepted-count** column next to a draft/accept checkbox. Pending columns show because they are pending; accepted columns are hidden unless `v`.

Row 1 of pending work is the first remaining column in A import order (bold + underline, §15.8). Rows that contain **returned-to-pending** items after the last refresh: standout/reverse on the row until the next successful refresh. Drafted columns are bold `[ON]`; unselected potential targets are dim `[off]`. Settled rows are dim.

**Immediate** on the focused column row:

| Kind | `Enter` | `a` |
|---|---|---|
| `column` (pending) | Pair list | Accept entire **column** now; **stay on the roster** |
| `column` (accepted / equal, `v` on) | Accepted or Equal tab | ERROR: no pending cells |

Grain (pair, cell, one unmatched key, mismatched column) is accepted with the same `a` on the matching detail screen — selection decides the grain.

**Batch:** `/` or `=` → draft of **comparable columns only** (all `[ON]`) → banner + footer say **y ACCEPT selected**; `Space` select/deselect; `Esc` cancel. After Run, the next action is `y`.

Roster is not Polars-paged.

### 15.3 Column detail (pair list, then cells)

One comparable column. **One thing at a time.**

**Pending (default)** is the **pair list only** (exact `A → B` counts, §9.6). Always paged 100. Sort: count desc, then `valA`, `valB`. Speculative chips on a pair are labels only. The pane shows the focused pair’s full strings. Categorical (§10.5) is a roster `cat` statistic, not a matrix.

- `Enter` → **cell step** (pair draft of those exact strings). Grid, first-difference, context columns.
- `Esc` from cell step → pair list (draft cancelled if not confirmed).
- `Esc` from pair list → roster.
- `a` on a pair → immediate accept that pair; stay on the pair list, cursor on the pair that was below (or the new last remaining / empty).
- `A` → immediate accept entire column.
- Named tabs **Pending / Accepted / Equal / All matched** switch views. There are no `1`–`4` keys. Tab switch is refused (error, draft kept) while a pair draft is in flight.

**Accepted / Equal / All matched** tabs: cell grid for that set, no pair list. Behind glass; deadline work stays on Pending.

Grid (cell step or non-Pending tabs), 100-row pages:

- key columns (always)
- A value and B value (raw; **wrap**)
- speculative labels when a mismatch
- **context columns** (cell step and non-Pending grids)

**Context columns:** both-sides intersection, excluding keys and the column under examination; A|B raw; per-column, in memory; display-only. Picker `c` on the cell step.

**First-difference caret:** on the cell step footer pane and focused `A`/`B` cells, mark the first differing Python `str` index (after null→`""`). Reverse/standout on both sides. Prefix/length-only differences count. Exact, not speculative. Red-lens safe (§15.8).

Paging: key-tuple order. Pair list paging: 100.

Returned-to-pending cells (and pairs/columns that contain them): standout until next successful refresh.

### 15.4 A-only keys / B-only keys

Reachable from the overview modal (`i` then Enter), or next lever. Same grid either way.

100-row pages. Order: composite key tuple of raw strings.

Each row: key columns + all other columns on that side, raw — comparable **and** extras only on that side.

`a` accept one key (then the next pending key **below** in this grid, or the new last remaining pending key; do not jump to the top or the roster); `A` accept all unmatched on this side then **next lever**; `u` undo; `Esc` roster.

### 15.5 Mismatched columns

Reachable from the overview modal (`i` then Enter), or next lever. Same list either way.

Headers that exist on one side only. Exact header + **Side** `A` or `B`; speculative near-misses labeled `speculative`. Order: exact name. `a` / `u` that column; `a` stays on this list and moves to the extra that was **below** (or the new last remaining / empty). `Esc` roster.

### 15.6 Footer (always on)

The **only** persistent chrome besides the work list. Show what you can do **now**, not the full keymap (`?` has the rest).

Each number is one noun. Do **not** sum cells + unmatched rows + header names into one “cells” (or lumped “pending”) figure.

- **pending columns** (comparable columns with pending cell mismatches)
- **unmatched rows** (A-only keys + B-only keys)
- **mismatched columns** (headers on one side only)
- On the roster: `v show accepted` / `v hide accepted`; `m same pair`
- Refresh delta after `r` (including returned-to-pending count)
- Page `n/m` when paged
- Pair list vs cell step when on detail
- `draft N` + `y` / `Esc` / `Space` when a draft is in flight
- Speculative fragments labeled `speculative`

### 15.7 Commands / keys

**Same keys everywhere.** Do not ship a second keymap when a draft starts; `Space` / `y` / `Esc` simply become useful.

Apply when focus is **not** in a text input (filter box, regex/sentinel modal). In a field: typing goes to the field; `Enter` runs/confirms the field; `Esc` leaves the field (modal: close without run; filter box: back to list).

| Key | Meaning |
|---|---|
| `Enter` | Drill: roster column → pair list; pair → cell step (draft); overview-modal entry → that list; modal → Run |
| `Esc` | Back: close modal → cancel roster/cell-step draft → parent screen (pair list / A-only / B-only / extras → roster). Roster `Esc` stays (overview is `i`, not a screen). On pair list / detail, a **column** draft stays live (`Esc` is back, not cancel) |
| `Space` | Toggle focused **column** row in the current column draft (`[ON]` / `[off]`); ERROR if no column draft (cheap) |
| `a` | Accept the **current selection**: roster column (stay on roster), pair (stay on pair list), cell (cell step), one unmatched key, one mismatched column. After the current row is removed/hidden, focus the item that was **below** it (or the new last remaining / empty). Do not jump to the top. Do not drill. ERROR on Accepted / Equal / All matched. 0-pending roster column: stay, ERROR, do not next-lever |
| `A` | Accept **all** on this screen: entire column from the pair list / cell step with **no** pair draft, or **all unmatched on this side** (A-only/B-only **grid**). Same as `a` on the roster (selection is one column). Refused while a pair draft is in flight. Refused on Accepted / Equal / All matched (switch to Pending). 0-pending roster column: stay |
| `y` | Confirm current draft; no-op if none; then next lever. All-unchecked pair draft: ERROR, stay, draft live. After `/` or `=` the obvious next action is `y ACCEPT selected` |
| `u` | Undo focused grain. After next lever, `u` undoes the last accepted grain (one last action). ERROR if nothing snapshotted for that grain (and no last grain). `U` undo entire column on the **pair list** only (ERROR elsewhere, including cell step where a pair draft is always in flight) |
| `r` | Refresh (stay put; mark returned-to-pending) |
| `.` | Repeat last pair as a new draft (§9.6); column detail only (pair list / cell step); refused if a draft is in flight; ERROR if last-pair column is gone |
| `/` | Roster: regex **column draft** (not the filter box). ERROR off roster |
| `=` | Roster: exact-value sentinel **column draft** (escape hatch; not the happy path). ERROR off roster |
| `i` | Overview modal (counts + unmatched rows / mismatched columns). Esc closes. ERROR is not a screen change |
| `v` | Roster: toggle showing accepted / equal columns (default hidden). Footer hint. ERROR off roster |
| `m` | Roster / pair list: same exact pair on selected columns (column picker, then grouped-union pair picker). Footer hint. Refused while a draft is in flight. ERROR off roster / pair list |
| `c` | Context-column picker (cell step). ERROR off cell step |
| `n` / `p` | Next/prev page on paged screens. Roster / overview modal: ERROR (page unused), do not increment `place.page`. Last page `n`: stay, ERROR, no wrap |
| `q` | Quit; discard unconfirmed draft |
| `?` | Help |

No `f`, `s`, or `j`.

View-filter tabs on detail stay named tabs (Pending / Accepted / Equal / All matched). There are no digit keys `1`–`4`. Pending is pair list; the others are cell grids. Tab switch while a pair draft is in flight is refused (error; draft is not cleared). `Esc` cancels the draft.

### 15.8 Visual language (red-lens safe)

The TUI is used with **maximally blue-blocking glasses (red lenses)**. Blue, cyan, and green are unreliable or invisible. **Hue must never be the only signal.** Encode meaning with luminance, weight, underline, reverse video, and glyphs first; warm hues only as extras.

| Role | Signal (must work in red/amber/black) | Allowed extra hue |
|---|---|---|
| Pending / remaining work | **Bold** + high luminance | Bright yellow or bright white |
| Accepted | Dim (lower luminance) | — |
| Equal / not a diff | Dimmer than accepted | — |
| Drafted / checked | **Reverse video** (fg/bg swap) and/or underline | Orange/amber underline |
| Focused row | Reverse or a `>` glyph in the gutter, not a blue bar | — |
| Speculative chips | Dim + the word `speculative`; optional italic | No blue |
| Top roster row (the lever) | Bold + underline | Yellow |
| Returned-to-pending | Reverse/standout in the existing list; not a new screen | Orange/amber or yellow |
| First-difference | Reverse/standout on the disagreeing characters | Yellow/white, not blue |
| Error | Bold + the word `ERROR` | Red (still reads as bright through red lenses) |
| Pending = 0 | Bold high luminance, not a green “success” | White/yellow |

Palette: dark background; foreground default, bright white, yellow, orange/amber, red; gray luminance steps. Do **not** use blue, cyan, or green as the sole distinguisher of pending vs accepted vs equal vs error vs success.

### 15.9 Deadline navigation

**Next lever** after a bulk accept (pair `y`, column-draft `y`, immediate whole-column `A` from the pair list, `A` on an unmatched-key grid):

1. If the current column still has pending pairs, focus the next-highest-count pair on that column’s pair list.
        2. Else the next roster cache row with pending > 0 from the **unfiltered** cache (A import order for comparable columns). **Clear `roster_filter`** on this jump:
   - `column` → that pair list, top pair focused
   - `A-only` / `B-only` → that grid, first pending key focused
   - `extra` → mismatched-column list, that header focused (ready for `a`)
3. Else the roster (including pending total 0).

**Roster `a` / roster `A` does not next-lever.** It accepts the focused column, hides it (unless `v`), and stays on the roster with the column that was **below** focused (or the new last remaining / empty).

**Pair-list `a` and extras `a` do not next-lever.** They stay on that screen and move selection to the item that was below (or the new last remaining / empty).

Single-cell `a` on the cell step does not jump columns; it advances to the next pending row **below** in that grid (cursor and page follow `focused_key`). If that was the last cell, stay on the new last remaining cell. If that pair is exhausted, go back to **this column’s pair list**, not next lever. Single-key `a` on an unmatched-key grid does not jump the roster or wrap to the top; it advances to the next pending key below, or the new last remaining pending key.

**Repeat last pair** `.` — §9.6. In memory only. Never auto-accepts.

Refresh: stay put; footer delta; mark returned-to-pending in place. No jump list.

---

## 16. Hard fail vs in-TUI error

Hard-fail and in-TUI error text must include **raw identifiers** so the user can find the row in the source file themselves (this TUI will not open or copy into the file):

| Failure | Must print |
|---|---|
| Duplicate key | Side (`A`/`B`), exact key tuple strings, occurrence count |
| Delimited parse error (unclosed quote, long-ragged line, invalid encoding, …) | Polars `ComputeError` text, **absolute path**, side (`A`/`B`) |
| Missing delimiter flag (non-`.csv` delimited side) | Flag (`--a-delim` / `--b-delim`) and absolute path |
| Excel read error (password/OLE, corrupt file, …) | fastexcel/calamine message and **absolute path** |
| Missing Excel sheet | Path, sheet name, available sheets if fastexcel provides them |
| Missing key column | Exact name, which side lacks it |
| Duplicate column names (Excel) | Side, exact duplicated header |
| Unknown delimiter flag | Flag (`--a-delim` / `--b-delim`), raw value, valid values |
| Unknown encoding flag | Flag (`--a-encoding` / `--b-encoding`), raw value, valid values (`utf8`, `windows-1252`, `utf8-lossy`, `windows-1252-lossy`) |
| Missing path | The absolute path |

| Situation | Behavior |
|---|---|
| Textual cannot start | stderr + exit `2` |
| Initial CLI load fails (missing file, unknown delimiter, unknown encoding, missing delim, Polars parse error, Excel dup columns, dup keys, missing key column, Excel fastexcel/calamine error, missing sheet) | stderr + identifiers + exit `2` |
| After TUI is up: refresh fail, invalid regex/sentinel, second selector or pair-draft start while a draft is in flight | Stay in TUI, last good state (draft unchanged unless the rule says drop/cancel), show error with identifiers |
| User quit, pending = 0 | exit `0` |
| User quit, pending > 0 | exit `1` |

---

## 17. Packaging

- Entry: `Reconcile.py`, launched from **PowerShell** (`python Reconcile.py ...`)
- **Python 3.13**
- Dependencies: Polars, fastexcel, Textual (and their transitive deps)
- `pyproject.toml` for the environment/deps is fine; the user-facing command is the script, not a separate console-script name
- No extra services, no database, no auth

---

## 18. Open items for review

1. Schema-extras list sort (exact name is the working rule).
2. Windows terminal host beyond PowerShell (Windows Terminal vs conhost) if that matters in practice.
3. UTF-16 delimited files (e.g. Excel “Unicode Text”) are **not** specified; v1 default is UTF-8 with optional `windows-1252` / lossy overrides.

---

## 19. Decision log (locked)

| Topic | Decision |
|---|---|
| Sides | Always 2 |
| Inputs | Delimited + xlsx/xlsm; 2 sheets max compared |
| Compare | Exact raw text; null → `""` only |
| Keys | Required, composite OK via `--keys a,b`; A-only/B-only reviewed; dups hard fail |
| Column pair | Exact name; extras surfaced |
| Product | Investigation TUI; in-memory acceptances; shrink or accept |
| Excel | fastexcel → Polars only; `dtypes="string"`; stored/cached values as-is; no formula evaluation; silent merges; password/OLE and missing sheet via fastexcel → HardFail |
| Headers | Required |
| Platform | Windows PowerShell; Python 3.13; default UTF-8 for delimited (override `--*-encoding`) |
| Detect | No sniff, no BOM→UTF-8→cp1252 ladder, no `.txt`→tilde default. `.csv` (case-insensitive) defaults to comma when `--*-delim` is omitted; CLI `--a-delim` / `--b-delim` still overrides. Other delimited extensions require `--*-delim`; missing flag is hard fail with flag name + path. Encoding default `utf8`; CLI `--a-encoding` / `--b-encoding` (`utf8`, `windows-1252`, plus `utf8-lossy` / `windows-1252-lossy` as explicit opt-in). Frozen in identity / refresh (reuse, do not re-detect; store the resolved character, including the comma default). Ingest is `pl.read_csv`. |
| Refresh | Manual; sources updatable; snapshots reapplied |
| Undo | Yes, in session |
| Persist | None. Confirmed snapshots, place, drafts, and context columns live in memory until quit |
| Visual | Red-lens safe (§15.8): luminance/underline/reverse; no blue/green as sole signal |
| Fluency | Work is home (column roster of pending comparable columns); A import order; pair-first detail; unified `Enter`/`Esc`/`a`/`y`; overview is `i` modal; no jump list / `f` / `s` / `j` |
| Deadline nav | After `a`, cursor goes to the former next-below item (or last remaining / empty), not the top and not a drill. Next lever still used after bulk `y` / pair-list `A` / unmatched `A`. `.` repeat pair; returned-to-pending marked in place (§15.9) |
| Hard fail text | Raw keys, names, types, paths on stderr / in-TUI |
| Insights | Speculative, view/filter only; date list locked; categorical 30/30/50 is a roster `cat` statistic only (pair list is always paged); no fuzzy keys; no accept-by-insight |
| Context columns | Both-sides intersection only; per column; in memory |
| Empty rows | Drop all-`""` rows after null cast |
| Ragged CSV | Polars as-is: short rows padded with `""`; long rows `ComputeError`; no record-number copy |
| Setup freeze | Paths/sheets/keys cannot change in-session; quit/relaunch |
| Sources | Read-only in this TUI. No clipboard-out to edit files. User edits sources elsewhere, then refresh |
| Roster | Home screen of **pending comparable columns** (accepted / all-equal shared columns hidden by default; `v` shows them dim with status, pending then settled). A-only / B-only / mismatched columns are not column rows (`i` overview). Order: table A import order. Persistent filter box. Immediate `a` column **in place**; `/` `=` behind glass; `m` same pair on columns |
| Batch column accept | Independent regex `/` or exact-value sentinel `=`. Polars `=` gone; `=` is exact-value `.all()` on one side. Draft all `[ON]`; banner/footer `y ACCEPT selected`; Space `[ON]`/`[off]`; `Esc` cancel; pending-only; zero-pending not drafted. Do not stack regex and sentinel into one draft |
| Pair accept | Pair list is Pending view; `Enter` cell-step draft; `a` accepts the pair now; `Esc` back to pairs |
| Launch | `python Reconcile.py`; `--keys` comma-separated; `--a-delim`/`--b-delim` optional on `.csv` (default comma), **required** on other delimited sides; `--a-encoding`/`--b-encoding` optional (default `utf8`); `--a`/`--b`/`--keys` required; CLI paths may be relative, stored absolute |
| Detail | Pair list then cells (always paged list; pane has full strings); Accepted/Equal/All matched behind glass; named tabs only (no `1`–`4`); `a` accepts the current selection on every screen |
| Keybindings | One map (§15.7). `Esc` always back. `a` = accept selection. `v` = show/hide accepted columns. `m` = same pair on selected columns. No `f`/`s`/`j` |
| Paging | 100 rows from Polars; order raw key tuple |
| A-only / B-only grid | Keys + all other columns on that side, including that side’s extras |
| Extra columns | Exact header + Side `A` or `B`; name is never prefixed |
| Fatal before TUI | stderr + exit |
| Fatal after TUI | Keep last state |
| Long strings | Wrap in grid and footer pane; no ellipsis truncate |
| Exit codes | 0 / 1 / 2 as §14 |
