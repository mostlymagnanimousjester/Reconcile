# Planned simplifications

- **`m` is pair-only.** Drop the column-picker. `/`, `=`, and Space already choose columns. On the pair list, `m` applies **this pair** to the live column draft, or to every pending column that has that pair. On the roster, keep only the pair-picker (the union), not a column-picker. Purpose: `""` → `0` (and similar) without a two-step wizard. Inspection tabs (Accepted / Equal / All matched) stay — they are optional and useful when assessing pending.
- **`u` always undoes the last accept, as one unit.** After a pair `a`, `u` restores that pair. After column `A` or draft `y`, `u` restores those columns. After pair-only `m` applies e.g. `""` → `0` on eight columns, `u` reverses that apply on all eight — not eight undos, and not the pair under the cursor. If nothing has been accepted yet, `u` errors (same idea as `y` with no draft).
- **`U` stays “undo this column”** on the pair list only — the inverse of `A`. It does not depend on what you did last. That is the only other undo. Drop the extra meaning where `u` undoes the focused row when there is no “last bulk” memory.

## Actionable insights

An insight is allowed only if it names a next keystroke on **that** screen. If it does not, it is not an insight.

### Problem

Roster `speculative` is one packed string. It mixes three jobs that cannot share a cell:

1. **Sentinel → `=`.** Today `format_sentinel_insight` writes `sentinel A=0`, `sentinel B=""`, or `sentinel both A=x B=y` into the same field as the transform tags. The `=` recipe is the side plus the constant. Those are three different kinds. A row can have A, B, or both (`column_sentinel_frame`: a side is a sentinel iff it is one unique value on all comparable shared-key rows).
2. **Column-level transform flags.** `_roster_agg_maps` already computes `trim`, `case`, `both` (trim+case), `numeric`, `ws`, and `same_date`, then `_build_roster_cache` joins up to three tags with `", "`. Each flag is **`.any()`** over pending cells: “some pair in this pile looks related.” That cannot drive `a` / `=` / `m` / `:`. A planner needs **every** pending cell in that column to satisfy the predicate.
3. **Unmatched / extras fuzzy tags** (`would match if…`, `name would pair if…`, `near-miss`). Those suggest a pairing the product does not do. `i` is how you reach unmatched rows and mismatched columns.

`top-pair %` is already a roster stat column. It is not an insight. Do not promote it, do not drop it.

Pair and cell tags sit on the exact grain (`cell_insights`). Those stay, for **this pair**.

### Roster columns

Replace the packed `speculative` string on comparable-column rows. TUI `_roster_headers` / `_fill_roster` grow these columns. `RosterRow.speculative` is no longer the roster insight surface (pair/cell/extras pages still have a `speculative` field). Cap-of-three is gone.

Keep the existing stat columns: `pending`, **`top-pair %`**, `equal`, `cat`. Draft / `status` (`v`) unchanged.

#### Sentinel (value cells, not y/n)

Three columns. Existing sentinel definition — do not invent a new one. `column_sentinel_frame` / `_sentinel_map` / `format_sentinel_value`:

| Header | When the cell is filled | Cell text |
|---|---|---|
| `sent A` | `sent_a` is not null | `format_sentinel_value(sent_a)` — e.g. `0`, `""` |
| `sent B` | `sent_b` is not null | `format_sentinel_value(sent_b)` — e.g. `0`, `""` |
| `sent both` | both sides are constants on comparable rows | `A=x B=y` using `format_sentinel_value` on each side |

A row can fill A and/or B. The both column is **only** when both sides are constants (same as today’s `format_sentinel_insight(..., ...)` both branch). A both-row therefore fills `sent A`, `sent B`, and `sent both`.

Do **not** write `sentinel A=0` into these cells. The header is the kind; the cell is the value. That is the `=` recipe: pick the side, type the constant.

#### Checks (y / n)

Keep every existing transform check. Each gets its own roster column. Cells are **`y`** if that column’s pending pile satisfies the predicate, **`n`** if not. Two states only — “n if both” means **`n` if not**. Do not invent a third state, a blank, or a leftover-only tag on the roster.

| Header | Predicate (same bits `_roster_agg_maps` already names) |
|---|---|
| `trim` | `val_a.str.strip_chars() == val_b.str.strip_chars()` |
| `case` | `val_a.str.to_lowercase() == val_b.str.to_lowercase()` |
| `trim+case` | strip then lower on both sides (`both` in the agg) |
| `num` | both parse as float, neither is blank after strip, floats equal (`numeric`) |
| `ws` | NBSP / tab / CR / LF, or either side differs from `strip_chars()` (`ws`) |
| `date` | `same_date_expr()` (`unambiguous_date_expr` on each side, both non-null, dates equal) |

