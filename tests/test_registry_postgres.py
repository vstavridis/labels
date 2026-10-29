"""Barcode registry against a real PostgreSQL server.

Runs only when VSPM_TEST_POSTGRES_URL is set, e.g.
    VSPM_TEST_POSTGRES_URL=postgresql+psycopg://postgres@127.0.0.1:5432/vspm_test
(CI starts a PostgreSQL service for this). The database is wiped by each test.
"""

from __future__ import annotations

import os
import random
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

URL = os.environ.get("VSPM_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="VSPM_TEST_POSTGRES_URL not set")

ADMIN = {"X-API-Key": "admin-key"}
CLIENT = {"X-API-Key": "client-key"}


@pytest.fixture
def clean_db():
    from sqlalchemy import create_engine
    from vspm_registry.app import metadata
    eng = create_engine(URL)
    metadata.drop_all(eng)
    eng.dispose()
    yield URL


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def two_servers(clean_db):
    """Two independent registry processes on the same database."""
    procs, urls = [], []
    env = {**os.environ, "VSPM_REGISTRY_DATABASE_URL": clean_db, "VSPM_REGISTRY_API_KEYS": "client-key",
           "VSPM_REGISTRY_ADMIN_KEY": "admin-key", "VSPM_REGISTRY_HOST": "127.0.0.1"}
    for i in range(2):
        port = _free_port()
        p = subprocess.Popen([sys.executable, "-c", "from vspm_registry.app import main; main()"],
                             env={**env, "VSPM_REGISTRY_PORT": str(port)},
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(p)
        urls.append(f"http://127.0.0.1:{port}")
        if i == 0:  # first server creates the schema; start the second afterwards
            _wait(urls[0])
    _wait(urls[1])
    with httpx.Client() as c:
        for series, prefix, nxt in [("VS", "VS", 11712), ("PG", "PG", 7030), ("PG0", "PG0", 2279), ("EX", "EX", 3417)]:
            r = c.put(f"{urls[0]}/v1/sequences/{series}", json={"prefix": prefix, "next_value": nxt}, headers=ADMIN)
            assert r.status_code == 200, r.text
    yield urls
    for p in procs:
        p.terminate()
        p.wait(10)


def _wait(url: str) -> None:
    for _ in range(100):
        try:
            if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"registry at {url} did not start")


def _all_reservations(url):
    out = []
    with httpx.Client() as c:
        for s in ("VS", "PG", "PG0", "EX"):
            out += c.get(f"{url}/v1/reservations", params={"series": s, "limit": 1000}, headers=CLIENT).json()
    return out


def test_parallel_allocations_on_two_servers_are_unique_and_gapless(two_servers):
    keys = [f"pc{k % 3}:row-key-{k:04d}" for k in range(200)]
    jobs = [(random.choice(two_servers), random.choice(keys)) for _ in range(800)]  # many retries/duplicates

    def go(job):
        url, key = job
        with httpx.Client(timeout=30) as c:
            r = c.post(f"{url}/v1/reservations", json={"series": "VS", "idempotency_key": key}, headers=CLIENT)
        assert r.status_code == 200, r.text
        return key, r.json()["number"]

    with ThreadPoolExecutor(40) as ex:
        results = list(ex.map(go, jobs))
    by_key: dict[str, set[int]] = {}
    for key, num in results:
        by_key.setdefault(key, set()).add(num)
    assert all(len(v) == 1 for v in by_key.values()), "a key received two different numbers"
    numbers = sorted(next(iter(v)) for v in by_key.values())
    assert len(numbers) == len(set(numbers)), "the same number was given to two keys"
    assert numbers == list(range(11712, 11712 + len(numbers))), "gap or jump in the sequence"


def test_blocks_and_single_reservations_never_overlap(two_servers):
    def single(i):
        url = two_servers[i % 2]
        r = httpx.post(f"{url}/v1/reservations", json={"series": "PG", "idempotency_key": f"single-{i:05d}"},
                       headers=CLIENT, timeout=30)
        assert r.status_code == 200, r.text
        return [r.json()["number"]]

    def block(i):
        url = two_servers[i % 2]
        r = httpx.post(f"{url}/v1/blocks", json={"series": "PG", "count": 5, "operator": f"manual{i}",
                                                 "request_id": f"block-req-{i:05d}"}, headers=CLIENT, timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        return list(range(d["first"], d["last"] + 1))

    tasks = [(single, i) for i in range(120)] + [(block, i) for i in range(30)]
    random.shuffle(tasks)
    with ThreadPoolExecutor(30) as ex:
        chunks = list(ex.map(lambda t: t[0](t[1]), tasks))
    numbers = sorted(n for ch in chunks for n in ch)
    assert len(numbers) == 120 + 30 * 5
    assert numbers == list(range(7030, 7030 + len(numbers)))
    for ch in chunks:  # each block is contiguous
        assert ch == list(range(ch[0], ch[0] + len(ch)))


def test_same_block_request_sent_concurrently_returns_one_block(two_servers):
    body = {"series": "EX", "count": 3, "operator": "manual", "request_id": "same-request-123"}

    def go(i):
        r = httpx.post(f"{two_servers[i % 2]}/v1/blocks", json=body, headers=CLIENT, timeout=30)
        assert r.status_code == 200, r.text
        return tuple(r.json()["barcodes"])

    with ThreadPoolExecutor(10) as ex:
        results = set(ex.map(go, range(20)))
    assert results == {("EX3417", "EX3418", "EX3419")}
    nxt = httpx.post(f"{two_servers[0]}/v1/reservations", json={"series": "EX", "idempotency_key": "after-block"},
                     headers=CLIENT).json()
    assert nxt["number"] == 3420


def test_observed_manual_numbers_are_never_issued(two_servers):
    """Manual DYMO printing reported while the app allocates in parallel."""
    observed = [f"VS{n}" for n in range(11712, 11900, 3)]  # someone printed these manually

    def observe(chunk):
        r = httpx.post(f"{two_servers[1]}/v1/observed", json={"items": chunk, "source": "manual-counter"},
                       headers=CLIENT, timeout=30)
        assert r.status_code == 200, r.text
        return r.json()

    def alloc(i):
        r = httpx.post(f"{two_servers[0]}/v1/reservations", json={"series": "VS", "idempotency_key": f"app-{i:04d}"},
                       headers=CLIENT, timeout=30)
        assert r.status_code == 200, r.text
        return r.json()["barcode"]

    with ThreadPoolExecutor(20) as ex:
        obs_f = [ex.submit(observe, observed[i:i + 10]) for i in range(0, len(observed), 10)]
        app_f = [ex.submit(alloc, i) for i in range(80)]
        issued = [f.result() for f in app_f]
        obs = [f.result() for f in obs_f]
    clashes = {b["barcode"] for o in obs for b in o["already_registered"]}
    # Every manual number is either recorded (and then never issued) or reported as a clash.
    assert len(issued) == len(set(issued))
    assert set(issued) & set(observed) <= clashes
    recorded = {b for o in obs for b in o["recorded"]}
    assert not (recorded & set(issued))
    # After the observation the sequence is past every observed number.
    r = httpx.post(f"{two_servers[0]}/v1/reservations", json={"series": "VS", "idempotency_key": "final-key"},
                   headers=CLIENT)
    assert r.status_code == 200, r.text
    nxt = r.json()["number"]
    assert nxt > max(int(b[2:]) for b in observed)


def test_database_constraints_hold(two_servers):
    for i in range(10):
        httpx.post(f"{two_servers[i % 2]}/v1/reservations", json={"series": "PG0", "idempotency_key": f"k-{i:06d}"},
                   headers=CLIENT)
    res = _all_reservations(two_servers[0])
    assert len({(r["series"], r["number"]) for r in res}) == len(res)
    assert len({r["barcode"] for r in res}) == len(res)
    assert all(r["barcode"].startswith("PG0") for r in res if r["series"] == "PG0")
