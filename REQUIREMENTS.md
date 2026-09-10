# Strict data reconciliation TUI — provisional requirements

Status: **provisional**. Written for review and modification. No implementation yet.

This document consolidates decisions from the requirements conversation. Items marked **PROVISIONAL** are proposed defaults that have not been explicitly confirmed.

---

## 1. Purpose

An investigation TUI for two-sided data reconciliation.

The user loads two sources (A and B), inspects exact differences, and either:

- shrinks remaining differences to zero by editing sources **outside this TUI** and refreshing, or
- accepts known variations in-session (principally at the column level, also exact value-pairs within a column) until remaining **pending** count is 0.

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
- Stacking regex and Polars into one column-accept draft
- Accept-by-insight-group
- **Do not implement:** copy pending keys/values to the clipboard (or any other export) in order to take them into the source files
- **Do not implement:** write, patch, save, create, or otherwise mutate files under `--a` / `--b`; open those files in Excel or an editor from the TUI; “fix this cell in the workbook.” Load and refresh are **read-only**. The user edits sources only in other tools, then refreshes.

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

Excel reads: **Polars + fastexcel**. Do not evaluate or follow formulas. Use only the stored string.

### 4.1 Headers

Header row is required on row 1 of every delimited file and every Excel sheet used. Column names are the header strings as read (exact).

### 4.2 Column names

Pairing is by **exact column name** (case-sensitive, space-sensitive: `ID` ≠ `id` ≠ `ID `).

Duplicate column names on one side: **hard fail**.

### 4.3 Excel types

Every cell in the used header+data range must be **text**. Any non-text type (number, date, bool, cached formula result, unused/trailing columns in the used range, etc.): **hard fail**. No special case for “probably unused” trailing columns.

Blank Excel cells are true nulls → `""`.

Password-protected workbook, merged cells, or a named sheet that does not exist: **hard fail**. Hidden sheets are usable only if the sheet name is passed on the CLI.

---

## 5. Load rules

Order of operations:

1. Read the source (delimited or Excel).
2. Cast true nulls to `""`.
3. **Drop rows where every column is `""`** (load rule, not a value cast). Needed so trailing blank Excel rows do not become duplicate empty keys.
4. Then apply keys and compare.

Rows where key columns are `""` but other columns have text are kept. Duplicate `""` keys still hard-fail.

**Ragged delimited rows** (field count ≠ header): hard fail that file.

**All-empty-key rows that are not all-empty rows** are kept and keyed as `""`.

---

## 6. Encoding and delimiters (delimited files)

Detect encoding and delimiter. **Hard fail if ambiguous.** No in-tool override. The user fixes the file.

Must work on **Windows**. UTF-8 is not guaranteed.

### 6.1 Delimiter detection

Candidates, **checked in this order**:

1. comma (`,`)
2. tilde (`~`)
3. pipe (`|`)
4. tab (`\t`)

Semicolon is **not** a candidate. Tilde is.

The detector must **profile the entire file** before concluding. No prefix sniff, first-N-rows sample, or early exit on a “looks good” header line. Every candidate above is scored against the **full** file; only then is a delimiter chosen or the run failed as ambiguous.

**PROVISIONAL scoring:** a candidate is plausible if splitting every record on that delimiter yields a consistent field count equal to the header’s field count (ragged → that candidate is not a winner). After the full-file profile: exactly one plausible candidate → use it; zero or more than one → hard fail. Check order is evaluation/report order, not a silent tie-break among multiple plausible delimiters.

### 6.2 Encoding detection

**PROVISIONAL detector scoring** for encodings: try UTF-8 with BOM, UTF-8, then Windows-1252, only if unambiguous. If UTF-8 decodes cleanly, UTF-8 wins. Do **not** silently fall back to latin-1 (it always “decodes”).

Excel encoding is not a separate concern; fastexcel supplies cell strings.

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
| Polars batch selector | **One side** (`A` or `B`) plus expression on that side’s pending values as series `s`. Not a name prefix. `.all()` cannot target both sides (those rows would not be pending) |

The same exact name cannot be an extra on both sides (that would be intersection, hence comparable). Two extras with different names, one on A and one on B, stay two rows on **Schema extras**, each with its `Side`.

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

Remaining work is the **pending** set. The session is “done” when pending count is 0 (every difference is gone from the sources, or accepted).

### 9.1 Kinds of difference

