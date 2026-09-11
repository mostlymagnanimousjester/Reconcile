"""Encoding, quoted record parse, and delimiter selection for delimited files."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

from reconcile.errors import HardFail

DELIM_CANDIDATES: tuple[tuple[str, str], ...] = (
    (",", "comma"),
    ("~", "tilde"),
    ("|", "pipe"),
    ("\t", "tab"),
)

DELIM_NAMES: dict[str, str] = {ch: name for ch, name in DELIM_CANDIDATES}
VALID_DELIM_CHARS: frozenset[str] = frozenset(DELIM_NAMES)
SNIFF_DELIMITERS = ",~|\t"

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


@dataclass(frozen=True)
class Detection:
    encoding: str
    delimiter: str
    delimiter_name: str


@dataclass(frozen=True)
class ParsedTable:
    headers: list[str]
    rows: list[list[str]]
    detection: Detection


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


def decode_bytes(data: bytes, path: str) -> tuple[str, str]:
    """UTF-8 BOM, else UTF-8, else Windows-1252. Never latin-1."""
    if data.startswith(b"\xef\xbb\xbf"):
        try:
            return data.decode("utf-8-sig"), "utf-8"
        except UnicodeDecodeError as exc:
            raise HardFail(
                f"Invalid UTF-8 after UTF-8 BOM in {path}: {exc}"
            ) from exc
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        try:
            return data.decode("cp1252"), "windows-1252"
        except UnicodeDecodeError as exc:
            raise HardFail(
                f"Could not decode {path} as UTF-8 or Windows-1252"
            ) from exc


def parse_records(text: str, delimiter: str) -> list[tuple[int, list[str]]]:
    """Quoted parser via stdlib csv: `\"` / `\"\"` / newlines in quotes.

    Returns (1-based record number, fields). Does not skip initial spaces
    (compare is exact raw text).
    """
    reader = csv.reader(
        io.StringIO(text),
        delimiter=delimiter,
        quotechar='"',
        doublequote=True,
        skipinitialspace=False,
        quoting=csv.QUOTE_MINIMAL,
        strict=True,
    )
    rows: list[tuple[int, list[str]]] = []
    try:
        for recno, fields in enumerate(reader, start=1):
            rows.append((recno, fields))
    except csv.Error as exc:
        raise HardFail(f"Unclosed quote in delimited file: {exc}") from exc
    return rows


def extension_default_delimiter(path: str) -> str | None:
    suffix = Path(path).suffix.lower()
    if suffix == ".csv":
        return ","
    if suffix == ".txt":
        return "~"
    return None


def _sniff_sample(text: str, limit: int = 65536) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    nl = cut.rfind("\n")
    return cut if nl < 0 else cut[: nl + 1]


def sniff_delimiter(text: str, path: str) -> str:
    """Use stdlib csv.Sniffer; hard-fail if sniffing is inconclusive."""
    sample = _sniff_sample(text)
    if not sample.strip():
        raise HardFail(
            f"Could not sniff delimiter for {path}: file is empty or whitespace-only"
        )
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=SNIFF_DELIMITERS)
    except csv.Error as exc:
        raise HardFail(f"Could not sniff delimiter for {path}: {exc}") from exc
    delim = dialect.delimiter
    if delim not in VALID_DELIM_CHARS:
        raise HardFail(
            f"Could not sniff delimiter for {path}: "
            f"sniffer returned {delim!r}; expected comma, tilde, pipe, or tab"
        )
    return delim


def choose_delimiter(text: str, path: str, cli_delim: str | None) -> tuple[str, str]:
    """CLI override, else .csv→comma / .txt→tilde, else csv.Sniffer."""
    if cli_delim is not None:
        delim = cli_delim
    else:
        ext = extension_default_delimiter(path)
        if ext is not None:
            delim = ext
        else:
            delim = sniff_delimiter(text, path)
    return delim, delimiter_name(delim)


def drop_all_empty_rows(rows: list[list[str]]) -> list[list[str]]:
    return [row for row in rows if any(field != "" for field in row)]


def load_delimited(path: str | Path, delimiter: str | None = None) -> ParsedTable:
    path = abs_path(path)
    p = Path(path)
    if not p.is_file():
        raise HardFail(f"Missing path: {path}")
    data = p.read_bytes()
    text, encoding = decode_bytes(data, path)
    delim, delim_name = choose_delimiter(text, path, delimiter)
    try:
        records = parse_records(text, delim)
    except HardFail as exc:
        raise HardFail(f"{exc.message} in {path}") from exc
    if not records:
        raise HardFail(f"No header row in {path}")
    _header_line, headers = records[0]
    header_n = len(headers)
    if header_n < 2:
        raise HardFail(
            f"Header in {path} has {header_n} field(s); tables must have ≥ 2 fields"
        )
    data_rows: list[list[str]] = []
    for line, fields in records[1:]:
        if len(fields) != header_n:
            raise HardFail(
                f"Ragged delimited row in {path}: row {line} has "
                f"{len(fields)} fields, header has {header_n} fields"
            )
        data_rows.append(fields)
    data_rows = drop_all_empty_rows(data_rows)
    return ParsedTable(
        headers=headers,
        rows=data_rows,
        detection=Detection(
            encoding=encoding,
            delimiter=delim,
            delimiter_name=delim_name,
        ),
    )
