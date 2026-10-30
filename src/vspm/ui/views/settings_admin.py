"""Admin sections of Settings: DYMO templates stored in the database, barcode series (database mode)."""

from __future__ import annotations

import uuid

import streamlit as st

from vspm.barcodes.allocator import BarcodeError, DatabaseRegistryAllocator
from vspm.domain.models import TemplateKey
from vspm.dymo.template import TemplateError
from vspm.ui.auth import is_admin
from vspm.ui.common import T, flash, operator


def templates_tab(ctx) -> None:
    st.caption(T("templates_help"))
    rows = []
    for key in TemplateKey:
        try:
            src = ctx.templates.source(key)
            sha = ctx.templates.load(key).sha256[:12] + "…"
        except TemplateError as e:
            src, sha = f"❌ {e}", "—"
        rows.append({"": key.value, T("template_source"): src, "SHA-256": sha})
    st.dataframe(rows, hide_index=True, width="stretch")
    if not is_admin():
        st.info(T("admin_only"))
        return
    with st.form("upload_template", clear_on_submit=True):
        c1, c2 = st.columns([1, 3])
        key = c1.selectbox(T("template"), [k.value for k in TemplateKey])
        f = c2.file_uploader(T("upload_label_file"), type=["label"])
        if st.form_submit_button(T("upload"), type="primary") and f is not None:
            try:
                sha = ctx.templates.store.upload(TemplateKey(key), ctx.templates.spec(TemplateKey(key)), f.name,
                                                 f.getvalue(), operator())
                flash("success", T("template_uploaded", k=key, s=sha[:12]))
            except TemplateError as e:
                flash("error", f"{f.name}: {e}")
            st.rerun()
    versions = ctx.templates.store.versions()
    if versions:
        with st.expander(T("template_versions")):
            st.dataframe([{k: v[k] for k in ("template_key", "filename", "sha256", "counter_prefix", "active",
                                             "uploaded_by", "uploaded_at")} for v in versions],
                         hide_index=True, width="stretch")


def series_tab(ctx) -> None:
    alloc = ctx.allocator
    if not isinstance(alloc, DatabaseRegistryAllocator):
        st.info(T("series_other_mode", m=ctx.settings.barcode_mode.value))
        return
    st.caption(T("series_help"))
    try:
        seqs = alloc.sequences()
    except BarcodeError as e:
        st.error(str(e))
        return
    if seqs:
        st.dataframe([{"series": q["series"], "next": f"{q['prefix']}{str(q['next_value']).zfill(q['format_width'])}",
                       "max": q["max_value"], "updated": str(q["updated_at"])[:16]} for q in seqs],
                     hide_index=True, width="stretch")
    else:
        st.warning(T("series_none"))
    if not is_admin():
        st.info(T("admin_only"))
        return

    known = {q["series"]: q for q in seqs}
    with st.form("init_series"):
        st.markdown("**" + T("series_set") + "**")
        st.caption(T("series_set_help"))
        c = st.columns(4)
        key = c[0].selectbox(T("template"), [k.value for k in TemplateKey])
        spec = ctx.templates.spec(TemplateKey(key))
        nxt = c[1].number_input(T("series_next"), min_value=0, step=1,
                                value=int(known[spec.barcode_prefix]["next_value"]) if spec.barcode_prefix in known
                                else 0)
        width = c[2].number_input(T("series_width"), min_value=0, max_value=9, step=1,
                                  value=int(known[spec.barcode_prefix]["format_width"]) if spec.barcode_prefix in known
                                  else 3)
        confirm = c[3].text_input(T("series_confirm"))
        preview = f"{spec.barcode_prefix}{str(int(nxt)).zfill(int(width)) if width else int(nxt)}"
        st.caption(T("series_preview", b=preview))
        if st.form_submit_button(T("save"), type="primary"):
            if confirm.strip().upper() != preview.upper():
                flash("error", T("series_confirm_mismatch", b=preview))
            elif int(nxt) <= 0:
                flash("error", T("series_next_positive"))
            else:
                try:
                    alloc.set_sequence(spec.barcode_prefix, spec.barcode_prefix, int(nxt), format_width=int(width),
                                       operator=operator())
                    flash("success", T("series_saved", b=preview))
                except BarcodeError as e:
                    flash("error", str(e))
            st.rerun()

    with st.form("observe"):
        st.markdown("**" + T("observe_title") + "**")
        st.caption(T("observe_help"))
        text = st.text_area(T("observe_codes"), height=100)
        if st.form_submit_button(T("observe_submit")):
            codes = [x for x in text.replace(",", " ").split() if x.strip()]
            if codes:
                try:
                    res = alloc.observe(codes, "manual report (Settings)", operator(), "cloud")
                    flash("success", T("observe_done", n=len(res["recorded"]), a=len(res["already_registered"]),
                                       i=len(res["ignored"])))
                except BarcodeError as e:
                    flash("error", str(e))
            st.rerun()

    with st.form("block"):
        st.markdown("**" + T("block_title") + "**")
        st.caption(T("block_help"))
        c = st.columns(3)
        key = c[0].selectbox(T("template"), [k.value for k in TemplateKey], key="block_tpl")
        count = c[1].number_input(T("block_count"), min_value=1, max_value=200, value=10, step=1)
        if st.form_submit_button(T("block_submit")):
            try:
                series = ctx.templates.spec(TemplateKey(key)).barcode_prefix
                res = alloc.reserve_block(series, int(count), operator(), uuid.uuid4().hex, "cloud")
                st.session_state["last_block"] = res
            except BarcodeError as e:
                flash("error", str(e))
            st.rerun()
    blk = st.session_state.get("last_block")
    if blk:
        st.success(T("block_done", f=blk["barcodes"][0], l=blk["barcodes"][-1]))
        st.code("\n".join(blk["barcodes"]))
