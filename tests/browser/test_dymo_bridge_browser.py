"""Phase 1: local DYMO detection and TEST0001 printing in a real browser."""

from __future__ import annotations

import re

import httpx
import pytest
from lxml import etree

from tests.browser.conftest import new_page

BRIDGE = 'iframe[data-testid="stCustomComponentV1"]'


def jobs():
    return httpx.get("https://127.0.0.1:41951/__test/jobs", verify=False).json()


def open_printer_page(page, app_server):
    page.goto(app_server["url"] + "/printer")
    page.get_by_text("Αυτός ο υπολογιστής").first.wait_for()
    return page.frame_locator(BRIDGE).first


def test_service_and_printer_detected_after_allowing_local_network(browser, app_server, dymo_reset):
    ctx, page = new_page(browser, app_server, allow_local_network=True)
    bridge = open_printer_page(page, app_server)
    bridge.get_by_test_id("service").filter(has_text="βρέθηκε").wait_for()
    assert "✔" in bridge.get_by_test_id("service").inner_text()
    # exactly one connected LabelWriter -> selected automatically
    assert bridge.get_by_test_id("printer-select").input_value() == "DYMO LabelWriter Wireless"
    # the server received the diagnostics of THIS browser
    page.get_by_text("DYMO LabelWriter Wireless").first.wait_for()
    assert jobs() == []
    ctx.close()


def test_without_permission_the_page_explains_what_to_do(browser, app_server, dymo_reset):
    ctx, page = new_page(browser, app_server, allow_local_network=False)
    bridge = open_printer_page(page, app_server)
    status = bridge.get_by_test_id("service")
    status.filter(has_text="ΔΕΝ βρέθηκε").wait_for()
    text = bridge.locator("#root").inner_text()
    assert "Πρόσβαση τοπικού δικτύου" in text or "127.0.0.1:41951" in text
    assert jobs() == []
    ctx.close()


def test_service_not_running_is_reported(browser, app_server, dymo_reset):
    httpx.post("https://127.0.0.1:41951/__test/config", json={"status": False}, verify=False)
    ctx, page = new_page(browser, app_server, allow_local_network=True)
    bridge = open_printer_page(page, app_server)
    bridge.get_by_test_id("service").filter(has_text="ΔΕΝ βρέθηκε").wait_for()
    assert "StatusConnected" in bridge.locator("#root").inner_text()
    ctx.close()


def test_print_test0001_then_confirm_verifies_this_pc(browser, app_server, dymo_reset):
    ctx, page = new_page(browser, app_server, allow_local_network=True)
    bridge = open_printer_page(page, app_server)
    bridge.get_by_test_id("service").filter(has_text="βρέθηκε").wait_for()
    page.get_by_text("DYMO LabelWriter Wireless").first.wait_for()
    page.get_by_role("button", name="Εκτύπωση TEST0001").click()
    bridge.locator('[data-testid="job-status"][data-state="submitted"]').wait_for()
    page.get_by_text("`submitted`").or_(page.get_by_text("submitted")).first.wait_for()

    printed = jobs()
    assert len(printed) == 1
    job = printed[0]
    assert job["printer"] == "DYMO LabelWriter Wireless"
    root = etree.fromstring(job["label_xml"].encode("utf-8"))
    barcodes = list(root.iter("BarcodeObject"))
    assert [b.findtext("Text") for b in barcodes] == ["TEST0001"]
    assert all(not (b.findtext("LinkedObjectName") or "").strip() for b in barcodes)
    from vspm.dymo.template import counter_text
    assert [counter_text(c) for c in root.iter("CounterObject")] == ["TEST0001"]
    assert "<Copies>1</Copies>" in job["print_params"]

    page.get_by_role("button", name=re.compile("Ναι — σωστή ετικέτα")).click()
    page.get_by_text("επιβεβαιώθηκαν").first.wait_for()
    assert len(jobs()) == 1  # confirming and the reruns it causes never print again
    ctx.close()