| Kind | Meaning |
|---|---|
| Cell mismatch | Matched key; comparable column; `A` string ≠ `B` string |
| A-only key | Key tuple exists only on A |
| B-only key | Key tuple exists only on B |
| Extra column | Name on one side only |

### 9.2 Accept actions

| Action | Effect |
|---|---|
| Accept one cell | Snapshot this `(key, column, valA, valB)` |
| Accept entire column | Bulk-accept **all current pending** cell mismatches in that column, each as its own snapshot. Does **not** mean “never compare this column again.” Immediate from roster or detail on a **single** column |
| Confirm drafted columns | Same snapshot rule as accept entire column, applied to every **still-checked** column in the in-flight **column** draft (§9.5). Then the draft is empty |
| Confirm drafted pair | Snapshot every **still-checked** pending cell in the in-flight **pair** draft: same column, exact `valA`, exact `valB` (§9.6). Then the draft is empty |
| Accept one unmatched key | Snapshot `(side, key, row snapshot)` |
| Accept all unmatched keys on a side | Bulk snapshot of current A-only or B-only keys |
| Accept one extra column | Snapshot `(side, column name)` |
| Undo | That snapshot returns to pending (same session) |

Accept entire column is available immediately from the **column roster** and from **column detail** (one column, no draft).

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

Session lives in the process. Export/reload persists it (§13).

### 9.4 Refresh UX

Refresh is manual.

On success: recompute counts and pages; show a short delta (e.g. pending `40→12`, accepted `10→8`, `3 returned to pending`). Then, if any cells **returned to pending** or any **new** pending cell pairs/unmatched keys/extras appeared, open the **Refresh jump list** (§15.9) before restoring place. If that list is empty, restore place: same screen if it still exists; else column roster if possible, else Overview.

In-flight **column** draft: drop names that vanished or now have pending 0; do not re-run selectors. In-flight **pair** draft: drop cells that are no longer pending or whose `valA`/`valB` changed; if none remain, the pair draft is cancelled.

On failure (locked file, parse error, missing key column, non-text Excel, etc.): **keep the last successful in-memory state**, show the error in the TUI, do not exit. This is standard behavior, not a special case. Error text must include the same raw identifiers as §16 (keys, column names, types, paths).

### 9.5 Power-user batch column accept

On the **column roster**, a power user can select many comparable columns, review a **draft**, then Confirm. Selectors never accept by themselves. Compare stays exact raw text.

**Single-column accept stays immediate.** It does not wait for Confirm and does not require a draft.

#### Draft

One in-session **column draft**: a set of comparable names (non-key A∩B). Default empty.

| Rule | |
|---|---|
| After a selector Run | Draft := hits that currently have **at least one pending** mismatch, all **checked**. User then unchecks exceptions |
| Zero pending | Column is already reconciled for remaining-work. **Not** drafted. No snapshots. Vacuous Polars `.all()` on zero rows must not check it. **Not** a standing ignore: after refresh, new pending cells are pending |
| Toggle | Space flips the focused roster row in/out of the draft (only while a draft is in flight) |
| Confirm (`y`) | For each still-checked name, accept-entire-column (pending snapshots only). Then draft empty. Undo is **per column**, not one bundle. Then **next lever** (§15.9) |
| Cancel (`Esc` on roster) | Draft empty; no accepts |
| In-flight | At most one draft in the session. Confirm or Cancel before another regex/Polars Run, and before starting a **pair** draft (§9.6) |
| Zip / quit | Draft is **never** persisted. `.recon.zip` restores **confirmed** snapshots only. Quit discards an unconfirmed draft |

Refresh does not re-run the selector. Drop from draft: name gone, or pending count now 0. New comparable columns are not added.

If the user **immediately** accepts a column that is in the draft (`a` on roster), that column is snapshotted now and **removed** from the draft; other drafted names remain. Immediate accept of the last drafted column (or immediate `a`/`A` with no draft) also goes to **next lever** (§15.9).

#### Selectors (independent)

Regex and Polars do **not** stack, union, or intersect. Each Run starts from an empty draft.

**Name regex** (roster `/`):

- Applied to exact comparable names only (not keys, not extras, not values).
- Python `re.search`, case-sensitive; user may put `(?i)` or `^` `$` in the pattern.
- Empty or invalid pattern: in-TUI error, draft unchanged.

**Polars expression** (roster `=`):

The pending universe is `A ≠ B`. An `.all()` predicate on the **same** constant therefore cannot be true on **both** sides: if every pending `A` and every pending `B` were `——`, those cells would be equal and not pending. So an `.all()` selector is **single-side**.

