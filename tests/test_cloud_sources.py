"""OneDrive templates + workbook and Google Drive folder links, against a test double of
Microsoft identity / Graph (vspm.testing_graph). No real Microsoft or Google account is used."""

from __future__ import annotations

import io
import threading
import unicodedata
import zipfile

import pytest

from vspm.domain.models import ProductionRow, RowStatus, TemplateKey
from vspm.excel.xlsx_package import SheetEditor, XlsxPackage
from vspm.integrations.onedrive import (
    AuthorizationPending, NotConnected, OneDriveError, is_onedrive_url, parse_template_names, share_id,
)
from vspm.services.cloud_sources import Lease, parse_drive_folder_id
from vspm.services.context import build_context
from vspm.testing import build_test_workbook
from vspm.testing_graph import FakeMicrosoft
from tests.conftest import FIXTURES, with_settings

FOLDER_LINK = "https://1drv.ms/f/c/0123456789abcdef/IgTESTFOLDERLINK?e=abc123"
BOOK_LINK = "https://1drv.ms/x/c/0123456789abcdef/IQTESTBOOKLINK?e=def456"
COPY_LINK = "https://1drv.ms/x/c/0123456789abcdef/IQTESTCOPYLINK?e=ghi789"
NAMES = {"VS": "ΕΤΙΚΕΤΑ ΔΟΚΙΜΗ VS.label", "PG": "ΕΤΙΚΕΤΑ ΔΟΚΙΜΗ PG-1.label",
         "PG0": "ΕΤΙΚΕΤΑ ΔΟΚΙΜΗ PG-2.label", "EX": "ΕΤΙΚΕΤΑ ΔΟΚΙΜΗ ΕΞΑΓΩΓΕΣ.label"}


def fixture_label(key: str) -> bytes:
    return (FIXTURES / "labels" / f"{key}.label").read_bytes()


@pytest.fixture
def ms(tmp_path):
    fake = FakeMicrosoft()
    folder = fake.add_folder("Ετικέτες", link=FOLDER_LINK)
    for key, name in NAMES.items():
        # one name stored decomposed (NFD), as macOS/OneDrive sometimes do: must still match exactly
        stored = unicodedata.normalize("NFD", name) if key == "EX" else name
        fake.add_file(stored, fixture_label(key), parent=folder.id)
    fake.add_file("notes.txt", b"not a template", parent=folder.id)
    book = build_test_workbook(tmp_path / "wb" / "book.xlsx").read_bytes()
    fake.add_file("Σχισμενα.xlsx", book, link=BOOK_LINK)
    fake.add_file("Σχισμενα - ΔΟΚΙΜΗ.xlsx", book, link=COPY_LINK)
    return fake


def cloud_ctx(settings, ms, **kw):
    s = with_settings(settings, ms_client_id="00000000-test-client", secret_key="a-long-test-secret-key-123",
                      onedrive_templates_url=FOLDER_LINK, onedrive_workbook_url=BOOK_LINK,
                      onedrive_workbook_test_url=COPY_LINK,
                      onedrive_template_files=";".join(f"{k}={v}" for k, v in NAMES.items()),
                      template_dir=settings.data_dir / "no_template_files", **kw)
    ctx = build_context(s)
    ctx.cloud.onedrive.http = ms.client()
    ctx.cloud.onedrive._auth = None
    return ctx


def connect(ctx, ms):
    code = ctx.cloud.onedrive.auth.start_device_login()
    ms.approved = True
    return ctx.cloud.onedrive.auth.finish_device_login(code)


def puts_to(ms, name):
    item = next(i for i in ms.items.values() if i.name == name)
    return ms.puts(item.id)


# ---- links ---------------------------------------------------------------------------------------
def test_link_parsing():
    assert is_onedrive_url(FOLDER_LINK) and is_onedrive_url("https://onedrive.live.com/?id=X")
    assert not is_onedrive_url("https://evil.example/1drv.ms") and not is_onedrive_url("1drv.ms/x")
    sid = share_id(FOLDER_LINK)
    assert sid.startswith("u!") and "=" not in sid and "/" not in sid and "+" not in sid
    import base64
    assert base64.urlsafe_b64decode(sid[2:] + "=" * (-len(sid[2:]) % 4)).decode() == FOLDER_LINK
    with pytest.raises(OneDriveError):
        share_id("https://drive.google.com/drive/folders/abc")
    assert parse_template_names("VS = a b.label; PG=c.label\nEX=d.label") == {"VS": "a b.label", "PG": "c.label",
                                                                              "EX": "d.label"}


