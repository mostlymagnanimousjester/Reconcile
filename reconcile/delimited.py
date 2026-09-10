"""Encoding, quoted record parse, and delimiter detection for delimited files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from reconcile.errors import HardFail

DELIM_CANDIDATES: tuple[tuple[str, str], ...] = (
    (",", "comma"),
    ("~", "tilde"),
    ("|", "pipe"),
    ("\t", "tab"),
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
    """Quoted parser: `\"` / `\"\"` / newlines in quotes. Returns (start_line, fields)."""
    rows: list[tuple[int, list[str]]] = []
    field_chars: list[str] = []
    row: list[str] = []
    in_quotes = False
    i = 0
    n = len(text)
    line_no = 1
    rec_start = 1
    started = False

    def flush_field() -> None:
        row.append("".join(field_chars))
        field_chars.clear()

    while i < n:
        ch = text[i]
        started = True
        if in_quotes:
            if ch == '"':
                if i + 1 < n and text[i + 1] == '"':
                    field_chars.append('"')
                    i += 2
                    continue
                in_quotes = False
                i += 1
                continue
            if ch == "\r":
                field_chars.append(ch)
                if i + 1 < n and text[i + 1] == "\n":
                    field_chars.append("\n")
                    i += 2
                else:
                    i += 1
                line_no += 1
                continue
            if ch == "\n":
                field_chars.append(ch)
                line_no += 1
                i += 1
                continue
            field_chars.append(ch)
            i += 1
            continue

        if ch == '"':
            in_quotes = True
            i += 1
            continue
        if ch == delimiter:
            flush_field()
            i += 1
            continue
        if ch == "\r" or ch == "\n":
            flush_field()
            rows.append((rec_start, row))
            row = []
            if ch == "\r" and i + 1 < n and text[i + 1] == "\n":
                i += 1
            i += 1
            line_no += 1
            rec_start = line_no
            started = False
            continue
        field_chars.append(ch)
        i += 1

    if in_quotes:
        raise HardFail("Unclosed quote in delimited file")

    if started or field_chars or row:
        flush_field()
        rows.append((rec_start, row))

    return rows


def _plausible(records: list[tuple[int, list[str]]]) -> tuple[bool, int]:
    if not records:
        return False, 0
    header_count = len(records[0][1])
    if header_count < 2:
        return False, header_count
    for _line, fields in records[1:]:
        if len(fields) != header_count:
            return False, header_count
    return True, header_count


def detect_delimiter(text: str, path: str) -> tuple[str, str, list[tuple[int, list[str]]]]:
    """Full-file profile. Exactly one plausible candidate or hard fail."""
    reports: list[str] = []
    hits: list[tuple[str, str, list[tuple[int, list[str]]], int]] = []

    for delim, name in DELIM_CANDIDATES:
        try:
            records = parse_records(text, delim)
        except HardFail as exc:
            raise HardFail(f"{exc.message} in {path}") from exc
        ok, nfields = _plausible(records)
        ragged = None
        if records and nfields >= 2 and not ok:
            for line, fields in records[1:]:
                if len(fields) != nfields:
                    ragged = (line, len(fields), nfields)
                    break
        if ok:
            reports.append(f"{name} plausible=yes ({nfields} fields)")
            hits.append((delim, name, records, nfields))
        else:
            extra = ""
            if records and len(records[0][1]) < 2:
                extra = f" (header fields={len(records[0][1])})"
            elif ragged:
                extra = (
                    f" (ragged row {ragged[0]}: {ragged[1]} fields, "
                    f"header {ragged[2]})"
                )
            reports.append(f"{name} plausible=no{extra}")

    detail = "; ".join(reports)
    if len(hits) == 1:
        delim, name, records, _n = hits[0]
        return delim, name, records
    if len(hits) == 0:
        raise HardFail(
            f"No plausible delimiter for {path}: {detail}. "
            "Exactly one plausible candidate is required."
        )
    raise HardFail(
        f"Ambiguous delimiter for {path}: {detail}. "
        "Exactly one plausible candidate is required."
    )


def drop_all_empty_rows(rows: list[list[str]]) -> list[list[str]]:
    return [row for row in rows if any(field != "" for field in row)]


def load_delimited(path: str | Path) -> ParsedTable:
    path = abs_path(path)
    p = Path(path)
    if not p.is_file():
        raise HardFail(f"Missing path: {path}")
    data = p.read_bytes()
    text, encoding = decode_bytes(data, path)
    delim, delim_name, records = detect_delimiter(text, path)
    if not records:
        raise HardFail(f"No header row in {path}")
    header_line, headers = records[0]
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