- User must choose **Side `A` or Side `B`** (modal tabs, same denotation as §8.1). Required; no default that means “both.”
- Per comparable column, one Series `s`: that side’s values on **pending mismatches only** (raw strings, nulls already `""`). The other side is not in the namespace.
- Expression must reduce to a **single boolean** per column, typically `.all()`, e.g. `(pl.col("s") == "——").all()` meaning “every pending value on the chosen side is `——`.”
- Referencing `a`, `b`, both sides, original field names, or anything except `s`: in-TUI error, draft unchanged.
- Per-row Series or non-boolean scalar: in-TUI error; do not silently `.all()`.
- No IO, no scans, no `map_elements`. Evaluated in **Polars**. Engine error: in-TUI, draft unchanged.

Zero pending still excluded (§9.5 draft). Choosing Side `B` does not look at A, and vice versa.

While a draft is in flight, `/` and `=` are disabled (or error: confirm or cancel first).

#### UX (roster)

- Full roster stays visible (not a drafted-only list). Drafted rows show a check.
- Opening `/` opens a regex modal. Opening `=` opens a Polars modal with **Side tabs `A` | `B`** plus the expression field on `s`; `Enter` Runs; `Esc` closes the modal without changing the draft. Run is refused until a side is selected.
- After Run, footer shows `draft N` plus Confirm / Cancel / toggle.
- Column **detail** has no regex/Polars. Detail accept column remains immediate (`A`). Pair accept is §9.6, and is refused while a **column** draft is in flight.

### 9.6 Exact pending-pair accept

On **column detail** (Pending view), pending mismatches for that column are grouped by exact raw `(valA, valB)`. Those groups are a first-class **exact** object (not speculative). Accepting a pair writes the same per-cell snapshots as `a` on each remaining drafted cell.

This is how a known recode (`Y` vs `Yes`) is accepted without accepting the whole column and burying unrelated pending cells.

**Not** accept-by-insight: the pair is two exact strings. Speculative chips on a pair (trim, date, …) must not be the accept target.

#### Pair list

Computed in Polars from **pending** cells of the current column only.

| Layout | When |
|---|---|
| Full `A → B` count matrix | Column is categorical by §10.5 (nunique pending A ≤ 30, B ≤ 30, union ≤ 50) |
| Paged list of pairs (100) | Otherwise. Sort: **count descending**, then `valA` raw, then `valB` raw |

Each row: exact `A` string, exact `B` string, pending count. Strings wrap. Side denotation is the two columns headed `A` and `B` (§8.1), not a name prefix.

Pair list is shown only on the **Pending** view tab. Hidden on Accepted / Equal / All matched.

#### Pair draft

Selecting a pair (not Confirm) fills a **pair draft**: every current pending cell in this column whose values are exactly those two strings, **all checked**. The cell grid shows that set (100-row pages, key-tuple order). The user unchecks exceptions, then Confirm.

| Rule | |
|---|---|
| After selecting a pair | Draft := matching pending cells, all checked. If that set is empty, do not start a draft (already reconciled for that pair) |
| Toggle | Space flips the focused **grid** row in/out of the draft |
| Confirm (`y`) | Snapshot every still-checked cell `(key, column, valA, valB)`. Undo is **per cell**, not one bundle. Draft empty. Remember this pair as **last pair**. Then **next lever** (§15.9) |
| Cancel (`Esc` on detail) | Draft empty; stay on column detail; pair list + full pending grid restored |
| In-flight | Session has no other draft. Confirm or Cancel before another pair, and before roster regex/Polars |
| Zip / quit | Pair draft is **never** persisted. Confirmed cell snapshots only |

Immediate `a` on a drafted cell: snapshot that cell now; drop it from the pair draft.

Immediate `A` (whole column) while a pair draft is in flight: accept **all** current pending cells in the column (not only the pair); pair draft is discarded; then **next lever**.

`.` **Repeat last pair** (column detail, no pair draft in flight): start a pair draft for the remembered `(column, valA, valB)` if that column still exists and any pending cells still have those exact strings. Does **not** auto-accept. New pending rows with that pair after refresh are included only when `.` is pressed. If none remain, go to next lever. If last pair was on another column, jump to that column’s Pending view first. Refused if a **column** draft is in flight.

Refresh: §9.4. New pending cells that happen to have the same two strings are **not** auto-drafted or auto-accepted.

#### UX / keys (detail, pair)

