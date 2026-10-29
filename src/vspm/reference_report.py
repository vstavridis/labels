"""Generate a technical report about the reference workbook and DYMO templates."""

from __future__ import annotations

import collections
import re
from pathlib import Path
from typing import Optional

from vspm.dymo.template import LabelTemplate, TemplateRegistry
from vspm.excel.formulas import row_pattern
from vspm.excel.xlsx_package import N, SheetEditor, XlsxPackage

ONEDRIVE_ID = re.compile(r"(d\.docs\.live\.net/)[0-9a-f]{8,}", re.I)


def redact(text: str) -> str:
    text = ONEDRIVE_ID.sub(r"\1<onedrive-id>", text)
    text = re.sub(r'(driveId|itemId)="[^"]+"', r'\1="<redacted>"', text)
    return text


def _customer_redact(s: str) -> str:
    return re.sub(r"((?:ΠΕΛΑΤΗΣ|CUSTOMER)\s*:\s*).+", r"\1<customer>", s)


def template_section(tpl: LabelTemplate, title: str, redact_customers: bool) -> list[str]:
    info = tpl.inspect()
    out = [f"### {title}", "",
           f"* File: `{info['file']}` — paper `{info['paper']}`, orientation {info['orientation']}, "
           f"layout {info['size_twips'][0]:.0f} × {info['size_twips'][1]:.0f} twips "
           f"({info['size_mm'][0]} × {info['size_mm'][1]} mm)", ""]
    out += ["| Object | Type | Bounds X,Y,W,H (twips) | Details |", "|---|---|---|---|"]
    for o in info["objects"]:
        b = o["bounds"]
        bounds = f"{b.get('X', 0):.0f}, {b.get('Y', 0):.0f}, {b.get('Width', 0):.0f}, {b.get('Height', 0):.0f}"
        det = ""
        if "runs" in o:
            runs = []
            for r in o["runs"]:
                t = (r["text"] or "").replace("\n", "⏎")
                t = _customer_redact(t) if redact_customers else t
                if redact_customers and o["type"] == "AddressObject":
                    t = "<company name>"
                runs.append(f"`{t}` ({r['font']})")
            det = f"{o['fit']}, {o['align']}; runs: " + " · ".join(runs)
        elif o["type"] == "BarcodeObject":
            det = (f"{o['symbology']}, size {o['size']}, text {o['text_position']}, sample `{o['text']}`, "
                   f"linked to `{o['linked_object'] or '-'}`, variable={o['is_variable']}")
        elif o["type"] == "CounterObject":
            det = (f"prefix `{o['prefix']}`, start {o['start']}, **current {o['current']}**, increment "
                   f"{o['increment']}, width {o['format_width']}, leading zeros {o['leading_zeros']}")
        elif o["type"] == "DateTimeObject":
            det = f"format {o['format']}, time {o['include_time']}, 24h {o['use_24h']}, font {o['font']}"
        elif o["type"] == "ImageObject":
            det = f"embedded PNG ({o['image_base64_chars']} base64 chars), scale {o['scale_mode']}"
        elif o["type"] == "ShapeObject":
            det = o["shape"]
        out.append(f"| `{o['name']}` | {o['type']} | {bounds} | {det} |")
    return out + [""]


