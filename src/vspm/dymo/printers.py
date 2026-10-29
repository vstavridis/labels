"""Printer adapters.

* MockPrinter        - writes the populated .label XML and a PNG preview to disk.
* DymoWebServicePrinter - DYMO's local "DYMO Label Web Service" (installed with
  DYMO Label 8.5+ / DYMO Connect). Accepts the full label XML, so our populated
  working copy is printed exactly. Also renders DYMO's own preview PNG.
* DymoComPrinter     - legacy DLS SDK COM objects (Dymo.DymoAddIn), Windows only,
  via pywin32. Opens a temporary .label file and prints it.

None of these can confirm that a physical label came out of the printer; the
operator confirms that in the app.

Hardware status: these adapters follow DYMO's published interfaces but have
NOT been tested against a physical LabelWriter in this project yet. See
docs/DYMO.md for the test procedure.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Protocol
from xml.etree import ElementTree as ET

import httpx

from vspm.dymo.render import render_label_png


class PrinterError(Exception):
    pass


@dataclass
class PrintResult:
    ok: bool
    job_ref: Optional[str] = None
    error: Optional[str] = None


@dataclass
class PrinterInfo:
    name: str
    model: str = ""
    connected: Optional[bool] = None


class LabelPrinter(Protocol):
    mode: str

    def list_printers(self) -> list[PrinterInfo]: ...

    def print_label(self, label_xml: str, printer_name: str, copies: int = 1) -> PrintResult: ...

    def render_preview(self, label_xml: str) -> Optional[bytes]: ...

    def status(self) -> tuple[bool, str]: ...


class MockPrinter:
    mode = "mock"

    def __init__(self, out_dir: Path):
        self.out_dir = Path(out_dir)
        self.fail_next: Optional[str] = None
        self.printed: list[str] = []

    def list_printers(self) -> list[PrinterInfo]:
        return [PrinterInfo("MOCK DYMO LabelWriter", "mock", True)]

    def print_label(self, label_xml: str, printer_name: str, copies: int = 1) -> PrintResult:
        if self.fail_next:
            err, self.fail_next = self.fail_next, None
            return PrintResult(False, error=err)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        ref = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        (self.out_dir / f"{ref}.label").write_text(label_xml, encoding="utf-8")
        (self.out_dir / f"{ref}.png").write_bytes(render_label_png(label_xml))
        self.printed.append(label_xml)
        return PrintResult(True, job_ref=ref)

    def render_preview(self, label_xml: str) -> Optional[bytes]:
        return None  # the app falls back to its own renderer

    def status(self) -> tuple[bool, str]:
        return True, f"mock printer -> {self.out_dir}"


class DymoWebServicePrinter:
    """DYMO Label Web Service on https://127.0.0.1:41951 (port may be 41951-41960)."""

    mode = "dymo_web"
    PATH = "/DYMO/DLS/Printing"

    def __init__(self, base_url: str = "https://127.0.0.1:41951", verify_tls: bool = False, timeout: float = 30.0,
                 client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(verify=verify_tls, timeout=timeout)

    def _url(self, op: str) -> str:
        return f"{self.base_url}{self.PATH}/{op}"

    def status(self) -> tuple[bool, str]:
        try:
            r = self.client.get(self._url("StatusConnected"))
            ok = r.status_code == 200 and "true" in r.text.lower()
            return ok, f"DYMO web service {'connected' if ok else 'not ready'}: {r.text[:100]}"
        except httpx.HTTPError as e:
            return False, f"DYMO web service not reachable at {self.base_url}: {e}"

    def list_printers(self) -> list[PrinterInfo]:
        try:
            r = self.client.get(self._url("GetPrinters"))
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise PrinterError(f"Cannot list DYMO printers: {e}") from e
        text = r.text.strip()
        if text.startswith('"'):
            text = json.loads(text)
        root = ET.fromstring(text)
        out = []
        for p in root:
            out.append(PrinterInfo(p.findtext("Name") or "", p.findtext("ModelName") or "",
                                   (p.findtext("IsConnected") or "").lower() == "true"))
        return out

    def print_label(self, label_xml: str, printer_name: str, copies: int = 1) -> PrintResult:
        params = (f"<LabelWriterPrintParams><Copies>{int(copies)}</Copies>"
                  f"<JobTitle>VS Production Manager</JobTitle></LabelWriterPrintParams>")
        try:
            r = self.client.post(self._url("PrintLabel"), data={
                "printerName": printer_name, "printParamsXml": params, "labelXml": label_xml, "labelSetXml": ""})
        except httpx.HTTPError as e:
            return PrintResult(False, error=f"DYMO web service error: {e}")
        if r.status_code != 200 or "false" in r.text.lower():
            return PrintResult(False, error=f"DYMO rejected the job ({r.status_code}): {r.text[:300]}")
        return PrintResult(True, job_ref=f"dymo-web-{datetime.now():%Y%m%d%H%M%S}")

    def render_preview(self, label_xml: str) -> Optional[bytes]:
        try:
            r = self.client.post(self._url("RenderLabel"), data={
                "labelXml": label_xml, "renderParamsXml": "", "printerName": ""})
            r.raise_for_status()
        except httpx.HTTPError:
            return None
        data = r.text.strip()
        if data.startswith('"'):
            data = json.loads(data)
        try:
            return base64.b64decode(data)
        except ValueError:
            return None


class DymoComPrinter:
    """Legacy DYMO Label (DLS) SDK via COM. Requires Windows + pywin32."""

    mode = "dymo_com"

    def __init__(self, work_dir: Path):
        if sys.platform != "win32":
            raise PrinterError("The DYMO COM adapter only works on Windows")
        try:
            import win32com.client  # type: ignore
        except ImportError as e:
            raise PrinterError("pywin32 is not installed (pip install pywin32)") from e
        self._win32 = win32com.client
        self.work_dir = Path(work_dir)

    def _addin(self):
        try:
            return self._win32.Dispatch("Dymo.DymoAddIn")
        except Exception as e:  # pywintypes.com_error
            raise PrinterError(f"DYMO COM SDK not available (is DYMO Label v8 installed?): {e}") from e

    def status(self) -> tuple[bool, str]:
        try:
            names = self._addin().GetDymoPrinters()
            return True, f"DYMO COM printers: {names}"
        except Exception as e:
            return False, str(e)

    def list_printers(self) -> list[PrinterInfo]:
        names = str(self._addin().GetDymoPrinters() or "")
        return [PrinterInfo(n) for n in names.split("|") if n]

    def print_label(self, label_xml: str, printer_name: str, copies: int = 1) -> PrintResult:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(suffix=".label", dir=self.work_dir)
        os.close(fd)
        Path(tmp).write_text(label_xml, encoding="utf-8")
        try:
            addin = self._addin()
            if not addin.Open(tmp):
                return PrintResult(False, error="DYMO could not open the label file")
            if printer_name and not addin.SelectPrinter(printer_name):
                return PrintResult(False, error=f"DYMO printer '{printer_name}' not found")
            addin.StartPrintJob()
            try:
                addin.Print(int(copies), False)
            finally:
                addin.EndPrintJob()
            return PrintResult(True, job_ref=f"dymo-com-{Path(tmp).stem}")
        except Exception as e:
            return PrintResult(False, error=f"DYMO COM print failed: {e}")
        finally:
            Path(tmp).unlink(missing_ok=True)

    def render_preview(self, label_xml: str) -> Optional[bytes]:
        return None
