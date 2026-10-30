"""Browser print jobs on the server side: persistence before submission, idempotency,
tab safety, reprints, unanswered jobs, manual .label fallback, station verification."""

from __future__ import annotations

from datetime import timedelta

import pytest
from lxml import etree

from vspm.domain.models import ProductionRow, RowStatus, TemplateKey
from vspm.dymo.template import check_prepared_label, xml_sha256
from vspm.excel.xlsx_package import SheetEditor, XlsxPackage
from vspm.services.browser_print import JobInProgress, TEST_BARCODE
from vspm.services.context import build_context
from vspm.services.production import AlreadyPrinted, PreviousPrintUnknown, StationNotVerified
from tests.conftest import with_settings

STATION, PRINTER = "station-1", "DYMO LabelWriter Wireless"


@pytest.fixture
def ctx(settings, clock):
    c = build_context(with_settings(settings, printer_mode="browser"))
    c.production._clock = clock
    c.browser._clock = clock
    return c


def report(ctx, job, status="submitted", **kw):
    r = {"job_key": job.job_key, "status": status, "barcode": kw.pop("barcode", job.barcode), "printer": PRINTER,
         "station_id": STATION, "station_name": "ΓΡΑΦΕΙΟ", "xml_sha256": kw.pop("sha", job.xml_sha256),
         "problems": [], "error": kw.pop("error", ""), "at": "2026-10-21T09:30:00Z"}
    diag = {"service_present": True, "selected_printer": PRINTER, "printers": [], "user_agent": "test"}
    return ctx.browser.apply_report({"report_id": "r", "station_id": STATION, "station_name": "ΓΡΑΦΕΙΟ",
                                     "diagnostics": diag, "results": [r]}, "tester")


def verify_station(ctx):
    job = ctx.browser.create_test_job("VS", "sess", "tester")
    report(ctx, job)
    ctx.browser.confirm_test_print(STATION, job.job_key, True, "tester")
    assert ctx.browser.station_verified(STATION, PRINTER)


def make_row(ctx, **kw) -> int:
    base = dict(document_id=None, row_index=1, customer_full="ΒΗΤΑΚΗΣ - ΓΑΜΜΑΤΟΥ", customer_short="ΒΗΤΑΚΗΣ",
                material="ΓΑΛΒΑΝΙΖΕ", thickness="0,60", dimensions="34ΧΡΟΛΟ", weight="496", pieces="4",
                template_key=TemplateKey.PG, approved=True, status=RowStatus.approved)
    base.update(kw)
    return ctx.repo.add_row(ProductionRow(**base))


def start(ctx, rid, **kw):
    return ctx.production.start_browser_print(rid, session_id="sess-A", station_id=STATION, printer=PRINTER,
                                              operator="tester", **kw)


def excel_rows(settings, barcode):
    return SheetEditor(XlsxPackage(settings.excel_path.read_bytes()), settings.excel_sheet).find_rows_with_value(
        "H", barcode)


# ---- TEST0001 -----------------------------------------------------------------------------
def test_test_job_allocates_nothing_and_is_idempotent(ctx):
    job = ctx.browser.create_test_job("PG0", "sess", "tester")
    assert job.barcode == TEST_BARCODE and check_prepared_label(job.label_xml, TEST_BARCODE) == []
    assert ctx.db.one("SELECT COUNT(*) FROM labels")[0] == 0
    assert ctx.db.one("SELECT COUNT(*) FROM local_reservations")[0] == 0
    out = report(ctx, job)
    assert out.acked == [job.job_key]
    assert ctx.browser.job(job.job_key)["status"] == "submitted"
    report(ctx, job, status="failed")  # late/duplicate report never changes a final job
    assert ctx.browser.job(job.job_key)["status"] == "submitted"


def test_station_must_be_verified_before_any_barcode_is_reserved(ctx):
    rid = make_row(ctx)
    with pytest.raises(StationNotVerified):
        start(ctx, rid)
    assert ctx.repo.label_for_row(rid) is None
    assert ctx.db.one("SELECT COUNT(*) FROM local_reservations")[0] == 0
    job = ctx.browser.create_test_job("VS", "sess", "tester")
    report(ctx, job)
    ctx.browser.confirm_test_print(STATION, job.job_key, False, "tester")  # TEST0001 came out wrong
    with pytest.raises(StationNotVerified):
        start(ctx, rid)
    assert not ctx.browser.station_verified(STATION, "Another printer")


