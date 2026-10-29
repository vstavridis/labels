"""Shared Streamlit helpers: cached context, language, styling."""

from __future__ import annotations

import io
from typing import Optional

import streamlit as st

from vspm.config import Settings
from vspm.logging_setup import setup_logging
from vspm.services.context import AppContext, build_context
from vspm.ui.i18n import status_label, t

CSS = """
<style>
  .block-container { padding-top: 1.2rem; max-width: 1500px; }
  div[data-testid="stMetricValue"] { font-size: 2rem; }
  .vs-badge { display:inline-block; padding:2px 10px; border-radius:12px; font-size:0.85rem; font-weight:600;
              background:#eef2f8; color:#1f3b63; margin-right:6px; }
  .vs-ok { background:#e5f5ea; color:#17602f; }
  .vs-warn { background:#fff4e0; color:#8a5300; }
  .vs-err { background:#fdecec; color:#9b1c1c; }
  .stButton>button { font-size: 1.05rem; padding: 0.45rem 1rem; }
</style>
"""


@st.cache_resource(show_spinner=False)
def get_context() -> AppContext:
    s = Settings()
    setup_logging(s)
    return build_context(s)


def reload_context() -> None:
    get_context.clear()


def lang() -> str:
    if "lang" not in st.session_state:
        st.session_state["lang"] = get_context().settings.language
    return st.session_state["lang"]


def T(key: str, **kw) -> str:
    return t(key, lang(), **kw)


def S(code: Optional[str]) -> str:
    return status_label(code or "", lang())


def badge(text: str, kind: str = "") -> str:
    return f'<span class="vs-badge {kind}">{text}</span>'


def status_kind(code: str) -> str:
    if code in ("synchronized", "printed_confirmed", "completed"):
        return "vs-ok"
    if code in ("error", "conflict", "print_failed_confirmed"):
        return "vs-err"
    if code in ("pending", "staged", "queued_remote", "print_unconfirmed", "needs_review", "barcode_reserved"):
        return "vs-warn"
    return ""


def session_id() -> str:
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        ctx = get_script_run_ctx()
        if ctx is not None:
            return ctx.session_id
    except Exception:
        pass
    return st.session_state.setdefault("_sid", __import__("uuid").uuid4().hex)


def operator() -> str:
    return st.session_state.get("operator") or get_context().settings.effective_operator


@st.cache_data(show_spinner=False, max_entries=64)
def page_pngs(doc_id: int, max_side: int = 1400) -> list[bytes]:
    from vspm.integrations.documents import page_images
    ctx = get_context()
    data, mime = ctx.documents.content(doc_id)
    out = []
    for img in page_images(data, mime, max_side=max_side):
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        out.append(buf.getvalue())
    return out


def show_document(doc_id: int) -> None:
    try:
        pages = page_pngs(doc_id)
    except Exception as e:
        st.error(f"{e}")
        return
    for i, png in enumerate(pages, 1):
        st.image(png, caption=f"{T('page')} {i}/{len(pages)}", width="stretch")


def flash(kind: str, msg: str) -> None:
    st.session_state.setdefault("_flash", []).append((kind, msg))


def show_flash() -> None:
    for kind, msg in st.session_state.pop("_flash", []):
        getattr(st, kind)(msg)
