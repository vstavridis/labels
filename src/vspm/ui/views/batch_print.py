"""«Print DYMO / Excel»: every label of the document that is not printed yet, one after the other.

Pressing the button is the operator's confirmation of the rows (values marked ⚠ included). Each
label goes through the normal Print & Save path: barcode reserved and saved first, printed, then
its Excel row and the barcode on the scan. In browser mode the labels are sent to this PC's DYMO
one at a time: the next one is started only after the browser reported the previous one as sent;
any other result stops the series so the operator can check the printer.
"""

from __future__ import annotations

import streamlit as st

from vspm.services.browser_print import JobInProgress
from vspm.services.production import (
    AlreadyPrinted, PossibleDuplicate, PreviousPrintUnknown, ProductionError, StationNotVerified, ValidationFailed,
)
from vspm.ui.common import T, flash, operator, session_id

# Errors the operator clears by printing (confirming): an uncertain AI value, the template, the approval.
CONFIRMABLE = "uncertain OCR value"


def blocking(errors: dict[str, str]) -> dict[str, str]:
    """The errors that still block printing after the operator's confirmation."""
    return {k: v for k, v in errors.items()
            if not (k == "approved" or CONFIRMABLE in v or (k == "template_key" and "confirm" in v))}


def rows_to_print(ctx, doc_id: int) -> list:
    out = []
    for r in ctx.repo.rows_for_document(doc_id):
        lab = ctx.repo.label_for_row(r.id)
        if lab is None or (not lab["first_printed_at"] and lab["print_status"] in ("barcode_reserved", "error")):
            out.append(r)
    return out


def missing_series(ctx, rows) -> list[str]:
    """Barcode series (e.g. PG0) that the rows need but that were never initialised in Settings."""
    sequences = getattr(ctx.allocator, "sequences", None)
    if sequences is None:
        return []
    try:
        known = {q["series"] for q in sequences()}
    except Exception:  # noqa: BLE001 - the print itself reports the registry error
        return []
    needed = set()
    for r in rows:
        if r.template_key is not None:
            needed.add(ctx.templates.spec(r.template_key).barcode_prefix)
    return sorted(needed - known)


def _confirm_rows(ctx, rows) -> list[int]:
    ids = []
    for r in rows:
        left = ctx.production.approve_row(r.id, operator())
        if left:
            flash("error", T("batch_skipped", i=r.row_index, e="; ".join(f"{k}: {v}" for k, v in left.items())))
        else:
            ids.append(r.id)
    return ids


def _skip(ctx, row_id: int, e: Exception) -> None:
    row = ctx.repo.get_row(row_id)
    flash("warning", T("batch_skipped", i=row.row_index if row else row_id, e=e))


def auto_series(ctx) -> None:
    """Continue every barcode series after the last number used (DYMO file, workbook, app)."""
    from vspm.services.series_sync import sync_series
    try:
        res = sync_series(ctx, operator())
    except Exception as e:  # noqa: BLE001 - printing then reports the registry's own error
        flash("warning", f"Barcode: {e}")
        return
    for series, nxt in res.set.items():
        flash("info", T("series_auto_set", s=series, b=nxt, src=res.sources[series]))
    for w in res.warnings:
        flash("warning", w)


def start(ctx, doc_id: int) -> None:
    """on_click of «Print DYMO / Excel»."""
    auto_series(ctx)  # labels printed by hand with DYMO meanwhile are never issued again
    ids = _confirm_rows(ctx, rows_to_print(ctx, doc_id))
    if ctx.settings.printer_mode.value != "browser":
        ok = 0
        for rid in ids:  # server-side printer: one after the other within this click
            try:
                out = ctx.production.print_and_save(rid, operator=operator())
                ok += int(out.print_ok)
                if not out.print_ok:
                    flash("error", f"{out.barcode}: " + "; ".join(out.messages))
                    break
            except PossibleDuplicate as e:
                st.session_state["possible_duplicate"] = {"row_id": rid, "msg": str(e)}
                _skip(ctx, rid, e)
            except (ValidationFailed, AlreadyPrinted, ProductionError) as e:
                _skip(ctx, rid, e)
        flash("success" if ok == len(ids) else "warning", T("batch_done", ok=ok, n=len(ids)))
        return
    st.session_state.pop("batch_result", None)
    st.session_state["batch"] = {"doc": doc_id, "queue": ids, "n": len(ids), "ok": 0, "current": None}
    _advance(ctx)


