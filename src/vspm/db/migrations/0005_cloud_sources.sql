-- OneDrive / Google Drive sources configured in the app (Settings -> Cloud sources).
-- OAuth tokens are stored ENCRYPTED (Fernet, key from VSPM_SECRET_KEY in the Streamlit secrets).
CREATE TABLE cloud_credentials (
    provider TEXT PRIMARY KEY,
    account TEXT,
    secret_enc TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Resolved cloud files: the sharing link is only used to find the item once; drive/item ids,
-- eTag and our own SHA-256 tell later whether the file changed.
CREATE TABLE cloud_files (
    role TEXT PRIMARY KEY,           -- templates_folder | template:VS | workbook | workbook_test
    source_url TEXT,
    drive_id TEXT,
    item_id TEXT,
    name TEXT,
    etag TEXT,
    size INTEGER,
    modified TEXT,
    sha256 TEXT,
    last_sync TEXT,
    last_error TEXT,
    last_error_at TEXT
);

-- Copy of the workbook as it was just before each automatic update (OneDrive also keeps
-- its own version history). The newest 20 are kept.
CREATE TABLE workbook_backups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    item_id TEXT,
    etag TEXT,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    content_b64 TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL
);

-- Short leases: only one app instance writes the workbook at a time.
CREATE TABLE leases (
    name TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    expires_at REAL NOT NULL
);
