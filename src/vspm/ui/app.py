"""Streamlit entry point: `streamlit run src/vspm/ui/app.py` (or `vspm run`)."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):  # launched by `streamlit run path/to/app.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st

st.set_page_config(page_title="VS Production Manager", page_icon="🏷️", layout="wide")

from vspm.ui import auth  # noqa: E402
from vspm.ui.common import CSS, T, get_context, lang  # noqa: E402
from vspm.ui.views import (  # noqa: E402
    dashboard, history, label_print, printer_page, recognition, review, settings_page,
)

st.markdown(CSS, unsafe_allow_html=True)
ctx = get_context()
lang()
access = auth.gate(ctx.settings)  # stops here unless the employee is signed in (cloud)
if ctx.cloud is not None and not st.session_state.get("_templates_checked"):
    # The DYMO templates come from OneDrive: check for a new version at most every few minutes
    # (shared by both PCs). Errors are recorded and shown in Settings -> Cloud sources.
    st.session_state["_templates_checked"] = True
    try:
        ctx.cloud.templates.maybe_refresh(ctx.settings.cloud_refresh_minutes * 60)
    except Exception:  # noqa: BLE001 - never block the app on a cloud problem
        pass

with st.sidebar:
    st.markdown(f"### 🏷️ {T('app_title')}")
    choice = st.segmented_control(T("language"), ["Ελληνικά", "English"],
                                  default="Ελληνικά" if st.session_state["lang"] == "el" else "English")
    if choice:
        new = "el" if choice == "Ελληνικά" else "en"
        if new != st.session_state["lang"]:
            st.session_state["lang"] = new
            st.rerun()
    if access.status == "ok":
        st.markdown(f"👤 **{access.name}**  \n{access.email}" + (" · admin" if access.admin else ""))
        if st.button(T("auth_logout"), key="logout"):
            st.logout()
    else:
        st.text_input(T("operator"), key="operator",
                      value=st.session_state.get("operator", ctx.settings.operator_name))
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
    st.Page(printer_page.render, title=T("nav_printer"), icon="🖨️", url_path="printer"),
    st.Page(settings_page.render, title=T("nav_settings"), icon="⚙️", url_path="settings"),
]
st.session_state["_pages"] = {p.url_path: p for p in pages}
st.navigation(pages).run()
