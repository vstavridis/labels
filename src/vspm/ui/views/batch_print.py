"""«Print DYMO / Excel»: every label of the document that is not printed yet, one after the other.

Pressing the button is the operator's confirmation of the rows (values marked ⚠ included). Each
label goes through the normal Print & Save path: barcode reserved and saved first, printed, then
its Excel row and the barcode on the scan. In browser mode the labels are sent to this PC's DYMO
one at a time: the next one is started only after the browser reported the previous one as sent;
any other result stops the series so the operator can check the printer.
"""

from __future__ import annotations

import logging
import streamlit as st

from vspm.services.browser_print import AlreadySent, JobInProgress
from vspm.services.production import (
    AlreadyPrinted, PossibleDuplicate, PreviousPrintUnknown, ProductionError, StationNotVerified, ValidationFailed,
)
from vspm.ui.common import T, flash, lang, operator, session_id
from vspm.ui.i18n import t

log = logging.getLogger(__name__)

# Errors the operator clears by printing (confirming): an uncertain AI value, the template, the approval.
CONFIRMABLE = "uncertain OCR value"


def blocking(errors: dict[str, str]) -> dict[str, str]:
    """The errors that still block printing after the operator's confirmation."""
    return {k: v for k, v in errors.items()
            if not (k == "approved" or CONFIRMABLE in v or (k == "template_key" and "confirm" in v))}


def rows_to_print(ctx, doc_id: int) -> list:
    out, label_of = [], {}
    for r in ctx.repo.rows_for_document(doc_id):
        lab = ctx.repo.label_for_row(r.id)
        if lab is None or (not lab["first_printed_at"] and lab["print_status"] in ("barcode_reserved", "error")):
            out.append(r)
            if lab is not None:
                label_of[r.id] = lab["id"]
    if label_of:  # already sent by a browser, still being recorded in the background: not to print again
        ids = tuple(label_of.values())
        sent = {x["label_id"] for x in ctx.db.query(
            "SELECT label_id FROM print_jobs WHERE status IN ('submitted','manual_confirmed') AND label_id IN "
            f"({','.join('?' for _ in ids)})", ids)}
        out = [r for r in out if label_of.get(r.id) not in sent]
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


_bg = {"at": 0.0}
_bg_lock = __import__("threading").Lock()


def background_upkeep(ctx) -> None:
    """When the print page opens (at most every 2 minutes per server): in a background thread, continue
    the barcode series after labels printed by hand meanwhile (DYMO file / workbook), and finish Excel
    rows and scan stamps still waiting. Nothing of this delays a click."""
    import threading
    import time
    with _bg_lock:
        if time.time() - _bg["at"] < 120:
            return
        _bg["at"] = time.time()
    user = operator()

    def run():
        from vspm.services.series_sync import sync_series
        try:
            sync_series(ctx, user)
        except Exception as e:  # noqa: BLE001 - the print page shows a missing series by itself
            log.warning("barcode series sync failed: %s", e)
        try:
            ctx.production.catch_up(user)
        except Exception as e:  # noqa: BLE001
            log.warning("catch-up of Excel rows / scan stamps failed: %s", e)

    threading.Thread(target=run, name="vspm-upkeep", daemon=True).start()


def start(ctx, doc_id: int) -> None:
    """on_click of «Print DYMO / Excel»."""
    if ctx.settings.printer_mode.value != "browser":
        ids = _confirm_rows(ctx, rows_to_print(ctx, doc_id))
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
    series = Series(doc_id, [r.id for r in rows_to_print(ctx, doc_id)], session=session_id(), user=operator(),
                    station=st.session_state.get("station_id"), printer=st.session_state.get("station_printer"))
    st.session_state["batch"] = series
    series.start(ctx)
    series.wait_for_job(0, 8.0)  # the first label goes out with this very run


