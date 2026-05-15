package com.medlens.core.data.model

import kotlinx.serialization.Serializable

@Serializable
data class NormalizedMedication(
    val input_name: String,
    val normalized_input: String,
    val canonical_name: String?,
    val drug_id: Long?,
    val matched_alias: String?,
    val resolved: Boolean,
)

@Serializable
data class InteractionEffect(
    val adverse_effect: String,
    val severity: String,
    val row_count: Int,
    val source_regions: List<String>,
)

@Serializable
data class RawDdiSignal(
    val source_file: String,
    val source_row_number: Int,
    val source_signal_id: String,
    val region: String,
    val drug1_raw: String,
    val drug2_raw: String,
    val adverse_effect: String,
    val severity: String,
    val mechanism_or_rationale: String,
    val interaction_category: String,
    val interaction_direction: String,
    val evidence_basis: String,
    val source_basis: String,
    val source_urls: String,
    val population_relevance: String,
    val patient_risk_flags: String,
    val dataset_type: String,
    val use_case_note: String,
)

@Serializable
data class PracticalGuidance(
    val rule_id: String,
    val practical_risk_tier: String,
    val practical_summary: String,
    val dose_context_needed: String,
    val risk_factor_questions: String,
    val source_urls: List<String>,
)

@Serializable
data class DuplicateIngredientWarning(
    val ingredient: String,
    val input_names: List<String>,
    val practical_risk_tier: String,
    val practical_summary: String,
    val dose_context_needed: String,
    val risk_factor_questions: String,
    val source_urls: List<String>,
)

@Serializable
data class KnownInteraction(
    val found: Boolean,
    val drug_a: String,
    val drug_b: String,
    val severity: String?,
    val row_count: Int,
    val source_regions: List<String>,
    val evidence_bases: List<String>,
    val source_bases: List<String>,
    val source_urls: List<String>,
    val mechanisms: List<String>,
    val risk_flags: List<String>,
    val dataset_types: List<String>,
    val use_case_notes: List<String>,
    val effects: List<InteractionEffect>,
    val raw_signals: List<RawDdiSignal>,
    val evidence_source: String,
    val practical_guidance: PracticalGuidance? = null,
)

@Serializable
data class MedicationSafetyReport(
    val input_medications: List<String>,
    val normalized_medications: List<NormalizedMedication>,
    val unresolved_medications: List<NormalizedMedication>,
    val checked_pair_count: Int,
    val findings: List<KnownInteraction>,
    val duplicate_ingredient_warnings: List<DuplicateIngredientWarning> = emptyList(),
    val overall_severity: String,
    val evidence_status: String,
    val limitations: List<String>,
)

@Serializable
data class CommonMedicineRow(
    val medicine_id: String,
    val canonical_name: String,
    val generic_or_common_name: String,
    val composition_or_strength_pattern: String,
    val dosage_form: String,
    val therapeutic_category: String,
    val common_daily_life_use_india: String,
    val common_brand_examples_india: String,
    val availability_context_india: String,
    val otc_or_rx: String,
    val nlem_or_jan_aushadhi_presence: String,
    val india_relevance: String,
    val patient_risk_flags_india: String,
    val source_basis: String,
    val source_urls: String,
    val dataset_note: String,
)

@Serializable
data class EvidenceImportFile(
    val source_file: String,
    val region: String,
    val rows_seen: Int,
    val rows_imported: Int,
    val rows_unresolved: Int,
    val unique_pairs_imported: Int,
)

@Serializable
data class AliasSearchResult(
    val canonical: String,
    val aliases: List<String>,
)

@Serializable
data class CommonMedicineProfile(
    val query: String,
    val normalized: NormalizedMedication,
    val aliases: List<String>,
    val matches: List<CommonMedicineRow>,
)

@Serializable
data class DrugInteractionList(
    val normalized: NormalizedMedication,
    val interactions: List<KnownInteraction>,
)
