# AI handwriting extraction

## How it works

1. The selected scan is sent to the AI model: PDFs as a PDF document (all pages), images as JPEG pages
   (long side ≤ 2400 px).
2. The model must answer with JSON matching `ExtractedDocument` (`src/vspm/domain/models.py`). The request
   uses the API's structured outputs with a schema in which **every field is required** (an unreadable value
   must be stated as `null` + uncertain, never silently omitted); the reply is validated again with Pydantic.
   The request is streamed with a generous token limit so long multi-page documents are not cut off.
3. Every value carries the raw text, a confidence, an `uncertain` flag, `is_ditto` and an optional note.
   The prompt (`src/vspm/integrations/ocr/base.py`) forbids guessing: unreadable values must be `null` and
   uncertain.
4. Post-processing (`postprocess.py`) — not the model — copies ditto marks from the previous row and header
   values (customer, material, thickness, roll, location, date) into rows, flags everything uncertain, keeps
   the original OCR text separately, and suggests the label type and short name from the customer rules.
5. Flagged values block printing until the operator corrects or explicitly confirms them.

## Provider configuration

Default provider: Anthropic Claude (`claude-opus-5-5`, effort `high`). Other providers can be added by
implementing the `OcrProvider` protocol (`extract(OcrInput) -> OcrOutput`) and wiring it in
`services/context.py`.

```
VSPM_OCR_PROVIDER=anthropic
VSPM_OCR_MODEL=claude-opus-5-5
VSPM_OCR_EFFORT=high
VSPM_OCR_SERVER_FALLBACKS=true
VSPM_OCR_UPLOAD_CONSENT=true
ANTHROPIC_API_KEY=sk-ant-...
```

* Create an API key at <https://console.anthropic.com/> (Settings → API keys) and set a monthly spend limit.
* `VSPM_OCR_SERVER_FALLBACKS=true` lets the API retry a request that the model declines on a fallback model
  (beta `server-side-fallback-2026-07-01`). Set it to `false` to disable.
* Cost: one request per document read; a one-page scan is typically a few cents at the configured model's
  rates. Lower `VSPM_OCR_EFFORT` to reduce cost once accuracy on your documents is known.

## Data handling (read before enabling)

* With `VSPM_OCR_UPLOAD_CONSENT=false` (default) **no document is sent anywhere**; the app refuses.
* When enabled, each document you click *Read with AI* on is sent over HTTPS to Anthropic's API. Under
  Anthropic's commercial terms, API inputs/outputs are not used to train models and are retained only for a
  limited period (see <https://www.anthropic.com/legal/commercial-terms> and the privacy pages; zero-data-
  retention agreements are available to eligible organisations). Review this against your customers'
  confidentiality requirements.
* Documents are only sent when an operator presses the button; nothing is uploaded automatically.
* The extracted JSON is stored locally in `data\vspm.sqlite3`.

## Testing with your documents

```powershell
.\.venv\Scripts\vspm.exe ocr-test C:\scans\order_0123.pdf --save data\ocr_0123.json
```

prints every recognised row (⚠ marks uncertain fields) without creating anything in the app. Use it on
3–5 real documents first ([PILOT_CHECKLIST.md](PILOT_CHECKLIST.md) C4).

## Accuracy notes

* No real scans were available during development, so **extraction quality on your handwriting is untested**.
  Before go-live, run 20–30 real documents and compare with what you would type manually
  (see [PILOT_CHECKLIST.md](PILOT_CHECKLIST.md)).
* Common confusions the prompt asks the model to flag: 1/7, 0/6, 5/6, Greek Χ vs Latin X, Ρ vs P, Η vs H.
