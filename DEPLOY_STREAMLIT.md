# Deploy VS Production Manager on Streamlit Community Cloud — step by step

**Goal:** one GitHub branch (`main`), one web address (`https://<name>.streamlit.app`), two Windows PCs
that each print to their own DYMO printer. **No Python on the PCs.**

```
  PC 1 (Chrome/Edge) ─┐                                   ┌─ PostgreSQL database (Neon)
                      ├─ https://<name>.streamlit.app ────┤   barcodes, labels, history, templates
  PC 2 (Chrome/Edge) ─┘   (Streamlit Community Cloud)     └─ shared by both PCs
     │
     └─ each browser prints to the DYMO Web Service + LabelWriter of ITS OWN PC
```

Time needed: about 1 hour the first time. You need: a GitHub login (owner of `vstavridis/labels`),
a Google account (for the login screen and Google Drive), your Anthropic API key, the four DYMO
`.label` templates (VS, PG, PG0, EX) and both PCs.

> **Never put passwords or keys in the repository.** They go only into Streamlit's *Secrets* box (step 4).
> The repository is public.

---

## Step 1 — Create the PostgreSQL database (Neon, free plan)

The app's server on Streamlit Cloud forgets everything when it restarts, so all records live in this
database. Both PCs use it, which is what stops them from ever printing the same barcode twice.

1. Go to <https://neon.tech> → **Sign up** (you can use your Google account).
2. **Create project**: name `vspm`, PostgreSQL **16**, region **AWS Europe (Frankfurt)** (closest to you).
3. On the project dashboard click **Connect**. Choose database `neondb`, role `neondb_owner`, and copy the
   **connection string**. It looks like:
   ```
   postgresql://neondb_owner:AbC123xyz@ep-cool-name-123456.eu-central-1.aws.neon.tech/neondb?sslmode=require
   ```
   Keep it secret — it contains the password. Make sure it ends with `?sslmode=require`.
4. Backups: Neon keeps a restore history automatically (*Branches → Restore*). On a paid plan you can keep it
   longer. This database is your record of every barcode — do not delete the project.

You do not need to create any tables: the app creates them on its first start.

(Supabase, Aiven or any other PostgreSQL 14+ with TLS works the same way.)

## Step 2 — Create the login (Google sign-in)

Only the people you list may use the app. They sign in with their Google account.

1. Go to <https://console.cloud.google.com> → top bar → **Select a project** → **New project** → name
   `vspm-login` → **Create**.
2. Menu → **APIs & Services → OAuth consent screen** → **Get started**: app name `VS Production Manager`,
   your e-mail as support e-mail, audience **External**, your e-mail as contact → **Create**.
3. **Audience** (left menu) → **Test users** → **Add users** → add the e-mail of every employee (while the app is
   in *Testing* mode only these accounts can sign in — that is fine for a small team).
4. **Clients** → **Create client** → type **Web application** → name `vspm` → under
   **Authorized redirect URIs** add exactly:
   ```
   https://YOUR-APP-NAME.streamlit.app/oauth2callback
   ```
   (you choose `YOUR-APP-NAME` in step 5; you can come back and correct this later) → **Create**.
5. Copy the **Client ID** and **Client secret**.

Using Microsoft 365 accounts instead? See the Microsoft block in `.streamlit/secrets.toml.example`.

## Step 3 — Google Drive and AI (for reading the scans)

* Google Drive: follow [docs/GOOGLE_DRIVE.md](docs/GOOGLE_DRIVE.md) to create a **service account**, download
  its JSON key and **share the scans folder** with the service account's e-mail. Copy the folder ID from the
  folder's address (`https://drive.google.com/drive/folders/<FOLDER_ID>`).
* AI reading: your Anthropic API key from <https://console.anthropic.com> → *API keys*.

## Step 4 — Prepare the Secrets text

Open [.streamlit/secrets.toml.example](.streamlit/secrets.toml.example) on GitHub, copy all of it into a
text editor (Notepad) and replace the placeholders:

| Placeholder | Put |
|---|---|
| `VSPM_DATABASE_URL` | the Neon connection string (step 1) |
| `VSPM_ALLOWED_EMAILS` | employees' e-mails, comma-separated |
| `VSPM_ADMIN_EMAILS` | your e-mail (may upload templates and set barcode numbers) |
| `VSPM_DRIVE_FOLDER_ID` | the scans folder ID (step 3) |
| `VSPM_GOOGLE_CREDENTIALS_JSON` | the whole content of the service-account JSON file, between the `'''` |
| `ANTHROPIC_API_KEY` | your Anthropic key |
| `[auth]` `redirect_uri` | `https://YOUR-APP-NAME.streamlit.app/oauth2callback` |
| `[auth]` `cookie_secret` | any long random text (at least 40 letters and digits, e.g. from a password manager) |
| `[auth]` `client_id` / `client_secret` | from step 2 |

Leave these as they are — they are the safe cloud settings:

```toml
VSPM_BARCODE_MODE = "database"        # barcodes come from the shared database
VSPM_PRINTER_MODE = "browser"         # each browser prints to its own PC's DYMO
VSPM_EXCEL_MODE = "staging"           # the app never writes your OneDrive workbook
VSPM_EXCEL_WRITE_ENABLED = "false"
```

Delete the Microsoft lines if you use Google. Keep the text only in Streamlit (next step), not in a file.

## Step 5 — Deploy the app

