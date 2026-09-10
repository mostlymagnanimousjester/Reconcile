"""CLI: identity flags, session zip, exit codes."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from reconcile.engine import Engine, InTuiError
from reconcile.errors import HardFail


def parse_keys(raw: str) -> list[str]:
    parts = raw.split(",")
    names = [p.strip() for p in parts]
    if not names or all(n == "" for n in names):
        raise HardFail("--keys must contain at least one non-empty name")
    if any(n == "" for n in names):
        raise HardFail("Empty key name in --keys (empty segment)")
    seen: set[str] = set()
    for n in names:
        if n in seen:
            raise HardFail(f"Duplicate key name in --keys: {n!r}")
        seen.add(n)
    return names


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="Reconcile.py",
        description=(
            "Strict two-sided data reconciliation TUI. "
            "Reads sources only; never writes or opens them."
        ),
    )
    p.add_argument("--a", dest="a", help="Side A file")
    p.add_argument("--b", dest="b", help="Side B file")
    p.add_argument("--a-sheet", dest="a_sheet", help="Sheet name if A is .xlsx/.xlsm")
    p.add_argument("--b-sheet", dest="b_sheet", help="Sheet name if B is .xlsx/.xlsm")
    p.add_argument(
        "--keys",
        dest="keys",
        help="Comma-separated key column names (order kept; no quoting)",
    )
    p.add_argument("--session", dest="session", help="Load a .recon.zip and live-reread sources")
    return p


def engine_from_args(ns: argparse.Namespace) -> Engine:
    identity = any([ns.a, ns.b, ns.a_sheet, ns.b_sheet, ns.keys])
    if ns.session and identity:
        raise HardFail(
            "Mixing --session with --a / --b / --a-sheet / --b-sheet / --keys is not allowed. "
            "The zip is the identity."
        )
    if ns.session:
        return Engine.from_session(ns.session)
    if not ns.a or not ns.b or not ns.keys:
        raise HardFail("Without --session, --a, --b, and --keys are required")
    keys = parse_keys(ns.keys)
    return Engine.from_paths(ns.a, ns.b, keys, ns.a_sheet, ns.b_sheet)


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    try:
        ns = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        return int(code) if isinstance(code, int) else 2
    try:
        engine = engine_from_args(ns)
    except HardFail as exc:
        print(exc.message, file=sys.stderr)
        return 2
    try:
        from reconcile.tui import ReconcileApp

        app = ReconcileApp(engine)
        result = app.run()
        if result is None:
            return 1 if engine.pending_total() else 0
        return int(result)
    except InTuiError as exc:
        print(exc.message, file=sys.stderr)
        return 2
    except HardFail as exc:
        print(exc.message, file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: Textual cannot start: {exc}", file=sys.stderr)
        return 2
