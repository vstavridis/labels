"""Row review: "⬇ Fill" copies the first row's value into all other unprinted rows;
changes in the table are saved without a Save button; the print page can approve a checked row."""

from __future__ import annotations

import pandas as pd
from streamlit.testing.v1 import AppTest

from vspm.ui.views.review import fill_candidates, fill_down
from tests.test_ui import env  # noqa: F401  (fixture: app settings in the environment)


def frame():
    return pd.DataFrame([
        {"id": 1, "#": 1, "customer_full": "ΚΑΡΑΜΑΥΡΟΥΔΗΣ", "weight": "492", "template": "PG", "barcode": ""},
        {"id": 2, "#": 2, "customer_full": "", "weight": "490", "template": "VS", "barcode": ""},
        {"id": 3, "#": 3, "customer_full": None, "weight": "", "template": "VS", "barcode": "VS11354"},  # printed
        {"id": 4, "#": 4, "customer_full": "ΑΛΛΟΣ", "weight": None, "template": "VS", "barcode": ""},
    ])


def test_fill_overwrites_all_unprinted_rows_but_never_printed_ones():
    out, n = fill_down(frame(), "customer_full")
    vals = [v if isinstance(v, str) else "" for v in out["customer_full"]]
    assert n == 2 and vals == ["ΚΑΡΑΜΑΥΡΟΥΔΗΣ", "ΚΑΡΑΜΑΥΡΟΥΔΗΣ", "", "ΚΑΡΑΜΑΥΡΟΥΔΗΣ"]  # printed row 3 untouched
    out, n = fill_down(frame(), "weight")
    assert n == 2 and list(out["weight"]) == ["492", "492", "", "492"]


def test_label_type_set_for_all_unprinted_rows():
    out, n = fill_down(frame(), "template")
    assert n == 2 and list(out["template"]) == ["PG", "PG", "VS", "PG"]


def test_candidates_only_where_something_changes():
    df = frame()
    df["customer_full"] = ["X", "X", None, "X"]
    assert set(fill_candidates(df)) == {"weight", "template"}
    df.loc[df["#"] == 1, "weight"] = ""
    assert set(fill_candidates(df)) == {"template"}  # nothing to copy from an empty first cell


PAGE = """
import streamlit as st
from vspm.ui.common import lang
lang()
st.session_state.setdefault("_pages", {{}})
st.session_state["doc_id"] = {doc}
from vspm.ui.views import review
review.render()
"""


def test_fill_button_saves_the_rows(env):
    from vspm.services.context import build_context
    ctx = build_context(env)
    doc = ctx.documents.latest(10)[0].doc_id
    rows = ctx.documents.run_ocr(doc, ctx.ocr_provider())
    for i, r in enumerate(ctx.repo.rows_for_document(doc)):
        r.customer_full = "ΚΑΡΑΜΑΥΡΟΥΔΗΣ" if i == 0 else ""
        r.customer_short = r.customer_full
        ctx.repo.save_row(r)
    from vspm.ui.common import get_context
    get_context.clear()
    at = AppTest.from_string(PAGE.format(doc=doc), default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    btn = next(b for b in at.button if b.label.startswith("⬇ ΠΕΛΑΤΗΣ"))
    btn.click().run()
    assert not at.exception, [e.value for e in at.exception]
    saved = ctx.repo.rows_for_document(doc)
    assert len(saved) == len(rows) and {r.customer_full for r in saved} == {"ΚΑΡΑΜΑΥΡΟΥΔΗΣ"}
    assert {r.customer_short for r in saved} == {"ΚΑΡΑΜΑΥΡΟΥΔΗΣ"}  # one customer value
    assert not any(b.label.startswith("⬇ ΠΕΛΑΤΗΣ") for b in at.button)  # nothing left to fill


def test_table_changes_are_saved_without_a_button(env):
    from vspm.services.context import build_context
    ctx = build_context(env)
    doc = ctx.documents.latest(10)[0].doc_id
    ctx.documents.run_ocr(doc, ctx.ocr_provider())
    from vspm.ui.common import get_context
    get_context.clear()
    at = AppTest.from_string(PAGE.format(doc=doc), default_timeout=120)
    at.run()
    assert not any("💾" in b.label for b in at.button)  # no Save button
    first = ctx.repo.rows_for_document(doc)[0]
    key = f"editor_{doc}_{len(ctx.repo.rows_for_document(doc))}_0"
    at.session_state[key] = {"edited_rows": {0: {"weight": "777", "approved": True}},
                             "added_rows": [], "deleted_rows": []}
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    row = ctx.repo.get_row(first.id)
    assert row.weight == "777"
    assert row.approved and "uncertain" not in row.flags.values()  # ticking Approved confirmed the ⚠ values


def test_approve_on_print_page(env):
    from vspm.services.context import build_context
    from vspm.ui.views.approve import approvable
    ctx = build_context(env)
    doc = ctx.documents.latest(10)[0].doc_id
    ctx.documents.run_ocr(doc, ctx.ocr_provider())
    row = ctx.repo.rows_for_document(doc)[0]
    row.flags["dimensions"] = "uncertain"
    row.flags["location"] = "uncertain"
    row.approved = False
    ctx.repo.save_row(row)
    errors = ctx.production.check_printable(row)
    assert "approved" in errors and "dimensions" in errors and approvable(errors)
    assert ctx.production.approve_row(row.id, "test") == {}
    assert ctx.repo.get_row(row.id).approved
