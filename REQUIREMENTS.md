# Strict data reconciliation TUI — provisional requirements

Status: **provisional**. Written for review and modification. No implementation yet.

This document consolidates decisions from the requirements conversation. Items marked **PROVISIONAL** are proposed defaults that have not been explicitly confirmed.

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
- Stacking regex and Polars into one column-accept draft
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

The detector must **profile the entire file** before concluding. No prefix sniff, first-N-rows sample, or early exit on a “looks good” header line. Every candidate above is scored against the **full** file with the **same record parser used to load** (§6.3); only then is a delimiter chosen or the run failed as ambiguous.

**Scoring (locked):**

- A candidate is plausible only if the header splits into **≥ 2** fields and **every** data record has **exactly that many** fields (ragged → that candidate loses).
- All tables have ≥ 2 fields. A file that is 1 field under every candidate: **hard fail**.
- After the full-file profile: **exactly one** plausible candidate → use it; zero or more than one → **hard fail**. Check order is evaluation/report order, **not** a silent tie-break (comma does not beat tilde if both are plausible).

### 6.2 Encoding detection

Locked:

1. If the file has a UTF-8 BOM, decode as UTF-8 (strip BOM). Invalid UTF-8 after a BOM: hard fail.
2. Else if the full file decodes as UTF-8: UTF-8.
3. Else Windows-1252.
4. Never latin-1 (it always “decodes”).

ASCII-only files are valid UTF-8 and take rule 2. Excel is not this path.

### 6.3 Record parse (detect and load)

Delimited load and delimiter scoring use the same rules:

- Quote character `"`; doubled `""` is a literal quote.
- Delimiters and newlines inside quotes are inside one field.
- Unclosed quote, or a record that cannot be parsed: **hard fail** that file.
- After parse, true null → `""`, then drop all-empty rows (§5).

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

Remaining work is the **pending** set. The session is “done” when pending count is 0 (every difference is gone from the sources, or accepted).

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
| Accept entire pair | Snapshot every current pending cell in this column with exact `valA`, `valB`. Immediate from the pair list (`a`). Then **next lever** |
| Confirm drafted pair | Snapshot every **still-checked** pending cell in the in-flight **pair** draft (§9.6). Then the draft is empty; then **next lever** |
| Accept one unmatched key | Snapshot `(side, key, row snapshot)`. On the unmatched-key grid, then the next pending key in that grid (not next lever) |
| Accept all unmatched keys on a side | Bulk snapshot of current A-only or B-only keys. Immediate from the roster row or `A` on that grid. Then **next lever** |
| Accept one extra column | Snapshot `(side, column name)`. Immediate from the roster extra row or Schema extras. Then **next lever** |
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

Session lives in the process. Export/reload persists it (§13).

### 9.4 Refresh UX

Refresh is manual.

On success: recompute counts and pages; show a short **delta in the footer** (e.g. pending `40→12`, accepted `10→8`, `3 returned to pending`). **Stay put** if that screen/column/pair still exists. If it vanished, **next lever** (§15.9). Else the roster.

Do **not** open a jump-list overlay. Rows that **returned to pending** are marked in the lists already on screen (reverse video / standout, red-lens safe §15.8) until the next successful refresh replaces the set.

In-flight **column** draft: drop names that vanished or now have pending 0; do not re-run selectors. In-flight **pair** draft: drop cells that are no longer pending or whose `valA`/`valB` changed; if none remain, the pair draft is cancelled (back to the pair list).

On failure (locked file, parse error, missing key column, non-text Excel, etc.): **keep the last successful in-memory state**, show the error in the TUI, do not exit. This is standard behavior, not a special case. Error text must include the same raw identifiers as §16 (keys, column names, types, paths).

### 9.5 Power-user batch column accept

On the **roster**, a power user can select many comparable columns, review a **draft**, then Confirm. Selectors never accept by themselves. Compare stays exact raw text. Unmatched-key and extra rows are never part of a column draft.