def test_print_failure_is_reported_not_retried(browser, app_server, dymo_reset):
    httpx.post("https://127.0.0.1:41951/__test/config", json={"fail_print": "Printer is out of labels"}, verify=False)
    ctx, page = new_page(browser, app_server, allow_local_network=True)
    bridge = open_printer_page(page, app_server)
    bridge.get_by_test_id("service").filter(has_text="βρέθηκε").wait_for()
    page.get_by_text("DYMO LabelWriter Wireless").first.wait_for()
    page.get_by_role("button", name="Εκτύπωση TEST0001").click()
    status = bridge.locator('[data-testid="job-status"][data-state="failed"]')
    status.wait_for()
    page.get_by_text("failed").first.wait_for()
    assert jobs() == []
    ctx.close()


@pytest.mark.parametrize("case", ["ok", "wrong_text", "linked", "counter", "tampered"])
def test_browser_and_server_safety_checks_agree(browser, app_server, dymo_reset, case):
    """The browser refuses exactly the labels the server refuses."""
    from vspm.dymo.template import TemplateRegistry, check_prepared_label, xml_sha256
    from vspm.domain.models import TemplateKey
    from vspm.services.browser_print import test_content
    from tests.conftest import FIXTURES, ROOT
    reg = TemplateRegistry.from_yaml(ROOT / "config" / "templates.yaml", FIXTURES / "labels")
    key = TemplateKey.PG0
    xml = reg.load(key).populate(reg.spec(key), test_content(key), 1)
    sha = xml_sha256(xml)
    if case == "wrong_text":
        xml = xml.replace("<Text>TEST0001</Text>", "<Text>PG02279</Text>")
        sha = xml_sha256(xml)
    elif case == "linked":
        xml = re.sub(r"(<BarcodeObject>.*?<LinkedObjectName)\s*/>", r"\1>COUNTER</LinkedObjectName>", xml, flags=re.S)
        sha = xml_sha256(xml)
    elif case == "counter":
        xml = xml.replace("<Current>1</Current>", "<Current>2279</Current>")
        sha = xml_sha256(xml)
    elif case == "tampered":
        xml = xml.replace("ΔΟΚΙΜΗ", "ΑΛΛΑΓΗ", 1)  # content changed after the server computed the hash
    ctx, page = new_page(browser, app_server, allow_local_network=True)
    bridge_frame = None
    page.goto(app_server["url"] + "/printer")
    page.locator(BRIDGE).first.wait_for()
    for f in page.frames:
        if f.url.endswith("index.html") or "component" in f.url:
            bridge_frame = f
    assert bridge_frame is not None
    job = {"job_key": "k", "kind": "test", "label_xml": xml, "expected_barcode": "TEST0001", "expected_sha256": sha,
           "template_key": key.value, "title": "x"}
    js = bridge_frame.evaluate("job => window.__vspmBridge.checkLabel(job)", job)
    server = check_prepared_label(xml, "TEST0001")
    if case == "ok":
        assert js["problems"] == [] and server == []
        assert js["sha"] == sha
    elif case == "tampered":
        assert any("SHA-256" in p for p in js["problems"])
    else:
        assert js["problems"] and server, (js, server)
    ctx.close()


def test_sha256_fallback_matches_webcrypto(browser, app_server):
    ctx, page = new_page(browser, app_server, allow_local_network=False)
    page.goto(app_server["url"] + "/printer")
    page.locator(BRIDGE).first.wait_for()
    frame = next(f for f in page.frames if "component" in f.url or f.url.endswith("index.html"))
    text = "Ελληνικά ΓΑΛΒΑΝΙΖΕ " * 1000 + "x"
    res = frame.evaluate("""async t => [await window.__vspmBridge.sha256Hex(t),
                                       window.__vspmBridge.sha256Fallback(new TextEncoder().encode(t))]""", text)
    import hashlib
    assert res[0] == res[1] == hashlib.sha256(text.encode("utf-8")).hexdigest()
    ctx.close()
