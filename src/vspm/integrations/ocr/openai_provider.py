"""OpenAI vision provider (VSPM_OCR_PROVIDER=openai).

Same prompt, same output model (ExtractedDocument) and the same review rules as the Anthropic
provider: the model must return every field (null + uncertain when unreadable) through
Structured Outputs with a strict JSON schema, and the result is validated before it is shown.
Key: OPENAI_API_KEY (Streamlit secrets). Model: VSPM_OCR_OPENAI_MODEL (default gpt-5).
"""

from __future__ import annotations

import base64
import copy

from vspm.domain.models import ExtractedDocument
from vspm.integrations.documents import page_images, to_jpeg
from vspm.integrations.ocr.base import SYSTEM_PROMPT, USER_PROMPT, OcrError, OcrInput, OcrNotAllowedError, OcrOutput

REASONING_EFFORT = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}
DROP_KEYS = {"default", "title"}  # not used by strict structured outputs


def strict_schema() -> dict:
    """JSON schema of ExtractedDocument for strict Structured Outputs: every property required,
    no additional properties, no defaults/titles."""
    schema = copy.deepcopy(ExtractedDocument.model_json_schema())

    def fix(node):
        if isinstance(node, dict):
            for k in DROP_KEYS & set(node):
                if not isinstance(node[k], dict):  # a keyword, not a property that happens to be named so
                    node.pop(k)
            if node.get("type") == "object" and isinstance(node.get("properties"), dict):
                node["required"] = list(node["properties"])
                node["additionalProperties"] = False
            if "$ref" in node and len(node) > 1:  # a $ref must stand alone
                for k in [k for k in node if k != "$ref"]:
                    node.pop(k)
            for v in list(node.values()):
                fix(v)
        elif isinstance(node, list):
            for v in node:
                fix(v)

    fix(schema)
    return schema


class OpenAIOcr:
    name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-5", effort: str = "high", upload_consent: bool = False,
                 client=None):
        if not upload_consent:
            raise OcrNotAllowedError(
                "Sending documents to the AI provider is not enabled. Set VSPM_OCR_UPLOAD_CONSENT=true after "
                "reviewing docs/AI_PROVIDER.md (data handling).")
        try:
            import openai  # type: ignore
        except ImportError as e:
            raise OcrError("openai package missing: pip install openai") from e
        if client is None and not api_key:
            raise OcrError("OPENAI_API_KEY is not set (Streamlit secrets).")
        self._openai = openai
        self.client = client or openai.OpenAI(api_key=api_key, timeout=300, max_retries=2)
        self.model = model
        self.effort = effort

    def _content(self, doc: OcrInput) -> list[dict]:
        parts: list[dict] = []
        pages = page_images(doc.data, doc.mime_type)  # PDFs are rendered page by page
        for i, img in enumerate(pages, 1):
            if len(pages) > 1:
                parts.append({"type": "text", "text": f"Page {i} of {len(pages)}:"})
            b64 = base64.standard_b64encode(to_jpeg(img)).decode("ascii")
            parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}",
                                                             "detail": "high"}})
        parts.append({"type": "text", "text": USER_PROMPT})
        return parts

    def extract(self, doc: OcrInput) -> OcrOutput:
        o = self._openai
        kwargs = dict(
            model=self.model,
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": self._content(doc)}],
            response_format={"type": "json_schema", "json_schema": {
                "name": "extracted_document", "strict": True, "schema": strict_schema()}},
            max_completion_tokens=32000,
        )
        if self.model.startswith(("gpt-5", "o1", "o3", "o4")):  # reasoning models
            kwargs["reasoning_effort"] = REASONING_EFFORT.get(self.effort, "high")
        try:
            resp = self.client.chat.completions.create(**kwargs)
        except o.AuthenticationError as e:
            raise OcrError("OpenAI rejected the API key (OPENAI_API_KEY)") from e
        except o.RateLimitError as e:
            raise OcrError("OpenAI rate limit or quota reached - wait a minute, or check the billing of the "
                           "OpenAI account") from e
        except o.NotFoundError as e:
            raise OcrError(f"OpenAI model '{self.model}' is not available for this key "
                           "(set VSPM_OCR_OPENAI_MODEL)") from e
        except o.BadRequestError as e:
            raise OcrError(f"OpenAI request rejected: {e}") from e
        except o.APIStatusError as e:
            raise OcrError(f"OpenAI error {e.status_code}: {e}") from e
        except o.APIConnectionError as e:
            raise OcrError(f"Cannot reach OpenAI: {e}") from e
        choice = resp.choices[0]
        if getattr(choice.message, "refusal", None):
            raise OcrError("OpenAI declined to process this document")
        if choice.finish_reason == "length":
            raise OcrError("The AI response was cut off (document too long); split the document and retry")
        text = choice.message.content or ""
        try:
            parsed = ExtractedDocument.model_validate_json(text)
        except ValueError as e:
            raise OcrError(f"The AI response did not match the expected structure: {e}") from e
        return OcrOutput(parsed, self.name, self.model, parsed.model_dump_json())