def test_google_drive_folder_link_parsing():
    fid = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    assert parse_drive_folder_id(f"https://drive.google.com/drive/folders/{fid}?usp=sharing") == fid
    assert parse_drive_folder_id(f"https://drive.google.com/drive/u/0/folders/{fid}") == fid
    assert parse_drive_folder_id(f"https://drive.google.com/open?id={fid}") == fid
    assert parse_drive_folder_id(fid) == fid and parse_drive_folder_id("") == ""
    with pytest.raises(ValueError):
        parse_drive_folder_id("https://example.com/not-a-folder")


# ---- Microsoft sign-in -----------------------------------------------------------------------------
def test_device_code_sign_in_stores_only_encrypted_tokens(settings, ms):
    ctx = cloud_ctx(settings, ms)
    auth = ctx.cloud.onedrive.auth
    code = auth.start_device_login()
    assert code.user_code == "ABCD-1234" and "devicelogin" in code.verification_uri
    with pytest.raises(AuthorizationPending):
        auth.finish_device_login(code)
    ms.approved = True
    assert auth.finish_device_login(code) == "owner@outlook.com"
    raw = ctx.db.one("SELECT * FROM cloud_credentials")
    assert raw["account"] == "owner@outlook.com"
    assert not any(t in raw["secret_enc"] for t in list(ms.access_tokens) + list(ms.refresh_tokens))
    assert any("/consumers/" in r[1] for r in ms.requests)


def test_expired_access_token_is_refreshed_and_revoked_sign_in_reported(settings, ms):
    ctx = cloud_ctx(settings, ms)
    connect(ctx, ms)
    ms.expire_tokens()
    od = ctx.cloud.onedrive
    od.auth.clock = lambda: 10 ** 12  # far future: stored token expired
    assert od.files.drive_info()["driveType"] == "personal"
    ms.refresh_tokens.clear()  # account disconnected on the Microsoft side
    ms.expire_tokens()
    with pytest.raises(NotConnected):
        od.files.drive_info()


def test_no_secret_key_no_token_storage(settings, ms):
    ctx = cloud_ctx(settings, ms)
    ctx.cloud.onedrive.s = with_settings(ctx.settings, secret_key="")
    ctx.cloud.onedrive.store.box_factory = lambda: __import__(
        "vspm.integrations.onedrive", fromlist=["SecretBox"]).SecretBox("")
    with pytest.raises(OneDriveError, match="VSPM_SECRET_KEY"):
        connect(ctx, ms)


def test_links_need_the_owners_sign_in(settings, ms):
    ctx = cloud_ctx(settings, ms)
    with pytest.raises(NotConnected):
        ctx.cloud.templates.refresh("admin")


# ---- templates --------------------------------------------------------------------------------------
def test_refresh_loads_the_four_templates_by_exact_name_and_never_writes_them(settings, ms):
    ctx = cloud_ctx(settings, ms)
    connect(ctx, ms)
    out = ctx.cloud.templates.refresh("admin")
    assert [(s.key, s.status) for s in out] == [("VS", "updated"), ("PG", "updated"), ("PG0", "updated"),
                                                ("EX", "updated")]
    for key in TemplateKey:
        tpl = ctx.templates.load(key)
        assert tpl.sha256 == __import__("hashlib").sha256(fixture_label(key.value)).hexdigest()  # byte-exact
        assert "OneDrive" in ctx.templates.source(key) or "uploaded" in ctx.templates.source(key)
    assert ms.puts() == []  # templates are only ever read
    downloads = len([r for r in ms.requests if "download.fake" in r[1]])
    again = ctx.cloud.templates.refresh("admin")
    assert {s.status for s in again} == {"unchanged"}
    assert len([r for r in ms.requests if "download.fake" in r[1]]) == downloads  # eTag unchanged: no download
    assert not any("authorization" in r[2] for r in ms.requests if "download.fake" in r[1])


