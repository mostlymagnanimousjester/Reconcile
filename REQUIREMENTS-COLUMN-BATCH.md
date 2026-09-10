# Feature draft: power-user batch column accept

Status: **independent working draft**. Not merged into `REQUIREMENTS.md`. Conflicts with the main spec are listed in §10 and must be resolved before merge.

Purpose of this document: pin down **column-level accept** when the user wants to hit many columns at once, without weakening the exact-text compare contract.

---

## 1. Intent

On the column roster (and only as a way to **choose which columns** to column-accept), a power user can:

1. Run a **selector** (regex on column **name**, or a Polars expression on column **values**).
2. Land on a **draft selection** of comparable columns — not an accept.
3. **Toggle** individual columns in or out of that draft.
4. **Confirm** to run the existing column-accept semantics on whatever is still drafted.

The selector is a search/filter for columns. It is not a new compare rule, not a cast, and not “ignore these columns forever.”

Confirm still means: for each drafted column, bulk-snapshot **current** cell mismatches in that column (`key, column, valA, valB`). New mismatches after refresh stay pending. Same as today’s column accept.

---

## 2. Non-goals (this feature)

- Fuzzy column-name matching (beyond the regex the user typed)
- Accept-by-insight-group (trim/case/date chips stay view-only)
- Polars expressions that change values, recode, or join
- Selecting **cells** or unmatched **keys** with these selectors (column roster only)
- Auto-confirm. No selector applies accept in one step
- Replacing exact compare with the expression (expression is selector only)

---

## 3. Contract (unchanged)

- Compare remains exact raw text; null → `""` only.
- Selectors may **look at** raw `a` / `b` strings. They must not write them.
- Remaining pending counts change only after **Confirm**, and only via existing snapshot rules.
- Invalid regex or expression: **in-TUI error**, last good state, **draft unchanged**.

---

## 4. Draft selection

A session has one **column draft**: a set of comparable column names (non-key A∩B names).

| Property | Rule |
|---|---|
| Default | Empty |
| After a selector Run | Draft := that Run’s hits with pending mismatches, **all checked**. Columns with zero pending are already reconciled (§8.4) and are **not** drafted |
| Toggle | Each drafted (or roster) row can be flipped in/out without re-running the selector |
| Confirm | Column-accept every **still-checked** drafted column (pending snapshots only). Then draft is empty |
| Cancel | Draft becomes empty; no accepts |
| In-flight | At most one selector result at a time. Confirm or Cancel before another regex or Polars Run (§9) |
| Quit / `.recon.zip` | Draft is **not** saved. Load restores **confirmed** snapshots only. Draft starts empty |

Draft is **not** an acceptance. Refresh does not convert draft into accepted.

On refresh (in-session, last good state if refresh fails):

- Selector is not re-run
- Column gone from comparable set → drop from draft
- Drafted column now has zero pending → drop from draft (already reconciled; nothing to accept)
- New comparable column → not added

---

## 5. Immediate vs draft

**Single-column accept stays immediate.** Roster or detail “accept this column” snapshots that column’s **current pending** mismatches now. It does not wait for Confirm. It does not require the draft.

**Power/batch selectors** (regex, Polars) never accept. They only fill a draft (all hits checked). The user toggles, then Confirm. Confirm snapshots **pending** mismatches only, same as single-column accept.

Draft checkboxes and Confirm exist for the batch path. They are not a gate in front of one-column accept.

---

## 6. Where it lives in the TUI

**Column roster** is the home.

- Roster rows gain a draft checkbox (in/out of draft).
- A command opens a **power select** panel: regex field, expression field (or two tabs), Run, resulting draft count.
- After Run, drafted columns are checked. **PROVISIONAL:** full roster remains visible (not a drafted-only filter).
- Footer or bar: `draft N` + **Confirm accept** + **Cancel**.
- Confirm and Cancel are explicit.
- While a draft is in flight, regex Run and Polars Run are **disabled** until Confirm or Cancel.

Column **detail** does not run regex/Polars (those are multi-column). Detail “accept column” remains **immediate** (pending mismatches in that column only).

---

## 7. Selector: regex on column name

- Applied to **exact** comparable column names (the same strings as pairing).
- Dialect: Python `re.search` (substring unless the user anchors with `^` `$`).
- Case-sensitive. `(?i)` in the pattern if they want insensitive.
- Invalid pattern: in-TUI error, draft unchanged.
- Empty pattern: in-TUI error, draft unchanged.
- Result: every comparable name that matches; **all of them enter the draft, checked**.

Does not look at values. Does not include key columns or extras.

Regex Run is refused (in-TUI, draft unchanged) if a draft is already in flight. Confirm or Cancel first. Same for Polars (§9).

---

## 8. Selector: Polars expression on column values

Intent example: side B is entirely the placeholder `——` for that column → those columns should appear in the draft (still not accepted until confirm).

### 8.1 Frame the expression sees

For each comparable column, independently, a two-column Polars frame of **raw strings** (nulls already `""`):

| Column | Meaning |
|---|---|
| `a` | Side A value |
| `b` | Side B value |