# ---- production label ------------------------------------------------------------------------
def test_barcode_is_persisted_before_the_job_and_job_prints_exactly_it(ctx, settings):
    verify_station(ctx)
    rid = make_row(ctx)
    job = start(ctx, rid)
    label = ctx.repo.label_for_row(rid)
    assert label is not None and label["print_status"] == "barcode_reserved"  # committed before printing
    assert job.barcode == label["barcode"] == "PG990000" and job.label_id == label["id"]
    assert check_prepared_label(job.label_xml, job.barcode) == []
    root = etree.fromstring(job.label_xml.encode("utf-8"))
    assert all(not (b.findtext("LinkedObjectName") or "").strip() for b in root.iter("BarcodeObject"))
    assert excel_rows(settings, job.barcode) == []  # nothing in Excel before the print is submitted


def test_submitted_report_records_print_and_exactly_one_excel_row(ctx, settings):
    verify_station(ctx)
    rid = make_row(ctx)
    job = start(ctx, rid)
    out = report(ctx, job)
    assert any("PG990000" in m for _, m in out.messages)
    lab = ctx.repo.label_for_row(rid)
    assert lab["print_status"] == "print_unconfirmed" and lab["first_printed_at"]
    assert excel_rows(settings, "PG990000") == [5]
    report(ctx, job)  # the browser re-sends its outbox after a reload
    assert excel_rows(settings, "PG990000") == [5]
    attempts = ctx.repo.print_attempts(lab["id"])
    assert [(a["kind"], a["status"], a["printer_mode"]) for a in attempts] == [("initial", "submitted", "browser")]
    with pytest.raises(AlreadyPrinted):
        start(ctx, rid)


def test_second_tab_cannot_start_a_parallel_job(ctx):
    verify_station(ctx)
    rid = make_row(ctx)
    start(ctx, rid)
    with pytest.raises(JobInProgress):
        ctx.production.start_browser_print(rid, session_id="sess-B", station_id=STATION, printer=PRINTER,
                                           operator="other")
    assert ctx.db.one("SELECT COUNT(*) FROM print_jobs WHERE status='pending'")[0] == 1


def test_reprint_reuses_barcode_and_never_adds_excel_row(ctx, settings):
    verify_station(ctx)
    rid = make_row(ctx)
    job = start(ctx, rid)
    report(ctx, job)
    before = settings.excel_path.read_bytes()
    lab = ctx.repo.label_for_row(rid)
    rj = ctx.production.start_browser_reprint(lab["id"], session_id="s", station_id=STATION, printer=PRINTER)
    assert rj.kind == "reprint" and rj.barcode == job.barcode and rj.label_xml == job.label_xml
    report(ctx, rj)
    assert settings.excel_path.read_bytes() == before
    assert [a["kind"] for a in ctx.repo.print_attempts(lab["id"])] == ["initial", "reprint"]


def test_failed_print_keeps_barcode_and_retry_uses_it(ctx, settings):
    verify_station(ctx)
    rid = make_row(ctx)
    job = start(ctx, rid)
    report(ctx, job, status="failed", error="Failed to execute webservice command: PrintLabel. Error: 500")
    lab = ctx.repo.label_for_row(rid)
    assert lab["print_status"] == "error" and excel_rows(settings, job.barcode) == []
    retry = start(ctx, rid)
    assert retry.kind == "retry" and retry.barcode == job.barcode
    report(ctx, retry)
    assert excel_rows(settings, job.barcode) == [5]


def test_rejected_label_is_recorded_as_error(ctx, settings):
    verify_station(ctx)
    rid = make_row(ctx)
    job = start(ctx, rid)
    report(ctx, job, barcode="PG999999")  # browser saw another barcode
    assert ctx.browser.job(job.job_key)["status"] == "rejected"
    lab = ctx.repo.label_for_row(rid)
    assert lab["print_status"] == "error" and not lab["first_printed_at"]
    assert excel_rows(settings, job.barcode) == []


