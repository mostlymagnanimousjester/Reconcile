"""Shared CSV / xlsx fixture helpers."""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape


def write_csv(path: Path, text: str, encoding: str = "utf-8") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode(encoding))
    return path


def write_xlsx(
    path: Path,
    sheet: str,
    rows: list[list[str]],
    *,
    number_cells: set[tuple[int, int]] | None = None,
    formula_cells: set[tuple[int, int]] | None = None,
    merges: list[str] | None = None,
    bool_cells: set[tuple[int, int]] | None = None,
) -> Path:
    """Minimal shared-string xlsx. rows are 0-based including header."""
    number_cells = number_cells or set()
    formula_cells = formula_cells or set()
    bool_cells = bool_cells or set()
    path.parent.mkdir(parents=True, exist_ok=True)
    strings: list[str] = []
    index: dict[str, int] = {}

    def sid(s: str) -> int:
        if s not in index:
            index[s] = len(strings)
            strings.append(s)
        return index[s]

    def col_letter(i: int) -> str:
        n = i + 1
        out = ""
        while n:
            n, rem = divmod(n - 1, 26)
            out = chr(65 + rem) + out
        return out

    sheet_cells = []
    max_r = len(rows)
    max_c = max((len(r) for r in rows), default=1)
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            ref = f"{col_letter(c)}{r + 1}"
            if (r, c) in formula_cells:
                sheet_cells.append(f'<c r="{ref}" t="str"><f>A1</f><v>{escape(val)}</v></c>')
            elif (r, c) in number_cells:
                sheet_cells.append(f'<c r="{ref}"><v>{escape(val)}</v></c>')
            elif (r, c) in bool_cells:
                sheet_cells.append(f'<c r="{ref}" t="b"><v>{escape(val)}</v></c>')
            else:
                i = sid(val)
                sheet_cells.append(f'<c r="{ref}" t="s"><v>{i}</v></c>')
    merge_xml = ""
    if merges:
        inner = "".join(f'<mergeCell ref="{m}"/>' for m in merges)
        merge_xml = f'<mergeCells count="{len(merges)}">{inner}</mergeCells>'
    sheet_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<dimension ref="A1:{col_letter(max_c - 1)}{max_r}"/>
<sheetData>
"""
    # regroup cells by row
    by_row: dict[int, list[str]] = {}
    idx = 0
    for r, row in enumerate(rows):
        parts = []
        for c, _val in enumerate(row):
            parts.append(sheet_cells[idx])
            idx += 1
        by_row[r] = parts
    for r, parts in by_row.items():
        sheet_xml += f'<row r="{r + 1}">' + "".join(parts) + "</row>\n"
    sheet_xml += f"</sheetData>{merge_xml}</worksheet>"

    sst = "".join(f'<si><t xml:space="preserve">{escape(s)}</t></si>' for s in strings)
    sst_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" uniqueCount="{len(strings)}" count="{len(strings)}">{sst}</sst>"""
    workbook = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="{escape(sheet)}" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
</Relationships>"""
    ctypes = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
</Types>"""
    root_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", ctypes)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        zf.writestr("xl/sharedStrings.xml", sst_xml)
    return path
