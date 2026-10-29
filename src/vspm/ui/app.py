"""Streamlit entry point: `streamlit run src/vspm/ui/app.py` (or `vspm run`)."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):  # launched by `streamlit run path/to/app.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st

st.set_page_config(page_title="VS Production Manager", page_icon="🏷️", layout="wide")

from vspm.ui.common import CSS, T, get_context, lang  # noqa: E402
from vspm.ui.views import dashboard, history, label_print, recognition, review, settings_page  # noqa: E402

st.markdown(CSS, unsafe_allow_html=True)
ctx = get_context()
lang()

with st.sidebar:
    st.markdown(f"### 🏷️ {T('app_title')}")
    choice = st.segmented_control(T("language"), ["Ελληνικά", "English"],
                                  default="Ελληνικά" if st.session_state["lang"] == "el" else "English")
    if choice:
        new = "el" if choice == "Ελληνικά" else "en"
        if new != st.session_state["lang"]:
            st.session_state["lang"] = new
            st.rerun()
    st.text_input(T("operator"), key="operator", value=st.session_state.get("operator", ctx.settings.operator_name))
    s = ctx.settings
    st.caption(f"Drive: {s.drive_mode.value} · AI: {s.ocr_provider.value} · "
               f"DYMO: {s.printer_mode.value} · Barcodes: {s.barcode_mode.value} · Excel: {s.excel_mode.value}")
    if s.printer_mode.value == "mock" or s.barcode_mode.value == "mock":
        st.warning("TEST MODE: mock printer / mock barcodes" if lang() == "en"
                   else "ΔΟΚΙΜΑΣΤΙΚΗ ΛΕΙΤΟΥΡΓΙΑ: εικονικός εκτυπωτής / barcode")

pages = [
    st.Page(dashboard.render, title=T("nav_dashboard"), icon="📊", url_path="dashboard", default=True),
    st.Page(recognition.render, title=T("nav_recognition"), icon="🔎", url_path="recognition"),
    st.Page(review.render, title=T("nav_review"), icon="✏️", url_path="review"),
    st.Page(label_print.render, title=T("nav_print"), icon="🖨️", url_path="print"),
    st.Page(history.render, title=T("nav_history"), icon="🗂️", url_path="history"),
    st.Page(settings_page.render, title=T("nav_settings"), icon="⚙️", url_path="settings"),
]
st.session_state["_pages"] = {p.url_path: p for p in pages}
st.navigation(pages).run()
