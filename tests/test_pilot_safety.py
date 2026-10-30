"""Regression tests for the pilot-safety fixes (re-read duplicates, duplicate guard,
print lock, test-copy guard, template detection, verification, dry run)."""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from vspm.barcodes.allocator import RegistryAllocator
from vspm.config import DEFAULT_SHEET_NAME
from vspm.domain.models import ProductionRow, RowStatus, TemplateKey
from vspm.dymo.template import TemplateError, TemplateRegistry, parse_template_files
from vspm.excel.writer import ExcelWritesDisabledError, SafeWorkbookWriter
from vspm.excel.xlsx_package import SheetEditor, XlsxPackage, save_package
from vspm.services.context import build_context
from vspm.services.documents import RereadBlocked
from vspm.services.production import AlreadyPrinted, PossibleDuplicate, PrintInProgress
from vspm.services.verification import verify_label
from vspm_registry.app import create_app
from tests.conftest import FIXTURES, ROOT, with_settings


def approve_first_row(ctx):
    doc = ctx.documents.latest(5)[0].doc_id
    rows = ctx.documents.run_ocr(doc, ctx.ocr_provider())
    r = rows[0]
    r.approved, r.flags, r.template_needs_confirmation = True, {}, False
    ctx.repo.save_row(r)
    return doc, r


@pytest.fixture
def ctx(settings, clock):
    c = build_context(settings)
    c.production._clock = clock
    return c


# ---- re-reading a partly printed document --------------------------------------------------
def test_reread_after_printing_requires_confirmation_and_never_duplicates_printed_rows(ctx):
    doc, row = approve_first_row(ctx)
    ctx.production.print_and_save(row.id)
    with pytest.raises(RereadBlocked):
        ctx.documents.run_ocr(doc, ctx.ocr_provider())
    rows = ctx.documents.run_ocr(doc, ctx.ocr_provider(), keep_printed=True)
    assert [r.row_index for r in rows] == [1, 2, 3]  # row 1 exists once: the printed one
    assert rows[0].label_id is not None and rows[0].id == row.id


# ---- duplicate guard -----------------------------------------------------------------------
def test_same_document_line_on_another_computer_is_flagged(settings, clock, tmp_path):
    api = TestClient(create_app(f"sqlite:///{tmp_path / 'reg.db'}", ["k"], "adm"))
    api.put("/v1/sequences/PG", json={"prefix": "PG", "next_value": 7030}, headers={"X-API-Key": "adm"})
    api.put("/v1/sequences/VS", json={"prefix": "VS", "next_value": 11712}, headers={"X-API-Key": "adm"})

    def pc(name):
        s = with_settings(settings, data_dir=tmp_path / name / "data", computer_name=name, database_url="",
                          excel_path=settings.excel_path, excel_require_test_copy=False)
        c = build_context(s)
        c.production._clock = clock
        client = RegistryAllocator("http://testserver", "k", client=api)
        client.client.headers.update({"X-API-Key": "k"})
        c.production.allocator = client
        return c

    pc1, pc2 = pc("PC1"), pc("PC2")
    _, r1 = approve_first_row(pc1)
    first = pc1.production.print_and_save(r1.id)
    _, r2 = approve_first_row(pc2)  # same scan, same line, other computer
    with pytest.raises(PossibleDuplicate) as e:
        pc2.production.print_and_save(r2.id)
    assert first.barcode in e.value.barcodes
    assert pc2.repo.label_for_row(r2.id) is None  # no number consumed
    second = pc2.production.print_and_save(r2.id, allow_possible_duplicate=True)  # operator: different line
    assert second.barcode != first.barcode


def test_duplicate_line_in_same_database_is_flagged(ctx):
    doc, row = approve_first_row(ctx)
    ctx.production.print_and_save(row.id)
    copy = ProductionRow(**{**row.model_dump(exclude={"id", "label_id"}), "status": RowStatus.approved})
    rid = ctx.repo.add_row(copy)  # e.g. the operator added the same line again by hand
    with pytest.raises(PossibleDuplicate):
        ctx.production.print_and_save(rid)


# ---- print lock ------------------------------------------------------------------------------
def test_concurrent_print_of_same_label_is_blocked(ctx):
    _, row = approve_first_row(ctx)
    ctx.printer.fail_next = "jam"
    out = ctx.production.print_and_save(row.id)  # reserved, print failed
    lid = out.label_id
    assert ctx.production._claim_print_lock(lid)  # another tab is printing right now
    with pytest.raises(PrintInProgress):
        ctx.production.print_and_save(row.id)
    ctx.production._release_print_lock(lid)
    assert ctx.production.print_and_save(row.id).print_ok


def test_stale_print_lock_expires(ctx, clock):
    _, row = approve_first_row(ctx)
    ctx.printer.fail_next = "jam"
    lid = ctx.production.print_and_save(row.id).label_id
    ctx.production._claim_print_lock(lid)  # app crashed while printing
    clock.dt = clock.dt + timedelta(minutes=5)
    assert ctx.production.print_and_save(row.id).print_ok


