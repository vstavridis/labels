"""Cloud setup: barcodes from the registry tables in the shared database (VSPM_BARCODE_MODE=database),
and two app instances (= two PCs' sessions) working on one PostgreSQL at the same time.

The PostgreSQL tests run when VSPM_TEST_DATABASE_URL is set (CI job "cloud database on PostgreSQL")."""

from __future__ import annotations

import os
import threading

import pytest

from vspm.barcodes.allocator import BarcodeUnavailableError, DatabaseRegistryAllocator
from vspm.domain.models import ProductionRow, RowStatus, TemplateKey
from vspm.services.context import build_context
from vspm.services.production import ProductionError
from tests.conftest import with_settings

ON_PG = bool(os.environ.get("VSPM_TEST_DATABASE_URL"))
pg_only = pytest.mark.skipif(not ON_PG, reason="VSPM_TEST_DATABASE_URL not set")


def db_settings(settings, tmp_path, **kw):
    reg = "" if settings.database_url else f"sqlite:///{tmp_path / 'registry.sqlite3'}"
    return with_settings(settings, barcode_mode="database", registry_database_url=reg, **kw)


def make_row(ctx, i: int = 1, template=TemplateKey.PG) -> int:
    return ctx.repo.add_row(ProductionRow(
        document_id=None, row_index=i, customer_full="ΒΗΤΑΚΗΣ", customer_short="ΒΗΤΑΚΗΣ", material="ΓΑΛΒΑΝΙΖΕ",
        thickness="0,60", dimensions="34ΧΡΟΛΟ", weight=str(400 + i), pieces="4", template_key=template,
        approved=True, status=RowStatus.approved))


def init_series(ctx, series="PG", start=7030):
    ctx.allocator.set_sequence(series, series, start, format_width=3, operator="admin")


def test_database_mode_refuses_until_series_initialised(settings, tmp_path):
    ctx = build_context(db_settings(settings, tmp_path))
    assert isinstance(ctx.allocator, DatabaseRegistryAllocator)
    ok, msg = ctx.allocator.health()
    assert not ok and "no barcode series" in msg
    rid = make_row(ctx)
    with pytest.raises(ProductionError, match="unknown series PG"):
        ctx.production.print_and_save(rid)
    assert ctx.repo.label_for_row(rid) is None


def test_database_mode_allocates_and_never_reissues_manual_numbers(settings, tmp_path):
    ctx = build_context(db_settings(settings, tmp_path))
    init_series(ctx)
    ctx.allocator.observe(["PG7030"], "manual DYMO", "employee", "PC2")  # printed by hand with the DYMO counter
    out = ctx.production.print_and_save(make_row(ctx))
    assert out.barcode == "PG7031"
    assert ctx.allocator.peek("PG") == "PG7032"
    block = ctx.allocator.reserve_block("PG", 2, "employee", "block-request-1")
    assert block["barcodes"] == ["PG7032", "PG7033"]
    assert ctx.production.print_and_save(make_row(ctx, 2)).barcode == "PG7034"


def test_database_mode_requires_a_database(settings, tmp_path):
    ctx = build_context(with_settings(settings, barcode_mode="database", database_url="", registry_database_url=""))
    assert "VSPM_DATABASE_URL" in ctx.problems["barcodes"]
    with pytest.raises(BarcodeUnavailableError):
        ctx.allocator.reserve("PG", "k" * 10, production_ref="x", operator="o", computer="c")


@pg_only
def test_two_instances_on_one_postgres_never_share_a_barcode(settings, tmp_path, clock):
    s = db_settings(settings, tmp_path, excel_mode="staging")
    a, b = build_context(s), build_context(with_settings(s, computer_name="PC2"))
    init_series(a)
    rows = [make_row(a, i) for i in range(1, 21)]
    out: dict[int, str] = {}
    errors: list[Exception] = []

    def worker(ctx, ids):
        for rid in ids:
            try:
                out[rid] = ctx.production.print_and_save(rid).barcode
            except Exception as e:  # noqa: BLE001
                errors.append(e)

    t1 = threading.Thread(target=worker, args=(a, rows[0::2]))
    t2 = threading.Thread(target=worker, args=(b, rows[1::2]))
    t1.start(), t2.start(), t1.join(), t2.join()
    assert errors == []
    assert sorted(out.values()) == [f"PG{n}" for n in range(7030, 7050)]  # unique and gapless


@pg_only
def test_same_row_from_two_instances_gets_one_label(settings, tmp_path):
    s = db_settings(settings, tmp_path, excel_mode="staging")
    a, b = build_context(s), build_context(with_settings(s, computer_name="PC2"))
    init_series(a)
    rid = make_row(a)
    results, errors = [], []
    barrier = threading.Barrier(2)

    def worker(ctx):
        barrier.wait()
        try:
            results.append(ctx.production.print_and_save(rid).barcode)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(c,)) for c in (a, b)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(results) == 1 and len(errors) == 1, (results, errors)
    assert a.db.one("SELECT COUNT(*) FROM labels WHERE production_row_id=?", (rid,))[0] == 1
    assert len(a.allocator.find_by_ref(a.production.production_ref(a.repo.get_row(rid)))) == 1
