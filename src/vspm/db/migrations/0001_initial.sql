-- VS Production Manager local state (SQLite). Applied once, in order.

CREATE TABLE documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,                 -- google | local | mock
    source_file_id TEXT NOT NULL,         -- Drive file id (or path for local)
    source_revision TEXT NOT NULL DEFAULT '',  -- Drive md5/headRevisionId/modifiedTime
    name TEXT NOT NULL,
    mime_type TEXT NOT NULL DEFAULT '',
    uploaded_at TEXT,
    local_path TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    ocr_provider TEXT,
    ocr_model TEXT,
    ocr_result_json TEXT,
    ocr_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (source, source_file_id, source_revision)
);

CREATE TABLE production_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER REFERENCES documents(id),
    row_index INTEGER NOT NULL,
    customer_full TEXT NOT NULL DEFAULT '',
    customer_short TEXT NOT NULL DEFAULT '',
    material TEXT NOT NULL DEFAULT '',
    thickness TEXT NOT NULL DEFAULT '',
    dimensions TEXT NOT NULL DEFAULT '',
    weight TEXT NOT NULL DEFAULT '',
    pieces TEXT NOT NULL DEFAULT '',
    packages TEXT NOT NULL DEFAULT '',
    source_barcode TEXT NOT NULL DEFAULT '',
    source_roll TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    document_date TEXT NOT NULL DEFAULT '',
    template_key TEXT,
    template_suggested TEXT,
    template_reason TEXT NOT NULL DEFAULT '',
    template_needs_confirmation INTEGER NOT NULL DEFAULT 0,
    flags_json TEXT NOT NULL DEFAULT '{}',
    ocr_original_json TEXT NOT NULL DEFAULT '{}',
    approved INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'draft',
    deleted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX ix_rows_document ON production_rows(document_id);

-- One label per production row. Reprints reuse this record.
CREATE TABLE labels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    production_row_id INTEGER NOT NULL UNIQUE REFERENCES production_rows(id),
    template_key TEXT NOT NULL,
    series TEXT NOT NULL,
    number INTEGER NOT NULL,
    barcode TEXT NOT NULL UNIQUE,
    reservation_id TEXT,
    reservation_mode TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    content_json TEXT NOT NULL,          -- exact printed values (for reprints)
    label_xml_path TEXT,
    preview_path TEXT,
    print_status TEXT NOT NULL,
    excel_status TEXT NOT NULL DEFAULT 'not_queued',
    excel_row_number INTEGER,
    excel_payload_json TEXT,
    first_printed_at TEXT,               -- UTC ISO timestamp of first successful submit
    print_date_local TEXT,               -- YYYY-MM-DD in factory timezone (Excel column P)
    operator TEXT,
    computer TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE print_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label_id INTEGER NOT NULL REFERENCES labels(id),
    kind TEXT NOT NULL,                  -- initial | retry | reprint
    printer_mode TEXT NOT NULL,
    printer_name TEXT,
    status TEXT NOT NULL,                -- submitted | error | confirmed_ok | confirmed_failed
    error TEXT,
    job_ref TEXT,
    operator TEXT,
    computer TEXT,
    created_at TEXT NOT NULL,
    confirmed_at TEXT,
    confirmed_by TEXT
);

CREATE TABLE excel_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label_id INTEGER NOT NULL REFERENCES labels(id),
    mode TEXT NOT NULL,
    status TEXT NOT NULL,                -- synchronized | already_present | staged | queued_remote | conflict | error
    row_number INTEGER,
    workbook_path TEXT,
    hash_before TEXT,
    hash_after TEXT,
    backup_path TEXT,
    error TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE customer_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    priority INTEGER NOT NULL,
    match_type TEXT NOT NULL,            -- exact | contains | regex
    pattern TEXT NOT NULL,
    template_key TEXT,                   -- VS | PG | PG0 | EX (NULL = only provides short name)
    short_name TEXT NOT NULL DEFAULT '',
    requires_confirmation INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE local_sequences (
    mode TEXT NOT NULL,                  -- pilot_range | mock
    series TEXT NOT NULL,
    next_value INTEGER NOT NULL,
    max_value INTEGER NOT NULL,
    PRIMARY KEY (mode, series)
);

CREATE TABLE local_reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL,
    series TEXT NOT NULL,
    number INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    production_ref TEXT,
    operator TEXT,
    computer TEXT,
    status TEXT NOT NULL DEFAULT 'reserved',
    created_at TEXT NOT NULL,
    UNIQUE (mode, series, number)
);

CREATE TABLE app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT,
    computer TEXT,
    entity TEXT NOT NULL,
    entity_id TEXT,
    action TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX ix_audit_entity ON audit_log(entity, entity_id);
