# Google Drive (scanned documents)

The app only **reads** the one folder your scanner uploads to. It lists the 5 newest PDF/image files
(by upload time), downloads the selected one into `data/documents/`, and remembers each file's Drive id and
content checksum (`md5Checksum`, or `headRevisionId`) so a re-scanned/changed file is recognised as a new
revision and flagged on the dashboard.

Scope requested: `https://www.googleapis.com/auth/drive.readonly`. With a **service account** (recommended) the
app can only see what you explicitly share with that account — i.e. just the scan folder.

## Option A — service account (recommended)

1. <https://console.cloud.google.com/> → create a project (e.g. `vs-production-manager`).
2. *APIs & Services → Library* → enable **Google Drive API**.
3. *APIs & Services → Credentials → Create credentials → Service account*. No roles are needed.
4. Open the service account → *Keys → Add key → JSON*. Save the file as
   `credentials/google_service_account.json` in the project folder (git-ignored).
5. In Google Drive, right-click the scanner folder → *Share* → add the service account e-mail
   (`…@….iam.gserviceaccount.com`) as **Viewer**.
6. Open the folder in the browser; the folder id is the last part of the URL
   (`https://drive.google.com/drive/folders/<FOLDER_ID>`).
7. `.env`:

   ```
   VSPM_DRIVE_MODE=google
   VSPM_DRIVE_FOLDER_ID=<FOLDER_ID>
   VSPM_GOOGLE_AUTH=service_account
   VSPM_GOOGLE_CREDENTIALS_FILE=credentials/google_service_account.json
   ```

## Option B — your own Google account (OAuth)

1. Same project; *OAuth consent screen* → External/Internal, add yourself as test user.
2. *Credentials → Create credentials → OAuth client ID → Desktop app*; download JSON to
   `credentials/google_oauth_client.json`.
3. `.env`: `VSPM_GOOGLE_AUTH=oauth`, `VSPM_GOOGLE_CREDENTIALS_FILE=credentials/google_oauth_client.json`.
4. On first use a browser window asks for permission; the token is stored in `credentials/google_token.json`.
   This grants read access to *all* your Drive files, which is why option A is preferred.

## Option C — Google Drive for Desktop folder

If Drive for Desktop syncs the scan folder to the PC, you can avoid API credentials entirely:

```
VSPM_DRIVE_MODE=local
VSPM_LOCAL_SCAN_DIR=G:/My Drive/Scans          # Windows
VSPM_LOCAL_SCAN_DIR=/Users/<you>/Library/CloudStorage/GoogleDrive-<account>/My Drive/Scans   # macOS
```

## Test

```bash
.venv/bin/vspm check
```

should print `Documents (google): 5 found`. Status: **this integration has not been tested against a real
Google account in development.**
