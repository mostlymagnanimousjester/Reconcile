"""Hard-fail errors. Messages must include raw identifiers (§16)."""

from __future__ import annotations


class HardFail(Exception):
    """Fatal load/parse/schema error. CLI maps this to stderr + exit 2."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def format_key_tuple(key: tuple[str, ...]) -> str:
    """Render a key tuple as exact strings for error text."""
    return repr(key)
