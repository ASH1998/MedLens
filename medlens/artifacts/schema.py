"""SQLite schemas for MedLens mobile/dashboard artifacts."""

NORMALIZATION_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS drug (
    id INTEGER PRIMARY KEY,
    canonical_name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    region_scope TEXT NOT NULL DEFAULT 'global',
    is_common INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS drug_alias (
    id INTEGER PRIMARY KEY,
    drug_id INTEGER NOT NULL REFERENCES drug(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL UNIQUE,
    alias_type TEXT NOT NULL,
    region TEXT NOT NULL DEFAULT 'global'
);

CREATE INDEX IF NOT EXISTS idx_drug_alias_drug_id ON drug_alias(drug_id);
CREATE INDEX IF NOT EXISTS idx_drug_alias_normalized ON drug_alias(normalized_alias);
CREATE INDEX IF NOT EXISTS idx_drug_category ON drug(category);
"""

EVIDENCE_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS known_interaction (
    id INTEGER PRIMARY KEY,
    drug_a_id INTEGER NOT NULL,
    drug_b_id INTEGER NOT NULL,
    drug_a TEXT NOT NULL,
    drug_b TEXT NOT NULL,
    severity TEXT NOT NULL,
    severity_rank INTEGER NOT NULL,
    row_count INTEGER NOT NULL,
    source_regions_json TEXT NOT NULL,
    evidence_bases_json TEXT NOT NULL,
    source_bases_json TEXT NOT NULL,
    source_urls_json TEXT NOT NULL,
    mechanisms_json TEXT NOT NULL,
    risk_flags_json TEXT NOT NULL,
    dataset_types_json TEXT NOT NULL,
    use_case_notes_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(drug_a, drug_b)
);

CREATE TABLE IF NOT EXISTS known_interaction_effect (
    id INTEGER PRIMARY KEY,
    known_interaction_id INTEGER NOT NULL REFERENCES known_interaction(id) ON DELETE CASCADE,
    adverse_effect TEXT NOT NULL,
    severity TEXT NOT NULL,
    severity_rank INTEGER NOT NULL,
    row_count INTEGER NOT NULL,
    source_regions_json TEXT NOT NULL,
    UNIQUE(known_interaction_id, adverse_effect, severity)
);

CREATE TABLE IF NOT EXISTS ddi_raw_signal (
    id INTEGER PRIMARY KEY,
    known_interaction_id INTEGER REFERENCES known_interaction(id) ON DELETE SET NULL,
    source_file TEXT NOT NULL,
    source_row_number INTEGER NOT NULL,
    source_signal_id TEXT,
    region TEXT NOT NULL,
    drug1_raw TEXT NOT NULL,
    drug2_raw TEXT NOT NULL,
    normalized_drug1 TEXT NOT NULL,
    normalized_drug2 TEXT NOT NULL,
    drug_a TEXT,
    drug_b TEXT,
    resolved INTEGER NOT NULL,
    adverse_effect TEXT,
    severity TEXT,
    severity_rank INTEGER NOT NULL,
    mechanism_or_rationale TEXT,
    interaction_category TEXT,
    interaction_direction TEXT,
    evidence_basis TEXT,
    source_basis TEXT,
    source_urls TEXT,
    population_relevance TEXT,
    patient_risk_flags TEXT,
    dataset_type TEXT,
    use_case_note TEXT
);

CREATE TABLE IF NOT EXISTS ddi_import_issue (
    id INTEGER PRIMARY KEY,
    source_file TEXT NOT NULL,
    row_number INTEGER NOT NULL,
    drug1 TEXT NOT NULL,
    drug2 TEXT NOT NULL,
    normalized_drug1 TEXT NOT NULL,
    normalized_drug2 TEXT NOT NULL,
    reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_import_file (
    id INTEGER PRIMARY KEY,
    source_file TEXT NOT NULL UNIQUE,
    region TEXT NOT NULL,
    rows_seen INTEGER NOT NULL,
    rows_imported INTEGER NOT NULL,
    rows_unresolved INTEGER NOT NULL,
    unique_pairs_imported INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_known_interaction_pair ON known_interaction(drug_a, drug_b);
CREATE INDEX IF NOT EXISTS idx_known_interaction_severity ON known_interaction(severity_rank DESC);
CREATE INDEX IF NOT EXISTS idx_known_interaction_effect ON known_interaction_effect(known_interaction_id);
CREATE INDEX IF NOT EXISTS idx_ddi_raw_signal_pair ON ddi_raw_signal(drug_a, drug_b);
CREATE INDEX IF NOT EXISTS idx_ddi_raw_signal_interaction ON ddi_raw_signal(known_interaction_id);
CREATE INDEX IF NOT EXISTS idx_ddi_raw_signal_resolved ON ddi_raw_signal(resolved);
CREATE INDEX IF NOT EXISTS idx_ddi_import_issue_source ON ddi_import_issue(source_file);
"""