- Body has two widgets: **pair list** and **cell grid**. Widget focus moves between them (not the same as the Pending/Accepted/Equal/All matched view tabs).
- `Enter` on a focused pair starts the pair draft and focuses the grid. Refused if any draft is already in flight.
- While pair draft in flight: footer `draft N` (cell count); `Space` / `y` / `Esc` as above; view-filter tabs other than Pending are disabled until Confirm or Cancel.
- `Esc` with **no** pair draft: back to roster (if a **column** draft is still in flight there, it remains).

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

### 10.5 Categorical layout (not an accept path)

A comparable column is treated as **categorical** when, among **pending** cells for that column:

- `nunique(A) ≤ 30`, and
- `nunique(B) ≤ 30`, and
- `nunique(A ∪ B) ≤ 50`

That heuristic only chooses **layout** of the exact pending-pair list in §9.6 (full matrix vs paged list). Pair **counts** are exact. Pair **accept** is §9.6, not this section.

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

- Load, join, compare, counts, remaining sets, and insights stay in Polars.
- The TUI must **not** convert full frames to Python objects.
- The TUI **requests pages** (100 rows) and small summaries (roster, counts, pending-pair groups, batch-selector booleans).
- Python/Polars round-trips must be minimized and explicit (page structs / small aggregate frames only).
- Batch Polars selectors (§9.5) run **in Polars** on one pending series `s` for the chosen side per comparable column (or one unpivot + group). Pending-pair lists (§9.6) are a Polars `group_by` of exact `valA`, `valB`. Do not pull full columns into Python to test predicates. Draft checkboxes are a small name/row set; that part may be Python.

Roster is one row per comparable column (small); it may be fully materialized. Cell/key lists are paged at **100**.

---

## 12. CLI

The TUI does not collect paths, sheets, or keys. Job identity is CLI (or a session zip).

Invoked from PowerShell:

```powershell
python Reconcile.py --a C:\data\left.csv --b C:\data\right.csv --keys id,year
python Reconcile.py --session C:\data\job.recon.zip
```

| Flag | Meaning |
|---|---|
| `--a PATH` | Side A file (absolute path stored in session) |
| `--b PATH` | Side B file |
| `--a-sheet NAME` | Required if A is `.xlsx`/`.xlsm` |
| `--b-sheet NAME` | Required if B is `.xlsx`/`.xlsm` |
| `--keys NAMES` | Comma-separated key column names, in order. Exclusive form; not repeatable `--key`. |
| `--session PATH` | Load `*.recon.zip` and live-reread sources |

Rules:

- `--session` alone is valid (zip contains paths, sheets, keys, detections, snapshots, context sets).
- Without `--session`: `--a`, `--b`, and `--keys` are required. `--keys` must contain at least one non-empty name. Sheet flags required per Excel side.
- Mixing `--session` with `--a` / `--b` / `--a-sheet` / `--b-sheet` / `--keys` is a **hard fail**. The zip is the identity. Refuse; do not override or merge.
- Duplicate names inside `--keys`, or an empty segment (e.g. `id,,year`): hard fail.
- Key name not on both sides: hard fail (stderr+exit at initial load).

Initial load/parse/schema/dup-key/non-text/ambiguous-delimiter/missing-path failures **before the TUI is up**: message on **stderr** including **raw identifiers** (§16), process **exit**.

Once the TUI is up: errors stay in the TUI with last good state (§9.4). Stderr+exit only if Textual cannot start.

Quit and relaunch to compare a different pair of sources or different keys.

---

## 13. Session package

Extension: **`.recon.zip`**

Contents (**PROVISIONAL** layout):

- `manifest.json` with a **schema version**
- Absolute paths for A and B
- Sheet names when Excel
- Key column names (ordered)
- Detected encoding and delimiter per delimited side
- Per-column context-column sets (§15.3)
- Acceptance snapshots: type, key tuple, column, `valA`, `valB`, side, row snapshot as needed
- **Place:** last screen, last comparable column (if any), detail view tab, roster sort mode, roster name-filter string, last pair `(column, valA, valB)` for `.` repeat

No copied row payload. No in-flight **draft** (column or pair). Reload always live-rereads files and restores **confirmed** snapshots, then restores **place** if those objects still exist (else Overview). Context columns already persist per column.

Missing path on load: hard fail (stderr+exit if before TUI; in-TUI error if already running and this was an Open).

Export and open from the TUI. Also `--session` on CLI.

---

## 14. Exit codes

