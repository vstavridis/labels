"""Page C - Production row review (editable table next to the scan)."""

from __future__ import annotations

import math

import pandas as pd
import streamlit as st

from vspm.domain.models import ProductionRow, RowStatus, TemplateKey
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
        d = {"id": r.id, "#": r.row_index,
             "template": r.template_key.value if r.template_key else None}
        for f, _, _ in FIELDS:
            d[f] = getattr(r, f)
        d["flags"] = " ".join(f"⚠{k}" for k, v in r.flags.items() if v == "uncertain") + (
            " ⚠label-type" if r.template_needs_confirmation else "")
        d["status"] = S(r.status.value)
        d["barcode"] = label["barcode"] if label else ""
        data.append(d)
    cols = ["id", "#", "flags", "template"] + [f for f, _, _ in FIELDS] + ["status", "barcode"]
    return pd.DataFrame(data, columns=cols)


# Columns with a "⬇ Fill" button: the value of the first row is copied into the same column of all
# the other rows that are not printed yet (printed rows are never changed).
FILL_COLUMNS = ["customer_full", "material", "thickness", "dimensions", "weight", "pieces", "packages",
                "source_roll", "location", "document_date", "template"]


def fill_down(df: pd.DataFrame, col: str) -> tuple[pd.DataFrame, int]:
    """Copy the first row's value of `col` into the other unprinted rows. Returns (new frame, cells changed)."""
    out = df.copy()
    rows = [i for i in out.index if not (isinstance(out.at[i, "#"], float) and math.isnan(out.at[i, "#"]))]
    if not rows:
        return out, 0
    first = min(rows, key=lambda i: out.at[i, "#"])
    value = _s(out.at[first, col])
    if not value:
        return out, 0
    n = 0
    for i in rows:
        if i == first or _s(out.at[i, "barcode"]):  # printed rows keep their values
            continue
        if _s(out.at[i, col]) != value:
            out.at[i, col] = value
            n += 1
    return out, n


EDITABLE = ["template"] + [f for f, _, _ in FIELDS]


def _changed(original: pd.DataFrame, edited: pd.DataFrame) -> bool:
    """True when the operator changed, added or deleted something in the table.
    A new row that is still completely empty is not a change yet (it is saved once it has a value)."""
    def is_new(v) -> bool:
        return v is None or (isinstance(v, float) and math.isnan(v))
    mask = edited["id"].map(is_new).astype(bool)  # .loc: an empty list would select columns, not rows
    if any(any(_s(r.get(f)) for f, _, _ in FIELDS) for _, r in edited.loc[mask].iterrows()):
        return True
    edited = edited.loc[~mask]
    if list(original["id"]) != list(edited["id"]):
        return True
    for col in EDITABLE:
        if col not in original.columns:
            continue
        for a, b in zip(original[col], edited[col]):
            if _s(a) != _s(b):
                return True
    return False


def fill_candidates(df: pd.DataFrame) -> dict[str, str]:
    """{column: first-row value} for the columns where Fill would change something."""
    out = {}
    for col in FILL_COLUMNS:
        if col in df.columns:
            _, n = fill_down(df, col)
            if n:
                rows = df.dropna(subset=["#"])
                out[col] = _s(rows.sort_values("#").iloc[0][col])
    return out


def apply_changes(ctx, doc_id: int, original: pd.DataFrame, edited: pd.DataFrame) -> list[str]:
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
            if not row.customer_short or getattr(ctx.customers, "label_same_as_full", False):
                row.customer_short = row.customer_full if getattr(ctx.customers, "label_same_as_full", False) \
                    else sel.short_name
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
        if getattr(ctx.customers, "label_same_as_full", False):
            row.customer_short = row.customer_full
            row.flags.pop("customer_short", None)
        new_tpl = _s(er.get("template"))
        if new_tpl and (row.template_key is None or new_tpl != row.template_key.value):
            row.template_key = TemplateKey(new_tpl)
            row.template_needs_confirmation = False
            row.template_reason = f"operator selected {new_tpl}"
        # No approval column: the operator confirms the rows by printing them (Print DYMO / Excel).
        if row.status in (RowStatus.draft, RowStatus.ocr_complete, RowStatus.needs_review, RowStatus.approved):
            row.status = RowStatus.needs_review if any(v == "uncertain" for v in row.flags.values()) or \
                row.template_needs_confirmation else RowStatus.ocr_complete
            row.approved = False
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
            "#": st.column_config.NumberColumn("#", width="small", disabled=True),
            "template": st.column_config.SelectboxColumn(T("template"), options=[k.value for k in TemplateKey],
                                                         width="small"),
            "flags": st.column_config.TextColumn("⚠", disabled=True),
            "status": st.column_config.TextColumn(T("status"), disabled=True),
            "barcode": st.column_config.TextColumn(T("barcode"), disabled=True),
        }
        same = getattr(ctx.customers, "label_same_as_full", False)
        for f, el, eng in FIELDS:
            colcfg[f] = st.column_config.TextColumn(eng if en else el)
        if same:  # one customer value: printed on the label and written to Excel F as entered
            colcfg["customer_full"] = st.column_config.TextColumn("Customer" if en else "ΠΕΛΑΤΗΣ")
            colcfg["customer_short"] = None
        ver = st.session_state.get("editor_ver", 0)
        edited = st.data_editor(original, column_config=colcfg, num_rows="dynamic", hide_index=True,
                                width="stretch", key=f"editor_{doc_id}_{len(rows)}_{ver}")
        # With no rows the editor can return a frame without the hidden columns (id, #): put them back.
        edited = edited.reindex(columns=list(original.columns))
        if _changed(original, edited):
            for m in apply_changes(ctx, doc_id, original, edited):  # saved at once - no Save button
                flash("warning", m)
            st.session_state["editor_ver"] = ver + 1
            st.rerun()
        fills = fill_candidates(original)
        if fills:
            st.caption(T("fill_help"))
            names = {f: (eng if en else el) for f, el, eng in FIELDS}
            names["template"] = T("template")
            if same:
                names["customer_full"] = "Customer" if en else "ΠΕΛΑΤΗΣ"
            cols = st.columns(min(4, len(fills)))
            for i, (col, value) in enumerate(fills.items()):
                short = value if len(value) <= 18 else value[:17] + "…"
                if cols[i % len(cols)].button(f"⬇ {names[col]}: {short}", key=f"fill_{col}", width="stretch",
                                              help=T("fill_one_help", c=names[col], v=value)):
                    filled, n = fill_down(original, col)
                    msgs = apply_changes(ctx, doc_id, original, filled)
                    if col == "template":  # choosing the type for all rows is the operator's confirmation
                        for r in ctx.repo.rows_for_document(doc_id):
                            if r.label_id is None and r.template_key and r.template_key.value == value \
                                    and r.template_needs_confirmation:
                                r.template_needs_confirmation = False
                                r.flags.pop("template_key", None)
                                ctx.repo.save_row(r)
                    for m in msgs:
                        flash("warning", m)
                    flash("success", T("filled", n=n, c=names[col]))
                    st.session_state["editor_ver"] = ver + 1
                    st.rerun()
        for r in rows:
            reasons = [f"#{r.row_index}: {r.template_reason}"] if r.template_reason else []
            notes = [f"{k[5:]}: {v}" for k, v in r.flags.items() if k.startswith("note:")]
            if reasons or notes:
                st.caption(" · ".join(reasons + notes))
        st.caption("✔ " + T("autosaved"))
        if st.button("🖨️ " + T("nav_print"), type="primary", width="stretch"):
            st.switch_page(st.session_state["_pages"]["print"])