def test_changed_template_becomes_a_new_version_and_the_old_one_is_kept(settings, ms):
    ctx = cloud_ctx(settings, ms)
    connect(ctx, ms)
    ctx.cloud.templates.refresh("admin")
    old_sha = ctx.templates.load(TemplateKey.PG).sha256
    pg = next(i for i in ms.items.values() if i.name == NAMES["PG"])
    new = fixture_label("PG").replace(b"<Name>", b"<Name>", 1).replace(b"</DieCutLabel>", b"<!-- v2 --></DieCutLabel>")
    ms.edit(pg, new)
    out = {s.key: s.status for s in ctx.cloud.templates.refresh("admin")}
    assert out["PG"] == "updated" and out["VS"] == "unchanged"
    assert ctx.templates.load(TemplateKey.PG).sha256 != old_sha
    assert ctx.templates.load_version(TemplateKey.PG, old_sha) is not None  # reprints keep the original layout


def test_broken_template_in_onedrive_keeps_the_previous_good_version(settings, ms):
    ctx = cloud_ctx(settings, ms)
    connect(ctx, ms)
    ctx.cloud.templates.refresh("admin")
    good = ctx.templates.load(TemplateKey.VS).sha256
    vs = next(i for i in ms.items.values() if i.name == NAMES["VS"])
    ms.edit(vs, fixture_label("PG"))  # someone saved a PG layout under the VS name
    out = {s.key: s for s in ctx.cloud.templates.refresh("admin")}
    assert out["VS"].status == "error" and "prefix" in out["VS"].error
    assert ctx.templates.load(TemplateKey.VS).sha256 == good
    assert ctx.cloud.onedrive.file_row("template:VS")["last_error"]


def test_missing_name_is_reported_and_detection_by_counter_works(settings, ms):
    ctx = cloud_ctx(settings, ms)
    connect(ctx, ms)
    ctx.cloud.config.set("onedrive_template_files", "VS=does not exist.label", "admin")
    out = {s.key: s for s in ctx.cloud.templates.refresh("admin")}
    assert out["VS"].status == "error" and "not found" in out["VS"].error
    assert {out[k].status for k in ("PG", "PG0", "EX")} == {"updated"}  # found by their COUNTER prefix


def test_auto_refresh_is_throttled(settings, ms):
    ctx = cloud_ctx(settings, ms)
    connect(ctx, ms)
    assert ctx.cloud.templates.maybe_refresh(600) is not None
    assert ctx.cloud.templates.maybe_refresh(600) is None


def test_folder_by_path_when_a_link_cannot_be_resolved(settings, ms):
    ctx = cloud_ctx(settings, ms)
    connect(ctx, ms)
    ctx.cloud.config.set("onedrive_templates_url", "Ετικέτες", "admin")
    assert {s.status for s in ctx.cloud.templates.refresh("admin")} == {"updated"}


# ---- workbook -----------------------------------------------------------------------------------------
def make_printed_label(ctx, weight="496"):
    rid = ctx.repo.add_row(ProductionRow(
        document_id=None, row_index=1, customer_full="ΒΗΤΑΚΗΣ", customer_short="ΒΗΤΑΚΗΣ", material="ΓΑΛΒΑΝΙΖΕ",
        thickness="0,60", dimensions="34ΧΡΟΛΟ", weight=weight, pieces="4", template_key=TemplateKey.PG,
        approved=True, status=RowStatus.approved))
    out = ctx.production.print_and_save(rid)
    return out, ctx.repo.get_label(out.label_id)


def book_rows(ms, name="Σχισμενα.xlsx", barcode=None):
    item = next(i for i in ms.items.values() if i.name == name)
    ed = SheetEditor(XlsxPackage(item.content), "΄Ετοιμα προϊόντα")
    if barcode:
        return ed.find_rows_with_value("H", barcode)
    return ed