def test_second_tab_after_successful_print_gets_already_printed(ctx):
    _, row = approve_first_row(ctx)
    ctx.production.print_and_save(row.id)
    with pytest.raises(AlreadyPrinted):
        ctx.production.print_and_save(row.id)
    assert len(ctx.printer.printed) == 1


# ---- test-copy guard ------------------------------------------------------------------------
def test_writes_outside_test_copy_folder_are_refused(settings, tmp_path):
    outside = tmp_path / "OneDrive" / "Σχισμενα.xlsx"
    outside.parent.mkdir(parents=True)
    shutil.copyfile(settings.excel_path, outside)
    c = build_context(with_settings(settings, excel_path=outside))
    with pytest.raises(ExcelWritesDisabledError, match="Pilot safety"):
        c.writer.append([])
    c2 = build_context(with_settings(settings, excel_path=outside, excel_require_test_copy=False))
    assert c2.writer.is_test_copy()


# ---- template detection -----------------------------------------------------------------
def test_templates_found_by_counter_prefix_whatever_their_names(tmp_path, templates_yaml):
    for k in ("VS", "PG", "PG0", "EX"):
        shutil.copyfile(FIXTURES / "labels" / f"{k}.label", tmp_path / f"ΕΤΙΚΕΤΑ τυχαίο όνομα {k}.label")
    import yaml
    data = yaml.safe_load(templates_yaml.read_text(encoding="utf-8"))
    for spec in data["templates"].values():
        spec.pop("files", None)
    cfg = tmp_path / "t.yaml"
    cfg.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    reg = TemplateRegistry.from_yaml(cfg, tmp_path)
    for k in TemplateKey:
        assert reg.load(k).counter_info()["prefix"] == k.value
    shutil.copyfile(FIXTURES / "labels" / "VS.label", tmp_path / "copy (1).label")
    reg2 = TemplateRegistry.from_yaml(cfg, tmp_path)
    with pytest.raises(TemplateError, match="Several templates"):
        reg2.load(TemplateKey.VS)
    reg3 = TemplateRegistry.from_yaml(cfg, tmp_path, parse_template_files("VS=copy (1).label"))
    assert reg3.load(TemplateKey.VS).path.name == "copy (1).label"


def test_template_files_setting_is_validated():
    assert parse_template_files(" VS=a.label ; PG0=b c.label ") == {"VS": "a.label", "PG0": "b c.label"}
    with pytest.raises(ValueError):
        parse_template_files("VS")


# ---- verification ---------------------------------------------------------------------------
def test_verification_detects_tampered_label_and_duplicate_excel_rows(ctx, settings):
    _, row = approve_first_row(ctx)
    out = ctx.production.print_and_save(row.id)
    assert verify_label(ctx, out.label_id).ok
    lab = ctx.repo.get_label(out.label_id)
    from pathlib import Path
    p = Path(lab["label_xml_path"])
    p.write_text(p.read_text(encoding="utf-8").replace('Size="12"', 'Size="9"', 1), encoding="utf-8")
    rep = verify_label(ctx, out.label_id)
    assert not rep.ok and any(c.name == "label layout preserved" and c.ok is False for c in rep.checks)
    p.write_text(ctx.production._label_xml(lab), encoding="utf-8")  # restore
    # Someone pastes the same row into the workbook by hand -> two records
    pkg = XlsxPackage(settings.excel_path.read_bytes())
    ed = SheetEditor(pkg, DEFAULT_SHEET_NAME)
    ed.write_row(6, {"H": out.barcode}, {}, {}, "dd/mm/yyyy", 5)
    settings.excel_path.write_bytes(save_package(pkg))
    rep = verify_label(ctx, out.label_id)
    assert any(c.name == "exactly one Excel record" and c.ok is False for c in rep.checks)


# ---- pilot tools ------------------------------------------------------------------------------
def test_pilot_dryrun_synthetic_passes_and_leaves_templates_untouched(settings, tmp_path):
    from vspm.pilot import pilot_dryrun
    res = pilot_dryrun(settings, None, True, work_dir=tmp_path / "dry")
    assert res.ok, "\n".join(res.lines)
    assert any("template file(s) unchanged" in line for line in res.lines)


def test_pilot_dryrun_never_modifies_the_source_workbook(settings, tmp_path):
    from vspm.pilot import pilot_dryrun, sha256_file
    before = sha256_file(settings.excel_path)
    res = pilot_dryrun(settings, settings.excel_path, False, work_dir=tmp_path / "dry2")
    assert res.ok, "\n".join(res.lines)
    assert sha256_file(settings.excel_path) == before


def test_dymo_template_test_does_not_touch_templates(ctx, tmp_path):
    from vspm.pilot import dymo_template_test
    res = dymo_template_test(ctx, ["VS", "PG", "PG0", "EX"], True, tmp_path / "dt")
    assert all(r.unchanged and r.printed for r in res)
    assert {r.counter_before for r in res} == {11546, 7029, 2278, 3416}
    for r in res:
        assert "TEST0001" in r.label_file.read_text(encoding="utf-8")
