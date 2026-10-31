"""Cloud sources: DYMO templates and the Excel workbook on personal OneDrive, scans on Google Drive.

* Templates: the four .label files are read from the configured OneDrive folder (read-only),
  validated and stored as versions in the shared database (TemplateStore). Both PCs print from
  that store; labels keep the SHA-256 of their template so reprints use the original layout.
* Workbook: production rows are always stored in the database first. With
  VSPM_EXCEL_MODE=onedrive they are written to the OneDrive workbook by ONE writer at a time
  (database lease): download -> append with the same code as the local writer (formulas L-O,
  no duplicate barcodes) -> upload with If-Match: <eTag> (refused if anyone saved meanwhile)
  -> read back and verify. Until an end-to-end test on a separate COPY has passed and an admin
  has enabled it, nothing is written to the production workbook: rows stay staged.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
import time
import uuid
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

from vspm.db.database import utcnow
from vspm.domain.models import ExcelRowPayload, ExcelStatus, TemplateKey
from vspm.dymo.template import LabelTemplate, TemplateError
from vspm.excel.writer import ExcelSyncError, append_rows, sha256, verify_rows
from vspm.excel.xlsx_package import SheetEditor, XlsxError, XlsxPackage
from vspm.integrations.onedrive import (
    DriveItem, GraphFiles, MicrosoftAuth, NotConnected, OneDriveError, PreconditionFailed, SecretBox, TokenStore, nfc,
    parse_template_names,
)

log = logging.getLogger(__name__)

CONFIG_KEYS = ("onedrive_templates_url", "onedrive_workbook_url", "onedrive_workbook_test_url",
               "onedrive_template_files", "gdrive_folder_url")
KEEP_BACKUPS = 20
HTTP_CLIENT_FACTORY: Optional[Callable[[], object]] = None  # tests: route Microsoft calls to a test double


def parse_drive_folder_id(value: str) -> str:
    """Google Drive folder URL (or bare id) -> folder id."""
    v = (value or "").strip()
    if not v:
        return ""
    m = re.search(r"/folders/([A-Za-z0-9_-]{10,})", v)
    if m:
        return m.group(1)
    q = parse_qs(urlparse(v).query).get("id")
    if q and re.fullmatch(r"[A-Za-z0-9_-]{10,}", q[0]):
        return q[0]
    if re.fullmatch(r"[A-Za-z0-9_-]{10,}", v):
        return v
    raise ValueError("Not a Google Drive folder link (expected https://drive.google.com/drive/folders/<id>)")


class CloudConfig:
    """Source settings kept in the database (shared by both PCs); defaults from the Streamlit secrets."""

    def __init__(self, db, settings):
        self.db = db
        self.s = settings

    def get(self, key: str) -> str:
        assert key in CONFIG_KEYS
        v = self.db.get_setting(f"cloud.{key}")
        return v if v is not None else str(getattr(self.s, key, "") or "")

    def set(self, key: str, value: str, user: str) -> None:
        assert key in CONFIG_KEYS
        self.db.set_setting(f"cloud.{key}", value.strip())
        self.db.audit("cloud_config", key, "changed", {"value": value.strip()[:200]}, user)

    def get_json(self, key: str) -> dict:
        v = self.db.get_setting(f"cloud.{key}")
        return json.loads(v) if v else {}

    def set_json(self, key: str, value: dict) -> None:
        self.db.set_setting(f"cloud.{key}", json.dumps(value, ensure_ascii=False, default=str))


class OneDrive:
    """Connected Microsoft account + resolved OneDrive files."""

    def __init__(self, db, settings, http=None, clock: Callable[[], float] = time.time):
        self.db = db
        self.s = settings
        self.http = http if http is not None else (HTTP_CLIENT_FACTORY() if HTTP_CLIENT_FACTORY else None)
        self.clock = clock
        self.store = TokenStore(db, lambda: SecretBox(settings.secret_key))
        self._auth: Optional[MicrosoftAuth] = None

    @property
    def auth(self) -> MicrosoftAuth:
        if self._auth is None:
            self._auth = MicrosoftAuth(self.s.ms_client_id, self.store, tenant=self.s.ms_tenant, http=self.http,
                                       clock=self.clock)
        return self._auth

    @property
    def files(self) -> GraphFiles:
        return GraphFiles(self.auth.access_token, http=self.http)

    def account(self) -> Optional[dict]:
        return self.store.info()

    def disconnect(self, user: str) -> None:
        self.store.clear()
        self.db.audit("cloud", "microsoft", "disconnected", {}, user)

    # ---- resolved items ----------------------------------------------------------------------
    def file_row(self, role: str):
        return self.db.one("SELECT * FROM cloud_files WHERE role=?", (role,))

    def resolve(self, role: str, url: str) -> DriveItem:
        """Item for a sharing link (or a path in the account's OneDrive). Cached by role + link."""
        url = (url or "").strip()
        if not url:
            raise OneDriveError(f"No OneDrive link configured for {role}.")
        row = self.file_row(role)
        if row and row["source_url"] == url and row["item_id"]:
            item = self.files.item(row["drive_id"], row["item_id"])
        elif url.lower().startswith(("http://", "https://")):
            item = self.files.resolve_link(url)
        else:
            item = self.files.resolve_path(url)
        self._save(role, url, item)
        return item

    def _save(self, role: str, url: str, item: DriveItem, sha: Optional[str] = None, synced: bool = False) -> None:
        now = utcnow()
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO cloud_files (role) VALUES (?) ON CONFLICT(role) DO NOTHING", (role,))
            conn.execute("UPDATE cloud_files SET source_url=?, drive_id=?, item_id=?, name=?, etag=?, size=?, "
                         "modified=?, sha256=COALESCE(?, sha256), last_sync=CASE WHEN ?=1 THEN ? ELSE last_sync END, "
                         "last_error=CASE WHEN ?=1 THEN NULL ELSE last_error END WHERE role=?",
                         (url, item.drive_id, item.item_id, item.name, item.etag, item.size, item.modified, sha,
                          int(synced), now, int(synced), role))

    def record_error(self, role: str, error: str) -> None:
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO cloud_files (role) VALUES (?) ON CONFLICT(role) DO NOTHING", (role,))
            conn.execute("UPDATE cloud_files SET last_error=?, last_error_at=? WHERE role=?",
                         (error[:500], utcnow(), role))


# ---- templates ---------------------------------------------------------------------------------
@dataclass
class TemplateStatus:
    key: str
    filename: str = ""
    status: str = ""  # updated | unchanged | error
    sha256: str = ""
    etag: str = ""
    error: str = ""


class TemplateSync:
    def __init__(self, db, onedrive: OneDrive, templates, config: CloudConfig, clock: Callable[[], float] = time.time):
        self.db = db
        self.od = onedrive
        self.templates = templates
        self.config = config
        self.clock = clock

    def last_refresh(self) -> dict:
        return self.config.get_json("templates_last_refresh")

    def maybe_refresh(self, max_age_seconds: int) -> Optional[list[TemplateStatus]]:
        """Refresh if configured, connected and the last attempt is older than max_age (both PCs share it)."""
        if not self.config.get("onedrive_templates_url") or self.od.account() is None:
            return None
        last = self.last_refresh()
        if last.get("at_epoch") and self.clock() - float(last["at_epoch"]) < max_age_seconds:
            return None
        try:
            return self.refresh("auto")
        except OneDriveError:
            return None  # recorded; shown in Settings

    def refresh(self, user: str, force: bool = False) -> list[TemplateStatus]:
        url = self.config.get("onedrive_templates_url")
        out: list[TemplateStatus] = []
        try:
            folder = self.od.resolve("templates_folder", url)
            if not folder.is_folder:
                raise OneDriveError(f"'{folder.name}' is a file, not a folder - use the link of the templates folder.")
            files = [f for f in self.od.files.children(folder.drive_id, folder.item_id)
                     if not f.is_folder and f.name.lower().endswith(".label")]
            chosen = self._choose(files)
            for key in TemplateKey:
                item, err = chosen.get(key.value, (None, "not found"))
                st = TemplateStatus(key.value, filename=item.name if item else "")
                if item is None:
                    st.status, st.error = "error", err
                    self.od.record_error(f"template:{key.value}", err)
                else:
                    self._sync_one(key, item, st, user, force)
                out.append(st)
            self.od._save("templates_folder", url, folder, synced=True)
        except OneDriveError as e:
            self.od.record_error("templates_folder", str(e))
            self.config.set_json("templates_last_refresh", {"at": utcnow(), "at_epoch": self.clock(), "ok": False,
                                                           "error": str(e)})
            raise
        ok = all(s.status != "error" for s in out)
        self.config.set_json("templates_last_refresh", {
            "at": utcnow(), "at_epoch": self.clock(), "ok": ok,
            "templates": {s.key: {"file": s.filename, "status": s.status, "sha256": s.sha256, "error": s.error}
                          for s in out}})
        return out

    def _choose(self, files: list[DriveItem]) -> dict[str, tuple[Optional[DriveItem], str]]:
        """Exact file names from the configuration; otherwise detection by the template's COUNTER prefix."""
        by_name = {nfc(f.name): f for f in files}
        names = parse_template_names(self.config.get("onedrive_template_files"))
        out: dict[str, tuple[Optional[DriveItem], str]] = {}
        undecided = [k for k in TemplateKey if k.value not in names]
        for k, name in names.items():
            f = by_name.get(nfc(name))
            out[k] = (f, "" if f else f"'{name}' not found in the OneDrive folder (found: "
                                      f"{', '.join(sorted(by_name)) or 'no .label files'})")
        if undecided:
            mapped = {id(v[0]) for v in out.values() if v[0] is not None}
            prefixes: dict[str, list[DriveItem]] = {}
            for f in files:
                if id(f) in mapped:
                    continue
                try:
                    data, _ = self.od.files.download(f.drive_id, f.item_id)
                    info = LabelTemplate.from_bytes(data).counter_info("COUNTER")
                except (TemplateError, OneDriveError):
                    continue
                if info:
                    prefixes.setdefault(info["prefix"], []).append(f)
            for k in undecided:
                cands = prefixes.get(self.templates.spec(k).barcode_prefix, [])
                if len(cands) == 1:
                    out[k.value] = (cands[0], "")
                else:
                    out[k.value] = (None, "no .label file with COUNTER prefix "
                                          f"{self.templates.spec(k).barcode_prefix}" if not cands else
                                    f"several files have COUNTER prefix {self.templates.spec(k).barcode_prefix}: "
                                    f"{[c.name for c in cands]} - set the file names (VS=…;PG=…)")
        return out

    def _sync_one(self, key: TemplateKey, item: DriveItem, st: TemplateStatus, user: str, force: bool) -> None:
        role = f"template:{key.value}"
        prev = self.od.file_row(role)
        st.etag = item.etag
        if (not force and prev and prev["item_id"] == item.item_id and prev["etag"] == item.etag and prev["sha256"]
                and self.templates.store.by_sha(key, prev["sha256"]) is not None):
            st.status, st.sha256 = "unchanged", prev["sha256"]
            active = self.templates.store.active(key)
            if active is None or active["sha256"] != prev["sha256"]:
                data = self.templates.store.by_sha(key, prev["sha256"])
                self.templates.store.upload(key, self.templates.spec(key), item.name, data, f"{user} (OneDrive)")
            return
        try:
            data, meta = self.od.files.download(item.drive_id, item.item_id)
            sha = self.templates.store.upload(key, self.templates.spec(key), item.name, data, f"{user} (OneDrive)")
        except (TemplateError, OneDriveError) as e:
            # The previous version (if any) stays active: a broken file never replaces a good template.
            st.status, st.error = "error", f"{item.name}: {e}"
            self.od.record_error(role, st.error)
            return
        self.od._save(role, item.web_url or item.name, meta, sha=sha, synced=True)
        st.status, st.sha256 = ("updated" if not prev or prev["sha256"] != sha else "unchanged"), sha


# ---- workbook ----------------------------------------------------------------------------------
class Lease:
    """One holder at a time across all app instances (database row with expiry)."""

    def __init__(self, db, name: str, seconds: float = 180, clock: Callable[[], float] = time.time):
        self.db, self.name, self.seconds, self.clock = db, name, seconds, clock
        self.owner = uuid.uuid4().hex

    def acquire(self) -> bool:
        now = self.clock()
        with self.db.transaction() as conn:
            row = conn.execute("SELECT owner, expires_at FROM leases WHERE name=?", (self.name,)).fetchone()
            if row is not None and row["expires_at"] > now and row["owner"] != self.owner:
                return False
            conn.execute("INSERT INTO leases (name, owner, expires_at) VALUES (?,?,?) ON CONFLICT(name) DO UPDATE "
                         "SET owner=excluded.owner, expires_at=excluded.expires_at",
                         (self.name, self.owner, now + self.seconds))
        return True

    def release(self) -> None:
        self.db.execute("DELETE FROM leases WHERE name=? AND owner=?", (self.name, self.owner))


@dataclass
class SyncResult:
    status: str  # synchronized | staged | busy | conflict | error | nothing
    message: str
    rows: dict[str, int]


class WorkbookSync:
    def __init__(self, db, onedrive: OneDrive, config: CloudConfig, settings, repo,
                 payload_for: Callable[[object], ExcelRowPayload], clock: Callable[[], float] = time.time):
        self.db, self.od, self.config, self.s, self.repo = db, onedrive, config, settings, repo
        self.payload_for = payload_for
        self.clock = clock

    # ---- status / checks ------------------------------------------------------------------
    def inspect(self, role: str) -> dict:
        """Download (read-only) and describe the workbook. Nothing is written."""
        url = self.config.get("onedrive_workbook_url" if role == "workbook" else "onedrive_workbook_test_url")
        try:
            item = self.od.resolve(role, url)
            if item.is_folder:
                raise OneDriveError(f"'{item.name}' is a folder - use the link of the .xlsx file.")
            data, meta = self.od.files.download(item.drive_id, item.item_id)
            try:
                ed = SheetEditor(XlsxPackage(data), self.s.excel_sheet)
            except XlsxError as e:
                raise OneDriveError(f"{item.name}: {e}") from e
            last = ed.last_data_row()
            info = {"name": meta.name, "size": len(data), "modified": meta.modified, "etag": meta.etag,
                    "sheet": self.s.excel_sheet, "last_row": last,
                    "barcodes": sum(1 for v in ed.column_values("H").values() if str(v).strip()),
                    "formulas_L_O_in_last_row": all(ed.value(last, c).kind == "formula" for c in "LMNO")}
        except OneDriveError as e:
            self.od.record_error(role, str(e))
            raise
        self.od._save(role, url, meta, sha=sha256(data), synced=True)
        return info

    def validation(self) -> dict:
        return self.config.get_json("excel_validation")

    def production_enabled(self) -> bool:
        flag = self.config.get_json("excel_production")
        prod = self.od.file_row("workbook")
        v = self.validation()
        return bool(flag.get("enabled") and v.get("ok") and prod is not None and prod["item_id"]
                    and prod["item_id"] != v.get("item_id"))

    def validate_copy(self, user: str) -> dict:
        """End-to-end test on the separate COPY: append a test row, upload with If-Match, read back,
        compare every other part of the file, and check that a stale eTag is refused."""
        checks: list[tuple[str, bool, str]] = []
        test_url = self.config.get("onedrive_workbook_test_url")
        prod_url = self.config.get("onedrive_workbook_url")
        result = {"ok": False, "at": utcnow(), "by": user, "checks": checks}
        try:
            item = self.od.resolve("workbook_test", test_url)
            if prod_url:
                prod = self.od.resolve("workbook", prod_url)
                if prod.item_id == item.item_id:
                    raise OneDriveError("The test copy link points to the PRODUCTION workbook. Make a separate copy "
                                        "in OneDrive and use its link.")
            original, meta = self.od.files.download(item.drive_id, item.item_id)
            barcode = f"TEST{int(self.clock()) % 100000000:08d}"
            payload = ExcelRowPayload(material="ΔΟΚΙΜΗ VSPM", thickness="0,50", dimensions="1000", pieces="1",
                                      weight="1", customer="TEST", category="VS", barcode=barcode, location="",
                                      roll="", print_date=date.today().isoformat())
            new_bytes, results, written = append_rows(original, self.s.excel_sheet, [payload],
                                                      date_format=self.s.excel_date_format)
            checks.append(("row prepared", True, f"row {written[0][0]}"))
            uploaded = self.od.files.replace_if_match(item.drive_id, item.item_id, new_bytes, meta.etag)
            checks.append(("upload with If-Match accepted", True, uploaded.etag[:20]))
            back, meta2 = self.od.files.download(item.drive_id, item.item_id)
            verify_rows(back, self.s.excel_sheet, written)
            checks.append(("row read back from OneDrive: A-K, P values, formulas L-O, unique barcode", True, barcode))
            changed = _changed_parts(original, back)
            allowed = {"xl/workbook.xml", "xl/styles.xml", XlsxPackage(original).sheet_part(self.s.excel_sheet)}
            extra = sorted(changed - allowed)
            checks.append(("all other parts of the workbook unchanged", not extra, ", ".join(extra) or
                           f"{len(_parts(original)) - len(changed)} parts identical"))
            try:
                self.od.files.replace_if_match(item.drive_id, item.item_id, original, meta.etag)  # stale eTag
                checks.append(("stale eTag refused", False, "OneDrive ACCEPTED an upload with an old eTag - automatic "
                               "writing would not be safe"))
            except PreconditionFailed:
                checks.append(("stale eTag refused (HTTP 412)", True, "another PC's or person's save is never "
                               "overwritten"))
            result.update(ok=all(c[1] for c in checks), item_id=item.item_id, name=item.name, barcode=barcode)
        except (OneDriveError, ExcelSyncError, XlsxError) as e:
            checks.append(("test", False, str(e)))
        self.config.set_json("excel_validation", result)
        self.db.audit("cloud", "excel_validation", "run", {"ok": result["ok"], "checks": checks}, user)
        return result

    def set_production(self, enabled: bool, user: str) -> None:
        if enabled:
            v = self.validation()
            if not v.get("ok"):
                raise OneDriveError("Run the end-to-end test on the workbook COPY first (it must pass).")
            prod = self.od.resolve("workbook", self.config.get("onedrive_workbook_url"))
            if prod.item_id == v.get("item_id"):
                raise OneDriveError("The production link and the tested copy are the same file.")
        self.config.set_json("excel_production", {"enabled": bool(enabled), "by": user, "at": utcnow()})
        self.db.audit("cloud", "excel_production", "enabled" if enabled else "disabled", {}, user)

    # ---- the queue ----------------------------------------------------------------------------
    def pending_labels(self, label_ids: Optional[list[int]] = None) -> list:
        rows = self.db.query("SELECT * FROM labels WHERE first_printed_at IS NOT NULL AND excel_status IN "
                             "('pending','staged','error','conflict') ORDER BY id")
        return [r for r in rows if label_ids is None or r["id"] in label_ids]

    def sync_pending(self, user: str, label_ids: Optional[list[int]] = None, attempts: int = 3) -> SyncResult:
        labels = self.pending_labels(label_ids)
        if not labels:
            return SyncResult("nothing", "Nothing waiting for Excel.", {})
        if not self.production_enabled():
            for lab in labels:
                if lab["excel_status"] != ExcelStatus.staged.value:
                    self.repo.update_label(lab["id"], excel_status=ExcelStatus.staged.value)
            return SyncResult("staged", "Automatic OneDrive writing is not enabled yet: rows stay staged.", {})
        lease = Lease(self.db, "excel_writer", clock=self.clock)
        if not lease.acquire():
            return SyncResult("busy", "The other PC is updating the workbook right now; the rows stay pending and "
                                      "will be written on the next sync.", {})
        try:
            return self._write(labels, user, attempts)
        finally:
            lease.release()

    def _write(self, labels: list, user: str, attempts: int) -> SyncResult:
        payloads = {lab["id"]: self.payload_for(lab) for lab in labels}
        item = None
        last_error = ""
        for attempt in range(1, attempts + 1):
            try:
                item = item or self.od.resolve("workbook", self.config.get("onedrive_workbook_url"))
                data, meta = self.od.files.download(item.drive_id, item.item_id)
                new_bytes, results, written = append_rows(data, self.s.excel_sheet, list(payloads.values()),
                                                          date_format=self.s.excel_date_format)
                rows = {r.barcode: r.row_number for r in results}
                if new_bytes is not None:
                    self._backup("workbook", meta, data, f"before adding {len(written)} row(s)")
                    uploaded = self.od.files.replace_if_match(item.drive_id, item.item_id, new_bytes, meta.etag)
                    back, back_meta = self.od.files.download(item.drive_id, item.item_id)
                    verify_rows(back, self.s.excel_sheet, written)
                    self.od._save("workbook", self.config.get("onedrive_workbook_url"), back_meta, sha=sha256(back),
                                  synced=True)
                else:
                    uploaded = meta
                for lab in labels:
                    p = payloads[lab["id"]]
                    res = next(r for r in results if r.barcode == p.barcode)
                    self.repo.update_label(lab["id"], excel_status=ExcelStatus.synchronized.value,
                                           excel_row_number=res.row_number)
                    self.repo.add_excel_attempt(lab["id"], mode="onedrive", status="synchronized" if
                                                res.status == "written" else "already_present",
                                                row_number=res.row_number, workbook_path=f"OneDrive:{item.name}",
                                                hash_before=sha256(data), hash_after=uploaded.etag[:60])
                self.db.audit("cloud", "workbook", "rows_written", {"rows": rows, "attempt": attempt}, user)
                return SyncResult("synchronized", f"{len(written)} row(s) written to {item.name}, "
                                                  f"{len(results) - len(written)} already present.", rows)
            except PreconditionFailed as e:
                last_error = f"{e} (attempt {attempt}/{attempts})"
                continue  # someone saved the workbook meanwhile: read it again, never overwrite
            except (OneDriveError, ExcelSyncError, XlsxError) as e:
                return self._fail(labels, ExcelStatus.error, str(e), user)
        return self._fail(labels, ExcelStatus.conflict, "The workbook kept changing while the rows were being added "
                          f"({last_error}). Nothing was overwritten. Close it in Excel and press Sync again.", user)

    def _fail(self, labels: list, status: ExcelStatus, error: str, user: str) -> SyncResult:
        for lab in labels:
            self.repo.update_label(lab["id"], excel_status=status.value)
            self.repo.add_excel_attempt(lab["id"], mode="onedrive", status=status.value, error=error[:500],
                                        workbook_path="OneDrive")
        self.od.record_error("workbook", error)
        self.db.audit("cloud", "workbook", "write_failed", {"error": error[:500], "labels": [l["id"] for l in labels]},
                      user)
        return SyncResult(status.value, error, {})

    def _backup(self, role: str, meta: DriveItem, data: bytes, reason: str) -> None:
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO workbook_backups (role, item_id, etag, sha256, size, content_b64, reason, "
                         "created_at) VALUES (?,?,?,?,?,?,?,?)",
                         (role, meta.item_id, meta.etag, sha256(data), len(data),
                          base64.b64encode(data).decode("ascii"), reason, utcnow()))
            conn.execute("DELETE FROM workbook_backups WHERE id NOT IN (SELECT id FROM workbook_backups "
                         "ORDER BY id DESC LIMIT ?)", (KEEP_BACKUPS,))

    def backups(self) -> list:
        return self.db.query("SELECT id, role, etag, sha256, size, reason, created_at FROM workbook_backups "
                             "ORDER BY id DESC")

    def backup_bytes(self, backup_id: int) -> tuple[str, bytes]:
        r = self.db.one("SELECT * FROM workbook_backups WHERE id=?", (backup_id,))
        stamp = datetime.fromisoformat(r["created_at"]).strftime("%Y%m%d-%H%M%S")
        return f"workbook-backup-{stamp}.xlsx", base64.b64decode(r["content_b64"])


def _parts(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {n: z.read(n) for n in z.namelist()}


def _changed_parts(a: bytes, b: bytes) -> set[str]:
    pa, pb = _parts(a), _parts(b)
    return {n for n in set(pa) | set(pb) if pa.get(n) != pb.get(n)}


class CloudServices:
    def __init__(self, db, settings, templates, repo, payload_for, http=None, clock: Callable[[], float] = time.time):
        self.config = CloudConfig(db, settings)
        self.onedrive = OneDrive(db, settings, http=http, clock=clock)
        self.templates = TemplateSync(db, self.onedrive, templates, self.config, clock=clock)
        self.workbook = WorkbookSync(db, self.onedrive, self.config, settings, repo, payload_for, clock=clock)

    def gdrive_folder_id(self) -> str:
        return parse_drive_folder_id(self.config.get("gdrive_folder_url"))


__all__ = ["CloudServices", "CloudConfig", "OneDrive", "TemplateSync", "WorkbookSync", "Lease", "SyncResult",
           "parse_drive_folder_id", "NotConnected", "OneDriveError"]