1. Go to <https://share.streamlit.io> → sign in **with GitHub** (allow access to `vstavridis/labels`).
2. **Create app** → **Deploy a public app from GitHub** (*"Yup, I have an app"*).
3. Fill in **exactly**:

   | Field | Value |
   |---|---|
   | Repository | `vstavridis/labels` |
   | Branch | `main` |
   | Main file path | `src/vspm/ui/app.py` |
   | App URL | choose `YOUR-APP-NAME` (the same name as in the redirect URI) |

4. **Advanced settings** → **Python version: 3.12** → **Secrets**: paste the text from step 4 → **Save**.
5. **Deploy**. The first start installs everything from `requirements.txt` (2–5 minutes). The DYMO browser
   component is part of the repository and needs no extra installation.
6. If you changed the app name, fix the redirect URI in Google (step 2.4) and in the Secrets.
7. Recommended: app **⋮ → Settings → Sharing** → *Only specific people can view this app* → add the
   employees' e-mails. (A second lock in front of the app's own login.)

You should now see the sign-in page. Sign in with your admin e-mail.

## Step 6 — First start (admin, about 10 minutes)

1. **⚙️ Settings → DYMO templates**: upload the four `.label` files from DYMO Label (VS, PG, PG0, EX), one
   at a time. Each file is checked; a wrong file is refused with the reason.
2. **⚙️ Settings → Customer rules**: enter the customer abbreviations (or check them if already there).
3. **⚙️ Settings → Integration checks**: Documents (Google Drive) should list your scans.
4. **Do not set the barcode series yet.** Until you do, the app refuses to print production labels.

## Step 7 — Set up each of the two PCs (about 10 minutes each)

Full sheet: [docs/WINDOWS_PC_SETUP.md](docs/WINDOWS_PC_SETUP.md). In short, on **each** PC:

1. DYMO Label v8 installed, LabelWriter Wireless connected by **USB**.
2. In Chrome or Edge open <https://127.0.0.1:41951/DYMO/DLS/Printing/StatusConnected> → it must show `true`.
3. Open `https://YOUR-APP-NAME.streamlit.app` → sign in → **🖨️ DYMO printer**.
4. The browser asks to *look for and connect to devices on your local network* → **Allow**.
5. Check: *DYMO service ✅*, your LabelWriter listed and selected. Give the PC a name (e.g. ΓΡΑΦΕΙΟ / ΑΠΟΘΗΚΗ).
6. **Print TEST0001** for VS, PG, PG0 and EX. For each label: compare it with a normal label, scan it — the
   scanner must read `TEST0001` — then press **Yes — correct label**. TEST0001 uses no real barcode, writes
   nothing to Excel and does not touch the DYMO counters.

## Step 8 — Set the barcode numbers (only after step 7 worked on BOTH PCs)

1. Find the highest barcode already used for each type — in the workbook **and** on the DYMO counters of both
   PCs (open each template in DYMO Label and look at the counter). Do not change the counters.
2. **⚙️ Settings → Barcode series** → for each type enter the **next free** number, type the resulting barcode
   in the confirmation box → **Save**. It can never be moved backwards.
3. From now on the app gives every label its number from the database. **Do not print app labels from DYMO
   Label with its counter.** Anyone who still prints by hand reserves a block on the same page.

## Step 9 — Test with the two PCs

| # | Do | Expected |
|---|---|---|
| 1 | PC 1: print one real label (any type) and scan it | the barcode shown in the app |
| 2 | PC 2: print the next label of the same type | the **next** number — never the same as PC 1 |
| 3 | Reprint one of them | same barcode; nothing new under *History → Excel* |
| 4 | Both PCs press Print & Save on the same row at the same time | only one label is printed |
| 5 | Switch a printer off and print | *failed*, nothing recorded; switch on and print again → same barcode |
| 6 | *History → Excel*: download the pending rows, enter them in `Σχισμενα.xlsx`, upload a copy → *Check the copy* | the labels become *synchronised* |
| 7 | Sign in with an e-mail that is not on the list | refused |

Excel stays in **staging** mode: the app never writes to the OneDrive workbook. Change this only after
direct OneDrive writing has been tested separately.

## Updating the app

Changes pushed to `main` on GitHub are deployed automatically. Database tables are updated automatically.
Your data stays in Neon.

## Troubleshooting

| Problem | Fix |
|---|---|
| **"This file does not exist"** when deploying | Branch must be `main` (not `master` or an old `claude/...` branch) and Main file path exactly `src/vspm/ui/app.py` (no leading `/`). If GitHub still offers the old branches, refresh the page after `main` exists. |
| *Employee login is not configured – the app stays closed* | The `[auth]` block or `VSPM_ALLOWED_EMAILS` is missing in Secrets |
| Google says *redirect_uri_mismatch* | The redirect URI in Google and in Secrets must both be `https://YOUR-APP-NAME.streamlit.app/oauth2callback` |
| *Access blocked / app not verified* at Google sign-in | Add the account under *OAuth consent screen → Audience → Test users* |
| Database errors at start | Check `VSPM_DATABASE_URL` (copy again from Neon, keep `?sslmode=require`) |
| *DYMO service: not found* on a PC | Step 7.2; then **Re-check** on the printer page |
| *The browser blocks access to the DYMO service* | Address bar icon → Site settings → *Local network access* → **Allow** → reload |
| App asleep after some days without use | Open the address and wait ~30 s; nothing is lost |
| See the server log | share.streamlit.io → your app → **Manage app** (bottom right) |

More: [docs/STREAMLIT_CLOUD.md](docs/STREAMLIT_CLOUD.md) (reference), [docs/BROWSER_PRINTING.md](docs/BROWSER_PRINTING.md)
(how printing works and what is protected), [docs/PILOT_CHECKLIST.md](docs/PILOT_CHECKLIST.md) Part 4 (checklist).