**Single-column accept stays immediate.** It does not wait for Confirm and does not require a draft.

#### Draft

One in-session **column draft**: a set of comparable names (non-key A∩B). Default empty.

| Rule | |
|---|---|
| After a selector Run | Draft := hits that currently have **at least one pending** mismatch, all **checked**. User then unchecks exceptions |
| Zero pending | Column is already reconciled for remaining-work. **Not** drafted. No snapshots. Vacuous Polars `.all()` on zero rows must not check it. **Not** a standing ignore: after refresh, new pending cells are pending |
| Toggle | Space flips the focused **comparable-column** roster row in/out of the draft (only while a draft is in flight). No-op on `A-only` / `B-only` / `extra` rows |
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
| Cancel (`Esc`) | Draft empty; **back to the pair list** (not the roster). No accepts |
| In-flight | Session has no other draft. Confirm or Cancel before another pair, and before roster regex/Polars |
| Zip / quit | Pair draft is **never** persisted. Confirmed cell snapshots only |

Immediate `a` on a drafted cell: snapshot that cell now; drop it from the pair draft.

Immediate `A` (whole column) while a pair draft is in flight: accept **all** current pending cells in the column (not only the pair); pair draft is discarded; then **next lever**.

`.` **Repeat last pair** (column detail, no pair draft in flight): start a pair draft for the remembered `(column, valA, valB)` if that column still exists and any pending cells still have those exact strings. Does **not** auto-accept. New pending rows with that pair after refresh are included only when `.` is pressed. If none remain, go to next lever. If last pair was on another column, jump to that column’s Pending view first. Refused if a **column** draft is in flight.

Refresh: §9.4. New pending cells that happen to have the same two strings are **not** auto-drafted or auto-accepted.

#### UX (detail: one thing at a time)

Pending view shows the **pair list only** (matrix or paged list). No cell grid on this step.

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
- **Place:** last screen (roster, Overview, pair list / cell step, A-only, B-only, Schema extras), last comparable column (if any), last extra `(side, name)` if on extras, detail step (pair list vs cell step / view tab), roster name-filter string, last pair `(column, valA, valB)` for `.` repeat

No copied row payload. No in-flight **draft** (column or pair). Reload always live-rereads files and restores **confirmed** snapshots, then restores **place** if those objects still exist (else the **roster**). Context columns already persist per column.

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

**Happy path (the product):** launch → **roster** (row 1 is the largest remaining pending pile) → `Enter` into that pile → knock it down (`a` / `A` / pair `y`) → **next lever** → `r` after the user saves the workbook in another tool. Regex `/`, Polars `=`, Equal/All-matched tabs: real, behind glass. A-only keys, B-only keys, and extras are remaining-work rows on the roster, not a side room.

A footer/status line is **always visible** (§15.6).

**Home is the work.** After a successful initial load, land on the **roster**, not Overview. `--session` restore: saved place if valid, else roster.

### 15.1 Overview (counts, not home)

Quiet counts and job identity. Reachable by `Esc` from the roster. Not the landing screen.

Shows:

- Frozen job identity (absolute paths, sheets, keys, detected encoding/delimiter)
- Exact remaining counts (pending vs accepted): matched keys, A-only, B-only, extras, mismatched cells, **remaining pending total**
- Entry points: **A-only keys**, **B-only keys**, **Schema extras** (same lists as `Enter` from the matching roster row)
- Speculative chips only as secondary, labeled `speculative`

No separate biggest-lever widget. The roster’s first row is that lever.

### 15.2 Roster (home)

The roster is **all remaining work**, not comparable columns only. One row per:

| Kind | When the row exists | `name` | Pending count on the row |
|---|---|---|---|
| `column` | Every non-key name on both sides | Exact header | Pending mismatched cells in that column |
| `A-only` | Current join has ≥ 1 A-only key (pending or still-accepted) | `A-only keys` | Pending A-only keys |
| `B-only` | Current join has ≥ 1 B-only key (pending or still-accepted) | `B-only keys` | Pending B-only keys |
| `extra` | Each current extra `(side, name)` (pending or still-accepted) | Exact header | `1` if that extra is pending, else `0` |

