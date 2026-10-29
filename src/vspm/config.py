"""Application configuration loaded from environment variables / `.env`."""

from __future__ import annotations

import platform
import re
from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# The worksheet name as stored in the reference workbook. Note: the first
# character is U+0384 GREEK TONOS, not an ASCII apostrophe.
DEFAULT_SHEET_NAME = "΄Ετοιμα προϊόντα"


class DriveMode(str, Enum):
    google = "google"
    local = "local"
    mock = "mock"


class OcrProvider(str, Enum):
    anthropic = "anthropic"
    mock = "mock"
    disabled = "disabled"


class PrinterMode(str, Enum):
    mock = "mock"
    dymo_web = "dymo_web"
    dymo_com = "dymo_com"
    cups = "cups"
    browser = "browser"  # printed by the employee's browser on that PC's own DYMO printer


class BarcodeMode(str, Enum):
    disabled = "disabled"
    mock = "mock"
    pilot_range = "pilot_range"
    registry = "registry"


class ExcelMode(str, Enum):
    staging = "staging"
    direct = "direct"
    registry_queue = "registry_queue"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8-sig",  # tolerate the BOM some Windows editors add
        env_prefix="VSPM_",
        extra="ignore",
    )

    language: str = "el"
    timezone: str = "Europe/Athens"
    data_dir: Path = Path("data")
    operator_name: str = ""
    computer_name: str = ""
    log_level: str = "INFO"

    drive_mode: DriveMode = DriveMode.local
    drive_folder_id: str = ""
    google_auth: str = "service_account"
    google_credentials_file: Path = Path("credentials/google_service_account.json")
    google_token_file: Path = Path("credentials/google_token.json")
    local_scan_dir: Path = Path("reference_files/scans")

    ocr_provider: OcrProvider = OcrProvider.mock
    ocr_model: str = "claude-opus-5-5"
    ocr_effort: str = "high"
    ocr_server_fallbacks: bool = True
    ocr_upload_consent: bool = False
    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")

    printer_mode: PrinterMode = PrinterMode.mock
    printer_name: str = "DYMO LabelWriter Wireless"
    dymo_web_url: str = "https://127.0.0.1:41951"
    dymo_web_verify_tls: bool = False
    # "sync": the template's tiny COUNTER object stays and prints the same text as the
    # barcode; "remove": it is removed from automated labels.
    dymo_counter_mode: str = "sync"
    # Browser printing: production labels only from a browser/PC whose TEST0001 print was
    # confirmed on the Printer page.
    require_verified_station: bool = True
    cups_options: str = "-o fit-to-page"
    cups_rotate: bool = True
    templates_config: Path = Path("config/templates.yaml")
    template_dir: Path = Path("reference_files")
    template_files: str = ""  # optional "VS=file.label;PG=file.label" when several files share a prefix

    barcode_mode: BarcodeMode = BarcodeMode.disabled
    registry_url: str = "http://localhost:8765"
    registry_api_key: str = ""
    pilot_ranges: str = ""

    excel_path: Path = Path("data/test_workbook/Σχισμενα.xlsx")
    excel_sheet: str = DEFAULT_SHEET_NAME
    excel_mode: ExcelMode = ExcelMode.staging
    excel_write_enabled: bool = False
    excel_date_format: str = "dd/mm/yyyy"
    excel_material_source: str = "canonical"
    excel_full_calc_on_load: bool = True
    excel_block_on_conflict_copies: bool = True
    # Pilot safety net: while true, the app writes ONLY to workbooks inside the
    # data folder (e.g. data/test_workbook/). Set to false only at go-live.
    excel_require_test_copy: bool = True

    customers_config: Path = Path("config/customers.yaml")
    materials_config: Path = Path("config/materials.yaml")

    @field_validator("language")
    @classmethod
    def _lang(cls, v: str) -> str:
        v = v.strip().lower() or "el"
        if v not in {"el", "en"}:
            raise ValueError("language must be 'el' or 'en'")
        return v

    @field_validator("dymo_counter_mode")
    @classmethod
    def _counter_mode(cls, v: str) -> str:
        if v not in {"sync", "remove"}:
            raise ValueError("dymo_counter_mode must be 'sync' or 'remove'")
        return v

    @field_validator("excel_material_source")
    @classmethod
    def _material_source(cls, v: str) -> str:
        if v not in {"canonical", "label"}:
            raise ValueError("excel_material_source must be 'canonical' or 'label'")
        return v

    @field_validator("ocr_effort")
    @classmethod
    def _effort(cls, v: str) -> str:
        if v not in {"low", "medium", "high", "xhigh", "max"}:
            raise ValueError("ocr_effort must be low|medium|high|xhigh|max")
        return v

    # ---- helpers -----------------------------------------------------------
    def resolve(self, p: Path) -> Path:
        return p if p.is_absolute() else (PROJECT_ROOT / p)

    @property
    def data_path(self) -> Path:
        return self.resolve(self.data_dir)

    @property
    def db_path(self) -> Path:
        return self.data_path / "vspm.sqlite3"

    @property
    def effective_computer_name(self) -> str:
        return self.computer_name or platform.node() or "unknown-pc"

    @property
    def effective_operator(self) -> str:
        return self.operator_name or "operator"

    def synced_folder_warning(self) -> str | None:
        """Local state must not live in a cloud-synced folder (SQLite corruption risk)."""
        markers = ("onedrive", "dropbox", "google drive", "googledrive", "icloud")
        path = str(self.data_path).lower()
        if any(m in path for m in markers):
            return (f"The data folder {self.data_path} is inside a cloud-synced folder. The local database can be "
                    "damaged by synchronisation. Install the app in a local folder such as C:\\VSPM (or set "
                    "VSPM_DATA_DIR to one).")
        return None

    def parsed_pilot_ranges(self) -> dict[str, tuple[int, int]]:
        """Parse `VS:900000-900999,PG:...` into {series: (start, end)}."""
        out: dict[str, tuple[int, int]] = {}
        for part in filter(None, (p.strip() for p in self.pilot_ranges.split(","))):
            m = re.fullmatch(r"([A-Z0-9]+)\s*:\s*(\d+)\s*-\s*(\d+)", part)
            if not m:
                raise ValueError(f"Invalid pilot range '{part}'")
            start, end = int(m.group(2)), int(m.group(3))
            if end < start:
                raise ValueError(f"Pilot range end < start in '{part}'")
            out[m.group(1)] = (start, end)
        return out

    def describe(self) -> dict[str, str]:
        """Settings safe to display (no secrets)."""
        d = self.model_dump()
        d["anthropic_api_key"] = "set" if self.anthropic_api_key else "not set"
        d["registry_api_key"] = "set" if self.registry_api_key else "not set"
        return {k: str(v.value if isinstance(v, Enum) else v) for k, v in d.items()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
