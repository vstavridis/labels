"""Scanned document workflow: list -> download -> OCR -> production rows."""

from __future__ import annotations

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

    def latest(self, n: int = 5) -> list[ListedDocument]:
        out = []
        for rd in self.source.list_latest(n):
            doc_id = self.repo.upsert_document(source=rd.source, source_file_id=rd.file_id,
                                               source_revision=rd.revision, name=rd.name, mime_type=rd.mime_type,
                                               uploaded_at=rd.uploaded_at)
            doc = self.repo.get_document(doc_id)
            rows = self.repo.rows_for_document(doc_id)
            changed = any(o["status"] != "new" for o in self.repo.other_revisions(rd.source, rd.file_id, doc_id))
            out.append(ListedDocument(doc_id, rd, doc["status"], len(rows),
                                      sum(1 for r in rows if r.label_id is not None), changed))
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
            data = self.source.download(self.remote_of(doc_id))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self.repo.update_document(doc_id, local_path=str(path),
                                      status=DocumentStatus.downloaded.value if d["status"] == "new" else d["status"])
        return path.read_bytes(), d["mime_type"]

    def run_ocr(self, doc_id: int, provider: OcrProvider) -> list[ProductionRow]:
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
