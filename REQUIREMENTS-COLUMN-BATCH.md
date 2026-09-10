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
| Default | Empty until a selector or a manual toggle adds members |
| After any selector | Draft is the selector’s result, **all members included** (checked). That is the default. The user then unchecks exceptions |
| Toggle | Each roster row (or a draft list) can be flipped in/out without re-running the selector |
| Confirm | Column-accept every drafted column that currently has pending cell mismatches. Empty-pending drafted columns are no-ops and drop from needing accept |
| Clear | Draft becomes empty; no accepts |
| Not confirm | Leaving the power-user UI without confirm **PROVISIONAL:** keep the draft for this session until clear/confirm/quit |

Draft is **not** an acceptance. Refresh does not turn draft into accepted.

**PROVISIONAL refresh vs draft:**

- Column gone from comparable set → drop from draft
- New comparable column → not added unless the user runs a selector again
- Selector is not re-run on refresh
- Draft is **PROVISIONAL** for `.recon.zip` (lean: do not persist draft; persist only real acceptances)

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
- After Run, roster is filtered or sorted to show drafted columns first **PROVISIONAL**, all drafted checked.
- Footer or bar: `draft N` + **Confirm accept** + **Clear draft**.
- Confirm is explicit (not implicit on leaving the panel).

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

How a second Run combines with an existing draft is still open (see §13 Q3, restated).

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
(pl.col("a") == pl.col("b")).all()
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

### 8.4 Empty pending / empty matched

If the row universe has **zero rows** for a column, `.all()` is true in Polars and `.any()` is false. **PROVISIONAL:** zero-row columns are **not** selected (treat as no evidence), so a vacant column is not drafted by accident.

---

## 9. Combining selectors and toggles

**PROVISIONAL apply modes** when Run is pressed (need Q3):

| Mode | Effect on draft |
|---|---|
| Replace | Draft := selector result (all checked) |
| Add | Draft := draft ∪ result |
| Restrict | Draft := draft ∩ result |

v1 can ship **Replace only** if we do not want three modes yet. Toggle-after-replace covers “uncheck a few.” Add/Restrict matter when stacking regex then expression.

Manual toggles always win until the next Run.

---

## 10. Conflicts with `REQUIREMENTS.md` (do not merge until resolved)

1. **Immediate column accept** on roster/detail stays as in the main spec. This feature **adds** a batch path; it does not replace one-column accept. Compatible.
2. **Insights remain view-only** — this feature is a separate, explicit selector, not insight-accept. Compatible if we keep that split.
3. **Polars-owns-data:** expression evaluation must stay in Polars (long/unpivot + group, or per-column frame). Do not pull full columns into Python to test the predicate. Roster checkboxes are a small name set; that part may be Python.
4. **Session zip** today has acceptances, not a draft set. Persist-draft is still open (§13 Q5).
5. **Footer** would gain `draft N` and Confirm/Clear when a draft is non-empty, in addition to pending counts.

---

## 11. Confirm semantics (reuse main spec)

On Confirm, for each name in the draft:

- Run **accept entire column** as already defined: snapshot every **current pending** cell mismatch in that column.
- Do not snapshot equals.
- Do not snapshot accepted-already cells again.
- Then clear those names from draft (they are no longer pending-mismatch columns, or still are if something failed — should not fail per column except keep last state on catastrophic error).

**PROVISIONAL:** Confirm is one undo granule or per-column undo still? Main spec undo is per column/cell. Lean: after confirm, undo is the existing per-column undo, not “undo whole batch as one.”

---

## 12. Decision log (this feature only)

| Topic | Status |
|---|---|
| Selector → draft, not accept | Locked |
| Individual toggle before confirm | Locked |
| Single-column accept | Locked: immediate; pending mismatches only |
| Batch confirm / expression universe | Locked: pending mismatches only |
| Regex | Locked: Python `re.search`, case-sensitive, empty/invalid refused |
| Polars on values as a selector | Locked as a selector type |
| Confirm = existing column-accept snapshots | Locked |
| Expression namespace `a` / `b` only | Proposed |
| Must reduce to boolean scalar | Proposed |
| Second Run vs existing draft | Open — question restated in §13 |
| Persist draft in zip | Open — question restated in §13 |
| Zero-row `.all()` | Open — question restated in §13 |

---

## 13. Remaining questions (restated)

**Q3. You already have a draft, then you Run again**

Suppose the roster has `Amount`, `Status`, `Note`, `Flag`.

1. You run regex `^S` → draft checkboxes: **Status** checked.
2. Without Confirm, you run `(pl.col("b") == "——").all()`, which matches **Amount** and **Flag**.

What should the checkboxes be?

- **Replace:** Amount and Flag checked; Status cleared (the last Run is the whole draft)
- **Add:** Status, Amount, and Flag all checked (Runs pile up)
- **Restrict:** nothing checked (only columns that were already drafted *and* matched this Run — here Status did not match the expression)

Need one of these as the v1 default. Extra modes can wait.

**Q5. You quit with a draft and never hit Confirm**

You checked eight columns via regex, did not Confirm, and either quit or exported `.recon.zip`.

Next launch (same zip or a new process): should those eight still be checked, or should the draft start empty and only **confirmed** accepts come back from the zip?

This is only about the **checkboxes**. Real accepts already persist as snapshots.

**Q6. A column has nothing pending**

`Comment` has zero pending mismatches (every matched key already has A = B for `Comment`). You run `(pl.col("b") == "——").all()`.

In Polars, “all rows satisfy X” on **zero rows** is True. So `Comment` would match even though there is nothing to accept.

Should `Comment` be checked in the draft anyway, or should a column with zero pending rows be skipped even if the expression would be vacuously true?

**Q7 (optional).** After Run, show the full roster with checkboxes, or only the drafted columns until Clear?
