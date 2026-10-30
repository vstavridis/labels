"""Independent checks of a printed label and its Excel record (pilot verification).

Everything is re-derived from files on disk: the original template, the exact
label XML that was sent to the printer (data/labels/<BARCODE>.label) and the
workbook. Nothing is modified.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from lxml import etree

from vspm.domain.models import ExcelRowPayload, LabelContent, TemplateKey
from vspm.dymo import code39
from vspm.dymo.template import LabelTemplate, TemplateSpec
from vspm.excel.formulas import row_pattern
from vspm.excel.writer import FORMULA_COLS, payload_to_cells
from vspm.excel.xlsx_package import SheetEditor, XlsxPackage, date_to_serial, N


@dataclass
class Check:
    name: str
    ok: Optional[bool]  # None = could not be checked
    detail: str = ""


@dataclass
class Report:
    barcode: str
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok is not False for c in self.checks) and any(c.ok for c in self.checks)

    def add(self, name: str, ok: Optional[bool], detail: str = "") -> None:
        self.checks.append(Check(name, ok, detail))

    def text(self) -> str:
        mark = {True: "OK  ", False: "FAIL", None: "SKIP"}
        lines = [f"Verification of {self.barcode}: {'PASSED' if self.ok else 'FAILED'}"]
        lines += [f"  [{mark[c.ok]}] {c.name}: {c.detail}" for c in self.checks]
        return "\n".join(lines)


def compare_layout(original: etree._Element, printed: etree._Element, spec: TemplateSpec) -> list[str]:
    """Differences between template and printed label other than the fields the app fills."""
    diffs: list[str] = []

    def allowed(obj_tag: str, obj_name: str, path: list[str]) -> bool:
        leaf = path[-1]
        if obj_tag in ("TextObject",) and obj_name in (spec.text_object, spec.weight_object) and leaf == "String":
            return True
        if obj_tag == "BarcodeObject" and obj_name == spec.barcode_object and leaf in ("Text", "LinkedObjectName"):
            return True
        if obj_tag == "CounterObject" and obj_name == spec.counter_object and leaf == "Current":
            return True
        return False

    def walk(a: etree._Element, b: etree._Element, path: list[str], obj: tuple[str, str]) -> None:
        if a.tag != b.tag:
            diffs.append(f"{'/'.join(path)}: element {a.tag} became {b.tag}")
            return
        if dict(a.attrib) != dict(b.attrib):
            diffs.append(f"{'/'.join(path + [a.tag])}: attributes changed {dict(a.attrib)} -> {dict(b.attrib)}")
        here = path + [a.tag]
        if a.tag in ("TextObject", "BarcodeObject", "CounterObject", "ShapeObject", "ImageObject",
                     "DateTimeObject", "AddressObject"):
            obj = (a.tag, a.findtext("Name") or "")
        if (a.text or "").strip() != (b.text or "").strip() and not allowed(obj[0], obj[1], here):
            diffs.append(f"{'/'.join(here)} [{obj[1]}]: text changed")
        ka, kb = list(a), list(b)
        if len(ka) != len(kb):
            diffs.append(f"{'/'.join(here)}: {len(ka)} children became {len(kb)}")
            return
        for x, y in zip(ka, kb):
            walk(x, y, here, obj)

    walk(original, printed, [], ("", ""))
    return diffs


def _browser_jobs(ctx, label_id: int) -> list:
    return ctx.db.query("SELECT * FROM print_jobs WHERE label_id=? AND status IN ('submitted','manual_confirmed') "
                        "ORDER BY id", (label_id,))


def verify_label(ctx, label_id: int) -> Report:
    """Run all checks for one label. `ctx` is an AppContext."""
    lab = ctx.repo.get_label(label_id)
    if lab is None:
        raise ValueError(f"label {label_id} not found")
    rep = Report(lab["barcode"])
    key = TemplateKey(lab["template_key"])
    spec = ctx.templates.spec(key)
    content = LabelContent.model_validate_json(lab["content_json"])

    # 1. Original template unchanged by the app
    current = ctx.templates.load(key)
    original = ctx.templates.load_version(key, lab["template_sha256"]) if lab["template_sha256"] else None
    if lab["template_sha256"]:
        rep.add("template file unchanged", original is not None,
                ("identical to the file used for printing" if current.sha256 == lab["template_sha256"] else
                 "the exact template version used for printing is stored (a newer template is active now)")
                if original is not None else
                "the template used for printing is no longer available (file edited or counter advanced in DYMO)")
    else:
        rep.add("template file unchanged", None, "no fingerprint stored for this label (printed by an older version)")
    original = original or current

    # 2. Printed label = template + only the filled fields
    printed_path = Path(lab["label_xml_path"]) if lab["label_xml_path"] else None
    printed = None
    if printed_path is not None and printed_path.exists():
        printed = LabelTemplate.from_bytes(printed_path.read_bytes(), printed_path)
        diffs = compare_layout(original.root, printed.root, spec)
        rep.add("label layout preserved", not diffs,
                "objects, positions, fonts and images identical to the template; only text, weight and barcode "
                "filled" if not diffs else "; ".join(diffs[:5]))
        expected = original.populate(spec, content, lab["number"])
        same = etree.tostring(etree.fromstring(expected.encode("utf-8"))) == etree.tostring(printed.root)
        rep.add("label content as approved", same,
                f"{content.material} / {content.thickness} / {content.dimensions} / {content.pieces} τεμ / "
                f"{content.customer_short} / {content.weight} kg" if same else "printed text differs from stored row")
    elif getattr(ctx, "browser", None) is not None and _browser_jobs(ctx, label_id):
        # Printed by the employee's browser: the server prepared the label, the browser reported the
        # SHA-256 of what it sent to DYMO. Rebuild it from the template version and the stored row.
        jobs = _browser_jobs(ctx, label_id)
        expected = original.populate(spec, content, lab["number"], counter_mode=ctx.browser.counter_mode)
        from vspm.dymo.template import check_prepared_label, xml_sha256
        want = xml_sha256(expected)
        bad = [j["job_key"][:8] for j in jobs if j["xml_sha256"] != want or
               (j["browser_sha256"] and j["browser_sha256"] != want)]
        problems = check_prepared_label(expected, lab["barcode"])
        rep.add("label layout preserved", not problems and not bad,
                f"{len(jobs)} browser print job(s): the label sent matches the template + approved row "
                f"(SHA-256 {want[:12]}…)" if not problems and not bad else
                "; ".join(problems + [f"job {b} sent a different label" for b in bad]))
        rep.add("label content as approved", not bad,
                f"{content.material} / {content.thickness} / {content.dimensions} / {content.pieces} τεμ / "
                f"{content.customer_short} / {content.weight} kg")
        printed = LabelTemplate.from_bytes(expected.encode("utf-8"))
    else:
        rep.add("label layout preserved", None, "printed label file not found (label never sent to the printer)")

    # 3. Barcode
    problems = []
    if not lab["barcode"].startswith(spec.barcode_prefix) or not re.fullmatch(
            rf"{spec.barcode_prefix}\d+", lab["barcode"]):
        problems.append(f"barcode does not match prefix {spec.barcode_prefix}")
    if int(lab["barcode"][len(spec.barcode_prefix):]) != lab["number"]:
        problems.append("barcode digits differ from reserved number")
    if not code39.is_encodable(lab["barcode"]):
        problems.append("not encodable in Code 39")
    if printed is not None:
        bc = printed.find(spec.barcode_object)
        if bc.findtext("Text") != lab["barcode"]:
            problems.append(f"label barcode text is {bc.findtext('Text')}")
        if bc.findtext("LinkedObjectName"):
            problems.append("barcode still linked to the DYMO counter")
    try:
        res = ctx.allocator.lookup(lab["idempotency_key"])
        if res is None:
            reg = "reservation not found in the allocator"
            if lab["reservation_mode"] == ctx.allocator.mode:
                problems.append(reg)
        elif res.barcode != lab["barcode"]:
            problems.append(f"allocator has {res.barcode} for this row")
        else:
            reg = f"reserved in {res.mode} as {res.barcode}"
    except Exception as e:  # registry offline: report, do not fail the label
        reg = f"allocator not reachable ({e})"
    rep.add("barcode is the reserved number", not problems, "; ".join(problems) if problems else
            f"{lab['barcode']} ({spec.barcode_prefix}, Code 39, unlinked from COUNTER; {reg})")

    # 4. Excel: exactly one record with the right values
    if lab["excel_status"] != "synchronized":
        rep.add("exactly one Excel record", None, f"Excel status is '{lab['excel_status']}'")
        return rep
    data = ctx.writer.read_bytes()
    ed = SheetEditor(XlsxPackage(data), ctx.writer.sheet)
    rows = ed.find_rows_with_value("H", lab["barcode"])
    rep.add("exactly one Excel record", len(rows) == 1, f"row(s) {rows} in {ctx.writer.path.name}")
    if len(rows) != 1:
        return rep
    r = rows[0]
    payload = ExcelRowPayload.model_validate_json(lab["excel_payload_json"])
    values, dates = payload_to_cells(payload)
    bad = []
    for col, exp in values.items():
        got = ed.value(r, col).value
        if not (got == exp or str(got) == str(exp) or (exp in ("", None) and got in ("", None))):
            bad.append(f"{col}{r}={got!r} expected {exp!r}")
    rep.add("Excel values A–K match the label", not bad, "; ".join(bad) or
            f"A={values['A']}, F={values['F']}, G={values['G']}, H={values['H']}")
    fbad = []
    for col in FORMULA_COLS:
        cv = ed.value(r, col)
        if cv.kind != "formula":
            fbad.append(f"{col}{r} has no formula")
            continue
        ref_row = next((x for x in range(r - 1, 1, -1) if ed.value(x, col).kind == "formula"), None)
        if ref_row and row_pattern(cv.formula, r) != row_pattern(ed.value(ref_row, col).formula, ref_row):
            fbad.append(f"{col}{r} differs from the formula pattern of row {ref_row}")
    if not fbad:
        links = "; external link [1] kept" if any("[1]" in (ed.value(r, c).formula or "") for c in "LMO") else ""
    rep.add("Excel formulas L–O", not fbad, "; ".join(fbad) or f"same pattern as the rows above, row {r}{links}")
    p = ed.value(r, "P")
    exp_serial = date_to_serial(date.fromisoformat(payload.print_date))
    fmt = _number_format(XlsxPackage(data), p.style)
    ok_date = p.kind == "num" and int(p.value) == exp_serial and "dd" in fmt and "yyyy" in fmt
    rep.add("Excel print date", ok_date, f"P{r} = {payload.print_date} (real date, format '{fmt}')"
            if ok_date else f"P{r}={p.value!r} format '{fmt}' expected serial {exp_serial}")
    return rep


def _number_format(pkg: XlsxPackage, style: Optional[str]) -> str:
    if style is None:
        return "General"
    styles = pkg.tree("xl/styles.xml")
    xf = styles.findall("m:cellXfs/m:xf", N)[int(style)]
    fid = xf.get("numFmtId", "0")
    for nf in styles.findall("m:numFmts/m:numFmt", N):
        if nf.get("numFmtId") == fid:
            return nf.get("formatCode")
    return {"14": "m/d/yyyy (locale short date)"}.get(fid, f"built-in #{fid}")