def _advance(ctx) -> bool:
    """Start the next label of the series. False when the series is over."""
    b = st.session_state.get("batch")
    while b and b["queue"]:
        rid = b["queue"].pop(0)
        try:
            job = ctx.production.start_browser_print(
                rid, session_id=session_id(), station_id=st.session_state.get("station_id"),
                printer=st.session_state.get("station_printer"), operator=operator())
        except PossibleDuplicate as e:
            st.session_state["possible_duplicate"] = {"row_id": rid, "msg": str(e)}
            _skip(ctx, rid, e)
            continue
        except PreviousPrintUnknown as e:
            st.session_state["unknown_print"] = {"row_id": rid, "barcode": e.barcode}
            _skip(ctx, rid, e)
            continue
        except StationNotVerified as e:
            flash("error", str(e))
            break
        except (ValidationFailed, JobInProgress, AlreadyPrinted, ProductionError) as e:
            _skip(ctx, rid, e)
            continue
        st.session_state["print_job"] = job.for_browser()
        st.session_state["print_job_key"] = job.job_key
        st.session_state["row_id"] = rid  # the page shows the label being printed
        b["current"] = rid
        return True
    if b:  # kept on the page (a flash message could be cleared by the bridge's next report)
        st.session_state["batch_result"] = {"doc": b["doc"], "ok": b["ok"], "n": b["n"]}
    st.session_state.pop("batch", None)
    return False


def step(ctx) -> None:
    """After the browser's report was recorded: go on with the next label (or stop on a problem)."""
    b = st.session_state.get("batch")
    if not b:
        return
    if b.get("current") is not None:
        key = st.session_state.get("print_job_key")
        job = ctx.browser.job(key) if key else None
        if job is None or job["status"] in ("pending", "not_submitted"):
            # still being sent - or nothing reached the printer (no DYMO service / printer yet): the bridge
            # shows «Try again»; the series goes on once this label is sent, or the operator stops it.
            return
        b["current"] = None
        if job["status"] != "submitted":
            flash("error", T("batch_stopped", b=job["barcode"], e=job["error"] or job["status"]))
            st.session_state["batch_result"] = {"doc": b["doc"], "ok": b["ok"], "n": b["n"]}
            st.session_state.pop("batch", None)
            st.rerun()
        b["ok"] += 1
    _advance(ctx)
    st.rerun()  # the bridge must be drawn again with the next label (or the result)


def _stop(ctx) -> None:
    b = st.session_state.pop("batch", None)
    if b:
        key = st.session_state.get("print_job_key")
        job = ctx.browser.job(key) if key else None
        if job is not None and job["status"] == "not_submitted":
            ctx.browser.cancel(key, operator())  # nothing reached the printer: safe to drop
        st.session_state["batch_result"] = {"doc": b["doc"], "ok": b["ok"], "n": b["n"]}


def progress(ctx) -> None:
    b = st.session_state.get("batch")
    if b and b["n"]:
        i = b["n"] - len(b["queue"])
        st.progress(min(1.0, b["ok"] / b["n"]), text=T("batch_progress", i=i, n=b["n"]))
        key = st.session_state.get("print_job_key")
        job = ctx.browser.job(key) if key else None
        if job is not None and job["status"] == "not_submitted":
            st.warning(T("batch_waiting", e=job["error"] or ""))
        st.button("⏹ " + T("batch_stop"), key="batch_stop", on_click=_stop, args=(ctx,))


def button(ctx, doc_id: int, *, disabled: bool = False) -> None:
    todo = rows_to_print(ctx, doc_id)
    running = bool(st.session_state.get("batch"))
    res = st.session_state.get("batch_result")
    if res and res["doc"] == doc_id and not running:
        (st.success if res["ok"] == res["n"] else st.warning)(T("batch_done", ok=res["ok"], n=res["n"]))
    if running:
        progress(ctx)
    elif not todo:
        st.success("✅ " + T("all_printed"))
        return
    missing = [] if running else missing_series(ctx, todo)
    if missing:  # set them automatically from the DYMO file / workbook, then look again (once a minute at most)
        import time
        if time.time() - st.session_state.get("series_sync_at", 0) > 60:
            st.session_state["series_sync_at"] = time.time()
            auto_series(ctx)
            missing = missing_series(ctx, todo)
    if missing:
        st.error(T("series_missing", s=", ".join(missing)))
    if not running and disabled and ctx.settings.printer_mode.value == "browser":
        st.caption(T("print_all_needs_station"))
    st.button(f"🖨️ {T('print_all')} ({len(todo)})", type="primary", width="stretch", key="print_all",
              disabled=disabled or running or bool(missing), help=T("print_all_help", n=len(todo)), on_click=start, args=(ctx, doc_id))
