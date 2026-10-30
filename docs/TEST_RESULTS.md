# Test results

## Hosted app with browser printing — 30/10/2026 (branch `claude/browser-dymo-printing`)

CI run: <https://github.com/vstavridis/labels/actions/runs/40985867812> — all 8 jobs green.

| Job | Result |
|---|---|
| tests on Ubuntu, macOS, Windows | passed |
| whole suite on PostgreSQL 16 (app database, registry, two app instances on one database) | passed |
| browser printing — bundled Chromium 156, Linux | 29 passed |
| browser printing — **Google Chrome 156**, Windows | 29 passed |
| browser printing — **Microsoft Edge 155**, Windows | 29 passed |
| Windows installation and launch | passed |

Locally (Linux): 176 passed on SQLite, 183 on PostgreSQL 16, 30 browser/print tests.

Defects found while bringing CI to green (all fixed, with regression tests where possible):

* two tabs of one browser could submit the same print job twice (the bridge checked the job state before
  taking its lock, not inside it);
* a button click could be swallowed by a rerun triggered by the bridge's report (now handled in callbacks);
* two simultaneous first reports from one PC crashed the page (`UNIQUE constraint failed: print_stations`);
* on Windows checkouts Git changed the line endings of the vendored DYMO framework, so its checksum failed;
* test harness: the test server's log pipe filled up on Windows and the server stopped answering.

The browser tests had intermittent timing failures on the CI runners before these fixes; the green run above
is the first one with all of them in place. Nothing here involved a real DYMO printer, a real DYMO Web
Service, Streamlit Community Cloud or a real login provider — see Part 4 of PILOT_CHECKLIST.md.

---

## Local pilot installation — 29/10/2026 (commit `5fe59f4` and later)

## 1. Automated test suite

| Environment | Collected | Passed | Skipped | Failed | Notes |
|---|---|---|---|---|---|
| Linux, development machine, with the confidential reference files and LibreOffice | 148 | 142 | 6 | 0 | skipped: 5 PostgreSQL tests (no `VSPM_TEST_POSTGRES_URL`), 1 Windows-only test |
| Linux, same, with a local PostgreSQL 16 | 5 | 5 | 0 | 0 | `tests/test_registry_postgres.py` |
| **Windows Server 2022** (GitHub Actions, Python 3.12.10) | 148 | **139** | 9 | 0 | the Windows-only file-lock test **ran and passed**; skipped: 4 tests needing the confidential reference files / LibreOffice, 5 PostgreSQL tests (separate job) |
| macOS (GitHub Actions) | 148 | passed | skipped as above | 0 | |
| Ubuntu (GitHub Actions) | 148 | passed | skipped as above | 0 | |
| PostgreSQL 16 service (GitHub Actions) | 22 | **22** | 0 | 0 | registry tests incl. two server processes |

CI run: <https://github.com/vstavridis/labels/actions/runs/40965328262> — all 5 jobs green.

**Before this round**, the Windows test job had **failed** in both earlier CI runs
(`test_other_package_parts_preserved_byte_for_byte`: `PermissionError [WinError 5]` when replacing the
workbook). Cause: Windows refuses to rename over a file that any program holds open. The test held the file
open; in production the same happens with Excel, OneDrive or antivirus. Fixed in the writer (retries, safe
re-check, temp-file cleanup) and in the test; a new test reproduces the real Windows lock.

## 2. Windows installation (GitHub Actions, clean Windows Server 2022)

`scripts\setup_windows.ps1` → venv, install, `.env`, `config\customers.yaml`, database, full test run → *Done*.
Then:

```
vspm check            Printer (mock): OK · Documents (mock): 5 found · Barcodes (disabled) · Excel: workbook not found (expected)
vspm dymo-test --print
  VS   template counter=11546  ...TEST_VS.label   sent to printer  template unchanged: YES
  PG   template counter=7029   ...TEST_PG.label   sent to printer  template unchanged: YES
  PG0  template counter=2278   ...TEST_PG0.label  sent to printer  template unchanged: YES
  EX   template counter=3416   ...TEST_EX.label   sent to printer  template unchanged: YES
vspm pilot-dryrun --synthetic      -> DRY RUN PASSED (16 checks)
scripts\start_vspm.bat             -> health ok: True  page status: 200
```

