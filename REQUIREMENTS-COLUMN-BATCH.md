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

## 5. “All actions default to draft”

**Reading A (strict):** every column-accept path — one roster row, detail “accept column”, regex, Polars — only **adds to / sets draft**. Nothing is accepted until Confirm.

**Reading B (narrow):** only **batch/power** actions (regex, Polars, select-all) go through draft+toggle+confirm. Single-column accept on roster/detail stays immediate.

This feature’s headline is batch/power. Strict reading also adds a review step to the simple case.

**Open — must decide before merge.** See Q1.

Until decided, this draft specifies the **mechanism** (selector → checked draft → toggle → confirm) for power actions, and flags single-column as unresolved.

---

## 6. Where it lives in the TUI

**Column roster** is the home.

- Roster rows gain a draft checkbox (in/out of draft).
- A command opens a **power select** panel: regex field, expression field (or two tabs), Run, resulting draft count.
- After Run, roster is filtered or sorted to show drafted columns first **PROVISIONAL**, all drafted checked.
- Footer or bar: `draft N` + **Confirm accept** + **Clear draft**.
- Confirm is explicit (not implicit on leaving the panel).

Column **detail** does not run regex/Polars (those are multi-column). If strict reading of §5: detail “accept column” only checks that column into draft.

---

## 7. Selector: regex on column name

- Applied to **exact** comparable column names (the same strings as pairing).
- **PROVISIONAL dialect:** Python `re.search` (substring unless the user anchors with `^` `$`).
- **PROVISIONAL:** case-sensitive, to match exact-name pairing. `(?i)` if they want insensitive.
- Invalid pattern: in-TUI error, draft unchanged.
- Empty pattern: in-TUI error, draft unchanged (**PROVISIONAL**).
- Result: every comparable name that matches; **all of them enter the draft, checked**.

Does not look at values. Does not include key columns or extras.

**Open:** Run replaces the draft, or unions with the current draft? See Q3.

---

## 8. Selector: Polars expression on column values

Intent example: side B is entirely the placeholder `——` for that column → those columns should appear in the draft (still not accepted until confirm).

### 8.1 Frame the expression sees

For each comparable column, independently, a two-column Polars frame of **raw strings** (nulls already `""`):

| Column | Meaning |
|---|---|
| `a` | Side A value |
| `b` | Side B value |

**Row universe (open — Q2), two candidates:**

| Universe | Rows in `{a, b}` |
|---|---|
| **Pending only** | Matched keys where this column is a **pending** cell mismatch |
| **All matched** | Every matched key, including equal and accepted |

The example “B is all `——`” is a statement about the **whole column on B**, which is **all matched** (and still does not include A-only / B-only rows; those are not cell diffs). **All matched** is the better fit for that example. Pending-only would miss columns where some rows already equal `——` on both sides.

**Not in the frame (v1):** key columns, other fields, extras, A-only/B-only rows, insight flags.

### 8.2 What the expression must return

The user writes a Polars expression that **reduces to a single boolean per comparable column** (include in draft or not).

Example for “B is all `——`” on all-matched rows:

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

This is still **selection**, not compare. A column can match `(pl.col("b") == "——").all()` and still have exact A vs B diffs (e.g. A has real text, B is all `——`). Confirm then snapshot those diffs.

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

1. **Immediate column accept** on roster and detail (§9.2, §15.2) vs mandatory draft+confirm for “all actions.”
2. **Insights remain view-only** — this feature is a separate, explicit selector, not insight-accept. Compatible if we keep that split.
3. **Polars-owns-data:** expression evaluation must stay in Polars (long/unpivot + group, or per-column frame). Do not pull full columns into Python to test the predicate. Roster checkboxes are a small name set; that part may be Python.
4. **Session zip** today has acceptances, not a draft set. Merge needs a persist decision (§4).
5. **Footer** would gain `draft N` and Confirm/Clear, in addition to pending counts.

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
| Selector → draft, not accept | Locked for this draft |
| Individual toggle before confirm | Locked |
| Regex on exact comparable names | Locked as a selector type |
| Polars on values as a selector type | Locked as a selector type |
| Confirm = existing column-accept snapshots | Locked |
| Expression namespace `a` / `b` only | Proposed |
| Must reduce to boolean scalar | Proposed |
| Row universe all matched vs pending | Open |
| Replace vs add vs restrict | Open |
| Single-column accept through draft? | Open |
| Regex dialect / case | Open |
| Persist draft in zip | Open (lean no) |
| Zero-row `.all()` | Open (lean exclude) |

---

## 13. Open questions

**Q1. Single-column accept**  
Does clicking accept on one roster/detail column still accept immediately, or does it only check that column into the draft until Confirm?

**Q2. Row universe for Polars**  
Should `(pl.col("b") == "——").all()` mean all **matched** keys, or only **pending** mismatches in that column?

**Q3. Successive Runs**  
Does a new regex/expression **replace** the draft, **add** to it, or **restrict** it? Need one default. Extra modes?

**Q4. Regex**  
Python `re.search`, case-sensitive, empty pattern refused — confirm or change.

**Q5. Draft persistence**  
Discard draft on refresh/quit (only confirmed snapshots go to `.recon.zip`), or save draft too?

**Q6. Zero-row columns**  
Exclude from expression matches when the universe has no rows?

**Q7. Roster filter after Run**  
Show full roster with checkboxes, or temporarily only drafted columns?
