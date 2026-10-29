"""Data access for documents, production rows, labels and attempts."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

from vspm.db.database import Database, utcnow
from vspm.domain.models import ProductionRow, RowStatus, TemplateKey

ROW_COLUMNS = [
    "customer_full", "customer_short", "material", "thickness", "dimensions", "weight",
    "pieces", "packages", "source_barcode", "source_roll", "location", "document_date",
]


class Repository:
    def __init__(self, db: Database):
        self.db = db

    # ---- documents ------------------------------------------------------------
    def upsert_document(self, *, source: str, source_file_id: str, source_revision: str, name: str,
                        mime_type: str, uploaded_at: str | None) -> int:
        existing = self.db.one(
            "SELECT id FROM documents WHERE source=? AND source_file_id=? AND source_revision=?",
            (source, source_file_id, source_revision),
        )
        if existing:
            return existing["id"]
        now = utcnow()
        return self.db.execute(
            "INSERT INTO documents (source, source_file_id, source_revision, name, mime_type, uploaded_at, "
            "status, created_at, updated_at) VALUES (?,?,?,?,?,?, 'new', ?, ?)",
            (source, source_file_id, source_revision, name, mime_type, uploaded_at, now, now),
        )

    def find_document(self, source: str, source_file_id: str, source_revision: str | None = None) -> sqlite3.Row | None:
        if source_revision is None:
            return self.db.one(
                "SELECT * FROM documents WHERE source=? AND source_file_id=? ORDER BY id DESC LIMIT 1",
                (source, source_file_id))
        return self.db.one("SELECT * FROM documents WHERE source=? AND source_file_id=? AND source_revision=?",
                           (source, source_file_id, source_revision))

    def other_revisions(self, source: str, source_file_id: str, exclude_id: int) -> list[sqlite3.Row]:
        return self.db.query("SELECT * FROM documents WHERE source=? AND source_file_id=? AND id<>?",
                             (source, source_file_id, exclude_id))

    def get_document(self, doc_id: int) -> sqlite3.Row | None:
        return self.db.one("SELECT * FROM documents WHERE id=?", (doc_id,))

    def update_document(self, doc_id: int, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = utcnow()
        sets = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE documents SET {sets} WHERE id=?", (*fields.values(), doc_id))

    def list_documents(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.db.query("SELECT * FROM documents ORDER BY COALESCE(uploaded_at, created_at) DESC LIMIT ?",
                             (limit,))

    # ---- production rows ------------------------------------------------------
    def _row_from_db(self, r: sqlite3.Row) -> ProductionRow:
        label = self.db.one("SELECT id FROM labels WHERE production_row_id=?", (r["id"],))
        return ProductionRow(
            id=r["id"], document_id=r["document_id"], row_index=r["row_index"],
            **{c: r[c] for c in ROW_COLUMNS},
            template_key=TemplateKey(r["template_key"]) if r["template_key"] else None,
            template_suggested=TemplateKey(r["template_suggested"]) if r["template_suggested"] else None,
            template_reason=r["template_reason"],
            template_needs_confirmation=bool(r["template_needs_confirmation"]),
            flags=json.loads(r["flags_json"] or "{}"),
            ocr_original=json.loads(r["ocr_original_json"] or "{}"),
            approved=bool(r["approved"]), status=RowStatus(r["status"]),
            label_id=label["id"] if label else None,
        )

    def add_row(self, row: ProductionRow, conn: sqlite3.Connection | None = None) -> int:
        now = utcnow()
        params = (
            row.document_id, row.row_index, *[getattr(row, c) for c in ROW_COLUMNS],
            row.template_key.value if row.template_key else None,
            row.template_suggested.value if row.template_suggested else None,
            row.template_reason, int(row.template_needs_confirmation),
            json.dumps(row.flags, ensure_ascii=False), json.dumps(row.ocr_original, ensure_ascii=False),
            int(row.approved), row.status.value, now, now,
        )
        sql = (
            "INSERT INTO production_rows (document_id, row_index, " + ", ".join(ROW_COLUMNS) +
            ", template_key, template_suggested, template_reason, template_needs_confirmation, flags_json, "
            "ocr_original_json, approved, status, created_at, updated_at) VALUES (" +
            ",".join("?" * (len(params))) + ")"
        )
        if conn is not None:
            return conn.execute(sql, params).lastrowid
        return self.db.execute(sql, params)

    def save_row(self, row: ProductionRow) -> None:
        """Save operator edits. Refuses to edit printed values of a row that has a label."""
        assert row.id is not None
        current = self.get_row(row.id)
        if current and current.label_id is not None:
            locked = [c for c in ("material", "thickness", "dimensions", "weight", "pieces", "customer_short")
                      if getattr(current, c) != getattr(row, c)] + (
                ["template_key"] if current.template_key != row.template_key else [])
            if locked:
                raise ValueError(f"Row {row.id} already has a printed label; cannot change {locked}")
        self.db.execute(
            "UPDATE production_rows SET " + ", ".join(f"{c}=?" for c in ROW_COLUMNS) +
            ", template_key=?, template_reason=?, template_needs_confirmation=?, flags_json=?, approved=?, "
            "status=?, row_index=?, updated_at=? WHERE id=?",
            (*[getattr(row, c) for c in ROW_COLUMNS],
             row.template_key.value if row.template_key else None, row.template_reason,
             int(row.template_needs_confirmation), json.dumps(row.flags, ensure_ascii=False),
             int(row.approved), row.status.value, row.row_index, utcnow(), row.id),
        )

    def set_row_status(self, row_id: int, status: RowStatus, conn: sqlite3.Connection | None = None) -> None:
        sql, p = "UPDATE production_rows SET status=?, updated_at=? WHERE id=?", (status.value, utcnow(), row_id)
        if conn is not None:
            conn.execute(sql, p)
        else:
            self.db.execute(sql, p)

    def delete_row(self, row_id: int) -> None:
        if self.db.one("SELECT id FROM labels WHERE production_row_id=?", (row_id,)):
            raise ValueError("Cannot delete a row that already has a printed label")
        self.db.execute("UPDATE production_rows SET deleted=1, updated_at=? WHERE id=?", (utcnow(), row_id))

    def get_row(self, row_id: int) -> Optional[ProductionRow]:
        r = self.db.one("SELECT * FROM production_rows WHERE id=? AND deleted=0", (row_id,))
        return self._row_from_db(r) if r else None

    def rows_for_document(self, doc_id: int) -> list[ProductionRow]:
        return [self._row_from_db(r) for r in self.db.query(
            "SELECT * FROM production_rows WHERE document_id=? AND deleted=0 ORDER BY row_index, id", (doc_id,))]

    def replace_rows_for_document(self, doc_id: int, rows: list[ProductionRow]) -> list[int]:
        """Replace the unprinted rows after re-running OCR.

        Printed rows are kept, and a new row with the same row number as a printed
        row is NOT inserted (it would be a second, printable copy of a production
        line that already has a label).
        """
        ids = []
        with self.db.transaction() as conn:
            printed = {r["row_index"] for r in conn.execute(
                "SELECT row_index FROM production_rows WHERE document_id=? AND deleted=0 AND id IN "
                "(SELECT production_row_id FROM labels)", (doc_id,))}
            conn.execute(
                "UPDATE production_rows SET deleted=1, updated_at=? WHERE document_id=? AND deleted=0 AND id NOT IN "
                "(SELECT production_row_id FROM labels)", (utcnow(), doc_id))
            for r in rows:
                if r.row_index in printed:
                    continue
                r.document_id = doc_id
                ids.append(self.add_row(r, conn))
        return ids

    def printed_row_count(self, doc_id: int) -> int:
        return self.db.one("SELECT COUNT(*) FROM production_rows r JOIN labels l ON l.production_row_id = r.id "
                           "WHERE r.document_id=? AND r.deleted=0", (doc_id,))[0]

    # ---- labels ---------------------------------------------------------------
    def get_label(self, label_id: int) -> sqlite3.Row | None:
        return self.db.one("SELECT * FROM labels WHERE id=?", (label_id,))

    def label_for_row(self, row_id: int) -> sqlite3.Row | None:
        return self.db.one("SELECT * FROM labels WHERE production_row_id=?", (row_id,))

    def update_label(self, label_id: int, conn: sqlite3.Connection | None = None, **fields: Any) -> None:
        fields["updated_at"] = utcnow()
        sets = ", ".join(f"{k}=?" for k in fields)
        sql, p = f"UPDATE labels SET {sets} WHERE id=?", (*fields.values(), label_id)
        if conn is not None:
            conn.execute(sql, p)
        else:
            self.db.execute(sql, p)

    def search_labels(self, *, date_from: str | None = None, date_to: str | None = None, customer: str | None = None,
                      template_key: str | None = None, barcode: str | None = None, document: str | None = None,
                      print_status: str | None = None, excel_status: str | None = None,
                      limit: int = 500) -> list[sqlite3.Row]:
        sql = ("SELECT l.*, r.customer_full, r.customer_short, r.document_id, d.name AS document_name "
               "FROM labels l JOIN production_rows r ON r.id = l.production_row_id "
               "LEFT JOIN documents d ON d.id = r.document_id WHERE 1=1")
        p: list[Any] = []
        if date_from:
            sql += " AND l.print_date_local >= ?"; p.append(date_from)
        if date_to:
            sql += " AND l.print_date_local <= ?"; p.append(date_to)
        if customer:
            sql += " AND (r.customer_full LIKE ? OR r.customer_short LIKE ?)"; p += [f"%{customer}%"] * 2
        if template_key:
            sql += " AND l.template_key = ?"; p.append(template_key)
        if barcode:
            sql += " AND l.barcode LIKE ?"; p.append(f"%{barcode}%")
        if document:
            sql += " AND d.name LIKE ?"; p.append(f"%{document}%")
        if print_status:
            sql += " AND l.print_status = ?"; p.append(print_status)
        if excel_status:
            sql += " AND l.excel_status = ?"; p.append(excel_status)
        sql += " ORDER BY l.id DESC LIMIT ?"
        p.append(limit)
        return self.db.query(sql, tuple(p))

    def add_print_attempt(self, label_id: int, *, kind: str, printer_mode: str, printer_name: str, status: str,
                          error: str | None, job_ref: str | None, operator: str, computer: str) -> int:
        return self.db.execute(
            "INSERT INTO print_attempts (label_id, kind, printer_mode, printer_name, status, error, job_ref, operator, "
            "computer, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (label_id, kind, printer_mode, printer_name, status, error, job_ref, operator, computer, utcnow()))

    def print_attempts(self, label_id: int) -> list[sqlite3.Row]:
        return self.db.query("SELECT * FROM print_attempts WHERE label_id=? ORDER BY id", (label_id,))

    def add_excel_attempt(self, label_id: int, **f: Any) -> int:
        f.setdefault("created_at", utcnow())
        cols = ", ".join(f)
        return self.db.execute(f"INSERT INTO excel_attempts (label_id, {cols}) VALUES (?, {','.join('?' * len(f))})",
                               (label_id, *f.values()))

    def excel_attempts(self, label_id: int) -> list[sqlite3.Row]:
        return self.db.query("SELECT * FROM excel_attempts WHERE label_id=? ORDER BY id", (label_id,))

    # ---- dashboard --------------------------------------------------------------
    def stats(self, today_local: str) -> dict[str, int]:
        one = lambda sql, p=(): self.db.one(sql, p)[0]  # noqa: E731
        return {
            "labels_today": one("SELECT COUNT(*) FROM labels WHERE print_date_local=?", (today_local,)),
            "excel_pending": one("SELECT COUNT(*) FROM labels WHERE excel_status IN ('pending','staged','queued_remote')"),
            "excel_errors": one("SELECT COUNT(*) FROM labels WHERE excel_status IN ('error','conflict')"),
            "print_errors": one("SELECT COUNT(*) FROM labels WHERE print_status IN ('error','print_failed_confirmed')"),
            "print_unconfirmed": one("SELECT COUNT(*) FROM labels WHERE print_status IN ('print_submitted','print_unconfirmed')"),
            "interrupted": one("SELECT COUNT(*) FROM labels WHERE print_status='barcode_reserved'"),
        }
