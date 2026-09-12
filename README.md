# Strict data reconciliation TUI

Investigation TUI for **two-sided** data reconciliation. Compare is **exact raw text** (the only cast is true null → `""`). Walk remaining differences, accept known variation as in-session snapshots, and/or edit the source files in another tool and refresh until **pending = 0**.

This is **not** an audit, sign-off, or certification tool. It **never writes, patches, opens, or copies into** the files passed as `--a` / `--b`.

See [REQUIREMENTS.md](REQUIREMENTS.md) for the full spec.

## Install

Python **3.13**. From the repo root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Linux / macOS:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Dependencies: Polars, fastexcel, Textual.

## Run

User-facing command is the script (not a console-script name).

PowerShell:

```powershell
python Reconcile.py --a C:\data\left.csv --b C:\data\right.csv --keys id,year
python Reconcile.py --a C:\data\left.csv --b C:\data\right.csv --a-delim tilde --keys id
python Reconcile.py --a C:\data\left.xlsx --b C:\data\right.xlsx --a-sheet Sheet1 --b-sheet Sheet1 --keys id
python Reconcile.py --a C:\data\left.dat --b C:\data\right.txt --a-delim pipe --b-delim tilde --keys id
python Reconcile.py --a C:\data\left.csv --b C:\data\right.csv --a-encoding windows-1252 --keys id
python Reconcile.py --session C:\data\job.recon.zip
```

Linux / macOS:

```bash
python Reconcile.py --a ./tests/fixtures/left.csv --b ./tests/fixtures/right.csv --keys id,year
python Reconcile.py --a ./left.csv --b ./right.csv --a-delim tilde --keys id
python Reconcile.py --a ./left.dat --b ./right.txt --a-delim pipe --b-delim tilde --keys id
python Reconcile.py --a ./left.csv --b ./right.csv --a-encoding windows-1252 --keys id
python Reconcile.py --session ./job.recon.zip
```

`--keys` is a single comma-separated list. Surrounding spaces on each name are stripped; there is no quoting. `--session` cannot be mixed with `--a` / `--b` / `--a-sheet` / `--b-sheet` / `--keys` / `--a-delim` / `--b-delim` / `--a-encoding` / `--b-encoding`.

CLI paths (`--a`, `--b`, `--session`) may be relative to the invocation cwd; they are resolved immediately and **only absolute paths** are stored in the job identity and `.recon.zip`.

Excel sides require `--a-sheet` / `--b-sheet`. Delimiter and encoding flags are illegal on Excel sides. Excel is loaded with fastexcel as string columns: stored/cached values as-is (formulas are not evaluated). Merged cells are allowed; secondary merge cells may be empty strings. A `.csv` side (extension case-insensitive) defaults to comma when `--a-delim` / `--b-delim` is omitted; the flag still overrides. Other delimited files (`.txt`, `.dat`, no extension, …) **require** `--a-delim` / `--b-delim` (`comma`, `tilde`, `pipe`, `tab`, or the literal character `,` `~` `|` / tab). No sniffing and no `.txt`→tilde default. Encoding defaults to UTF-8 (`utf8`); override with `--a-encoding` / `--b-encoding` (`utf8`, `windows-1252`; `utf8-lossy` / `windows-1252-lossy` as explicit opt-in). The resolved delimiter character (including the `.csv` comma default) is frozen in `.recon.zip`.

## In the TUI

Home is the **roster** of remaining work (comparable columns, A-only keys, B-only keys, extras), sorted by pending, then top-pair %, then name. `Enter` drills in; `Esc` goes back (roster → Overview). `a` accepts the focused grain; `A` accepts a whole column or all unmatched on a side; `r` re-reads the live files; `e` / `o` export or open a `.recon.zip`; `q` quits; `?` help.

On the roster, `/` drafts comparable columns whose **names** match a Python regex. `=` drafts comparable columns where every **pending** value on one chosen side is exactly a sentinel string (raw text; no trim, no regex, no expression; empty `""` is legal). Both fill a column draft (all-checked); `Space` toggles, `y` confirms accept-entire-column snapshots, `Esc` cancels. At most one draft is in flight. Insights cannot accept. Compare stays exact raw text.

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Quit with remaining pending = 0 |
| `1` | Quit with pending remaining |
| `2` | Hard fail (load/parse/schema). Message on stderr includes raw identifiers. |

Once the TUI is up, refresh/open/draft errors stay in the TUI and keep the last good state.

## Tests

```bash
python -m pytest
```
