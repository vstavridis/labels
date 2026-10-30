# Browser-side DYMO printing (hosted app)

When the app runs on Streamlit Community Cloud, the server cannot reach the printers: they are
USB devices on the two Windows PCs. Labels are therefore sent **by the employee's browser** to
the **DYMO Web Service running on that same PC** (installed with DYMO Label v8), using DYMO's
official JavaScript framework (`dymo.connect.framework.js`, MIT licence, vendored unmodified in
`src/vspm/ui/dymo_bridge/static/vendor/`, SHA-256 checked at start-up).

```
 Streamlit Cloud (HTTPS)                         Windows PC
 ┌──────────────────────────────┐                ┌─────────────────────────────────────┐
 │ app + PostgreSQL             │  page + job    │ Chrome / Edge                       │
 │ 1. reserve barcode (DB)      │ ─────────────▶ │  DYMO bridge (Streamlit component)  │
 │ 2. save label row (DB)       │                │  3. check label XML (barcode, hash) │
 │ 3. prepare .label XML        │                │  4. printLabelAsync ──▶ https://127.0.0.1:41951
 │ 5. record result, Excel row  │ ◀───────────── │     (DYMO Web Service) ──▶ LabelWriter
 └──────────────────────────────┘  report        └─────────────────────────────────────┘
```

## What the bridge does

* `VSPM_PRINTER_MODE=browser` enables it. The **🖨️ DYMO printer** page shows this PC's diagnostics:
  framework loaded, DYMO service found, printers, selected printer, Local Network Access
  permission state, secure context, whether the app is framed by another origin.
* Printer choice is remembered per browser (`localStorage`); a single connected LabelWriter is
  selected automatically.
* Every station (= browser profile on a PC) must print **TEST0001** and the employee must confirm
  that the label came out correctly and scans as `TEST0001`, before that station + printer may
  print production labels (`VSPM_REQUIRE_VERIFIED_STATION=true`, default). TEST0001 allocates no
  barcode and writes nothing to Excel.

## Safety rules (all enforced in code and tested)

| Rule | How |
|---|---|
| The label prints the **reserved** barcode, never a DYMO counter value | Barcode object unlinked from the COUNTER; the counter is set to the reserved number (`VSPM_DYMO_COUNTER_MODE=sync`) or removed (`remove`). The server checks the prepared XML; the browser checks it **again** before submitting (all barcode texts = expected, no counter link, counter text = expected, Code 39 charset) and refuses otherwise. |
| Barcode persisted before printing | The label row with its barcode is committed in the database before the print job exists; `create_label_job` re-checks this. |
| Idempotent jobs | Each job has a key. The browser records `printing` in `localStorage` **before** calling DYMO; a reload during submission becomes *uncertain* and is never resubmitted automatically. A per-job Web Lock plus a re-check inside it stops two frames/tabs of the same browser from submitting the same job. |
| Two tabs / two PCs | A unique index allows one pending job per label; the second tab gets "already being printed". |
| Submission ≠ physical confirmation | DYMO only reports that the job was accepted (`submitted`). The label is marked *print unconfirmed* until the employee confirms; *came out wrong* → reprint. |
| Unanswered jobs | After 5 minutes without a report the job expires and the employee must say whether the label came out before anything else happens with that barcode. |
| Reprint | Same barcode, same template version (stored by SHA-256), never a new Excel row. |
| Content integrity | The browser reports the SHA-256 of the XML it sent; a different hash is recorded as *uncertain*, a different barcode as *rejected* — neither counts as printed. |
| Fallback | **Download .label**: the populated file with the reserved barcode (counter synchronised); the employee opens it in DYMO Label, prints, and confirms manually. |

## Investigation: HTTPS, CORS, Local Network Access, iframes

Tested here with Chrome for Testing **153** (Playwright 1.63), the real Streamlit app served over
HTTPS, and a test double of the DYMO Web Service on `https://127.0.0.1:41951`. Chrome was told
to treat the app's address as **public** (`--ip-address-space-overrides`, the switch Chrome's own
Local Network Access tests use), so it applied the same rules as to `*.streamlit.app`.

| Topic | Finding | Status |
|---|---|---|
| **HTTPS / mixed content** | The DYMO Web Service is HTTPS (`https://127.0.0.1:41951`), so an HTTPS page may call it. A plain-HTTP app page cannot (not a secure context). Streamlit Cloud is HTTPS. | tested (fake service) |
| **Local Network Access (Chrome 142+)** | A public HTTPS page calling `127.0.0.1` needs the user's permission: Chrome shows *"… wants to look for and connect to any device on your local network"*. **Allow** once per site; it is remembered. Without it the request is blocked and the bridge shows step-by-step guidance (site settings → Local network access → Allow). | tested in Chrome 153 (permission granted / not granted) |
| **Edge** | Same Chromium engine and permission; CI runs the same tests on the installed Edge (`browser-dymo` job, Windows). | CI |
| **CORS** | The framework calls the service cross-origin; DYMO's service is designed for this (it is how DYMO's own web SDK samples work). The fake service sends `Access-Control-Allow-Origin: *`. **The real service's headers were not observable here.** | ⚠ physical validation |
| **iframe** | Streamlit renders components in a same-origin iframe (sandbox `allow-same-origin allow-scripts …`). Same-origin frames inherit the page's permission; tested working. If the app is ever embedded in a page of **another** origin, the bridge reports "embedded by another site" — open the app's own address instead. Community Cloud serves the app from its own `*.streamlit.app` origin; the deployed framing must be confirmed on the printer page ("Top frame: OK"). | tested locally; ⚠ confirm on the deployed app |
| **Certificate** | The DYMO service uses a certificate for `localhost`/`127.0.0.1` installed by DYMO. If the browser does not trust it, the framework cannot connect; opening `https://127.0.0.1:41951/DYMO/DLS/Printing/StatusConnected` once shows the problem (should show `true`). | ⚠ physical validation |
| **Error detail** | On a print failure the framework only reports `Failed to execute webservice command: PrintLabel. Error: 500`; the reason (out of labels, offline) is visible on the printer / DYMO tray, not in the browser. The app records it as *failed* and never retries automatically. | tested (fake failure) |

**Default solution if the browser blocks access:** allow *Local network access* for the app's
address in the site settings (a per-site permission). Do **not** disable browser security
features or start the browser with special flags. Managed PCs can pre-approve exactly the app's
address with the Chrome/Edge policy `LocalNetworkAccessAllowedForUrls`. If printing from the
browser is still impossible, use **Download .label**.

## What needs physical validation on the two PCs

See [WINDOWS_PC_SETUP.md](WINDOWS_PC_SETUP.md) (per PC) and Part 4 of
[PILOT_CHECKLIST.md](PILOT_CHECKLIST.md). Nothing below has been tested with a real printer:

1. The real DYMO Web Service of DYMO Label v8 is found from the hosted app (CORS, certificate).
2. Chrome and Edge show the Local Network Access prompt and remember the permission.
3. GetPrinters lists the **LabelWriter Wireless connected by USB** as connected.
4. TEST0001 prints on each template (VS, PG, PG0, EX) with the exact design, fonts and barcode,
   and a scanner reads `TEST0001`; the DYMO template on disk is unchanged afterwards.
5. A production label prints the reserved barcode (scan it) — counter mode `sync` keeps the
   counter object showing the same number; `remove` deletes it.
6. Printer off / out of labels → the app shows *failed*, nothing is written to Excel, retry
   reuses the same barcode.
