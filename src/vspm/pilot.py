"""Pilot tooling: DYMO template test prints, a full dry run on copies, and an
optional check of the written workbook with the real Excel application.

None of these functions modifies the original templates or the source workbook;
each verifies that with SHA-256 fingerprints taken before and after.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from vspm.config import Settings
from vspm.domain.models import LabelContent, TemplateKey


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ---- DYMO template test ---------------------------------------------------------------------
@dataclass
class TemplateTestResult:
    key: str
    file: str
    counter_before: Optional[int]
    unchanged: bool
    label_file: Path
    printed: Optional[bool] = None
    error: Optional[str] = None


def dymo_template_test(ctx, keys: list[str], do_print: bool, out_dir: Path,
                       barcode_text: str = "TEST0001") -> list[TemplateTestResult]:
    """Fill each template with obviously-test values and (optionally) print it.

    The barcode is the fixed text TEST0001 - no number is allocated, the DYMO
    counter in the template is not used, and Excel is not touched. Template files
    are fingerprinted before and after to prove they were not modified."""
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for k in keys:
        key = TemplateKey(k)
        spec = ctx.templates.spec(key)
        path = ctx.templates.find_file(key)
        before = sha256_file(path)
        tpl = ctx.templates.load(key)
        counter = (tpl.counter_info(spec.counter_object) or {}).get("current")
        content = LabelContent(template_key=key, material="ΔΟΚΙΜΗ TEST", thickness="0,00", dimensions="TEST",
                               pieces="0", customer_short="ΔΟΚΙΜΗ / TEST", weight="0", barcode=barcode_text)
        xml = tpl.populate(spec, content, 0)
        label_file = out_dir / f"TEST_{key.value}.label"
        label_file.write_text(xml, encoding="utf-8")
        r = TemplateTestResult(key.value, path.name, counter, True, label_file)
        if do_print:
            res = ctx.printer.print_label(xml, ctx.settings.printer_name)
            r.printed, r.error = res.ok, res.error
        r.unchanged = sha256_file(path) == before
        results.append(r)
    return results


# ---- full dry run -----------------------------------------------------------------------------
@dataclass
class DryRunResult:
    ok: bool
    lines: list[str] = field(default_factory=list)
    work_dir: Optional[Path] = None
    workbook_copy: Optional[Path] = None

    def say(self, ok: Optional[bool], text: str) -> None:
        mark = {True: "OK  ", False: "FAIL", None: "INFO"}[ok]
        self.lines.append(f"[{mark}] {text}")
        if ok is False:
            self.ok = False


def pilot_dryrun(base: Settings, workbook: Optional[Path], synthetic: bool, work_dir: Optional[Path] = None,
                 excel_com: bool = False) -> DryRunResult:
    """Rehearse the pilot end to end on copies:

    mock scan -> AI (mock) -> approve row 1 -> Print & Save (mock printer, test
    barcode range) -> independent verification -> reprint -> verify that no second
    Excel row appeared and that a second Print & Save is refused.
    Uses the real templates from VSPM_TEMPLATE_DIR (read-only)."""
    from vspm.services.context import build_context
    from vspm.services.production import AlreadyPrinted
    from vspm.services.verification import verify_label
    from vspm.excel.xlsx_package import SheetEditor, XlsxPackage

    out = DryRunResult(ok=True)
    work = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="vspm-dryrun-"))
    out.work_dir = work
    data = work / "data"
    copy = data / "test_workbook" / "dryrun.xlsx"
    copy.parent.mkdir(parents=True, exist_ok=True)
    source_hash = None
    if synthetic or workbook is None:
        from vspm.testing import build_test_workbook
        build_test_workbook(copy)
        out.say(None, "using a synthetic workbook (same structure as the production sheet)")
    else:
        source_hash = sha256_file(workbook)
        shutil.copyfile(workbook, copy)
        out.say(None, f"working on a COPY of {workbook.name}: {copy}")
    out.workbook_copy = copy

    tdir = base.resolve(base.template_dir)
    template_hashes = {p: sha256_file(p) for p in tdir.glob("*.label")}
    settings = Settings(_env_file=None, **{
        **base.model_dump(exclude={"anthropic_api_key"}),
        "data_dir": data, "drive_mode": "mock", "ocr_provider": "mock", "barcode_mode": "mock",
        "printer_mode": "mock", "excel_mode": "direct", "excel_write_enabled": True, "excel_path": copy,
        "excel_require_test_copy": True, "template_dir": tdir,
    })
    ctx = build_context(settings)
    for k, v in ctx.problems.items():
        if k.startswith("template"):
            out.say(False, f"{k}: {v}")
    if not out.ok:
        return out
    before_rows = SheetEditor(XlsxPackage(copy.read_bytes()), settings.excel_sheet).last_data_row()

    doc = ctx.documents.latest(1)[0]
    rows = ctx.documents.run_ocr(doc.doc_id, ctx.ocr_provider())
    out.say(len(rows) == 3, f"document '{doc.remote.name}' read: {len(rows)} rows")
    row = rows[0]
    row.flags = {k: v for k, v in row.flags.items() if v != "uncertain"}  # the operator checked them
    row.template_needs_confirmation = False
    row.approved = True
    ctx.repo.save_row(row)
    res = ctx.production.print_and_save(row.id, operator="dry-run")
    out.say(res.print_ok, f"Print & Save row 1 -> {res.barcode}: print '{res.print_status}', Excel '{res.excel_status}'")
    rep = verify_label(ctx, res.label_id)
    for c in rep.checks:
        out.say(c.ok, f"{c.name}: {c.detail}")
    after_first = copy.read_bytes()
    re_out = ctx.production.reprint(res.label_id, operator="dry-run")
    out.say(re_out.print_ok and re_out.barcode == res.barcode, f"reprint uses the same barcode {re_out.barcode}")
    out.say(copy.read_bytes() == after_first, "reprint did not change the workbook")
    try:
        ctx.production.print_and_save(row.id, operator="dry-run")
        out.say(False, "second Print & Save of the same row was NOT refused")
    except AlreadyPrinted:
        out.say(True, "second Print & Save of the same row is refused")
    ed = SheetEditor(XlsxPackage(copy.read_bytes()), settings.excel_sheet)
    out.say(ed.last_data_row() == before_rows + 1, f"workbook copy grew by exactly one row (row {before_rows + 1})")
    out.say(ed.find_rows_with_value("H", res.barcode) == [before_rows + 1], f"{res.barcode} appears once")
    out.say(all(sha256_file(p) == h for p, h in template_hashes.items()),
            f"{len(template_hashes)} original template file(s) unchanged (SHA-256)")
    if source_hash:
        out.say(sha256_file(workbook) == source_hash, f"source workbook {workbook.name} unchanged (SHA-256)")
    if excel_com:
        for ok, text in excel_com_check(copy, settings.excel_sheet, [res.barcode]):
            out.say(ok, text)
    return out


# ---- check with the real Excel application (Windows, optional) -----------------------------
def excel_com_check(path: Path, sheet: str, barcodes: list[str]) -> list[tuple[Optional[bool], str]]:
    """Open the written workbook copy in Microsoft Excel (read-only, links not updated)
    and read back the new rows. Requires Windows, Excel and pywin32."""
    if sys.platform != "win32":
        return [(None, "Excel check skipped: needs Windows with Microsoft Excel")]
    try:
        import win32com.client  # type: ignore
    except ImportError:
        return [(None, "Excel check skipped: pywin32 not installed (pip install -e .[windows])")]
    out: list[tuple[Optional[bool], str]] = []
    xl = None
    try:
        xl = win32com.client.DispatchEx("Excel.Application")
        xl.Visible = False
        xl.DisplayAlerts = False
        xl.AskToUpdateLinks = False
        wb = xl.Workbooks.Open(str(Path(path).resolve()), UpdateLinks=0, ReadOnly=True)
        out.append((True, f"Excel {xl.Version} opened the workbook copy ({wb.Name})"))
        repaired = any(w in wb.Name.lower() for w in ("repaired", "επισκευ"))
        out.append((not repaired, "no repair needed" if not repaired else "Excel REPAIRED the file"))
        ws = wb.Worksheets(sheet)
        used = ws.UsedRange
        last = used.Row + used.Rows.Count - 1
        for bc in barcodes:
            rows = [r for r in range(2, last + 1) if str(ws.Cells(r, 8).Value or "").strip() == bc]
            out.append((len(rows) == 1, f"Excel sees {bc} in row(s) {rows}"))
            if len(rows) == 1:
                r = rows[0]
                formulas = [ws.Cells(r, c).Formula for c in (12, 13, 14, 15)]
                out.append((all(str(f).startswith("=") for f in formulas), f"Excel formulas L–O row {r}: {formulas}"))
                out.append((None, f"Excel shows the date as '{ws.Cells(r, 16).Text}' (format "
                                  f"'{ws.Cells(r, 16).NumberFormat}')"))
                vis = not ws.Rows(r).Hidden
                out.append((vis, f"row {r} visible in Excel" if vis else f"row {r} is HIDDEN in Excel"))
        wb.Close(SaveChanges=False)
    except Exception as e:
        out.append((False, f"Excel check failed: {e}"))
    finally:
        if xl is not None:
            try:
                xl.Quit()
            except Exception:
                pass
    return out
