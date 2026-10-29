# DYMO printing

## How labels are produced

1. The original template (`reference_files\…label`) is read; it is never modified.
2. An in-memory copy is filled: each styled run of the `TEXT` object (fonts kept), the `Quantity` weight,
   the `BARCODE` text set to the allocated number and **unlinked from the `COUNTER` object**, and the tiny
   counter object set to the same number. The date/time object is left to DYMO (it prints the PC's
   current date and time).
3. The filled label is saved as `data\labels\<BARCODE>.label` (you can open it in DYMO Label to inspect it)
   and sent to the printer by the configured adapter.

Because the app prints a temporary filled copy, printing from the app never advances the counter stored in
the shared OneDrive template files.

## Adapters (`VSPM_PRINTER_MODE`)

| Mode | How | Requirements |
|---|---|---|
| `mock` | writes `.label` + PNG to `data\mock_printer\` | none (default) |
| `dymo_web` (recommended) | HTTPS calls to the local **DYMO Label Web Service** (`PrintLabel`, `RenderLabel`, `GetPrinters`) with the complete label XML | DYMO Label 8.5+ with the web service running |
| `dymo_com` | legacy DLS SDK COM object `Dymo.DymoAddIn` via pywin32: open temp file, select printer, print | DYMO Label v8 with SDK COM registered, `pip install pywin32` |

`dymo_web` also returns DYMO's own rendered preview, used on the print page instead of the app's approximate
preview.

### Enabling the DYMO Label Web Service

* DYMO Label 8.5–8.7 installs *DYMO Label Web Service* (tray icon "DYMO Web Service" / Windows service).
  Open <https://127.0.0.1:41951/DYMO/DLS/Printing/StatusConnected> in a browser: it should show `true`.
  If the port differs (41951–41960), set `VSPM_DYMO_WEB_URL`.
* The service uses a self-signed certificate on localhost, hence `VSPM_DYMO_WEB_VERIFY_TLS=false`
  (connections are to 127.0.0.1 only).
* If your DYMO Label v8 version has no web service, update to the latest DYMO Label 8.7 or use `dymo_com`.

### Printer name

`VSPM_PRINTER_NAME` must match the name shown by `vspm check` (e.g. `DYMO LabelWriter Wireless`). The
LabelWriter Wireless connected by USB appears as a normal Windows printer.

## Hardware test procedure (not yet performed)

**No physical label has been printed during development.** On the Windows PC:

1. `VSPM_PRINTER_MODE=dymo_web` (or `dymo_com`), run `vspm check` → printer listed and connected.
2. `vspm test-print --template VS` → prints a label marked TEST/ΔΟΚΙΜΗ without allocating a number or touching
   Excel. Repeat for `PG`, `PG0`, `EX`.
3. Compare each with a label printed manually from DYMO Label: layout, fonts, bold parts, logos, date/time.
4. Scan the barcode with your barcode scanner; it must read exactly the barcode text (e.g. `TEST0001`).
5. Open `data\test_print.label` in DYMO Label v8 to confirm the file opens and the barcode is not linked to
   the counter.
6. Record results in the integration checklist. If the web route fails, try `dymo_com`.

## Known limitations

* Windows only accepts the job; the app cannot detect paper-out/jams after that. The operator confirms each
  label ("printed correctly" / "did not print"). Failed labels are reprinted with the same barcode.
* The preview renderer is an approximation (fonts are substituted on non-Windows systems).