class Series:
    """A series of labels being printed from this browser.

    The barcodes are reserved and the print jobs created one after the other in a SERVER THREAD: Streamlit
    stops and restarts the page's run whenever the browser reports a label, which must never interrupt the
    preparation (a row could get lost and the series stop half way). Every run of the page hands the
    browser the jobs ready so far; the browser prints them back to back while the next ones are prepared."""

    def __init__(self, doc: int, rows: list[int], *, session: str, user: str, station, printer):
        import threading
        self.doc, self.rows, self.n = doc, list(rows), len(rows)
        self.session, self.user, self.station, self.printer = session, user, station, printer
        self.lang = lang()  # the thread has no Streamlit session to ask
        self.jobs: list[dict] = []      # handed to the browser, in order
        self.labels: list[int] = []
        self.ok = 0
        self.handed = 0                 # jobs given to the browser by the latest run
        self.messages: list[tuple[str, str]] = []   # to show on the page (level, text)
        self.state: dict = {}           # session_state entries for the page (e.g. a possible duplicate)
        self.done = False
        self.stopped = False
        self.lock = threading.Lock()
        self.changed = threading.Condition(self.lock)
        self.thread = None

    # ---- server thread --------------------------------------------------------------
    def start(self, ctx) -> None:
        import threading
        self.thread = threading.Thread(target=self._run, args=(ctx,), name="vspm-series", daemon=True)
        self.thread.start()

    def _say(self, level: str, text: str) -> None:
        with self.lock:
            self.messages.append((level, text))

    def _t(self, key: str, **kw) -> str:
        return t(key, self.lang, **kw)

    def _row_no(self, ctx, rid: int):
        row = ctx.repo.get_row(rid)
        return row.row_index if row else rid

    def _start_job(self, ctx, rid: int):
        """start_browser_print; a job this session made for the row earlier but never handed to a browser
        (e.g. a series stopped meanwhile) is cancelled and made again with the same barcode - safe, since
        no browser ever had it."""
        kw = dict(session_id=self.session, station_id=self.station, printer=self.printer, operator=self.user)
        try:
            return ctx.production.start_browser_print(rid, **kw)
        except AlreadySent:
            raise
        except JobInProgress:
            label = ctx.repo.label_for_row(rid)
            job = ctx.browser.pending_job_for_label(label["id"]) if label else None
            with self.lock:
                handed = {j["job_key"] for j in self.jobs}
            if job is None or job["session_id"] != self.session or job["job_key"] in handed:
                raise
            ctx.browser.cancel(job["job_key"], self.user)
            return ctx.production.start_browser_print(rid, **kw)

    def _run(self, ctx) -> None:
        try:
            for rid in list(self.rows):
                if self.stopped:
                    break
                self._one(ctx, rid)
                if isinstance(self.state.get("_halt"), bool):
                    break
        except Exception as e:  # noqa: BLE001 - shown on the page; labels already handed stay valid
            log.exception("print series failed")
            self._say("error", str(e))
        finally:
            with self.changed:
                self.rows = []
                self.done = True
                self.changed.notify_all()

    def _one(self, ctx, rid: int) -> None:
        left = ctx.production.approve_row(rid, self.user)  # pressing the button confirms the row
        if left:
            self._say("error", self._t("batch_skipped", i=self._row_no(ctx, rid),
                                 e="; ".join(f"{k}: {v}" for k, v in left.items())))
            return
        try:
            job = self._start_job(ctx, rid)
        except PossibleDuplicate as e:
            with self.lock:
                self.state["possible_duplicate"] = {"row_id": rid, "msg": str(e)}
            self._say("warning", self._t("batch_skipped", i=self._row_no(ctx, rid), e=e))
            return
        except PreviousPrintUnknown as e:
            with self.lock:
                self.state["unknown_print"] = {"row_id": rid, "barcode": e.barcode}
            self._say("warning", self._t("batch_skipped", i=self._row_no(ctx, rid), e=e))
            return
        except StationNotVerified as e:
            self._say("error", str(e))
            self.state["_halt"] = True
            return
        except (ValidationFailed, JobInProgress, AlreadyPrinted, ProductionError) as e:
            self._say("warning", self._t("batch_skipped", i=self._row_no(ctx, rid), e=e))
            return
        with self.changed:
            if self.stopped:  # «Stop» was pressed while this one was being prepared: never hand it over
                ctx.browser.cancel(job.job_key, self.user)
                return
            self.jobs = self.jobs + [job.for_browser()]
            self.labels = self.labels + [job.label_id]
            self.changed.notify_all()

    # ---- page side ------------------------------------------------------------------
    def wait_for_job(self, have: int, timeout: float) -> bool:
        """Wait (at most `timeout` seconds) until there are more than `have` jobs or the preparation ended."""
        with self.changed:
            return self.changed.wait_for(lambda: len(self.jobs) > have or self.done, timeout=timeout)

    def snapshot(self) -> list[dict]:
        with self.lock:
            return list(self.jobs)

    def take_messages(self) -> tuple[list, dict]:
        with self.lock:
            msgs, self.messages = self.messages, []
            state = {k: v for k, v in self.state.items() if not k.startswith("_")}
            for k in state:
                self.state.pop(k)
            return msgs, state


def batch_jobs(ctx) -> list[dict]:
    """The labels of the series still to be sent by this browser (all those ready, at once)."""
    b = st.session_state.get("batch")
    if not isinstance(b, Series):
        return []
    jobs = b.snapshot()
    b.handed = len(jobs)
    if not jobs:
        return []
    rows = ctx.browser.statuses([j["job_key"] for j in jobs])
    return [j for j in jobs if rows.get(j["job_key"], {}).get("status") in ("pending", "not_submitted")]