| Code | When |
|---|---|
| `0` | User quits and remaining **pending** is 0 (including all-accepted) |
| `1` | User quits and pending remains |
| `2` | Hard fail (load/parse/dup keys/ambiguous detect/non-text/etc.) |

---

## 15. TUI screens

Do not use the word “summary” for two different screens. Names below are canonical.

After a successful initial load, land on **Overview**.

A footer/status line is **always visible** (§15.6).

### 15.1 Overview (landing)

Shows:

- Frozen job identity (absolute paths, sheets, keys, detected encoding/delimiter)
- Exact remaining counts (pending vs accepted):
  - matched keys (with / without pending cell diffs)
  - A-only keys
  - B-only keys
  - extra columns
  - mismatched cells
  - **remaining pending total**
- Entry points (selectable):
  - **Column roster**
  - **A-only keys**
  - **B-only keys**
  - **Schema extras**
- Speculative chips only as secondary, labeled `speculative`
- **Biggest lever** (exact, not speculative): the pending cell group `(column, valA, valB)` with the largest count. One line: column name, `A` string, `B` string, count, percent of all pending cells. Default focus on Overview. `Enter` opens that column’s detail on Pending with that pair **focused** in the pair list (does not start a draft)

This is the screen A-only / B-only / extras hang off. The column roster is a separate screen. After `--session` restore, land on saved **place** if valid; else Overview.

### 15.2 Column roster

One row per **comparable column** only (non-key intersection). Not extras, not keys.

**Concentration** for a column: pending count of its largest exact `(valA, valB)` pair divided by that column’s pending count (0 if pending is 0).

Sort (cycle with `s`):

1. **Pending** (default): pending count descending, then exact column name
2. **Concentration**: concentration descending, then pending descending, then exact column name

**Name filter** (`f`): view-only typeahead. Case-insensitive substring on the exact header. Does **not** change pairing, drafts, or compare. `/` remains regex → column draft. `Esc` in the filter field clears focus; empty filter shows all.

Columns on the roster:

- draft check (visible / active only while a batch draft is in flight)
- name
- pending count
- **top-pair %** (concentration)
- accepted count
- equal count (matched keys where A=B for this column)
- categorical yes/no
- compact speculative tags (e.g. `trim 80%`, `same-date 12`, `Y→Yes 400`)

**Immediate actions:** open **column detail**; accept/undo **this one column** (pending snapshots now). `a` / `u` do not use the draft.

**Batch actions (§9.5):** `/` name regex or `=` Polars expression → checked draft → Space toggles → `y` Confirm or `Esc` Cancel. Full roster remains visible. While a draft is in flight, `/` and `=` are blocked; footer shows `draft N`.

Roster is not Polars-paged (column count is small).

### 15.3 Column detail

One comparable column.

**Default row set: pending cell mismatches only.**

**View filter** (not default pending): the user must be able to look at other matched rows for this column while diagnosing. This is a **row filter**, not a change to compare.

| Filter | Rows shown |
|---|---|
| **Pending** (default) | Matched keys where this column’s A ≠ B and the mismatch is **not** accepted |
| **Accepted** | Matched keys where this column currently has an **accepted** cell snapshot (still A ≠ B; if values changed, refresh already moved them to pending) |
| **Equal** | Matched keys where this column’s A string **equals** B |
| **All matched** | Every key present on both sides, for this column, regardless of equal/pending/accepted |

The four filters are **tabs** on column detail, labeled with those names. Default tab: **Pending**. Not an unlabeled cycle.

Grid (100-row pages):

- key columns (always; not optional)
- A value and B value for this column (raw strings; **wrap**, do not ellipsis-truncate)
- speculative labels for that cell, when the row is a mismatch
- **context columns** the user has added

**Context columns:**

- Purpose: extra both-sides fields to diagnose this column’s differences
- Pool: names in the **both-sides intersection**, excluding keys and excluding the column under examination
- If added: show **A and B raw strings** for that name
- Easy add/remove (multi-select / checkbox)
- Selection is **per examined column**, in-session, stored in `.recon.zip`
- Display-only; never part of the compare contract or remaining counts

**Transition / pending pairs (§9.6):** exact `A → B` pending counts. Categorical (§10.5) → full matrix; otherwise paged pair list. `Enter` on a pair starts a **pair draft** (not an accept). Speculative chips on a pair are labels only.

Long strings **wrap** in the grid (no max-width ellipsis). A page is still 100 data rows; wrapped rows may occupy multiple screen lines. Selected cell: full raw A and B strings in a **footer pane**, also wrapped to the pane width (exact text, no ellipsis).

