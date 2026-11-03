"""Scanned document workflow: list -> download -> OCR -> production rows."""

from __future__ import annotations

import dataclasses
import threading
import time

import json
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from vspm.db.repository import Repository
from vspm.domain.models import DocumentStatus, ProductionRow
from vspm.integrations.documents import DocumentSource, RemoteDocument
from vspm.integrations.ocr.base import OcrInput, OcrProvider
from vspm.integrations.ocr.postprocess import rows_from_extraction
from vspm.services.customers import CustomerService


class RereadBlocked(Exception):
    """Re-reading a document that already has printed labels needs confirmation."""


@dataclass
class ListedDocument:
    doc_id: int
    remote: RemoteDocument
    status: str
    rows: int
    printed: int
    changed_since_processing: bool


class DocumentService:
    def __init__(self, repo: Repository, source: DocumentSource, customers: CustomerService, cache_dir: Path):
        self.repo = repo
        self.source = source
        self.customers = customers
        self.cache_dir = Path(cache_dir)
        self._listing = None
        self._listing_lock = threading.Lock()
        self.stamper = None  # PhotoStamper: barcodes written onto the scans

    LISTING_TTL = 60.0  # seconds a Drive listing is reused (both PCs share it); "Refresh" forces a new one

    def latest(self, n: int = 5, refresh: bool = False) -> list[ListedDocument]:
        """The n newest scans. The remote listing is reused for LISTING_TTL seconds: every click in
        Streamlit reruns the page, and listing a folder tree on Google Drive takes several requests."""
        with self._listing_lock:
            cached = self._listing
            if refresh or cached is None or cached[1] != n or time.monotonic() - cached[0] > self.LISTING_TTL:
                pairs = []
                ours = self.stamper.stamped_fingerprints() if self.stamper is not None else {}
                for rd in self.source.list_latest(n):
                    if (rd.file_id, rd.revision) in ours:  # our own barcode stamp, not a re-scan
                        rd = dataclasses.replace(rd, revision=ours[(rd.file_id, rd.revision)])
                    doc_id = self.repo.upsert_document(source=rd.source, source_file_id=rd.file_id,
                                                       source_revision=rd.revision, name=rd.name,
                                                       mime_type=rd.mime_type, uploaded_at=rd.uploaded_at)
                    pairs.append((doc_id, rd))
                self._listing = cached = (time.monotonic(), n, pairs)
        pairs = cached[2]
        info = self.repo.document_summaries([d for d, _ in pairs])
        out = []
        for doc_id, rd in pairs:
            i = info.get(doc_id)
            if i is None:
                continue
            out.append(ListedDocument(doc_id, rd, i["doc"]["status"], i["rows"], i["printed"], i["changed"]))
        return out

    def remote_of(self, doc_id: int) -> RemoteDocument:
        d = self.repo.get_document(doc_id)
        return RemoteDocument(d["source_file_id"], d["name"], d["mime_type"], d["uploaded_at"], d["source_revision"],
                              None, d["source"])

    def content(self, doc_id: int) -> tuple[bytes, str]:
        d = self.repo.get_document(doc_id)
        ext = mimetypes.guess_extension(d["mime_type"]) or Path(d["name"]).suffix or ".bin"
        path = self.cache_dir / f"doc{doc_id}{ext}"
        if not path.exists():
            orig = self.stamper.original_revision_for(doc_id) if self.stamper is not None else None
            # the AI always reads the original scan, not our stamped copy
            data = self.source.download_revision(*orig) if orig else self.source.download(self.remote_of(doc_id))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self.repo.update_document(doc_id, local_path=str(path),
                                      status=DocumentStatus.downloaded.value if d["status"] == "new" else d["status"])
        return path.read_bytes(), d["mime_type"]

    def run_ocr(self, doc_id: int, provider: OcrProvider, keep_printed: bool = False) -> list[ProductionRow]:
        printed = self.repo.printed_row_count(doc_id)
        if printed and not keep_printed:
            raise RereadBlocked(
                f"{printed} row(s) of this document already have printed labels. Re-reading replaces only the "
                "unprinted rows; rows with the same number as a printed row are skipped. Confirm to continue.")
        data, mime = self.content(doc_id)
        d = self.repo.get_document(doc_id)
        try:
            out = provider.extract(OcrInput(data, mime, d["name"]))
        except Exception as e:
            self.repo.update_document(doc_id, ocr_error=str(e), status=DocumentStatus.error.value)
            raise
        rows = rows_from_extraction(out.document, self.customers)
        self.repo.replace_rows_for_document(doc_id, rows)
        self.repo.update_document(doc_id, ocr_provider=out.provider, ocr_model=out.model, ocr_result_json=out.raw_json,
                                  ocr_error=None, status=DocumentStatus.ocr_complete.value)
        return self.repo.rows_for_document(doc_id)

    def warnings(self, doc_id: int) -> list[str]:
        d = self.repo.get_document(doc_id)
        if not d or not d["ocr_result_json"]:
            return []
        return json.loads(d["ocr_result_json"]).get("warnings", [])

    def refresh_status(self, doc_id: int) -> str:
        rows = self.repo.rows_for_document(doc_id)
        d = self.repo.get_document(doc_id)
        if not rows:
            return d["status"]
        printed = sum(1 for r in rows if r.label_id is not None)
        status: Optional[str] = None
        if printed == len(rows):
            status = DocumentStatus.completed.value
        elif printed:
            status = DocumentStatus.partially_printed.value
        elif d["status"] in ("ocr_complete",):
            status = DocumentStatus.in_review.value
        if status and status != d["status"]:
            self.repo.update_document(doc_id, status=status)
        return status or d["status"]
