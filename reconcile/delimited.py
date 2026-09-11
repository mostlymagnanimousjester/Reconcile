"""Delimiter/encoding tokens and Polars CSV ingest for delimited files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import polars as pl

from reconcile.errors import HardFail

DELIM_CANDIDATES: tuple[tuple[str, str], ...] = (
    (",", "comma"),
    ("~", "tilde"),
    ("|", "pipe"),
    ("\t", "tab"),
)

DELIM_NAMES: dict[str, str] = {ch: name for ch, name in DELIM_CANDIDATES}

# CLI names (case-insensitive) and literal / escape forms → delimiter character.
_DELIM_ALIASES: dict[str, str] = {
    "comma": ",",
    ",": ",",
    "tilde": "~",
    "~": "~",
    "pipe": "|",
    "|": "|",
    "tab": "\t",
    "\t": "\t",
    "\\t": "\t",
}

VALID_DELIM_HELP = (
    "comma, tilde, pipe, tab, or the literal character `,` `~` `|` / tab / \\t"
)

# Polars-native encodings. Lossy variants are explicit opt-in, never the default.
DEFAULT_ENCODING = "utf8"
VALID_ENCODINGS: tuple[str, ...] = (
    "utf8",
    "windows-1252",
    "utf8-lossy",
    "windows-1252-lossy",
)
VALID_ENCODING_HELP = ", ".join(VALID_ENCODINGS)


@dataclass(frozen=True)
class Detection:
    encoding: str
    delimiter: str
    delimiter_name: str


@dataclass(frozen=True)
class ParsedTable:
    frame: pl.DataFrame
    detection: Detection

    @property
    def headers(self) -> list[str]:
        return list(self.frame.columns)


def abs_path(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())


def delimiter_name(delim: str) -> str:
    return DELIM_NAMES.get(delim, delim)


def parse_delimiter(raw: str, flag: str) -> str:
    """Map a CLI delimiter token to a single character, or hard-fail."""
    if raw == "\t":
        return "\t"
    key = raw.strip()
    mapped = _DELIM_ALIASES.get(key)
    if mapped is None and key != key.lower():
        mapped = _DELIM_ALIASES.get(key.lower())
    if mapped is None:
        raise HardFail(
            f"Unknown delimiter for {flag}: {raw!r}. Valid values: {VALID_DELIM_HELP}."
        )
    return mapped


def parse_encoding(raw: str, flag: str) -> str:
    """Map a CLI encoding token to a Polars encoding, or hard-fail."""
    key = raw.strip().lower()
    if key not in VALID_ENCODINGS:
        raise HardFail(
            f"Unknown encoding for {flag}: {raw!r}. Valid values: {VALID_ENCODING_HELP}."
        )
    return key


def stringify_and_drop_empty_rows(df: pl.DataFrame) -> pl.DataFrame:
    """Cast every column to Utf8, null→`""`, drop rows that are all empty."""
    if not df.columns:
        raise HardFail("Header row is required")
    df = df.with_columns(pl.all().cast(pl.Utf8).fill_null(""))
    return df.filter(~pl.all_horizontal(pl.all() == ""))


def load_delimited(
    path: str | Path,
    delimiter: str,
    *,
    encoding: str = DEFAULT_ENCODING,
    side: str = "A",
) -> ParsedTable:
    """Read a delimited file with Polars `read_csv`. Delimiter is required."""
    path = abs_path(path)
    p = Path(path)
    if not p.is_file():
        raise HardFail(f"Missing path: {path}")
    encoding = parse_encoding(encoding, f"--{'a' if side == 'A' else 'b'}-encoding")
    try:
        df = pl.read_csv(
            path,
            infer_schema=False,
            empty_string_is_null=False,
            quote_char='"',
            has_header=True,
            separator=delimiter,
            encoding=encoding,
            glob=False,
        )
    except Exception as exc:
        raise HardFail(
            f"Failed to parse delimited file {path} (side {side}): {exc}"
        ) from exc
    df = stringify_and_drop_empty_rows(df)
    return ParsedTable(
        frame=df,
        detection=Detection(
            encoding=encoding,
            delimiter=delimiter,
            delimiter_name=delimiter_name(delimiter),
        ),
    )
