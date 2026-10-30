"""Printer page: DYMO service/printer diagnostics of THIS browser's PC and the TEST0001 print."""

from __future__ import annotations

import json

import streamlit as st

from vspm.domain.models import TemplateKey
from vspm.services.browser_print import TEST_BARCODE, BrowserPrintError
from vspm.ui.common import T, flash, get_context, lang, operator, session_id, show_flash
from vspm.ui.dymo_bridge import dymo_bridge, framework_ok


def process_bridge_value(ctx, value, state_prefix: str) -> list[tuple[str, str]]:
    """Record a browser report once (Streamlit returns the last value on every rerun).

    Never calls st.rerun(): a rerun here would swallow a button click made in the same run."""
    if not value or value.get("report_id") == st.session_state.get(f"{state_prefix}_last_report"):
        return []
    st.session_state[f"{state_prefix}_last_report"] = value.get("report_id")
    out = ctx.browser.apply_report(value, operator())
    acked = st.session_state.get("bridge_acked", [])
    st.session_state["bridge_acked"] = (acked + [a for a in out.acked if a not in acked])[-100:]
    if out.station_id:
        st.session_state["station_id"] = out.station_id
        st.session_state["station_printer"] = (value.get("diagnostics") or {}).get("selected_printer") or \
            st.session_state.get("station_printer")
    return out.messages


def show_messages(messages: list[tuple[str, str]]) -> None:
    for level, msg in messages:
        getattr(st, level if level in ("success", "info", "warning", "error") else "info")(msg)


# Clicks are handled in on_click callbacks: Streamlit runs them before the page script, so a
# rerun triggered by the browser bridge at the same moment cannot swallow the click.
def _start_test(ctx, tpl: str) -> None:
    try:
        j = ctx.browser.create_test_job(tpl, session_id(), operator())
        st.session_state["diag_job"] = j.for_browser()
        st.session_state["diag_job_key"] = j.job_key
    except BrowserPrintError as e:
        flash("error", str(e))


def _confirm_test(ctx, station_id: str, job_key: str, ok: bool) -> None:
    ctx.browser.confirm_test_print(station_id, job_key, ok, operator())
    flash("success" if ok else "warning", T("station_verified") if ok else T("test_failed_note"))
    st.session_state.pop("diag_job_key", None)


def _yes(v) -> str:
    return "✅" if v else "❌"


def render() -> None:
    ctx = get_context()
    show_flash()
    st.title("🖨️ " + T("nav_printer"))
    st.caption(T("printer_intro"))
    if not framework_ok():
        st.error("dymo.connect.framework.js missing or modified (src/vspm/ui/dymo_bridge/static/vendor).")

    job_key = st.session_state.get("diag_job_key")
    job_row = ctx.browser.job(job_key) if job_key else None
    job = st.session_state.get("diag_job") if job_row is not None and job_row["status"] in (
        "pending", "not_submitted") else None
    value = dymo_bridge("diagnose", job=job, acked=st.session_state.get("bridge_acked", []), lang=lang(),
                        rescan_token=st.session_state.get("rescan", ""), key="bridge_diag")
    show_messages(process_bridge_value(ctx, value, "diag"))
    job_row = ctx.browser.job(job_key) if job_key else None  # may have changed with the report

    station_id = st.session_state.get("station_id")
    station = ctx.browser.station(station_id) if station_id else None
    st.subheader(T("this_pc"))
    if station is None:
        st.info(T("waiting_browser"))
    else:
        d = json.loads(station["diagnostics_json"] or "{}")
        printers = d.get("printers") or []
        c = st.columns(4)
        c[0].metric(T("dymo_service"), _yes(d.get("service_present")))
        c[1].metric(T("printers_found"), len([p for p in printers if p.get("printerType") == "LabelWriterPrinter"]))
        c[2].metric(T("selected_printer"), d.get("selected_printer") or "—")
        verified = ctx.browser.station_verified(station_id, d.get("selected_printer"))
        c[3].metric(T("verified"), _yes(verified))
        with st.expander(T("details"), expanded=not d.get("service_present")):
            st.dataframe([{
                T("station_name"): station["name"] or "—",
                "station id": station["station_id"][:8] + "…",
                "DYMO framework": _yes(d.get("framework_loaded")),
                T("dymo_service"): _yes(d.get("service_present")),
                "Local Network Access": d.get("lna_permission"),
                "HTTPS (secure context)": _yes(d.get("secure_context")),
                T("top_frame"): {True: "OK", False: T("embedded_cross_origin"), None: "?"}[d.get("top_same_origin")],
                "browser": (d.get("user_agent") or "")[:90],
            }], hide_index=True, width="stretch")
            if printers:
                st.dataframe([{"name": p.get("name"), "model": p.get("modelName"), "connected": p.get("isConnected"),
                               "type": p.get("printerType")} for p in printers], hide_index=True, width="stretch")
            if d.get("error_details"):
                st.code(d["error_details"])
            if d.get("errors"):
                st.code("\n".join(d["errors"]))
        if d.get("top_same_origin") is False:
            st.warning(T("embedded_hint"))

    st.subheader(T("test_print"))
    st.caption(T("test_print_help"))
    cols = st.columns([2, 2, 2])
    tpl = cols[0].selectbox(T("template"), [k.value for k in TemplateKey], key="test_tpl")
    cols[1].button("🖨️ " + T("print_test"), type="primary", width="stretch", key="print_test",
                   disabled=station is None or not station["service_present"], on_click=_start_test, args=(ctx, tpl))
    try:
        prepared = ctx.browser.prepare("test", TemplateKey(tpl), __import__(
            "vspm.services.browser_print", fromlist=["test_content"]).test_content(TemplateKey(tpl)), 1, None, "")
        cols[2].download_button("⬇️ " + T("download_test_label"), prepared.label_xml.encode("utf-8"),
                                file_name=f"{TEST_BARCODE}_{tpl}.label", mime="application/xml", width="stretch")
    except Exception as e:
        cols[2].error(str(e))

    if job_row is not None:
        status = job_row["status"]
        st.markdown(f"**{T('job')}:** {job_row['template_key']} {job_row['barcode']} → `{status}`"
                    + (f" — {job_row['printer_name']}" if job_row["printer_name"] else ""))
        if job_row["error"]:
            st.error(job_row["error"])
        if status == "submitted" and job_row["kind"] == "test" and station_id:
            st.info(T("confirm_test_question"))
            b1, b2 = st.columns(2)
            args = (ctx, station_id, job_row["job_key"])
            b1.button("✅ " + T("test_ok"), width="stretch", key="test_ok", on_click=_confirm_test, args=args + (True,))
            b2.button("❌ " + T("test_bad"), width="stretch", key="test_bad", on_click=_confirm_test,
                      args=args + (False,))

    st.subheader(T("all_stations"))
    rows = ctx.browser.stations()
    if rows:
        st.dataframe([{T("station_name"): r["name"] or r["station_id"][:8], T("selected_printer"): r["printer_name"],
                       T("dymo_service"): _yes(r["service_present"]), T("verified"): r["verified_printer"] or "—",
                       "verified at": (r["verified_at"] or "")[:16], "last seen": (r["last_seen"] or "")[:16],
                       "user": r["last_user"]} for r in rows], hide_index=True, width="stretch")
