# Barcode numbering, registry and migration

## The problem

Each template's `COUNTER` (VS, PG, PG0, EX) lives inside a `.label` file shared through OneDrive. Two PCs can
print from their own synced copy before OneDrive syncs the other's change, so both can print the same number.
The reference files already show this risk: the VS template copy is at 11546 while the workbook already holds
VS barcodes up to **VS11711** (see [FINDINGS.md](FINDINGS.md)).

**Uniqueness cannot be guaranteed while any PC still prints with an uncoordinated DYMO counter.**

## The solution: one authority for numbers

`vspm_registry` is a small web service backed by PostgreSQL. It keeps four independent sequences and allocates
each number in one database transaction (`UPDATE … RETURNING` on a row-locked sequence + insert of the
reservation). Every reservation stores series, number, barcode, idempotency key, production reference,
operator, computer, client type and timestamps; every change is written to an audit table.

* **Idempotency**: the app uses `vspm:<install id>:row:<row id>` as key. Retrying after a timeout or crash
  returns the same number, so one production row can never consume two numbers.
* **Reprints** never call the registry; they reuse the stored barcode.
* **No offline fallback**: if the registry is unreachable, Print & Save stops with an error.
* `max_value` per sequence can cap allocation (useful for pilot ranges).
* Sequences can only move forward (unless explicitly overridden) and never below an existing reservation.

### Employees who still print manually in DYMO

Open `https://<registry>/manual`, enter the API key, name, series and number of labels. The page reserves a
contiguous block (e.g. VS11720–VS11724). Set the DYMO counter's start to the first number and print exactly
that many labels. The block is recorded in the registry like any other reservation.

## Deploying the registry

Any small Linux server/VPS or a managed container platform with PostgreSQL works.

```bash
cd deploy/registry
export POSTGRES_PASSWORD='…' VSPM_REGISTRY_API_KEYS='key-pc1,key-pc2,key-manual' VSPM_REGISTRY_ADMIN_KEY='…'
docker compose up -d --build
```

Put it behind HTTPS (reverse proxy such as Caddy, or the platform's TLS). Give each PC its own client key
(`VSPM_REGISTRY_API_KEY`) so keys can be revoked individually. Back up the PostgreSQL volume daily.

For a trial on one PC without a server: `vspm registry serve` (SQLite file `registry.sqlite3`).

## Initial reconciliation (required before going live)

The counter values in the templates are **not authoritative**. For each series:

1. Stop all printing (app and manual DYMO) for a short window.
2. Find the highest number really printed: newest physical labels, every PC's current template counter
   (DYMO Label → counter properties), and the workbook (`vspm report` lists the highest per prefix).
3. Initialise the registry with the next free number (admin key required):

   ```bash
   .venv/bin/vspm registry set-sequence VS 11712 --prefix VS --width 3
   .venv/bin/vspm registry set-sequence PG 7030 --prefix PG --width 3
   .venv/bin/vspm registry set-sequence PG0 2279 --prefix PG0 --width 3
   .venv/bin/vspm registry set-sequence EX 3417 --prefix EX --width 3
   ```

   (Example numbers only — use your reconciled values; consider adding a safety gap, e.g. +50.)
4. From now on every printing PC either uses the app (`VSPM_BARCODE_MODE=registry`) or reserves blocks on the
   `/manual` page before printing manually.

## Safe pilot mode (before the registry exists)

Give the app PC an exclusive range far from current numbers that no DYMO counter will reach during the
pilot, e.g.:

```
VSPM_BARCODE_MODE=pilot_range
VSPM_PILOT_RANGES=VS:90000-90999,PG:90000-90999,PG0:90000-90999,EX:90000-90999
```

Agree with everyone that manual DYMO counters stay below these ranges. Pilot numbers are allocated in the
local SQLite database (single PC only) and the app stops when a range is exhausted. If the ranges conflict
with your numbering conventions, keep `VSPM_BARCODE_MODE=disabled` until the registry is deployed.

`VSPM_BARCODE_MODE=mock` (990000+) is for testing only; never stick mock labels on products.
