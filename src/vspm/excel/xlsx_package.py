"""Minimal, surgical editing of an .xlsx package.

Why not openpyxl for writing: the production workbook contains form controls
(ctrlProps), drawings, VML, printer settings, an external link to ΑΠΟΘΗΚΗ.xlsx
and extension lists. openpyxl drops several of these when it saves. Here we
change only:
  * the target worksheet XML (the appended rows and the <dimension> ref),
  * styles.xml (only if a dd/mm/yyyy date style must be added),
  * workbook.xml calcPr (optional fullCalcOnLoad so Excel recalculates).
Every other part of the zip is copied unchanged (same uncompressed bytes,
same order, same timestamps).
"""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from lxml import etree

from vspm.excel.formulas import FALLBACK_FORMULAS, row_pattern, translate_to_row

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RNS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_RNS = "http://schemas.openxmlformats.org/package/2006/relationships"
XML_NS = "http://www.w3.org/XML/1998/namespace"
N = {"m": NS}

EXCEL_EPOCH = date(1899, 12, 30)


class XlsxError(Exception):
    pass


def col_letter(idx: int) -> str:
    s = ""
    while idx:
        idx, r = divmod(idx - 1, 26)
        s = chr(65 + r) + s
    return s


def col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n


def split_ref(ref: str) -> tuple[str, int]:
    m = re.fullmatch(r"([A-Z]+)(\d+)", ref)
    if not m:
        raise XlsxError(f"Bad cell ref {ref}")
    return m.group(1), int(m.group(2))


def date_to_serial(d: date) -> int:
    return (d - EXCEL_EPOCH).days


def _q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


@dataclass
class CellValue:
    kind: str  # empty | str | num | bool | formula | error
    value: Any = None
    formula: Optional[str] = None
    style: Optional[str] = None


class XlsxPackage:
    def __init__(self, data: bytes):
        self.original = data
        try:
            self.zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as e:
            raise XlsxError(f"Not a valid .xlsx file: {e}") from e
        self.infos = self.zf.infolist()
        self.modified: dict[str, bytes] = {}
        self._trees: dict[str, etree._Element] = {}
        self._shared: Optional[list[str]] = None

    # ---- part access -----------------------------------------------------------
    def read(self, name: str) -> bytes:
        if name in self.modified:
            return self.modified[name]
        return self.zf.read(name)

    def tree(self, name: str) -> etree._Element:
        if name not in self._trees:
            parser = etree.XMLParser(huge_tree=True, remove_blank_text=False)
            self._trees[name] = etree.fromstring(self.read(name), parser)
        return self._trees[name]

    def mark_dirty(self, name: str) -> None:
        self.modified[name] = b""  # serialized in save()

    def sheet_names(self) -> list[str]:
        wb = self.tree("xl/workbook.xml")
        return [s.get("name") for s in wb.findall("m:sheets/m:sheet", N)]

    def sheet_part(self, sheet_name: str) -> str:
        wb = self.tree("xl/workbook.xml")
        rid = None
        for s in wb.findall("m:sheets/m:sheet", N):
            if s.get("name") == sheet_name:
                rid = s.get(f"{{{RNS}}}id")
        if rid is None:
            raise XlsxError(f"Worksheet '{sheet_name}' not found. Sheets: {self.sheet_names()}")
        rels = etree.fromstring(self.read("xl/_rels/workbook.xml.rels"))
        for r in rels:
            if r.get("Id") == rid:
                target = r.get("Target")
                return target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
        raise XlsxError(f"Relationship {rid} for sheet '{sheet_name}' not found")

    def shared_strings(self) -> list[str]:
        if self._shared is None:
            self._shared = []
            if "xl/sharedStrings.xml" in self.zf.namelist():
                sst = etree.fromstring(self.zf.read("xl/sharedStrings.xml"), etree.XMLParser(huge_tree=True))
                for si in sst.findall("m:si", N):
                    self._shared.append("".join(t.text or "" for t in si.iter(_q("t"))
                                                if t.getparent().tag != _q("rPh")))
        return self._shared

    # ---- reading cells ---------------------------------------------------------
    def read_cell(self, c: etree._Element) -> CellValue:
        t = c.get("t", "n")
        f = c.find("m:f", N)
        v = c.find("m:v", N)
        style = c.get("s")
        formula = f.text if f is not None and f.text else None
        if f is not None:
            return CellValue("formula", v.text if v is not None else None, formula, style)
        if t == "inlineStr":
            is_ = c.find("m:is", N)
            txt = "".join(x.text or "" for x in is_.iter(_q("t"))) if is_ is not None else ""
            return CellValue("str", txt, None, style)
        if v is None or v.text is None:
            return CellValue("empty", None, None, style)
        if t == "s":
            return CellValue("str", self.shared_strings()[int(v.text)], None, style)
        if t in ("str",):
            return CellValue("str", v.text, None, style)
        if t == "b":
            return CellValue("bool", v.text == "1", None, style)
        if t == "e":
            return CellValue("error", v.text, None, style)
        return CellValue("num", float(v.text) if re.search(r"[.eE]", v.text) else int(v.text), None, style)


