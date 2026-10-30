"""Server side of browser printing: stations (PCs), print jobs and browser reports.

Flow for every print (production label, reprint or TEST0001):

  1. create_*_job(): the label XML is prepared and checked, its SHA-256 stored and
     a print_jobs row committed with status 'pending' (for production labels the
     barcode reservation and the label row are committed BEFORE this).
  2. The job (XML + expected barcode + expected SHA-256) is handed ONLY to the
     Streamlit session that created it; the browser bridge prints it on the PC's
     own DYMO printer.
  3. apply_report(): the browser's result is recorded idempotently (by job_key).
     'submitted' means the DYMO service accepted the job; the physical result is
     confirmed separately by the operator.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from vspm.db.database import Database, utcnow
from vspm.domain.models import LabelContent, TemplateKey
from vspm.dymo.template import TemplateRegistry, check_prepared_label, xml_sha256

TEST_BARCODE = "TEST0001"
JOB_TIMEOUT_SECONDS = 300
FINAL = {"submitted", "failed", "rejected", "uncertain", "expired", "manual_confirmed", "cancelled"}
RESULT_STATUSES = {"submitted", "failed", "not_submitted", "rejected", "uncertain"}


class BrowserPrintError(Exception):
    pass


class JobInProgress(BrowserPrintError):
    pass


@dataclass
class PreparedJob:
    job_key: str
    kind: str
    label_id: Optional[int]
    template_key: str
    barcode: str
    label_xml: str
    xml_sha256: str
    title: str

    def for_browser(self) -> dict:
        return {"job_key": self.job_key, "kind": self.kind, "label_xml": self.label_xml,
                "expected_barcode": self.barcode, "expected_sha256": self.xml_sha256,
                "template_key": self.template_key, "title": self.title}


@dataclass
class ReportOutcome:
    acked: list[str] = field(default_factory=list)
    messages: list[tuple[str, str]] = field(default_factory=list)  # (level, text)
    station_id: Optional[str] = None


def test_content(key: TemplateKey) -> LabelContent:
    return LabelContent(template_key=key, material="ΔΟΚΙΜΗ TEST", thickness="0,00", dimensions="TEST", pieces="0",
                        customer_short="ΔΟΚΙΜΗ / TEST", weight="0", barcode=TEST_BARCODE)


class BrowserPrintService:
    def __init__(self, db: Database, templates: TemplateRegistry, counter_mode: str = "sync",
                 clock: Optional[Callable[[], datetime]] = None):
        self.db = db
        self.templates = templates
        self.counter_mode = counter_mode
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        # Called for results of production jobs: (job_row, result_dict, user) -> list[(level, text)]
        self.on_label_result: Optional[Callable] = None

    # ---- jobs -----------------------------------------------------------------------
    def _expire_stale(self, conn) -> None:
        limit = (self._clock() - timedelta(seconds=JOB_TIMEOUT_SECONDS)).isoformat(timespec="seconds")
        conn.execute("UPDATE print_jobs SET status='expired', error=COALESCE(error, 'no answer from the browser') "
                     "WHERE status='pending' AND created_at < ?", (limit,))

    def _insert_job(self, conn, job: PreparedJob, session_id: str, user: str) -> None:
        conn.execute(
            "INSERT INTO print_jobs (job_key, kind, label_id, template_key, barcode, xml_sha256, status, session_id, "
            "created_by, created_at) VALUES (?,?,?,?,?,?, 'pending', ?,?,?)",
            (job.job_key, job.kind, job.label_id, job.template_key, job.barcode, job.xml_sha256, session_id, user,
             self._clock().isoformat(timespec="seconds")))

    def prepare(self, kind: str, template_key: TemplateKey, content: LabelContent, number: int,
                label_id: Optional[int], title: str, template_sha256: Optional[str] = None) -> PreparedJob:
        """Populate the template. With `template_sha256` (the version the label was first prepared
        with) that exact version is used, so reprints keep the original design; the current
        template is used only when that version is no longer available."""
        spec = self.templates.spec(template_key)
        tpl = self.templates.load_version(template_key, template_sha256) or self.templates.load(template_key)
        xml = tpl.populate(spec, content, number, counter_mode=self.counter_mode)
        problems = check_prepared_label(xml, content.barcode)
        if problems:
            raise BrowserPrintError("; ".join(problems))
        return PreparedJob(uuid.uuid4().hex, kind, label_id, template_key.value, content.barcode, xml,
                           xml_sha256(xml), title)

    def create_test_job(self, template_key: str, session_id: str, user: str) -> PreparedJob:
        """TEST0001 on the given template: no barcode is allocated, nothing is written to Excel."""
        key = TemplateKey(template_key)
        job = self.prepare("test", key, test_content(key), 1, None, f"TEST {key.value}")
        with self.db.transaction() as conn:
            self._expire_stale(conn)
            self._insert_job(conn, job, session_id, user)
            self.db.audit("print_job", job.job_key, "test_job_created", {"template": key.value}, user, conn=conn)
        return job

    def create_label_job(self, label, kind: str, session_id: str, user: str, content: LabelContent) -> PreparedJob:
        """Production label (initial / retry / reprint). The label row (with its reserved
        barcode) must already be committed - checked here."""
        lab = self.db.one("SELECT * FROM labels WHERE id=?", (label["id"],))
        if lab is None or lab["barcode"] != content.barcode:
            raise BrowserPrintError("the label and its reserved barcode must be saved before printing")
        job = self.prepare(kind, TemplateKey(lab["template_key"]), content, lab["number"], lab["id"],
                           f"{lab['template_key']} {lab['barcode']}", template_sha256=lab["template_sha256"])
        try:
            with self.db.transaction() as conn:
                self._expire_stale(conn)
                self._insert_job(conn, job, session_id, user)
                self.db.audit("label", lab["id"], f"browser_{kind}_job_created", {"job": job.job_key}, user,
                              conn=conn)
        except Exception as e:  # unique index: another tab/PC is printing this label right now
            if "unique" in str(e).lower() or "ux_print_jobs_one_pending_per_label" in str(e):
                raise JobInProgress(f"{lab['barcode']} is already being printed from another tab or computer. "
                                    "Wait for it to finish (or 5 minutes) before trying again.") from e
            raise
        return job

    def job(self, job_key: str):
        return self.db.one("SELECT * FROM print_jobs WHERE job_key=?", (job_key,))

    def jobs_for_label(self, label_id: int):
        return self.db.query("SELECT * FROM print_jobs WHERE label_id=? ORDER BY id", (label_id,))

    def pending_job_for_label(self, label_id: int):
        with self.db.transaction() as conn:
            self._expire_stale(conn)
        return self.db.one("SELECT * FROM print_jobs WHERE label_id=? AND status='pending'", (label_id,))

    def unanswered_jobs_for_label(self, label_id: int):
        """Jobs that may or may not have printed (expired without answer or 'uncertain')."""
        with self.db.transaction() as conn:
            self._expire_stale(conn)  # a pending job past its timeout counts as unanswered NOW
        return self.db.query("SELECT * FROM print_jobs WHERE label_id=? AND status IN ('expired','uncertain') "
                             "ORDER BY id", (label_id,))

    def cancel(self, job_key: str, user: str) -> None:
        self.db.execute("UPDATE print_jobs SET status='cancelled' WHERE job_key=? AND status='pending'", (job_key,))
        self.db.audit("print_job", job_key, "cancelled", {}, user)

    # ---- reports from the browser -------------------------------------------------------
    def apply_report(self, value: Optional[dict], user: str) -> ReportOutcome:
        out = ReportOutcome()
        if not value or not isinstance(value, dict):
            return out
        station_id = str(value.get("station_id") or "")[:64]
        out.station_id = station_id or None
        if station_id:
            self._upsert_station(station_id, value, user)
        for r in value.get("results") or []:
            if not isinstance(r, dict) or not r.get("job_key"):
                continue
            out.messages.extend(self._apply_result(r, station_id, user))
            out.acked.append(str(r["job_key"]))
        return out

    def _apply_result(self, r: dict, station_id: str, user: str) -> list[tuple[str, str]]:
        job = self.job(str(r["job_key"]))
        if job is None:
            return []
        status = str(r.get("status"))
        if status not in RESULT_STATUSES:
            return [("error", f"unknown print status '{status}' from the browser")]
        if job["status"] in FINAL:
            return []  # already recorded (idempotent: re-sent from the browser's outbox)
        msgs: list[tuple[str, str]] = []
        if str(r.get("barcode")) != job["barcode"]:
            # Never record a print for a barcode other than the reserved one.
            status = "rejected"
            r = {**r, "status": status, "error": f"browser reported barcode {r.get('barcode')} for job "
                                                 f"{job['barcode']}"}
        elif status == "submitted" and r.get("xml_sha256") != job["xml_sha256"]:
            # Something other than the prepared label may have been printed: the operator must check.
            status = "uncertain"
            r = {**r, "status": status, "error": "printed content differs from the prepared label (SHA-256)"}
            msgs.append(("error", "The browser reported content that differs from the prepared label (SHA-256)."))
        self.db.execute(
            "UPDATE print_jobs SET status=?, station_id=?, printer_name=?, reported_at=?, browser_sha256=?, error=?, "
            "result_json=? WHERE job_key=? AND status NOT IN ('submitted','failed','rejected','uncertain','expired',"
            "'manual_confirmed','cancelled')",
            (status, station_id or r.get("station_id"), r.get("printer"), utcnow(), r.get("xml_sha256"),
             r.get("error") or "; ".join(r.get("problems") or []) or None,
             json.dumps(r, ensure_ascii=False)[:20000], job["job_key"]))
        self.db.audit("print_job", job["job_key"], f"browser_{status}",
                      {"barcode": job["barcode"], "printer": r.get("printer"), "station": station_id,
                       "error": r.get("error"), "problems": r.get("problems")}, user)
        job = self.job(job["job_key"])
        if job["label_id"] is not None and self.on_label_result is not None:
            msgs.extend(self.on_label_result(job, r, user) or [])
        elif job["kind"] == "test" and status == "submitted":
            self.db.execute("UPDATE print_stations SET printer_name=? WHERE station_id=?",
                            (r.get("printer") or "", station_id))
        return msgs

    # ---- stations ---------------------------------------------------------------------------
    def _upsert_station(self, station_id: str, value: dict, user: str) -> None:
        d = value.get("diagnostics") or {}
        now = utcnow()
        name = str(value.get("station_name") or "")[:40]
        fields = {"name": name, "last_seen": now, "last_user": user}
        if d:
            fields.update(user_agent=str(d.get("user_agent", ""))[:300],
                          service_present=int(bool(d.get("service_present"))),
                          printers_json=json.dumps(d.get("printers") or [], ensure_ascii=False)[:5000],
                          diagnostics_json=json.dumps(d, ensure_ascii=False)[:20000],
                          printer_name=str(d.get("selected_printer") or "")[:200])
        sets = ", ".join(f"{k}=?" for k in fields)
        # One transaction, insert-if-missing: two reports from the same PC at the same moment (two tabs,
        # two reruns) must not both try to create the station row.
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO print_stations (station_id, name, last_seen, last_user) VALUES (?,?,?,?) "
                         "ON CONFLICT(station_id) DO NOTHING", (station_id, name, now, user))
            conn.execute(f"UPDATE print_stations SET {sets} WHERE station_id=?", (*fields.values(), station_id))

    def station(self, station_id: str):
        return self.db.one("SELECT * FROM print_stations WHERE station_id=?", (station_id,))

    def stations(self):
        return self.db.query("SELECT * FROM print_stations ORDER BY last_seen DESC")

    def confirm_test_print(self, station_id: str, job_key: str, ok: bool, user: str) -> None:
        """Operator confirms that TEST0001 came out correctly and scans as TEST0001."""
        job = self.job(job_key)
        if job is None or job["kind"] != "test" or job["status"] != "submitted":
            raise BrowserPrintError("only a TEST0001 job that was sent to the printer can be confirmed")
        if ok:
            self.db.execute("UPDATE print_stations SET verified_printer=?, verified_at=?, verified_by=? "
                            "WHERE station_id=?", (job["printer_name"], utcnow(), user, station_id))
        self.db.audit("print_station", station_id, "test_print_confirmed" if ok else "test_print_failed",
                      {"job": job_key, "printer": job["printer_name"]}, user)

    def station_verified(self, station_id: Optional[str], printer: Optional[str]) -> bool:
        """Production printing is allowed only from a browser whose current printer printed TEST0001
        correctly (confirmed by the operator)."""
        if not station_id or not printer:
            return False
        st = self.station(station_id)
        return bool(st and st["verified_at"] and st["verified_printer"] == printer)
