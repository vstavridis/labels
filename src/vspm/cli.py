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

from vspm.config import PROJECT_ROOT, get_settings

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


@app.command("test-print")
def test_print(template: str = "VS", printer: Optional[str] = None,
               barcode: str = typer.Option("TEST0001", help="Text printed in the barcode (not allocated)")):
    """Print a clearly marked TEST label without allocating a number or touching Excel."""
    from vspm.domain.models import LabelContent, TemplateKey
    ctx = _ctx()
    key = TemplateKey(template)
    spec = ctx.templates.spec(key)
    content = LabelContent(template_key=key, material="ΔΟΚΙΜΗ / TEST", thickness="0,00", dimensions="TEST",
                           pieces="0", customer_short="TEST", weight="0", barcode=barcode)
    xml = ctx.templates.load(key).populate(spec, content, 0)
    out = ctx.settings.data_path / "test_print.label"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(xml, encoding="utf-8")
    res = ctx.printer.print_label(xml, printer or ctx.settings.printer_name)
    typer.echo(f"Saved {out}. Print result: {res}")
    typer.echo("Check the physical label: layout, fonts, barcode text and scannability.")


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


@registry.command("serve")
def registry_serve():
    """Run the registry service locally (for tests / small installations)."""
    from vspm_registry.app import main
    main()


if __name__ == "__main__":  # pragma: no cover
    app()
