from __future__ import annotations

import pytest

from vspm.db.database import Database
from vspm.domain.models import TemplateKey
from vspm.services.customers import CustomerRule, CustomerService, normalize_key, script_profile
from tests.conftest import ROOT


@pytest.fixture
def svc():
    return CustomerService(Database(":memory:"), ROOT / "tests" / "fixtures" / "customers.yaml")


@pytest.mark.parametrize("a,b", [
    ("ΒΗΤΑΚΗΣ - ΓΑΜΜΑΤΟΥ", "βητάκης-γαμμάτου"),
    ("ΒΗΤΑΚΗΣ - ΓΑΜΜΑΤΟΥ", "  ΒΗΤΑΚΗΣ   –  ΓΑΜΜΑΤΟΥ. "),
    ("ΠΑΠΑΪΩΑΝΝΟΥ", "ΠΑΠΑΙΩΑΝΝΟΥ"),
    ("ΑΛΦΑΔΗΣ", "AΛΦAΔΗΣ"),  # Latin A typed inside a Greek word
])
def test_normalization_equivalences(a, b):
    assert normalize_key(a) == normalize_key(b)


def test_greek_and_latin_names_are_distinct():
    assert normalize_key("METAL") != normalize_key("ΜΕΤΑΛ")
    assert script_profile("NORTHWIND LTD") == "latin"
    assert script_profile("ΒΗΤΑΚΗΣ") == "greek"
    assert script_profile("ΑΛΦAΔΗΣ") == "mixed"


def test_vs_pg_pg0_ex_selection(svc):
    assert svc.select("ΑΛΦΑΔΗΣ").template_key == TemplateKey.PG0
    assert svc.select("Βητάκης - Γαμμάτου").template_key == TemplateKey.PG
    assert svc.select("Βητάκης - Γαμμάτου").short_name == "ΒΗΤΑΚΗΣ"
    ex = svc.select("SOME COMPANY LTD")
    assert ex.template_key == TemplateKey.EX and ex.needs_confirmation
    vs = svc.select("ΠΑΠΑΔΟΠΟΥΛΟΣ")
    assert vs.template_key == TemplateKey.VS and not vs.needs_confirmation


def test_specialized_rules_take_precedence(svc):
    # "contains ΓΑΜΜΑΤΟΥ" (priority 30) would match too, but the exact rule (20) wins.
    sel = svc.select("ΒΗΤΑΚΗΣ - ΓΑΜΜΑΤΟΥ")
    assert not sel.needs_confirmation
    other = svc.select("ΔΟΚΙΜΗ ΓΑΜΜΑΤΟΥ")
    assert other.template_key == TemplateKey.PG and other.needs_confirmation


def test_latin_customer_mapped_to_vs_by_rule(svc):
    svc.add_rule(CustomerRule(None, 5, "exact", "ACME FILTERS", TemplateKey.VS, "ACME FILTERS"))
    sel = svc.select("Acme Filters")
    assert sel.template_key == TemplateKey.VS and not sel.needs_confirmation


def test_mixed_script_requires_confirmation(svc):
    sel = svc.select("ΠΑΠΑΔΟΠΟΥΛΟΣ AE")
    assert sel.needs_confirmation


def test_unmapped_short_name_is_full_name_not_invented(svc):
    sel = svc.select("ΔΟΚΙΜΑΣΤΙΚΗ ΑΒΕΕ")
    assert sel.short_name == "ΔΟΚΙΜΑΣΤΙΚΗ ΑΒΕΕ" and not sel.short_name_mapped


def test_regex_rule_and_inactive_rule(svc):
    rid = svc.add_rule(CustomerRule(None, 1, "regex", r"^ΤΕΣΤ\b", TemplateKey.EX, "T", active=False))
    assert svc.select("ΤΕΣΤ ΑΕ").template_key == TemplateKey.VS
    r = [x for x in svc.rules() if x.id == rid][0]
    r.active = True
    svc.update_rule(r)
    assert svc.select("ΤΕΣΤ ΑΕ").template_key == TemplateKey.EX


def test_empty_name_requires_confirmation(svc):
    assert svc.select("  ").needs_confirmation
