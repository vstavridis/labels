"""Page C - Production row review (editable table next to the scan)."""

from __future__ import annotations

import math

import pandas as pd
import streamlit as st

from vspm.domain.models import ProductionRow, RowStatus, TemplateKey
from vspm.domain.validation import validate_row
from vspm.ui.common import S, T, flash, get_context, show_document, show_flash
from vspm.ui.views.recognition import doc_selector

FIELDS = [
    ("customer_full", "ΕΠΩΝΥΜΙΑ (πλήρης)", "Customer (full)"),
    ("customer_short", "ΠΕΛΑΤΗΣ (ετικέτα)", "Customer (label)"),
    ("material", "ΕΙΔΟΣ", "Material"),
    ("thickness", "ΠΑΧΟΣ", "Thickness"),
    ("dimensions", "ΔΙΑΣΤΑΣΕΙΣ", "Dimensions"),
    ("weight", "ΚΙΛΑ", "Weight (kg)"),
    ("pieces", "ΤΕΜΑΧΙΑ", "Pieces"),
    ("packages", "ΔΕΜΑΤΑ", "Packages"),
    ("source_barcode", "BARCODE (χειρόγρ.)", "Handwritten barcode"),
    ("source_roll", "ΑΡ. ΡΟΛΟΥ", "Roll no."),
    ("location", "ΤΟΠΟΘΕΣΙΑ", "Location"),
    ("document_date", "ΗΜΕΡ/ΝΙΑ", "Doc. date"),
]


