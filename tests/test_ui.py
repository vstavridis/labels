"""Streamlit pages render without errors (headless, any OS) and the main buttons work."""

from __future__ import annotations

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

from vspm.services.context import build_context  # noqa: E402

PAGE = """
import streamlit as st
from vspm.ui.common import lang
lang()
st.session_state.setdefault("_pages", {{}})
{prep}
from vspm.ui.views import {view}
{view}.render()
"""


@pytest.fixture
def env(settings, monkeypatch):
    for k, v in {
        "VSPM_DATA_DIR": str(settings.data_dir), "VSPM_DRIVE_MODE": "mock", "VSPM_OCR_PROVIDER": "mock",
        "VSPM_BARCODE_MODE": "mock", "VSPM_PRINTER_MODE": "mock", "VSPM_EXCEL_MODE": "direct",
        "VSPM_EXCEL_WRITE_ENABLED": "true", "VSPM_EXCEL_PATH": str(settings.excel_path),
        "VSPM_TEMPLATE_DIR": str(settings.template_dir), "VSPM_TEMPLATES_CONFIG": str(settings.templates_config),
        "VSPM_CUSTOMERS_CONFIG": str(settings.customers_config), "VSPM_LANGUAGE": "el",
        "VSPM_DATABASE_URL": settings.database_url, "VSPM_REQUIRE_AUTH": "false",
    }.items():
        monkeypatch.setenv(k, v)
    from vspm.ui.common import get_context
    get_context.clear()
    yield settings
    get_context.clear()


def run_page(view: str, prep: str = "") -> AppTest:
    at = AppTest.from_string(PAGE.format(view=view, prep=prep), default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


@pytest.mark.parametrize("view", ["dashboard", "recognition", "review", "label_print", "history", "settings_page"])
def test_every_page_renders_without_errors(env, view):
    run_page(view)


def test_read_with_ai_then_review_and_print_pages_show_rows(env):
    at = run_page("recognition")
    read = [b for b in at.button if "AI" in b.label]
    assert read, [b.label for b in at.button]
    read[0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("3" in s.value for s in at.success), [s.value for s in at.success]
    ctx = build_context(env)
    doc = next(d["id"] for d in ctx.repo.list_documents() if ctx.repo.rows_for_document(d["id"]))
    run_page("review", prep=f"st.session_state['doc_id'] = {doc}")
    at3 = run_page("label_print", prep=f"st.session_state['doc_id'] = {doc}")
    labels = [b.label for b in at3.button]
    assert any("Εκτύπωση" in lab for lab in labels)


def test_print_button_prints_one_label_and_records_excel(env):
    ctx = build_context(env)
    doc = ctx.documents.latest(5)[0].doc_id
    rows = ctx.documents.run_ocr(doc, ctx.ocr_provider())
    r = rows[0]
    r.approved, r.flags, r.template_needs_confirmation = True, {}, False
    ctx.repo.save_row(r)
    at = run_page("label_print", prep=f"st.session_state['doc_id'] = {doc}; st.session_state['row_id'] = {r.id}")
    btn = [b for b in at.button if "Εκτύπωση & Αποθήκευση" in b.label]
    assert btn and not btn[0].disabled
    btn[0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    lab = ctx.repo.label_for_row(r.id)
    assert lab is not None and lab["excel_status"] == "synchronized"
    assert len(ctx.writer.find_barcodes([lab["barcode"]])[lab["barcode"]]) == 1


def test_barcode_series_needs_typed_confirmation(env, monkeypatch, tmp_path):
    monkeypatch.setenv("VSPM_BARCODE_MODE", "database")
    if not env.database_url:
        monkeypatch.setenv("VSPM_REGISTRY_DATABASE_URL", f"sqlite:///{tmp_path / 'reg.sqlite3'}")
    from vspm.ui.common import get_context
    get_context.clear()
    at = run_page("settings_page")

    def fill_and_save(typed: str):
        at.number_input[0].set_value(7030)  # next number (template VS is preselected)
        at.number_input[1].set_value(3)  # digits
        next(t for t in at.text_input if "επιβεβαίωση" in t.label.lower()).set_value(typed)
        next(b for b in at.button if b.proto.form_id == "init_series").click().run()

    fill_and_save("VS7031")  # does not match the preview VS7030: refused
    assert not at.exception, [e.value for e in at.exception]
    assert get_context().allocator.sequences() == []
    fill_and_save("VS7030")
    assert not at.exception, [e.value for e in at.exception]
    seqs = get_context().allocator.sequences()
    assert [(q["series"], q["next_value"]) for q in seqs] == [("VS", 7030)]


def test_non_admin_cannot_change_templates_or_series(env, monkeypatch, tmp_path):
    monkeypatch.setenv("VSPM_REQUIRE_AUTH", "true")  # signed-in employee who is not an admin
    monkeypatch.setenv("VSPM_BARCODE_MODE", "database")
    if not env.database_url:
        monkeypatch.setenv("VSPM_REGISTRY_DATABASE_URL", f"sqlite:///{tmp_path / 'reg.sqlite3'}")
    from vspm.ui.common import get_context
    get_context.clear()
    at = run_page("settings_page")
    assert not at.number_input  # no series form
    assert not any(b.proto.form_id in ("upload_template", "init_series", "observe", "block") for b in at.button)
    assert sum("VSPM_ADMIN_EMAILS" in i.value for i in at.info) >= 2


def test_cloud_sources_tab_connects_and_refreshes_templates(env, monkeypatch, tmp_path):
    """Settings -> Cloud sources against the Microsoft test double: device-code sign-in, template refresh."""
    from tests.test_cloud_sources import FOLDER_LINK, NAMES, fixture_label
    from vspm.services import cloud_sources
    from vspm.testing_graph import FakeMicrosoft
    fake = FakeMicrosoft()
    folder = fake.add_folder("Ετικέτες", link=FOLDER_LINK)
    for key, name in NAMES.items():
        fake.add_file(name, fixture_label(key), parent=folder.id)
    monkeypatch.setattr(cloud_sources, "HTTP_CLIENT_FACTORY", fake.client)
    for k, v in {"VSPM_MS_CLIENT_ID": "test-client", "VSPM_SECRET_KEY": "a-long-test-secret-key-123",
                 "VSPM_ONEDRIVE_TEMPLATES_URL": FOLDER_LINK,
                 "VSPM_ONEDRIVE_TEMPLATE_FILES": ";".join(f"{k}={v}" for k, v in NAMES.items())}.items():
        monkeypatch.setenv(k, v)
    from vspm.ui.common import get_context
    get_context.clear()
    at = run_page("settings_page")
    assert any(t.value == FOLDER_LINK for t in at.text_input)  # prefilled from the secrets
    next(b for b in at.button if b.label in ("Σύνδεση λογαριασμού Microsoft", "Connect Microsoft account")).click().run()
    assert any("ABCD-1234" in i.value for i in at.info), [i.value for i in at.info]
    fake.approved = True
    next(b for b in at.button if b.label in ("Συνδέθηκα", "I have signed in")).click().run()
    assert not at.exception, [e.value for e in at.exception]
    next(b for b in at.button if b.label in ("Ανανέωση προτύπων DYMO", "Refresh DYMO templates")).click().run()
    assert not at.exception, [e.value for e in at.exception]
    ok = [s.value for s in at.success]
    assert sum(" — updated" in v for v in ok) == 4, ok
    assert get_context().templates.load("PG0").sha256