**Row universe: pending mismatches only.** Matched keys where this column’s A ≠ B and that mismatch is **not** already accepted. Equal rows and already-accepted snapshots are not in `{a, b}`. A-only / B-only keys are not in the frame.

So `(pl.col("b") == "——").all()` means: **among this column’s pending mismatches**, every B value is `——`. It does not mean “every B cell in the whole file.”

**Not in the frame (v1):** key columns, other fields, extras, A-only/B-only rows, insight flags, equal cells, accepted cells.

### 8.2 What the expression must return

The user writes a Polars expression that **reduces to a single boolean per comparable column** (include in draft or not).

Example for “every **pending** B value is `——`”:

```text
(pl.col("b") == "——").all()
```

Other examples (illustrative, not extra product):

```text
(pl.col("b") == "").all()
(pl.col("b").str.len_chars() == 0).all()
```

If the expression returns a Series/boolean-per-row instead of a scalar: **in-TUI error** asking for a reduction (`.all()`, `.any()`, `.mean()` comparison, etc.). Do not silently apply `.all()`.

If the expression returns non-boolean scalar: in-TUI error.

### 8.3 Safety

- Expression is evaluated only against that `{a, b}` frame (implementation may batch internally; user-visible namespace is still `a` and `b` only).
- No IO, no scans of other files, no Python `map_elements` callbacks, no reaching other table columns by original field name.
- Reference to a name other than `a` / `b`: in-TUI error, draft unchanged.
- Timeout / engine error: in-TUI error, draft unchanged.

This is still **selection**, not compare. A column can match `(pl.col("b") == "——").all()` because every *pending* B is `——` while A still has other text. Confirm then snapshots those pending pairs.

### 8.4 Columns with zero pending rows

If a column has **no pending mismatches**, there is nothing to reconcile for that column. It is already complete (pending count 0).

- Do **not** add it to the draft (including when Polars `.all()` would be vacuously true on zero rows).
- Do **not** write accept snapshots (there are no pending cells).
- It is already in the done state for remaining-work. This is **not** a standing “ignore column on future refresh” flag. After refresh, any **new** pending mismatches in that column are pending, per the main spec.

Selectors only propose columns that currently have at least one pending mismatch.

---

## 9. Selectors are independent

Regex and Polars are **separate operations**. They do not stack, union, or intersect.

Flow:

1. Draft is empty.
2. User Runs **either** a name regex **or** a Polars expression.
3. Hits with pending mismatches become the draft, all checked. User may uncheck individuals.
4. User **Confirm** (accept pending snapshots) or **Cancel** (discard draft).
5. Only then may they Run the other selector (or the same kind again).

If a draft is in flight, a second Run is an in-TUI error (or the control is disabled): confirm or cancel the current selection first.

Example: regex `^S` checks **Status**. Polars is not available until Status’s draft is Confirmed or Canceled. After that, `(pl.col("b") == "——").all()` is a new, empty-started operation.

Manual uncheck is the only refinement inside one operation.

---

## 10. Conflicts with `REQUIREMENTS.md` (do not merge until asked)

1. **Immediate column accept** on roster/detail stays. This feature **adds** a batch path. Compatible.
2. **Insights remain view-only.** Compatible.
3. **Polars-owns-data:** evaluate expressions in Polars. Roster checkboxes are a small name set.
4. **Session zip:** no draft in the zip. Confirmed snapshots only. Compatible with current zip contents.
5. **Footer:** `draft N` + Confirm + Cancel while a draft is in flight.

Ready to merge when you say so. Remaining nits: expression must be a boolean scalar (proposed), `a`/`b` namespace (proposed), per-column vs one-batch undo after Confirm (lean: existing per-column undo), roster filter vs full list while drafting (lean: full roster, checks on).

---

## 11. Confirm semantics (reuse main spec)

On Confirm, for each name in the draft:

- Run **accept entire column** as already defined: snapshot every **current pending** cell mismatch in that column.
- Do not snapshot equals.
- Do not snapshot accepted-already cells again.
- Then clear the draft.

Undo after Confirm is the existing **per-column** undo, not one undo for the whole batch.

---

## 12. Decision log (this feature only)

| Topic | Status |
|---|---|
| Selector → draft, not accept | Locked |
| Individual toggle before confirm | Locked |
| Single-column accept | Locked: immediate; pending mismatches only |
| Batch confirm / expression universe | Locked: pending mismatches only |
| Regex | Locked: Python `re.search`, case-sensitive, empty/invalid refused |
| Polars on values as a selector | Locked |
| Selectors | Locked: independent; Confirm or Cancel before the next Run |
| Zip | Locked: restore confirmed snapshots only; draft never persisted |
| Zero pending | Locked: already reconciled; not drafted; no snapshots; not a future-ignore |
| Confirm = existing column-accept snapshots | Locked |
| Expression namespace `a` / `b` only | Proposed |
| Must reduce to boolean scalar | Proposed |

---

## 13. Remaining nits (optional)

- After Run, keep the full roster with checkboxes, or show only drafted columns until Confirm/Cancel?
- Expression must return a boolean scalar (proposed yes).
- Undo batch Confirm as many per-column undos (locked lean above) vs one bundle.
