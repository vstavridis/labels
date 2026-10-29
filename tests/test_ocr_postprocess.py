"""Greek handwritten document parsing from mocked AI responses."""

from __future__ import annotations

import json

import pytest

from vspm.db.database import Database
from vspm.domain.models import ExtractedDocument, TemplateKey
from vspm.domain.validation import validate_row
from vspm.integrations.ocr.postprocess import rows_from_extraction
from vspm.integrations.ocr.providers import DEMO_EXTRACTION, MockOcr
from vspm.integrations.ocr.base import OcrInput
from vspm.services.customers import CustomerService
from tests.conftest import ROOT


def v(raw, conf=0.97, uncertain=False, ditto=False, note=None):
    return {"raw": raw, "confidence": conf, "uncertain": uncertain, "is_ditto": ditto, "note": note}


@pytest.fixture
def customers():
    return CustomerService(Database(":memory:"), ROOT / "config" / "customers.yaml")


def test_three_production_entries_give_three_rows(customers):
    rows = rows_from_extraction(ExtractedDocument.model_validate(DEMO_EXTRACTION), customers)
    assert len(rows) == 3
    assert [r.row_index for r in rows] == [1, 2, 3]
    assert [r.weight for r in rows] == ["496", "502", "730"]


def test_shared_fields_inherited_from_header(customers):
    rows = rows_from_extraction(ExtractedDocument.model_validate(DEMO_EXTRACTION), customers)
    for r in rows:
        assert r.customer_full == "ΓΚΟΥΝΤΑ - ΠΑΓΟΥΝΗ"
        assert r.material == "ΓΑΛΒΑΝΙΖΕ"
        assert r.thickness == "0,60"  # comma decimal preserved for display
        assert r.location == "7"
        assert r.flags["material"] == "info:header"


def test_ditto_mark_copies_previous_row_and_is_marked(customers):
    rows = rows_from_extraction(ExtractedDocument.model_validate(DEMO_EXTRACTION), customers)
    assert rows[1].dimensions == "34ΧΡΟΛΟ"
    assert rows[1].flags["dimensions"] == "info:ditto"
    assert rows[1].ocr_original["dimensions"] is None  # original OCR value kept separately


def test_ditto_on_numeric_field_must_be_confirmed(customers):
    data = {"rows": [
        {"row_number": 1, "customer": v("ΑΒΓ"), "weight": v("100"), "pieces": v("2")},
        {"row_number": 2, "customer": v(None, ditto=True), "weight": v(None, ditto=True), "pieces": v("3")},
    ]}
    rows = rows_from_extraction(ExtractedDocument.model_validate(data), customers)
    assert rows[1].weight == "100"
    assert rows[1].flags["weight"] == "uncertain"
    assert rows[1].customer_full == "ΑΒΓ"


def test_uncertain_values_are_flagged_and_block_printing(customers):
    rows = rows_from_extraction(ExtractedDocument.model_validate(DEMO_EXTRACTION), customers)
    r3 = rows[2]
    assert r3.flags["weight"] == "uncertain"
    assert r3.flags["note:weight"] == "730 or 750"
    errors = validate_row(r3)
    assert "weight" in errors


def test_missing_values_are_not_invented(customers):
    data = {"rows": [{"row_number": 1, "customer": v("ΤΕΣΤ"), "material": v("ΓΑΛΒΑΝΙΖΕ"),
                      "weight": v(None, 0.0, True, note="illegible")}]}
    rows = rows_from_extraction(ExtractedDocument.model_validate(data), customers)
    assert rows[0].weight == ""
    assert validate_row(rows[0])["weight"] == "required"


def test_source_barcode_is_separate_from_label_barcode(customers):
    rows = rows_from_extraction(ExtractedDocument.model_validate(DEMO_EXTRACTION), customers)
    assert rows[0].source_barcode == "B-101"
    assert rows[0].label_id is None  # no generated barcode until printing


def test_multi_page_rows_are_ordered_by_page(customers):
    data = {"rows": [
        {"row_number": 1, "page": 2, "customer": v("Β"), "weight": v("20")},
        {"row_number": 1, "page": 1, "customer": v("Α"), "weight": v("10")},
    ], "page_count": 2}
    rows = rows_from_extraction(ExtractedDocument.model_validate(data), customers)
    assert [r.weight for r in rows] == ["10", "20"]


def test_template_suggested_from_customer_rules(customers):
    data = {"rows": [
        {"row_number": 1, "customer": v("ΚΥΡΙΑΚΙΔΗΣ")},
        {"row_number": 2, "customer": v("ΓΚΟΥΝΤΑ - ΠΑΓΟΥΝΗ")},
        {"row_number": 3, "customer": v("NORTHWIND LTD")},
        {"row_number": 4, "customer": v("ΠΑΠΑΔΟΠΟΥΛΟΣ")},
    ]}
    rows = rows_from_extraction(ExtractedDocument.model_validate(data), customers)
    assert [r.template_key for r in rows] == [TemplateKey.PG0, TemplateKey.PG, TemplateKey.EX, TemplateKey.VS]
    assert rows[2].template_needs_confirmation  # Latin name: EX only suggested
    assert rows[1].customer_short == "ΓΚΟΥΝΤΑ"


def test_weight_unit_suffix_removed(customers):
    data = {"rows": [{"row_number": 1, "weight": v("496 kg")}]}
    rows = rows_from_extraction(ExtractedDocument.model_validate(data), customers)
    assert rows[0].weight == "496"


def test_mock_provider_reads_fixture_file(tmp_path):
    f = tmp_path / "x.json"
    f.write_text(json.dumps({"rows": [{"row_number": 1, "weight": v("5")}]}), encoding="utf-8")
    out = MockOcr(f).extract(OcrInput(b"", "image/png", "a.png"))
    assert out.document.rows[0].weight.raw == "5"
