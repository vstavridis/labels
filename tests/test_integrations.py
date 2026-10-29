"""Document sources, AI provider request shape, DYMO adapters, .env editing."""

from __future__ import annotations

import io
import json
import os
import time
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from vspm.domain.models import ExtractedDocument
from vspm.dymo.printers import DymoWebServicePrinter, MockPrinter
from vspm.envfile import read_env, update_env
from vspm.integrations.documents import LocalFolderSource, MockSource, page_images
from vspm.integrations.ocr.base import OcrError, OcrInput, OcrNotAllowedError
from vspm.integrations.ocr.providers import AnthropicOcr


def _pdf(pages: int) -> bytes:
    imgs = [Image.new("RGB", (600, 800), "white") for _ in range(pages)]
    buf = io.BytesIO()
    imgs[0].save(buf, format="PDF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


def test_local_folder_lists_latest_five(tmp_path):
    for i in range(7):
        p = tmp_path / f"scan{i}.png"
        Image.new("RGB", (10, 10)).save(p)
        os.utime(p, (time.time() + i, time.time() + i))
    (tmp_path / "notes.txt").write_text("x")
    docs = LocalFolderSource(tmp_path).list_latest(5)
    assert [d.name for d in docs] == [f"scan{i}.png" for i in (6, 5, 4, 3, 2)]
    assert LocalFolderSource(tmp_path).download(docs[0])[:4] == b"\x89PNG"


def test_revision_changes_when_file_changes(tmp_path):
    p = tmp_path / "a.png"
    Image.new("RGB", (10, 10)).save(p)
    r1 = LocalFolderSource(tmp_path).list_latest(1)[0].revision
    Image.new("RGB", (20, 20)).save(p)
    os.utime(p, (time.time() + 5, time.time() + 5))
    assert LocalFolderSource(tmp_path).list_latest(1)[0].revision != r1


def test_multi_page_pdf_rendering():
    assert len(page_images(_pdf(3), "application/pdf")) == 3


def test_mock_source():
    docs = MockSource().list_latest(5)
    assert len(docs) == 5 and MockSource().download(docs[0])[:4] == b"\x89PNG"


class FakeStream:
    def __init__(self, resp):
        self.resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.resp


class FakeMessages:
    def __init__(self, stop_reason="end_turn", text=None):
        self.calls = []
        self.stop_reason = stop_reason
        self.text = text if text is not None else ExtractedDocument.model_validate(
            {"rows": [{"row_number": 1}]}).model_dump_json()

    def stream(self, **kw):
        self.calls.append(kw)
        return FakeStream(SimpleNamespace(stop_reason=self.stop_reason,
                                          content=[SimpleNamespace(type="text", text=self.text)]))


def fake_client(stop_reason="end_turn", text=None):
    m = FakeMessages(stop_reason, text)
    return SimpleNamespace(messages=m, beta=SimpleNamespace(messages=m)), m


def test_ai_requires_explicit_upload_consent():
    with pytest.raises(OcrNotAllowedError):
        AnthropicOcr(api_key="x", upload_consent=False)


def test_ai_request_for_pdf_uses_document_block_and_structured_output():
    client, m = fake_client()
    ocr = AnthropicOcr(api_key="x", upload_consent=True, client=client)
    out = ocr.extract(OcrInput(_pdf(2), "application/pdf", "a.pdf"))
    call = m.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["effort"] == "high"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["max_tokens"] >= 32000
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]
    content = call["messages"][0]["content"]
    assert content[0]["type"] == "document" and content[0]["source"]["media_type"] == "application/pdf"
    assert content[-1]["type"] == "text"
    assert len(out.document.rows) == 1


def test_ai_request_for_image_without_fallbacks():
    client, m = fake_client()
    buf = io.BytesIO()
    Image.new("RGB", (3000, 2000), "white").save(buf, format="PNG")
    AnthropicOcr(api_key="x", upload_consent=True, server_fallbacks=False, client=client).extract(
        OcrInput(buf.getvalue(), "image/png", "a.png"))
    call = m.calls[0]
    assert "fallbacks" not in call
    img = call["messages"][0]["content"][0]
    assert img["type"] == "image" and img["source"]["media_type"] == "image/jpeg"


def test_ai_refusal_is_reported():
    client, _ = fake_client("refusal")
    with pytest.raises(OcrError, match="declined"):
        AnthropicOcr(api_key="x", upload_consent=True, client=client).extract(OcrInput(_pdf(1), "application/pdf", "a"))


