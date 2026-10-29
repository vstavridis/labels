# DYMO printing

## How labels are produced

1. The original template (`reference_files/…label`) is read; it is never modified.
2. An in-memory copy is filled: each styled run of the `TEXT` object (fonts kept), the `Quantity` weight,
   the `BARCODE` text set to the allocated number and **unlinked from the `COUNTER` object**, and the tiny
   counter object set to the same number. The date/time object is left to DYMO (it prints the PC's
   current date and time).
3. The filled label is saved as `data/labels/<BARCODE>.label` (you can open it in DYMO Label to inspect it)
   and sent to the printer by the configured adapter.

Because the app prints a temporary filled copy, printing from the app never advances the counter stored in
the shared OneDrive template files.

## Adapters (`VSPM_PRINTER_MODE`)

| Mode | OS | How | Requirements |
|---|---|---|---|
| `mock` | any | writes `.label` + PNG to `data/mock_printer/` | none (default) |
| `dymo_web` (recommended) | Windows, macOS | HTTPS calls to the local **DYMO Web Service** (`PrintLabel`, `RenderLabel`, `GetPrinters`) with the complete label XML; DYMO's own engine renders the label | DYMO Label 8.5+ or DYMO Connect with the web service running |
| `cups` | macOS, Linux | the app renders the label (300 dpi PDF, 54 × 101 mm, rotated to the feed direction) and prints it with `lp` | DYMO's CUPS driver for the LabelWriter (macOS: installed with DYMO software; Linux: `printer-driver-dymo` / DYMO's LabelWriter CUPS driver), printer added in CUPS |
| `dymo_com` | Windows only (optional) | legacy DLS SDK COM object `Dymo.DymoAddIn` via pywin32 | DYMO Label v8 with SDK COM registered, `pip install -e .[windows]` |

`dymo_web` gives the most faithful output because DYMO renders the template itself. `cups` uses this app's
renderer (fonts substituted if Arial/Impact are missing), so check it carefully against a manual label.

`dymo_web` also returns DYMO's own rendered preview, used on the print page instead of the app's approximate
preview.

### Enabling the DYMO Label Web Service

* DYMO Label 8.5–8.7 (Windows) and DYMO Label / DYMO Connect for macOS install the *DYMO Web Service*.
  Open <https://127.0.0.1:41951/DYMO/DLS/Printing/StatusConnected> in a browser: it should show `true`.
  If the port differs (41951–41960), set `VSPM_DYMO_WEB_URL`.
* The service uses a self-signed certificate on localhost, hence `VSPM_DYMO_WEB_VERIFY_TLS=false`
  (connections are to 127.0.0.1 only).
* If your DYMO Label v8 version has no web service, update to the latest DYMO Label 8.7 or use `dymo_com`.

### Printer name

`VSPM_PRINTER_NAME` must match the name shown by `vspm check` (e.g. `DYMO LabelWriter Wireless` with
`dymo_web`, or the CUPS queue name such as `DYMO_LabelWriter_Wireless` with `cups` — see `lpstat -e`).

### CUPS (macOS / Linux)

```
VSPM_PRINTER_MODE=cups
VSPM_PRINTER_NAME=DYMO_LabelWriter_Wireless
VSPM_CUPS_OPTIONS=-o fit-to-page
VSPM_CUPS_ROTATE=true
```

If the label comes out sideways or scaled, change `VSPM_CUPS_ROTATE` or add the driver's media option for
30323 labels (list them with `lpoptions -p <queue> -l`, e.g. `-o PageSize=w154h288`).
When the app runs on a central server, the CUPS queue on that server prints to the LabelWriter (USB on the
server, or a network/shared queue).

## Hardware test procedure (not yet performed)

**No physical label has been printed by the app during development.** Follow
[DYMO_TEST_PROCEDURE.md](DYMO_TEST_PROCEDURE.md): it prints TEST labels from your original templates without
touching their counters (`vspm dymo-test --print`), checks them in DYMO Label v8, with the scanner and against a
production label, and proves with SHA-256 fingerprints that the templates are unchanged.

## Known limitations

* The operating system only accepts the job; the app cannot detect paper-out/jams after that. The operator confirms each
  label ("printed correctly" / "did not print"). Failed labels are reprinted with the same barcode.
* The app's own preview/CUPS renderer is an approximation of DYMO's rendering (fonts are substituted when Arial/Impact are not installed).
