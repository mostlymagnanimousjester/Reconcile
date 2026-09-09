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

Every cell in the used header+data range must be **text**. Any non-text type (number, date, bool, cached formula result, etc.): **hard fail**.

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

**PROVISIONAL detection:**

- Delimiters tried: comma, tab, semicolon, pipe. One clear winner or hard fail.
- Encodings tried: UTF-8 with BOM, UTF-8, then Windows-1252, only if unambiguous. If UTF-8 decodes cleanly, UTF-8 wins. Do **not** silently fall back to latin-1 (it always “decodes”).

Excel encoding is not a separate concern; fastexcel supplies cell strings.

---

## 7. Keys and join

- Keys are **required**, specified on the CLI (`--key`, repeatable).
- Composite keys are allowed. Order is the CLI order.
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
- `YYYY-MM-DDTHH:MM:SS` (optional fractional seconds **PROVISIONAL**)
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

Thresholds are a starting point and may be tuned.

### 10.6 Not v1

- Fuzzy unmatched-key matching (Levenshtein, “this row looks like that row”)
- Cross-column value hunting
- Date-format guessing beyond §10.4
- Accept-by-insight-group

---

## 11. Architecture: Polars owns the data

Stack: **Python, Polars, fastexcel, Textual**. Must run on Windows.

- Load, join, compare, counts, remaining sets, and insights stay in Polars.
- The TUI must **not** convert full frames to Python objects.
- The TUI **requests pages** (100 rows) and small summaries (roster, counts, transition table).
- Python/Polars round-trips must be minimized and explicit (page structs / small aggregate frames only).

Roster is one row per comparable column (small); it may be fully materialized. Cell/key lists are paged at **100**.

---

## 12. CLI

The TUI does not collect paths, sheets, or keys. Job identity is CLI (or a session zip).

**PROVISIONAL flag names:**

| Flag | Meaning |
|---|---|
| `--a PATH` | Side A file (absolute path stored in session) |
| `--b PATH` | Side B file |
| `--a-sheet NAME` | Required if A is `.xlsx`/`.xlsm` |
| `--b-sheet NAME` | Required if B is `.xlsx`/`.xlsm` |
| `--key NAME` | Repeatable; composite key in this order |
| `--session PATH` | Load `*.recon.zip` and live-reread sources |

Rules:

- `--session` alone is valid (zip contains paths, sheets, keys, detections, snapshots, context sets).
- Without `--session`: `--a`, `--b`, and at least one `--key` are required. Sheet flags required per Excel side.
- **PROVISIONAL:** mixing `--session` with `--a` / `--b` / `--a-sheet` / `--b-sheet` / `--key` is a hard fail. The zip is the identity.
- Duplicate `--key` names: hard fail.
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

Export and open from the TUI. **PROVISIONAL:** also `--session` on CLI.

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

**PROVISIONAL** sort: pending count descending, then exact column name.

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

How the filter is presented in the UI (cycle key vs labeled menu vs tabs) is **PROVISIONAL** — pick whatever is obvious in Textual. The four names above are the requirement; a rotating unlabeled control is not sufficient.

Grid (100-row pages):

- key columns (always; not optional)
- A value and B value for this column (raw strings; truncated in the grid)
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

Selected cell: full raw A and B strings in a **footer pane** (no truncation). Grid cells truncate to a max width.

Paging: 100 rows from Polars. **PROVISIONAL** page order: composite key as a tuple of raw strings (stable, exact).

Actions: accept/undo one cell; accept/undo entire column; add/remove context; change view filter; next/prev page; back to roster.

### 15.4 A-only keys / B-only keys

Reachable from **Overview**.

100-row pages. **PROVISIONAL** order: composite key tuple of raw strings.

Each row: key columns + **all comparable columns** (intersection minus keys) from the side that has the row, raw. (Not extras.)

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
- Current detail view filter when on column detail (`pending` / `accepted` / `equal` / `all matched`)
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
| View filter (detail) | v |
| Help | ? |
| Quit | q |

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

## 17. Packaging (PROVISIONAL)

- `pyproject.toml` console script (name TBD)
- Windows-supported Python (version TBD; 3.11+ intended)
- No extra services, no database, no auth

---

## 18. Open items for review

1. Exact CLI flag names and `--key` vs comma-separated `--keys`.
2. Mixing `--session` with identity flags (proposed: refuse).
3. Detail **view filter** control (tabs vs menu vs cycle with visible label).
4. Page order for detail / A-only / B-only (proposed: raw key tuple).
5. Roster sort (proposed: pending desc, then name).
6. Keybinding map.
7. Categorical thresholds (30 / 30 / 50) and date format list.
8. Delimiter/encoding detector scoring.
9. Console script name, Python version, Windows terminal assumptions (Windows Terminal vs conhost).
10. Whether A-only/B-only rows should also show extra-only columns from that side.
11. Max truncated cell width in the grid.
12. Behavior if Excel reports a used range with non-text in unused/trailing columns after empty-row drop.

---

## 19. Decision log (locked)

| Topic | Decision |
|---|---|
| Sides | Always 2 |
| Inputs | Delimited + xlsx/xlsm; 2 sheets max compared |
| Compare | Exact raw text; null → `""` only |
| Keys | Required, composite OK; A-only/B-only reviewed; dups hard fail |
| Column pair | Exact name; extras surfaced |
| Product | Investigation TUI; session acceptances; shrink or accept |
| Excel | Text cells only; fastexcel; no formulas |
| Headers | Required |
| Platform | Windows; UTF-8 not guaranteed |
| Detect | Encoding + delimiter; hard fail if ambiguous; no override |
| Refresh | Manual; sources updatable; snapshots reapplied |
| Undo | Yes, in session |
| Persist | `.recon.zip`, absolute paths, live reread |
| Insights | Speculative, view/filter only; dates yes; no fuzzy keys; categorical transitions pending-only |
| Context columns | Both-sides intersection only; per column; persisted |
| Empty rows | Drop all-`""` rows after null cast |
| Ragged CSV | Hard fail |
| Setup freeze | Paths/sheets/keys cannot change in-session; quit/relaunch |
| Roster | Comparable intersection only; accept column on roster and detail |
| Launch | CLI identity; `--session` alone OK |
| Detail default | Pending only; extra views helpful, not default |
| Paging | 100 rows from Polars |
| Fatal before TUI | stderr + exit |
| Fatal after TUI | Keep last state |
| Long strings | Truncate in grid; full raw in footer pane |
| Exit codes | 0 / 1 / 2 as §14 |
