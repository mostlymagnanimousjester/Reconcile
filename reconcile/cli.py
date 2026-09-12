"""CLI: identity flags, session zip, exit codes."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from reconcile.delimited import (
    VALID_DELIM_HELP,
    VALID_ENCODING_HELP,
    parse_delimiter,
    parse_encoding,
)
from reconcile.engine import Engine, InTuiError, Place
from reconcile.errors import HardFail

IDENTITY_FLAGS = (
    "--a / --b / --a-sheet / --b-sheet / --keys / "
    "--a-delim / --b-delim / --a-encoding / --b-encoding"
)


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
    p.add_argument(
        "--a-delim",
        dest="a_delim",
        help=(
            "Delimiter for side A if it is a delimited file "
            f"({VALID_DELIM_HELP}). Defaults to comma when A is a .csv file. "
            "Required for other delimited extensions. Hard fail if A is Excel."
        ),
    )
    p.add_argument(
        "--b-delim",
        dest="b_delim",
        help=(
            "Delimiter for side B if it is a delimited file "
            f"({VALID_DELIM_HELP}). Defaults to comma when B is a .csv file. "
            "Required for other delimited extensions. Hard fail if B is Excel."
        ),
    )
    p.add_argument(
        "--a-encoding",
        dest="a_encoding",
        help=(
            "Encoding for side A delimited file "
            f"({VALID_ENCODING_HELP}; default utf8). Hard fail if A is Excel."
        ),
    )
    p.add_argument(
        "--b-encoding",
        dest="b_encoding",
        help=(
            "Encoding for side B delimited file "
            f"({VALID_ENCODING_HELP}; default utf8). Hard fail if B is Excel."
        ),
    )
    p.add_argument("--session", dest="session", help="Load a .recon.zip and live-reread sources")
    return p


def engine_from_args(ns: argparse.Namespace) -> tuple[Engine, Place]:
    identity = any(
        [
            ns.a,
            ns.b,
            ns.a_sheet,
            ns.b_sheet,
            ns.keys,
            ns.a_delim,
            ns.b_delim,
            ns.a_encoding,
            ns.b_encoding,
        ]
    )
    if ns.session and identity:
        raise HardFail(
            f"Mixing --session with {IDENTITY_FLAGS} is not allowed. "
            "The zip is the identity."
        )
    if ns.session:
        return Engine.from_session(ns.session)
    if not ns.a or not ns.b or not ns.keys:
        raise HardFail("Without --session, --a, --b, and --keys are required")
    keys = parse_keys(ns.keys)
    a_delim = parse_delimiter(ns.a_delim, "--a-delim") if ns.a_delim is not None else None
    b_delim = parse_delimiter(ns.b_delim, "--b-delim") if ns.b_delim is not None else None
    a_encoding = (
        parse_encoding(ns.a_encoding, "--a-encoding")
        if ns.a_encoding is not None
        else None
    )
    b_encoding = (
        parse_encoding(ns.b_encoding, "--b-encoding")
        if ns.b_encoding is not None
        else None
    )
    return Engine.from_paths(
        ns.a,
        ns.b,
        keys,
        ns.a_sheet,
        ns.b_sheet,
        a_delim=a_delim,
        b_delim=b_delim,
        a_encoding=a_encoding,
        b_encoding=b_encoding,
    ), Place()


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
        engine, place = engine_from_args(ns)
    except HardFail as exc:
        print(exc.message, file=sys.stderr)
        return 2
    try:
        from reconcile.tui import ReconcileApp

        app = ReconcileApp(engine, place)
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
