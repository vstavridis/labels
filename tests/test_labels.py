"""DYMO template population, barcode formatting, transforms and preview."""

from __future__ import annotations

from datetime import date

import pytest
from lxml import etree

from vspm.domain.models import LabelContent, ProductionRow, TemplateKey
from vspm.dymo import code39
from vspm.dymo.render import render_label_png
from vspm.dymo.template import LabelTemplate, TemplateError, format_barcode
from vspm.services.transforms import (
    TransformError, Transforms, build_excel_payload, build_label_content, excel_thickness, format_thickness,
)
from tests.conftest import ROOT


@pytest.fixture
def transforms():
    return Transforms.from_yaml(ROOT / "config" / "materials.yaml")


def row(**kw) -> ProductionRow:
    base = dict(id=1, customer_full="ΓΚΟΥΝΤΑ - ΠΑΓΟΥΝΗ", customer_short="ΓΚΟΥΝΤΑ", material="ΓΑΛΒΑΝΙΖΕ",
                thickness="0,6", dimensions="34ΧΡΟΛΟ", weight="496", pieces="4", location="7",
                source_roll="E1830460001", source_barcode="B-1", template_key=TemplateKey.VS, approved=True)
    base.update(kw)
    return ProductionRow(**base)


def test_counter_objects_match_brief(registry):
    expected = {TemplateKey.VS: ("VS", 11546), TemplateKey.PG: ("PG", 7029), TemplateKey.PG0: ("PG0", 2278),
                TemplateKey.EX: ("EX", 3416)}
    for key, (prefix, current) in expected.items():
        ci = registry.load(key).counter_info()
        assert ci["prefix"] == prefix and ci["current"] == current and ci["format_width"] == 3


@pytest.mark.parametrize("key", list(TemplateKey))
def test_populate_keeps_layout_and_sets_values(registry, transforms, key):
    spec = registry.spec(key)
    tpl = registry.load(key)
    r = row(template_key=key, material="ΓΑΛΒΑΝΙΖΕ")
    barcode = format_barcode(spec.barcode_prefix, 11547, 3)
    content = build_label_content(r, spec, transforms, barcode)
    xml = tpl.populate(spec, content, 11547)
    new = LabelTemplate.from_bytes(xml.encode("utf-8"))
    # same objects, same bounds
    assert [(o.tag, o.findtext("Name"), dict(b.attrib)) for o, b in tpl.objects()] == \
           [(o.tag, o.findtext("Name"), dict(b.attrib)) for o, b in new.objects()]
    # fonts of every run unchanged
    old_fonts = [dict(f.attrib) for f in tpl.find(spec.text_object).iter("Font")]
    new_fonts = [dict(f.attrib) for f in new.find(spec.text_object).iter("Font")]
    assert old_fonts == new_fonts
    text = "".join(new.text_runs(spec.text_object))
    assert content.dimensions in text and content.thickness in text and "4" in text
    assert content.material in text
    if key != TemplateKey.PG0:
        assert "ΓΚΟΥΝΤΑ" in text  # PG0 template has no customer line
    bc = new.find("BARCODE")
    assert bc.findtext("Text") == barcode
    assert not bc.findtext("LinkedObjectName")  # unlinked from DYMO's own counter
    assert new.counter_info()["current"] == 11547
    assert new.text_runs("Quantity") == ["496"]
    # original template object untouched
    assert tpl.find("BARCODE").findtext("LinkedObjectName") == "COUNTER"


def test_ex_label_uses_english_material(registry, transforms):
    spec = registry.spec(TemplateKey.EX)
    c = build_label_content(row(template_key=TemplateKey.EX), spec, transforms, "EX3417")
    assert c.material == "GALVANIZED"


def test_ex_label_without_established_translation_is_refused(registry, transforms):
    spec = registry.spec(TemplateKey.EX)
    with pytest.raises(TransformError):
        build_label_content(row(template_key=TemplateKey.EX, material="ΝΤΕΚΑΠΕ"), spec, transforms, "EX1")
    # Latin material names are used unchanged
    c = build_label_content(row(template_key=TemplateKey.EX, material="RAL 9003"), spec, transforms, "EX1")
    assert c.material == "RAL 9003"


def test_thickness_formatting():
    assert format_thickness("0,6", ",") == "0,60"
    assert format_thickness("0.6", ".") == "0.60"
    assert excel_thickness("0,70") == "0.70"
    assert format_thickness("(-)", ",") == "(-)"


def test_excel_payload_pg0_maps_to_pg_and_uses_label_values(registry, transforms):
    spec = registry.spec(TemplateKey.PG0)
    r = row(template_key=TemplateKey.PG0, customer_full="ΚΥΡΙΑΚΙΔΗΣ", customer_short="ΚΥΡΙΑΚΙΔΗΣ")
    c = build_label_content(r, spec, transforms, "PG02279")
    p = build_excel_payload(r, c, spec, transforms, date(2026, 10, 21))
    assert p.category == "PG"
    assert p.barcode == "PG02279"
    assert p.customer == "ΚΥΡΙΑΚΙΔΗΣ"
    assert p.thickness == "0.60"
    assert p.print_date == "2026-10-21"
    assert p.roll == "E1830460001" and p.location == "7"


def test_excel_material_for_ex_is_greek_canonical_by_default(registry, transforms):
    spec = registry.spec(TemplateKey.EX)
    r = row(template_key=TemplateKey.EX)
    c = build_label_content(r, spec, transforms, "EX1")
    assert build_excel_payload(r, c, spec, transforms, date(2026, 1, 1)).material == "ΓΑΛΒΑΝΙΖΕ"
    assert build_excel_payload(r, c, spec, transforms, date(2026, 1, 1), "label").material == "GALVANIZED"


def test_format_barcode():
    assert format_barcode("PG0", 2278, 3) == "PG02278"
    assert format_barcode("PG0", 5, 3) == "PG0005"
    assert format_barcode("VS", 11547, 3) == "VS11547"


def test_code39_table_valid():
    assert all(len(p) == 9 and p.count("1") == 3 for p in code39.PATTERNS.values())
    assert code39.is_encodable("PG02278") and not code39.is_encodable("ΓΚ")


def test_preview_renders_png(registry, transforms):
    spec = registry.spec(TemplateKey.VS)
    xml = registry.load(TemplateKey.VS).populate(spec, build_label_content(row(), spec, transforms, "VS1"), 1)
    png = render_label_png(xml)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_changed_template_is_detected(registry, tmp_path):
    spec = registry.spec(TemplateKey.VS)
    data = registry.load(TemplateKey.VS).path.read_bytes()
    root = etree.fromstring(data)
    text = [o for o in root.iter("TextObject") if o.findtext("Name") == "TEXT"][0]
    els = text.find("StyledText")
    els.remove(els[-1])
    tpl = LabelTemplate.from_bytes(etree.tostring(root))
    with pytest.raises(TemplateError, match="styled runs"):
        tpl.check_spec(spec)