`trim+case` on the roster is **`.all()` of the combined strip+lower predicate**, not the exclusive leftover `cell_insights` uses (`both_eq and not trim_eq and not case_eq`). A pile that is all trim-equal is also all trim+case-equal; both columns may be `y`. That is honest. Pair/cell keep the exclusive leftover so they do not print `equal if trim+case` next to `equal if trim`.

### Hide rules

Compute visibility from **pending comparable-column rows only** (not settled `v` rows, not A-only / B-only / extras).

- **Sentinel kind:** if no pending column has that kind, do not show that header. No empty `sent A` when nobody has an A-present sentinel (`sent_a` set — A-only or both). Same for `sent B` (`sent_b` set) and `sent both` (both set).
- **Check:** if every pending row is `n` for that check, hide that column. No empty `trim` header when nobody’s whole pile is trim-equal.

Settled rows (`v` on) may show empty / `n` under headers that pending rows kept visible. Do not keep a header alive for settled rows alone.

### All pending cells, not `.any()`

**Behavior change.** Today `_roster_agg_maps` does `.any()` on each predicate, then `_build_roster_cache` treats a true as “show the tag.” That means one lucky pair lights the column.

A check column is `y` only if **every pending cell** in that roster row satisfies the predicate (trim-equal, case-equal, numeric-equal, whitespace, same date, …).

In `roster._roster_agg_maps` / the lazy `stats_lf` collected with `pl.collect_all`:

- Prefer **`.all()`** over **`.any()`** for `trim`, `case`, `both`, `numeric`, `ws`, and `same_date`.
- Fast path: Polars / the predicate may fail on the first failing row (`all()` short-circuit). Do not scan for “any hit” and then promote it.
- Empty pending is not a pending roster row; do not use `all()`-of-empty as a reason to show `y`.

Pair/cell do **not** change grain: `cell_insights` is still **this pair**.

### SAS dates (same predicates on roster and pair/cell)

`same date` is one check. Roster `date` and pair/cell `same date` must use the **same** parsers.

Today, both lists are the same five strptime / Polars codes:

```
DATE_FORMATS / _POLARS_DATE_FORMATS
  %Y-%m-%d              2020-01-02
  %Y-%m-%dT%H:%M:%S     2020-01-02T14:30:00
  %m/%d/%Y              01/02/2020
  %d/%m/%Y              01/02/2020
  %Y%m%d                20200102
```

`parse_unambiguous_date` tries every `DATE_FORMATS` entry with `datetime.strptime` and returns the date only when the successful parses collapse to **one** calendar date; otherwise `None`. `unambiguous_date_expr` is the Polars twin (`str.to_datetime(fmt, strict=False).dt.date()`, `concat_list` → drop-nulls → unique → keep iff `len == 1`). `same_date_expr` is `da` and `db` both non-null and equal. `cell_insights` calls `parse_unambiguous_date` on each side. Keep that unambiguous rule: **`01/02/2020` and `01/02/03` stay rejected** (US vs EU, and with 2-digit years more slash forms collide).

Extend **both** lists together (Python and Polars must stay in lockstep) with the common SAS named-month and YYMMDD-style forms. Concrete add:

| SAS / ISO | Example | Format code |
|---|---|---|
| DATE9. | `15JAN2024` | `%d%b%Y` |
| DATE7. | `15JAN24` | `%d%b%y` |
| DATE11. | `15-JAN-2024` | `%d-%b-%Y` |
| MMDDYY8. | `01/15/24` | `%m/%d/%y` |
| MMDDYY10. | `01/15/2024` | `%m/%d/%Y` (already shipped) |
| DDMMYY8. | `15/01/24` | `%d/%m/%y` |
| DDMMYY10. | `15/01/2024` | `%d/%m/%Y` (already shipped) |
| YYMMDD10. | `2024-01-15` | `%Y-%m-%d` (already shipped) |
| compact YYYYMMDD / YYMMDDn8. | `20240115` | `%Y%m%d` (already shipped) |
| MONYY7. | `JAN2024` | `%b%Y` (first of month) |
| MONYY5. | `JAN24` | `%b%y` (first of month) |
| DATETIME19. / DATETIME20. | `15JAN2024:14:30:00` | `%d%b%Y:%H:%M:%S` |
| DATETIME. / DATETIME16. | `15JAN24:14:30:00` | `%d%b%y:%H:%M:%S` |
| ISO-8601 date | `2024-01-15` | `%Y-%m-%d` (already shipped) |
| ISO-8601 datetime | `2024-01-15T14:30:00` | `%Y-%m-%dT%H:%M:%S` (already shipped) |

Month tokens are SAS English abbreviations (`JAN`…`DEC`). Implementation must accept that uppercase form in both `strptime` and Polars (`%b` is locale-sensitive — do not depend on a French/German locale). Two strings that parse to the same unambiguous calendar date under this list count as `same date` (`15JAN2024` vs `2024-01-15`, `15JAN24` vs `20240115`, `15JAN2024:14:30:00` vs `2024-01-15T14:30:00`). Time-of-day is ignored once the calendar date is taken (`.dt.date()` / `.date()`), same as today.

