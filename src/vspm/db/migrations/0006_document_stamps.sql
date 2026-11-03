-- Barcodes written onto the scanned sheet in Google Drive (VSPM_DRIVE_STAMP_BARCODES).
-- The original scan stays in the file's version history (kept forever) and every stamp is drawn
-- on that original; original_md5/stamped_md5 tell our own edit apart from a re-scan.
CREATE TABLE document_stamps (
    document_id INTEGER PRIMARY KEY,
    file_id TEXT NOT NULL,
    original_revision_id TEXT,
    original_md5 TEXT,
    stamped_md5 TEXT,
    barcodes_json TEXT NOT NULL DEFAULT '[]',
    mode TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    updated_at TEXT NOT NULL
);