Hide `A-only` / `B-only` when that side’s unmatched set is empty. Vanished extras drop. New comparable columns and new extras appear on refresh.

**Concentration** (columns only): pending count of the column’s largest exact `(valA, valB)` pair divided by that column’s pending count (0 if pending is 0). Shown as **top-pair %**. Non-column rows show `—`. Concentration is a visible statistic, **not** the sort.

**One sort (not a key):** pending descending, then concentration descending (treat `—` as 0), then exact `name`. No sort-cycle.

Row 1 is the biggest lever because it has the most remaining pending, not the purest recode. Example: column `Status` pending 4000 at 5% top-pair, `A-only keys` pending 50, column `Flag` pending 1 at 100% top-pair → order is `Status`, `A-only keys`, `Flag`. Clearing comparable columns is not “done” while unmatched keys or extras remain.

**Filter box (always visible):** case-insensitive substring on `name` (exact headers and the unmatched labels). View only; does not change pairing or drafts. Focus the box to type; `Esc` returns focus to the list (does not have to clear the text). Empty box = all rows. `/` is **not** this filter; `/` opens regex → column **draft**.

Columns on the roster:

- draft check (visible / active only while a column draft is in flight; only `column` rows are checkable)
- **kind** (`column` / `A-only` / `B-only` / `extra`)
- name
- **side** (`A` or `B` for extras and unmatched-key rows; `—` for comparable columns)
- pending count
- **top-pair %** (concentration; `—` unless `kind` is `column`)
- accepted count (cells, keys, or `1`/`0` for an extra)
- equal count (`—` unless `kind` is `column`)
- categorical yes/no (`—` unless `kind` is `column`)
- compact speculative tags

Row 1 is the biggest lever (bold + underline, §15.8). Rows that contain **returned-to-pending** items after the last refresh: standout/reverse on the row until the next successful refresh.

**Immediate** on the focused row:

| Kind | `Enter` | `a` / `A` |
|---|---|---|
| `column` | Pair list | Accept entire column now |
| `A-only` / `B-only` | That side’s unmatched-key grid | Accept all unmatched keys on that side now, then next lever |
| `extra` | Schema extras, focused on this extra | Accept this extra now, then next lever |

**Batch:** `/` or `=` → draft of **comparable columns only** → `Space` / `y` / `Esc`. `Space` on a non-`column` row is a no-op.

Roster is not Polars-paged.

### 15.3 Column detail (pair list, then cells)

One comparable column. **One thing at a time.**

**Pending (default)** is the **pair list only** (exact `A → B` counts, §9.6). Categorical (§10.5) → full matrix; otherwise paged 100. Sort: count desc, then `valA`, `valB`. Speculative chips on a pair are labels only.

- `Enter` → **cell step** (pair draft of those exact strings). Grid, first-difference, context columns.
- `Esc` from cell step → pair list (draft cancelled if not confirmed).
- `Esc` from pair list → roster.
- `a` on a pair → immediate accept that pair.
- `A` → immediate accept entire column.

**Accepted / Equal / All matched** tabs: cell grid for that set, no pair list. Behind glass; deadline work stays on Pending.

Grid (cell step or non-Pending tabs), 100-row pages:

- key columns (always)
- A value and B value (raw; **wrap**)
- speculative labels when a mismatch
- **context columns** (cell step and non-Pending grids)

**Context columns:** both-sides intersection, excluding keys and the column under examination; A|B raw; per-column, in `.recon.zip`; display-only. Picker `c` on the cell step.

**First-difference caret:** on the cell step footer pane and focused `A`/`B` cells, mark the first differing Python `str` index (after null→`""`). Reverse/standout on both sides. Prefix/length-only differences count. Exact, not speculative. Red-lens safe (§15.8).