def _s(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return str(v).strip()


def to_frame(ctx, rows: list[ProductionRow]) -> pd.DataFrame:
    data = []
    for r in rows:
        label = ctx.repo.label_for_row(r.id)
        d = {"id": r.id, "approved": r.approved, "#": r.row_index,
             "template": r.template_key.value if r.template_key else None}
        for f, _, _ in FIELDS:
            d[f] = getattr(r, f)
        d["flags"] = " ".join(f"⚠{k}" for k, v in r.flags.items() if v == "uncertain") + (
            " ⚠label-type" if r.template_needs_confirmation else "")
        d["status"] = S(r.status.value)
        d["barcode"] = label["barcode"] if label else ""
        data.append(d)
    cols = ["id", "approved", "#", "flags", "template"] + [f for f, _, _ in FIELDS] + ["status", "barcode"]
    return pd.DataFrame(data, columns=cols)


def apply_changes(ctx, doc_id: int, original: pd.DataFrame, edited: pd.DataFrame, confirmed: bool) -> list[str]:
    msgs: list[str] = []
    orig_ids = {int(i) for i in original["id"].dropna()}
    kept_ids = {int(i) for i in edited["id"].dropna()}
    for rid in orig_ids - kept_ids:
        try:
            ctx.repo.delete_row(rid)
        except ValueError as e:
            msgs.append(f"⛔ {e}")
    max_idx = int(edited["#"].fillna(0).max() or 0)
    for _, er in edited.iterrows():
        rid = er.get("id")
        if rid is None or (isinstance(rid, float) and math.isnan(rid)):
            if not any(_s(er.get(f)) for f, _, _ in FIELDS):
                continue  # ignore completely empty new rows
            max_idx += 1
            row = ProductionRow(document_id=doc_id, row_index=max_idx, status=RowStatus.draft)
            for f, _, _ in FIELDS:
                setattr(row, f, _s(er.get(f)))
            sel = ctx.customers.select(row.customer_full)
            row.template_key = TemplateKey(er["template"]) if _s(er.get("template")) else sel.template_key
            row.template_suggested = sel.template_key
            row.template_reason = "added manually; " + sel.reason
            if not row.customer_short:
                row.customer_short = sel.short_name
            ctx.repo.add_row(row)
            continue
        row = ctx.repo.get_row(int(rid))
        if row is None:
            continue
        if row.label_id is not None:
            changed = [f for f, _, _ in FIELDS if _s(er.get(f)) != getattr(row, f)]
            if changed:
                msgs.append(f"⛔ #{row.row_index}: already printed - changes ignored ({', '.join(changed)})")
            continue
        for f, _, _ in FIELDS:
            new = _s(er.get(f))
            if new != getattr(row, f):
                setattr(row, f, new)
                row.flags.pop(f, None)  # operator corrected the value
                if f == "customer_full":
                    sel = ctx.customers.select(new)
                    if _s(er.get("customer_short")) == _s(original.loc[original["id"] == rid, "customer_short"].iloc[0]):
                        row.customer_short = sel.short_name
                    row.template_suggested = sel.template_key
                    row.template_key = sel.template_key
                    row.template_reason = sel.reason
                    row.template_needs_confirmation = sel.needs_confirmation
        new_tpl = _s(er.get("template"))
        if new_tpl and (row.template_key is None or new_tpl != row.template_key.value):
            row.template_key = TemplateKey(new_tpl)
            row.template_needs_confirmation = False
            row.template_reason = f"operator selected {new_tpl}"
        want_approved = bool(er.get("approved"))
        if want_approved:
            needs = any(v == "uncertain" for v in row.flags.values()) or row.template_needs_confirmation
            if needs and not confirmed:
                msgs.append("⚠️ " + T("need_confirm") + f" (#{row.row_index})")
                want_approved = False
            elif needs:
                row.flags = {k: v for k, v in row.flags.items() if v != "uncertain"}
                row.template_needs_confirmation = False
                ctx.db.audit("row", row.id, "flags_confirmed", {}, st.session_state.get("operator"))
        row.approved = False
        if want_approved:
            errors = validate_row(row)
            if errors:
                msgs.append("⛔ " + T("row_errors", i=row.row_index, e="; ".join(f"{k}: {v}" for k, v in errors.items())))
            else:
                row.approved = True
        if row.status in (RowStatus.draft, RowStatus.ocr_complete, RowStatus.needs_review, RowStatus.approved):
            row.status = RowStatus.approved if row.approved else (
                RowStatus.needs_review if any(v == "uncertain" for v in row.flags.values()) or
                row.template_needs_confirmation else RowStatus.ocr_complete)
        try:
            ctx.repo.save_row(row)
        except ValueError as e:
            msgs.append(f"⛔ {e}")
    ctx.db.audit("document", doc_id, "rows_saved", {"messages": msgs}, st.session_state.get("operator"))
    return msgs


def render() -> None:
    ctx = get_context()
    show_flash()
    st.title(T("nav_review"))
    doc_id = doc_selector(ctx)
    if doc_id is None:
        return
    rows = ctx.repo.rows_for_document(doc_id)
    en = st.session_state["lang"] == "en"
    left, right = st.columns([3, 5])
    with left:
        show_document(doc_id)
    with right:
        st.caption(T("review_help"))
        original = to_frame(ctx, rows)
        colcfg = {
            "id": None,
            "approved": st.column_config.CheckboxColumn(T("approved"), width="small"),
            "#": st.column_config.NumberColumn("#", width="small", disabled=True),
            "template": st.column_config.SelectboxColumn(T("template"), options=[k.value for k in TemplateKey],
                                                         width="small"),
            "flags": st.column_config.TextColumn("⚠", disabled=True),
            "status": st.column_config.TextColumn(T("status"), disabled=True),
            "barcode": st.column_config.TextColumn(T("barcode"), disabled=True),
        }
        for f, el, eng in FIELDS:
            colcfg[f] = st.column_config.TextColumn(eng if en else el)
        edited = st.data_editor(original, column_config=colcfg, num_rows="dynamic", hide_index=True,
                                use_container_width=True, key=f"editor_{doc_id}_{len(rows)}")
        for r in rows:
            reasons = [f"#{r.row_index}: {r.template_reason}"] if r.template_reason else []
            notes = [f"{k[5:]}: {v}" for k, v in r.flags.items() if k.startswith("note:")]
            if reasons or notes:
                st.caption(" · ".join(reasons + notes))
        confirmed = st.checkbox(T("confirm_flags"))
        c1, c2 = st.columns(2)
        if c1.button("💾 " + T("save"), type="primary", use_container_width=True):
            msgs = apply_changes(ctx, doc_id, original, edited, confirmed)
            for m in msgs:
                flash("warning", m)
            flash("success", T("saved"))
            st.rerun()
        if c2.button("🖨️ " + T("nav_print"), use_container_width=True):
            st.switch_page(st.session_state["_pages"]["print"])
