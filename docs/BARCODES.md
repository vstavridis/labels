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

### Coexistence with manual DYMO printing

The registry can only prevent duplicates for numbers it hands out. Three tools cover manual printing:

1. **Reserve before printing (preferred).** Open `https://<registry>/manual`, enter the API key, your name,
   the series and the number of labels. The page reserves a contiguous block (e.g. VS11720–VS11724). In DYMO
   Label set the counter of the template to the first number and print exactly that many labels. Retrying
   the same request (e.g. after a network error) returns the same block, never a second one.
2. **Report numbers printed without a reservation.**
   `vspm registry observe VS11800 VS11801 --source manual` records them and moves the sequence past them, so
   the registry never issues them later. Any number listed under `already_registered` had **also** been
   issued by the registry: that is a real duplicate — find both labels.
3. **Teach the registry the workbook's history.**
   `vspm registry observe-workbook --workbook <copy of the workbook>` sends every barcode in column H
   (read-only). Numbers already known are normal here (labels printed by the app); unknown ones are recorded
   and the sequences are advanced past them. Run it at go-live and whenever manual printing happened.

**Duplicates remain possible** while someone prints with a DYMO counter that was neither reserved nor
reported. The registry cannot see those labels. The only complete protection is: every manual print starts
with a block reservation (1), and the counters inside the shared template files are not used as the source
of numbers.

### Audit (October 2026)

| Property | How it is achieved | Verified by |
|---|---|---|
| No number issued twice | one transaction: row-locked `UPDATE … RETURNING` on the sequence + insert with `UNIQUE(series, number)` and `UNIQUE(barcode)` | 800 parallel requests across **two registry processes on PostgreSQL 16**: one number per key, no duplicates, no gaps (`tests/test_registry_postgres.py`) |
| Retries never consume a second number | `UNIQUE(idempotency_key)`; a concurrent duplicate rolls back its own allocation | same test (many repeats of each key) |
| Manual blocks never overlap app numbers | blocks use the same locked allocation | 120 single + 30 block requests interleaved: contiguous, no overlap |
| Same block request sent twice at once | exact `production_ref` match, `IntegrityError` → return existing block | 20 concurrent identical requests → one block |
| Manually printed numbers never issued later | `/v1/observed` locks the sequence row before checking/recording | observations interleaved with 80 allocations |
| Sequence cannot move back below issued numbers | checked in `PUT /v1/sequences` | `tests/test_barcodes.py` |
| Duplicate print of the same document line from two PCs | `production_ref` = scan file id + row number, checked before reserving; operator must confirm "different line" | `tests/test_pilot_safety.py` |

Defects found and fixed in this audit: the block lookup used an SQL `LIKE` with the user's request id, so a
request id containing `%` or `_` could receive **another person's block**; two identical block requests at
the same moment returned an error; a retried request with a different count silently returned the old block;
failed Excel queue jobs were retried every 30 s forever (now 5 min doubling, max 1 h).

Not yet verified: the registry deployed on your server behind HTTPS, network failures between the PCs and the
server, PostgreSQL backups/restore.

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
