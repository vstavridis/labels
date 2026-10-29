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
