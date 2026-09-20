#!/usr/bin/env python3
"""Launch the strict data reconciliation TUI.

User-facing command (PowerShell / POSIX):

    python Reconcile.py --a PATH.csv --b PATH.csv --keys id,year
    python Reconcile.py --a PATH.csv --b PATH.csv --a-delim tilde --keys id
    python Reconcile.py --a PATH.xlsx --b PATH.xlsx --a-sheet Foo --b-sheet Bar --keys id
    python Reconcile.py --a PATH.xlsx --b PATH.xlsx -sheets data{1-4,7} --keys id
    python Reconcile.py --a PATH --b PATH --a-delim pipe --b-delim tilde --keys id
    python Reconcile.py --a PATH.csv --b PATH.csv --a-encoding windows-1252 --keys id
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from reconcile.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
