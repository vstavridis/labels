"""Barcode number allocation.

Modes
-----
disabled     Printing new labels is impossible (safe default before setup).
mock         Local test numbers from 990000 up. NEVER for real labels.
pilot_range  Local allocation inside number ranges reserved exclusively for this
             PC (agreed with everyone who prints with DYMO manually).
registry     Central transactional registry service (PostgreSQL), shared by all
             computers. Recommended for production.

All modes are idempotent: reserving twice with the same idempotency key returns
the same number. When the registry cannot be reached, an error is raised - the
app never falls back to issuing numbers on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

import httpx

from vspm.db.database import Database, utcnow


class BarcodeError(Exception):
    pass


class BarcodeUnavailableError(BarcodeError):
    """Allocation impossible right now (disabled, offline, exhausted)."""


@dataclass
class Reservation:
    series: str
    number: int
    barcode: str
    reservation_id: str
    idempotency_key: str
    mode: str
    status: str = "reserved"


class BarcodeAllocator(Protocol):
    mode: str

    def reserve(self, series: str, idempotency_key: str, *, production_ref: str, operator: str,
                computer: str) -> Reservation: ...

    def lookup(self, idempotency_key: str) -> Optional[Reservation]: ...

    def mark(self, reservation: Reservation, status: str, operator: str) -> None: ...

    def health(self) -> tuple[bool, str]: ...

    def peek(self, series: str) -> Optional[str]:
        """Indicative next barcode (not reserved; another PC may take it first)."""
        ...

    def find_by_ref(self, production_ref: str) -> list[Reservation]:
        """Reservations made for the same document line (duplicate detection)."""
        ...


def format_number(prefix: str, number: int, width: int) -> str:
    digits = str(number).zfill(width) if width else str(number)
    return f"{prefix}{digits}"


class DisabledAllocator:
    mode = "disabled"

    def reserve(self, series, idempotency_key, *, production_ref, operator, computer):
        raise BarcodeUnavailableError(
            "Barcode allocation is disabled (VSPM_BARCODE_MODE=disabled). Configure the barcode registry or a "
            "pilot range first - see docs/BARCODES.md.")

    def lookup(self, idempotency_key):
        return None

    def mark(self, reservation, status, operator):
        return None

    def health(self):
        return False, "disabled"

    def peek(self, series):
        return None

    def find_by_ref(self, production_ref):
        return []


class LocalRangeAllocator:
    """Atomic allocation in local SQLite (single PC only)."""

    def __init__(self, db: Database, mode: str, ranges: dict[str, tuple[int, int]],
                 prefixes: dict[str, str], widths: dict[str, int]):
        if mode not in ("pilot_range", "mock"):
            raise ValueError(mode)
        self.db = db
        self.mode = mode
        self.ranges = ranges
        self.prefixes = prefixes
        self.widths = widths
        with db.transaction() as conn:
            for series, (start, end) in ranges.items():
                row = conn.execute("SELECT next_value, max_value FROM local_sequences WHERE mode=? AND series=?",
                                   (mode, series)).fetchone()
                if row is None:
                    conn.execute("INSERT INTO local_sequences VALUES (?,?,?,?)", (mode, series, start, end))
                elif row["max_value"] != end or row["next_value"] < start:
                    # Range changed in configuration: never move backwards.
                    conn.execute("UPDATE local_sequences SET next_value=MAX(next_value, ?), max_value=? "
                                 "WHERE mode=? AND series=?", (start, end, mode, series))

    def _to_res(self, r) -> Reservation:
        return Reservation(series=r["series"], number=r["number"],
                           barcode=format_number(self.prefixes[r["series"]], r["number"], self.widths.get(r["series"], 0)),
                           reservation_id=f"local-{r['id']}", idempotency_key=r["idempotency_key"], mode=self.mode,
                           status=r["status"])

    def lookup(self, idempotency_key: str) -> Optional[Reservation]:
        r = self.db.one("SELECT * FROM local_reservations WHERE idempotency_key=? AND mode=?",
                        (idempotency_key, self.mode))
        return self._to_res(r) if r else None

    def reserve(self, series, idempotency_key, *, production_ref, operator, computer) -> Reservation:
        if series not in self.ranges:
            raise BarcodeUnavailableError(f"No {self.mode} range configured for series {series}")
        with self.db.transaction() as conn:
            r = conn.execute("SELECT * FROM local_reservations WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if r is not None:
                if r["series"] != series or r["mode"] != self.mode:
                    raise BarcodeError(f"Idempotency key {idempotency_key} already used for {r['mode']}/{r['series']}")
                return self._to_res(r)
            seq = conn.execute("SELECT next_value, max_value FROM local_sequences WHERE mode=? AND series=?",
                               (self.mode, series)).fetchone()
            if seq["next_value"] > seq["max_value"]:
                raise BarcodeUnavailableError(f"{self.mode} range for {series} is exhausted")
            number = seq["next_value"]
            conn.execute("UPDATE local_sequences SET next_value=? WHERE mode=? AND series=?",
                         (number + 1, self.mode, series))
            rid = conn.execute(
                "INSERT INTO local_reservations (mode, series, number, idempotency_key, production_ref, operator, "
                "computer, status, created_at) VALUES (?,?,?,?,?,?,?, 'reserved', ?)",
                (self.mode, series, number, idempotency_key, production_ref, operator, computer, utcnow())).lastrowid
            r = conn.execute("SELECT * FROM local_reservations WHERE id=?", (rid,)).fetchone()
        return self._to_res(r)

    def mark(self, reservation: Reservation, status: str, operator: str) -> None:
        self.db.execute("UPDATE local_reservations SET status=? WHERE idempotency_key=?",
                        (status, reservation.idempotency_key))

    def find_by_ref(self, production_ref: str) -> list[Reservation]:
        return [self._to_res(r) for r in self.db.query(
            "SELECT * FROM local_reservations WHERE production_ref=? AND mode=?", (production_ref, self.mode))]

    def peek(self, series):
        r = self.db.one("SELECT next_value FROM local_sequences WHERE mode=? AND series=?", (self.mode, series))
        return format_number(self.prefixes.get(series, series), r["next_value"], self.widths.get(series, 0)) if r else None

    def health(self):
        return True, f"{self.mode}: " + ", ".join(f"{s} {a}-{b}" for s, (a, b) in self.ranges.items())


class RegistryAllocator:
    """Client for the central barcode registry (vspm_registry)."""

    mode = "registry"

    def __init__(self, base_url: str, api_key: str, timeout: float = 10.0, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(base_url=self.base_url, timeout=timeout,
                                             headers={"X-API-Key": api_key})

    def _res(self, d: dict) -> Reservation:
        return Reservation(series=d["series"], number=d["number"], barcode=d["barcode"],
                           reservation_id=str(d["id"]), idempotency_key=d["idempotency_key"], mode=self.mode,
                           status=d["status"])

    def _call(self, method: str, url: str, **kw) -> httpx.Response:
        try:
            r = self.client.request(method, url, **kw)
        except httpx.HTTPError as e:
            raise BarcodeUnavailableError(f"Barcode registry unreachable ({self.base_url}): {e}") from e
        if r.status_code in (401, 403):
            raise BarcodeUnavailableError("Barcode registry rejected the API key")
        if r.status_code == 409:
            raise BarcodeUnavailableError(f"Barcode registry: {r.json().get('detail')}")
        if r.status_code >= 400 and r.status_code != 404:
            raise BarcodeError(f"Barcode registry error {r.status_code}: {r.text[:300]}")
        return r

    def reserve(self, series, idempotency_key, *, production_ref, operator, computer) -> Reservation:
        r = self._call("POST", "/v1/reservations", json={
            "series": series, "idempotency_key": idempotency_key, "production_ref": production_ref,
            "operator": operator, "computer": computer, "client_kind": "app"})
        return self._res(r.json())

    def lookup(self, idempotency_key: str) -> Optional[Reservation]:
        r = self._call("GET", "/v1/reservations", params={"idempotency_key": idempotency_key})
        if r.status_code == 404:
            return None
        items = r.json()
        return self._res(items[0]) if items else None

    def mark(self, reservation: Reservation, status: str, operator: str) -> None:
        self._call("POST", f"/v1/reservations/{reservation.reservation_id}/status",
                   json={"status": status, "operator": operator})

    def find_by_ref(self, production_ref: str) -> list[Reservation]:
        r = self._call("GET", "/v1/reservations", params={"production_ref": production_ref})
        return [self._res(d) for d in r.json()] if r.status_code == 200 else []

    def peek(self, series):
        try:
            for seq in self._call("GET", "/v1/sequences").json():
                if seq["series"] == series:
                    return format_number(seq["prefix"], seq["next_value"], seq["format_width"])
        except BarcodeError:
            return None
        return None

    def health(self):
        try:
            r = self.client.get("/health")
            return r.status_code == 200, r.text[:200]
        except httpx.HTTPError as e:
            return False, str(e)

    def observe(self, barcodes: list[str], source: str, operator: str, computer: str) -> dict:
        return self._call("POST", "/v1/observed", json={"items": barcodes, "source": source, "operator": operator,
                                                        "computer": computer}).json()

    # ---- Excel job queue (registry_queue mode) ------------------------------------------
    def enqueue_excel(self, idempotency_key: str, barcode: str, payload: dict) -> dict:
        return self._call("POST", "/v1/excel-jobs", json={"idempotency_key": idempotency_key, "barcode": barcode,
                                                          "payload": payload}).json()

    def excel_job(self, idempotency_key: str) -> Optional[dict]:
        r = self._call("GET", "/v1/excel-jobs", params={"idempotency_key": idempotency_key})
        items = r.json() if r.status_code == 200 else []
        return items[0] if items else None

    def claim_excel(self, worker: str, limit: int = 20) -> list[dict]:
        return self._call("POST", "/v1/excel-jobs/claim", json={"worker": worker, "limit": limit}).json()

    def complete_excel(self, job_id: int, status: str, result: dict) -> None:
        self._call("POST", f"/v1/excel-jobs/{job_id}/complete", json={"status": status, "result": result})
