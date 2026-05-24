package com.medlens.core.agent

import com.medlens.core.data.model.InteractionEffect
import com.medlens.core.data.model.KnownInteraction
import com.medlens.core.data.model.MedicationSafetyReport
import com.medlens.core.data.model.NormalizedMedication
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class FallbackReportFormatterTest {

    @Test
    fun `flagged pair fallback includes severity and effects`() {
        val report = MedicationSafetyReport(
            input_medications = listOf("Advil", "Warfarin"),
            normalized_medications = listOf(
                NormalizedMedication(input_name = "Advil", canonical_name = "ibuprofen", drug_id = 1, resolved = true, matched_alias = "advil"),
                NormalizedMedication(input_name = "Warfarin", canonical_name = "warfarin", drug_id = 2, resolved = true, matched_alias = "warfarin"),
            ),
            checked_pair_count = 1,
            overall_severity = "Major",
            evidence_status = "found",
            unresolved_medications = emptyList(),
            findings = listOf(
                KnownInteraction(
                    found = true,
                    drug_a = "ibuprofen",
                    drug_b = "warfarin",
                    severity = "Major",
                    row_count = 5,
                    effects = listOf(
                        InteractionEffect(adverse_effect = "increased bleeding risk", severity = "Major", row_count = 3, source_regions = listOf("USA", "EU")),
                    ),
                    source_regions = listOf("USA", "EU"),
                    source_bases = listOf("FDA", "EMA"),
                    source_urls = listOf("https://example.com"),
                    mechanisms = listOf("NSAIDs inhibit platelet aggregation"),
                    risk_flags = listOf("elderly", "anticoagulant users"),
                    evidence_bases = emptyList(),
                    practical_guidance = null,
                ),
            ),
            duplicate_ingredient_warnings = emptyList(),
            limitations = emptyList(),
            source_files = emptyList(),
        )

        val result = FallbackReportFormatter.format(report)

        assertNotNull(result)
        assertTrue(result!!.contains("ibuprofen"))
        assertTrue(result.contains("warfarin"))
        assertTrue(result.contains("Major"))
        assertTrue(result.contains("increased bleeding risk"))
        assertTrue(result.contains("USA"))
        assertTrue(result.contains("EU"))
        assertTrue(result.contains("not medical advice"))
    }

    @Test
    fun `no local finding fallback message is clear`() {
        val report = MedicationSafetyReport(
            input_medications = listOf("Aspirin", "Vitamin C"),
            normalized_medications = listOf(
                NormalizedMedication(input_name = "Aspirin", canonical_name = "aspirin", drug_id = 1, resolved = true, matched_alias = "aspirin"),
                NormalizedMedication(input_name = "Vitamin C", canonical_name = "ascorbic acid", drug_id = 2, resolved = true, matched_alias = "vitamin c"),
            ),
            checked_pair_count = 1,
            overall_severity = null,
            evidence_status = "no_finding",
            unresolved_medications = emptyList(),
            findings = emptyList(),
            duplicate_ingredient_warnings = emptyList(),
            limitations = emptyList(),
            source_files = emptyList(),
        )

        val result = FallbackReportFormatter.format(report)

        assertNotNull(result)
        assertTrue(result!!.contains("aspirin"))
        assertTrue(result.contains("ascorbic acid"))
        assertTrue(result.contains("No flagged interaction"))
        assertTrue(result.contains("not medical advice"))
    }

    @Test
    fun `unresolved medicine fallback lists unresolvable names`() {
        val result = FallbackReportFormatter.formatUnresolved(listOf("Mystery Pill", "Unknown Drug"))

        assertTrue(result.contains("Mystery Pill"))
        assertTrue(result.contains("Unknown Drug"))
        assertTrue(result.contains("could not identify"))
        assertTrue(result.contains("active ingredient"))
    }

    @Test
    fun `format returns null for empty report`() {
        val report = MedicationSafetyReport(
            input_medications = emptyList(),
            normalized_medications = emptyList(),
            checked_pair_count = 0,
            overall_severity = null,
            evidence_status = "empty",
            unresolved_medications = emptyList(),
            findings = emptyList(),
            duplicate_ingredient_warnings = emptyList(),
            limitations = emptyList(),
            source_files = emptyList(),
        )

        assertNull(FallbackReportFormatter.format(report))
    }

    @Test
    fun `unresolved medicines appear in output`() {
        val report = MedicationSafetyReport(
            input_medications = listOf("Advil", "Mystery Pill"),
            normalized_medications = listOf(
                NormalizedMedication(input_name = "Advil", canonical_name = "ibuprofen", drug_id = 1, resolved = true, matched_alias = "advil"),
                NormalizedMedication(input_name = "Mystery Pill", canonical_name = null, drug_id = null, resolved = false, matched_alias = null),
            ),
            checked_pair_count = 0,
            overall_severity = null,
            evidence_status = "partial",
            unresolved_medications = listOf(
                NormalizedMedication(input_name = "Mystery Pill", canonical_name = null, drug_id = null, resolved = false, matched_alias = null),
            ),
            findings = emptyList(),
            duplicate_ingredient_warnings = emptyList(),
            limitations = emptyList(),
            source_files = emptyList(),
        )

        val result = FallbackReportFormatter.format(report)

        assertNotNull(result)
        assertTrue(result!!.contains("ibuprofen"))
        assertTrue(result.contains("Mystery Pill"))
        assertTrue(result.contains("could not identify"))
    }

    @Test
    fun `duplicate ingredient warning appears in output`() {
        val report = MedicationSafetyReport(
            input_medications = listOf("Dolo", "Paracetamol"),
            normalized_medications = listOf(
                NormalizedMedication(input_name = "Dolo", canonical_name = "acetaminophen", drug_id = 1, resolved = true, matched_alias = "dolo"),
                NormalizedMedication(input_name = "Paracetamol", canonical_name = "acetaminophen", drug_id = 1, resolved = true, matched_alias = "paracetamol"),
            ),
            checked_pair_count = 0,
            overall_severity = null,
            evidence_status = "no_finding",
            unresolved_medications = emptyList(),
            findings = emptyList(),
            duplicate_ingredient_warnings = listOf(
                com.medlens.core.data.model.DuplicateIngredientWarning(
                    ingredient = "acetaminophen",
                    input_names = listOf("Dolo", "Paracetamol"),
                    practical_risk_tier = "Duplicate",
                    practical_summary = "These contain the same active ingredient. Do not take both together.",
                    dose_context_needed = "Check all sources for acetaminophen.",
                    risk_factor_questions = "Are you taking other acetaminophen products?",
                    source_urls = emptyList(),
                ),
            ),
            limitations = emptyList(),
        )

        val result = FallbackReportFormatter.format(report)

        assertNotNull(result)
        assertTrue(result!!.contains("acetaminophen"))
        assertTrue(result.contains("Dolo"))
        assertTrue(result.contains("Paracetamol"))
        assertTrue(result.contains("same active ingredient"))
    }

    @Test
    fun `formatNoFinding produces clear message`() {
        val result = FallbackReportFormatter.formatNoFinding(
            resolvedNames = listOf("aspirin", "vitamin c"),
            unresolvedNames = listOf("unknown pill"),
        )

        assertTrue(result.contains("aspirin"))
        assertTrue(result.contains("vitamin c"))
        assertTrue(result.contains("unknown pill"))
        assertTrue(result.contains("No flagged interaction"))
        assertTrue(result.contains("not medical advice"))
    }
}
