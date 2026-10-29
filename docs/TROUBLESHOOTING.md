# Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Black window closes immediately | Not installed: run `scripts\setup_windows.ps1`. Or Python missing from PATH. |
| Browser shows "This site can't be reached" | The black window was closed; start `start_vspm.bat` again. Port 8501 busy → change `--server.port` in the .bat file. |
| Dashboard: "Configuration needing attention" | Read the message; each names the setting to fix. `vspm check` gives more detail. |
| "Barcode allocation is disabled" | `VSPM_BARCODE_MODE=disabled` (default). Configure the registry or a pilot range ([BARCODES.md](BARCODES.md)). |
| "Barcode registry unreachable" | No internet, registry down, or wrong `VSPM_REGISTRY_URL`. The app will not print without it — by design. |
| "Barcode registry rejected the API key" | Wrong/revoked `VSPM_REGISTRY_API_KEY`. |
| "Template file for VS not found" | Put the `.label` files in `VSPM_TEMPLATE_DIR` with the names in `config/templates.yaml`. |
| "text object has N styled runs but templates.yaml defines M" | Someone edited the DYMO template. Review `config/templates.yaml` `text_runs` for that template (see FINDINGS), then reload. |
| "No established English name for material" | EX label with a Greek material that has no translation: type the English name on the row or add `en:` in `config/materials.yaml`. |
| DYMO web service not reachable | Start DYMO Label / the DYMO Web Service; check <https://127.0.0.1:41951/DYMO/DLS/Printing/StatusConnected>; try ports 41951–41960; or use `dymo_com`. |
| Printed label looks different from preview | The app's preview is approximate unless `dymo_web` is used. Trust the physical test print; report differences. |
| Label did not come out | Press **Η ετικέτα ΔΕΝ βγήκε**, fix the printer, press **Επανεκτύπωση** (same barcode). |
| "The workbook appears to be open in Excel on this PC" | Close Excel, then **Επανάληψη Excel**. The label is safe. |
| "online-only OneDrive file" | Right-click the workbook → *Always keep on this device*. |
| "OneDrive conflict copies exist" | Another version was created by OneDrive (`Σχισμενα-<PC>.xlsx`). Merge it manually in Excel, delete/rename the copy, then retry. |
| "The workbook changed while the row was being prepared" | Someone saved meanwhile. Just retry. |
| Excel shows `-` in L/M/O | The external workbook `ΑΠΟΘΗΚΗ.xlsx` link is not updated or the roll/customer is not in the lookup sheet. Not an app error. |
| Excel N column shows `-` | Short customer name in F does not exactly match the `ID` sheet. |
| "Sending documents to the AI service is not enabled" | Set `VSPM_OCR_UPLOAD_CONSENT=true` after reading [AI_PROVIDER.md](AI_PROVIDER.md). |
| AI error 401 / rate limit | Check `ANTHROPIC_API_KEY` / wait a minute. |
| App was closed during printing | On restart the dashboard lists interrupted labels; continue with **Εκτύπωση & Αποθήκευση** (same number). |

Logs: `data\logs\vspm.jsonl` (also on **Ρυθμίσεις → Αρχείο καταγραφής**), full audit trail in the same page.
Printed label files: `data\labels\`. Excel backups: `data\excel_backups\`.