**First-difference caret:** in that footer pane (and on the focused grid `A`/`B` cells), mark the first character index where the two strings differ (Python `str` code points after null→`""`). Reverse/standout those characters on both sides. If one string is a prefix of the other, mark the first extra character on the longer side. Length-only difference (equal prefix) is a difference. This is exact, not speculative. Style must remain readable through red lenses (§15.8).

Paging: 100 rows from Polars. Page order: **composite key as a tuple of raw strings** (stable, exact). Pair list paging: 100 pairs when not a full matrix.

Actions: accept/undo one **cell** (`a`); accept/undo **entire column** immediate (`A`); pair draft `Enter` / `Space` / `y` / `Esc` (§9.6); add/remove context; switch view-filter **tabs**; next/prev page; back to roster (`Esc` when no pair draft). No regex/Polars on this screen.

### 15.4 A-only keys / B-only keys

Reachable from **Overview**.

100-row pages. Order: **composite key tuple of raw strings**.

Each row: key columns + **all other columns on that side**, raw — comparable (intersection minus keys) **and extras that exist only on that side**.

Actions: accept one unmatched key; accept all unmatched on this side; undo.

Speculative trim/case key hints labeled `speculative`.

### 15.5 Schema extras

Reachable from **Overview**.

List extra names as the **exact header**, a **Side** column (`A` or `B`), and speculative name near-misses (labeled `speculative`). No `A.` / `B.` prefix on the name.

Actions: accept/undo that extra.

**PROVISIONAL** order: exact name.

### 15.6 Footer / status (always on)

- Remaining pending total
- Breakdown: A-only pending, B-only pending, extras pending, cell pending
- Current page `n/m` when on a paged list
- Current detail view filter when on column detail (which **tab**: pending / accepted / equal / all matched)
- Any insight fragment labeled `speculative`
- **When a column draft is in flight (roster):** `draft N` (columns), plus Confirm / Cancel / toggle hints (`y` / `Esc` / `Space`)
- **When a pair draft is in flight (detail):** `draft N` (cells), plus Confirm / Cancel / toggle hints (`y` / `Esc` / `Space`)

Bindings in the footer must match §15.7 for the current screen and mode. The command bar is visible; users are not expected to memorize keys. `?` lists the same map.

### 15.7 Commands / keys

Bindings are **mode-aware**. Keys below apply when focus is **not** in a text input. Inside the regex/Polars modal, typing goes to the field; `Enter` / `Esc` are modal only.

**Global** (TUI up, not in text input):

| Key | Action |
|---|---|
| `?` | Help (this map) |
| `q` | Quit. Unconfirmed draft (column or pair) is discarded (not an accept) |
| `r` | Refresh |
| `.` | Repeat last pair as a **new draft** (§9.6). Global when last pair is set; jumps to that column if needed. Refused if any draft is in flight (Confirm or Cancel first) |
| `j` | Open **Refresh jump list** if the last refresh produced one; no-op if none |
| `e` | Export `.recon.zip` (confirmed snapshots only; includes place) |
| `o` | Open `.recon.zip` (refused / in-TUI error if any draft is in flight: Confirm or Cancel first) |

**Overview:**

| Key | Action |
|---|---|
| `Enter` | If biggest lever focused: column detail, Pending, that pair focused. Else open the focused entry (roster / A-only / B-only / extras) |
| `Esc` | No-op (already landing) |

**Column roster — no draft:**

| Key | Action |
|---|---|
| `Enter` | Column detail for the focused row |
| `Esc` | Back to Overview |
| `a` | **Immediate** accept this column (pending mismatches); then **next lever** |
| `u` | Undo this column’s acceptances |
| `/` | Open **name regex** modal (column **draft**, not a view filter) |
| `=` | Open **Polars expression** modal |
| `f` | Name **typeahead filter** (view only) |
| `s` | Cycle roster sort (pending vs concentration) |

**Column roster — draft in flight:**

| Key | Action |
|---|---|
| `Enter` | Column detail for the focused row. Pair accept on detail is refused until this column draft is Confirmed or Canceled |
| `Space` | Toggle focused row in/out of the draft |
| `y` | Confirm: column-accept every still-checked drafted column; draft clears |
| `Esc` | Cancel draft; stay on roster |
| `a` | **Immediate** accept focused column; if it was drafted, drop it from the draft |
| `u` | Undo this column |
| `/` `=` | Disabled until Confirm or Cancel |
| `f` | Name typeahead filter (view only) |
| `s` | Cycle roster sort |

