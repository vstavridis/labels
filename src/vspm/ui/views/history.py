"""Page E - History, reprints and Excel reconciliation."""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

import streamlit as st

from vspm.domain.models import TemplateKey
from vspm.ui.common import S, T, flash, get_context, operator, show_flash


def render() -> None:
    ctx = get_context()
    show_flash()
    st.title(T("nav_history"))
    with st.expander(T("filters"), expanded=True):
        c = st.columns(4)
        d_from = c[0].date_input(T("date_from"), value=None)
        d_to = c[1].date_input(T("date_to"), value=None)
        customer = c[2].text_input(T("customer"))
        tpl = c[3].selectbox(T("template"), [""] + [k.value for k in TemplateKey])
        c = st.columns(4)
        barcode = c[0].text_input(T("barcode"))
        document = c[1].text_input(T("document"))
        ps = c[2].selectbox(T("print_status"), ["", "barcode_reserved", "print_unconfirmed", "printed_confirmed",
                                                 "print_failed_confirmed", "error"], format_func=lambda x: S(x) if x else "")
        es = c[3].selectbox(T("excel_status"), ["", "pending", "staged", "queued_remote", "synchronized", "conflict",
                                                 "error"], format_func=lambda x: S(x) if x else "")
    rows = ctx.repo.search_labels(date_from=d_from.isoformat() if d_from else None,
                                  date_to=d_to.isoformat() if d_to else None, customer=customer or None,
                                  template_key=tpl or None, barcode=barcode or None, document=document or None,
                                  print_status=ps or None, excel_status=es or None)
    table = [{
        "id": r["id"], T("barcode"): r["barcode"], T("template"): r["template_key"],
        T("customer"): r["customer_short"], "ΕΠΩΝΥΜΙΑ": r["customer_full"], T("document"): r["document_name"],
        "Ημ/νία" if st.session_state["lang"] == "el" else "Date": r["print_date_local"],
        T("print_status"): S(r["print_status"]), T("excel_status"): S(r["excel_status"]),
        "Excel row": r["excel_row_number"],
    } for r in rows]
    event = st.dataframe(table, hide_index=True, use_container_width=True, on_select="rerun",
                         selection_mode="single-row", column_config={"id": None})
    sel = event.selection.rows if event and event.selection else []
    if sel:
        lab = ctx.repo.get_label(table[sel[0]]["id"])
        st.markdown(f"### {lab['barcode']}")
        c1, c2, c3 = st.columns(3)
        if c1.button("🔁 " + T("reprint"), use_container_width=True, disabled=not lab["first_printed_at"]):
            out = ctx.production.reprint(lab["id"], operator())
            for m in out.messages:
                flash("success" if out.print_ok else "error", f"{lab['barcode']}: {m}")
            st.rerun()
        if c2.button("📗 " + T("retry_excel"), use_container_width=True,
                     disabled=lab["excel_status"] not in ("pending", "staged", "error", "conflict")):
            res = ctx.production.apply_staged([lab["id"]], operator()) if lab["excel_status"] == "staged" \
                else {lab["id"]: ctx.production.sync_excel(lab["id"], operator())}
            for m in res.get(lab["id"], []):
                flash("info", m)
            st.rerun()
        if c3.button("🖨️ " + T("nav_print"), use_container_width=True):
            st.session_state["row_id"] = lab["production_row_id"]
            r = ctx.repo.get_row(lab["production_row_id"])
            st.session_state["doc_id"] = r.document_id if r else None
            st.switch_page(st.session_state["_pages"]["print"])
        with st.expander(T("attempts"), expanded=True):
            st.dataframe([dict(a) for a in ctx.repo.print_attempts(lab["id"])], hide_index=True)
            st.dataframe([dict(a) for a in ctx.repo.excel_attempts(lab["id"])], hide_index=True)
            st.dataframe([dict(a) for a in ctx.db.query(
                "SELECT ts, actor, computer, action, details_json FROM audit_log WHERE entity='label' AND entity_id=? "
                "ORDER BY id", (str(lab["id"]),))], hide_index=True)

    st.divider()
    st.subheader("📗 " + T("staging"))
    pending = ctx.production.pending_excel_labels()
    st.caption(f"{len(pending)} · mode: {ctx.settings.excel_mode.value} · "
               f"workbook: {ctx.writer.path} · writes {'enabled' if ctx.settings.excel_write_enabled else 'disabled'}")
    problems = ctx.writer.preflight()
    for p in problems:
        st.warning(p)
    c1, c2, c3 = st.columns(3)
    if c1.button(T("apply_now"), use_container_width=True, disabled=not pending or not ctx.settings.excel_write_enabled):
        res = ctx.production.apply_staged(None, operator())
        for lid, msgs in res.items():
            for m in msgs:
                flash("info", f"#{lid}: {m}")
        st.rerun()
    if c2.button(T("reconcile"), use_container_width=True):
        try:
            res = ctx.production.reconcile()
            flash("success", f"✔ {len(res['synchronized'])} · missing {len(res['missing'])} · "
                             f"duplicates {res['duplicates']}")
        except Exception as e:
            flash("error", str(e))
        st.rerun()
    if pending:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "pending.xlsx"
            ctx.production.export_staging(p)
            c3.download_button(T("export_staging"), p.read_bytes(), file_name="vspm_excel_pending.xlsx",
                               use_container_width=True)
