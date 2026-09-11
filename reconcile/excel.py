"""Excel load via Polars + fastexcel, with XML checks for types/merges/password."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import fastexcel
import polars as pl

from reconcile.delimited import abs_path, stringify_and_drop_empty_rows
from reconcile.errors import HardFail

_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}

_CELL_REF = re.compile(r"^([A-Z]+)(\d+)$")


def _col_letters_to_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _parse_cell_ref(ref: str) -> tuple[int, int] | None:
    m = _CELL_REF.match(ref.upper())
    if not m:
        return None
    return int(m.group(2)) - 1, _col_letters_to_index(m.group(1))


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _is_ole(data: bytes) -> bool:
    return data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _sheet_map(zf: zipfile.ZipFile) -> dict[str, str]:
    try:
        wb = zf.read("xl/workbook.xml")
        rels = zf.read("xl/_rels/workbook.xml.rels")
    except KeyError as exc:
        raise HardFail("Workbook is missing xl/workbook.xml (not a usable xlsx/xlsm)") from exc
    wb_root = ET.fromstring(wb)
    rel_root = ET.fromstring(rels)
    rid_to_target: dict[str, str] = {}
    for rel in rel_root:
        rid = rel.attrib.get("Id")
        target = rel.attrib.get("Target")
        if rid and target:
            target = target.replace("\\", "/")
            if not target.startswith("xl/"):
                target = "xl/" + target.lstrip("/")
            rid_to_target[rid] = target
    out: dict[str, str] = {}
    for el in wb_root.iter():
        if _local(el.tag) != "sheet":
            continue
        name = el.attrib.get("name")
        rid = el.attrib.get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        ) or el.attrib.get("id")
        if name and rid and rid in rid_to_target:
            out[name] = rid_to_target[rid]
    return out


def _cell_type_label(cell: ET.Element) -> str | None:
    """Return None if the cell is blank-or-text; otherwise a type name to fail on."""
    t = cell.attrib.get("t")
    has_formula = any(_local(ch.tag) == "f" for ch in list(cell))
    if has_formula:
        return "formula"
    children_local = [_local(ch.tag) for ch in list(cell)]
    has_v = "v" in children_local
    has_is = "is" in children_local
    if not has_v and not has_is:
        return None
    if t in (None, "n"):
        return "number"
    if t == "b":
        return "bool"
    if t == "d":
        return "date"
    if t == "e":
        return "error"
    if t == "str":
        return "cached formula result"
    if t in ("s", "inlineStr"):
        return None
    return f"type {t!r}"


def _cell_text(cell: ET.Element, shared: list[str]) -> str:
    t = cell.attrib.get("t")
    v_el = None
    is_el = None
    for ch in list(cell):
        loc = _local(ch.tag)
        if loc == "v":
            v_el = ch
        elif loc == "is":
            is_el = ch
    if t == "s" and v_el is not None and v_el.text:
        try:
            return shared[int(v_el.text)]
        except (ValueError, IndexError):
            return v_el.text or ""
    if t == "inlineStr" and is_el is not None:
        return "".join(tnode.text or "" for tnode in is_el.iter() if _local(tnode.tag) == "t")
    if v_el is not None:
        return v_el.text or ""
    return ""


def _inspect_sheet_xml(path: str, sheet_name: str, xml_bytes: bytes, shared: list[str]) -> None:
    root = ET.fromstring(xml_bytes)
    headers: dict[int, str] = {}
    cells: list[ET.Element] = []
    for el in root.iter():
        loc = _local(el.tag)
        if loc == "mergeCell" or (
            loc == "mergeCells" and any(_local(ch.tag) == "mergeCell" for ch in list(el))
        ):
            if loc == "mergeCell" or any(_local(ch.tag) == "mergeCell" for ch in list(el)):
                raise HardFail(f"Merged cells in {path} sheet {sheet_name!r}")
        if loc == "c":
            cells.append(el)
            parsed = _parse_cell_ref(el.attrib.get("r", ""))
            if parsed and parsed[0] == 0:
                headers[parsed[1]] = _cell_text(el, shared)
    for el in cells:
        bad = _cell_type_label(el)
        if bad is None:
            continue
        ref = el.attrib.get("r", "") or "(unknown)"
        parsed = _parse_cell_ref(ref) if ref != "(unknown)" else None
        if parsed is not None and parsed[1] in headers:
            col_header = repr(headers[parsed[1]])
        elif parsed is not None:
            col_header = f"column index {parsed[1] + 1}"
        else:
            col_header = "(unknown)"
        raise HardFail(
            f"Non-text Excel cell in {path} sheet {sheet_name!r} "
            f"column {col_header} type {bad} (cell {ref})"
        )


def _validate_xlsx(path: str, sheet_name: str) -> None:
    p = Path(path)
    raw = p.read_bytes()
    if _is_ole(raw):
        raise HardFail(
            f"Password-protected or OLE Excel workbook: {path}"
        )
    try:
        zf = zipfile.ZipFile(p)
    except zipfile.BadZipFile as exc:
        raise HardFail(f"Not a valid xlsx/xlsm zip: {path}") from exc
    with zf:
        names = set(zf.namelist())
        if "EncryptionInfo" in names or "EncryptedPackage" in names:
            raise HardFail(f"Password-protected workbook: {path}")
        sheets = _sheet_map(zf)
        if sheet_name not in sheets:
            available = ", ".join(repr(n) for n in sheets)
            raise HardFail(
                f"Missing sheet {sheet_name!r} in {path}. Available: {available}"
            )
        xml_path = sheets[sheet_name]
        try:
            xml_bytes = zf.read(xml_path)
        except KeyError as exc:
            raise HardFail(
                f"Missing sheet part {xml_path} for {sheet_name!r} in {path}"
            ) from exc
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            sst = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in sst.iter():
                if _local(si.tag) == "si":
                    shared.append(
                        "".join(t.text or "" for t in si.iter() if _local(t.tag) == "t")
                    )
        _inspect_sheet_xml(path, sheet_name, xml_bytes, shared)


def load_excel(path: str | Path, sheet_name: str) -> pl.DataFrame:
    """Load an Excel sheet as a Utf8 Polars frame. No Python list dump."""
    path = abs_path(path)
    p = Path(path)
    if not p.is_file():
        raise HardFail(f"Missing path: {path}")
    if not sheet_name:
        raise HardFail(f"Sheet name required for Excel file {path}")
    _validate_xlsx(path, sheet_name)
    try:
        reader = fastexcel.read_excel(path)
    except Exception as exc:
        msg = str(exc).lower()
        if "password" in msg:
            raise HardFail(f"Password-protected workbook: {path}") from exc
        raise HardFail(f"Failed to open Excel workbook {path}: {exc}") from exc
    if sheet_name not in reader.sheet_names:
        available = ", ".join(repr(n) for n in reader.sheet_names)
        raise HardFail(
            f"Missing sheet {sheet_name!r} in {path}. Available: {available}"
        )
    try:
        sheet = reader.load_sheet(
            sheet_name,
            header_row=0,
            schema_sample_rows=None,
            dtype_coercion="strict",
        )
        cols = sheet.available_columns()
        for col in cols:
            dtype = getattr(col, "dtype", None)
            if dtype not in (None, "string", "null"):
                raise HardFail(
                    f"Non-text Excel cell in {path} sheet {sheet_name!r} "
                    f"column {col.name!r} type {dtype}"
                )
        df = sheet.to_polars()
    except HardFail:
        raise
    except Exception as exc:
        raise HardFail(
            f"Failed to read Excel sheet {sheet_name!r} in {path}: {exc}"
        ) from exc

    for c in df.columns:
        s = df[c]
        if s.dtype != pl.Utf8 and s.dtype != pl.String:
            if str(s.dtype) not in ("Null", "NullType", "String", "Utf8"):
                raise HardFail(
                    f"Non-text Excel cell in {path} sheet {sheet_name!r} "
                    f"column {c!r} type {s.dtype}"
                )
    return stringify_and_drop_empty_rows(df)
