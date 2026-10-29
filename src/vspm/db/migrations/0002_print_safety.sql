-- Print lock (one print at a time per label) and template fingerprint.
ALTER TABLE labels ADD COLUMN print_lock_until TEXT;
ALTER TABLE labels ADD COLUMN template_sha256 TEXT;
ALTER TABLE labels ADD COLUMN production_ref TEXT;
CREATE INDEX ix_labels_production_ref ON labels(production_ref);
