# Integration checklist (go-live)

Work top to bottom. Record date, who, and result for each line. Do not skip to the live workbook.

## 0. Base installation
- [ ] `scripts\setup_windows.ps1` completed, all tests passed on the Windows PC
- [ ] `vspm report` run on the current reference files; [FINDINGS.md](FINDINGS.md) §4 decisions taken
      (EX material language, customer rules, short names, dimension notation)
- [ ] App starts with `start_vspm.bat` in demo mode; full mock workflow done once

## 1. DYMO printer ([DYMO.md](DYMO.md))
- [ ] `VSPM_PRINTER_MODE=dymo_web`; `vspm check` lists `DYMO LabelWriter Wireless` as connected
- [ ] `vspm test-print --template VS|PG|PG0|EX` — each physical label matches a manual DYMO label
- [ ] Barcode scanner reads the printed barcode text exactly
- [ ] `data\test_print.label` opens in DYMO Label v8; barcode not linked to the counter
- [ ] Shared OneDrive templates' counters unchanged after app printing
- [ ] (if needed) `dymo_com` tried as fallback

## 2. Barcode registry ([BARCODES.md](BARCODES.md))
- [ ] Registry deployed with PostgreSQL, HTTPS, backups; `/health` reachable from every PC
- [ ] One API key per PC; admin key stored safely
- [ ] Counters reconciled (physical labels, every PC's templates, workbook) and sequences initialised
- [ ] Every manual DYMO user knows the `/manual` block-reservation page
- [ ] Two PCs pressing Print & Save at the same time receive different numbers
- [ ] Registry offline → app refuses to print (tested by stopping the service)
- (alternative) [ ] pilot ranges agreed and configured, manual counters kept below them

## 3. Google Drive ([GOOGLE_DRIVE.md](GOOGLE_DRIVE.md))
- [ ] Service account created, folder shared as Viewer, JSON key in `credentials\`
- [ ] Dashboard shows the latest 5 scans with previews and correct upload times
- [ ] Re-uploading/changing a scan shows it as a new revision

## 4. AI extraction ([AI_PROVIDER.md](AI_PROVIDER.md))
- [ ] Data handling reviewed; `VSPM_OCR_UPLOAD_CONSENT=true`; API key with spend limit
- [ ] 20–30 real documents read; for each, rows/fields compared with manual reading
- [ ] Error rate per field recorded; uncertain values are being flagged where the AI was wrong
- [ ] Documents with ditto marks, multiple pages and abbreviations tested

## 5. Excel ([EXCEL_ONEDRIVE.md](EXCEL_ONEDRIVE.md))
- [ ] `make-test-workbook`; `direct` mode on the **test copy**; 5+ labels printed (mock barcodes)
- [ ] Test copy opened in **Excel desktop**: no repair prompt, rows/format/date `dd/mm/yyyy` correct,
      formulas L–O reference the right row and calculate after updating links, form controls still work
- [ ] Reprint and Retry Excel do not add rows; closing/opening Excel during a write gives a clear error
- [ ] Decide the mode: `direct` (single printing PC) or `registry_queue` with one designated Excel PC
- [ ] Live workbook: right-click → *Always keep on this device*; `VSPM_EXCEL_PATH` set to the OneDrive path
- [ ] First live writes done at a quiet time with nobody else editing; verified in Excel Online and desktop
- [ ] Concurrent-edit behaviour observed (another user editing in Excel Online while the app writes):
      result documented; if conflicts appear, stay with `staging` + reconciliation

## 6. Go-live
- [ ] Mock/test settings removed (`VSPM_BARCODE_MODE` ≠ mock, sidebar shows no TEST warning)
- [ ] Operators trained on approve → print → confirm → reprint, and on the Ιστορικό page
- [ ] Daily: dashboard shows 0 Excel errors / unconfirmed prints at end of day
