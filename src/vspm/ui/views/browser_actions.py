"""Print page actions when labels are printed by the employee's browser (VSPM_PRINTER_MODE=browser)."""

from __future__ import annotations

import streamlit as st

from vspm.services.browser_print import JobInProgress
from vspm.services.production import (
    AlreadyPrinted, PossibleDuplicate, PreviousPrintUnknown, ProductionError, StationNotVerified, ValidationFailed,
)
from vspm.ui.common import T, flash, lang, operator, session_id
from vspm.ui.dymo_bridge import dymo_bridge
from vspm.ui.views.printer_page import process_bridge_value, show_messages


# All clicks are handled in on_click callbacks: Streamlit runs them before the page script, so a
# rerun caused by the browser bridge reporting at the same moment cannot swallow the click.
def _start(ctx, row_id: int, *, allow_dup: bool = False, previous_not_printed: bool = False) -> None:
    try:
        job = ctx.production.start_browser_print(
            row_id, session_id=session_id(), station_id=st.session_state.get("station_id"),
            printer=st.session_state.get("station_printer"), operator=operator(),
            allow_possible_duplicate=allow_dup, previous_not_printed=previous_not_printed)
        st.session_state["print_job"] = job.for_browser()
        st.session_state["print_job_key"] = job.job_key
        st.session_state.pop("possible_duplicate", None)
        st.session_state.pop("unknown_print", None)
    except PossibleDuplicate as e:
        st.session_state["possible_duplicate"] = {"row_id": row_id, "msg": str(e)}
    except PreviousPrintUnknown as e:
        st.session_state["unknown_print"] = {"row_id": row_id, "barcode": e.barcode}
    except (ValidationFailed, StationNotVerified, JobInProgress, AlreadyPrinted, ProductionError) as e:
        flash("error", str(e))


def _reprint(ctx, label_id: int) -> None:
    try:
        j = ctx.production.start_browser_reprint(label_id, session_id=session_id(),
                                                 station_id=st.session_state.get("station_id"),
                                                 printer=st.session_state.get("station_printer"), operator=operator())
        st.session_state["print_job"] = j.for_browser()
        st.session_state["print_job_key"] = j.job_key
    except (StationNotVerified, JobInProgress, ProductionError) as e:
        flash("error", str(e))


def _flash_outcome(out) -> None:
    for m in out.messages:
        flash("success", f"{out.barcode}: {m}")


def _unknown_printed(ctx, label_id: int) -> None:
    _flash_outcome(ctx.production.confirm_unanswered_printed(label_id, operator()))
    st.session_state.pop("unknown_print", None)


def _confirm(ctx, label_id: int, ok: bool) -> None:
    ctx.production.confirm_print(label_id, ok, operator())
    if not ok:
        flash("warning", T("use_reprint"))


def _reserve_manual(ctx, row_id: int, allow_dup: bool) -> None:
    try:
        ctx.production.prepare_label(row_id, operator(), allow_possible_duplicate=allow_dup)
    except PossibleDuplicate as e:
        st.session_state["possible_duplicate"] = {"row_id": row_id, "msg": str(e)}
    except (ValidationFailed, ProductionError) as e:
        flash("error", str(e))


def _manual_confirm(ctx, label_id: int, ok: bool) -> None:
    out = ctx.production.confirm_manual_print(label_id, ok, operator())
    if ok:
        _flash_outcome(out)


def bridge_section(ctx) -> None:
    """Render the browser bridge and record its report FIRST, so everything drawn after
    it on the page (label status, buttons) reflects the browser's latest result."""
    job_key = st.session_state.get("print_job_key")
    job_row = ctx.browser.job(job_key) if job_key else None
    job = st.session_state.get("print_job") if job_row is not None and job_row["status"] in (
        "pending", "not_submitted") else None
    value = dymo_bridge("print", job=job, acked=st.session_state.get("bridge_acked", []), lang=lang(),
                        key="bridge_print")
    show_messages(process_bridge_value(ctx, value, "print"))


