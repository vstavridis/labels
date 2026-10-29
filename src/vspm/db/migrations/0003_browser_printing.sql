-- Browser-side DYMO printing: one row per browser/PC ("station") and per print job.

CREATE TABLE print_stations (
    station_id TEXT PRIMARY KEY,           -- random id kept in the browser's localStorage
    name TEXT NOT NULL DEFAULT '',
    printer_name TEXT NOT NULL DEFAULT '',
    last_seen TEXT,
    last_user TEXT,
    user_agent TEXT,
    service_present INTEGER NOT NULL DEFAULT 0,
    printers_json TEXT NOT NULL DEFAULT '[]',
    diagnostics_json TEXT NOT NULL DEFAULT '{}',
    verified_printer TEXT,                 -- printer on which TEST0001 was confirmed physically
    verified_at TEXT,
    verified_by TEXT
);

CREATE TABLE print_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,                    -- initial | retry | reprint | test
    label_id INTEGER REFERENCES labels(id),
    template_key TEXT NOT NULL,
    barcode TEXT NOT NULL,
    xml_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,                  -- pending | submitted | failed | not_submitted | rejected | uncertain | expired | manual_pending | manual_confirmed | cancelled
    session_id TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,
    station_id TEXT,
    printer_name TEXT,
    reported_at TEXT,
    browser_sha256 TEXT,
    error TEXT,
    result_json TEXT
);

-- At most one print job waiting for a browser per label: two tabs cannot both send it.
CREATE UNIQUE INDEX ux_print_jobs_one_pending_per_label ON print_jobs(label_id) WHERE status = 'pending' AND label_id IS NOT NULL;
CREATE INDEX ix_print_jobs_label ON print_jobs(label_id);
