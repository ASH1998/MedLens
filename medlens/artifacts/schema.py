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

CREATE TABLE IF NOT EXISTS india_common_medicine (
    id INTEGER PRIMARY KEY,
    medicine_id TEXT NOT NULL UNIQUE,
    drug_id INTEGER NOT NULL REFERENCES drug(id) ON DELETE CASCADE,
    source_row_number INTEGER NOT NULL,
    generic_or_common_name TEXT NOT NULL,
    normalized_generic_name TEXT NOT NULL,
    composition_or_strength_pattern TEXT NOT NULL,
    dosage_form TEXT NOT NULL,
    therapeutic_category TEXT NOT NULL,
    common_daily_life_use_india TEXT NOT NULL,
    common_brand_examples_india TEXT NOT NULL,
    availability_context_india TEXT NOT NULL,
    otc_or_rx TEXT NOT NULL,
    nlem_or_jan_aushadhi_presence TEXT NOT NULL,
    india_relevance TEXT NOT NULL,
    patient_risk_flags_india TEXT NOT NULL,
    source_basis TEXT NOT NULL,
    source_urls TEXT NOT NULL,
    dataset_note TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS medicine_ingredient_map (
    id INTEGER PRIMARY KEY,
    brand_name TEXT NOT NULL,
    normalized_brand_name TEXT NOT NULL,
    ingredient_drug_id INTEGER NOT NULL REFERENCES drug(id) ON DELETE CASCADE,
    ingredient_order INTEGER NOT NULL,
    strength TEXT NOT NULL DEFAULT '',
    region TEXT NOT NULL DEFAULT 'india',
    source_basis TEXT NOT NULL DEFAULT '',
    source_urls TEXT NOT NULL DEFAULT '',
    UNIQUE(normalized_brand_name, ingredient_drug_id)
);

CREATE TABLE IF NOT EXISTS practical_pair_guidance (
    id INTEGER PRIMARY KEY,
    rule_id TEXT NOT NULL UNIQUE,
    left_key TEXT NOT NULL,
    right_key TEXT NOT NULL,
    match_type TEXT NOT NULL,
    practical_risk_tier TEXT NOT NULL,
    practical_summary TEXT NOT NULL,
    dose_context_needed TEXT NOT NULL,
    risk_factor_questions TEXT NOT NULL,
    source_urls TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_drug_alias_drug_id ON drug_alias(drug_id);
CREATE INDEX IF NOT EXISTS idx_drug_alias_normalized ON drug_alias(normalized_alias);
CREATE INDEX IF NOT EXISTS idx_drug_category ON drug(category);
CREATE INDEX IF NOT EXISTS idx_india_common_medicine_drug_id ON india_common_medicine(drug_id);
CREATE INDEX IF NOT EXISTS idx_india_common_medicine_normalized ON india_common_medicine(normalized_generic_name);
CREATE INDEX IF NOT EXISTS idx_medicine_ingredient_map_normalized ON medicine_ingredient_map(normalized_brand_name);
CREATE INDEX IF NOT EXISTS idx_practical_pair_guidance_keys ON practical_pair_guidance(left_key, right_key);
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
