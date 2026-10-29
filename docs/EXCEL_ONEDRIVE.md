# Excel workbook on OneDrive

## What gets written

One row per newly printed label, in the first empty row after the last data row of `΄Ετοιμα προϊόντα`:

| Col | Value | Source |
|---|---|---|
| A | material | Greek canonical material (default; see FINDINGS §4.2), or label text with `VSPM_EXCEL_MATERIAL_SOURCE=label` |
| B | thickness | text `0.60` (dot, 2 decimals — workbook convention) |
| C | dimensions | as printed on the label |
| D | pieces | number, or text such as `2+1` |
| E | kilograms | number |
| F | short customer name | as printed on the label |
| G | `VS` / `PG` / `EX` | template category; PG0 → `PG` |
| H | full generated barcode | e.g. `PG02279` |
| I | location | from the document |
| J | — | **never written** |
| K | roll | ΑΡ. ΡΟΛΟΥ |
| L–O | formulas | copied from the nearest formula row above, row numbers shifted (`K350`, `F350` …) |
| P | print date | real Excel date, format `dd/mm/yyyy`, date of the first successful print in `Europe/Athens` |

Formatting is copied from the row above; the new row is made visible. Formulas are not calculated by this
app: Excel calculates them when the workbook is opened (`fullCalcOnLoad` is set), and L/M/O need access to the
external workbook `ΑΠΟΘΗΚΗ.xlsx`. The app never invents lookup results.

## Why not Microsoft Graph or openpyxl

* Microsoft Graph's Excel workbook API does not support files in personal (consumer) OneDrive, so it is not used.
* openpyxl would drop form controls/drawings in this workbook and its formula translator corrupts formula L.
  The app edits only the worksheet XML, `styles.xml` and `workbook.xml` inside the `.xlsx`; all other parts are
  copied unchanged.

## Safety measures

1. **Write switch**: nothing is written unless `VSPM_EXCEL_WRITE_ENABLED=true`.
2. **Pre-checks**: file exists and is fully downloaded (online-only placeholder detection: Windows); not open
   in Excel on this computer (`~$` owner file on Windows/macOS, plus an exclusive-open test on Windows); no OneDrive conflict copies (`Σχισμενα-<PC>.xlsx`).
3. **Idempotency**: the barcode (column H) is the key. If it is already present the row is not written again.
4. **Optimistic lock**: the file's SHA-256 is checked again immediately before replacing it; any change → abort.
5. **Backup** of the previous version in `data/excel_backups/` (last 200 kept).
6. **Atomic replace** (temp file + rename, like Excel itself), then **read-back verification** of every
   written cell and that the barcode appears exactly once.
7. Every attempt is recorded (`excel_attempts`, audit log) with hashes and backup path.

These measures **cannot** see edits that another employee made in Excel Online or on another PC that OneDrive
has not yet synchronised to this PC. If such an edit and the app's write overlap, OneDrive may keep both
versions as a conflict copy (which the app then detects and reports) or co-authoring may merge them. **This
has not been tested.**

## Modes (`VSPM_EXCEL_MODE`)

| Mode | Use when | Behaviour |
|---|---|---|
| `staging` (default) | until the checklist below is complete, or when people often have the workbook open | rows wait in the app; **Ιστορικό** offers *Εγγραφή στο Excel τώρα* (direct write when the workbook is closed), *download a sheet for manual entry*, and *Reconcile* (reads the workbook and marks rows that are present) |
| `direct` | one PC prints labels, or each PC writes only when nobody else edits | each Print & Save writes immediately with all checks; failures stay pending and can be retried |
| `registry_queue` | several PCs print labels, or the app runs on Linux/a server without OneDrive | PCs send rows to the barcode registry's queue; **one designated computer with OneDrive** (Windows or macOS) runs `vspm excel-worker` (`scripts\start_excel_worker.bat` on Windows) and is the only writer, so writes are serialised |

Recommended: start with `staging` on the test copy → `direct` on the test copy → `direct` (single PC) or
`registry_queue` (several PCs) on the live workbook during quiet hours, after the checklist.

## Test-copy validation (do this first)

```bash
.venv/bin/vspm make-test-workbook       # data/test_workbook/Σχισμενα.xlsx
```

Set `VSPM_EXCEL_PATH=data/test_workbook/Σχισμενα.xlsx`, `VSPM_EXCEL_MODE=direct`,
`VSPM_EXCEL_WRITE_ENABLED=true`, use mock barcodes, print a few rows, then open the test copy **in Excel**:
check the new rows, formats, the date, formulas L–O (Excel may ask to update links), form controls and that no
repair message appears.
