"""Material abbreviations on the sheets and the customer rules (label type, short name, priority).

Customer names here are fictitious: the real rules are entered in Settings -> Customer rules."""

from __future__ import annotations

import pytest

from vspm.db.database import Database
from vspm.domain.models import ExtractedDocument, TemplateKey
from vspm.integrations.ocr.postprocess import rows_from_extraction
from vspm.services.customers import CustomerRule, CustomerService
from vspm.services.transforms import Transforms
from tests.conftest import ROOT

MATERIALS = Transforms.from_yaml(ROOT / "config" / "materials.yaml")


@pytest.mark.parametrize("raw,full", [
    ("ΗΛ/ΖΕ", "ΗΛΕΚΤΡΟΓΑΛΒΑΝΙΖΕ"), ("ηλ/ζε", "ΗΛΕΚΤΡΟΓΑΛΒΑΝΙΖΕ"), ("ΗΛ / ΖΕ", "ΗΛΕΚΤΡΟΓΑΛΒΑΝΙΖΕ"),
    ("HΛ/ZE", "ΗΛΕΚΤΡΟΓΑΛΒΑΝΙΖΕ"),  # Latin look-alike letters
    ("ΓΑ/ΖΕ", "ΓΑΛΒΑΝΙΖΕ"), ("γα/ζε", "ΓΑΛΒΑΝΙΖΕ"),
    ("ΨΕ", "ΝΤΕΚΑΠΕ"), ("ψε", "ΝΤΕΚΑΠΕ"), ("ΨΕ.", "ΝΤΕΚΑΠΕ"),
    ("ΧΑΛΚΟΣ", "ΧΑΛΚΟΣ"),  # unknown: kept as written
])
def test_material_abbreviations_become_the_full_name(raw, full):
    assert MATERIALS.normalize_material(raw) == full


def v(raw):
    return {"raw": raw, "confidence": 0.97, "uncertain": False, "is_ditto": False, "note": None}


def test_ai_reading_gives_the_full_material_name():
    customers = CustomerService(Database(":memory:"))
    doc = ExtractedDocument.model_validate({"rows": [
        {"row_number": 1, "customer": v("ΑΒΓ"), "material": v("ΗΛ/ΖΕ"), "weight": v("814")},
        {"row_number": 2, "customer": v("ΑΒΓ"), "material": v("ΨΕ"), "weight": v("816")},
        {"row_number": 3, "customer": v("ΑΒΓ"), "material": v("ΓΑ/ΖΕ"), "weight": v("810")},
    ]})
    rows = rows_from_extraction(doc, customers, transforms=MATERIALS)
    assert [r.material for r in rows] == ["ΗΛΕΚΤΡΟΓΑΛΒΑΝΙΖΕ", "ΝΤΕΚΑΠΕ", "ΓΑΛΒΑΝΙΖΕ"]


@pytest.fixture
def rules():
    svc = CustomerService(Database(":memory:"))
    svc.label_same_as_full = True
    add = lambda p, m, pat, t, short="": svc.add_rule(CustomerRule(  # noqa: E731
        id=None, priority=p, match_type=m, pattern=pat, template_key=t, short_name=short))
    add(10, "contains", "ΚΑΠΠΑΔΗΣ", TemplateKey.PG0)                   # like rule 1 (PG0)
    add(20, "exact", "ΔΕΛΤΑ - ΕΨΙΛΟΥ", TemplateKey.PG, "ΔΕΛΤΑ")          # like rule 2 (PG, short name)
    add(30, "exact", "NORTH SEA METALS LTD", TemplateKey.EX)            # an export customer
    return svc


@pytest.mark.parametrize("name,tpl", [
    ("ΚΑΠΠΑΔΗΣ", TemplateKey.PG0), ("καππάδης", TemplateKey.PG0), ("ΚΑΠΠΑΔΗΣ - ΖΗΤΑ", TemplateKey.PG0),
    ("Δέλτα-Εψιλού", TemplateKey.PG), ("ΔΕΛΤΑ – ΕΨΙΛΟΥ.", TemplateKey.PG),
    ("north sea metals ltd", TemplateKey.EX),
    ("ΟΠΟΙΟΣΔΗΠΟΤΕ ΑΛΛΟΣ", TemplateKey.VS),
])
def test_rule_order_and_tolerance_to_case_accents_and_punctuation(rules, name, tpl):
    assert rules.select(name).template_key == tpl


def test_label_shows_the_rule_short_name_else_the_name_as_entered(rules):
    assert rules.label_name("Δέλτα - Εψιλού") == "ΔΕΛΤΑ"  # the workbook's spelling (Excel F)
    assert rules.label_name("ΟΠΟΙΟΣΔΗΠΟΤΕ ΑΛΛΟΣ") == "ΟΠΟΙΟΣΔΗΠΟΤΕ ΑΛΛΟΣ"