Paging: key-tuple order. Pair list paging: 100 when not a matrix.

Returned-to-pending cells (and pairs/columns that contain them): standout until next successful refresh.

### 15.4 A-only keys / B-only keys

Reachable by `Enter` on the matching roster row, or from Overview. Same grid either way.

100-row pages. Order: composite key tuple of raw strings.

Each row: key columns + all other columns on that side, raw — comparable **and** extras only on that side.

`a` accept one key (then the next pending key in this grid; do not jump the roster); `A` accept all unmatched on this side then **next lever**; `u` undo; `Esc` roster.

### 15.5 Schema extras

Reachable by `Enter` on an extra roster row, or from Overview. Same list either way.

Exact header + **Side** `A` or `B`; speculative near-misses labeled `speculative`. Order: exact name. `a` / `u` that extra; `a` then **next lever**. `Esc` roster.

### 15.6 Footer (always on)

The **only** persistent chrome besides the work list. Show what you can do **now**, not the full keymap (`?` has the rest).

- Remaining pending total; A-only / B-only / extras / cells pending
- Refresh delta after `r` (including returned-to-pending count)
- Page `n/m` when paged
- Pair list vs cell step when on detail
- `draft N` + `y` / `Esc` / `Space` when a draft is in flight
- Speculative fragments labeled `speculative`

### 15.7 Commands / keys

**Same keys everywhere.** Do not ship a second keymap when a draft starts; `Space` / `y` / `Esc` simply become useful.

Apply when focus is **not** in a text input (filter box, regex/Polars modal). In a field: typing goes to the field; `Enter` runs/confirms the field; `Esc` leaves the field (modal: close without run; filter box: back to list).

| Key | Meaning |
|---|---|
| `Enter` | Drill: roster row → its child (column → pair list; A-only/B-only → that grid; extra → Schema extras); pair → cell step (draft); Overview entry → that list; modal → Run |
| `Esc` | Back: close modal → cancel draft (cell step → pair list, or roster draft → roster) → parent screen (pair list / A-only / B-only / extras → roster → Overview) |
| `Space` | Toggle focused **column** row in the current column draft; no-op if none, or if the focused roster row is not `kind` `column` |
| `a` | Accept **focused grain** now: entire column (roster `column` row), pair (pair list), cell (cell step), one unmatched key (in that grid), all unmatched on a side (roster `A-only`/`B-only` row), extra |
| `A` | Accept **entire column** (roster `column` row / detail) or **all unmatched on this side** (roster `A-only`/`B-only` row, or A-only/B-only grid) |
| `y` | Confirm current draft; no-op if none; then next lever |
| `u` | Undo focused grain (`U` undo entire column on cell step) |
| `r` | Refresh (stay put; mark returned-to-pending) |
| `.` | Repeat last pair as a new draft (§9.6); refused if a draft is in flight |
| `/` | Roster: regex **column draft** (not the filter box) |
| `=` | Roster: Polars selector (escape hatch; not the happy path) |
| `c` | Context-column picker (cell step) |
| `n` / `p` | Next/prev page |
| `e` | Export `.recon.zip` |
| `o` | Open zip (refused if a draft is in flight) |
| `q` | Quit; discard unconfirmed draft |
| `?` | Help |

No `f`, `s`, or `j`.

View-filter tabs on detail stay named tabs (Pending / Accepted / Equal / All matched), not a `v` cycle. Pending is pair list; the others are cell grids.

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

**Next lever** after a bulk accept (pair `y` or pair-list `a`, column-draft `y`, immediate whole-column `A` / roster `a` on a `column` row, roster `a`/`A` on an unmatched-key row, `A` on an unmatched-key grid, roster or extras-list `a` on an extra):

1. If the current column still has pending pairs, focus the next-highest-count pair on that column’s pair list.
2. Else the next roster row with pending > 0 (fixed sort: pending, then concentration, then name):
   - `column` → that pair list, top pair focused
   - `A-only` / `B-only` → that grid, first pending key focused
   - `extra` → roster, that extra focused (ready for `a`)