(The CI job uses the sanitized template fixtures and the mock printer; a real LabelWriter is not available there.)

## 3. Print & Save on a copy of the real workbook

`vspm pilot-dryrun --workbook reference_files/Σχισμενα.xlsx` with the **real** templates (mock printer, test
barcodes, the workbook is copied first):

```
[OK  ] Print & Save row 1 -> VS990000: print 'print_unconfirmed', Excel 'synchronized'
[OK  ] template file unchanged: identical to the file used for printing
[OK  ] label layout preserved: objects, positions, fonts and images identical to the template; only text, weight and barcode filled
[OK  ] label content as approved: ΓΑΛΒΑΝΙΖΕ / 0,60 / 34ΧΡΟΛΟ / 4 τεμ / … / 496 kg
[OK  ] barcode is the reserved number: VS990000 (VS, Code 39, unlinked from COUNTER; reserved in mock as VS990000)
[OK  ] exactly one Excel record: row(s) [330] in dryrun.xlsx
[OK  ] Excel values A–K match the label
[OK  ] Excel formulas L–O: same pattern as the rows above, row 330; external link [1] kept
[OK  ] Excel print date: P330 = 2026-10-30 (real date, format 'dd\/mm\/yyyy')
[OK  ] reprint uses the same barcode VS990000
[OK  ] reprint did not change the workbook
[OK  ] second Print & Save of the same row is refused
[OK  ] workbook copy grew by exactly one row (row 330)
[OK  ] VS990000 appears once
[OK  ] 4 original template file(s) unchanged (SHA-256)
[OK  ] source workbook Σχισμενα.xlsx unchanged (SHA-256)
DRY RUN PASSED
```

(The date is 30/10 because the run happened after 22:00 UTC, i.e. after midnight in Athens — the factory
time zone is applied as intended.)

## 4. Workbook formulas, links and formatting (copy of the real workbook)

* Only `xl/workbook.xml`, `xl/styles.xml` and the target sheet change; all other package parts (form controls,
  drawings, VML, external link, printer settings, calc chain, other sheets) are byte-identical.
* New row formulas have the same pattern as all 328 existing rows, with `[1]` external links intact.
* Recalculated independently with **LibreOffice**: a row written with an existing roll number and customer
  gets **M = KMC, N = 45, O = 760 — identical to Excel's cached values for the existing row**; across the
  sheet M/N/O match Excel's cache in **984 of 984** cells.
* Column L shows `#VALUE!` in LibreOffice for **every** row (old and new): LibreOffice cannot evaluate the
  external table reference `[1]!Table4[...]`. In the workbook as saved by Excel, column L is `-` for all 328
  rows — the ΠΟΙΟΤΗΤΑ lookup apparently never finds a value today (pre-existing; please check in Excel).
* Microsoft Excel itself was **not** available: `vspm pilot-dryrun --excel` performs this check on the
  Windows PC (opens the copy in Excel via COM, reads values, formulas, date text, row visibility).

## 5. Barcode registry (PostgreSQL 16)

| Test | Result |
|---|---|
| 800 requests, 40 threads, 2 registry processes, 200 idempotency keys | 200 numbers, one per key, contiguous VS11712…VS11911, no duplicates |
| 120 single + 30 five-number manual blocks interleaved | 270 contiguous numbers, blocks contiguous, no overlap |
| 20 identical block requests at once | one block (EX3417–EX3419) |
| manual numbers reported while the app allocates | reported numbers never issued; sequence ends past them |
| constraints | `(series, number)` and `barcode` unique |

## 6. Not tested (needs your environment)

Physical printing on the LabelWriter Wireless (DYMO Web Service / COM) · DYMO Label v8 opening the app's files ·
Google Drive authentication and real folder listing · AI extraction on real handwritten documents (no API
key, no scans) · Microsoft Excel opening the written workbook · OneDrive sync and concurrent editing ·
registry deployed on a server with HTTPS. See [PILOT_CHECKLIST.md](PILOT_CHECKLIST.md).

## How to reproduce

```bash
python -m pytest -q -rs                                             # whole suite
VSPM_TEST_POSTGRES_URL=postgresql+psycopg://user:pw@host/db python -m pytest tests/test_registry_postgres.py
vspm pilot-dryrun --workbook reference_files/Σχισμενα.xlsx          # add --excel on Windows with Excel
```