def ready_ctx(settings, ms):
    ctx = cloud_ctx(settings, ms, excel_mode="onedrive")
    connect(ctx, ms)
    ctx.cloud.templates.refresh("admin")
    return ctx


def test_inspect_reads_the_workbook_without_writing(settings, ms):
    ctx = ready_ctx(settings, ms)
    info = ctx.cloud.workbook.inspect("workbook")
    assert info["name"] == "Σχισμενα.xlsx" and info["last_row"] >= 4 and info["formulas_L_O_in_last_row"]
    assert ms.puts() == []


def test_rows_stay_staged_until_the_copy_test_passed_and_production_is_enabled(settings, ms):
    ctx = ready_ctx(settings, ms)
    out, lab = make_printed_label(ctx)
    assert lab["excel_status"] == "staged" and ms.puts() == []  # nothing written to OneDrive
    with pytest.raises(OneDriveError, match="COPY first"):
        ctx.cloud.workbook.set_production(True, "admin")


def test_copy_test_then_rows_written_once_with_formulas_and_other_parts_untouched(settings, ms):
    ctx = ready_ctx(settings, ms)
    before = next(i for i in ms.items.values() if i.name == "Σχισμενα.xlsx").content
    v = ctx.cloud.workbook.validate_copy("admin")
    assert v["ok"], v["checks"]
    assert puts_to(ms, "Σχισμενα.xlsx") == []  # the test only touched the copy
    ctx.cloud.workbook.set_production(True, "admin")
    out, lab = make_printed_label(ctx)
    lab = ctx.repo.get_label(lab["id"])
    assert lab["excel_status"] == "synchronized"
    rows = book_rows(ms, barcode=out.barcode)
    assert rows == [lab["excel_row_number"]]
    ed = book_rows(ms)
    r = rows[0]
    assert ed.value(r, "G").value == "PG" and all(ed.value(r, c).kind == "formula" for c in "LMNO")
    after = next(i for i in ms.items.values() if i.name == "Σχισμενα.xlsx").content
    za, zb = zipfile.ZipFile(io.BytesIO(before)), zipfile.ZipFile(io.BytesIO(after))
    changed = {n for n in za.namelist() if za.read(n) != zb.read(n)}
    assert changed <= {"xl/workbook.xml", "xl/styles.xml", XlsxPackage(before).sheet_part("΄Ετοιμα προϊόντα")}
    # second sync and reprint add nothing
    ctx.cloud.workbook.sync_pending("admin")
    ctx.production.reprint(lab["id"])
    assert book_rows(ms, barcode=out.barcode) == rows
    assert ctx.cloud.workbook.backups()  # copy of the workbook before the change


def test_copy_test_refuses_the_production_file_and_unsafe_servers(settings, ms):
    ctx = ready_ctx(settings, ms)
    ctx.cloud.config.set("onedrive_workbook_test_url", BOOK_LINK, "admin")
    v = ctx.cloud.workbook.validate_copy("admin")
    assert not v["ok"] and "PRODUCTION" in v["checks"][-1][2]
    assert puts_to(ms, "Σχισμενα.xlsx") == []
    ctx.cloud.config.set("onedrive_workbook_test_url", COPY_LINK, "admin")
    ms.ignore_if_match = True  # a server that would overwrite blindly
    v = ctx.cloud.workbook.validate_copy("admin")
    assert not v["ok"] and any(c[0] == "stale eTag refused" and not c[1] for c in v["checks"])
    with pytest.raises(OneDriveError):
        ctx.cloud.workbook.set_production(True, "admin")


def enabled_ctx(settings, ms):
    ctx = ready_ctx(settings, ms)
    assert ctx.cloud.workbook.validate_copy("admin")["ok"]
    ctx.cloud.workbook.set_production(True, "admin")
    return ctx


def test_someone_saves_the_workbook_meanwhile_their_change_is_kept(settings, ms):
    ctx = enabled_ctx(settings, ms)

    def colleague_saves(fake, item):  # between our download and our upload
        fake.edit(item, item.content)  # a new version (new eTag), as when someone saves in Excel

    ms.before_put = colleague_saves
    out, lab = make_printed_label(ctx)
    lab = ctx.repo.get_label(lab["id"])
    assert lab["excel_status"] == "synchronized" and book_rows(ms, barcode=out.barcode)
    attempts = [r for r in ms.puts(next(i for i in ms.items.values() if i.name == "Σχισμενα.xlsx").id)]
    assert len(attempts) == 2  # first refused with 412, written after reading the new version


