-- DYMO templates uploaded through the app (Streamlit Cloud has no persistent disk and the
-- templates must not be in the public repository). Every version is kept: labels store the
-- SHA-256 of the template they were printed with, so reprints and verification use exactly it.
CREATE TABLE label_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_key TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    filename TEXT NOT NULL,
    content_b64 TEXT NOT NULL,
    counter_prefix TEXT,
    active INTEGER NOT NULL DEFAULT 0,
    uploaded_by TEXT,
    uploaded_at TEXT NOT NULL,
    UNIQUE (template_key, sha256)
);
CREATE INDEX ix_label_templates_active ON label_templates(template_key, active);
