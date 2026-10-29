"""Page B - Document recognition."""

from __future__ import annotations

import streamlit as st

from vspm.ui.common import S, T, get_context, show_document, show_flash


def doc_selector(ctx) -> int | None:
    docs = ctx.repo.list_documents(30)
    if not docs:
        st.info(T("no_docs"))
        return None
    ids = [d["id"] for d in docs]
    current = st.session_state.get("doc_id")
    idx = ids.index(current) if current in ids else 0
    choice = st.selectbox(T("select_doc"), ids, index=idx,
                          format_func=lambda i: next(f"{d['name']}  ·  {S(d['status'])}" for d in docs if d["id"] == i))
    st.session_state["doc_id"] = choice
    return choice


def render() -> None:
    ctx = get_context()
    show_flash()
    st.title(T("nav_recognition"))
    if ctx.documents is None:
        st.error(ctx.problems.get("documents", "Document source not configured"))
        return
    try:
        ctx.documents.latest(5)
    except Exception as e:
        st.warning(str(e))
    doc_id = doc_selector(ctx)
    if doc_id is None:
        return
    left, right = st.columns([3, 2])
    with left:
        show_document(doc_id)
    with right:
        doc = ctx.repo.get_document(doc_id)
        rows = ctx.repo.rows_for_document(doc_id)
        s = ctx.settings
        st.markdown(f"**AI:** {s.ocr_provider.value} · {s.ocr_model}")
        if s.ocr_provider.value == "anthropic" and not s.ocr_upload_consent:
            st.warning("Η αποστολή εγγράφων στην υπηρεσία AI δεν είναι ενεργή (VSPM_OCR_UPLOAD_CONSENT)."
                       if st.session_state["lang"] == "el" else
                       "Sending documents to the AI service is not enabled (VSPM_OCR_UPLOAD_CONSENT).")
        if s.ocr_provider.value == "mock":
            st.info("Mock AI: επιστρέφει δοκιμαστικά δεδομένα." if st.session_state["lang"] == "el"
                    else "Mock AI: returns demo data.")
        keep_printed = False
        if rows:
            st.caption(T("ocr_again_warn"))
            if any(r.label_id is not None for r in rows):
                st.warning(T("reread_printed"))
                keep_printed = st.checkbox(T("reread_confirm"))
        if st.button("🤖 " + T("read_ai"), type="primary", width="stretch"):
            with st.spinner(T("reading")):
                try:
                    new_rows = ctx.documents.run_ocr(doc_id, ctx.ocr_provider(), keep_printed=keep_printed)
                    st.success(T("ocr_done", n=len(new_rows)))
                    rows = new_rows
                    doc = ctx.repo.get_document(doc_id)
                except Exception as e:
                    st.error(f"{type(e).__name__}: {e}")
        if doc["ocr_error"]:
            st.error(doc["ocr_error"])
        for w in ctx.documents.warnings(doc_id):
            st.warning(w)
        if rows:
            st.dataframe([{"#": r.row_index, "ΕΠΩΝΥΜΙΑ": r.customer_full, "ΕΙΔΟΣ": r.material, "ΠΑΧΟΣ": r.thickness,
                           "ΔΙΑΣΤΑΣΕΙΣ": r.dimensions, "ΚΙΛΑ": r.weight, "ΤΕΜ": r.pieces, "ΕΤΙΚΕΤΑ":
                               r.template_key.value if r.template_key else "",
                           "⚠": ", ".join(k for k, v in r.flags.items() if v == "uncertain")} for r in rows],
                         hide_index=True, width="stretch")
            if st.button(T("go_review"), width="stretch"):
                st.switch_page(st.session_state["_pages"]["review"])
