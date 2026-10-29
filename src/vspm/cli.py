"""`vspm` command-line tool."""

from __future__ import annotations

import collections
import json
import shutil
import socket
import sys
import time
from pathlib import Path
from typing import Optional

import typer

from vspm.config import get_settings

# Windows consoles/redirects may use a legacy code page; Greek output must not crash.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

app = typer.Typer(help="VS Production Manager utilities", no_args_is_help=True)
registry = typer.Typer(help="Barcode registry administration")
app.add_typer(registry, name="registry")


def _ctx():
    from vspm.logging_setup import setup_logging
    from vspm.services.context import build_context
    s = get_settings()
    setup_logging(s)
    return build_context(s)


@app.command()
def run(port: int = 8501, host: str = typer.Option("localhost", help="0.0.0.0 = reachable from other computers")):
    """Start the Streamlit user interface (any OS)."""
    import subprocess
    ui = Path(__file__).parent / "ui" / "app.py"
    raise SystemExit(subprocess.call([sys.executable, "-m", "streamlit", "run", str(ui), "--server.port", str(port),
                                      "--server.address", host, "--server.headless", "true",
                                      "--browser.gatherUsageStats", "false"]))


@app.command()
def init():
    """Create the local database and data folders, and show configuration problems."""
    ctx = _ctx()
    typer.echo(f"Database: {ctx.settings.db_path}")
    for k, v in ctx.problems.items():
        typer.secho(f"  ! {k}: {v}", fg="yellow")
    if not ctx.problems:
        typer.secho("OK", fg="green")


