"""Scan images: phone photos upright (EXIF), PDF rendering safe from several sessions at once."""

from __future__ import annotations

import io
import threading

from PIL import Image

from vspm.integrations.documents import page_images


def test_phone_photo_rotation_from_exif_is_applied():
    img = Image.new("RGB", (400, 200), "white")  # stored landscape ...
    exif = Image.Exif()
    exif[0x0112] = 6  # ... but the camera says "rotate 90° clockwise" (portrait sheet)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    (page,) = page_images(buf.getvalue(), "image/jpeg")
    assert page.size == (200, 400)


def test_pdf_pages_rendered_from_many_threads():
    buf = io.BytesIO()
    Image.new("RGB", (300, 400), "white").save(buf, format="PDF")
    data = buf.getvalue()
    errors, sizes = [], []

    def render():
        try:
            sizes.append(page_images(data, "application/pdf", max_side=600)[0].size)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=render) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == [] and len(sizes) == 8


def test_scan_listing_is_reused_and_refresh_forces_a_new_one(settings):
    from vspm.services.context import build_context
    ctx = build_context(settings)
    calls = []
    original = ctx.documents.source.list_latest
    ctx.documents.source.list_latest = lambda n: calls.append(n) or original(n)
    first = ctx.documents.latest(10)
    second = ctx.documents.latest(10)  # every click reruns the page: no new Drive listing
    assert len(calls) == 1 and [d.doc_id for d in first] == [d.doc_id for d in second]
    ctx.documents.latest(10, refresh=True)
    assert len(calls) == 2
    ctx.documents.run_ocr(first[0].doc_id, ctx.ocr_provider())
    again = {d.doc_id: d for d in ctx.documents.latest(10)}  # still cached, but status/rows are current
    assert again[first[0].doc_id].rows > 0 and len(calls) == 2
