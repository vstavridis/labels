"""Page D - Label preview and printing (one production row at a time)."""

from __future__ import annotations

import streamlit as st

from vspm.domain.models import LabelContent
from vspm.services.production import AlreadyPrinted, PossibleDuplicate, ProductionError, ValidationFailed
from vspm.ui.common import S, T, badge, flash, get_context, operator, show_flash, status_kind
from vspm.ui.views.recognition import doc_selector


def render() -> None:
    ctx = get_context()
    show_flash()
    st.title(T("nav_print"))
    doc_id = doc_selector(ctx)
    if doc_id is None:
        return
    rows = ctx.repo.rows_for_document(doc_id)
    if not rows:
        st.info(T("no_rows"))
        return

    def fmt(rid: int) -> str:
        r = next(x for x in rows if x.id == rid)
        lab = ctx.repo.label_for_row(rid)
        mark = "✅" if lab else ("🟢" if r.approved else "⚪")
        return (f"{mark} #{r.row_index} · {r.customer_short or r.customer_full} · {r.material} {r.thickness} · "
                f"{r.dimensions} · {r.weight} kg" + (f" · {lab['barcode']}" if lab else ""))

    ids = [r.id for r in rows]
    cur = st.session_state.get("row_id")
    row_id = st.selectbox(T("select_row"), ids, index=ids.index(cur) if cur in ids else 0, format_func=fmt)
    st.session_state["row_id"] = row_id
    browser_mode = ctx.settings.printer_mode.value == "browser"
    left, right = st.columns([3, 2])
    if browser_mode:
        from vspm.ui.views.browser_actions import bridge_section
        with right:
            bridge_section(ctx)  # first: the browser's report updates the label status shown below
    row = ctx.repo.get_row(row_id)
    label = ctx.repo.label_for_row(row_id)

    with right:
        spec = ctx.templates.spec(row.template_key) if row.template_key else None
        if label:
            content = LabelContent.model_validate_json(label["content_json"])
            barcode_text = label["barcode"]
        else:
            content = None
            peek = ctx.allocator.peek(spec.barcode_prefix) if spec else None
            barcode_text = f"{peek or (spec.barcode_prefix + '…' if spec else '—')}  ({T('barcode_at_print')})"
        st.markdown(f"**{T('template')}:** {row.template_key.value if row.template_key else '—'}"
                    + (f" — {spec.description}" if spec else ""))
        info = {
            "ΕΙΔΟΣ": content.material if content else row.material,
            "ΠΑΧΟΣ": content.thickness if content else row.thickness,
            "ΔΙΑΣΤΑΣΕΙΣ": content.dimensions if content else row.dimensions,
            "ΤΕΜΑΧΙΑ": content.pieces if content else row.pieces,
            "ΠΕΛΑΤΗΣ": content.customer_short if content else row.customer_short,
            "ΚΙΛΑ": content.weight if content else row.weight,
            "BARCODE": barcode_text,
            "ΗΜΕΡ/ΩΡΑ": ctx.production.now_local().strftime("%d/%m/%Y %H:%M"),
        }
        st.dataframe([{"": k, " ": v} for k, v in info.items()], hide_index=True, width="stretch")
        ps = label["print_status"] if label else "not_printed"
        es = label["excel_status"] if label else "not_queued"
        st.markdown(f"{T('print_status')}: " + badge(S(ps), status_kind(ps)) + "  " + f"{T('excel_status')}: "
                    + badge(S(es), status_kind(es)), unsafe_allow_html=True)
        if label and label["excel_row_number"]:
            st.caption(f"Excel row {label['excel_row_number']}")

        if browser_mode:
            from vspm.ui.views.browser_actions import browser_actions
            browser_actions(ctx, row, row_id, label)
        elif not label:
            errors = ctx.production.check_printable(row)
            if errors:
                st.warning(T("not_printable") + "\n\n" + "\n".join(f"- **{k}**: {v}" for k, v in errors.items()))
            dup = st.session_state.get("possible_duplicate")
            allow_dup = False
            if dup and dup.get("row_id") == row_id:
                st.error(T("possible_duplicate", e=dup["msg"]))
                allow_dup = st.checkbox(T("print_anyway"), key=f"dup_ok_{row_id}")
            if st.button("🖨️ " + T("print_save"), type="primary", width="stretch",
                         disabled=bool(errors) or (bool(dup) and dup.get("row_id") == row_id and not allow_dup)):
                try:
                    out = ctx.production.print_and_save(row_id, operator=operator(),
                                                        allow_possible_duplicate=allow_dup)
                    st.session_state.pop("possible_duplicate", None)
                    for m in out.messages:
                        flash("success" if out.print_ok else "error", f"{out.barcode}: {m}")
                except PossibleDuplicate as e:
                    st.session_state["possible_duplicate"] = {"row_id": row_id, "msg": str(e)}
                except ValidationFailed as e:
                    flash("error", str(e))
                except (ProductionError, AlreadyPrinted) as e:
                    flash("error", str(e))
                st.rerun()
        else:
            if label["print_status"] in ("barcode_reserved", "error"):
                if st.button("🖨️ " + T("print_save") + f" ({label['barcode']})", type="primary",
                             width="stretch"):
                    try:
                        out = ctx.production.print_and_save(row_id, operator=operator())
                        for m in out.messages:
                            flash("success" if out.print_ok else "error", f"{out.barcode}: {m}")
                    except ProductionError as e:
                        flash("error", str(e))
                    st.rerun()
            if label["first_printed_at"]:
                c1, c2 = st.columns(2)
                if c1.button("✅ " + T("confirm_ok"), width="stretch",
                             disabled=label["print_status"] == "printed_confirmed"):
                    ctx.production.confirm_print(label["id"], True, operator())
                    st.rerun()
                if c2.button("❌ " + T("confirm_bad"), width="stretch"):
                    ctx.production.confirm_print(label["id"], False, operator())
                    flash("warning", "OK - χρησιμοποιήστε «Επανεκτύπωση» (ίδιο barcode)."
                          if st.session_state["lang"] == "el" else "OK - use Reprint (same barcode).")
                    st.rerun()
                if st.button("🔁 " + T("reprint") + f" ({label['barcode']})", width="stretch"):
                    try:
                        out = ctx.production.reprint(label["id"], operator=operator())
                        for m in out.messages:
                            flash("success" if out.print_ok else "error", m)
                    except ProductionError as e:
                        flash("error", str(e))
                    st.rerun()
        if label is not None and label["excel_status"] in ("pending", "staged", "error", "conflict"):
            if st.button("📗 " + T("retry_excel"), width="stretch"):
                if label["excel_status"] == "staged":
                    res = ctx.production.apply_staged([label["id"]], operator())
                    msgs = res.get(label["id"], [])
                else:
                    msgs = ctx.production.sync_excel(label["id"], operator())
                for m in msgs:
                    flash("info", m)
                st.rerun()
    with left:
        try:
            png, _, _ = ctx.production.preview(row)
            st.image(png, caption=T("preview") + (" (DYMO)" if ctx.settings.printer_mode.value == "dymo_web" else ""),
                     width="stretch")
        except Exception as e:
            st.warning(f"{T('preview')}: {e}")
        if label and label["first_printed_at"]:
            if st.button("🔎 " + T("verify"), width="stretch"):
                from vspm.services.verification import verify_label
                rep = verify_label(ctx, label["id"])
                (st.success if rep.ok else st.error)(rep.text().splitlines()[0])
                for c in rep.checks:
                    icon = {True: "✅", False: "❌", None: "➖"}[c.ok]
                    st.markdown(f"{icon} **{c.name}** — {c.detail}")
        if label:
            with st.expander(T("attempts")):
                st.dataframe([dict(a) for a in ctx.repo.print_attempts(label["id"])], hide_index=True)
                st.dataframe([dict(a) for a in ctx.repo.excel_attempts(label["id"])], hide_index=True)
