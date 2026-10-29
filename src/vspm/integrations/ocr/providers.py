"""OCR provider implementations: Anthropic Claude (vision) and a mock."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Optional

from vspm.domain.models import ExtractedDocument
from vspm.integrations.documents import page_images, to_jpeg
from vspm.integrations.ocr.base import (
    SYSTEM_PROMPT, USER_PROMPT, OcrError, OcrInput, OcrNotAllowedError, OcrOutput,
)


class AnthropicOcr:
    """Claude vision with structured outputs (validated against ExtractedDocument)."""

    name = "anthropic"
    FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(self, api_key: str, model: str = "claude-opus-5-5", effort: str = "high",
                 upload_consent: bool = False, server_fallbacks: bool = True, client=None):
        if not upload_consent:
            raise OcrNotAllowedError(
                "Sending documents to the AI provider is not enabled. Set VSPM_OCR_UPLOAD_CONSENT=true after "
                "reviewing docs/AI_PROVIDER.md (data handling).")
        try:
            import anthropic  # type: ignore
        except ImportError as e:
            raise OcrError("anthropic package missing: pip install -e .[ai]") from e
        self._anthropic = anthropic
        self.client = client or (anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic())
        self.model = model
        self.effort = effort
        self.server_fallbacks = server_fallbacks

    def _content(self, doc: OcrInput) -> list[dict]:
        blocks: list[dict] = []
        if doc.mime_type == "application/pdf":
            blocks.append({"type": "document", "source": {
                "type": "base64", "media_type": "application/pdf",
                "data": base64.standard_b64encode(doc.data).decode("ascii")}})
        else:
            pages = page_images(doc.data, doc.mime_type)
            for i, img in enumerate(pages, 1):
                if len(pages) > 1:
                    blocks.append({"type": "text", "text": f"Page {i} of {len(pages)}:"})
                blocks.append({"type": "image", "source": {
                    "type": "base64", "media_type": "image/jpeg",
                    "data": base64.standard_b64encode(to_jpeg(img)).decode("ascii")}})
        blocks.append({"type": "text", "text": USER_PROMPT})
        return blocks

    def extract(self, doc: OcrInput) -> OcrOutput:
        a = self._anthropic
        kwargs = dict(
            model=self.model,
            max_tokens=64000,  # adaptive thinking counts toward this; streaming avoids HTTP timeouts
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": self._content(doc)}],
            output_config={"effort": self.effort, "format": extraction_output_format()},
        )
        try:
            if self.server_fallbacks:
                stream = self.client.beta.messages.stream(betas=[self.FALLBACK_BETA], fallbacks="default", **kwargs)
            else:
                stream = self.client.messages.stream(**kwargs)
            with stream as s:
                resp = s.get_final_message()
        except a.AuthenticationError as e:
            raise OcrError("AI provider rejected the API key (ANTHROPIC_API_KEY)") from e
        except a.RateLimitError as e:
            raise OcrError("AI provider rate limit reached - wait a minute and retry") from e
        except a.BadRequestError as e:
            raise OcrError(f"AI request rejected: {e}") from e
        except a.APIStatusError as e:
            raise OcrError(f"AI provider error {e.status_code}: {e}") from e
        except a.APIConnectionError as e:
            raise OcrError(f"Cannot reach the AI provider: {e}") from e
        if resp.stop_reason == "refusal":
            raise OcrError("The AI provider declined to process this document")
        if resp.stop_reason == "max_tokens":
            raise OcrError("The AI response was cut off (document too long); split the document and retry")
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
        try:
            parsed = ExtractedDocument.model_validate_json(text)
        except ValueError as e:
            raise OcrError(f"The AI response did not match the expected structure: {e}") from e
        return OcrOutput(parsed, self.name, self.model, parsed.model_dump_json())


def extraction_output_format() -> dict:
    """JSON schema sent to the API: the SDK's strict transform of ExtractedDocument with
    EVERY property required, so the model must state each field explicitly (null +
    uncertain when unreadable) instead of omitting it."""
    from anthropic.lib._parse._transform import transform_schema  # SDK helper used by messages.parse

    schema = transform_schema(ExtractedDocument)

    def require_all(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"])
                node["additionalProperties"] = False
            for v in node.values():
                require_all(v)
        elif isinstance(node, list):
            for v in node:
                require_all(v)

    require_all(schema)
    return {"type": "json_schema", "schema": schema}


class MockOcr:
    """Returns a fixed or file-provided extraction. For demos and tests only."""

    name = "mock"
    model = "mock"

    def __init__(self, fixture: Optional[Path] = None):
        self.fixture = fixture

    def extract(self, doc: OcrInput) -> OcrOutput:
        if self.fixture and self.fixture.exists():
            data = json.loads(self.fixture.read_text(encoding="utf-8"))
        else:
            data = DEMO_EXTRACTION
        parsed = ExtractedDocument.model_validate(data)
        return OcrOutput(parsed, self.name, self.model, parsed.model_dump_json())


def _v(raw, conf=0.97, uncertain=False, ditto=False, note=None):
    return {"raw": raw, "confidence": conf, "uncertain": uncertain, "is_ditto": ditto, "note": note}


# Matches the synthetic mock scan (three rows, header values, one ditto mark).
DEMO_EXTRACTION = {
    "customer": _v("ΒΗΤΑΚΗΣ - ΓΑΜΜΑΤΟΥ"),
    "material": _v("ΓΑΛΒΑΝΙΖΕ"),
    "thickness": _v("0,60"),
    "source_roll": _v("ROLL-T-0001", 0.8, True, note="E or Ε? first character"),
    "location": _v("7"),
    "document_date": _v("21/10/2026"),
    "dimensions": _v(None, 0.0),
    "rows": [
        {"row_number": 1, "dimensions": _v("34ΧΡΟΛΟ"), "weight": _v("496"), "pieces": _v("4"),
         "packages": _v("1"), "source_barcode": _v("B-101")},
        {"row_number": 2, "dimensions": _v(None, 0.9, ditto=True), "weight": _v("502"), "pieces": _v("4"),
         "packages": _v("1"), "source_barcode": _v("B-102")},
        {"row_number": 3, "dimensions": _v("50ΧΡΟΛΟ"), "weight": _v("730", 0.6, True, note="730 or 750"),
         "pieces": _v("3"), "packages": _v("1"), "source_barcode": _v("B-103")},
    ],
    "page_count": 1,
    "warnings": [],
}
