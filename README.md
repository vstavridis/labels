# VS Production Manager

Scan a handwritten production order → let AI read it → check the rows → print each DYMO label → the row is
recorded in the Excel workbook automatically.

* Streamlit web app running locally on Windows (Greek interface, English optional)
* Google Drive scans · Claude vision for handwritten Greek · DYMO LabelWriter · OneDrive Excel workbook
* Central barcode registry so several computers never issue the same barcode

---

## For employees: daily use

1. Double-click **`scripts\start_vspm.bat`** (or the desktop shortcut). A browser page opens at
   `http://localhost:8501`. Keep the black window open while you work.
2. **Πίνακας ελέγχου**: the 5 latest scans appear. Click **Άνοιγμα** on the right one.
3. **Αναγνώριση εγγράφου**: click **Ανάγνωση με AI**. After a few seconds the rows appear.
4. **Έλεγχος γραμμών**: compare each row with the scan on the left.
   * Correct anything wrong directly in the table. Cells marked **⚠** were hard to read — check them.
   * Check the label type (VS / PG / PG0 / EX).
   * Tick **Εγκρ.** on the rows that are correct, tick the confirmation box, press **Αποθήκευση**.
5. **Ετικέτα & εκτύπωση**: choose a row, look at the preview, press **Εκτύπωση & Αποθήκευση**.
   When the label comes out, press **Η ετικέτα βγήκε σωστά** (or **ΔΕΝ βγήκε** → **Επανεκτύπωση**).
   A reprint always uses the same barcode and never adds a second Excel row.
6. If Excel could not be updated (e.g. the workbook was open), the label is still safe. Use
   **Ιστορικό → Εγγραφή στο Excel τώρα** later.

To stop the app, close the black window.

---

## Status: what is implemented and what still needs your environment

| Area | Implemented & tested here | Needs credentials / hardware / live test |
|---|---|---|
| Reference analysis | ✅ `docs/FINDINGS.md`, `docs/REFERENCE_REPORT.md` | — |
| Streamlit UI (6 pages, Greek/English) | ✅ driven end-to-end in a browser with mock services | Your review on the Windows PC |
| Google Drive (latest 5, preview, download, revision check) | ✅ code; local-folder & mock sources tested | **Not tested against Google** (needs your service account + folder) |
| AI handwriting extraction | ✅ Claude vision + structured output; request shape and post-processing tested with mocked responses | **Not tested on real scans** (none provided; needs API key + consent) |
| Label selection / customer rules | ✅ tested | Rules for your other customers |
| DYMO template filling + preview | ✅ tested on all 4 real templates | — |
| DYMO printing | ✅ mock printer; DYMO Web Service and COM adapters written, Web Service tested against a simulated service | **No physical print has been made.** Run the DYMO checklist |
| Barcode registry (PostgreSQL service) | ✅ service + client, concurrency/idempotency tests on SQLite | Deploying it with PostgreSQL; counter reconciliation |
| Excel writing | ✅ exact A–P mapping, L–O formulas, dates, idempotency, locks, backups; tested on a copy of the real workbook and opened in LibreOffice | **Not tested with Excel itself, OneDrive sync or concurrent editors** |
| Transaction tracking, reprint, recovery | ✅ tested | — |

Nothing in this repository has printed a physical label, authenticated to Google, called the AI service with a
real document, or written to the live OneDrive workbook.

---

## Installation (Windows)

Full guide: [docs/WINDOWS_SETUP.md](docs/WINDOWS_SETUP.md). Short version:

```powershell
git clone <your private repo URL> vs-production-manager
cd vs-production-manager
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
```

Then edit `.env` (created from `.env.example`) and start with `scripts\start_vspm.bat`.

Out of the box the app runs in **safe demo mode**: mock scans, mock AI, mock printer, barcode allocation
disabled, Excel in staging mode pointing at a test copy. Switch each integration on with the
[integration checklist](docs/INTEGRATION_CHECKLIST.md).

## Documentation

| Topic | File |
|---|---|
| Reference files & discrepancies | [docs/FINDINGS.md](docs/FINDINGS.md), [docs/REFERENCE_REPORT.md](docs/REFERENCE_REPORT.md) |
| Architecture, states, data model | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Windows setup & launch | [docs/WINDOWS_SETUP.md](docs/WINDOWS_SETUP.md) |
| Google Drive | [docs/GOOGLE_DRIVE.md](docs/GOOGLE_DRIVE.md) |
| AI provider & data handling | [docs/AI_PROVIDER.md](docs/AI_PROVIDER.md) |
| DYMO printing | [docs/DYMO.md](docs/DYMO.md) |
| Excel / OneDrive | [docs/EXCEL_ONEDRIVE.md](docs/EXCEL_ONEDRIVE.md) |
| Barcode registry & migration | [docs/BARCODES.md](docs/BARCODES.md) |
| Tests | [docs/TESTING.md](docs/TESTING.md) |
| Go-live checklist | [docs/INTEGRATION_CHECKLIST.md](docs/INTEGRATION_CHECKLIST.md) |
| Troubleshooting | [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) |

## Repository layout

```
src/vspm/              application package
  ui/                  Streamlit app (app.py) and pages (views/)
  services/            production transactions, documents, customers, transforms, context
  integrations/        Google Drive / local / mock sources, AI providers (ocr/)
  dymo/                .label parsing & filling, preview renderer, printer adapters, Code 39
  excel/               surgical .xlsx writer, formula translator, safety checks
  barcodes/            allocators: disabled, mock, pilot range, registry client
  db/                  SQLite schema migrations and repository
src/vspm_registry/     central barcode registry service (FastAPI, PostgreSQL)
config/                templates.yaml, customers.yaml, materials.yaml
deploy/registry/       Dockerfile + docker-compose for the registry
scripts/               Windows setup/start scripts, fixture generator
tests/                 pytest suite (sanitized label fixtures, synthetic workbook)
reference_files/       your confidential files (git-ignored)
```

Never commit `.env`, `credentials/`, `data/` or anything in `reference_files/` (all are git-ignored).
