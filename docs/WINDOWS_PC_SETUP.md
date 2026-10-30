# What to install on each Windows PC (hosted app)

With the app on Streamlit Community Cloud the PCs need **no Python and no VS Production Manager
installation**. Each of the two PCs needs only:

| # | What | Why | Check |
|---|---|---|---|
| 1 | **DYMO Label v8** (latest 8.7.x from dymo.com) — already installed | Printer driver, **DYMO Label Web Service** (local HTTPS service the browser prints through), and DYMO Label for manual printing / the .label fallback | Tray icon *DYMO Label Web Service* running |
| 2 | **DYMO LabelWriter Wireless connected by USB** | The printer | Prints a label from DYMO Label |
| 3 | **Google Chrome or Microsoft Edge**, current version | Runs the app and the DYMO bridge | `chrome://version` / `edge://version` |
| 4 | The app's address as a bookmark: `https://<your-app>.streamlit.app` | — | — |

Nothing else: no browser extensions, no special browser flags, no changes to security settings.

## One-time setup per PC (≈10 minutes)

1. **DYMO Web Service.** In the browser open
   <https://127.0.0.1:41951/DYMO/DLS/Printing/StatusConnected> — it must show `true`.
   * Page cannot be reached → start *DYMO Label Web Service* (Start menu, or restart the PC);
     if it still fails, reinstall the latest DYMO Label 8.7 and choose to install the web service.
   * Certificate warning → the DYMO localhost certificate is not trusted: reinstall DYMO Label
     (it installs the certificate) rather than clicking through the warning.
   * The service may use the next free port (41952…41960); the app tries them all.
2. **Sign in.** Open the app and sign in with your work account (only e-mails on the employee
   list are accepted).
3. **Allow local network access.** Open **🖨️ DYMO printer**. The browser asks whether the site may
   *look for and connect to devices on your local network* → **Allow**. (Blocked by mistake:
   click the icon left of the address → Site settings → *Local network access* → Allow → reload.)
4. **Check the diagnostics** on the same page: *DYMO service ✅*, *Printers found ≥ 1*, the
   LabelWriter selected, *Top frame: OK*. Give this PC a name (e.g. ΓΡΑΦΕΙΟ, ΑΠΟΘΗΚΗ).
5. **TEST0001.** Choose a template, press **Print TEST0001**. When the label comes out:
   * compare it with a label printed normally from DYMO Label (layout, fonts, sizes, barcode);
   * scan it: the scanner must read `TEST0001`;
   * press **Yes — correct label** (or *No* if anything differs, and report it).
   Repeat for VS, PG, PG0 and EX. Only after a confirmed TEST0001 can this PC print
   production labels. If you change the printer or the browser profile, repeat this step.

## Every day

* Use the same browser profile on the PC (the confirmation and printer choice are stored there).
* Labels printed by the app take their number from the shared database. **Do not print app labels
  from DYMO Label with its counter.** Anyone who still prints manually in DYMO Label must use a
  block reserved in *Settings → Barcode series* (or report the numbers they printed there), so
  the same number is never issued twice.
* If browser printing does not work: on the print page open **Download .label**, open the file in
  DYMO Label, print, then confirm in the app. The file already contains the reserved barcode.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| *DYMO service: not found* | Step 1; then **Re-check** on the printer page |
| *The browser blocks access to the DYMO service* | Step 3 (permission) |
| *Embedded by another site* | Open the app's own address, not a page that embeds it |
| *Printers found: 0* | Printer off / USB cable / driver: print once from DYMO Label |
| *failed … Error: 500* | DYMO refused the job (out of labels, printer busy/offline). Fix the printer, press Print again — the same barcode is reused |
| Label came out wrong | Press *No* (or *came out wrong*), then **Reprint** (same barcode) |