class SheetEditor:
    """Append rows to one worksheet of an XlsxPackage."""

    def __init__(self, pkg: XlsxPackage, sheet_name: str):
        self.pkg = pkg
        self.sheet_name = sheet_name
        self.part = pkg.sheet_part(sheet_name)
        self.root = pkg.tree(self.part)
        self.sheet_data = self.root.find("m:sheetData", N)
        if self.sheet_data is None:
            raise XlsxError("Worksheet has no sheetData")
        self._date_style_cache: dict[tuple[str, str], str] = {}

    # ---- lookup --------------------------------------------------------------------
    def rows(self) -> dict[int, etree._Element]:
        return {int(r.get("r")): r for r in self.sheet_data.findall("m:row", N)}

    def cell(self, row_el: etree._Element, col: str) -> Optional[etree._Element]:
        ref = f"{col}{row_el.get('r')}"
        for c in row_el.findall("m:c", N):
            if c.get("r") == ref:
                return c
        return None

    def value(self, row: int, col: str) -> CellValue:
        r = self.rows().get(row)
        if r is None:
            return CellValue("empty")
        c = self.cell(r, col)
        return self.pkg.read_cell(c) if c is not None else CellValue("empty")

    @staticmethod
    def _has_value(cv: CellValue) -> bool:
        if cv.kind == "empty":
            return False
        if cv.kind == "str":
            return bool(str(cv.value).strip())
        return True

    def last_data_row(self, cols: str = "ABCDEFGHIJK") -> int:
        last = 1
        for rnum, r in self.rows().items():
            for c in r.findall("m:c", N):
                col, _ = split_ref(c.get("r"))
                if col in cols and self._has_value(self.pkg.read_cell(c)):
                    last = max(last, rnum)
                    break
        return last

    def column_values(self, col: str) -> dict[int, Any]:
        out = {}
        for rnum, r in self.rows().items():
            c = self.cell(r, col)
            if c is not None:
                cv = self.pkg.read_cell(c)
                if self._has_value(cv):
                    out[rnum] = cv.value
        return out

    def find_rows_with_value(self, col: str, value: str) -> list[int]:
        target = str(value).strip().upper()
        return [r for r, v in self.column_values(col).items() if str(v).strip().upper() == target]

    def formula_template(self, col: str, before_row: int) -> tuple[str, int]:
        """Nearest formula in `col` above `before_row` -> (formula, its row)."""
        rows = self.rows()
        for rnum in sorted((r for r in rows if r < before_row), reverse=True):
            c = self.cell(rows[rnum], col)
            if c is None:
                continue
            f = c.find("m:f", N)
            if f is not None and f.text and f.get("t") != "shared":
                return f.text, rnum
            if f is not None and f.get("t") == "shared" and f.text and f.get("ref"):
                return f.text, rnum
        if col in FALLBACK_FORMULAS:
            return FALLBACK_FORMULAS[col].replace("{r}", "1"), 1
        raise XlsxError(f"No formula found in column {col} to use as template")

    def formula_for_row(self, col: str, target_row: int) -> str:
        f, src = self.formula_template(col, target_row)
        return translate_to_row(f, src, target_row)

    # ---- writing ---------------------------------------------------------------------
    def _get_or_create_row(self, rnum: int, template_row: Optional[etree._Element]) -> etree._Element:
        rows = self.rows()
        tattrs = {k: v for k, v in (template_row.attrib.items() if template_row is not None else [])
                  if k not in ("r", "hidden", "collapsed", "outlineLevel")}
        if rnum in rows:
            row = rows[rnum]
        else:
            row = etree.Element(_q("row"))
            row.set("r", str(rnum))
            for k, v in tattrs.items():
                row.set(k, v)
            nxt = next((rows[k] for k in sorted(rows) if k > rnum), None)
            if nxt is not None:
                nxt.addprevious(row)
            else:
                self.sheet_data.append(row)
        # A new production row must be visible. The sheet uses zeroHeight="1",
        # so rows without an explicit height are hidden by default.
        row.attrib.pop("hidden", None)
        if row.get("ht") in (None, "0", "0.0"):
            row.set("ht", tattrs.get("ht", "15"))
            if "customHeight" in tattrs:
                row.set("customHeight", tattrs["customHeight"])
            else:
                row.attrib.pop("customHeight", None)
        return row

    def _get_or_create_cell(self, row: etree._Element, col: str, style: Optional[str]) -> etree._Element:
        ref = f"{col}{row.get('r')}"
        existing = self.cell(row, col)
        if existing is not None:
            if existing.get("s") is None and style is not None:
                existing.set("s", style)
            return existing
        c = etree.Element(_q("c"))
        c.set("r", ref)
        if style is not None:
            c.set("s", style)
        idx = col_index(col)
        after = None
        for other in row.findall("m:c", N):
            ocol, _ = split_ref(other.get("r"))
            if col_index(ocol) < idx:
                after = other
        if after is not None:
            after.addnext(c)
        else:
            row.insert(0, c)
        return c

    @staticmethod
    def _clear(c: etree._Element) -> None:
        for child in list(c):
            c.remove(child)
        c.attrib.pop("t", None)

    def set_string(self, c: etree._Element, text: str) -> None:
        self._clear(c)
        c.set("t", "inlineStr")
        is_ = etree.SubElement(c, _q("is"))
        t = etree.SubElement(is_, _q("t"))
        t.text = text
        if text != text.strip():
            t.set(f"{{{XML_NS}}}space", "preserve")

    def set_number(self, c: etree._Element, num: int | float) -> None:
        self._clear(c)
        v = etree.SubElement(c, _q("v"))
        v.text = str(int(num)) if float(num).is_integer() else repr(float(num))

    def set_formula(self, c: etree._Element, formula: str, string_result: bool) -> None:
        self._clear(c)
        if string_result:
            c.set("t", "str")
        f = etree.SubElement(c, _q("f"))
        f.text = formula.lstrip("=")

    def write_row(self, rnum: int, values: dict[str, Any], formulas: dict[str, str],
                  date_cols: dict[str, date], date_format: str, template_rnum: int) -> None:
        rows = self.rows()
        template_row = rows.get(template_rnum)
        row = self._get_or_create_row(rnum, template_row)
        for col in sorted(set(values) | set(formulas) | set(date_cols), key=col_index):
            tc = self.cell(template_row, col) if template_row is not None else None
            style = tc.get("s") if tc is not None else None
            c = self._get_or_create_cell(row, col, style)
            if col in formulas:
                tf = tc.find("m:f", N) if tc is not None else None
                string_result = tc is not None and tc.get("t") == "str" and tf is not None
                self.set_formula(c, formulas[col], string_result)
            elif col in date_cols:
                self.set_number(c, date_to_serial(date_cols[col]))
                c.set("s", self.date_style(c.get("s") or "0", date_format))
            else:
                v = values[col]
                if v is None or (isinstance(v, str) and v == ""):
                    self._clear(c)
                elif isinstance(v, (int, float)) and not isinstance(v, bool):
                    self.set_number(c, v)
                else:
                    self.set_string(c, str(v))
        self._update_dimension(rnum)
        self.pkg.mark_dirty(self.part)

    def _update_dimension(self, rnum: int) -> None:
        dim = self.root.find("m:dimension", N)
        if dim is None:
            return
        ref = dim.get("ref", "A1")
        m = re.fullmatch(r"([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?", ref)
        if not m:
            return
        c1, r1, c2, r2 = m.group(1), int(m.group(2)), m.group(3) or m.group(1), int(m.group(4) or m.group(2))
        if rnum > r2:
            dim.set("ref", f"{c1}{r1}:{c2}{rnum}")

    # ---- styles -------------------------------------------------------------------------
    def date_style(self, base_style: str, fmt: str) -> str:
        key = (base_style, fmt)
        if key in self._date_style_cache:
            return self._date_style_cache[key]
        code = fmt if "\\/" in fmt else fmt.replace("/", "\\/")  # literal '/' regardless of locale
        styles = self.pkg.tree("xl/styles.xml")
        numfmts = styles.find("m:numFmts", N)
        if numfmts is None:
            numfmts = etree.Element(_q("numFmts"))
            numfmts.set("count", "0")
            styles.insert(0, numfmts)
        fmt_id = None
        for nf in numfmts.findall("m:numFmt", N):
            if nf.get("formatCode", "").lower() == code.lower():
                fmt_id = nf.get("numFmtId")
        if fmt_id is None:
            fmt_id = str(max([163] + [int(nf.get("numFmtId")) for nf in numfmts.findall("m:numFmt", N)]) + 1)
            nf = etree.SubElement(numfmts, _q("numFmt"))
            nf.set("numFmtId", fmt_id)
            nf.set("formatCode", code)
            numfmts.set("count", str(len(numfmts.findall("m:numFmt", N))))
        xfs = styles.find("m:cellXfs", N)
        all_xf = xfs.findall("m:xf", N)
        base = all_xf[int(base_style)]
        if base.get("numFmtId") == fmt_id:
            result = base_style
        else:
            clone = etree.fromstring(etree.tostring(base))
            clone.set("numFmtId", fmt_id)
            clone.set("applyNumberFormat", "1")
            canon = etree.tostring(clone)
            result = None
            for i, xf in enumerate(all_xf):
                if etree.tostring(xf) == canon:
                    result = str(i)
                    break
            if result is None:
                xfs.append(clone)
                xfs.set("count", str(len(all_xf) + 1))
                result = str(len(all_xf))
            self.pkg.mark_dirty("xl/styles.xml")
        self._date_style_cache[key] = result
        return result


def set_full_calc_on_load(pkg: XlsxPackage) -> None:
    wb = pkg.tree("xl/workbook.xml")
    calc = wb.find("m:calcPr", N)
    if calc is None:
        calc = etree.SubElement(wb, _q("calcPr"))
    if calc.get("fullCalcOnLoad") != "1":
        calc.set("fullCalcOnLoad", "1")
        pkg.mark_dirty("xl/workbook.xml")


def save_package(pkg: XlsxPackage) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for info in pkg.infos:
            if info.filename in pkg.modified:
                data = etree.tostring(pkg.tree(info.filename), xml_declaration=True, encoding="UTF-8",
                                      standalone=True)
            else:
                data = pkg.zf.read(info.filename)
            zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            zi.compress_type = info.compress_type
            zi.external_attr = info.external_attr
            zi.create_system = info.create_system
            z.writestr(zi, data)
    return out.getvalue()


__all__ = [
    "XlsxPackage", "SheetEditor", "XlsxError", "CellValue", "save_package", "set_full_calc_on_load",
    "date_to_serial", "row_pattern", "col_letter", "col_index",
]
