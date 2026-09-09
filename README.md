# Strict data reconciliation TUI

Provisional product: a **very strict** two-sided data reconciliation TUI (exact raw text; true nulls become empty strings). Investigation-focused: walk differences, accept known variation in-session, refresh when sources change.

**No application code yet.** Shape is being locked in `REQUIREMENTS.md`.

## Requirements

See [REQUIREMENTS.md](REQUIREMENTS.md) for the provisional spec (inputs, compare contract, CLI, TUI screens, session zip, insights). Review and modify that document before implementation.

## Status

- Stack (planned): Python, Polars, fastexcel, Textual
- Platform: Windows
- Not a web app; not an audit/sign-off tool
