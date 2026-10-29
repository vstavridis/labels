# Windows production pilot — deployment checklist

**Pilot goal:** select one scanned production document, verify its rows, print **one** correct DYMO label,
and create **one** matching Excel entry in a **test copy** of the workbook.

Legend: ✅ verified during development (evidence given) · 🖨️ needs your printer · 🔑 needs your credentials
· 🏭 needs your live environment (OneDrive, colleagues, network). Nothing marked 🖨️/🔑/🏭 has been tested.

---

## Part 1 — already verified

| # | What | Evidence |
|---|---|---|
| V1 | Installation with `scripts\setup_windows.ps1` on a clean Windows Server 2022 runner (Python 3.12) | CI job *Windows installation and launch* ([TEST_RESULTS.md](TEST_RESULTS.md)) |
| V2 | App starts with `scripts\start_vspm.bat` and serves pages on Windows | same CI job (health check + page request) |
| V3 | Full automated test suite on Windows, macOS and Linux | CI jobs *tests (…)* |
| V4 | All six pages render; *Read with AI* (mock) → rows; *Εκτύπωση & Αποθήκευση* → one label + one Excel row | `tests/test_ui.py` (headless Streamlit, all OS) |
| V5 | Labels filled from your **real templates** keep every object, position, font, image; only text, weight and barcode change; barcode unlinked from COUNTER; template files unchanged (SHA-256) | `vspm pilot-dryrun` on the real templates; `tests/test_labels.py`, `tests/test_pilot_safety.py` |
| V6 | One Print & Save on a **copy of the real Σχισμενα.xlsx**: new row 330, exactly once, A–K values, formulas L–O with `[1]` external link, real date `dd/mm/yyyy`, reprint adds nothing, second Print & Save refused, source workbook unchanged | `vspm pilot-dryrun --workbook <copy>` ([TEST_RESULTS.md](TEST_RESULTS.md)) |
| V7 | Formulas M, N, O of a new row calculate the same results as existing rows (LibreOffice recalculation, 984/984 cells identical); only package parts workbook/styles/sheet changed | [TEST_RESULTS.md](TEST_RESULTS.md) |
| V8 | Barcode registry: no duplicates, no gaps, idempotent retries with two registry processes on PostgreSQL 16 | `tests/test_registry_postgres.py` (local PostgreSQL 16 + CI PostgreSQL job) |
| V9 | Windows file-lock handling: workbook held open by another program → clear error, nothing written; brief locks retried | `tests/test_excel.py` (real Windows handle test runs on the Windows CI runner) |
| V10 | Duplicate protection: re-reading a printed document, same line on another PC, two tabs printing at once | `tests/test_pilot_safety.py` |
| V11 | Google Drive connector logic (query, latest 5, revisions, download, errors) | `tests/test_drive.py` with a fake Drive API |
| V12 | AI request format and validation (all fields required, streaming, refusal/truncation handling) | `tests/test_integrations.py` with a fake client |
| V13 | No credentials/IDs in the repository; customer data removed from the current version | [SECURITY.md](SECURITY.md) |

---

## Part 2 — pilot checklist on your Windows PC

Work top to bottom. Tick, date and note results. Stop at the first failure.

### A. Installation  (≈20 min)
- [ ] A1 Repository **made private** on GitHub ([SECURITY.md](SECURITY.md)).
- [ ] A2 Python 3.12 installed (*Add to PATH* ticked). Project cloned to **`C:\VSPM`** (a local folder,
      *not* Documents/Desktop if those are synced by OneDrive).
- [ ] A3 `powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1` finishes with *Done* and all
      tests passed.