Do not add every SAS width/separator variant (spaces, colons between date parts, JULIAN., WEEKU.). Named-month + the slash/ISO/YYMMDD forms above are the list.

### Pair / cell

Keep, per exact pair, from `cell_insights`:

- `equal if trim`
- `equal if case-fold`
- `equal if trim+case` (exclusive leftover: only when strip+lower holds and neither trim-only nor case-only does)
- `equal as numbers`
- `same date` — **same** `parse_unambiguous_date` / format list as the roster `date` column

Drop `invisible/odd whitespace` when `equal if trim` already applies (NBSP / pad). Do not keep both. A leftover-only whitespace tag still does not name a keystroke — omit it.

The all-rows meaning is **roster only**. On pair/cell the check is still **this pair**.

Do **not** add sentinel to `cell_insights`. Sentinel is a column fact (`column_sentinel_frame`). `test_cell_insights_do_not_treat_token_lists_as_sentinels` already locks that.

### A-only / B-only / extras

Empty `speculative`. Always.

No would-match / near-miss / name-would-pair columns. Those are not these checks. `i` is how you reach unmatched rows and mismatched columns.

Delete `unmatched_key_insights` (dead outside `test_unmatched_trim`). Delete `extra_insights` and `_unmatched_side_tags` with them. `pages.py` extras rows keep a `speculative` field; it is `[]`.

### After `m` ships

No new insight type. Pair-only `m` on the focused pair is the keystroke the pair tags already point at. Roster sentinel columns still point at `=`, not at `m`.

### Non-goals

- No ML, no scoring, no near-miss revival
- No `shared value pattern` (already gone; keep it gone)
- No `speculative:` prefix on the text
- No accept-by-insight-group
- `=` stays sentinel (not regex). `:` stays name regex
- Inspection tabs stay
- Pair-only `m` and last-accept `u` stay the bullets above — this section does not implement them
- Do not implement TUI/engine code in the same change as this spec rewrite

When this ships, rewrite REQUIREMENTS §10.1–10.4 (grain + date list) and §15.2 (roster columns: drop packed speculative tags). Do not change compare, remaining counts, or pairing.

### Tests

Characterization after the change: roster insight surface is per-column cells (optional `sent A` / `sent B` / `sent both` value columns; `trim` / `case` / `trim+case` / `num` / `ws` / `date` as `y`/`n`, hidden when unused). Pair list still shows transform tags for **this pair**. Extras / unmatched tags are empty.

- `tests/test_insights.py`
  - **`test_roster_same_date_via_polars_any` must become all-pending / new formats.** Today’s fixture (`2020-01-02`/`20200102` on row 1, `nope`/`nope2` on row 2) is `.any() is True` and `.all() is False`. Rewrite: a pile where every pending cell is `same_date_expr` → roster `date` is `y` and `same_date_expr().all()` is True; a mixed pile → `n`. Add DATE9. / DATE7. / DATE11. / MONYY. / DATETIME. pairs against ISO or `%Y%m%d`. Rename off `_polars_any`.
  - `test_roster_ambiguous_us_eu_date_not_same_date` and `test_same_date_unambiguous` stay: `01/02/2020` still not a date; pair/cell `same date` uses the same predicates. Extend pair-grain tests for the new named-month forms (`15JAN2024` vs `2024-01-15`).
  - `test_roster_speculation_flags_sentinel_on_a_b_or_both`: assert `sent A` / `sent B` / `sent both` **values** (`0`, `""`, `A=NA B=z`), not a packed `sentinel A=0` string. Hide: a fixture with only B-present sentinels must not expose a `sent A` header.
  - `test_trim_case_numeric_whitespace`: pair/cell keep trim / case / numbers; drop the redundant whitespace assertion.
  - `test_extra_near_miss` and `test_unmatched_trim` go away with the functions.
- `tests/test_golden_derived.py` — `test_golden_roster_tags_bytes`: golden roster now has per-column `y`/`n` and optional sentinel columns. `trim_c` / `case_c` / `num_c` / `ws_c` / `date_c` are `y` on their own check (all pending rows match) and `n` on the others they do not satisfy; `sent_a` / `sent_b` / `sent_both` hold `format_sentinel_value` cells (`0`, `""`, `A=NA B=z`) rather than `speculative: "sentinel A=0"`. `mixed` / `plain` stay all-`n` / empty sentinels. `test_golden_extras_tags_slice`: extras tags are empty (`[]` on the extras slice, `""` on the roster extra row).
- `tests/test_tui.py` — `test_roster_speculative_column_has_no_prefix` stays in spirit (no `speculative:` prefix on remaining insight text; roster no longer ships one packed `speculative` column).
