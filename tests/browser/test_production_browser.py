"""Phase 2: production labels printed by the browser (mock barcodes, test workbook)."""

from __future__ import annotations

import re

import httpx
import pytest
from lxml import etree

from tests.browser.conftest import new_page
from tests.browser.test_dymo_bridge_browser import BRIDGE, jobs, open_printer_page, wait_printer_selected, wait_station_verified


@pytest.fixture
def app_ctx(app_server):
    """Direct access to the running app's database (same files) to prepare rows."""
    from vspm.config import Settings
    from vspm.services.context import build_context
    env = {k[5:].lower(): v for k, v in app_server["env"].items() if k.startswith("VSPM_")}
    return build_context(Settings(_env_file=None, **env))


_DOC_SEQ = iter(range(10, 60))


def make_document_row(ctx, weight="496"):
    """A new document that sorts first in the page's document list (later upload time than any other)."""
    from vspm.domain.models import ProductionRow, RowStatus, TemplateKey
    import uuid
    doc = ctx.repo.upsert_document(source="mock", source_file_id="bt-" + uuid.uuid4().hex, source_revision="r1",
                                   name="BROWSER_TEST_" + uuid.uuid4().hex[:6] + ".png", mime_type="image/png",
                                   uploaded_at=f"2999-01-01T00:{next(_DOC_SEQ):02d}:00+00:00")
    rid = ctx.repo.add_row(ProductionRow(document_id=doc, row_index=1, customer_full="ΑΛΦΑΔΗΣ",
                                         customer_short="ΑΛΦΑΔΗΣ", material="ΓΑΛΒΑΝΙΖΕ", thickness="0,60",
                                         dimensions="34ΧΡΟΛΟ", weight=weight, pieces="4",
                                         template_key=TemplateKey.PG0, approved=True, status=RowStatus.approved))
    return doc, rid


def verify_this_browser(page, app_server):
    bridge = open_printer_page(page, app_server)
    bridge.get_by_test_id("service").filter(has_text="βρέθηκε").wait_for()
    wait_printer_selected(page, bridge)
    page.get_by_role("button", name="Εκτύπωση TEST0001").click()
    bridge.locator('[data-testid="job-status"][data-state="submitted"]').wait_for()
    page.get_by_role("button", name=re.compile("Ναι — σωστή ετικέτα")).click()
    wait_station_verified(page)


def open_print_page(page, app_server, doc_name_part="BROWSER_TEST_"):
    # A sidebar click made while Streamlit is still drawing the previous page can be ignored, and a late
    # report from the previous page's bridge can rerun that page: retry until the print page stays.
    for attempt in range(4):
        page.get_by_role("link", name=re.compile("Ετικέτα & εκτύπωση")).click()
        try:
            page.get_by_text("Επιλογή εγγράφου").first.wait_for(timeout=15000)
            page.wait_for_timeout(1500)
            if page.get_by_text("Επιλογή εγγράφου").first.is_visible():
                break
        except Exception:
            pass
        if attempt == 3:
            raise AssertionError("could not open the print page")
    page.locator(BRIDGE).first.wait_for()
    return page.frame_locator(BRIDGE).first


def excel_rows(app_ctx, barcode):
    return app_ctx.writer.find_barcodes([barcode])[barcode]


def test_unverified_browser_cannot_print_production_labels(browser, app_server, dymo_reset, app_ctx):
    make_document_row(app_ctx)
    ctx, page = new_page(browser, app_server, allow_local_network=True)
    page.goto(app_server["url"] + "/printer")
    page.get_by_text("Αυτός ο υπολογιστής").first.wait_for()
    open_print_page(page, app_server)
    page.get_by_text("δεν έχει επιβεβαιωθεί").first.wait_for()
    assert page.get_by_role("button", name=re.compile("Εκτύπωση & Αποθήκευση")).is_disabled()
    assert jobs() == []
    ctx.close()