**Regex / Polars modal:**

| Key | Action |
|---|---|
| `Enter` | Run selector; on success, close modal, fill draft (all hits with pending checked). Polars: refused until Side `A` or `B` is selected |
| `Esc` | Close modal; draft unchanged |
| Side tabs (Polars only) | Choose **A** or **B**. `.all()` applies to that side’s pending series `s` only |

**Column detail — no pair draft:**

| Key | Action |
|---|---|
| `Esc` | Back to roster |
| `Enter` | If pair list focused: start **pair draft** for that exact `(valA, valB)` (§9.6). Refused if a **column** draft is in flight |
| `.` | Repeat last pair as a new draft (§9.6) |
| `a` | Accept **focused cell**. Then move to the next pending row in this grid; do **not** change column |
| `A` | **Immediate** accept **entire column**; then **next lever** |
| `u` | Undo focused cell |
| `U` | Undo entire column (this column’s acceptances) |
| `c` | Context-column picker |
| `n` / `p` | Next/prev page (focused widget: pair list or cell grid) |
| Tab keys / click | View-filter **tabs** (Pending / Accepted / Equal / All matched) |

**Column detail — pair draft in flight:**

| Key | Action |
|---|---|
| `Space` | Toggle focused grid row in/out of the pair draft |
| `y` | Confirm: snapshot every still-checked drafted cell; draft clears; **next lever** |
| `Esc` | Cancel pair draft; stay on column detail |
| `a` | **Immediate** accept focused cell; if drafted, drop it from the pair draft; stay on grid |
| `A` | **Immediate** accept **entire column**; pair draft discarded; **next lever** |
| `n` / `p` | Next/prev page of drafted cells |
| View-filter tabs | Stay on **Pending**; other tabs disabled until Confirm or Cancel |

**A-only / B-only / extras:**

| Key | Action |
|---|---|
| `Esc` | Overview |
| `a` | Accept focused item (one key or one extra) |
| `A` | Accept all unmatched on this side (A-only / B-only screens only) |
| `u` | Undo focused item |
| `n` / `p` | Next/prev page |

View-filter on column detail is **tabs**, not a `v` cycle.

**Refresh jump list** (after `r` if non-empty, or `j`):

| Key | Action |
|---|---|
| `Enter` | Jump to the focused item (column detail + cell, or A-only/B-only/extras row) |
| `Esc` | Dismiss; land where §9.4 would have without the list |
| `n` / `p` | Page |

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
| Biggest lever | Bold + underline | Yellow |
| First-difference | Reverse/standout on the disagreeing characters | Yellow/white, not blue |
| Error | Bold + the word `ERROR` | Red (still reads as bright through red lenses) |
| Pending = 0 | Bold high luminance, not a green “success” | White/yellow |

Palette: dark background; foreground default, bright white, yellow, orange/amber, red; gray luminance steps. Do **not** use blue, cyan, or green as the sole distinguisher of pending vs accepted vs equal vs error vs success.

### 15.9 Deadline navigation

Power-user path to pending → 0 without hunting.

**Biggest lever.** Exact `group_by` of pending cells: max count `(column, valA, valB)`. Shown on Overview. `Enter` focuses that pair (no draft).

**Next lever** after a bulk accept (pair Confirm, column-draft Confirm, immediate whole-column `A` / roster `a`):

1. If the current column still has pending pairs, focus the next-highest-count pair on that column’s Pending view.
2. Else open the next roster column with pending > 0, using the **current roster sort**, Pending view, its top pair focused.
3. Else Overview (including pending total 0).

Single-cell `a` does not jump columns; it advances to the next pending row in the current grid.

**Refresh jump list.** Exact items, 100 per page, key-tuple / name order: each cell that **returned to pending**; new pending pairs (column + valA + valB + count); new A-only/B-only keys; new extras. `Enter` jumps. Empty list is not shown.

**Repeat last pair** `.` — §9.6. Remembered in-session and in `.recon.zip`. Never auto-accepts.

**Roster typeahead** `f` — view filter only. **Roster sort** `s` — pending vs concentration.

---

## 16. Hard fail vs in-TUI error

Hard-fail and in-TUI error text must include **raw identifiers** so the user can find the row in the source file themselves (this TUI will not open or copy into the file):

