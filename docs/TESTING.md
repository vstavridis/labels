# Tests

```bash
.venv/bin/python -m pytest -q        # Windows: .venv\Scripts\python.exe -m pytest -q
```

CI runs the suite on Ubuntu, macOS and Windows.

The suite never touches the live workbook: it uses a synthetic workbook generated per test (same sheet name,
`[1]` formulas, hidden pre-formatted rows, extra package parts that must survive) and sanitized label fixtures
in `tests/fixtures/labels/` (real template structure, logos and customer text removed; regenerate with
`python scripts/make_label_fixtures.py reference_files tests/fixtures/labels`).

| File | Covers |
|---|---|
| `test_ocr_postprocess.py` | mocked AI responses, 3-row document → 3 rows, header inheritance, ditto marks, uncertainty flags, no invented values, source vs generated barcode, multi-page order |
| `test_customers.py` | normalisation (accents, case, spacing, Greek/Latin look-alikes), VS/PG/PG0/EX rules, priorities, Latin names need confirmation |
| `test_labels.py` | filling all four templates without changing layout/fonts, barcode unlinked from COUNTER, PG0→PG, EX translation, thickness formats, Code 39, preview |
| `test_excel.py` | exact A–P mapping, L–O formulas per row (350→K350/F350, 351→…), external-link preservation, dates, duplicate prevention, untouched package parts, hidden rows, locks, OneDrive conflict copies, concurrent change, backups |
| `test_barcodes.py` | pilot/mock allocator, registry concurrency (90 parallel requests), idempotency, max ranges, manual blocks, client errors, Excel queue |
| `test_production.py` | Print & Save, reprint (same barcode, no Excel row), failed print/retry, failed Excel/retry, registry offline, restart recovery, staging + reconciliation, timezone date |
| `test_integrations.py` | local/mock document sources, PDF pages, AI request shape/consent/refusal (fake client), DYMO web-service adapter (simulated), CUPS adapter (fake `lp`, PDF page size), `.env` editing |
| `test_reference_files.py` | **only when `reference_files\` exists**: real templates match config; write to a temporary copy of the real workbook (only 3 parts change, formulas consistent, original unchanged); open result in LibreOffice if installed |

## Manual integration tests on your computers

See [INTEGRATION_CHECKLIST.md](INTEGRATION_CHECKLIST.md). These cannot run in CI: physical printing, Google
Drive authentication, AI on real scans, Excel desktop opening the written file, OneDrive sync behaviour.
