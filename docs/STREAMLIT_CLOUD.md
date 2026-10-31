# Deploying on Streamlit Community Cloud

Target setup: one hosted app, used from the two Windows PCs (and any other authorised browser),
with all state in one persistent PostgreSQL database. Branch: **`main`**.
Step-by-step beginner guide: [DEPLOY_STREAMLIT.md](../DEPLOY_STREAMLIT.md).

```
 Browser (PC 1, PC 2) ──HTTPS──▶ Streamlit Community Cloud ──TLS──▶ PostgreSQL (Neon / Supabase / …)
        │                         app, login, label preparation       barcodes, labels, print jobs,
        └─▶ local DYMO Web Service                                    templates, history, audit log
```

## 1. PostgreSQL

Create a database at a managed provider (Neon, Supabase, Aiven, …), **EU region** (close to the
PCs and to Streamlit's servers is best for latency), with **backups / point-in-time restore**
enabled. Copy the connection string with `sslmode=require`:

```
postgresql://USER:PASSWORD@HOST/DBNAME?sslmode=require
```

The app creates its tables on first start (`src/vspm/db/migrations`, plus the barcode registry
tables). Writers are serialised with a PostgreSQL advisory lock, so the rules that protect
barcodes and labels behave exactly as tested on SQLite (see `tests/test_cloud_database.py`).

## 2. Login provider (OIDC)

Streamlit's built-in login (`st.login`) needs an OAuth client. Choose one:

* **Google** (Google Workspace or Gmail accounts): Google Cloud Console → APIs & Services →
  Credentials → *OAuth client ID* → Web application. Authorised redirect URI:
  `https://<your-app>.streamlit.app/oauth2callback`.
* **Microsoft 365** (Entra ID): App registrations → New registration → Web redirect URI
  `https://<your-app>.streamlit.app/oauth2callback` → create a client secret.

Only e-mails listed in `VSPM_ALLOWED_EMAILS` (employees) or `VSPM_ADMIN_EMAILS` (may upload
templates and set barcode series) can use the app; everyone else is refused after signing in.

## 3. Create the app

1. <https://share.streamlit.io> → **Create app** → from GitHub: repository `vstavridis/labels`,
   branch **`main`**, **Main file path `src/vspm/ui/app.py`**.
2. *Advanced settings*: **Python 3.12**. Secrets: paste `.streamlit/secrets.toml.example` with
   real values (database URL, e-mail lists, `[auth]` block, Google Drive service-account JSON,
   `ANTHROPIC_API_KEY`). Never commit the real values.
3. Deploy. Dependencies come from `requirements.txt`.
4. Recommended extra layer: in the app's **Sharing** settings make the app private and invite
   only the employees (Streamlit's own viewer check runs before the app's login).

The repository is **public**: it contains no templates, customer data, credentials or workbook.
The DYMO templates are uploaded into the database (step 5), customer rules are entered in
Settings, secrets live only in the Streamlit secrets store.

## 4. Settings that differ from a local installation

| Setting | Cloud value | Why |
|---|---|---|
| `VSPM_DATABASE_URL` | PostgreSQL URL | Container disk is temporary |
| `VSPM_PRINTER_MODE` | `browser` | Printers are on the PCs |
| `VSPM_BARCODE_MODE` | `database` | Shared registry tables in the same database |
| `VSPM_EXCEL_MODE` | `staging` | The server cannot write the OneDrive workbook |
| `VSPM_REQUIRE_AUTH` | (unset = required) | Login is required whenever a database URL is set |
| `VSPM_DYMO_COUNTER_MODE` | `sync` (or `remove`) | How the template COUNTER is neutralised |

## 5. First start — in this order

1. Sign in with an **admin** e-mail. *Settings → DYMO templates*: upload the four `.label` files
   (VS, PG, PG0, EX). Each upload is validated (objects, counter prefix, barcode can be set);
   every version is kept.
2. On **each PC** follow [WINDOWS_PC_SETUP.md](WINDOWS_PC_SETUP.md): DYMO service, local network
   permission, **TEST0001 on every template**, confirmation.
3. **Only after TEST0001 is confirmed on both PCs**: *Settings → Barcode series*. For each series
   enter the next free number (higher than every barcode already printed, including manual
   ones — check the workbook and the DYMO counters on both PCs), type the resulting barcode to
   confirm. Until a series is set, no production label can be printed (the app refuses).
4. Print one real label per series, scan it, check the Excel staging list, enter it in the
   workbook, and reconcile with an uploaded copy (*History → Excel*).

## 6. Excel on the cloud

The hosted app does not write `Σχισμενα.xlsx` (it is on OneDrive on the PCs; replacing it with a
copy could overwrite someone else's edits). Printed labels stay **waiting for Excel**:
*History → Excel* → **download** the pending rows (in the workbook's column order) → enter them
in the workbook → upload a copy of the workbook → **Check the copy**: labels found exactly once
become *synchronised*, duplicates are flagged. A future option is writing through the Microsoft
Graph API (requires an Entra app registration with access to the file); not implemented.

## 7. Operations

* **Backups**: the database is the system of record (barcodes, labels, print history). Use the
  provider's automated backups; test a restore once.
* **Updates**: pushing to the deployed branch redeploys the app; migrations run automatically.
* **Sleeping apps**: Community Cloud may put an unused app to sleep; the first visit wakes it
  (≈ 30 s). No data is lost (it is in PostgreSQL).
* **Limits**: Community Cloud resources are modest (see Streamlit's current limits); the app's load from
  two PCs is small.
* **Data location**: Streamlit Community Cloud is hosted outside the EU (check Streamlit's documentation
  for the current region); scans pass through it to the AI
  provider (when `VSPM_OCR_UPLOAD_CONSENT=true`). Check this against your data-protection
  requirements.
