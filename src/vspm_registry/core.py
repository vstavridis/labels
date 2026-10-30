"""Barcode registry core: the allocation rules, independent of HTTP.

Used by the FastAPI service (vspm-registry) AND directly by the app in
VSPM_BARCODE_MODE=database (Streamlit Cloud: the app and the registry share one
PostgreSQL, no separate service to host). Both paths run exactly this code
against the same tables, so they can be mixed safely.

Allocation is one transaction: an UPDATE ... RETURNING on the sequence row
(row-locked in PostgreSQL) plus the INSERT of the reservation. A unique
constraint on idempotency_key makes retries return the original number.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    BigInteger, Column, DateTime, Index, Integer, MetaData, String, Table, Text, UniqueConstraint, create_engine,
    func, insert, select, text, update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError


class RegistryError(Exception):
    status = 500


class RegistryConflict(RegistryError):
    """Refused by the allocation rules (unknown series, range exhausted, key reused)."""
    status = 409


class RegistryNotFound(RegistryError):
    status = 404


class RegistryInvalid(RegistryError):
    status = 422


SCHEMA_VERSION = 1
metadata = MetaData()

sequences = Table(
    "barcode_sequences", metadata,
    Column("series", String(8), primary_key=True),
    Column("prefix", String(8), nullable=False),
    Column("format_width", Integer, nullable=False, default=3),
    Column("next_value", BigInteger, nullable=False),
    Column("max_value", BigInteger, nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

reservations = Table(
    "barcode_reservations", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("series", String(8), nullable=False),
    Column("number", BigInteger, nullable=False),
    Column("barcode", String(32), nullable=False, unique=True),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    Column("status", String(20), nullable=False),  # reserved | printed | void
    Column("production_ref", Text),
    Column("operator", String(100)),
    Column("computer", String(100)),
    Column("client_kind", String(20), nullable=False),  # app | manual
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("series", "number", name="uq_series_number"),
    Index("ix_reservations_production_ref", "production_ref"),
)

audit = Table(
    "registry_audit", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("actor", String(100)),
    Column("computer", String(100)),
    Column("action", String(50), nullable=False),
    Column("details", Text, nullable=False),
)

excel_jobs = Table(
    "excel_jobs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    Column("barcode", String(32), nullable=False),
    Column("payload", Text, nullable=False),
    Column("status", String(20), nullable=False),  # pending | claimed | done | already_present | error | conflict
    Column("claimed_by", String(100)),
    Column("claimed_at", DateTime(timezone=True)),
    Column("result", Text),
    Column("attempts", Integer, nullable=False, default=0),
    Column("next_attempt_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

schema_info = Table("registry_schema", metadata, Column("version", Integer, primary_key=True))


BARCODE_RE = re.compile(r"(PG0|PG|VS|EX)(\d{1,9})")


def now() -> datetime:
    return datetime.now(timezone.utc)


def make_engine(url: str) -> Engine:
    url = url.replace("postgresql://", "postgresql+psycopg://", 1) if url.startswith("postgresql://") else url
    kw = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        kw = {"future": True, "connect_args": {"check_same_thread": False, "timeout": 30}}
    engine = create_engine(url, **kw)
    metadata.create_all(engine)
    with engine.begin() as c:
        if c.execute(select(schema_info.c.version)).first() is None:
            c.execute(insert(schema_info).values(version=SCHEMA_VERSION))
    return engine


def res_dict(r) -> dict:
    d = dict(r._mapping)
    for k in ("created_at", "updated_at", "claimed_at", "next_attempt_at", "ts"):
        if d.get(k) is not None and hasattr(d[k], "isoformat"):
            d[k] = d[k].isoformat()
    return d


def fmt(prefix: str, n: int, width: int) -> str:
    return f"{prefix}{str(n).zfill(width) if width else n}"


class Registry:
    def __init__(self, engine: Engine):
        self.engine = engine

    # ---- internals -----------------------------------------------------------------------
    @staticmethod
    def _log(c, action: str, details: dict, actor: Optional[str] = None, computer: Optional[str] = None):
        c.execute(insert(audit).values(ts=now(), actor=actor, computer=computer, action=action,
                                       details=json.dumps(details, ensure_ascii=False, default=str)))

    @staticmethod
    def _allocate(c, series: str, count: int = 1) -> tuple[int, dict]:
        seq = c.execute(select(sequences).where(sequences.c.series == series)).first()
        if seq is None:
            raise RegistryConflict(f"unknown series {series}: initialise it first")
        res = c.execute(text(
            "UPDATE barcode_sequences SET next_value = next_value + :n, updated_at = :ts "
            "WHERE series = :s AND (max_value IS NULL OR next_value + :n - 1 <= max_value) "
            "RETURNING next_value - :n AS first_number, prefix, format_width"),
            {"n": count, "s": series, "ts": now()}).first()
        if res is None:
            raise RegistryConflict(f"series {series} has no numbers left in its allowed range")
        return int(res.first_number), {"prefix": res.prefix, "width": int(res.format_width)}

    # ---- sequences -------------------------------------------------------------------------
    def health(self) -> dict:
        with self.engine.connect() as c:
            c.execute(select(func.count()).select_from(sequences))
        return {"status": "ok", "time": now().isoformat()}

    def list_sequences(self) -> list[dict]:
        with self.engine.connect() as c:
            return [res_dict(r) for r in c.execute(select(sequences).order_by(sequences.c.series))]

    def set_sequence(self, series: str, prefix: str, next_value: int, *, format_width: int = 3,
                     max_value: Optional[int] = None, allow_backwards: bool = False, operator: str = "admin") -> dict:
        with self.engine.begin() as c:
            cur = c.execute(select(sequences).where(sequences.c.series == series)).first()
            highest = c.execute(select(func.max(reservations.c.number)).where(
                reservations.c.series == series)).scalar()
            if cur is not None and next_value < cur.next_value and not allow_backwards:
                raise RegistryConflict(f"refusing to move {series} backwards from {cur.next_value}")
            if highest is not None and next_value <= highest:
                raise RegistryConflict(f"{series} already has reservations up to {highest}")
            vals = dict(prefix=prefix, format_width=format_width, next_value=next_value, max_value=max_value,
                        updated_at=now())
            if cur is None:
                c.execute(insert(sequences).values(series=series, **vals))
            else:
                c.execute(update(sequences).where(sequences.c.series == series).values(**vals))
            self._log(c, "sequence_set", {"series": series, **vals, "previous": cur.next_value if cur else None},
                      actor=operator)
        return {"series": series, **vals}

    # ---- reservations ----------------------------------------------------------------------
    def reserve(self, series: str, idempotency_key: str, *, production_ref: Optional[str] = None,
                operator: Optional[str] = None, computer: Optional[str] = None, client_kind: str = "app") -> dict:
        if not 8 <= len(idempotency_key) <= 200:
            raise RegistryInvalid("idempotency_key must be 8-200 characters")
        for _ in range(3):
            try:
                with self.engine.begin() as c:
                    existing = c.execute(select(reservations).where(
                        reservations.c.idempotency_key == idempotency_key)).first()
                    if existing is not None:
                        if existing.series != series:
                            raise RegistryConflict("idempotency key already used for another series")
                        return res_dict(existing)
                    number, f = self._allocate(c, series)
                    ts = now()
                    rid = c.execute(insert(reservations).values(
                        series=series, number=number, barcode=fmt(f["prefix"], number, f["width"]),
                        idempotency_key=idempotency_key, status="reserved", production_ref=production_ref,
                        operator=operator, computer=computer, client_kind=client_kind, created_at=ts,
                        updated_at=ts)).inserted_primary_key[0]
                    self._log(c, "reserve", {"series": series, "number": number, "key": idempotency_key,
                                             "ref": production_ref}, operator, computer)
                    return res_dict(c.execute(select(reservations).where(reservations.c.id == rid)).first())
            except IntegrityError:
                continue  # concurrent request with same key: the loop returns the stored one
        raise RegistryError("could not reserve after retries")

    def reserve_block(self, series: str, count: int, operator: str, request_id: str,
                      computer: Optional[str] = None) -> dict:
        """Contiguous numbers for employees who still print manually in DYMO.

        Retrying with the same request_id returns the same block (exact match on
        the block id - no pattern matching on user input)."""
        if not 1 <= count <= 200 or not operator or not 8 <= len(request_id) <= 150:
            raise RegistryInvalid("count 1-200, operator and request_id (8-150 characters) are required")
        block_ref = f"block:{request_id}"

        def existing_block(c):
            rows = c.execute(select(reservations).where(reservations.c.production_ref == block_ref)
                             .order_by(reservations.c.number)).all()
            if not rows:
                return None
            if rows[0].series != series or len(rows) != count:
                raise RegistryConflict("request_id already used for a different block")
            return {"series": series, "barcodes": [r.barcode for r in rows], "first": rows[0].number,
                    "last": rows[-1].number}

        for _ in range(3):
            try:
                with self.engine.begin() as c:
                    found = existing_block(c)
                    if found:
                        return found
                    first, f = self._allocate(c, series, count)
                    ts = now()
                    for i in range(count):
                        n = first + i
                        c.execute(insert(reservations).values(
                            series=series, number=n, barcode=fmt(f["prefix"], n, f["width"]),
                            idempotency_key=f"{block_ref}:{i}", status="reserved", production_ref=block_ref,
                            operator=operator, computer=computer, client_kind="manual", created_at=ts,
                            updated_at=ts))
                    self._log(c, "reserve_block", {"series": series, "first": first, "count": count,
                                                   "request_id": request_id}, operator, computer)
                return {"series": series, "first": first, "last": first + count - 1,
                        "barcodes": [fmt(f["prefix"], first + i, f["width"]) for i in range(count)]}
            except IntegrityError:
                continue  # same request_id sent twice at once: the other one won, return its block
        raise RegistryError("could not reserve block after retries")

    def observe(self, items: list[str], source: str, operator: Optional[str] = None,
                computer: Optional[str] = None) -> dict:
        """Report barcodes printed OUTSIDE the registry (manual DYMO counters, workbook history).

        Unknown numbers are recorded and the sequence is moved past them, so the
        registry never issues them. Numbers the registry already knows are listed
        in `already_registered`: normal for a workbook scan (the app printed
        them), but a DUPLICATE when reporting labels printed manually without a
        block reservation."""
        results = {"recorded": [], "already_registered": [], "advanced": {}, "ignored": []}
        parsed = []
        for item in items:
            m = BARCODE_RE.fullmatch(item.strip().upper())
            if m:
                parsed.append((m.group(1), int(m.group(2))))
            else:
                results["ignored"].append(item)
        parsed.sort()  # lock sequences in a fixed order (no deadlocks between concurrent reports)
        with self.engine.begin() as c:
            for prefix, num in parsed:
                # Lock the sequence row exactly like an allocation does, so checking
                # "is this number free?" and recording it cannot interleave with an
                # allocation of the same number by another request.
                seq = c.execute(select(sequences).where(sequences.c.prefix == prefix).with_for_update()).first()
                if seq is None:
                    results["ignored"].append(f"{prefix}{num}")
                    continue
                barcode = fmt(seq.prefix, num, seq.format_width)
                prev = c.execute(select(reservations).where(reservations.c.series == seq.series,
                                                            reservations.c.number == num)).first()
                if prev is not None:
                    results["already_registered"].append({
                        "barcode": barcode, "kind": prev.client_kind, "operator": prev.operator,
                        "computer": prev.computer, "at": prev.created_at.isoformat() if prev.created_at else None})
                    continue
                ts = now()
                c.execute(insert(reservations).values(
                    series=seq.series, number=num, barcode=barcode, idempotency_key=f"observed:{seq.series}:{num}",
                    status="printed", production_ref=f"observed:{source}", operator=operator,
                    computer=computer, client_kind="observed", created_at=ts, updated_at=ts))
                results["recorded"].append(barcode)
                adv = c.execute(text(
                    "UPDATE barcode_sequences SET next_value = :n + 1, updated_at = :ts "
                    "WHERE series = :s AND next_value <= :n RETURNING next_value"),
                    {"n": num, "s": seq.series, "ts": ts}).first()
                if adv is not None:
                    results["advanced"][seq.series] = int(adv.next_value)
            self._log(c, "observed", {"source": source, "recorded": len(results["recorded"]),
                                      "already_registered": len(results["already_registered"]),
                                      "advanced": results["advanced"]}, operator, computer)
        return results

    def find(self, *, idempotency_key: Optional[str] = None, barcode: Optional[str] = None,
             series: Optional[str] = None, production_ref: Optional[str] = None, limit: int = 100) -> list[dict]:
        q = select(reservations).order_by(reservations.c.id.desc()).limit(limit)
        if idempotency_key:
            q = q.where(reservations.c.idempotency_key == idempotency_key)
        if barcode:
            q = q.where(reservations.c.barcode == barcode)
        if series:
            q = q.where(reservations.c.series == series)
        if production_ref:
            q = q.where(reservations.c.production_ref == production_ref)
        with self.engine.connect() as c:
            return [res_dict(r) for r in c.execute(q)]

    def set_status(self, rid: int, status: str, operator: Optional[str] = None) -> dict:
        if status not in ("reserved", "printed", "void"):
            raise RegistryInvalid("status must be reserved|printed|void")
        with self.engine.begin() as c:
            r = c.execute(select(reservations).where(reservations.c.id == rid)).first()
            if r is None:
                raise RegistryNotFound("reservation not found")
            c.execute(update(reservations).where(reservations.c.id == rid).values(status=status, updated_at=now()))
            self._log(c, "status", {"id": rid, "barcode": r.barcode, "from": r.status, "to": status}, operator)
        return {"id": rid, "status": status}

    def audit_log(self, limit: int = 200) -> list[dict]:
        with self.engine.connect() as c:
            return [res_dict(r) for r in c.execute(select(audit).order_by(audit.c.id.desc()).limit(limit))]
