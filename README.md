# Strict data reconciliation TUI

Provisional product: a **very strict** two-sided data reconciliation TUI (exact raw text; true nulls become empty strings). Investigation-focused: walk differences, accept known variation in-session, refresh when sources change. **Read-only vs the source files** — this TUI never writes or opens them.

**No application code yet.** Shape is being locked in `REQUIREMENTS.md`.

## Requirements

See [REQUIREMENTS.md](REQUIREMENTS.md) for the spec (inputs, compare contract, CLI, TUI screens, keybindings, session zip, insights, batch column accept). Review and modify that document before implementation.

## Planned launch (Windows)

Python **3.13**, from **PowerShell**:

```powershell
python Reconcile.py --a C:\data\left.csv --b C:\data\right.csv --keys id,year
python Reconcile.py --session C:\data\job.recon.zip
```

## Status

- Stack (planned): Python 3.13, Polars, fastexcel, Textual
- Platform: Windows / PowerShell
- Not a web app; not an audit/sign-off tool
- TUI colors are specified for red blue-blocker lenses (no blue/green-only signals)
