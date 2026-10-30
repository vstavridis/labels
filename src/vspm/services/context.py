"""Builds all services from Settings. Integration failures are captured so the
UI can still start and show what needs configuring."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from vspm.barcodes.allocator import (
    DatabaseRegistryAllocator,
    BarcodeAllocator, DisabledAllocator, LocalRangeAllocator, RegistryAllocator,
)
from vspm.config import BarcodeMode, DriveMode, OcrProvider as OcrMode, PrinterMode, Settings
from vspm.db.database import DatabaseBase, open_database
from vspm.db.repository import Repository
from vspm.dymo.printers import CupsPrinter, DymoComPrinter, DymoWebServicePrinter, LabelPrinter, MockPrinter
from vspm.dymo.template import TemplateStore, TemplateRegistry, parse_template_files
from vspm.excel.writer import SafeWorkbookWriter
from vspm.integrations.documents import (
    DocumentSource, GoogleDriveSource, LocalFolderSource, MockSource,
)
from vspm.integrations.ocr.providers import AnthropicOcr, MockOcr
from vspm.services.browser_print import BrowserPrintService
from vspm.services.customers import CustomerService
from vspm.services.documents import DocumentService
from vspm.services.production import ProductionService
from vspm.services.transforms import Transforms

log = logging.getLogger(__name__)

MOCK_RANGES = {"VS": (990000, 999999), "PG": (990000, 999999), "PG0": (99000, 99999), "EX": (990000, 999999)}


@dataclass
class AppContext:
    settings: Settings
    db: DatabaseBase
    repo: Repository
    customers: CustomerService
    templates: TemplateRegistry
    transforms: Transforms
    allocator: BarcodeAllocator
    printer: LabelPrinter
    writer: SafeWorkbookWriter
    production: ProductionService
    documents: Optional[DocumentService]
    registry_client: Optional[RegistryAllocator]
    problems: dict[str, str] = field(default_factory=dict)
    recovery_notes: list[str] = field(default_factory=list)
    browser: Optional[BrowserPrintService] = None

    def ocr_provider(self):
        s = self.settings
        if s.ocr_provider == OcrMode.disabled:
            raise RuntimeError("AI reading is disabled (VSPM_OCR_PROVIDER=disabled)")
        if s.ocr_provider == OcrMode.mock:
            return MockOcr()
        return AnthropicOcr(api_key=s.anthropic_api_key, model=s.ocr_model, effort=s.ocr_effort,
                            upload_consent=s.ocr_upload_consent, server_fallbacks=s.ocr_server_fallbacks)


def _widths(templates: TemplateRegistry) -> tuple[dict[str, str], dict[str, int]]:
    prefixes, widths = {}, {}
    for key, spec in templates.specs.items():
        prefixes[spec.barcode_prefix] = spec.barcode_prefix
        try:
            ci = templates.load(key).counter_info(spec.counter_object) or {}
            widths[spec.barcode_prefix] = ci.get("format_width", 3) if ci.get("leading_zeros", True) else 0
        except Exception:
            widths[spec.barcode_prefix] = 3
    return prefixes, widths


def build_context(s: Settings) -> AppContext:
    problems: dict[str, str] = {}
    if s.synced_folder_warning():
        problems["data_dir"] = s.synced_folder_warning()
    db = open_database(s.db_path, s.database_url or None)
    repo = Repository(db)
    customers = CustomerService(db, s.resolve(s.customers_config))
    if not s.resolve(s.customers_config).exists() and not customers.rules():
        problems["customer_rules"] = (f"{s.customers_config} not found: no customer rules, every customer gets VS. "
                                      "Copy config/customers.example.yaml and enter your real customers.")
    try:
        overrides = parse_template_files(s.template_files)
    except ValueError as e:
        problems["template_files"] = str(e)
        overrides = {}
    templates = TemplateRegistry.from_yaml(s.resolve(s.templates_config), s.resolve(s.template_dir), overrides)
    templates.store = TemplateStore(db)  # uploaded templates (Settings) take precedence over files
    for key in templates.specs:
        try:
            templates.load(key)
        except Exception as e:
            problems[f"template_{key.value}"] = str(e)
    transforms = Transforms.from_yaml(s.resolve(s.materials_config))

    registry_client = None
    if s.registry_url and s.registry_api_key:
        registry_client = RegistryAllocator(s.registry_url, s.registry_api_key)

    allocator: BarcodeAllocator = DisabledAllocator()
    try:
        prefixes, widths = _widths(templates)
        if s.barcode_mode == BarcodeMode.registry:
            if registry_client is None:
                raise RuntimeError("VSPM_REGISTRY_URL and VSPM_REGISTRY_API_KEY are required for registry mode")
            allocator = registry_client
        elif s.barcode_mode == BarcodeMode.database:
            url = s.registry_database_url or s.database_url
            if not url:
                raise RuntimeError("VSPM_BARCODE_MODE=database needs VSPM_DATABASE_URL (the shared PostgreSQL)")
            allocator = DatabaseRegistryAllocator(url)
        elif s.barcode_mode == BarcodeMode.pilot_range:
            ranges = s.parsed_pilot_ranges()
            if not ranges:
                raise RuntimeError("VSPM_PILOT_RANGES is empty")
            allocator = LocalRangeAllocator(db, "pilot_range", ranges, prefixes, widths)
        elif s.barcode_mode == BarcodeMode.mock:
            allocator = LocalRangeAllocator(db, "mock", MOCK_RANGES, prefixes, widths)
    except Exception as e:
        problems["barcodes"] = str(e)
        allocator = DisabledAllocator()

    printer: LabelPrinter
    try:
        if s.printer_mode == PrinterMode.dymo_web:
            printer = DymoWebServicePrinter(s.dymo_web_url, s.dymo_web_verify_tls)
        elif s.printer_mode == PrinterMode.browser:
            printer = MockPrinter(s.data_path / "unused_server_printer")  # never used: the browser prints
        elif s.printer_mode == PrinterMode.cups:
            printer = CupsPrinter(s.cups_options, rotate=s.cups_rotate)
        elif s.printer_mode == PrinterMode.dymo_com:
            printer = DymoComPrinter(s.data_path / "dymo_tmp")
        else:
            printer = MockPrinter(s.data_path / "mock_printer")
    except Exception as e:
        problems["printer"] = str(e)
        printer = MockPrinter(s.data_path / "mock_printer")
        problems["printer"] += " - using MOCK printer instead"

    writer = SafeWorkbookWriter(s.resolve(s.excel_path), s.excel_sheet, backup_dir=s.data_path / "excel_backups",
                                date_format=s.excel_date_format, full_calc_on_load=s.excel_full_calc_on_load,
                                block_on_conflict_copies=s.excel_block_on_conflict_copies,
                                write_enabled=s.excel_write_enabled,
                                allowed_root=s.data_path if s.excel_require_test_copy else None)

    source: Optional[DocumentSource] = None
    try:
        if s.drive_mode == DriveMode.google:
            source = GoogleDriveSource(s.drive_folder_id, s.google_auth, s.resolve(s.google_credentials_file),
                                       s.resolve(s.google_token_file))
        elif s.drive_mode == DriveMode.local:
            source = LocalFolderSource(s.resolve(s.local_scan_dir))
        else:
            source = MockSource()
    except Exception as e:
        problems["documents"] = str(e)
    documents = DocumentService(repo, source, customers, s.data_path / "documents") if source else None

    production = ProductionService(settings=s, db=db, repo=repo, templates=templates, transforms=transforms,
                                   allocator=allocator, printer=printer, writer=writer,
                                   registry_client=registry_client)
    ctx = AppContext(s, db, repo, customers, templates, transforms, allocator, printer, writer, production, documents,
                     registry_client, problems)
    ctx.browser = BrowserPrintService(db, templates, counter_mode=s.dymo_counter_mode)
    production.browser = ctx.browser
    ctx.browser.on_label_result = production.apply_browser_result
    try:
        ctx.recovery_notes = production.recover()
    except Exception as e:
        problems["recovery"] = str(e)
    return ctx
