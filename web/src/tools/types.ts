// TS row interfaces mirroring the Python dataclasses in
// `medlens/tools/local_safety.py`. Field names use snake_case so the JSON
// surface returned to LLM tool calls is byte-compatible with the Python tool
// outputs (the agent loop streams these straight into the model).

export type Severity = "Major" | "Moderate" | "Minor" | "Low-Moderate" | string | null;

export interface NormalizedMedication {
  input_name: string;
  normalized_input: string;
  canonical_name: string | null;
  drug_id: number | null;
  matched_alias: string | null;
  resolved: boolean;
}

export interface InteractionEffect {
  adverse_effect: string;
  severity: string;
  row_count: number;
  source_regions: string[];
}

export interface RawDdiSignal {
  source_file: string;
  source_row_number: number;
  source_signal_id: string;
  region: string;
  drug1_raw: string;
  drug2_raw: string;
  adverse_effect: string;
  severity: string;
  mechanism_or_rationale: string;
  interaction_category: string;
  interaction_direction: string;
  evidence_basis: string;
  source_basis: string;
  source_urls: string;
  population_relevance: string;
  patient_risk_flags: string;
  dataset_type: string;
  use_case_note: string;
}

export interface KnownInteraction {
  found: boolean;
  drug_a: string;
  drug_b: string;
  severity: string | null;
  row_count: number;
  source_regions: string[];
  evidence_bases: string[];
  source_bases: string[];
  source_urls: string[];
  mechanisms: string[];
  risk_flags: string[];
  dataset_types: string[];
  use_case_notes: string[];
  effects: InteractionEffect[];
  raw_signals: RawDdiSignal[];
  evidence_source: string;
}

export interface MedicationSafetyReport {
  input_medications: string[];
  normalized_medications: NormalizedMedication[];
  unresolved_medications: NormalizedMedication[];
  checked_pair_count: number;
  findings: KnownInteraction[];
  overall_severity: string;
  evidence_status: string;
  limitations: string[];
}

export interface CommonMedicineRow {
  medicine_id: string;
  canonical_name: string;
  generic_or_common_name: string;
  composition_or_strength_pattern: string;
  dosage_form: string;
  therapeutic_category: string;
  common_daily_life_use_india: string;
  common_brand_examples_india: string;
  availability_context_india: string;
  otc_or_rx: string;
  nlem_or_jan_aushadhi_presence: string;
  india_relevance: string;
  patient_risk_flags_india: string;
  source_basis: string;
  source_urls: string;
  dataset_note: string;
}

export interface InteractionSearchResult {
  filters: Record<string, unknown>;
  drug_normalization: NormalizedMedication | null;
  interactions: KnownInteraction[];
}

export interface EvidenceImportFile {
  source_file: string;
  region: string;
  rows_seen: number;
  rows_imported: number;
  rows_unresolved: number;
  unique_pairs_imported: number;
}