def workbook_section(path: Path, sheet: str) -> list[str]:
    pkg = XlsxPackage(path.read_bytes())
    out = ["## Excel workbook", "", f"* File: `{path.name}` ({path.stat().st_size:,} bytes)", ""]
    wb = pkg.tree("xl/workbook.xml")
    out += ["| # | Sheet name | Code points of first chars | State |", "|---|---|---|---|"]
    for i, s in enumerate(wb.findall("m:sheets/m:sheet", N), 1):
        name = s.get("name")
        cps = " ".join(f"U+{ord(c):04X}" for c in name[:2])
        out.append(f"| {i} | `{name}` | {cps} | {s.get('state', 'visible')} |")
    out.append("")
    parts = sorted(i.filename for i in pkg.infos)
    out += ["Package parts (shows why openpyxl must not be used to save this file):", "",
            "```", *[p for p in parts if not p.startswith("xl/worksheets/sheet")], "```", ""]
    ext = [p for p in parts if p.startswith("xl/externalLinks/_rels/")]
    for p in ext:
        targets = re.findall(r'Target="([^"]+)"', pkg.read(p).decode("utf-8"))
        out.append(f"* External link targets: {', '.join('`' + redact(t) + '`' for t in targets)}")
    out.append("")

    ed = SheetEditor(pkg, sheet)
    last = ed.last_data_row()
    header = [ed.value(1, c).value for c in "ABCDEFGHIJKLMNOP"]
    out += [f"### Worksheet `{sheet}` (part `{ed.part}`)", "",
            f"* Last data row (A–K): **{last}**; data rows: {last - 1}",
            f"* `<dimension>`: `{ed.root.find('m:dimension', N).get('ref')}` — pre-formatted empty rows follow the data",
            f"* Freeze pane / autofilter: `{ed.root.find('m:sheetViews/m:sheetView/m:pane', N).get('topLeftCell')}` / "
            f"`{ed.root.find('m:autoFilter', N).get('ref') if ed.root.find('m:autoFilter', N) is not None else '-'}`",
            f"* sheetFormatPr: `{dict(ed.root.find('m:sheetFormatPr', N).attrib)}` (zeroHeight=1 hides rows "
            "without an explicit height)", ""]
    out += ["| Col | Header | Value types in data rows | Style ids | Example (row 2..last) |", "|---|---|---|---|---|"]
    for i, col in enumerate("ABCDEFGHIJKLMNOP"):
        kinds = collections.Counter()
        styles = collections.Counter()
        for r in range(2, last + 1):
            cv = ed.value(r, col)
            kind = cv.kind
            if kind == "num" and col == "P":
                kind = "date-serial"
            kinds[kind] += 1
            styles[cv.style] += 1
        ex = ed.value(last, col)
        exv = ex.formula if ex.kind == "formula" else ex.value
        if col in "FHK":
            exv = "<redacted>"
        out.append(f"| {col} | {header[i]} | {dict(kinds)} | {dict(styles)} | `{exv}` |")
    out.append("")
    out += ["Formula patterns in L–O (row numbers replaced by `{r}`):", ""]
    for col in "LMNO":
        pats = collections.Counter()
        for r in range(2, last + 1):
            cv = ed.value(r, col)
            pats[row_pattern(cv.formula, r) if cv.kind == "formula" else f"<{cv.kind}>"] += 1
        for p, n in pats.items():
            out.append(f"* **{col}** ({n} rows): `={p}`")
    out.append("")
    g = collections.Counter(str(ed.value(r, "G").value) for r in range(2, last + 1))
    pre = collections.Counter(re.match(r"PG0|PG|VS|EX", str(ed.value(r, "H").value) or "").group(0)
                              if re.match(r"PG0|PG|VS|EX", str(ed.value(r, "H").value)) else "other"
                              for r in range(2, last + 1))
    out += [f"* Column G categories: {dict(g)}; barcode prefixes in H: {dict(pre)}"]
    highest: dict[str, int] = {}
    for r in range(2, last + 1):
        m = re.fullmatch(r"(PG0|PG|VS|EX)(\d+)", str(ed.value(r, "H").value or ""))
        if m:
            highest[m.group(1)] = max(highest.get(m.group(1), 0), int(m.group(2)))
    out.append(f"* Highest barcode number per prefix in this sheet: {highest}")
    text_dates = [r for r in range(2, last + 1) if ed.value(r, "P").kind == "str"]
    out.append(f"* Rows whose date (P) is stored as text instead of a date: {len(text_dates)} "
               f"(rows {text_dates[:10]})")
    empty_dates = [r for r in range(2, last + 1) if ed.value(r, "P").kind == "empty"]
    out.append(f"* Rows with empty date (P): {len(empty_dates)}")
    out.append("")
    return out


def build_report(reference_dir: Path, templates_yaml: Path, sheet: str, redact_customers: bool = True,
                 workbook_name: str = "Σχισμενα.xlsx") -> str:
    reg = TemplateRegistry.from_yaml(templates_yaml, reference_dir)
    lines = ["# Reference file analysis", "",
             "_Generated by `vspm report`. Customer names and the OneDrive account id are redacted._", ""]
    present = sorted(p.name for p in reference_dir.iterdir() if p.is_file() and p.name != "README.md")
    lines += ["## Files present", "", *[f"* `{p}`" for p in present], ""]
    missing = []
    for key, spec in reg.specs.items():
        if reg.find_file(key) is None:
            missing.append(f"{key.value}: none of {spec.files}")
    if not any(p.lower().endswith((".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff")) for p in present):
        missing.append("sample scans of handwritten production documents (none provided)")
    lines += ["Missing:", "", *([f"* {m}" for m in missing] or ["* nothing"]), ""]
    wb = reference_dir / workbook_name
    if wb.exists():
        lines += workbook_section(wb, sheet)
    lines += ["## DYMO templates", ""]
    for key, spec in reg.specs.items():
        path: Optional[Path] = reg.find_file(key)
        if path is None:
            continue
        tpl = LabelTemplate.load(path)
        lines += template_section(tpl, f"{key.value} — {spec.description}", redact_customers)
    return "\n".join(lines) + "\n"
