"""DYMO templates uploaded into the database (Streamlit Cloud): validation, versions,
reprints with the original design, verification of browser-printed labels."""

from __future__ import annotations

import pytest
from lxml import etree

from vspm.domain.models import ProductionRow, RowStatus, TemplateKey
from vspm.dymo.template import TemplateError
from vspm.services.context import build_context
from vspm.services.verification import verify_label
from tests.conftest import FIXTURES, with_settings

STATION, PRINTER = "station-1", "DYMO LabelWriter Wireless"


@pytest.fixture
def ctx(settings, tmp_path, clock):
    # No template files at all: everything must come from uploads.
    empty = tmp_path / "no_templates"
    empty.mkdir()
    c = build_context(with_settings(settings, template_dir=empty, printer_mode="browser"))
    c.production._clock = clock
    c.browser._clock = clock
    return c


def upload(ctx, key: str, data: bytes | None = None, name: str | None = None) -> str:
    data = data if data is not None else (FIXTURES / "labels" / f"{key}.label").read_bytes()
    return ctx.templates.store.upload(TemplateKey(key), ctx.templates.spec(TemplateKey(key)), name or f"{key}.label",
                                      data, "admin")


def changed_design(key: str) -> bytes:
    """Same template with one object moved (a new design version)."""
    root = etree.fromstring((FIXTURES / "labels" / f"{key}.label").read_bytes())
    bounds = root.find(".//ObjectInfo/Bounds")
    bounds.set("X", str(int(float(bounds.get("X"))) + 10))
    return etree.tostring(root, xml_declaration=True, encoding="utf-8")


def report(ctx, job, status="submitted"):
    r = {"job_key": job.job_key, "status": status, "barcode": job.barcode, "printer": PRINTER,
         "station_id": STATION, "station_name": "ΓΡΑΦΕΙΟ", "xml_sha256": job.xml_sha256, "problems": [],
         "error": "", "at": "2026-10-21T09:30:00Z"}
    diag = {"service_present": True, "selected_printer": PRINTER, "printers": [], "user_agent": "test"}
    return ctx.browser.apply_report({"report_id": job.job_key + status, "station_id": STATION,
                                     "diagnostics": diag, "results": [r]}, "tester")


def verify_station(ctx):
    job = ctx.browser.create_test_job("PG", "s", "tester")
    report(ctx, job)
    ctx.browser.confirm_test_print(STATION, job.job_key, True, "tester")


def make_row(ctx) -> int:
    return ctx.repo.add_row(ProductionRow(
        document_id=None, row_index=1, customer_full="ΒΗΤΑΚΗΣ", customer_short="ΒΗΤΑΚΗΣ", material="ΓΑΛΒΑΝΙΖΕ",
        thickness="0,60", dimensions="34ΧΡΟΛΟ", weight="496", pieces="4", template_key=TemplateKey.PG,
        approved=True, status=RowStatus.approved))


def test_missing_template_explains_upload(ctx):
    with pytest.raises(TemplateError, match="upload it in Settings"):
        ctx.templates.load(TemplateKey.PG)


def test_upload_is_byte_exact_and_used(ctx):
    sha = upload(ctx, "PG")
    tpl = ctx.templates.load(TemplateKey.PG)
    assert tpl.sha256 == sha
    assert ctx.templates.store.by_sha(TemplateKey.PG, sha) == (FIXTURES / "labels" / "PG.label").read_bytes()
    assert "uploaded: PG.label" in ctx.templates.source(TemplateKey.PG)


def test_upload_rejects_wrong_type_and_garbage(ctx):
    with pytest.raises(TemplateError, match="counter prefix"):
        upload(ctx, "PG", (FIXTURES / "labels" / "VS.label").read_bytes(), "VS-as-PG.label")
    with pytest.raises(TemplateError):
        upload(ctx, "PG", b"not xml", "x.label")
    assert ctx.templates.store.versions() == []


def test_reprint_keeps_original_design_after_new_template(ctx):
    upload(ctx, "PG")
    verify_station(ctx)
    rid = make_row(ctx)
    first = ctx.production.start_browser_print(rid, session_id="s", station_id=STATION, printer=PRINTER)
    report(ctx, first)
    new_sha = upload(ctx, "PG", changed_design("PG"), "PG v2.label")
    assert ctx.templates.load(TemplateKey.PG).sha256 == new_sha
    lab = ctx.repo.label_for_row(rid)
    again = ctx.production.start_browser_reprint(lab["id"], session_id="s", station_id=STATION, printer=PRINTER)
    assert again.label_xml == first.label_xml  # original version, not v2
    assert [v["active"] for v in ctx.templates.store.versions(TemplateKey.PG)] == [1, 0]


def test_verify_browser_printed_label(ctx):
    upload(ctx, "PG")
    verify_station(ctx)
    rid = make_row(ctx)
    job = ctx.production.start_browser_print(rid, session_id="s", station_id=STATION, printer=PRINTER)
    report(ctx, job)
    rep = verify_label(ctx, ctx.repo.label_for_row(rid)["id"])
    checks = {c.name: c.ok for c in rep.checks}
    assert checks["template file unchanged"] is True
    assert checks["label layout preserved"] is True
    assert checks["barcode is the reserved number"] is True
    assert checks["exactly one Excel record"] is True
