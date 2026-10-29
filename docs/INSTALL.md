# Installation (Windows, macOS, Linux)

VS Production Manager is a Streamlit web app. It runs on any computer with Python 3.11+ and is used through a
web browser. It can run:

* **on each computer** that prints labels (opens at `http://localhost:8501`), or
* **on one computer or server** that the others open in their browser (`http://<server>:8501`) — see
  [Running for several computers](#running-for-several-computers).

Nothing in the app requires Windows. What each operating system offers for printing:

| OS | Printing options ([DYMO.md](DYMO.md)) |
|---|---|
| Windows | `dymo_web` (DYMO Web Service, recommended), `dymo_com` (legacy SDK, optional) |
| macOS | `dymo_web` (DYMO Connect / DYMO Label web service), `cups` |
| Linux | `cups` (DYMO's CUPS driver for LabelWriter) |

## 1. Prerequisites

* **Python 3.12** (3.11+): <https://www.python.org/downloads/> — on Windows tick *Add python.exe to PATH*;
  on macOS `brew install python@3.12` also works; on Linux use the distribution package (`python3`, `python3-venv`).
* **Git** (or download the repository as a ZIP).
* The DYMO software for your OS (see [DYMO.md](DYMO.md)).
* For direct Excel writing: the folder with `Σχισμενα.xlsx` synchronised locally by OneDrive
  (Windows/macOS). On Linux use `staging` or `registry_queue` mode with an Excel computer elsewhere, see
  [EXCEL_ONEDRIVE.md](EXCEL_ONEDRIVE.md).

## 2. Install

Use a **local** folder that is not synchronised by OneDrive/Dropbox. On Windows, Documents and Desktop are
often redirected into OneDrive — use e.g. `C:\VSPM` instead (the app warns if its data folder is synced,
because the local database can be damaged by synchronisation).

```bash
git clone https://github.com/<owner>/<repo>.git C:/VSPM        # Windows; e.g. ~/vspm on macOS/Linux
cd C:/VSPM
```

macOS / Linux:

```bash
bash scripts/setup.sh
```

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
```

Both scripts create a `.venv`, install the app, create `.env` from `.env.example`, initialise the local
database and run the tests. Manual equivalent on any OS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[ai,google,dev]"     # Windows: .venv\Scripts\python.exe
cp .env.example .env
.venv/bin/vspm init
```

## 3. Reference files and test workbook

Copy the `.label` templates (copies, not the shared OneDrive originals) and a copy of `Σχισμενα.xlsx` into
`reference_files/`. Template file names do not matter: each is recognised by its COUNTER prefix. Put your real
customer rules in `config/customers.yaml` (created from the example; git-ignored). Then:

```bash
.venv/bin/vspm make-test-workbook     # copy to data/test_workbook/ (default VSPM_EXCEL_PATH)
.venv/bin/vspm report
```

## 4. Configure `.env`

Edit `.env` in any text editor, or on the **Ρυθμίσεις** page (press **Επαναφόρτωση** afterwards). Start in
demo mode and enable integrations one at a time with [PILOT_CHECKLIST.md](PILOT_CHECKLIST.md).

## 5. Start

| OS | Command |
|---|---|
| any | `.venv/bin/vspm run` (Windows: `.venv\Scripts\vspm.exe run`) |
| macOS / Linux | `bash scripts/start.sh` (opens the browser) |
| Windows | double-click `scripts\start_vspm.bat` |

The app listens on `localhost` only, unless you choose otherwise (below).

Check all integrations at any time:

```bash
.venv/bin/vspm check
```

## Running for several computers

Start the app on one computer/server and open it from the others:

```bash
VSPM_HOST=0.0.0.0 bash scripts/start.sh          # macOS / Linux
.venv/bin/vspm run --host 0.0.0.0                # any OS
```

Then browse to `http://<that computer's name or IP>:8501`. Things to know:

* **Printing happens on the computer running the app.** The printer must be attached to, or shared with,
  that computer (USB, network, or a CUPS/Windows print share of the LabelWriter). A label for another room
  needs a printer queue for that room on the server.
* Streamlit has no login. Only expose it on the factory network/VPN, never directly to the internet.
* With one central app there is a single local database and a single Excel writer, which also avoids the
  multi-PC Excel problem; the barcode registry is still recommended (it also serves manual DYMO users).

### Docker

`deploy/app/` contains a Dockerfile and compose file for running the app in a container on any OS:

```bash
cd deploy/app
docker compose up -d --build        # http://<host>:8501
```

Mount your templates, workbook folder and credentials as shown in `docker-compose.yml`. From a container
print with `VSPM_PRINTER_MODE=cups` to the host's CUPS (`CUPS_SERVER`) or a network print server. The DYMO
web service on a desktop cannot be reached from a container. *The image has not been built or run during
development (no Docker daemon was available).*

## Start automatically

* Windows: put a shortcut to `scripts\start_vspm.bat` in `shell:startup`.
* macOS: create a Login Item for `scripts/start.sh` (or a `launchd` agent).
* Linux: a `systemd --user` service running `scripts/start.sh`.

## Updating

```bash
git pull
.venv/bin/python -m pip install -e ".[ai,google,dev]"
```

Database migrations run automatically at the next start. `.env`, `data/` and `credentials/` are kept.