| Failure | Must print |
|---|---|
| Duplicate key | Side (`A`/`B`), exact key tuple strings, occurrence count |
| Ragged delimited row | Path, 1-based row number, header field count, actual field count |
| Non-text Excel | Path, sheet, exact column header, type seen (and cell address if the engine provides it) |
| Missing key column | Exact name, which side lacks it |
| Duplicate column names | Side, exact duplicated header |
| Ambiguous delimiter | Each candidate (comma, tilde, pipe, tab) and full-file field-count consistency |
| Missing path | The absolute path |

| Situation | Behavior |
|---|---|
| Textual cannot start | stderr + exit `2` |
| Initial CLI load fails (missing file, ambiguous delimiter/encoding, ragged row, dup columns, dup keys, missing key column, non-text Excel, merged cells, password, missing sheet, mixed `--session` + identity flags) | stderr + identifiers + exit `2` |
| After TUI is up: refresh fail, open-session fail, invalid regex/Polars selector, second selector or pair-draft start while a draft is in flight | Stay in TUI, last good state (draft unchanged unless the rule says drop/cancel), show error with identifiers |
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

1. Encoding detector scoring (delimiter *candidates*, check order, and full-file profile are locked in §6.1; encoding “clear winner” vs ambiguous is not).
2. Schema-extras list sort (still exact name).
3. Windows terminal host beyond PowerShell (Windows Terminal vs conhost) if that matters in practice.

---

## 19. Decision log (locked)

| Topic | Decision |
|---|---|
| Sides | Always 2 |
| Inputs | Delimited + xlsx/xlsm; 2 sheets max compared |
| Compare | Exact raw text; null → `""` only |
| Keys | Required, composite OK via `--keys a,b`; A-only/B-only reviewed; dups hard fail |
| Column pair | Exact name; extras surfaced |
| Product | Investigation TUI; session acceptances; shrink or accept |
| Excel | Text cells only; fastexcel; no formulas; any non-text in used range hard-fails |
| Headers | Required |
| Platform | Windows PowerShell; Python 3.13; UTF-8 not guaranteed |
| Detect | Encoding + delimiter; hard fail if ambiguous; no override. Delimiters checked comma, tilde, pipe, tab (full file profiled before conclude). Semicolon not a candidate. |
| Refresh | Manual; sources updatable; snapshots reapplied |
| Undo | Yes, in session |
| Persist | `.recon.zip`: confirmed snapshots + **place** (screen, column, tab, roster sort/filter, last pair). No drafts |
| Visual | Red-lens safe (§15.8): luminance/underline/reverse; no blue/green as sole signal |
| Deadline nav | Biggest lever, next lever, refresh jump list, `.` repeat pair, roster `f`/`s` (§15.9) |
| Hard fail text | Raw keys, names, types, paths on stderr / in-TUI |
| Insights | Speculative, view/filter only; date list locked; categorical 30/30/50 is pair-list **layout** only; no fuzzy keys; no accept-by-insight |
| Context columns | Both-sides intersection only; per column; persisted |
| Empty rows | Drop all-`""` rows after null cast |
| Ragged CSV | Hard fail |
| Setup freeze | Paths/sheets/keys cannot change in-session; quit/relaunch |
| Sources | Read-only in this TUI. No clipboard-out to edit files. User edits sources elsewhere, then refresh |
| Roster | Comparable intersection only; sort pending **or** concentration; typeahead `f`; **immediate** one-column accept; batch via column draft (§9.5) |
| Batch column accept | Independent regex `/` or Polars `=`; Polars `.all()` is **one side** (`A` or `B`) on series `s`; draft all-checked; Space toggle; `y` confirm / `Esc` cancel; pending-only; zero-pending not drafted |
| Pair accept | Exact pending `(valA, valB)` on column detail; pair draft → toggle → confirm; same cell snapshots; one draft in session (§9.6) |
| Launch | `python Reconcile.py`; `--keys` comma-separated; `--session` alone OK; refuse mix with identity flags |
| Detail | Pending tab default; tabs: pending / accepted / equal / all matched; `a` cell / `A` column; pair list on Pending |
| Keybindings | Mode-aware map in §15.7; visible footer; `?` help |
| Paging | 100 rows from Polars; order raw key tuple |
| A-only / B-only grid | Keys + all other columns on that side, including that side’s extras |
| Extra columns | Exact header + Side `A` or `B`; name is never prefixed |
| Fatal before TUI | stderr + exit |
| Fatal after TUI | Keep last state |
| Long strings | Wrap in grid and footer pane; no ellipsis truncate |
| Exit codes | 0 / 1 / 2 as §14 |
