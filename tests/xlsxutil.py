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


def write_xlsx_sheets(path: Path, sheets: dict[str, list[list[str]]]) -> Path:
    """Minimal multi-sheet shared-string xlsx. Insertion order is sheet order."""
    if not sheets:
        raise ValueError("write_xlsx_sheets requires at least one sheet")
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

    def sheet_xml(rows: list[list[str]]) -> str:
        max_r = max(len(rows), 1)
        max_c = max((len(r) for r in rows), default=1)
        by_row: dict[int, list[str]] = {}
        for r, row in enumerate(rows):
            parts = []
            for c, val in enumerate(row):
                ref = f"{col_letter(c)}{r + 1}"
                i = sid(val)
                parts.append(f'<c r="{ref}" t="s"><v>{i}</v></c>')
            by_row[r] = parts
        xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"\n'
            ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">\n'
            f'<dimension ref="A1:{col_letter(max_c - 1)}{max_r}"/>\n'
            "<sheetData>\n"
        )
        for r, parts in by_row.items():
            xml += f'<row r="{r + 1}">' + "".join(parts) + "</row>\n"
        xml += "</sheetData></worksheet>"
        return xml

    names = list(sheets)
    sheet_parts = [sheet_xml(sheets[name]) for name in names]
    sst = "".join(f'<si><t xml:space="preserve">{escape(s)}</t></si>' for s in strings)
    sst_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'uniqueCount="{len(strings)}" count="{len(strings)}">{sst}</sst>'
    )
    sheet_tags = "".join(
        f'<sheet name="{escape(name)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
        for i, name in enumerate(names)
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"\n'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">\n'
        f"<sheets>{sheet_tags}</sheets>\n"
        "</workbook>"
    )
    rel_items = [
        (
            f'<Relationship Id="rId{i + 1}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{i + 1}.xml"/>'
        )
        for i in range(len(names))
    ]
    sst_rid = len(names) + 1
    rel_items.append(
        f'<Relationship Id="rId{sst_rid}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" '
        'Target="sharedStrings.xml"/>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        + "\n".join(rel_items)
        + "\n</Relationships>"
    )
    overrides = [
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    ]
    for i in range(len(names)):
        overrides.append(
            f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
    overrides.append(
        '<Override PartName="/xl/sharedStrings.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
    )
    ctypes = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
        '<Default Extension="xml" ContentType="application/xml"/>\n'
        + "\n".join(overrides)
        + "\n</Types>"
    )
    root_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", ctypes)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", rels)
        for i, xml in enumerate(sheet_parts):
            zf.writestr(f"xl/worksheets/sheet{i + 1}.xml", xml)
        zf.writestr("xl/sharedStrings.xml", sst_xml)
    return path