def test_print_save_prints_reserved_barcode_once_and_one_excel_row(browser, app_server, dymo_reset, app_ctx):
    doc, rid = make_document_row(app_ctx)
    ctx, page = new_page(browser, app_server, allow_local_network=True)
    verify_this_browser(page, app_server)
    httpx.post("https://127.0.0.1:41951/__test/reset", verify=False)
    bridge = open_print_page(page, app_server)
    page.get_by_role("button", name=re.compile("Εκτύπωση & Αποθήκευση")).click()
    bridge.locator('[data-testid="job-status"][data-state="submitted"]').wait_for()
    page.get_by_text("Καταχωρήθηκε").first.wait_for()

    label = app_ctx.repo.label_for_row(rid)
    barcode = label["barcode"]
    assert barcode.startswith("PG0")
    printed = jobs()
    assert len(printed) == 1
    root = etree.fromstring(printed[0]["label_xml"].encode("utf-8"))
    assert [b.findtext("Text") for b in root.iter("BarcodeObject")] == [barcode]
    from vspm.dymo.template import check_prepared_label, counter_text
    assert check_prepared_label(printed[0]["label_xml"], barcode) == []
    assert [counter_text(c) for c in root.iter("CounterObject")] == [barcode]
    assert len(excel_rows(app_ctx, barcode)) == 1

    # Reload: the browser must not print again, the server must not add anything.
    page.reload()
    page.locator(BRIDGE).first.wait_for()
    page.wait_for_timeout(3000)
    assert len(jobs()) == 1 and len(excel_rows(app_ctx, barcode)) == 1

    # Reprint: same barcode, one more label, still one Excel row.
    open_print_page(page, app_server)
    page.get_by_role("button", name=re.compile(f"Επανεκτύπωση \\({barcode}\\)")).click()
    page.frame_locator(BRIDGE).first.locator('[data-testid="job-status"][data-state="submitted"]').wait_for()
    page.wait_for_timeout(1500)
    printed = jobs()
    assert len(printed) == 2
    assert etree.fromstring(printed[1]["label_xml"].encode()).find(".//BarcodeObject/Text").text == barcode
    assert len(excel_rows(app_ctx, barcode)) == 1
    kinds = [a["kind"] for a in app_ctx.repo.print_attempts(label["id"])]
    assert kinds == ["initial", "reprint"]
    ctx.close()


def test_two_tabs_print_the_row_only_once(browser, app_server, dymo_reset, app_ctx):
    doc, rid = make_document_row(app_ctx, weight="777")
    ctx, page_a = new_page(browser, app_server, allow_local_network=True)
    verify_this_browser(page_a, app_server)
    page_b = ctx.new_page()  # second tab of the same browser
    page_b.set_default_timeout(45000)
    page_b.goto(app_server["url"] + "/printer")
    page_b.get_by_text("Αυτός ο υπολογιστής").first.wait_for()
    httpx.post("https://127.0.0.1:41951/__test/reset", verify=False)
    open_print_page(page_a, app_server)
    open_print_page(page_b, app_server)
    btn_a = page_a.get_by_role("button", name=re.compile("Εκτύπωση & Αποθήκευση"))
    btn_b = page_b.get_by_role("button", name=re.compile("Εκτύπωση & Αποθήκευση"))
    btn_a.click()
    btn_b.click()
    page_a.wait_for_timeout(6000)
    page_b.wait_for_timeout(2000)
    barcode = app_ctx.repo.label_for_row(rid)["barcode"]
    assert len(jobs()) == 1, jobs()
    assert len(excel_rows(app_ctx, barcode)) == 1
    text_b = page_b.locator("section.main").inner_text() if page_b.locator("section.main").count() else \
        page_b.inner_text("body")
    assert ("ήδη" in text_b) or ("already" in text_b) or ("Επανεκτύπωση" in text_b)
    ctx.close()


def test_download_fallback_contains_reserved_barcode(browser, app_server, dymo_reset, app_ctx):
    doc, rid = make_document_row(app_ctx, weight="321")
    ctx, page = new_page(browser, app_server, allow_local_network=False)  # browser printing blocked
    page.goto(app_server["url"] + "/printer")
    page.get_by_text("Αυτός ο υπολογιστής").first.wait_for()
    open_print_page(page, app_server)
    page.get_by_text(re.compile("Λήψη αρχείου .label")).click()
    page.get_by_role("button", name="Δέσμευση barcode για χειροκίνητη εκτύπωση").click()
    with page.expect_download() as dl:
        page.get_by_role("button", name=re.compile(r"Λήψη .*\.label")).click()
    path = dl.value.path()
    xml = open(path, encoding="utf-8").read()
    barcode = app_ctx.repo.label_for_row(rid)["barcode"]
    assert dl.value.suggested_filename == f"{barcode}.label"
    from vspm.dymo.template import check_prepared_label
    assert check_prepared_label(xml, barcode) == []
    page.get_by_role("button", name="Ναι, τυπώθηκε σωστά").click()
    page.get_by_text("Καταχωρήθηκε").first.wait_for()
    assert len(excel_rows(app_ctx, barcode)) == 1
    assert jobs() == []  # nothing went through the browser bridge
    ctx.close()
