"""Barcodes written onto the scanned sheet: grid detection on a synthetic form, and the Google Drive
flow (original kept forever, stamps drawn on the original, re-scans never overwritten)."""

from __future__ import annotations

import hashlib
import io

import numpy as np
from PIL import Image, ImageDraw

from vspm.integrations.documents import GoogleDriveSource
from vspm.integrations.stamp import find_grid, stamp

W, H = 1600, 900
COLS = [20, 200, 360, 640, 940, 1160, 1380, 1580]  # ΕΙΔΟΣ … ΔΕΜΑΤΑ | BARCODE
ROW_H, TOP = 62, 110  # header 110-172, data rows from 172


def form(missing_line: int | None = None, rows: int = 9) -> bytes:
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    d.rectangle((20, 20, 900, 80), outline="black", width=3)  # ΕΠΩΝΥΜΙΑ box (not the table)
    lines = [TOP + i * ROW_H for i in range(rows + 2)]
    for i, y in enumerate(lines):
        if i != missing_line:
            d.line((COLS[0], y, COLS[-1], y), fill=(30, 30, 30), width=3)
    for x in COLS:
        d.line((x, TOP, x, lines[-1]), fill=(30, 30, 30), width=3)
    for i, x in enumerate(COLS[:-1]):  # bold header text
        d.rectangle((x + 15, TOP + 18, x + 15 + (COLS[i + 1] - x) // 2, TOP + 42), fill="black")
    d.line((40, TOP + ROW_H + 30, 600, TOP + ROW_H + 25), fill=(30, 60, 200), width=6)  # handwriting, row 1
    y0 = lines[-1] + 40
    d.rectangle((20, y0, 1580, y0 + 120), outline="black", width=3)  # footer box (ΑΡ. ΡΟΛΟΥ …)
    d.line((20, y0 + 60, 1580, y0 + 60), fill="black", width=3)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def red_in(img: Image.Image, box) -> int:
    a = np.asarray(img.crop(box).convert("RGB"), dtype=np.int16)
    return int(((a[:, :, 0] > 150) & (a[:, :, 1] < 90) & (a[:, :, 2] < 90)).sum())


def test_grid_found_rows_and_barcode_column():
    g = find_grid(Image.open(io.BytesIO(form())))
    assert g is not None and len(g.rows) == 9
    assert abs(g.barcode_x[0] - 1380) <= 4 and abs(g.barcode_x[1] - 1580) <= 4
    assert abs(g.rows[0][0] - (TOP + ROW_H)) <= 4  # first data row right under the header


def test_barcodes_written_into_their_cells_only():
    out, mode = stamp(form(), [(1, "VS11354"), (4, "VS11357")])
    img = Image.open(io.BytesIO(out))
    assert mode == "cells" and img.size == (W, H)
    row = lambda n: (TOP + n * ROW_H + 4, TOP + (n + 1) * ROW_H - 4)  # noqa: E731
    for n, has in [(1, True), (2, False), (3, False), (4, True)]:
        y0, y1 = row(n)
        assert (red_in(img, (1384, y0, 1576, y1)) > 50) is has, n
    assert red_in(img, (20, TOP, 1375, H)) == 0  # nothing outside the BARCODE column


def test_missing_faint_line_still_finds_the_rows():
    g = find_grid(Image.open(io.BytesIO(form(missing_line=4))))
    assert g is not None and len(g.rows) == 9


def _tilted(data: bytes, angle: float, crop_right: int = 0) -> bytes:
    img = Image.open(io.BytesIO(data)).rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor="white")
    if crop_right:
        img = img.crop((0, 0, img.width - crop_right, img.height))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def test_tilted_phone_photo_is_straightened_to_find_the_cells():
    for angle in (2.5, -1.7):
        g = find_grid(Image.open(io.BytesIO(_tilted(form(), angle))))
        assert g is not None and len(g.rows) == 9, angle
        assert abs(g.angle + angle) < 0.3  # turned back by the photo's tilt
        assert abs((g.barcode_x[1] - g.barcode_x[0]) - (COLS[-1] - COLS[-2])) <= 12


def test_photo_cut_at_the_right_border_still_uses_the_barcode_column():
    data = _tilted(form(), 0.0, crop_right=W - COLS[-1] - 1)  # the table's right line is cut off
    g = find_grid(Image.open(io.BytesIO(data)))
    assert g is not None and abs(g.barcode_x[0] - COLS[-2]) <= 6  # not the ΔΕΜΑΤΑ column
    out, mode = stamp(data, [(1, "PG02303")])
    img = Image.open(io.BytesIO(out))
    assert mode == "cells" and red_in(img, (COLS[-2] + 4, TOP + ROW_H + 4, img.width - 1, TOP + 2 * ROW_H - 4)) > 50
    assert red_in(img, (0, 0, COLS[-2] - 4, img.height)) == 0


def test_no_table_falls_back_to_a_strip():
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(buf, format="JPEG")
    out, mode = stamp(buf.getvalue(), [(1, "VS1")])
    assert mode == "strip" and Image.open(io.BytesIO(out)).height > 600  # barcodes only, no "#1 -"


# ---- Google Drive flow --------------------------------------------------------------------------
class Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeDrive:
    """files().list/get/get_media/update and revisions().update/get_media of one folder."""

    def __init__(self, files: dict[str, bytes]):
        self.revs: dict[str, list[dict]] = {}
        for fid, data in files.items():
            self.revs[fid] = [{"id": f"{fid}-r1", "data": data, "keep": False}]
        self.updates = 0

    def md5(self, fid):
        return hashlib.md5(self.revs[fid][-1]["data"]).hexdigest()

    def files(self):
        return self

    def revisions(self):
        return Revisions(self)

    def list(self, **kw):
        return Req(lambda: {"files": [{"id": f, "name": f"{f}.jpg", "mimeType": "image/jpeg", "parents": ["F"],
                                        "createdTime": f"2026-11-0{i + 1}T08:00:00Z", "md5Checksum": self.md5(f)}
                                       for i, f in enumerate(self.revs)]})

    def get(self, fileId, **kw):
        return Req(lambda: {"id": fileId, "md5Checksum": self.md5(fileId),
                            "headRevisionId": self.revs[fileId][-1]["id"]})

    def get_media(self, fileId, **kw):
        return Req(lambda: self.revs[fileId][-1]["data"])

    def update(self, fileId, media_body=None, **kw):
        def do():
            data = media_body.getbytes(0, media_body.size())
            self.revs[fileId].append({"id": f"{fileId}-r{len(self.revs[fileId]) + 1}", "data": data, "keep": False})
            self.updates += 1
            return {"md5Checksum": self.md5(fileId), "headRevisionId": self.revs[fileId][-1]["id"]}
        return Req(do)


class Revisions:
    def __init__(self, drive):
        self.d = drive

    def update(self, fileId, revisionId, body):
        def do():
            next(r for r in self.d.revs[fileId] if r["id"] == revisionId)["keep"] = body["keepForever"]
            return {}
        return Req(do)

    def get_media(self, fileId, revisionId):
        return Req(lambda: next(r for r in self.d.revs[fileId] if r["id"] == revisionId)["data"])


def setup(settings):
    from vspm.domain.models import ProductionRow, RowStatus, TemplateKey
    from vspm.services.context import build_context
    ctx = build_context(settings)
    drive = FakeDrive({"sheet1": form()})
    ctx.documents.source = GoogleDriveSource.with_service("F", drive)
    ctx.stamper.enabled = True
    docs = ctx.documents.latest(10, refresh=True)
    doc_id = docs[0].doc_id

    def printed(i, weight):
        rid = ctx.repo.add_row(ProductionRow(
            document_id=doc_id, row_index=i, customer_full="ΒΗΤΑΚΗΣ", material="ΓΑΛΒΑΝΙΖΕ", thickness="0,60",
            dimensions="1000", weight=weight, pieces="1", template_key=TemplateKey.VS, approved=True,
            status=RowStatus.approved))
        ctx.production.photo_stamper = None  # stamp synchronously in the test
        return ctx.production.print_and_save(rid).barcode
    return ctx, drive, doc_id, printed


def test_stamp_keeps_original_forever_and_is_not_seen_as_a_rescan(settings):
    ctx, drive, doc_id, printed = setup(settings)
    original = drive.revs["sheet1"][0]["data"]
    b1 = printed(1, "100")
    assert ctx.stamper.stamp_document(doc_id, "t") == "cells"
    assert drive.revs["sheet1"][0]["keep"] is True and drive.updates == 1
    b2 = printed(3, "300")
    ctx.stamper.stamp_document(doc_id, "t")
    final = Image.open(io.BytesIO(drive.revs["sheet1"][-1]["data"]))
    for n in (1, 3):
        assert red_in(final, (1384, TOP + n * ROW_H + 4, 1576, TOP + (n + 1) * ROW_H - 4)) > 50
    assert red_in(final, (1384, TOP + 2 * ROW_H + 4, 1576, TOP + 3 * ROW_H - 4)) == 0
    assert b1 != b2
    # our own edit: same document, not "changed since processing", and the AI still gets the original
    docs = {d.doc_id: d for d in ctx.documents.latest(10, refresh=True)}
    assert list(docs) == [doc_id] and not docs[doc_id].changed_since_processing
    (ctx.documents.cache_dir / f"doc{doc_id}.jpg").unlink(missing_ok=True)
    assert ctx.documents.content(doc_id)[0] == original
    assert ctx.stamper.pending_documents() == []


def test_rescanned_photo_is_never_overwritten(settings):
    ctx, drive, doc_id, printed = setup(settings)
    printed(1, "100")
    ctx.stamper.stamp_document(doc_id, "t")
    drive.revs["sheet1"].append({"id": "sheet1-new", "data": form(rows=8), "keep": False})  # someone re-scanned
    printed(2, "200")
    import pytest
    from vspm.integrations.documents import DocumentSourceError
    with pytest.raises(DocumentSourceError, match="replaced"):
        ctx.stamper.stamp_document(doc_id, "t")
    assert drive.revs["sheet1"][-1]["id"] == "sheet1-new"
    assert ctx.stamper.record(doc_id)["status"] == "error"