def test_report_with_other_content_hash_is_not_recorded_as_printed(ctx, settings):
    verify_station(ctx)
    rid = make_row(ctx)
    job = start(ctx, rid)
    report(ctx, job, sha="0" * 64)
    assert ctx.browser.job(job.job_key)["status"] == "uncertain"
    assert ctx.repo.label_for_row(rid)["print_status"] == "barcode_reserved"
    assert excel_rows(settings, job.barcode) == []


def test_unanswered_job_requires_operator_decision(ctx, settings, clock):
    verify_station(ctx)
    rid = make_row(ctx)
    job = start(ctx, rid)
    clock.dt = clock.dt + timedelta(minutes=10)  # the browser never answered (tab closed)
    with pytest.raises(PreviousPrintUnknown):
        start(ctx, rid)
    assert ctx.browser.job(job.job_key)["status"] == "expired"
    out = ctx.production.confirm_unanswered_printed(ctx.repo.label_for_row(rid)["id"], "tester")
    assert out.print_status == "printed_confirmed" and excel_rows(settings, job.barcode) == [5]


def test_unanswered_job_not_printed_then_print_again_same_barcode(ctx, clock):
    verify_station(ctx)
    rid = make_row(ctx)
    job = start(ctx, rid)
    clock.dt = clock.dt + timedelta(minutes=10)
    again = start(ctx, rid, previous_not_printed=True)
    assert again.barcode == job.barcode and ctx.browser.job(job.job_key)["status"] == "cancelled"


def test_uncertain_report_does_not_mark_printed(ctx):
    verify_station(ctx)
    rid = make_row(ctx)
    job = start(ctx, rid)
    out = report(ctx, job, status="uncertain", error="page reloaded")
    assert any(level == "warning" for level, _ in out.messages)
    assert ctx.repo.label_for_row(rid)["print_status"] == "barcode_reserved"


# ---- manual fallback ------------------------------------------------------------------------
def test_manual_label_file_has_reserved_barcode_and_confirmation_records_once(ctx, settings):
    rid = make_row(ctx)
    lab = ctx.production.prepare_label(rid, "tester")  # reserve + persist, no browser needed
    name, data = ctx.production.manual_label_file(lab["id"])
    xml = data.decode("utf-8")
    assert name == f"{lab['barcode']}.label" and check_prepared_label(xml, lab["barcode"]) == []
    assert ctx.db.one("SELECT COUNT(*) FROM print_jobs")[0] == 0  # nothing recorded until downloaded
    key = ctx.production.record_manual_download(lab["id"], "tester")
    assert ctx.production.record_manual_download(lab["id"], "tester") == key
    out = ctx.production.confirm_manual_print(lab["id"], True, "tester")
    assert out.print_status == "printed_confirmed" and excel_rows(settings, lab["barcode"]) == [5]
    assert ctx.browser.job(key)["xml_sha256"] == xml_sha256(xml)


def test_counter_can_be_removed_from_automated_labels(settings, clock):
    c = build_context(with_settings(settings, printer_mode="browser", dymo_counter_mode="remove"))
    job = c.browser.create_test_job("EX", "s", "t")
    root = etree.fromstring(job.label_xml.encode("utf-8"))
    assert list(root.iter("CounterObject")) == [] and check_prepared_label(job.label_xml, TEST_BARCODE) == []


def test_simultaneous_first_reports_from_one_station_do_not_crash(ctx):
    """Two tabs/reruns of a new PC report at the same moment (seen on CI: UNIQUE constraint failed)."""
    import threading
    errors, barrier = [], threading.Barrier(8)
    value = {"report_id": "x", "station_id": "new-station", "station_name": "ΑΠΟΘΗΚΗ",
             "diagnostics": {"service_present": True, "selected_printer": PRINTER, "printers": []}, "results": []}

    def go():
        barrier.wait()
        try:
            ctx.browser.apply_report(dict(value), "tester")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert ctx.browser.station("new-station")["printer_name"] == PRINTER
