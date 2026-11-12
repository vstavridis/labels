"""«Print DYMO / Excel»: every label of the document that is not printed yet, one after the other.

Pressing the button is the operator's confirmation of the rows (values marked ⚠ included). Each
label goes through the normal Print & Save path: barcode reserved and saved first, printed, then
its Excel row and the barcode on the scan. In browser mode the labels are sent to this PC's DYMO
one at a time: the next one is started only after the browser reported the previous one as sent;
any other result stops the series so the operator can check the printer.
"""

from __future__ import annotations

from typing import Optional

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
    import time
    if time.time() - st.session_state.get("series_sync_at", 0) > 30:
        st.session_state["series_sync_at"] = time.time()
        auto_series(ctx)  # labels printed by hand with DYMO meanwhile are never issued again
    ids = _confirm_rows(ctx, rows_to_print(ctx, doc_id))
    if ctx.settings.printer_mode.value != "browser":
        ok, done = 0, []
        for rid in ids:  # server-side printer: one after the other within this click
            try:
                label = ctx.production.prepare_label(rid, operator())
                ctx.production.defer([label["id"]])
                done.append(label["id"])
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
        for m in ctx.production.finish_series(done, operator()):
            flash("info", m)
        flash("success" if ok == len(ids) else "warning", T("batch_done", ok=ok, n=len(ids)))
        return
    st.session_state.pop("batch_result", None)
    st.session_state["batch"] = {"doc": doc_id, "rows": ids, "n": len(ids), "jobs": [], "labels": [], "ok": 0}
    _prepare(ctx, limit=1)  # the first label goes to the printer at once; the others follow in the next run


def _prepare(ctx, limit: Optional[int] = None) -> None:
    """Reserve the barcodes and create the print jobs of the next rows of the series."""
    b = st.session_state.get("batch")
    n = 0
    while b and b["rows"] and (limit is None or n < limit):
        rid = b["rows"].pop(0)
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
            b["rows"] = []
            break
        except (ValidationFailed, JobInProgress, AlreadyPrinted, ProductionError) as e:
            _skip(ctx, rid, e)
            continue
        ctx.production.defer([job.label_id])
        b["jobs"].append(job.for_browser())
        b["labels"].append(job.label_id)
        n += 1


def batch_jobs(ctx) -> list[dict]:
    """The labels of the series still to be sent by this browser (handed over all at once)."""
    b = st.session_state.get("batch")
    if not b or not b["jobs"]:
        return []
    rows = ctx.browser.statuses([j["job_key"] for j in b["jobs"]])
    return [j for j in b["jobs"] if rows.get(j["job_key"], {}).get("status") in ("pending", "not_submitted")]


def _finish(ctx, b: dict, ok: int) -> None:
    for m in ctx.production.finish_series(b["labels"], operator()):
        flash("info", m)
    st.session_state["batch_result"] = {"doc": b["doc"], "ok": ok, "n": b["n"]}
    st.session_state.pop("batch", None)


def step(ctx) -> None:
    """After the browser's report was recorded: hand over the rest of the series, or close it."""
    b = st.session_state.get("batch")
    if not b:
        return
    if b["rows"]:  # the first label is printing meanwhile: prepare all the others now
        _prepare(ctx)
        st.rerun()
    rows = ctx.browser.statuses([j["job_key"] for j in b["jobs"]])
    states = [rows.get(j["job_key"], {}).get("status") for j in b["jobs"]]
    b["ok"] = sum(s in ("submitted", "manual_confirmed") for s in states)
    bad = next((j for j in b["jobs"] if rows.get(j["job_key"], {}).get("status") in
                ("failed", "rejected", "uncertain", "expired", "cancelled")), None)
    if bad is not None:
        row = rows[bad["job_key"]]
        flash("error", T("batch_stopped", b=row["barcode"], e=row["error"] or row["status"]))
        for j in b["jobs"]:  # the browser stops at the first problem: the later labels were never sent
            if rows.get(j["job_key"], {}).get("status") in ("pending", "not_submitted"):
                ctx.browser.cancel(j["job_key"], operator())
        _finish(ctx, b, b["ok"])
        st.rerun()
    if all(s in ("submitted", "manual_confirmed") for s in states):
        _finish(ctx, b, b["ok"])
        st.rerun()
    # still being sent - or a label was not sent (no DYMO service / printer): the bridge shows «Try again»


def _stop(ctx) -> None:
    b = st.session_state.get("batch")
    if not b:
        return
    for j in batch_jobs(ctx):
        ctx.browser.cancel(j["job_key"], operator())  # a label sent meanwhile is marked uncertain, never lost
    rows = ctx.browser.statuses([j["job_key"] for j in b["jobs"]])
    _finish(ctx, b, sum(r["status"] in ("submitted", "manual_confirmed") for r in rows.values()))


def progress(ctx) -> None:
    b = st.session_state.get("batch")
    if b and b["n"]:
        st.progress(min(1.0, b["ok"] / b["n"]), text=T("batch_progress", i=min(b["n"], b["ok"] + 1), n=b["n"]))
        waiting = [r for r in ctx.browser.statuses([j["job_key"] for j in b["jobs"]]).values()
                   if r["status"] == "not_submitted"]
        if waiting:
            st.warning(T("batch_waiting", e=waiting[0]["error"] or ""))
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
