"""VS barcode registry: the single authority for barcode numbers.

Run:  vspm-registry   (env: VSPM_REGISTRY_DATABASE_URL, VSPM_REGISTRY_API_KEYS,
                       VSPM_REGISTRY_ADMIN_KEY, VSPM_REGISTRY_HOST/PORT)

Allocation is one transaction: an UPDATE ... RETURNING on the sequence row
(row-locked in PostgreSQL) plus the INSERT of the reservation. A unique
constraint on idempotency_key makes retries return the original number.
"""

from __future__ import annotations

import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import (
    BigInteger, Column, DateTime, Index, Integer, MetaData, String, Table, Text, UniqueConstraint, create_engine,
    func, insert, select, text, update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

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


def now() -> datetime:
    return datetime.now(timezone.utc)


def make_engine(url: str) -> Engine:
    kw = {"future": True}
    if url.startswith("sqlite"):
        kw["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(url, **kw)
    metadata.create_all(engine)
    with engine.begin() as c:
        if c.execute(select(schema_info.c.version)).first() is None:
            c.execute(insert(schema_info).values(version=SCHEMA_VERSION))
    return engine


# ---- request models ---------------------------------------------------------------------
class ReserveIn(BaseModel):
    series: str
    idempotency_key: str = Field(min_length=8, max_length=200)
    production_ref: Optional[str] = None
    operator: Optional[str] = None
    computer: Optional[str] = None
    client_kind: str = "app"


class BlockIn(BaseModel):
    series: str
    count: int = Field(ge=1, le=200)
    operator: str = Field(min_length=1)
    computer: Optional[str] = None
    request_id: str = Field(min_length=8, max_length=150)


class ObservedIn(BaseModel):
    items: list[str] = Field(min_length=1, max_length=50000)
    source: str = Field(min_length=1, max_length=100)
    operator: Optional[str] = None
    computer: Optional[str] = None


BARCODE_RE = __import__("re").compile(r"(PG0|PG|VS|EX)(\d{1,9})")


class StatusIn(BaseModel):
    status: str
    operator: Optional[str] = None


class SequenceIn(BaseModel):
    prefix: str
    format_width: int = 3
    next_value: int = Field(ge=0)
    max_value: Optional[int] = None
    allow_backwards: bool = False
    operator: str = "admin"


class ExcelJobIn(BaseModel):
    idempotency_key: str
    barcode: str
    payload: dict


class ClaimIn(BaseModel):
    worker: str
    limit: int = 20


class CompleteIn(BaseModel):
    status: str
    result: dict = {}


def _res_dict(r) -> dict:
    d = dict(r._mapping)
    for k in ("created_at", "updated_at", "claimed_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


def create_app(database_url: Optional[str] = None, api_keys: Optional[list[str]] = None,
               admin_key: Optional[str] = None) -> FastAPI:
    database_url = database_url or os.environ.get("VSPM_REGISTRY_DATABASE_URL", "sqlite:///registry.sqlite3")
    keys = api_keys if api_keys is not None else [
        k.strip() for k in os.environ.get("VSPM_REGISTRY_API_KEYS", "").split(",") if k.strip()]
    admin = admin_key if admin_key is not None else os.environ.get("VSPM_REGISTRY_ADMIN_KEY", "")
    if not keys:
        raise RuntimeError("VSPM_REGISTRY_API_KEYS must contain at least one key")
    engine = make_engine(database_url)
    app = FastAPI(title="VS Barcode Registry", version="1.0")
    app.state.engine = engine

    def client_key(x_api_key: str = Header(default="")) -> str:
        if not any(hmac.compare_digest(x_api_key, k) for k in keys) and not (
                admin and hmac.compare_digest(x_api_key, admin)):
            raise HTTPException(401, "invalid API key")
        return x_api_key

    def admin_only(x_api_key: str = Header(default="")) -> str:
        if not admin or not hmac.compare_digest(x_api_key, admin):
            raise HTTPException(403, "admin key required")
        return x_api_key

    def log(c, action: str, details: dict, actor: Optional[str] = None, computer: Optional[str] = None):
        c.execute(insert(audit).values(ts=now(), actor=actor, computer=computer, action=action,
                                       details=json.dumps(details, ensure_ascii=False, default=str)))

    def allocate(c, series: str, count: int = 1) -> tuple[int, dict]:
        seq = c.execute(select(sequences).where(sequences.c.series == series)).first()
        if seq is None:
            raise HTTPException(409, f"unknown series {series}: initialise it first")
        res = c.execute(text(
            "UPDATE barcode_sequences SET next_value = next_value + :n, updated_at = :ts "
            "WHERE series = :s AND (max_value IS NULL OR next_value + :n - 1 <= max_value) "
            "RETURNING next_value - :n AS first_number, prefix, format_width"),
            {"n": count, "s": series, "ts": now()}).first()
        if res is None:
            raise HTTPException(409, f"series {series} has no numbers left in its allowed range")
        return int(res.first_number), {"prefix": res.prefix, "width": int(res.format_width)}

    def fmt(prefix: str, n: int, width: int) -> str:
        return f"{prefix}{str(n).zfill(width) if width else n}"

    @app.get("/health")
    def health():
        with engine.connect() as c:
            c.execute(select(func.count()).select_from(sequences))
        return {"status": "ok", "time": now().isoformat()}

    @app.get("/v1/sequences", dependencies=[Depends(client_key)])
    def list_sequences():
        with engine.connect() as c:
            return [_res_dict(r) for r in c.execute(select(sequences).order_by(sequences.c.series))]

    @app.put("/v1/sequences/{series}", dependencies=[Depends(admin_only)])
    def set_sequence(series: str, body: SequenceIn):
        with engine.begin() as c:
            cur = c.execute(select(sequences).where(sequences.c.series == series)).first()
            highest = c.execute(select(func.max(reservations.c.number)).where(
                reservations.c.series == series)).scalar()
            if cur is not None and body.next_value < cur.next_value and not body.allow_backwards:
                raise HTTPException(409, f"refusing to move {series} backwards from {cur.next_value}")
            if highest is not None and body.next_value <= highest:
                raise HTTPException(409, f"{series} already has reservations up to {highest}")
            vals = dict(prefix=body.prefix, format_width=body.format_width, next_value=body.next_value,
                        max_value=body.max_value, updated_at=now())
            if cur is None:
                c.execute(insert(sequences).values(series=series, **vals))
            else:
                c.execute(update(sequences).where(sequences.c.series == series).values(**vals))
            log(c, "sequence_set", {"series": series, **vals, "previous": cur.next_value if cur else None},
                actor=body.operator)
        return {"series": series, **vals}

    @app.post("/v1/reservations", dependencies=[Depends(client_key)])
    def reserve(body: ReserveIn):
        for _ in range(3):
            try:
                with engine.begin() as c:
                    existing = c.execute(select(reservations).where(
                        reservations.c.idempotency_key == body.idempotency_key)).first()
                    if existing is not None:
                        if existing.series != body.series:
                            raise HTTPException(409, "idempotency key already used for another series")
                        return _res_dict(existing)
                    number, f = allocate(c, body.series)
                    ts = now()
                    rid = c.execute(insert(reservations).values(
                        series=body.series, number=number, barcode=fmt(f["prefix"], number, f["width"]),
                        idempotency_key=body.idempotency_key, status="reserved",
                        production_ref=body.production_ref, operator=body.operator, computer=body.computer,
                        client_kind=body.client_kind, created_at=ts, updated_at=ts)).inserted_primary_key[0]
                    log(c, "reserve", {"series": body.series, "number": number, "key": body.idempotency_key,
                                       "ref": body.production_ref}, body.operator, body.computer)
                    return _res_dict(c.execute(select(reservations).where(reservations.c.id == rid)).first())
            except IntegrityError:
                continue  # concurrent request with same key: loop returns the stored one
        raise HTTPException(500, "could not reserve after retries")

    @app.post("/v1/blocks", dependencies=[Depends(client_key)])
    def reserve_block(body: BlockIn):
        """Contiguous numbers for employees who still print manually in DYMO.

        Retrying with the same request_id returns the same block (exact match on
        the block id - no pattern matching on user input)."""
        block_ref = f"block:{body.request_id}"

        def existing_block(c):
            rows = c.execute(select(reservations).where(reservations.c.production_ref == block_ref)
                             .order_by(reservations.c.number)).all()
            if not rows:
                return None
            if rows[0].series != body.series or len(rows) != body.count:
                raise HTTPException(409, "request_id already used for a different block")
            return {"series": body.series, "barcodes": [r.barcode for r in rows], "first": rows[0].number,
                    "last": rows[-1].number}

        for _ in range(3):
            try:
                with engine.begin() as c:
                    found = existing_block(c)
                    if found:
                        return found
                    first, f = allocate(c, body.series, body.count)
                    ts = now()
                    for i in range(body.count):
                        n = first + i
                        c.execute(insert(reservations).values(
                            series=body.series, number=n, barcode=fmt(f["prefix"], n, f["width"]),
                            idempotency_key=f"{block_ref}:{i}", status="reserved", production_ref=block_ref,
                            operator=body.operator, computer=body.computer, client_kind="manual",
                            created_at=ts, updated_at=ts))
                    log(c, "reserve_block", {"series": body.series, "first": first, "count": body.count,
                                             "request_id": body.request_id}, body.operator, body.computer)
                return {"series": body.series, "first": first, "last": first + body.count - 1,
                        "barcodes": [fmt(f["prefix"], first + i, f["width"]) for i in range(body.count)]}
            except IntegrityError:
                continue  # same request_id sent twice at once: the other one won, return its block
        raise HTTPException(500, "could not reserve block after retries")

    @app.post("/v1/observed", dependencies=[Depends(client_key)])
    def observe(body: ObservedIn):
        """Report barcodes printed OUTSIDE the registry (manual DYMO counters, workbook history).

        Unknown numbers are recorded and the sequence is moved past them, so the
        registry never issues them. Numbers the registry already knows are listed
        in `already_registered`: normal for a workbook scan (the app printed
        them), but a DUPLICATE when reporting labels printed manually without a
        block reservation."""
        results = {"recorded": [], "already_registered": [], "advanced": {}, "ignored": []}
        parsed = []
        for item in body.items:
            m = BARCODE_RE.fullmatch(item.strip().upper())
            if m:
                parsed.append((m.group(1), int(m.group(2))))
            else:
                results["ignored"].append(item)
        parsed.sort()  # lock sequences in a fixed order (no deadlocks between concurrent reports)
        with engine.begin() as c:
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
                    status="printed", production_ref=f"observed:{body.source}", operator=body.operator,
                    computer=body.computer, client_kind="observed", created_at=ts, updated_at=ts))
                results["recorded"].append(barcode)
                adv = c.execute(text(
                    "UPDATE barcode_sequences SET next_value = :n + 1, updated_at = :ts "
                    "WHERE series = :s AND next_value <= :n RETURNING next_value"),
                    {"n": num, "s": seq.series, "ts": ts}).first()
                if adv is not None:
                    results["advanced"][seq.series] = int(adv.next_value)
            log(c, "observed", {"source": body.source, "recorded": len(results["recorded"]),
                                "already_registered": len(results["already_registered"]),
                                "advanced": results["advanced"]},
                body.operator, body.computer)
        return results

    @app.get("/v1/reservations", dependencies=[Depends(client_key)])
    def find_reservations(idempotency_key: Optional[str] = None, barcode: Optional[str] = None,
                          series: Optional[str] = None, production_ref: Optional[str] = None,
                          limit: int = Query(100, le=1000)):
        q = select(reservations).order_by(reservations.c.id.desc()).limit(limit)
        if idempotency_key:
            q = q.where(reservations.c.idempotency_key == idempotency_key)
        if barcode:
            q = q.where(reservations.c.barcode == barcode)
        if series:
            q = q.where(reservations.c.series == series)
        if production_ref:
            q = q.where(reservations.c.production_ref == production_ref)
        with engine.connect() as c:
            return [_res_dict(r) for r in c.execute(q)]

    @app.post("/v1/reservations/{rid}/status", dependencies=[Depends(client_key)])
    def set_status(rid: int, body: StatusIn):
        if body.status not in ("reserved", "printed", "void"):
            raise HTTPException(422, "status must be reserved|printed|void")
        with engine.begin() as c:
            r = c.execute(select(reservations).where(reservations.c.id == rid)).first()
            if r is None:
                raise HTTPException(404, "reservation not found")
            c.execute(update(reservations).where(reservations.c.id == rid).values(status=body.status,
                                                                                   updated_at=now()))
            log(c, "status", {"id": rid, "barcode": r.barcode, "from": r.status, "to": body.status}, body.operator)
        return {"id": rid, "status": body.status}

    @app.get("/v1/audit", dependencies=[Depends(admin_only)])
    def get_audit(limit: int = Query(200, le=2000)):
        with engine.connect() as c:
            return [_res_dict(r) for r in c.execute(select(audit).order_by(audit.c.id.desc()).limit(limit))]

    # ---- Excel job queue -------------------------------------------------------------------
    @app.post("/v1/excel-jobs", dependencies=[Depends(client_key)])
    def add_excel_job(body: ExcelJobIn):
        try:
            with engine.begin() as c:
                ex = c.execute(select(excel_jobs).where(excel_jobs.c.idempotency_key == body.idempotency_key)).first()
                if ex is not None:
                    return _res_dict(ex)
                ts = now()
                jid = c.execute(insert(excel_jobs).values(
                    idempotency_key=body.idempotency_key, barcode=body.barcode,
                    payload=json.dumps(body.payload, ensure_ascii=False), status="pending", attempts=0,
                    created_at=ts, updated_at=ts)).inserted_primary_key[0]
                return _res_dict(c.execute(select(excel_jobs).where(excel_jobs.c.id == jid)).first())
        except IntegrityError:
            with engine.connect() as c:
                return _res_dict(c.execute(select(excel_jobs).where(
                    excel_jobs.c.idempotency_key == body.idempotency_key)).first())

    @app.get("/v1/excel-jobs", dependencies=[Depends(client_key)])
    def list_excel_jobs(idempotency_key: Optional[str] = None, status: Optional[str] = None,
                        limit: int = Query(100, le=1000)):
        q = select(excel_jobs).order_by(excel_jobs.c.id).limit(limit)
        if idempotency_key:
            q = q.where(excel_jobs.c.idempotency_key == idempotency_key)
        if status:
            q = q.where(excel_jobs.c.status == status)
        with engine.connect() as c:
            return [_res_dict(r) for r in c.execute(q)]

    def _aware(dt):
        return None if dt is None else (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))

    @app.post("/v1/excel-jobs/claim", dependencies=[Depends(client_key)])
    def claim(body: ClaimIn):
        """Hand pending jobs to ONE worker. Failed jobs are retried with back-off;
        claims older than 10 minutes (crashed worker) are released."""
        current = now()
        with engine.begin() as c:
            rows = c.execute(select(excel_jobs).where(excel_jobs.c.status.in_(["pending", "claimed", "error",
                                                                                "conflict"]))
                             .order_by(excel_jobs.c.id).limit(body.limit * 5)).all()
            picked = []
            for r in rows:
                if r.status == "claimed" and _aware(r.claimed_at) and \
                        (current - _aware(r.claimed_at)).total_seconds() < 600:
                    continue
                if r.status in ("error", "conflict") and _aware(r.next_attempt_at) and _aware(r.next_attempt_at) > current:
                    continue
                res = c.execute(update(excel_jobs).where(excel_jobs.c.id == r.id,
                                                         excel_jobs.c.updated_at == r.updated_at)
                                .values(status="claimed", claimed_by=body.worker, claimed_at=current,
                                        updated_at=current))
                if res.rowcount == 1:
                    picked.append(r.id)
                if len(picked) >= body.limit:
                    break
            return [_res_dict(x) for x in c.execute(select(excel_jobs).where(excel_jobs.c.id.in_(picked))
                                                    .order_by(excel_jobs.c.id))]

    @app.post("/v1/excel-jobs/{jid}/complete", dependencies=[Depends(client_key)])
    def complete(jid: int, body: CompleteIn):
        if body.status not in ("done", "already_present", "error", "conflict"):
            raise HTTPException(422, "bad status")
        with engine.begin() as c:
            job = c.execute(select(excel_jobs).where(excel_jobs.c.id == jid)).first()
            if job is None:
                raise HTTPException(404, "job not found")
            attempts = (job.attempts or 0) + 1
            delay = min(300 * 2 ** (attempts - 1), 3600)  # 5 min, 10, 20, ... max 1 h
            c.execute(update(excel_jobs).where(excel_jobs.c.id == jid).values(
                status=body.status, result=json.dumps(body.result, ensure_ascii=False), attempts=attempts,
                next_attempt_at=(now() + timedelta(seconds=delay)) if body.status in ("error", "conflict") else None,
                updated_at=now()))
        return {"id": jid, "status": body.status, "attempts": attempts}

    # ---- manual DYMO users ---------------------------------------------------------------
    @app.get("/manual", response_class=HTMLResponse)
    def manual_page():
        return (Path(__file__).parent / "static" / "manual.html").read_text(encoding="utf-8")

    return app


def main() -> None:  # pragma: no cover - entry point
    import uvicorn
    uvicorn.run(create_app(), host=os.environ.get("VSPM_REGISTRY_HOST", "0.0.0.0"),
                port=int(os.environ.get("VSPM_REGISTRY_PORT", "8765")))


if __name__ == "__main__":  # pragma: no cover
    main()
