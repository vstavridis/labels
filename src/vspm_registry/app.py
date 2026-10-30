"""VS barcode registry service: HTTP API over the registry core (vspm_registry.core).

Run:  vspm-registry   (env: VSPM_REGISTRY_DATABASE_URL, VSPM_REGISTRY_API_KEYS,
                       VSPM_REGISTRY_ADMIN_KEY, VSPM_REGISTRY_HOST/PORT)

The allocation rules live in the core so the Streamlit app can use the same
tables directly (VSPM_BARCODE_MODE=database) without this service.
"""

from __future__ import annotations

import hmac
import json
import os
from datetime import timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from vspm_registry.core import (  # noqa: F401  (re-exported for existing imports)
    BARCODE_RE, SCHEMA_VERSION, Registry, RegistryError, audit, excel_jobs, make_engine, metadata, now,
    res_dict as _res_dict, reservations, schema_info, sequences,
)


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


def create_app(database_url: Optional[str] = None, api_keys: Optional[list[str]] = None,
               admin_key: Optional[str] = None) -> FastAPI:
    database_url = database_url or os.environ.get("VSPM_REGISTRY_DATABASE_URL", "sqlite:///registry.sqlite3")
    keys = api_keys if api_keys is not None else [
        k.strip() for k in os.environ.get("VSPM_REGISTRY_API_KEYS", "").split(",") if k.strip()]
    admin = admin_key if admin_key is not None else os.environ.get("VSPM_REGISTRY_ADMIN_KEY", "")
    if not keys:
        raise RuntimeError("VSPM_REGISTRY_API_KEYS must contain at least one key")
    engine = make_engine(database_url)
    reg = Registry(engine)
    app = FastAPI(title="VS Barcode Registry", version="1.0")
    app.state.engine = engine
    app.state.registry = reg

    def client_key(x_api_key: str = Header(default="")) -> str:
        if not any(hmac.compare_digest(x_api_key, k) for k in keys) and not (
                admin and hmac.compare_digest(x_api_key, admin)):
            raise HTTPException(401, "invalid API key")
        return x_api_key

    def admin_only(x_api_key: str = Header(default="")) -> str:
        if not admin or not hmac.compare_digest(x_api_key, admin):
            raise HTTPException(403, "admin key required")
        return x_api_key

    def call(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except RegistryError as e:
            raise HTTPException(e.status, str(e)) from e

    @app.get("/health")
    def health():
        return reg.health()

    @app.get("/v1/sequences", dependencies=[Depends(client_key)])
    def list_sequences():
        return reg.list_sequences()

    @app.put("/v1/sequences/{series}", dependencies=[Depends(admin_only)])
    def set_sequence(series: str, body: SequenceIn):
        return call(reg.set_sequence, series, body.prefix, body.next_value, format_width=body.format_width,
                    max_value=body.max_value, allow_backwards=body.allow_backwards, operator=body.operator)

    @app.post("/v1/reservations", dependencies=[Depends(client_key)])
    def reserve(body: ReserveIn):
        return call(reg.reserve, body.series, body.idempotency_key, production_ref=body.production_ref,
                    operator=body.operator, computer=body.computer, client_kind=body.client_kind)

    @app.post("/v1/blocks", dependencies=[Depends(client_key)])
    def reserve_block(body: BlockIn):
        """Contiguous numbers for employees who still print manually in DYMO."""
        return call(reg.reserve_block, body.series, body.count, body.operator, body.request_id, body.computer)

    @app.post("/v1/observed", dependencies=[Depends(client_key)])
    def observe(body: ObservedIn):
        """Report barcodes printed OUTSIDE the registry (manual DYMO counters, workbook history)."""
        return call(reg.observe, body.items, body.source, body.operator, body.computer)

    @app.get("/v1/reservations", dependencies=[Depends(client_key)])
    def find_reservations(idempotency_key: Optional[str] = None, barcode: Optional[str] = None,
                          series: Optional[str] = None, production_ref: Optional[str] = None,
                          limit: int = Query(100, le=1000)):
        return reg.find(idempotency_key=idempotency_key, barcode=barcode, series=series,
                        production_ref=production_ref, limit=limit)

    @app.post("/v1/reservations/{rid}/status", dependencies=[Depends(client_key)])
    def set_status(rid: int, body: StatusIn):
        return call(reg.set_status, rid, body.status, body.operator)

    @app.get("/v1/audit", dependencies=[Depends(admin_only)])
    def get_audit(limit: int = Query(200, le=2000)):
        return reg.audit_log(limit)

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
