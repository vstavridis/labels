"""Write printed barcodes onto the scanned sheet in Google Drive.

After a label's FIRST print, the barcode is drawn into that row's BARCODE cell of the scan and
the photo is saved back to the same Drive file:

* the original scan is kept in the file's version history (revisions: keepForever) and every
  stamp is drawn on that original with all barcodes printed so far for the sheet - stamping is
  repeatable and two PCs printing rows of the same sheet give the same picture;
* the file is replaced only if it is still the original or our own last stamp; if someone
  re-scanned / replaced it in Drive, nothing is overwritten and the reason is shown;
* the document keeps its original fingerprint, so our own edit is never mistaken for a re-scan.

Runs in a background thread so printing is never slowed down; errors are recorded per document
and can be retried from Settings -> Cloud sources.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Optional

from vspm.db.database import utcnow
from vspm.integrations.documents import DocumentSourceError
from vspm.integrations.stamp import stamp

log = logging.getLogger(__name__)


class PhotoStamper:
    def __init__(self, db, repo, documents, enabled: bool):
        self.db = db
        self.repo = repo
        self.documents = documents
        self.enabled = enabled
        self._lock = threading.Lock()  # one stamp at a time in this app instance
        self._queue_lock = threading.Lock()
        self._waiting: set[int] = set()
        self._worker = False

    @property
    def source(self):
        return self.documents.source if self.documents else None

    def available(self) -> bool:
        return bool(self.enabled and self.source is not None and getattr(self.source, "name", "") == "google"
                    and hasattr(self.source, "replace_content"))

    # ---- what to stamp -------------------------------------------------------------------------
    def printed_barcodes(self, doc_id: int) -> list[tuple[int, str]]:
        return [(r["row_index"], r["barcode"]) for r in self.db.query(
            "SELECT r.row_index, l.barcode FROM labels l JOIN production_rows r ON r.id = l.production_row_id "
            "WHERE r.document_id=? AND l.first_printed_at IS NOT NULL ORDER BY r.row_index", (doc_id,))]

    def record(self, doc_id: int):
        return self.db.one("SELECT * FROM document_stamps WHERE document_id=?", (doc_id,))

    def pending_documents(self) -> list[int]:
        out = []
        for r in self.db.query("SELECT DISTINCT r.document_id FROM labels l JOIN production_rows r "
                               "ON r.id = l.production_row_id WHERE l.first_printed_at IS NOT NULL "
                               "AND r.document_id IS NOT NULL"):
            doc_id = r[0]
            rec = self.record(doc_id)
            if rec is None or rec["status"] != "done" or \
                    json.loads(rec["barcodes_json"]) != [list(e) for e in self.printed_barcodes(doc_id)]:
                out.append(doc_id)
        return out

    # ---- doing it --------------------------------------------------------------------------------
    def request_for_row(self, row_id: int) -> None:
        """Called after a label's first print: stamp its sheet in the background."""
        if not self.available():
            return
        row = self.repo.get_row(row_id)
        if row is None or row.document_id is None:
            return
        self.request_document(row.document_id)

    def request_document(self, doc_id: int, delay: float = 2.0) -> None:
        """Queue a sheet for stamping. One background worker; labels printed in quick succession are
        written onto the photo together (one download / upload) after a short pause."""
        with self._queue_lock:
            self._waiting.add(doc_id)
            if self._worker:
                return
            self._worker = True
        threading.Thread(target=self._work, args=(delay,), name="vspm-stamp", daemon=True).start()

    def _work(self, delay: float) -> None:
        import time
        while True:
            time.sleep(delay)
            with self._queue_lock:
                if not self._waiting:
                    self._worker = False
                    return
                docs, self._waiting = sorted(self._waiting), set()
            for doc_id in docs:
                self._safe(doc_id)

    def catch_up(self) -> int:
        """Stamp every sheet whose printed barcodes are not on its photo yet (e.g. after a restart)."""
        if not self.available():
            return 0
        docs = self.pending_documents()
        for d in docs:
            self.request_document(d, delay=0.5)
        return len(docs)

    def _safe(self, doc_id: int) -> None:
        try:
            self.stamp_document(doc_id, "auto")
        except Exception as e:  # noqa: BLE001 - recorded, shown in Settings, never breaks printing
            log.warning("Stamping document %s failed: %s", doc_id, e)

    def stamp_document(self, doc_id: int, user: str) -> str:
        with self._lock:
            return self._stamp(doc_id, user)

    def _set(self, doc_id: int, file_id: str, **fields) -> None:
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO document_stamps (document_id, file_id, updated_at) VALUES (?,?,?) "
                         "ON CONFLICT(document_id) DO NOTHING", (doc_id, file_id, utcnow()))
            fields["updated_at"] = utcnow()
            sets = ", ".join(f"{k}=?" for k in fields)
            conn.execute(f"UPDATE document_stamps SET {sets} WHERE document_id=?", (*fields.values(), doc_id))

    def _stamp(self, doc_id: int, user: str) -> str:
        doc = self.repo.get_document(doc_id)
        if doc is None or doc["source"] != "google":
            return "skipped"
        entries = self.printed_barcodes(doc_id)
        if not entries:
            return "nothing"
        file_id = doc["source_file_id"]
        try:
            meta = self.source.file_meta(file_id)
            rec = self.record(doc_id)
            if rec is None or not rec["original_revision_id"]:
                if meta.get("md5Checksum") != doc["source_revision"]:
                    raise DocumentSourceError("the scan in Google Drive changed after it was read - not touched")
                self.source.keep_revision_forever(file_id, meta["headRevisionId"])
                self._set(doc_id, file_id, original_revision_id=meta["headRevisionId"],
                          original_md5=meta.get("md5Checksum"))
                rec = self.record(doc_id)
            elif meta.get("md5Checksum") not in (rec["original_md5"], rec["stamped_md5"]):
                raise DocumentSourceError("the scan was replaced in Google Drive (re-scanned?) - not overwritten")
            if rec["status"] == "done" and json.loads(rec["barcodes_json"]) == [list(e) for e in entries] \
                    and meta.get("md5Checksum") == rec["stamped_md5"]:
                return "done"
            original = self.source.download_revision(file_id, rec["original_revision_id"])
            data, mode = stamp(original, entries)
            new = self.source.replace_content(file_id, data, "image/jpeg")
        except DocumentSourceError as e:
            self._set(doc_id, file_id, status="error", error=str(e)[:500])
            self.db.audit("document", doc_id, "stamp_failed", {"error": str(e)[:300]}, user)
            raise
        self._set(doc_id, file_id, stamped_md5=new.get("md5Checksum"), barcodes_json=json.dumps(entries),
                  mode=mode, status="done", error=None)
        self.db.audit("document", doc_id, "barcodes_stamped", {"barcodes": entries, "mode": mode}, user)
        return mode

    def original_revision_for(self, doc_id: int) -> Optional[tuple[str, str]]:
        rec = self.record(doc_id)
        return (rec["file_id"], rec["original_revision_id"]) if rec and rec["original_revision_id"] else None

    def stamped_fingerprints(self) -> dict[tuple[str, str], str]:
        """{(file_id, stamped md5): original md5} - our own edits, not re-scans."""
        return {(r["file_id"], r["stamped_md5"]): r["original_md5"]
                for r in self.db.query("SELECT file_id, stamped_md5, original_md5 FROM document_stamps "
                                       "WHERE stamped_md5 IS NOT NULL")}