def test_ai_schema_requires_every_field_and_matches_model():
    from vspm.integrations.ocr.providers import extraction_output_format
    schema = extraction_output_format()["schema"]

    def objects(n):
        if isinstance(n, dict):
            if n.get("type") == "object":
                yield n
            for v in n.values():
                yield from objects(v)
        elif isinstance(n, list):
            for v in n:
                yield from objects(v)

    objs = list(objects(schema))
    assert {o.get("title") for o in objs} == {"ExtractedDocument", "ExtractedRow", "ExtractedValue"}
    for o in objs:
        assert o["additionalProperties"] is False and set(o["required"]) == set(o["properties"])
    raw = schema["$defs"]["ExtractedValue"]["properties"]["raw"]
    assert "null" in str(raw)  # unreadable values can be stated as null


def test_ai_response_not_matching_schema_is_reported():
    client, _ = fake_client(text='{"rows": "not a list"}')
    with pytest.raises(OcrError, match="expected structure"):
        AnthropicOcr(api_key="x", upload_consent=True, client=client).extract(OcrInput(_pdf(1), "application/pdf", "a"))


def test_dymo_web_service_adapter():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("StatusConnected"):
            return httpx.Response(200, text="true")
        if path.endswith("GetPrinters"):
            xml = ("<Printers><LabelWriterPrinter><Name>DYMO LabelWriter Wireless</Name>"
                   "<ModelName>DYMO LabelWriter Wireless</ModelName><IsConnected>True</IsConnected>"
                   "</LabelWriterPrinter></Printers>")
            return httpx.Response(200, text=json.dumps(xml))
        if path.endswith("PrintLabel"):
            seen["form"] = dict(httpx.QueryParams(request.content.decode()))
            return httpx.Response(200, text="true")
        if path.endswith("RenderLabel"):
            return httpx.Response(200, text=json.dumps("iVBORw0KGgo="))
        return httpx.Response(404)

    p = DymoWebServicePrinter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert p.status()[0]
    assert p.list_printers()[0].name == "DYMO LabelWriter Wireless"
    res = p.print_label("<DieCutLabel/>", "DYMO LabelWriter Wireless")
    assert res.ok and seen["form"]["printerName"] == "DYMO LabelWriter Wireless"
    assert seen["form"]["labelXml"] == "<DieCutLabel/>"
    assert p.render_preview("<x/>")[:4] == b"\x89PNG"


def test_dymo_web_service_unreachable():
    p = DymoWebServicePrinter("https://127.0.0.1:9", client=httpx.Client(timeout=0.5))
    assert not p.status()[0]
    assert not p.print_label("<x/>", "p").ok


def test_mock_printer_failure_injection(tmp_path):
    mp = MockPrinter(tmp_path)
    mp.fail_next = "jam"
    assert not mp.print_label("<x/>", "p").ok


def test_env_file_update_keeps_comments(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# comment\nVSPM_A=1   # inline\nVSPM_B=x\n", encoding="utf-8")
    update_env(p, {"VSPM_A": "two words", "VSPM_C": "΄Ετοιμα προϊόντα"})
    text = p.read_text(encoding="utf-8")
    assert text.startswith("# comment")
    assert read_env(p) == {"VSPM_A": "two words", "VSPM_B": "x", "VSPM_C": "΄Ετοιμα προϊόντα"}


def test_cups_printer_builds_lp_command_and_pdf(registry):
    from vspm.dymo.printers import CupsPrinter
    from vspm.domain.models import LabelContent, TemplateKey
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "lpstat":
            return SimpleNamespace(returncode=0, stdout="DYMO_LabelWriter_Wireless\n", stderr="")
        assert open(cmd[-1], "rb").read(4) == b"%PDF"
        return SimpleNamespace(returncode=0, stdout="request id is DYMO-1 (1 file(s))", stderr="")

    p = CupsPrinter("-o fit-to-page", runner=runner)
    spec = registry.spec(TemplateKey.VS)
    xml = registry.load(TemplateKey.VS).populate(spec, LabelContent(
        template_key=TemplateKey.VS, material="M", thickness="0,60", dimensions="D", pieces="1",
        customer_short="C", weight="1", barcode="VS1"), 1)
    assert p.status()[0] and p.list_printers()[0].name == "DYMO_LabelWriter_Wireless"
    res = p.print_label(xml, "DYMO_LabelWriter_Wireless")
    assert res.ok and "DYMO-1" in res.job_ref
    assert calls[-1][:3] == ["lp", "-d", "DYMO_LabelWriter_Wireless"] and "fit-to-page" in calls[-1]
    import pypdfium2 as pdfium
    w, h = pdfium.PdfDocument(p.render_pdf(xml))[0].get_size()  # points; rotated to the feed direction
    assert h > w and abs(w / 72 * 25.4 - 54) < 1 and abs(h / 72 * 25.4 - 100.8) < 1


def test_cups_printer_reports_lp_error(registry):
    from vspm.dymo.printers import CupsPrinter
    p = CupsPrinter(runner=lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="", stderr="lp: no such printer"))
    res = p.print_label(registry.load("VS").path.read_text(encoding="utf-8"), "X")
    assert not res.ok and "no such printer" in res.error
