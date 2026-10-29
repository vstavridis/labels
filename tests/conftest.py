from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from vspm.config import Settings
from vspm.dymo.template import TemplateRegistry

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
REFERENCE = ROOT / "reference_files"

from vspm.testing import EXTRA_PARTS, FORMULAS, build_test_workbook  # noqa: E402,F401


@pytest.fixture
def workbook(tmp_path) -> Path:
    return build_test_workbook(tmp_path / "data" / "test_workbook" / "Σχισμενα.xlsx")


@pytest.fixture
def templates_yaml(tmp_path) -> Path:
    data = yaml.safe_load((ROOT / "config" / "templates.yaml").read_text(encoding="utf-8"))
    for key, spec in data["templates"].items():
        spec["files"] = [f"{key}.label"]
    p = tmp_path / "templates.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return p


@pytest.fixture
def registry(templates_yaml) -> TemplateRegistry:
    return TemplateRegistry.from_yaml(templates_yaml, FIXTURES / "labels")


@pytest.fixture
def settings(tmp_path, templates_yaml, workbook) -> Settings:
    return Settings(
        _env_file=None, data_dir=tmp_path / "data", drive_mode="mock", ocr_provider="mock", barcode_mode="mock",
        printer_mode="mock", excel_mode="direct", excel_write_enabled=True, excel_path=workbook,
        templates_config=templates_yaml, template_dir=FIXTURES / "labels", language="en",
        customers_config=FIXTURES / "customers.yaml", materials_config=ROOT / "config" / "materials.yaml",
        operator_name="tester", computer_name="TEST-PC",
    )


def with_settings(s: Settings, **kw) -> Settings:
    """Copy of settings with validated overrides."""
    data = s.model_dump(exclude={"anthropic_api_key"})
    data.update(kw)
    return Settings(_env_file=None, **data)


class Clock:
    def __init__(self, dt: datetime):
        self.dt = dt

    def __call__(self) -> datetime:
        return self.dt


@pytest.fixture
def clock() -> Clock:
    return Clock(datetime(2026, 10, 21, 9, 30, tzinfo=timezone.utc))