3. Else the roster (including pending total 0).

Single-cell `a` on the cell step does not jump columns; it advances to the next pending row in that grid. Single-key `a` on an unmatched-key grid does not jump the roster; it advances to the next pending key in that grid.

**Repeat last pair** `.` — §9.6. In-session and in `.recon.zip`. Never auto-accepts.

Refresh: stay put; footer delta; mark returned-to-pending in place. No jump list.

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
| Ambiguous delimiter | Each candidate and whether it produced ≥ 2 consistent fields on the full file |
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

1. Schema-extras list sort (exact name is the working rule).
2. Windows terminal host beyond PowerShell (Windows Terminal vs conhost) if that matters in practice.
3. UTF-16 delimited files (e.g. Excel “Unicode Text”) are **not** specified; v1 is UTF-8 or Windows-1252 only unless you add them.

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
| Detect | Encoding: UTF-8 BOM, else UTF-8, else Windows-1252; never latin-1. Delimiter: comma, tilde, pipe, tab; full file; ≥ 2 fields; exactly one plausible or hard fail. Same quoted parse for detect and load. |
| Refresh | Manual; sources updatable; snapshots reapplied |
| Undo | Yes, in session |
| Persist | `.recon.zip`: confirmed snapshots + **place** (screen, column, pair vs cell step, filter string, last pair). No drafts, no sort mode |
| Visual | Red-lens safe (§15.8): luminance/underline/reverse; no blue/green as sole signal |
| Fluency | Work is home (roster of all remaining-work kinds); one sort (pending then concentration then name); pair-first detail; unified `Enter`/`Esc`/`a`/`y`; no jump list / `f` / `s` / `j` |
| Deadline nav | Next lever walks the same roster sort (columns, unmatched keys, extras); `.` repeat pair; returned-to-pending marked in place (§15.9) |
| Hard fail text | Raw keys, names, types, paths on stderr / in-TUI |
| Insights | Speculative, view/filter only; date list locked; categorical 30/30/50 is pair-list **layout** only; no fuzzy keys; no accept-by-insight |
| Context columns | Both-sides intersection only; per column; persisted |
| Empty rows | Drop all-`""` rows after null cast |
| Ragged CSV | Hard fail |
| Setup freeze | Paths/sheets/keys cannot change in-session; quit/relaunch |
| Sources | Read-only in this TUI. No clipboard-out to edit files. User edits sources elsewhere, then refresh |
| Roster | Home screen of remaining work (comparable columns, A-only, B-only, extras). Sort: pending then concentration then name. Concentration is a visible top-pair % only. Persistent filter box. Immediate `a`; `/` `=` behind glass |
| Batch column accept | Independent regex `/` or Polars `=`; Polars `.all()` is **one side** (`A` or `B`) on series `s`; draft all-checked; Space toggle; `y` confirm / `Esc` cancel; pending-only; zero-pending not drafted |
| Pair accept | Pair list is Pending view; `Enter` cell-step draft; `a` accepts the pair now; `Esc` back to pairs |
| Launch | `python Reconcile.py`; `--keys` comma-separated; `--session` alone OK; refuse mix with identity flags |
| Detail | Pair list then cells; Accepted/Equal/All matched behind glass; `a` grain / `A` column |
| Keybindings | One map (§15.7). `Esc` always back. No `f`/`s`/`j` |
| Paging | 100 rows from Polars; order raw key tuple |
| A-only / B-only grid | Keys + all other columns on that side, including that side’s extras |
| Extra columns | Exact header + Side `A` or `B`; name is never prefixed |
| Fatal before TUI | stderr + exit |
| Fatal after TUI | Keep last state |
| Long strings | Wrap in grid and footer pane; no ellipsis truncate |
| Exit codes | 0 / 1 / 2 as §14 |
