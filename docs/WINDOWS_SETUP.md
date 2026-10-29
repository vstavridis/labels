# Windows setup

Tested commands assume Windows 10/11, PowerShell, and an account that can install software.

## 1. Install prerequisites

1. **Python 3.12** from <https://www.python.org/downloads/windows/>. In the installer tick
   *Add python.exe to PATH* and keep *py launcher* selected.
2. **Git** from <https://git-scm.com/download/win> (or download the repository as a ZIP from GitHub).
3. **DYMO Label v8** (already installed). For the recommended print route make sure the
   *DYMO Label Web Service* is running (it is installed with DYMO Label 8.5+; see [DYMO.md](DYMO.md)).
4. **OneDrive** signed in, with the folder containing `Σχισμενα.xlsx` synchronised. Right-click the workbook →
   *Always keep on this device*.

## 2. Get the code and install

```powershell
cd $HOME\Documents
git clone https://github.com/<owner>/<repo>.git vs-production-manager
cd vs-production-manager
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
```

The script creates `.venv`, installs dependencies, creates `.env` from `.env.example`, initialises the local
database and runs the test suite.

## 3. Reference files and test workbook

Copy the `.label` templates and a copy of `Σχισμενα.xlsx` into `reference_files\` (see
`reference_files\README.md`). Then:

```powershell
.\.venv\Scripts\vspm.exe make-test-workbook
.\.venv\Scripts\vspm.exe report
```

`make-test-workbook` copies the workbook to `data\test_workbook\` — the default `VSPM_EXCEL_PATH`. The app
never writes to `reference_files\`.

## 4. Configure `.env`

Open `.env` in Notepad. Each integration is described in its own document; start with the defaults (demo mode)
and enable integrations one at a time following [INTEGRATION_CHECKLIST.md](INTEGRATION_CHECKLIST.md).
Settings can also be edited on the **Ρυθμίσεις** page (press **Επαναφόρτωση** afterwards).

## 5. Start

Double-click `scripts\start_vspm.bat`. The browser opens `http://localhost:8501`.
The app listens only on this PC (`localhost`).

Optional desktop shortcut: right-click `start_vspm.bat` → *Send to* → *Desktop (create shortcut)*.

Optional start at logon: press `Win+R`, type `shell:startup`, and put a shortcut to `start_vspm.bat` there.

## 6. Check

```powershell
.\.venv\Scripts\vspm.exe check
```

prints the state of the printer, barcode allocation, Excel workbook, documents and AI settings.

## Updating

```powershell
cd $HOME\Documents\vs-production-manager
git pull
.\.venv\Scripts\python.exe -m pip install -e ".[ai,google,windows,dev]"
```

Database migrations run automatically at the next start. Your `.env`, `data\` and `credentials\` are kept.
