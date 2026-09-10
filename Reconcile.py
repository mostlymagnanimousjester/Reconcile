#!/usr/bin/env python3
"""Launch the strict data reconciliation TUI.

User-facing command (PowerShell / POSIX):

    python Reconcile.py --a PATH --b PATH --keys id,year
    python Reconcile.py --session PATH
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