@app.command()
def report(reference_dir: Path = Path("reference_files"), output: Path = Path("docs/REFERENCE_REPORT.md"),
           no_redact: bool = typer.Option(False, help="Include customer names (do not commit the result)")):
    """Analyse the reference workbook and DYMO templates."""
    from vspm.reference_report import build_report
    s = get_settings()
    text = build_report(s.resolve(reference_dir), s.resolve(s.templates_config), s.excel_sheet,
                        redact_customers=not no_redact)
    out = s.resolve(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    typer.echo(f"Report written to {out}")


@app.command("make-test-workbook")
def make_test_workbook(source: Path = Path("reference_files/Σχισμενα.xlsx"),
                       dest: Path = Path("data/test_workbook/Σχισμενα.xlsx")):
    """Copy the reference workbook to a test location (never edits the source)."""
    s = get_settings()
    src, dst = s.resolve(source), s.resolve(dest)
    if dst.exists():
        typer.confirm(f"{dst} exists. Overwrite?", abort=True)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    dst.chmod(0o644)
    typer.echo(f"Test workbook: {dst}")


@app.command()
def check():
    """Check every integration and print a status line for each."""
    ctx = _ctx()
    s = ctx.settings
    ok, msg = ctx.printer.status()
    typer.echo(f"Printer ({s.printer_mode.value}): {'OK' if ok else 'PROBLEM'} - {msg}")
    try:
        names = [p.name for p in ctx.printer.list_printers()]
        typer.echo(f"  printers: {names}")
    except Exception as e:
        typer.echo(f"  printers: cannot list ({e})")
    ok, msg = ctx.allocator.health()
    typer.echo(f"Barcodes ({s.barcode_mode.value}): {'OK' if ok else 'PROBLEM'} - {msg}")
    problems = ctx.writer.preflight()
    typer.echo(f"Excel ({s.excel_mode.value}, writes {'enabled' if s.excel_write_enabled else 'disabled'}): "
               f"{ctx.writer.path} -> {'OK' if not problems else problems}")
    if ctx.documents:
        try:
            docs = ctx.documents.source.list_latest(5)
            typer.echo(f"Documents ({s.drive_mode.value}): {len(docs)} found")
        except Exception as e:
            typer.echo(f"Documents ({s.drive_mode.value}): PROBLEM - {e}")
    typer.echo(f"AI: {s.ocr_provider.value} / {s.ocr_model} / upload consent {s.ocr_upload_consent}")
    for k, v in ctx.problems.items():
        typer.secho(f"! {k}: {v}", fg="yellow")


@app.command("dymo-test")
def dymo_test(template: list[str] = typer.Option(["VS", "PG", "PG0", "EX"], help="Template(s) to test"),
              print_: bool = typer.Option(False, "--print", help="Send the TEST labels to the printer"),
              barcode: str = typer.Option("TEST0001", help="Fixed barcode text (no number is allocated)")):
    """DYMO integration test with your ORIGINAL templates, without touching their counters.

    Fills each template with TEST values and a fixed TEST barcode, saves the label files to
    data/dymo_test/ (open them in DYMO Label v8 to compare) and, with --print, prints them.
    Verifies with SHA-256 that the original template files are unchanged afterwards."""
    ctx = _ctx()
    out = ctx.settings.data_path / "dymo_test"
    typer.echo(f"Printer mode: {ctx.settings.printer_mode.value}  printer: {ctx.settings.printer_name}")
    if print_:
        ok, msg = ctx.printer.status()
        typer.echo(f"Printer status: {'OK' if ok else 'PROBLEM'} - {msg}")
    from vspm.pilot import dymo_template_test
    failed = False
    for r in dymo_template_test(ctx, template, print_, out, barcode):
        state = "not printed" if r.printed is None else ("sent to printer" if r.printed else f"PRINT FAILED: {r.error}")
        typer.echo(f"{r.key:4s} template counter={r.counter_before}  file={r.label_file}  {state}  "
                   f"template unchanged: {'YES' if r.unchanged else 'NO'}")
        failed |= (not r.unchanged) or r.printed is False
    raise typer.Exit(1 if failed else 0)


@app.command("test-print", hidden=True)
def test_print(template: str = "VS"):
    """Old name of `dymo-test --print --template X`."""
    dymo_test(template=[template], print_=True, barcode="TEST0001")


@app.command("pilot-dryrun")
def pilot_dryrun_cmd(workbook: Optional[Path] = typer.Option(None, help="Workbook to COPY (default: VSPM_EXCEL_PATH)"),
                     synthetic: bool = typer.Option(False, help="Use a generated workbook instead of a copy"),
                     excel: bool = typer.Option(False, "--excel", help="Also open the copy in Microsoft Excel (Windows)"),
                     work_dir: Optional[Path] = typer.Option(None, help="Folder for the copies (default: new temp folder)")):
    """Rehearse the pilot on copies: scan -> rows -> Print & Save -> verify -> reprint.

    Uses the mock scanner, mock AI, mock printer and test barcodes, your real templates
    (read-only) and a COPY of the workbook. Nothing original is modified."""
    from vspm.pilot import pilot_dryrun
    s = get_settings()
    src = None if synthetic else s.resolve(workbook or s.excel_path)
    if src is not None and not src.exists():
        raise typer.BadParameter(f"workbook not found: {src} (use --synthetic or --workbook)")
    res = pilot_dryrun(s, src, synthetic, work_dir=work_dir, excel_com=excel)
    for line in res.lines:
        typer.echo(line)
    typer.echo(f"\nWorkbook copy with the test row: {res.workbook_copy}")
    typer.secho("DRY RUN PASSED" if res.ok else "DRY RUN FAILED", fg="green" if res.ok else "red")
    raise typer.Exit(0 if res.ok else 1)


@app.command("verify-label")
def verify_label_cmd(barcode: str):
    """After printing: check layout, barcode and that Excel has exactly one matching record."""
    from vspm.services.verification import verify_label
    ctx = _ctx()
    lab = ctx.db.one("SELECT id FROM labels WHERE barcode=?", (barcode.strip().upper(),))
    if lab is None:
        raise typer.BadParameter(f"no label {barcode} in this computer's database")
    rep = verify_label(ctx, lab["id"])
    typer.echo(rep.text())
    raise typer.Exit(0 if rep.ok else 1)


@app.command("drive-test")
def drive_test():
    """List the 5 latest scans from the configured source and download the newest one."""
    ctx = _ctx()
    if ctx.documents is None:
        typer.secho(f"Document source not available: {ctx.problems.get('documents')}", fg="red")
        raise typer.Exit(1)
    docs = ctx.documents.source.list_latest(5)
    typer.echo(f"Source: {ctx.settings.drive_mode.value}  ({len(docs)} documents)")
    for d in docs:
        typer.echo(f"  {d.uploaded_at}  {d.mime_type:18s} {d.name}  rev={d.revision[:12]}")
    if docs:
        from vspm.integrations.documents import page_images
        data = ctx.documents.source.download(docs[0])
        pages = page_images(data, docs[0].mime_type)
        typer.echo(f"Downloaded {docs[0].name}: {len(data):,} bytes, {len(pages)} page(s), first page {pages[0].size}")
    raise typer.Exit(0 if docs else 1)


@app.command("ocr-test")
def ocr_test(file: Path, save: Optional[Path] = typer.Option(None, help="Save the raw AI result as JSON")):
    """Read one local scan (PDF/JPG/PNG) with the configured AI provider and show the rows.

    Sends the document to the AI provider only if VSPM_OCR_UPLOAD_CONSENT=true."""
    import mimetypes
    from vspm.integrations.ocr.base import OcrInput
    from vspm.integrations.ocr.postprocess import rows_from_extraction
    ctx = _ctx()
    mime = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
    out = ctx.ocr_provider().extract(OcrInput(file.read_bytes(), mime, file.name))
    if save:
        save.write_text(out.raw_json, encoding="utf-8")
    rows = rows_from_extraction(out.document, ctx.customers)
    typer.echo(f"{out.provider}/{out.model}: {len(rows)} row(s), pages={out.document.page_count}")
    for w in out.document.warnings:
        typer.secho(f"  warning: {w}", fg="yellow")
    for r in rows:
        flags = ", ".join(k for k, v in r.flags.items() if v == "uncertain")
        typer.echo(f"  #{r.row_index}: {r.customer_full} | {r.material} | {r.thickness} | {r.dimensions} | "
                   f"{r.weight} kg | {r.pieces} τεμ | δέματα {r.packages} | ρολό {r.source_roll} | "
                   f"{r.template_key.value if r.template_key else '-'}" + (f"  ⚠ {flags}" if flags else ""))


@app.command("suggest-customers")
def suggest_customers(workbook: Path = Path("reference_files/Σχισμενα.xlsx")):
    """Show customer -> category history from a workbook copy (read-only) to help write rules."""
    from vspm.excel.xlsx_package import SheetEditor, XlsxPackage
    s = get_settings()
    ed = SheetEditor(XlsxPackage(s.resolve(workbook).read_bytes()), s.excel_sheet)
    counts = collections.Counter()
    for r in range(2, ed.last_data_row() + 1):
        h = str(ed.value(r, "H").value or "")
        cat = "PG0" if h.startswith("PG0") else str(ed.value(r, "G").value)
        counts[(str(ed.value(r, "F").value).strip(), cat)] += 1
    for (name, cat), n in sorted(counts.items(), key=lambda x: (x[0][0], -x[1])):
        typer.echo(f"{name:45s} {cat:4s} {n}")


@app.command("excel-worker")
def excel_worker(once: bool = False, interval: int = 30):
    """Designated Excel PC: write queued rows from the registry into the workbook."""
    ctx = _ctx()
    if ctx.registry_client is None:
        raise typer.BadParameter("VSPM_REGISTRY_URL and VSPM_REGISTRY_API_KEY are required")
    from vspm.domain.models import ExcelRowPayload
    from vspm.excel.writer import ExcelSyncError
    worker = f"{ctx.settings.effective_computer_name}:{socket.gethostname()}"
    while True:
        jobs = ctx.registry_client.claim_excel(worker)
        for job in jobs:
            payload = ExcelRowPayload.model_validate(json.loads(job["payload"]))
            try:
                rep = ctx.writer.append([payload])
                res = rep.results[0]
                status = "done" if res.status == "written" else "already_present"
                ctx.registry_client.complete_excel(job["id"], status, {"row_number": res.row_number,
                                                                       "hash_after": rep.hash_after})
                typer.echo(f"{payload.barcode}: {status} row {res.row_number}")
            except ExcelSyncError as e:
                ctx.registry_client.complete_excel(job["id"], e.status, {"error": str(e)})
                typer.secho(f"{payload.barcode}: {e.status} {e}", fg="red")
        n = ctx.production.refresh_remote()
        if n:
            typer.echo(f"{n} local labels marked synchronized")
        if once:
            break
        time.sleep(interval)


@registry.command("set-sequence")
def set_sequence(series: str, next_value: int, prefix: Optional[str] = None, width: int = 3,
                 max_value: Optional[int] = None, admin_key: str = typer.Option(..., prompt=True, hide_input=True),
                 allow_backwards: bool = False):
    """Initialise / reconcile a series in the registry (admin key required)."""
    import httpx
    s = get_settings()
    r = httpx.put(f"{s.registry_url.rstrip('/')}/v1/sequences/{series}", headers={"X-API-Key": admin_key}, json={
        "prefix": prefix or series, "format_width": width, "next_value": next_value, "max_value": max_value,
        "allow_backwards": allow_backwards, "operator": s.effective_operator}, timeout=15)
    typer.echo(f"{r.status_code} {r.text}")


@registry.command("status")
def registry_status():
    """Show the registry's sequences."""
    import httpx
    s = get_settings()
    r = httpx.get(f"{s.registry_url.rstrip('/')}/v1/sequences", headers={"X-API-Key": s.registry_api_key}, timeout=15)
    typer.echo(json.dumps(r.json(), indent=2, ensure_ascii=False))


@registry.command("observe")
def registry_observe(barcodes: list[str], source: str = typer.Option("manual", help="Where these were printed"),
                     operator: Optional[str] = None):
    """Report barcodes printed manually in DYMO WITHOUT a block reservation.

    The registry records them and moves its counter past them. Any number listed under
    'already registered' was ALSO issued by the registry: that is a duplicate."""
    s = get_settings()
    from vspm.barcodes.allocator import RegistryAllocator
    res = RegistryAllocator(s.registry_url, s.registry_api_key).observe(barcodes, source, operator or s.effective_operator,
                                                                         s.effective_computer_name)
    typer.echo(json.dumps(res, indent=2, ensure_ascii=False))


@registry.command("observe-workbook")
def registry_observe_workbook(workbook: Path = typer.Option(..., help="A COPY of the workbook (read-only use)")):
    """Teach the registry every barcode already in the workbook (column H), so it never issues them."""
    from vspm.barcodes.allocator import RegistryAllocator
    from vspm.excel.xlsx_package import SheetEditor, XlsxPackage
    s = get_settings()
    ed = SheetEditor(XlsxPackage(s.resolve(workbook).read_bytes()), s.excel_sheet)
    codes = sorted({str(v).strip() for r, v in ed.column_values("H").items() if r > 1})
    res = RegistryAllocator(s.registry_url, s.registry_api_key).observe(codes, "workbook", s.effective_operator,
                                                                         s.effective_computer_name)
    typer.echo(f"{len(codes)} barcodes sent: {len(res['recorded'])} recorded, "
               f"{len(res['already_registered'])} already registered, {len(res['ignored'])} not recognised")
    typer.echo(f"sequences advanced: {res['advanced']}")


@registry.command("serve")
def registry_serve():
    """Run the registry service locally (for tests / small installations)."""
    from vspm_registry.app import main
    main()


if __name__ == "__main__":  # pragma: no cover
    app()