def _finish(ctx, b: "Series", ok: int) -> None:
    st.session_state["batch_result"] = {"doc": b.doc, "ok": ok, "n": b.n}
    st.session_state.pop("batch", None)


def _show(b: "Series") -> None:
    msgs, state = b.take_messages()
    for level, text in msgs:
        flash(level, text)
    st.session_state.update(state)


def step(ctx) -> None:
    """After the browser's report was recorded: hand over the labels prepared meanwhile, or close the series."""
    b = st.session_state.get("batch")
    if not isinstance(b, Series):
        st.session_state.pop("batch", None)
        return
    _show(b)
    jobs = b.snapshot()
    rows = ctx.browser.statuses([j["job_key"] for j in jobs]) if jobs else {}
    states = [rows.get(j["job_key"], {}).get("status") for j in jobs]
    b.ok = sum(s in ("submitted", "manual_confirmed") for s in states)
    bad = next((j for j in jobs if rows.get(j["job_key"], {}).get("status") in
                ("failed", "rejected", "uncertain", "expired", "cancelled")), None)
    if bad is not None:
        row = rows[bad["job_key"]]
        flash("error", T("batch_stopped", b=row["barcode"], e=row["error"] or row["status"]))
        _halt(ctx, b)
        _finish(ctx, b, b.ok)
        st.rerun()
    final = all(s in ("submitted", "manual_confirmed") for s in states)
    if b.done and final:
        _show(b)
        _finish(ctx, b, b.ok)
        st.rerun()
    if final and b.wait_for_job(b.handed, 3.0):
        st.rerun()  # everything handed over is printed and the next label is ready (or none is left)
    # still being sent - or a label was not sent (no DYMO service / printer): the bridge shows «Try again»


def _halt(ctx, b: "Series") -> None:
    """No more labels: the preparation stops and the jobs not sent yet are cancelled."""
    with b.lock:
        b.stopped = True
    for j in batch_jobs(ctx):
        ctx.browser.cancel(j["job_key"], operator())  # a label sent meanwhile is marked uncertain, never lost


def _stop(ctx) -> None:
    b = st.session_state.get("batch")
    if not isinstance(b, Series):
        st.session_state.pop("batch", None)
        return
    _halt(ctx, b)
    jobs = b.snapshot()
    rows = ctx.browser.statuses([j["job_key"] for j in jobs]) if jobs else {}
    _finish(ctx, b, sum(r["status"] in ("submitted", "manual_confirmed") for r in rows.values()))


def progress(ctx) -> None:
    b = st.session_state.get("batch")
    if isinstance(b, Series) and b.n:
        st.progress(min(1.0, b.ok / b.n), text=T("batch_progress", i=min(b.n, b.ok + 1), n=b.n))
        jobs = b.snapshot()
        waiting = [r for r in (ctx.browser.statuses([j["job_key"] for j in jobs]) if jobs else {}).values()
                   if r["status"] == "not_submitted"]
        if waiting:
            st.warning(T("batch_waiting", e=waiting[0]["error"] or ""))
        st.button("⏹ " + T("batch_stop"), key="batch_stop", on_click=_stop, args=(ctx,))


def button(ctx, doc_id: int, *, disabled: bool = False) -> None:
    background_upkeep(ctx)
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


def _stamp_now(ctx, doc_id: int, force: bool = False) -> None:
    try:
        res = ctx.stamper.stamp_document(doc_id, operator(), force=force)
        flash("success", T("stamp_done", m=res))
    except Exception as e:  # noqa: BLE001 - shown to the operator
        flash("error", T("stamp_failed", e=e))


def stamp_status(ctx, doc_id: int) -> None:
    """Whether the printed barcodes are on the scan in Google Drive (and why not)."""
    stamper = getattr(ctx, "stamper", None)
    if stamper is None or not stamper.available():
        return
    printed = stamper.printed_barcodes(doc_id)
    if not printed:
        return
    rec = stamper.record(doc_id)
    import json
    if rec is not None and rec["status"] == "done" and json.loads(rec["barcodes_json"]) == [list(e) for e in printed]:
        if rec["mode"] == "cells":
            st.caption("🖼️ " + T("stamp_ok", n=len(printed)))
            return
        # an older version listed them under the photo: offer to write them into the BARCODE cells
        st.caption("🖼️ " + T("stamp_strip"))
        st.button("🖼️ " + T("stamp_redo"), key=f"stamp_redo_{doc_id}", on_click=_stamp_now, args=(ctx, doc_id, True))
        return
    if rec is not None and rec["status"] == "error":
        st.warning("🖼️ " + T("stamp_failed", e=rec["error"]))
    else:
        st.caption("🖼️ " + T("stamp_waiting"))
    st.button("🖼️ " + T("stamp_retry"), key=f"stamp_now_{doc_id}", on_click=_stamp_now, args=(ctx, doc_id))
