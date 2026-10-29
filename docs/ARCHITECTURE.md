# Architecture

```
 Google Drive ──► DocumentSource ──► DocumentService ──► OCR provider (Claude vision / mock)
                                          │                     │ ExtractedDocument (Pydantic)
                                          ▼                     ▼
                                    SQLite (local state) ◄── postprocess: ditto/header inheritance,
                                          ▲                  uncertainty flags, customer rules
                                          │
 Streamlit UI ──► ProductionService (Print & Save transaction manager)
                     │            │                 │                      │
                     ▼            ▼                 ▼                      ▼
              BarcodeAllocator  TemplateRegistry  LabelPrinter        SafeWorkbookWriter
              (registry/pilot)  (.label filling)  (DYMO web/COM/mock) (surgical .xlsx edit)
                     │                                                     │
                     ▼                                                     ▼
          VS Barcode Registry (FastAPI + PostgreSQL)          OneDrive-synced Σχισμενα.xlsx
          sequences · reservations · audit · Excel queue
```

All integrations are behind small interfaces (`DocumentSource`, `OcrProvider`, `LabelPrinter`,
`BarcodeAllocator`) with mock implementations, built from settings in `services/context.py`. Integration
failures are collected in `AppContext.problems` and shown on the dashboard instead of crashing the app.

## Print & Save transaction

```
approved row ─► validate (required fields, ⚠ flags, label type confirmed, template intact, EX translation)
   ─► reserve barcode   (idempotency key = install id + row id; registry/pilot range; never offline)
   ─► COMMIT label record  [print_status = barcode_reserved]
   ─► fill template copy, save .label to data/labels/, submit print
         ├─ failed  ─► [print_status = error]   (retry reuses the same barcode)
         └─ accepted─► [print_unconfirmed], print date = now in Europe/Athens
                        ─► Excel payload frozen  [excel_status = pending]
                        ─► sync by mode: direct ─► [synchronized | conflict | error]
                                         staging ─► [staged]  (apply / reconcile later)
                                         registry_queue ─► [queued_remote] ─► worker ─► [synchronized]
operator ─► "printed OK" [printed_confirmed]  |  "not printed" [print_failed_confirmed] ─► Reprint
Reprint  ─► same stored content + barcode, new print_attempt, Excel untouched
Restart  ─► recover(): printed-but-not-queued labels are queued; reserved-not-printed labels are listed
```

Row states: draft → ocr_complete / needs_review → approved → barcode_reserved → print_unconfirmed →
printed_confirmed (or error). Print and Excel statuses are separate columns on the `labels` table.

## Local database (SQLite, `data/vspm.sqlite3`)

Versioned SQL migrations in `src/vspm/db/migrations/`, applied automatically at start.
Tables: `documents` (Drive id + revision, OCR JSON), `production_rows` (values, OCR originals, flags,
approval), `labels` (one per row: barcode, reservation, printed content snapshot, statuses, Excel payload),
`print_attempts`, `excel_attempts`, `customer_rules`, `local_sequences`/`local_reservations` (pilot/mock
allocation only), `app_settings`, `audit_log`. SQLite is never the barcode authority across computers.

## Key design decisions

* **Label text is filled at XML level**, keeping each styled run's font — DYMO's `SetObjectText` would flatten
  bold/regular formatting. The barcode is unlinked from DYMO's `COUNTER` and set to the allocated number.
* **The workbook is edited surgically** (only the sheet XML, styles and calcPr), because openpyxl drops form
  controls/drawings and corrupts the external-link formula. Writes are idempotent by barcode and verified by
  reading the file back.
* **Excel and printing are independent**; an Excel failure never blocks or repeats a label.
* **Configuration over code**: label templates/run mapping (`templates.yaml`), customer rules (DB, seeded from
  `customers.yaml`), material translations and dimension rules (`materials.yaml`).

## Development phases (all implemented in this repository)

1. Foundation: config, DB, models, UI skeleton · 2. Reference analysis (`vspm report`) · 3. Drive + AI +
editable rows · 4. Label selection, preview, mock printing · 5. DYMO web-service and COM adapters ·
6. Barcode registry, pilot ranges, manual block reservations · 7. Excel writer, staging, reconciliation,
Excel queue · 8. Tests · 9. Deployment documentation.
