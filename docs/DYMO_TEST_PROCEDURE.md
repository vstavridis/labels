# DYMO LabelWriter Wireless — integration test with the original templates

Goal: prove on the real Windows PC that VS Production Manager prints labels that look exactly like your DYMO
Label v8 labels, with the intended barcode, **without changing the production counters** stored in your
original `.label` files.

Time needed: about 30 minutes. You need: the Windows PC with DYMO Label v8, the LabelWriter Wireless on USB,
a roll of 30323 labels, your barcode scanner, and one label printed earlier in normal production (for
comparison).

## Why the counters are safe

* The app **reads** the original templates and never writes them. Every check below prints a SHA-256
  fingerprint of each template before and after, so you can see they are byte-for-byte unchanged.
* The app prints a **filled copy** in which the barcode is unlinked from DYMO's `COUNTER` object, so DYMO
  has no counter to advance.
* The test labels carry the barcode **`TEST0001`** — no production number is used.

**Never press Print in DYMO Label while one of the ORIGINAL template files is open.** DYMO advances the COUNTER
and saves it into the file, which is shared through OneDrive — that changes the production counter for every
computer. In this procedure DYMO Label only opens the *test* files the app creates in `data\dymo_test\`.

## Step 0 — preparation

1. Install the app ([INSTALL.md](INSTALL.md)) in a **local** folder such as `C:\VSPM` (not inside OneDrive).
2. Copy the four templates into `C:\VSPM\reference_files\` (a copy, not the shared OneDrive originals —
   then even an accident cannot touch the production counters). Any file names are fine: the app
   recognises each template by its counter prefix. If two files have the same prefix, the commands below
   tell you to choose one with `VSPM_TEMPLATE_FILES`.
3. In `.env`: `VSPM_TEMPLATE_DIR=reference_files`, `VSPM_PRINTER_NAME=DYMO LabelWriter Wireless`.
4. Write down each template's counter value (step 1 prints them) so you can compare later.

## Step 1 — generate the test labels (no printing yet)

```powershell
.\.venv\Scripts\vspm.exe dymo-test
```

Expected output, one line per template:

```
VS   template counter=11546  file=...\data\dymo_test\TEST_VS.label  not printed  template unchanged: YES
PG   template counter=7029   ...
PG0  template counter=2278   ...
EX   template counter=3416   ...
```

## Step 2 — DYMO Label v8 opens and prints the app's file

1. In DYMO Label v8: *File → Open* → `C:\VSPM\data\dymo_test\TEST_VS.label`.
2. Check on screen: same layout, logos, fonts and bold parts as your normal VS label; text shows ΔΟΚΙΜΗ /
   TEST; barcode text `TEST0001`.
3. Print **one** label from DYMO Label. Close the file (answer *No* if asked to save).
4. Repeat for `TEST_PG`, `TEST_PG0`, `TEST_EX`.

Result: the app's files are valid DYMO files and your printer prints them. ☐

## Step 3 — the app prints directly (DYMO Web Service, recommended)

1. Check the service: open <https://127.0.0.1:41951/DYMO/DLS/Printing/StatusConnected> in a browser
   (accept the certificate warning) → must show `true`. If the port differs (41951–41960), set
   `VSPM_DYMO_WEB_URL`.
2. In `.env`: `VSPM_PRINTER_MODE=dymo_web`.
3. Run:

   ```powershell
   .\.venv\Scripts\vspm.exe check
   .\.venv\Scripts\vspm.exe dymo-test --print
   ```

   `check` must list `DYMO LabelWriter Wireless` as connected; `dymo-test --print` prints four TEST labels
   and must end every line with `sent to printer  template unchanged: YES`.
4. Compare each printed label with (a) the label from step 2 and (b) a label printed earlier in production:
   position of every text block, font sizes, bold labels (ΠΑΧΟΣ, ΔΙΑΣΤΑΣΕΙΣ…), logos, the ΚΙΛΑ box, date/time
   line, barcode width and the text under the barcode. ☐

If step 3 fails (service missing or errors), use the legacy route: `pip install -e .[windows]`,
`VSPM_PRINTER_MODE=dymo_com`, and repeat step 3. Note which route works. ☐

## Step 4 — barcode scanner

Scan each TEST label. The scanner must return exactly `TEST0001` (Code 39, no checksum). ☐

## Step 5 — counters unchanged

1. Run `vspm dymo-test` again: the counters must be the same as in step 1 and every line must say
   `template unchanged: YES`.
2. Optional: compare the fingerprints of the shared OneDrive originals before and after the whole test:

   ```powershell
   Get-FileHash "C:\Users\<you>\OneDrive\...\*.label" -Algorithm SHA256
   ```

   (They are not used by the app at all if step 0.2 was followed.) ☐

## Step 6 — one production-style label through Print & Save (pilot)

Only after steps 1–5 pass. This prints a label with a real allocated number, so decide first which number
range the pilot uses ([BARCODES.md](BARCODES.md)): the registry (after reconciliation) or a pilot range
agreed with everyone, e.g. `VSPM_BARCODE_MODE=pilot_range` and `VSPM_PILOT_RANGES=VS:90000-90099,...`.
Follow [PILOT_CHECKLIST.md](PILOT_CHECKLIST.md) section D. After printing:

```powershell
.\.venv\Scripts\vspm.exe verify-label VS90000
```

All checks must show `OK`: template file unchanged, layout preserved, content as approved, barcode is the
reserved number, exactly one Excel record, values, formulas, print date. ☐

## Record

| Step | Date | Result | Notes (route used, differences seen) |
|---|---|---|---|
| 2 DYMO opens/prints app file | | | |
| 3 App prints (dymo_web / dymo_com) | | | |
| 4 Scanner | | | |
| 5 Counters unchanged | | | |
| 6 Pilot label verified | | | |
