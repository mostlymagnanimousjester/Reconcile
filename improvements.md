# Planned simplifications

- **`m` is pair-only.** Drop the column-picker. `/`, `=`, and Space already choose columns. On the pair list, `m` applies **this pair** to the live column draft, or to every pending column that has that pair. On the roster, keep only the pair-picker (the union), not a column-picker. Purpose: `""` → `0` (and similar) without a two-step wizard. Inspection tabs (Accepted / Equal / All matched) stay — they are optional and useful when assessing pending.
- **`u` always undoes the last accept, as one unit.** After a pair `a`, `u` restores that pair. After column `A` or draft `y`, `u` restores those columns. After pair-only `m` applies e.g. `""` → `0` on eight columns, `u` reverses that apply on all eight — not eight undos, and not the pair under the cursor. If nothing has been accepted yet, `u` errors (same idea as `y` with no draft).
- **`U` stays “undo this column”** on the pair list only — the inverse of `A`. It does not depend on what you did last. That is the only other undo. Drop the extra meaning where `u` undoes the focused row when there is no “last bulk” memory.

## Actionable insights

An insight is allowed only if it names a next keystroke on **that** screen. If it does not, it is not an insight.

### Problem

Roster `speculative` mixes one real next key with noise.

The real key is sentinel → `=`. The rest is column-level `.any()` transform flags (`equal if trim`, case-fold, trim+case, equal as numbers, invisible/odd whitespace, same date) plus unmatched/extras fuzzy tags (`would match if…`, `name would pair if…`, `near-miss`). Those flags mean “some pair in this pile looks related.” They cannot drive `a` / `=` / `m` / `:`.

Pair and cell tags sit on the exact grain. Those can stay.

`top-pair %` is already a column. It is a stat, not an insight. Do not promote it.

### Roster

Sentinel only. Formats already shipped:

- `sentinel A=0`
- `sentinel B=""`
- `sentinel both A=x B=y`

That text is the `=` recipe: pick the side, type the constant. No sentinel → empty `speculative`.

Cut every column-level transform flag. In `reconcile/roster.py`, `_roster_agg_maps` / `_build_roster_cache` must stop emitting trim / case / trim+case / numeric / whitespace / same-date from pending `.any()`. Drop those agg bits if nothing else reads them. Cap-of-three is irrelevant once only sentinel remains.

### Pair / cell

Keep, per exact pair, from `cell_insights`:

- `equal if trim`
- `equal if case-fold`
- `equal if trim+case`
- `equal as numbers`
- `same date`

Drop `invisible/odd whitespace` when `equal if trim` already applies (NBSP / pad). Do not keep both. A leftover-only whitespace tag still does not name a keystroke — omit it.

Do **not** add sentinel to `cell_insights`. Sentinel is a column fact. `test_cell_insights_do_not_treat_token_lists_as_sentinels` already locks that.

### A-only / B-only / extras

Empty `speculative`. Always.

Cut `would match if…`, `name would pair if…`, `near-miss`. Those suggest a pairing the product does not do. `i` is how you reach unmatched rows and mismatched columns.

Delete `unmatched_key_insights` (dead outside `test_unmatched_trim`). Delete `extra_insights` and `_unmatched_side_tags` with them. `pages.py` extras rows keep a `speculative` field; it is `[]`.

### After `m` ships

No new insight type. Pair-only `m` on the focused pair is the keystroke the pair tags already point at. Roster sentinel still points at `=`, not at `m`.

### Non-goals

- No ML, no scoring, no near-miss revival
- No `shared value pattern` (already gone; keep it gone)
- No `speculative:` prefix on the text
- No accept-by-insight-group
- `=` stays sentinel (not regex). `:` stays name regex
- Inspection tabs stay
- Pair-only `m` and last-accept `u` stay the bullets above — this section does not implement them

When this ships, rewrite REQUIREMENTS §10.1–10.3 to this grain. Do not change compare, remaining counts, or pairing.

### Tests

Characterization after the cut: roster `speculative` is sentinel-or-empty; the pair list still shows transform tags.

- `tests/test_insights.py` — `test_roster_same_date_via_polars_any` (roster must not carry `same date`; `same_date_expr().any()` may stay as a pair-grain check). `test_trim_case_numeric_whitespace` (pair/cell keep trim / case / numbers; drop the redundant whitespace assertion). `test_extra_near_miss` and `test_unmatched_trim` go away with the functions.
- `tests/test_golden_derived.py` — `test_golden_roster_tags_bytes`: `trim_c` / `case_c` / `num_c` / `ws_c` / `date_c` keep only their sentinel (e.g. `trim_c` becomes `sentinel both A=" Y" B=Y`). `test_golden_extras_tags_slice`: extras tags are empty (`[]` on the extras slice, `""` on the roster extra row).
- `tests/test_tui.py` — `test_roster_speculative_column_has_no_prefix` stays (sentinel, no prefix).
