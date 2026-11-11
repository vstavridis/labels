"""Row review: "⬇ Fill" copies the first row's value into the empty cells of the other rows."""

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


def test_fill_only_empty_cells_and_never_printed_rows():
    out, n = fill_down(frame(), "customer_full")
    vals = [v if isinstance(v, str) else "" for v in out["customer_full"]]
    assert n == 1 and vals == ["ΚΑΡΑΜΑΥΡΟΥΔΗΣ", "ΚΑΡΑΜΑΥΡΟΥΔΗΣ", "", "ΑΛΛΟΣ"]  # printed row 3 untouched
    out, n = fill_down(frame(), "weight")
    assert n == 1 and list(out["weight"]) == ["492", "490", "", "492"]  # existing weights kept


def test_label_type_set_for_all_unprinted_rows():
    out, n = fill_down(frame(), "template")
    assert n == 2 and list(out["template"]) == ["PG", "PG", "VS", "PG"]


def test_candidates_only_where_something_changes():
    df = frame()
    df.loc[df["#"] == 2, "customer_full"] = "X"
    assert set(fill_candidates(df)) == {"weight", "template"}


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
