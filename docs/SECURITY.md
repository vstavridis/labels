# Confidentiality and security review

Repository: `vstavridis/labels` — **visibility: PUBLIC** (checked 29/10/2026). The original brief asked for a
private repository. **Recommended action: make it private** (GitHub → Settings → General → Danger Zone →
Change repository visibility → Private).

## What was checked

Every file in every commit of the repository history (all branches), searched for:

* API keys and tokens (Anthropic `sk-ant-…`, Google `AIza…`), private keys, `client_secret`, passwords;
* the OneDrive account/drive id that appears in the workbook's external link;
* e-mail addresses;
* every customer name in column F and the `ID` sheet of the workbook, every roll number (column K), and the
  customer/company names visible on the label templates;
* production documents (scans, workbooks, `.label` originals, databases).

The search list is built from the reference workbook itself, so it covers every customer in it
(251 terms).

## Result

| Item | Status |
|---|---|
| API keys, tokens, passwords, private keys | **none found** in any commit |
| OneDrive account/drive id | **none** (redacted as `<onedrive-id>` in all documents) |
| E-mail addresses | **none** (commits are authored as `Claude <noreply@anthropic.com>`) |
| Workbook, original `.label` files, scans, databases, `.env`, credentials | **never committed** (git-ignored) |
| Customer names from the workbook | **none**, except the two below |
| The two customer names quoted in the brief (PG and PG0 examples) and the partner name contained in two template **file names** | present in the **first two commits** (config, tests, docs); **removed from the current version** |
| One roll number from the workbook (used as test data) | present in the first two commits; **removed from the current version** |
| Label template structure used by tests (`tests/fixtures/labels/`) | sanitized: logos replaced by a 1-pixel image, customer/company text replaced by `TEST …` |
| Business figures in `docs/` (DYMO counter values, highest barcode per series, sheet layout) | present by design; low sensitivity — remove `docs/FINDINGS.md` / `REFERENCE_REPORT.md` if you prefer |

Changes made so the repository can stay clean:

* Real customer rules live in `config/customers.yaml`, which is **git-ignored**; the repository has only
  `config/customers.example.yaml` with placeholders.
* Template files are recognised by their COUNTER prefix, so their (customer-revealing) file names are not
  needed in the repository; if needed they go in `.env` (`VSPM_TEMPLATE_FILES`), which is git-ignored.
* `vspm report` hides template file names and customer names by default.
* Tests use fictional customers (`tests/fixtures/customers.yaml`).

## Remaining exposure and options

The removed names and roll number are still readable in the **history** of the first two commits while the
repository is public. Options:

1. **Make the repository private** (removes public access; simplest).
2. Additionally **rewrite the history** into one clean commit and force-push. This needs your explicit
   approval because it replaces the published history (anyone who cloned it keeps the old copy, and GitHub
   may keep unreferenced commits reachable by direct link for some time; GitHub Support can purge them).

No credentials were ever committed, so **no key rotation is needed**.

## Operational security notes

* `.env`, `credentials/`, `data/` (database, logs, label files, Excel backups) and `reference_files/` must
  stay git-ignored. `git status` before every commit should never list them.
* The Streamlit app has **no login**. Run it on `localhost` (default) or only on the factory network/VPN.
* The barcode registry must run behind **HTTPS**; give every PC its own API key; keep the admin key off the
  production PCs. The manual-reservation page stores the API key in the browser's local storage on that PC.
* AI: documents are sent to the provider only when `VSPM_OCR_UPLOAD_CONSENT=true` and only when an
  operator presses *Read with AI* ([AI_PROVIDER.md](AI_PROVIDER.md)).
* Google Drive: prefer a service account that can see only the scan folder ([GOOGLE_DRIVE.md](GOOGLE_DRIVE.md)).

## Hosted app (Streamlit Community Cloud)

* **Login**: Streamlit OIDC (Google or Microsoft). Required whenever `VSPM_DATABASE_URL` is set unless
  `VSPM_REQUIRE_AUTH=false` is set explicitly. Fails closed: missing `[auth]` secrets, an empty
  `VSPM_ALLOWED_EMAILS`, an unlisted or unverified e-mail → nothing of the app is shown.
* **Roles**: only `VSPM_ADMIN_EMAILS` may upload templates, set barcode series, report manual numbers
  and reserve blocks. Server settings are read-only on the page in cloud mode.
* **Secrets** live in the Streamlit secrets store (template: `.streamlit/secrets.toml.example`);
  `.streamlit/secrets.toml` is git-ignored. The settings page never shows the database URL, keys or the
  employee e-mail lists (only counts).
* **Templates and customer data** are stored in the database, not in the public repository.
* **Browser printing** talks only to the DYMO service on the same PC (`127.0.0.1`), with the per-site
  Local Network Access permission; no browser security feature needs to be disabled.
* **Database**: use TLS (`sslmode=require`), a dedicated database user, and provider backups.
* Error details shown to users are limited to the error type (`.streamlit/config.toml`).

