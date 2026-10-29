"""Barcode allocation: uniqueness, idempotency, registry service, manual blocks."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from vspm.barcodes.allocator import (
    BarcodeUnavailableError, DisabledAllocator, LocalRangeAllocator, RegistryAllocator,
)
from vspm.db.database import Database
from vspm_registry.app import create_app

ADMIN = {"X-API-Key": "admin-key"}
CLIENT = {"X-API-Key": "client-key"}


@pytest.fixture
def api(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'reg.sqlite3'}", ["client-key"], "admin-key")
    c = TestClient(app)
    for series, prefix, nxt in [("VS", "VS", 11547), ("PG", "PG", 7030), ("PG0", "PG0", 2279), ("EX", "EX", 3417)]:
        assert c.put(f"/v1/sequences/{series}", json={"prefix": prefix, "next_value": nxt}, headers=ADMIN).status_code == 200
    return c


def test_local_allocator_unique_and_idempotent(tmp_path):
    db = Database(tmp_path / "a.db")
    a = LocalRangeAllocator(db, "pilot_range", {"VS": (900000, 900002)}, {"VS": "VS"}, {"VS": 3})
    r1 = a.reserve("VS", "key-1", production_ref="r1", operator="o", computer="pc")
    r1b = a.reserve("VS", "key-1", production_ref="r1", operator="o", computer="pc")
    r2 = a.reserve("VS", "key-2", production_ref="r2", operator="o", computer="pc")
    assert r1.barcode == r1b.barcode == "VS900000" and r2.barcode == "VS900001"
    a.reserve("VS", "key-3", production_ref="", operator="o", computer="pc")
    with pytest.raises(BarcodeUnavailableError, match="exhausted"):
        a.reserve("VS", "key-4", production_ref="", operator="o", computer="pc")
    with pytest.raises(BarcodeUnavailableError):
        a.reserve("PG", "key-5", production_ref="", operator="o", computer="pc")


def test_local_allocator_range_never_moves_backwards(tmp_path):
    db = Database(tmp_path / "a.db")
    a = LocalRangeAllocator(db, "pilot_range", {"VS": (100, 200)}, {"VS": "VS"}, {"VS": 3})
    a.reserve("VS", "k1", production_ref="", operator="o", computer="pc")
    b = LocalRangeAllocator(db, "pilot_range", {"VS": (50, 300)}, {"VS": "VS"}, {"VS": 3})
    assert b.reserve("VS", "k2", production_ref="", operator="o", computer="pc").number == 101


def test_disabled_allocator_refuses():
    with pytest.raises(BarcodeUnavailableError):
        DisabledAllocator().reserve("VS", "k", production_ref="", operator="", computer="")


def test_registry_concurrent_reservations_are_unique(api):
    def go(i):
        r = api.post("/v1/reservations", json={"series": "VS", "idempotency_key": f"row-key-{i % 30:03d}"},
                     headers=CLIENT)
        assert r.status_code == 200, r.text
        return r.json()["barcode"]

    with ThreadPoolExecutor(12) as ex:
        codes = list(ex.map(go, range(90)))
    unique = sorted(set(codes))
    assert len(unique) == 30
    assert unique[0] == "VS11547" and unique[-1] == "VS11576"


def test_registry_series_are_independent_and_pg0_prefix(api):
    r = api.post("/v1/reservations", json={"series": "PG0", "idempotency_key": "abcdefgh-1"}, headers=CLIENT).json()
    assert r["barcode"] == "PG02279"
    r = api.post("/v1/reservations", json={"series": "PG", "idempotency_key": "abcdefgh-2"}, headers=CLIENT).json()
    assert r["barcode"] == "PG7030"


def test_registry_rejects_bad_key_and_unknown_series(api):
    assert api.post("/v1/reservations", json={"series": "VS", "idempotency_key": "abcdefgh"},
                    headers={"X-API-Key": "nope"}).status_code == 401
    assert api.post("/v1/reservations", json={"series": "ZZ", "idempotency_key": "abcdefgh"},
                    headers=CLIENT).status_code == 409


def test_registry_sequence_cannot_go_backwards_or_below_reservations(api):
    api.post("/v1/reservations", json={"series": "EX", "idempotency_key": "abcdefgh-x"}, headers=CLIENT)
    assert api.put("/v1/sequences/EX", json={"prefix": "EX", "next_value": 100}, headers=ADMIN).status_code == 409
    assert api.put("/v1/sequences/EX", json={"prefix": "EX", "next_value": 3417, "allow_backwards": True},
                   headers=ADMIN).status_code == 409
    assert api.put("/v1/sequences/EX", json={"prefix": "EX", "next_value": 5000}, headers=CLIENT).status_code == 403


def test_registry_max_value_enforced(api):
    api.put("/v1/sequences/VS", json={"prefix": "VS", "next_value": 11547, "max_value": 11547}, headers=ADMIN)
    assert api.post("/v1/reservations", json={"series": "VS", "idempotency_key": "abcdefgh-1"},
                    headers=CLIENT).status_code == 200
    assert api.post("/v1/reservations", json={"series": "VS", "idempotency_key": "abcdefgh-2"},
                    headers=CLIENT).status_code == 409


def test_manual_block_for_dymo_users(api):
    body = {"series": "VS", "count": 5, "operator": "Νίκος", "request_id": "req-12345678"}
    r1 = api.post("/v1/blocks", json=body, headers=CLIENT).json()
    r2 = api.post("/v1/blocks", json=body, headers=CLIENT).json()  # retry -> same numbers
    assert r1["first"] == 11547 and r1["last"] == 11551 and r1["barcodes"] == r2["barcodes"]
    nxt = api.post("/v1/reservations", json={"series": "VS", "idempotency_key": "after-block"}, headers=CLIENT).json()
    assert nxt["number"] == 11552
    assert api.get("/manual").status_code == 200


def test_registry_client_and_status_audit(api):
    client = RegistryAllocator("http://testserver", "client-key", client=api)
    client.client.headers.update(CLIENT)
    r = client.reserve("VS", "row-key-abc", production_ref="row:1", operator="op", computer="pc")
    assert client.reserve("VS", "row-key-abc", production_ref="row:1", operator="op", computer="pc").number == r.number
    assert client.lookup("row-key-abc").barcode == r.barcode
    assert client.lookup("missing-key") is None
    client.mark(r, "printed", "op")
    assert api.get("/v1/reservations", params={"barcode": r.barcode}, headers=CLIENT).json()[0]["status"] == "printed"
    actions = [a["action"] for a in api.get("/v1/audit", headers=ADMIN).json()]
    assert "reserve" in actions and "status" in actions
    assert client.peek("VS") == f"VS{r.number + 1}"


def test_registry_unreachable_raises():
    client = RegistryAllocator("http://127.0.0.1:9", "k", timeout=0.5)
    with pytest.raises(BarcodeUnavailableError, match="unreachable"):
        client.reserve("VS", "abcdefghij", production_ref="", operator="", computer="")


def test_excel_job_queue(api):
    j = api.post("/v1/excel-jobs", json={"idempotency_key": "excel:VS1", "barcode": "VS1", "payload": {"a": 1}},
                 headers=CLIENT).json()
    again = api.post("/v1/excel-jobs", json={"idempotency_key": "excel:VS1", "barcode": "VS1", "payload": {"a": 1}},
                     headers=CLIENT).json()
    assert j["id"] == again["id"]
    claimed = api.post("/v1/excel-jobs/claim", json={"worker": "pc1"}, headers=CLIENT).json()
    assert [c["id"] for c in claimed] == [j["id"]]
    assert api.post("/v1/excel-jobs/claim", json={"worker": "pc2"}, headers=CLIENT).json() == []
    api.post(f"/v1/excel-jobs/{j['id']}/complete", json={"status": "done", "result": {"row_number": 9}}, headers=CLIENT)
    assert api.get("/v1/excel-jobs", params={"idempotency_key": "excel:VS1"}, headers=CLIENT).json()[0]["status"] == "done"


# ---- regression tests for the registry audit ---------------------------------------------
def test_block_request_id_is_matched_exactly_not_as_pattern(api):
    a = api.post("/v1/blocks", json={"series": "VS", "count": 2, "operator": "anna", "request_id": "req-abc-001"},
                 headers=CLIENT).json()
    # "%" / "_" used to be SQL wildcards: this request must NOT receive anna's numbers.
    b = api.post("/v1/blocks", json={"series": "VS", "count": 2, "operator": "bob", "request_id": "req-abc-%"},
                 headers=CLIENT).json()
    c = api.post("/v1/blocks", json={"series": "VS", "count": 2, "operator": "carl", "request_id": "req-abc-00_"},
                 headers=CLIENT).json()
    assert len({tuple(a["barcodes"]), tuple(b["barcodes"]), tuple(c["barcodes"])}) == 3


def test_block_retry_with_different_parameters_is_refused(api):
    body = {"series": "VS", "count": 2, "operator": "anna", "request_id": "req-xyz-001"}
    assert api.post("/v1/blocks", json=body, headers=CLIENT).status_code == 200
    assert api.post("/v1/blocks", json={**body, "count": 3}, headers=CLIENT).status_code == 409
    assert api.post("/v1/blocks", json={**body, "series": "PG"}, headers=CLIENT).status_code == 409


def test_observed_numbers_advance_sequence_and_report_known(api):
    issued = api.post("/v1/reservations", json={"series": "VS", "idempotency_key": "app-row-0001"},
                      headers=CLIENT).json()["barcode"]
    r = api.post("/v1/observed", json={"items": ["VS11600", "VS11700", issued, "XX1", "vs11701"],
                                       "source": "workbook"}, headers=CLIENT).json()
    assert r["recorded"] == ["VS11600", "VS11700", "VS11701"]
    assert [x["barcode"] for x in r["already_registered"]] == [issued]
    assert r["ignored"] == ["XX1"]
    nxt = api.post("/v1/reservations", json={"series": "VS", "idempotency_key": "app-row-0002"}, headers=CLIENT).json()
    assert nxt["number"] == 11702
    again = api.post("/v1/observed", json={"items": ["VS11600"], "source": "workbook"}, headers=CLIENT).json()
    assert again["recorded"] == [] and again["already_registered"][0]["kind"] == "observed"


def test_reservations_can_be_found_by_production_ref(api):
    api.post("/v1/reservations", json={"series": "PG", "idempotency_key": "pc1-row-0001",
                                       "production_ref": "google:FILE1#row2"}, headers=CLIENT)
    found = api.get("/v1/reservations", params={"production_ref": "google:FILE1#row2"}, headers=CLIENT).json()
    assert [f["idempotency_key"] for f in found] == ["pc1-row-0001"]


def test_failed_excel_jobs_back_off(api):
    j = api.post("/v1/excel-jobs", json={"idempotency_key": "excel:VS9", "barcode": "VS9", "payload": {}},
                 headers=CLIENT).json()
    assert api.post("/v1/excel-jobs/claim", json={"worker": "pc1"}, headers=CLIENT).json()
    r = api.post(f"/v1/excel-jobs/{j['id']}/complete", json={"status": "conflict", "result": {}}, headers=CLIENT).json()
    assert r["attempts"] == 1
    # not handed out again immediately (it used to be retried on every worker loop)
    assert api.post("/v1/excel-jobs/claim", json={"worker": "pc1"}, headers=CLIENT).json() == []
