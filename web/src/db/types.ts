// Row interfaces mirroring the Python dataclasses in `medlens/tools/local_safety.py`.
// These intentionally use snake_case to match the SQL column names so the TS port
// can use Object.assign / direct row mapping with no per-column rename.

export type Severity = "Major" | "Moderate" | "Minor" | "Low-Moderate" | "Unknown";

export interface NormalizedMedication {
  input: string;
  canonical: string | null;
  matched_alias: string | null;
  resolved: boolean;
}

export interface KnownInteraction {
  drug_a: string;
  drug_b: string;
  severity: Severity;
  row_count: number;
  source_regions: string[];
  source_bases: string[];
  source_urls: string[];
}

export interface InteractionEffect {
  drug_a: string;
  drug_b: string;
  effect: string;
  severity: Severity;
  row_count: number;
}

export interface RawDdiSignal {
  drug1: string;
  drug2: string;
  effect: string | null;
  severity: string | null;
  mechanism: string | null;
  rationale: string | null;
  source_basis: string | null;
  source_url: string | null;
  region: string | null;
  patient_risk_flag: string | null;
  caveat: string | null;
}

export interface CommonMedicineRow {
  medicine_id: string;
  common_name: string;
  generic_name: string | null;
  strength: string | null;
  form: string | null;
  brand_examples: string | null;
  daily_life_use: string | null;
  therapeutic_category: string | null;
  otc_or_rx: string | null;
  risk_flags: string | null;
  source_url: string | null;
}

export interface EvidenceImportFile {
  source: string;
  rows_seen: number;
  rows_imported: number;
  rows_unresolved: number;
  unique_pairs_imported: number;
}
