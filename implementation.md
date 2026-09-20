# `-sheets` implementer checklist

Delete this file when the implementation is done. Do not merge it.

- [ ] Grammar: `PREFIX{N or start-end}` and exact comma list; HardFail on a bad `SET` (empty, bad braces, non-integer items, `start > end`, duplicates). Include the flag and the raw `SET`.
- [ ] `-sheets` / `--sheets` argparse. If `-sheets` is set, do **not** require `--a-sheet` / `--b-sheet`.
- [ ] Both `-sheets` and `--a-sheet` / `--b-sheet`: HardFail (mutually exclusive).
- [ ] Both workbooks must be Excel. Each expanded name must exist in both (preflight sheet lists; do not data-load later sheets).
- [ ] Startup: data-load only the first expanded pair (same engine as today).
- [ ] `S` next sheet: remaining work 0 (pending columns + unmatched rows + extras) and no draft → load that name on both workbooks, roster home, clean snaps.
- [ ] Mid-work `S` / last-sheet `S` / no-`-sheets` `S`: ERROR. Does not steal pair-list `A` or `[` / `]` tabs. Roster `A` stays ERROR.
- [ ] Footer / HELP / `?` document `S` when `-sheets` is active and remaining is 0.
- [ ] Tests: expand grammar; missing sheet HardFail; first-only load; `S` refused when pending; `S` advances when remaining 0; last sheet ERROR; `--a-sheet` / `--b-sheet` still work; exclusive flags HardFail.
- [ ] `pytest -q`
- [ ] DELETE this file.
