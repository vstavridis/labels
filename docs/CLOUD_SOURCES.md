# Cloud sources: OneDrive templates, OneDrive workbook, Google Drive scans

Settings → **☁️ Cloud sources** (admin only). Code: `src/vspm/integrations/onedrive.py`,
`src/vspm/services/cloud_sources.py`. Tests: `tests/test_cloud_sources.py` (against a test double of
Microsoft identity + Graph, `src/vspm/testing_graph.py`).

## What was investigated (personal Microsoft account)

| Question | Finding | What the app does |
|---|---|---|
| Can a `1drv.ms` link be used as access? | No. A sharing link only identifies the item; anonymous access to consumer sharing links through the API is not a supported way for an app to read files. | The link is resolved with **your own signed-in token**: `GET /shares/u!{base64url(link)}/driveItem`. The drive id + item id are stored and used afterwards. |
| Which sign-in works for personal OneDrive? | Microsoft identity platform, `consumers` endpoint, delegated permissions. A web app on Streamlit Cloud has no browser session with Microsoft, so the **device code flow** (public client) is the simplest supported flow: the admin signs in once at microsoft.com/devicelogin. | Device code flow; the **refresh token is stored encrypted** (Fernet, key from `VSPM_SECRET_KEY`) in PostgreSQL; access tokens are refreshed automatically. |
| Narrower permission than the whole OneDrive? | Personal OneDrive has no per-folder delegated scope (only `Files.*`, or the app's own folder). | `Files.ReadWrite` (the workbook must be written), `offline_access`, `User.Read`. The app only touches the items behind the configured links. |
| Graph Excel workbook API (`/workbook/...`)? | Not supported for personal OneDrive. | Not used. |
| Safe way to update the workbook? | File level: download the file, change it, upload it again with `If-Match: <eTag>`; OneDrive answers **412** if the file was saved by anyone in between. | That is exactly what the app does, one writer at a time (database lease), with a read-back check. |
| Detect template changes | `eTag` (changes with every save) + our own SHA-256 of the content. | New content → new **template version** in the database; older versions are kept for reprints. |

**Not verified here** (no Microsoft account available to the developer): that `/shares` resolves your
specific new-style `1drv.ms/f/c/…` and `1drv.ms/x/c/…` links, that OneDrive honours `If-Match` on
`PUT /content` for your account (the **end-to-end copy test checks exactly this** before production
writing can be enabled), and refresh-token lifetime. If a link cannot be resolved, enter the path in
your OneDrive instead of the link (e.g. `Documents/Ετικέτες`) — the app then uses
`/me/drive/root:/<path>`.

## DYMO templates

1. *Refresh DYMO templates* (and automatically when the app is opened, at most every
   `VSPM_CLOUD_REFRESH_MINUTES`, default 10, shared by both PCs) lists the folder.
2. Each type is found by its **exact file name** (`VSPM_ONEDRIVE_TEMPLATE_FILES`, Unicode-normalised
   comparison) — or, if no name is set for a type, by the template's COUNTER prefix (VS / PG / PG0 / EX).
3. Unchanged eTag → nothing is downloaded. Changed → the file is downloaded (read-only), validated
   (objects, counter prefix, barcode can be set) and stored byte-exact as a new version. A broken or
   wrong file is **refused** and the previous version stays active (shown as an error).
4. The original files are never written: the app only issues GET requests for them.
5. Labels store the SHA-256 of their template; reprints and verification use that exact version.

## Excel workbook (`VSPM_EXCEL_MODE=onedrive`)

Every production record is first stored in PostgreSQL (label row + Excel payload A–P). Then:

```
 lease "excel_writer" (only one app instance at a time)
   download workbook + eTag
   append rows with the same code as the local writer:
      barcode already in column H -> not written again (marked synchronised)
      A–K values, G = VS/PG/EX (PG0 -> PG), H = reserved barcode, P = print date (Europe/Athens)
      L–O = formulas of the previous row, every other part of the file kept byte for byte
   keep a copy of the downloaded file (database, newest 20) — OneDrive also keeps version history
   upload with If-Match: <eTag>      412 -> someone saved meanwhile: download again, retry (3x)
   download again and verify the rows
```

* **Off until proven:** with `onedrive` mode, rows stay **staged** until (1) the *end-to-end test on the
  COPY* passed and (2) an admin switched on *automatic writing to the PRODUCTION workbook*. The test
  appends a test row to the **copy**, uploads with If-Match, reads it back, checks that all other parts
  of the file are identical, and checks that an upload with a stale eTag is **refused**. If OneDrive
  accepted the stale upload, the test fails and production writing cannot be enabled.
* **Errors:** a rejected upload (network, permission, storage full) keeps the rows in the queue with
  status *error* and the message; *conflict* means the workbook kept changing (3 attempts) — nothing
  was overwritten. *Sync pending rows now* retries.
* **Two PCs:** both use the same database lease; the second waits (*busy*) and its rows are written on
  the next sync. Duplicate barcodes are impossible: column H is checked on the downloaded file right
  before each upload.

### Procedures

| Situation | Do |
|---|---|
| Rows show *error* | Read the message in Settings → Cloud sources, fix (sign-in expired → reconnect), press *Sync pending rows now*. |
| Rows show *conflict* | Someone was editing. Close the workbook in Excel on all PCs, press *Sync pending rows now*. |
| Check what is really in the workbook | History → Excel → upload a copy → *Check the copy* (read-only reconciliation; marks labels found exactly once as synchronised, flags duplicates). |
| Undo an automatic change | OneDrive web → the file → *Version history* → restore; or download the backup from Settings → Cloud sources → *Workbook backups*. Then run the reconciliation. |
| Excel desktop with the file open | Prefer syncing while nobody has it open. With AutoSave/co-authoring, Excel merges server changes; if it cannot, it offers to save a copy — the If-Match check guarantees the app never overwrites a version saved before its upload, but **edits not yet saved in an open Excel window are outside the app's control**. Validate this with the copy (step 7 of DEPLOY_STREAMLIT.md) before enabling production writing. |

## Google Drive scans

* The folder link (`https://drive.google.com/drive/folders/<id>?usp=sharing`) is parsed to its id and
  stored; it overrides `VSPM_DRIVE_FOLDER_ID`.
* Access: a **service account** (key in the secrets) with the **Drive API** enabled; share the folder
  with the service account's e-mail (shown in Settings) as *Viewer*. Read-only scope
  (`drive.readonly`): scans are never modified or deleted.
* The page lists the 5 newest PDFs/images; changed or re-uploaded files are detected by checksum /
  revision and a re-read never duplicates printed rows (existing duplicate protection).