def test_workbook_that_keeps_changing_is_never_overwritten(settings, ms):
    ctx = enabled_ctx(settings, ms)
    book = next(i for i in ms.items.values() if i.name == "Σχισμενα.xlsx")
    original_puts = ms.puts(book.id)

    def always_changing(fake, item):
        fake.edit(item, item.content)
        fake.before_put = always_changing

    ms.before_put = always_changing
    out, lab = make_printed_label(ctx)
    lab = ctx.repo.get_label(lab["id"])
    assert lab["excel_status"] == "conflict"
    assert book_rows(ms, barcode=out.barcode) == []
    assert len(ms.puts(book.id)) - len(original_puts) == 3
    ms.before_put = None
    assert ctx.cloud.workbook.sync_pending("admin").status == "synchronized"  # retry later succeeds
    assert book_rows(ms, barcode=out.barcode) == [ctx.repo.get_label(lab["id"])["excel_row_number"]]


def test_onedrive_error_keeps_the_row_pending(settings, ms):
    ctx = enabled_ctx(settings, ms)
    ms.fail_put_status = 507  # e.g. storage full
    out, lab = make_printed_label(ctx)
    lab = ctx.repo.get_label(lab["id"])
    assert lab["excel_status"] == "error" and lab["first_printed_at"]
    assert ctx.cloud.onedrive.file_row("workbook")["last_error"]
    ms.fail_put_status = None
    assert ctx.cloud.workbook.sync_pending("admin").status == "synchronized"


def test_two_pcs_syncing_at_the_same_time_write_each_row_once(settings, ms):
    a = enabled_ctx(settings, ms)
    b = cloud_ctx(settings, ms, excel_mode="onedrive", computer_name="PC2")
    labels = [make_printed_label(a, weight=str(400 + i))[0].barcode for i in range(3)]
    for ctx in (a, b):  # make both PCs try to sync everything that is pending, at once
        ctx.db.execute("UPDATE labels SET excel_status='pending'")
    results, barrier = [], threading.Barrier(2)

    def go(ctx):
        barrier.wait()
        results.append(ctx.cloud.workbook.sync_pending("x").status)

    threads = [threading.Thread(target=go, args=(c,)) for c in (a, b)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    for bc in labels:
        assert len(book_rows(ms, barcode=bc)) == 1
    assert set(results) <= {"synchronized", "busy", "nothing"}


def test_lease_has_one_holder_and_expires(settings):
    ctx = build_context(settings)
    clock = [1000.0]
    a = Lease(ctx.db, "x", seconds=60, clock=lambda: clock[0])
    b = Lease(ctx.db, "x", seconds=60, clock=lambda: clock[0])
    assert a.acquire() and not b.acquire()
    clock[0] += 61  # a crashed: its lease expires
    assert b.acquire()
    a.release()  # a no longer owns it: b keeps it
    assert not Lease(ctx.db, "x", clock=lambda: clock[0]).acquire()


# ---- Google Drive folder from Settings -------------------------------------------------------------
def test_google_drive_folder_from_settings_is_used(settings):
    ctx = build_context(settings)
    fid = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    ctx.cloud.config.set("gdrive_folder_url", f"https://drive.google.com/drive/folders/{fid}?usp=sharing", "admin")
    assert ctx.cloud.gdrive_folder_id() == fid
    import vspm.services.context as cmod
    seen = {}

    class Spy:
        name = "google"

        def __init__(self, folder_id, *a, **k):
            seen["folder"] = folder_id

        def list_latest(self, n=5):
            return []

    orig = cmod.GoogleDriveSource
    cmod.GoogleDriveSource = Spy
    try:
        build_context(with_settings(settings, drive_mode="google", drive_folder_id="OLD_ENV_FOLDER_ID"))
    finally:
        cmod.GoogleDriveSource = orig
    assert seen["folder"] == fid
