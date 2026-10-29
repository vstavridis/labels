"""Page A - Dashboard."""

from __future__ import annotations

import streamlit as st

from vspm.ui.common import S, T, badge, get_context, page_pngs, show_flash, status_kind


def go(page: str, doc_id: int | None = None) -> None:
    if doc_id is not None:
        st.session_state["doc_id"] = doc_id
    st.switch_page(st.session_state["_pages"][page])


def render() -> None:
    ctx = get_context()
    show_flash()
    head, btn = st.columns([5, 1])
    head.title(T("nav_dashboard"))
    if btn.button("🔄 " + T("refresh"), use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    today = ctx.production.now_local().date().isoformat()
    stats = ctx.repo.stats(today)
    c = st.columns(5)
    c[0].metric(T("labels_today"), stats["labels_today"])
    c[1].metric(T("excel_pending"), stats["excel_pending"])
    c[2].metric(T("excel_errors"), stats["excel_errors"])
    c[3].metric(T("print_errors"), stats["print_errors"])
    c[4].metric(T("print_unconfirmed"), stats["print_unconfirmed"])

    if ctx.problems:
        with st.expander("⚠️ " + T("problems"), expanded=True):
            for k, v in ctx.problems.items():
                st.warning(f"**{k}**: {v}")
    if ctx.recovery_notes or stats["interrupted"]:
        with st.expander("⏯️ " + T("recovery"), expanded=True):
            for n in ctx.recovery_notes:
                st.info(n)
    if stats["excel_errors"] or stats["print_errors"]:
        st.error(("Υπάρχουν εκκρεμότητες - δείτε το Ιστορικό." if st.session_state["lang"] == "el"
                  else "There are outstanding errors - see History."))

    st.subheader(T("latest_docs"))
    if ctx.documents is None:
        st.error(ctx.problems.get("documents", "Document source not configured"))
        return
    try:
        docs = ctx.documents.latest(5)
    except Exception as e:
        st.error(str(e))
        return
    if not docs:
        st.info(T("no_docs"))
        return
    cols = st.columns(len(docs))
    for col, d in zip(cols, docs):
        with col:
            with st.container(border=True):
                try:
                    st.image(page_pngs(d.doc_id, 500)[0], use_container_width=True)
                except Exception as e:
                    st.caption(f"(preview unavailable: {e})")
                st.markdown(f"**{d.remote.name}**")
                st.caption(f"{T('uploaded')}: {(d.remote.uploaded_at or '')[:16].replace('T', ' ')}")
                status = ctx.documents.refresh_status(d.doc_id) if d.rows else d.status
                st.markdown(badge(S(status), status_kind(status)), unsafe_allow_html=True)
                st.caption(f"{T('rows_recognized')}: {d.rows} · {T('printed')}: {d.printed}")
                if d.changed_since_processing:
                    st.warning(T("changed_doc"))
                if st.button(T("open"), key=f"open{d.doc_id}", use_container_width=True):
                    go("review" if d.rows else "recognition", d.doc_id)
