# Strict data reconciliation TUI — provisional requirements

Status: **provisional**. Written for review and modification. No implementation yet.

This document consolidates decisions from the requirements conversation. Items marked **PROVISIONAL** are proposed defaults that have not been explicitly confirmed.

---

## 1. Purpose

An investigation TUI for two-sided data reconciliation.

The user loads two sources (A and B), inspects exact differences, and either:

- shrinks remaining differences to zero by fixing the sources and refreshing, or
- accepts known variations in-session (principally at the column level) until remaining **pending** count is 0.

This is **not** an audit, sign-off, or certification tool. It does not produce a signed report as a product goal.

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
| Accept entire column | Bulk-accept **all current** cell mismatches in that column, each as its own snapshot. Does **not** mean “never compare this column again.” |
| Accept one unmatched key | Snapshot `(side, key, row snapshot)` |
| Accept all unmatched keys on a side | Bulk snapshot of current A-only or B-only keys |
| Accept one extra column | Snapshot `(side, column name)` |
| Undo | That snapshot returns to pending (same session) |

Accept entire column is available from the **column roster** and from **column detail**.

Insights cannot accept. There is no “accept this insight group.”

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

On success: stay on the same screen if it still exists; recompute counts and the current page; show a short delta (e.g. pending `40→12`, accepted `10→8`, `3 returned to pending`). If the current column or page no longer exists, go to the column roster if possible, otherwise Overview.

On failure (locked file, parse error, missing key column, non-text Excel, etc.): **keep the last successful in-memory state**, show the error in the TUI, do not exit. This is standard behavior, not a special case.

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

### 10.5 Categorical transitions

A comparable column is treated as **categorical** when, among **pending** cells for that column:

- `nunique(A) ≤ 30`, and
- `nunique(B) ≤ 30`, and
- `nunique(A ∪ B) ≤ 50`

Then the column detail view shows a **transition table**: counts of `A value → B value` for **pending mismatches only**. Speculative. Not a recode.

These thresholds and the date format list in §10.4 are locked for v1.

### 10.6 Not v1

- Fuzzy unmatched-key matching (Levenshtein, “this row looks like that row”)
- Cross-column value hunting
- Date-format guessing beyond §10.4
- Accept-by-insight-group

---

## 11. Architecture: Polars owns the data

Stack: **Python 3.13**, Polars, fastexcel, Textual. Must run on Windows. Launch from **PowerShell** via `Reconcile.py` (see §12, §17).

- Load, join, compare, counts, remaining sets, and insights stay in Polars.
- The TUI must **not** convert full frames to Python objects.
- The TUI **requests pages** (100 rows) and small summaries (roster, counts, transition table).
- Python/Polars round-trips must be minimized and explicit (page structs / small aggregate frames only).

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

Initial load/parse/schema/dup-key/non-text/ambiguous-delimiter/missing-path failures **before the TUI is up**: message on **stderr**, process **exit**.

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

No copied row payload of the source data. Reload always live-rereads files.

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

This is the screen A-only / B-only / extras hang off. The column roster is a separate screen.

### 15.2 Column roster

One row per **comparable column** only (non-key intersection). Not extras, not keys.

Sort: **pending count descending, then exact column name**.

Columns on the roster:

- name
- pending count
- accepted count
- equal count (matched keys where A=B for this column)
- categorical yes/no
- compact speculative tags (e.g. `trim 80%`, `same-date 12`, `Y→Yes 400`)

Actions: open **column detail**; accept/undo **entire column** without opening detail.

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

**Transition table** (if categorical, §10.5): pending-only `A → B` counts, labeled speculative.

Long strings **wrap** in the grid (no max-width ellipsis). A page is still 100 data rows; wrapped rows may occupy multiple screen lines. Selected cell: full raw A and B strings in a **footer pane**, also wrapped to the pane width (exact text, no ellipsis).

Paging: 100 rows from Polars. Page order: **composite key as a tuple of raw strings** (stable, exact).

Actions: accept/undo one cell; accept/undo entire column; add/remove context; switch view-filter **tabs**; next/prev page; back to roster.

### 15.4 A-only keys / B-only keys

Reachable from **Overview**.

100-row pages. Order: **composite key tuple of raw strings**.

Each row: key columns + **all other columns on that side**, raw — comparable (intersection minus keys) **and extras that exist only on that side**.

Actions: accept one unmatched key; accept all unmatched on this side; undo.

Speculative trim/case key hints labeled `speculative`.

### 15.5 Schema extras

Reachable from **Overview**.

List extra names, which side, speculative name near-misses.

Actions: accept/undo that extra.

**PROVISIONAL** order: exact name.

### 15.6 Footer / status (always on)

- Remaining pending total
- Breakdown: A-only pending, B-only pending, extras pending, cell pending
- Current page `n/m` when on a paged list
- Current detail view filter when on column detail (which **tab**: pending / accepted / equal / all matched)
- Any insight fragment labeled `speculative`

### 15.7 Commands / keys

User was unsure about keybindings. **PROVISIONAL:** visible footer actions plus `?` help. Suggested bindings (replaceable):

| Action | Suggested |
|---|---|
| Drill / confirm | Enter |
| Back | Esc |
| Next/prev page | n / p |
| Accept | a |
| Undo | u |
| Refresh | r |
| Export session | e |
| Open session | o |
| Context columns | c |
| Help | ? |
| Quit | q |

View filter on column detail is **tabs**, not a key cycle.

---

## 16. Hard fail vs in-TUI error

| Situation | Behavior |
|---|---|
| Textual cannot start | stderr + exit `2` |
| Initial CLI load fails (missing file, ambiguous delimiter/encoding, ragged row, dup columns, dup keys, missing key column, non-text Excel, merged cells, password, missing sheet, mixed `--session` + identity flags) | stderr + exit `2` |
| After TUI is up: refresh fail, open-session fail | Stay in TUI, last good state, show error |
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

1. Keybinding map (footer actions + `?` still proposed; view-filter is tabs, not a `v` cycle).
2. Encoding detector scoring (delimiter *candidates*, check order, and full-file profile are locked in §6.1; encoding “clear winner” vs ambiguous is not).
3. Schema-extras list sort (still exact name).
4. Windows terminal host beyond PowerShell (Windows Terminal vs conhost) if that matters in practice.

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
| Persist | `.recon.zip`, absolute paths, live reread |
| Insights | Speculative, view/filter only; date list locked; categorical 30/30/50 pending-only; no fuzzy keys |
| Context columns | Both-sides intersection only; per column; persisted |
| Empty rows | Drop all-`""` rows after null cast |
| Ragged CSV | Hard fail |
| Setup freeze | Paths/sheets/keys cannot change in-session; quit/relaunch |
| Roster | Comparable intersection only; sort pending desc then name; accept column on roster and detail |
| Launch | `python Reconcile.py`; `--keys` comma-separated; `--session` alone OK; refuse mix with identity flags |
| Detail | Pending tab default; tabs: pending / accepted / equal / all matched |
| Paging | 100 rows from Polars; order raw key tuple |
| A-only / B-only grid | Keys + all other columns on that side, including that side’s extras |
| Fatal before TUI | stderr + exit |
| Fatal after TUI | Keep last state |
| Long strings | Wrap in grid and footer pane; no ellipsis truncate |
| Exit codes | 0 / 1 / 2 as §14 |
