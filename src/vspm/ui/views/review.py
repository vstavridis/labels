"""Page C - Production row review (editable table next to the scan)."""

from __future__ import annotations

import math

import pandas as pd
import streamlit as st

from vspm.domain.models import ProductionRow, RowStatus, TemplateKey
from vspm.ui.common import S, T, flash, get_context, show_document, show_flash
from vspm.ui.row_grid import row_grid
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
        d["flags"] = " ".join(f"⚠{k}" for k, v in r.flags.items() if v == "uncertain" and k != "source_barcode") + (
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
        before = original.loc[original["id"] == rid]
        if len(before) and all(_s(before.iloc[0].get(c)) == _s(er.get(c)) for c in EDITABLE if c in original.columns):
            continue  # unchanged row: no database round trips
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
    left, right = st.columns([2, 5])
    with left:
        show_document(doc_id)
    with right:
        st.caption(T("review_help"))
        original = to_frame(ctx, rows)
        same = getattr(ctx.customers, "label_same_as_full", False)
        names = {f: (eng if en else el) for f, el, eng in FIELDS}
        names["template"] = T("template")
        if same:  # one customer value: printed on the label and written to Excel F as entered
            names["customer_full"] = "Customer" if en else "ΠΕΛΑΤΗΣ"
        keys = ["template"] + [f for f, _, _ in FIELDS if not (same and f == "customer_short")]
        widths = {"template": 70, "customer_full": 150, "material": 160, "thickness": 60, "dimensions": 110,
                  "weight": 60, "pieces": 60, "packages": 50, "source_roll": 90, "location": 110,
                  "document_date": 95, "customer_short": 120}
        columns = [{"key": k, "label": names[k], "fill": k in FILL_COLUMNS, "width": widths.get(k, 80),
                    **({"options": [t.value for t in TemplateKey]} if k == "template" else {})} for k in keys]
        grid_rows = []
        for rec in original.to_dict("records"):
            item = {k: _s(rec.get(k)) for k in keys}
            item.update({"id": int(rec["id"]), "#": int(rec["#"]), "flags": _s(rec.get("flags")),
                         "barcode": _s(rec.get("barcode")), "printed": bool(_s(rec.get("barcode")))})
            grid_rows.append(item)
        ver = st.session_state.get("editor_ver", 0)
        version = f"{doc_id}:{ver}:{len(rows)}"
        texts = {"save": "🖨️ " + T("nav_print"), "add": T("grid_add"), "delete": T("grid_delete"),
                 "fill_tip": T("fill_one_help", c="").strip(), "unsaved": T("grid_unsaved"),
                 "saved_hint": T("autosaved"), "barcode": T("barcode")}
        value = row_grid(grid_rows, columns, texts=texts, version=version, key=f"grid_{doc_id}")
        done = st.session_state.get("grid_done")
        if value and value.get("action") == "save" and value.get("version") == version \
                and done != (version, value.get("click")):
            st.session_state["grid_done"] = (version, value.get("click"))
            edited = pd.DataFrame([{**{c: r.get(c) for c in keys}, "id": r.get("id"), "#": r.get("#")}
                                   for r in value.get("rows", [])], columns=list(original.columns)) \
                if value.get("rows") else original.iloc[0:0]
            edited = edited.reindex(columns=list(original.columns))
            if _changed(original, edited):
                for m in apply_changes(ctx, doc_id, original, edited):
                    flash("warning", m)
            st.session_state["editor_ver"] = ver + 1
            page = st.session_state.get("_pages", {}).get("print")
            if page is not None:
                st.switch_page(page)
            st.rerun()
        for r in rows:
            reasons = [f"#{r.row_index}: {r.template_reason}"] if r.template_reason else []
            notes = [f"{k[5:]}: {v}" for k, v in r.flags.items() if k.startswith("note:")]
            if reasons or notes:
                st.caption(" · ".join(reasons + notes))

