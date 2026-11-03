"""OpenAI provider: request format, strict schema, error handling (fake client, no network)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("openai")

from vspm.integrations.ocr.base import OcrError, OcrInput, OcrNotAllowedError  # noqa: E402
from vspm.integrations.ocr.openai_provider import OpenAIOcr, strict_schema  # noqa: E402
from vspm.integrations.ocr.providers import MockOcr  # noqa: E402


class FakeCompletions:
    def __init__(self, content=None, refusal=None, finish="stop", error=None):
        self.content, self.refusal, self.finish, self.error = content, refusal, finish, error
        self.kwargs = None

    def create(self, **kw):
        self.kwargs = kw
        if self.error:
            raise self.error
        msg = SimpleNamespace(content=self.content, refusal=self.refusal)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=self.finish)])


def client(**kw):
    comp = FakeCompletions(**kw)
    return SimpleNamespace(chat=SimpleNamespace(completions=comp)), comp


def scan() -> OcrInput:
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(buf, format="JPEG")
    return OcrInput(buf.getvalue(), "image/jpeg", "scan.jpg")


def expected_json() -> str:
    return MockOcr().extract(OcrInput(b"", "image/jpeg", "x.jpg")).raw_json


def test_schema_is_strict_every_field_required_no_defaults():
    s = json.dumps(strict_schema())
    assert '"default"' not in s

    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                assert set(node["required"]) == set(node["properties"]) and node["additionalProperties"] is False
            for v in node.values():
                check(v)
        elif isinstance(node, list):
            for v in node:
                check(v)
    check(strict_schema())


def test_request_and_result():
    c, comp = client(content=expected_json())
    out = OpenAIOcr("k", model="gpt-5", effort="xhigh", upload_consent=True, client=c).extract(scan())
    assert out.provider == "openai" and out.model == "gpt-5" and out.document.rows
    kw = comp.kwargs
    assert kw["response_format"]["json_schema"]["strict"] is True and kw["reasoning_effort"] == "high"
    parts = kw["messages"][1]["content"]
    assert parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,") and parts[0]["image_url"]["detail"] == "high"
    assert kw["messages"][0]["role"] == "system" and "HANDWRITTEN Greek" in kw["messages"][0]["content"]


def test_non_reasoning_model_gets_no_reasoning_effort():
    c, comp = client(content=expected_json())
    OpenAIOcr("k", model="gpt-4.1", upload_consent=True, client=c).extract(scan())
    assert "reasoning_effort" not in comp.kwargs


def test_refusal_truncation_and_bad_json_are_errors():
    for kw, msg in [({"refusal": "no"}, "declined"), ({"content": "{}", "finish": "length"}, "cut off"),
                    ({"content": '{"rows": "x"}'}, "expected structure")]:
        c, _ = client(**kw)
        with pytest.raises(OcrError, match=msg):
            OpenAIOcr("k", upload_consent=True, client=c).extract(scan())


def test_api_errors_explained():
    import httpx
    import openai
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    err = openai.AuthenticationError("bad key", response=httpx.Response(401, request=req), body=None)
    c, _ = client(error=err)
    with pytest.raises(OcrError, match="OPENAI_API_KEY"):
        OpenAIOcr("k", upload_consent=True, client=c).extract(scan())


def test_consent_and_key_required():
    with pytest.raises(OcrNotAllowedError):
        OpenAIOcr("k", upload_consent=False)
    with pytest.raises(OcrError, match="OPENAI_API_KEY"):
        OpenAIOcr("", upload_consent=True)


def test_context_selects_openai(settings):
    from vspm.services.context import build_context
    from tests.conftest import with_settings
    ctx = build_context(with_settings(settings, ocr_provider="openai", ocr_upload_consent=True))
    ctx.settings = with_settings(settings, ocr_provider="openai", ocr_upload_consent=True)
    import os
    os.environ.pop("OPENAI_API_KEY", None)
    with pytest.raises(OcrError, match="OPENAI_API_KEY"):
        ctx.ocr_provider()
