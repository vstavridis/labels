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

## Risk assessment: several employees and one personal-OneDrive workbook

How the pieces behave (based on how OneDrive and Excel work; **none of this has been tested with your
accounts** — see the checklist at the end):

| Situation | What happens | Risk |
|---|---|---|
| Workbook open in **Excel desktop on the app PC** | Excel locks the file; the app refuses to write ("open in Excel on this PC") and keeps the row pending | none (safe refusal) |
| Workbook open in Excel desktop **on another PC** with AutoSave | Their edits go to the cloud; the app's PC receives them through OneDrive some seconds or minutes later. If the app writes before that sync arrives, OneDrive holds two different versions | **conflict copy** (`Σχισμενα-PCNAME.xlsx`) or, if Office merges, usually both changes kept. The app detects conflict copies and stops writing until they are resolved, but the other person's edit may sit in the copy |
| Someone edits in **Excel Online** while the app writes | Same as above: the cloud has a newer version than the app's PC | **conflict copy** possible |
| OneDrive **sync paused / offline / file online-only** | The app writes to the local copy (placeholder is refused); the upload happens later and can conflict with edits made meanwhile | conflict when sync resumes |
| **Two PCs running the app** in `direct` mode | Each writes its own local copy; OneDrive reconciles later | **conflict copies**; rows can end up only in a copy |
| Antivirus / OneDrive briefly holding the file | Rename fails; the app retries for a few seconds, re-checking the file before each try | none (retried or refused) |

What the app guarantees in every case: a label's barcode is written **at most once** to the workbook it sees
(idempotency by column H), nothing is written when the file changed while preparing the row, every write is
verified by reading the file back, a backup of the previous version is kept, and anything not written stays
**pending and visible** on the History page, with *Reconcile* to confirm it later.

What no local program can guarantee: that the local file is the newest cloud version at the moment of
writing. Microsoft's API for editing workbooks safely in the cloud does not support personal OneDrive.

### Deployment options, safest first

1. **One writer, nobody editing while it writes (recommended for the pilot and for production).**
   * Exactly one computer writes to the workbook: the only printing PC in `direct` mode, or a designated
     PC running `vspm excel-worker` with `registry_queue` when several PCs print.
   * During production hours other employees **view** `΄Ετοιμα προϊόντα` but do not edit it. Their other
     sheets/other workbooks are unaffected.
   * The writer PC keeps the file "Always keep on this device", and its OneDrive must show *Up to date*
     before the shift starts.
2. **Staging + scheduled apply.** The app keeps rows pending (`staging`) all day; at a fixed quiet time
   (e.g. 17:00) when everybody has closed the workbook, the operator presses *Write to Excel now* on the
   writer PC, then *Reconcile*. Safest when people must edit the workbook during the day.
3. **Separate app workbook (most robust, needs a workbook change).** The app writes to its own workbook that
   nobody edits (e.g. `VSPM_ετικέτες.xlsx`, same columns); `Σχισμενα.xlsx` pulls those rows with Power Query.
   This removes write conflicts completely but changes how the workbook is maintained — consider it if the
   pilot shows conflicts.
4. **Not recommended:** several PCs in `direct` mode, or writing while others edit the same sheet.

**For the pilot:** option 1 on a **test copy** (`data\test_workbook\`), enforced by
`VSPM_EXCEL_REQUIRE_TEST_COPY=true`. Go to the live workbook only after the checklist below, starting with
option 1 or 2.

### Live-environment checks (to do once, with the test copy placed in OneDrive)

* Put a copy named e.g. `Σχισμενα_TEST.xlsx` in the same OneDrive folder, point the app at it
  (`VSPM_EXCEL_REQUIRE_TEST_COPY=false` for this test only), write a row while the file is closed everywhere
  → check in Excel Online that the row arrived.
* Repeat while a colleague has the test copy open in Excel Online and types in another row → note whether
  OneDrive creates a conflict copy or merges, and whether the app reports it.
* Repeat with OneDrive paused for a minute.
Record the results in [PILOT_CHECKLIST.md](PILOT_CHECKLIST.md) section F before deciding the production mode.

## Test-copy validation (do this first)

```bash
.venv/bin/vspm make-test-workbook       # data/test_workbook/Σχισμενα.xlsx
```

Set `VSPM_EXCEL_PATH=data/test_workbook/Σχισμενα.xlsx`, `VSPM_EXCEL_MODE=direct`,
`VSPM_EXCEL_WRITE_ENABLED=true`, use mock barcodes, print a few rows, then open the test copy **in Excel**:
check the new rows, formats, the date, formulas L–O (Excel may ask to update links), form controls and that no
repair message appears.