- [ ] A4 Copy the four `.label` templates and a copy of `Σχισμενα.xlsx` into `C:\VSPM\reference_files\`.
      Run `.\.venv\Scripts\vspm.exe make-test-workbook` → `data\test_workbook\Σχισμενα.xlsx`.
- [ ] A5 Put your real customer rules in `config\customers.yaml` (copied from the example; stays local).
- [ ] A6 `.\.venv\Scripts\vspm.exe pilot-dryrun --excel` → **DRY RUN PASSED**. With Excel installed,
      `--excel` also opens the written copy in Microsoft Excel and reads the new row back 🏭.

### B. DYMO printer  🖨️  (≈30 min)
- [ ] B1 Follow [DYMO_TEST_PROCEDURE.md](DYMO_TEST_PROCEDURE.md) steps 1–5: test labels from all four
      templates, printed from DYMO Label and from the app, compared with a production label, scanned,
      templates unchanged.
- [ ] B2 Note the working route: `dymo_web` ☐ / `dymo_com` ☐.

### C. Google Drive and AI  🔑  (≈30 min)
- [ ] C1 Service account + folder sharing ([GOOGLE_DRIVE.md](GOOGLE_DRIVE.md)); `.env`:
      `VSPM_DRIVE_MODE=google`, `VSPM_DRIVE_FOLDER_ID=…`.
- [ ] C2 `.\.venv\Scripts\vspm.exe drive-test` lists the **5 latest scans** with correct times and downloads
      the newest.
- [ ] C3 Read [AI_PROVIDER.md](AI_PROVIDER.md) (data handling); set `ANTHROPIC_API_KEY`,
      `VSPM_OCR_PROVIDER=anthropic`, `VSPM_OCR_UPLOAD_CONSENT=true`.
- [ ] C4 `.\.venv\Scripts\vspm.exe ocr-test <one real scan> --save data\ocr1.json` on **3–5 real documents**
      (including one with ditto marks and one with several rows). Compare every field with the paper; note
      errors and whether the wrong values were flagged ⚠.

### D. The pilot: one document → one label → one Excel row  🖨️🔑
Settings for the pilot (`.env`):

```
VSPM_PRINTER_MODE=dymo_web                    (or dymo_com, from B2)
VSPM_BARCODE_MODE=pilot_range                  (or registry, if deployed and reconciled - see BARCODES.md)
VSPM_PILOT_RANGES=VS:90000-90099,PG:90000-90099,PG0:90000-90099,EX:90000-90099   (agree the range first!)
VSPM_EXCEL_MODE=direct
VSPM_EXCEL_PATH=data/test_workbook/Σχισμενα.xlsx
VSPM_EXCEL_WRITE_ENABLED=true
VSPM_EXCEL_REQUIRE_TEST_COPY=true
```

- [ ] D1 Start the app (`scripts\start_vspm.bat`). Dashboard shows no red configuration problems.
- [ ] D2 **Πίνακας ελέγχου**: the 5 latest scans appear; open the chosen document.
- [ ] D3 **Ανάγνωση με AI** → rows appear. Compare each row with the scan; correct values; check ⚠ fields
      and the label type.
- [ ] D4 Approve **one** row (tick Εγκρ. + confirmation box), **Αποθήκευση**.
- [ ] D5 **Ετικέτα & εκτύπωση**: select that row; check the preview; **Εκτύπωση & Αποθήκευση** once.
- [ ] D6 Label comes out → **Η ετικέτα βγήκε σωστά**. Compare with a production label; scan the barcode.
- [ ] D7 Press **Έλεγχος ετικέτας και Excel** (or `vspm verify-label <BARCODE>`): every line OK.
- [ ] D8 Open `data\test_workbook\Σχισμενα.xlsx` in Excel: exactly one new row, values as on the label,
      L–O formulas (Excel may ask to update links: you may choose *Don't update*), date in column P as
      `DD/MM/YYYY`, row visible, formatting like the row above, no repair message.
- [ ] D9 **Επανεκτύπωση** once: same barcode, the workbook still has one row for it.
- [ ] D10 The pilot label is **not** a production record (it is only in the test workbook): do not ship it,
      or enter it in the live workbook manually.

### E. Barcode registry for production  🏭
- [ ] E1 Registry deployed with PostgreSQL behind HTTPS; one API key per PC ([BARCODES.md](BARCODES.md)).
- [ ] E2 Reconcile counters: stop printing, find the highest printed number per series (physical labels,
      every PC's templates, workbook), `vspm registry set-sequence …`, then
      `vspm registry observe-workbook --workbook <copy>`.
- [ ] E3 Every manual DYMO user reserves blocks on `/manual` before printing.
- [ ] E4 Two PCs print at the same time → different numbers; registry stopped → app refuses to print.

### F. OneDrive workbook for production  🏭
- [ ] F1 Choose the deployment option ([EXCEL_ONEDRIVE.md](EXCEL_ONEDRIVE.md) — recommended: one writer,
      nobody edits `΄Ετοιμα προϊόντα` while it writes; or staging + scheduled apply).
- [ ] F2 Live-environment checks with a `Σχισμενα_TEST.xlsx` in the OneDrive folder: closed everywhere /
      open in Excel Online by a colleague / OneDrive paused. Record conflict-copy behaviour.
- [ ] F3 Only then: `VSPM_EXCEL_PATH=<live workbook>`, `VSPM_EXCEL_REQUIRE_TEST_COPY=false`, first live
      writes at a quiet time, checked in Excel Online.

---

## Part 3 — known limitations at the start of the pilot

* No physical label has been printed by the app; DYMO Web Service and COM routes are written to DYMO's
  documented interfaces but untested on hardware (B).
* AI accuracy on your handwriting is unknown; no real scans were available (C4).
* Google Drive authentication is untested (C1–C2).
* The workbook was never opened by Microsoft Excel during development (only openpyxl and LibreOffice);
  `pilot-dryrun --excel` and D8 cover this.
* LibreOffice cannot evaluate formula L (`[1]!Table4[...]`) for **any** row, old or new; in your workbook
  Excel itself has cached `-` in column L for **all 328 existing rows** — worth checking in Excel whether the
  ΠΟΙΟΤΗΤΑ lookup works at all (pre-existing, not caused by the app).
* Concurrent OneDrive editing behaviour is untested (F2).