def browser_actions(ctx, row, row_id: int, label) -> None:
    job_key = st.session_state.get("print_job_key")
    job_row = ctx.browser.job(job_key) if job_key else None
    station_id = st.session_state.get("station_id")
    printer = st.session_state.get("station_printer")
    verified = ctx.browser.station_verified(station_id, printer)
    if ctx.settings.require_verified_station and not verified:
        st.warning(T("station_not_verified"))
    pending = job_row is not None and job_row["status"] == "pending"
    if pending:
        st.info(T("sending_to_this_pc"))

    unknown = st.session_state.get("unknown_print")
    if unknown and unknown.get("row_id") == row_id:
        st.warning(T("previous_unknown", b=unknown["barcode"]))
        c1, c2 = st.columns(2)
        c1.button("✅ " + T("it_came_out"), width="stretch", key="unknown_printed", on_click=_unknown_printed,
                  args=(ctx, label["id"]))
        c2.button("🖨️ " + T("not_out_print_again"), width="stretch", key="unknown_again", on_click=_start,
                  args=(ctx, row_id), kwargs={"previous_not_printed": True})

    dup = st.session_state.get("possible_duplicate")
    allow_dup = False
    if dup and dup.get("row_id") == row_id:
        st.error(T("possible_duplicate", e=dup["msg"]))
        allow_dup = st.checkbox(T("print_anyway"), key=f"dup_ok_{row_id}")

    needs_print = label is None or label["print_status"] in ("barcode_reserved", "error")
    if needs_print:
        errors = ctx.production.check_printable(row) if label is None else {}
        if errors:
            st.warning(T("not_printable") + "\n\n" + "\n".join(f"- **{k}**: {v}" for k, v in errors.items()))
        title = "🖨️ " + T("print_save") + (f" ({label['barcode']})" if label else "")
        blocked = bool(errors) or pending or (ctx.settings.require_verified_station and not verified) or \
            (bool(dup) and dup.get("row_id") == row_id and not allow_dup)
        st.button(title, type="primary", width="stretch", disabled=blocked, key="browser_print_save",
                  on_click=_start, args=(ctx, row_id), kwargs={"allow_dup": allow_dup})

    if label is not None and label["first_printed_at"]:
        c1, c2 = st.columns(2)
        c1.button("✅ " + T("confirm_ok"), width="stretch", key="browser_confirm_ok",
                  disabled=label["print_status"] == "printed_confirmed", on_click=_confirm, args=(ctx, label["id"], True))
        c2.button("❌ " + T("confirm_bad"), width="stretch", key="browser_confirm_bad", on_click=_confirm,
                  args=(ctx, label["id"], False))
        st.button("🔁 " + T("reprint") + f" ({label['barcode']})", width="stretch", key="browser_reprint",
                  disabled=pending or (ctx.settings.require_verified_station and not verified),
                  on_click=_reprint, args=(ctx, label["id"]))

    with st.expander("⬇️ " + T("manual_fallback"), expanded=False):
        st.caption(T("manual_fallback_help"))
        if label is None:
            errors = ctx.production.check_printable(row)
            st.button(T("reserve_for_manual"), disabled=bool(errors), key="reserve_manual", on_click=_reserve_manual,
                      args=(ctx, row_id, allow_dup))
        else:
            name, data = ctx.production.manual_label_file(label["id"])
            st.download_button("⬇️ " + T("download_label", b=label["barcode"]), data, file_name=name,
                               mime="application/xml", key="manual_download",
                               on_click=ctx.production.record_manual_download, args=(label["id"], operator()))
            pending_manual = ctx.db.one("SELECT job_key FROM print_jobs WHERE label_id=? AND status='manual_pending'",
                                        (label["id"],))
            if pending_manual:
                st.info(T("manual_confirm_question", b=label["barcode"]))
                c1, c2 = st.columns(2)
                c1.button("✅ " + T("manual_printed_ok"), key="manual_ok", width="stretch", on_click=_manual_confirm,
                          args=(ctx, label["id"], True))
                c2.button("❌ " + T("manual_not_printed"), key="manual_bad", width="stretch", on_click=_manual_confirm,
                          args=(ctx, label["id"], False))

    if job_row is not None and job_row["label_id"] == (label["id"] if label else None):
        with st.expander(T("browser_jobs")):
            st.dataframe([{k: j[k] for k in ("kind", "status", "barcode", "printer_name", "created_at", "reported_at",
                                             "error")} for j in ctx.browser.jobs_for_label(label["id"])],
                         hide_index=True, width="stretch")
