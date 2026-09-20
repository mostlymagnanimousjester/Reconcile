# Planned simplifications

- **`m` is pair-only.** Drop the column-picker. `/`, `=`, and Space already choose columns. On the pair list, `m` applies **this pair** to the live column draft, or to every pending column that has that pair. On the roster, keep only the pair-picker (the union), not a column-picker. Purpose: `""` → `0` (and similar) without a two-step wizard. Inspection tabs (Accepted / Equal / All matched) stay — they are optional and useful when assessing pending.
