"""Streamlit entry point: `streamlit run src/vspm/ui/app.py` (or `vspm run`)."""

from __future__ import annotations

import faulthandler
import sys
from pathlib import Path

# If a native library crashes (segmentation fault), print where every thread was to the log
# (Streamlit Cloud: Manage app -> logs) instead of dying silently.
if not faulthandler.is_enabled():
    faulthandler.enable(all_threads=True)

if __package__ in (None, ""):  # launched by `streamlit run path/to/app.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st

# A new version pushed to GitHub: Streamlit Cloud pulls the files but reloads only the page modules -
# the rest of the package (printing, Excel, photo stamping) would keep running the OLD code until a
# reboot. When any source file changed, drop every loaded vspm module and the cached services so the
# whole app runs the new version.
_SRC = Path(__file__).resolve().parents[2]


def _code_version() -> float:
    try:
        return max(p.stat().st_mtime for d in ("vspm", "vspm_registry") for p in (_SRC / d).rglob("*.py"))
    except (OSError, ValueError):
        return 0.0


_version = _code_version()
if getattr(sys, "_vspm_code_version", None) not in (None, _version):
    for _name in [n for n in sys.modules if n == "vspm" or n.startswith(("vspm.", "vspm_registry"))]:
        del sys.modules[_name]
    st.cache_resource.clear()
    st.cache_data.clear()
sys._vspm_code_version = _version  # type: ignore[attr-defined]

st.set_page_config(page_title="VS Production Manager", page_icon="🏷️", layout="wide")

from vspm.ui import auth  # noqa: E402
from vspm.ui.common import CSS, T, get_context, lang  # noqa: E402
from vspm.ui.views import (  # noqa: E402
    dashboard, history, label_print, printer_page, recognition, review, settings_page,
)

st.markdown(CSS, unsafe_allow_html=True)
try:
    ctx = get_context()
except Exception as e:  # noqa: BLE001 - show a clear page instead of a stack trace
    from vspm.db.postgres import DatabaseConnectError
    st.title("🏷️ VS Production Manager")
    if isinstance(e, DatabaseConnectError):
        st.error("Η βάση δεδομένων δεν είναι προσβάσιμη / The database cannot be reached.\n\n" + str(e))
    else:
        st.error(f"The app could not start: {type(e).__name__}. Details: Manage app → logs.")
    st.caption("VSPM_DATABASE_URL — Streamlit Cloud → ⋮ → Settings → Secrets. DEPLOY_STREAMLIT.md, step 1.")
    if st.button("🔄 Retry / Ξανά"):
        st.rerun()
    st.stop()
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
