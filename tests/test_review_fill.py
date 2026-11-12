"""Row review: "⬇ Fill" copies the first row's value into all other unprinted rows;
changes in the table are saved without a Save button (no approval column); «Print DYMO / Excel» prints every
label of the document."""

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



def test_table_changes_are_saved_with_label_and_print(env):
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
    from vspm.ui.views.review import to_frame
    assert "approved" not in to_frame(ctx, ctx.repo.rows_for_document(doc)).columns  # no approval column
    at.session_state[key] = {"edited_rows": {0: {"weight": "777"}},
                             "added_rows": [], "deleted_rows": []}
    at.run()
    assert ctx.repo.get_row(first.id).weight != "777"  # editing alone reloads nothing and saves nothing
    next(b for b in at.button if "Ετικέτα & εκτύπωση" in b.label).click().run()
    assert not at.exception, [e.value for e in at.exception]
    row = ctx.repo.get_row(first.id)
    assert row.weight == "777"


PRINT_PAGE = """
import streamlit as st
from vspm.ui.common import lang
lang()
st.session_state.setdefault("_pages", {{}})
st.session_state["doc_id"] = {doc}
from vspm.ui.views import label_print
label_print.render()
"""


def test_print_all_prints_every_label_of_the_document_and_writes_excel(env):
    from vspm.services.context import build_context
    ctx = build_context(env)
    doc = ctx.documents.latest(10)[0].doc_id
    ctx.documents.run_ocr(doc, ctx.ocr_provider())
    rows = ctx.repo.rows_for_document(doc)
    assert len(rows) > 1
    for r in rows:  # uncertain AI values: confirmed by pressing the button, no approval needed before
        r.flags["dimensions"] = "uncertain"
        r.approved = False
        ctx.repo.save_row(r)
    from vspm.ui.common import get_context
    get_context.clear()
    at = AppTest.from_string(PRINT_PAGE.format(doc=doc), default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    btn = next(b for b in at.button if b.label.startswith("🖨️ Εκτύπωση DYMO / Excel"))
    assert f"({len(rows)})" in btn.label
    btn.click().run()
    assert not at.exception, [e.value for e in at.exception]
    labels = [ctx.repo.label_for_row(r.id) for r in rows]
    assert all(lab and lab["first_printed_at"] for lab in labels)
    assert len({lab["barcode"] for lab in labels}) == len(rows)  # one barcode per label
    assert all(lab["excel_status"] == "synchronized" for lab in labels)
    assert not any(b.label.startswith("🖨️ Εκτύπωση DYMO / Excel") for b in at.button)  # nothing left
    btns = [b.label for b in at.button]
    assert not any("Εκτύπωση & Αποθήκευση" in b for b in btns)


def test_review_page_of_a_document_without_rows(env):
    """Regression: KeyError 'id' when the scan had no rows yet (seen in the Streamlit Cloud check)."""
    from vspm.services.context import build_context
    ctx = build_context(env)
    doc = ctx.documents.latest(10)[0].doc_id
    assert ctx.repo.rows_for_document(doc) == []
    from vspm.ui.common import get_context
    get_context.clear()
    at = AppTest.from_string(PAGE.format(doc=doc), default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    key = f"editor_{doc}_0_0"  # the operator adds a row by hand
    at.session_state[key] = {"edited_rows": {}, "added_rows": [{"material": "ΓΑΛΒΑΝΙΖΕ", "weight": "100"}],
                             "deleted_rows": []}
    at.run()
    next(b for b in at.button if "Ετικέτα & εκτύπωση" in b.label).click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert [r.material for r in ctx.repo.rows_for_document(doc)] == ["ΓΑΛΒΑΝΙΖΕ"]


def _db_registry(env, monkeypatch, tmp_path):
    monkeypatch.setenv("VSPM_BARCODE_MODE", "database")
    if not env.database_url:
        monkeypatch.setenv("VSPM_REGISTRY_DATABASE_URL", f"sqlite:///{tmp_path / 'reg.sqlite3'}")
    from vspm.ui.common import get_context
    get_context.clear()
    return get_context()


def test_series_continue_after_the_last_barcode_automatically(env, monkeypatch, tmp_path):
    from vspm.services import series_sync
    ctx = _db_registry(env, monkeypatch, tmp_path)
    assert ctx.allocator.sequences() == []
    # the workbook already has VS11555 (printed by hand, newer than the DYMO file's counter 11546)
    monkeypatch.setattr(series_sync, "workbook_barcodes", lambda c: (["VS9000", "VS11555", "PG0x", "PG7100"], None))
    res = series_sync.sync_series(ctx, "test")
    nxt = {q["series"]: q["next_value"] for q in ctx.allocator.sequences()}
    assert nxt["VS"] == 11556 and res.sources["VS"] == "Excel"   # last label VS11555 -> VS11556
    assert nxt["PG"] == 7101                                    # PG7100 in the workbook
    assert nxt["PG0"] == 2278 and res.sources["PG0"] == "DYMO"  # only the DYMO file's counter
    # never backwards: a series already further is left alone; later labels move it forward again
    ctx.allocator.set_sequence("EX", "EX", 5000, format_width=3, operator="test")
    monkeypatch.setattr(series_sync, "workbook_barcodes", lambda c: (["VS11600"], None))
    res = series_sync.sync_series(ctx, "test")
    nxt = {q["series"]: q["next_value"] for q in ctx.allocator.sequences()}
    assert nxt["EX"] == 5000 and nxt["VS"] == 11601 and set(res.set) == {"VS"}


def test_print_page_sets_the_series_by_itself(env, monkeypatch, tmp_path):
    ctx = _db_registry(env, monkeypatch, tmp_path)
    doc = ctx.documents.latest(10)[0].doc_id
    ctx.documents.run_ocr(doc, ctx.ocr_provider())
    at = AppTest.from_string(PRINT_PAGE.format(doc=doc), default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert not any("Σειρές barcode" in e.value for e in at.error)  # nothing to set by hand
    assert not next(b for b in at.button if b.label.startswith("🖨️ Εκτύπωση DYMO / Excel")).disabled
    assert {q["series"] for q in ctx.allocator.sequences()} >= {"VS", "PG", "PG0", "EX"}


def test_type_in_row_one_then_fill_without_saving_first(env):
    from vspm.services.context import build_context
    ctx = build_context(env)
    doc = ctx.documents.latest(10)[0].doc_id
    ctx.documents.run_ocr(doc, ctx.ocr_provider())
    n = len(ctx.repo.rows_for_document(doc))
    from vspm.ui.common import get_context
    get_context.clear()
    at = AppTest.from_string(PAGE.format(doc=doc), default_timeout=120)
    at.run()
    at.session_state[f"editor_{doc}_{n}_0"] = {"edited_rows": {0: {"customer_full": "ΝΕΟΣ ΠΕΛΑΤΗΣ"}},
                                                "added_rows": [], "deleted_rows": []}
    next(b for b in at.button if b.label == "⬇ ΠΕΛΑΤΗΣ").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert {r.customer_full for r in ctx.repo.rows_for_document(doc)} == {"ΝΕΟΣ ΠΕΛΑΤΗΣ"}
