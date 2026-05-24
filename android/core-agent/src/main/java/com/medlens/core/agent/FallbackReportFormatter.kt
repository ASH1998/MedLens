package com.medlens.core.agent

import com.medlens.core.data.model.MedicationSafetyReport

/**
 * Conservative fallback formatter that turns a [MedicationSafetyReport] into
 * patient-facing text without an LLM. Used when the Gemma model is not yet
 * downloaded but the user has typed medication names that can be checked
 * locally.
 *
 * This formatter only summarizes structured report fields. It does not add
 * clinical knowledge that is not already present in the deterministic output.
 */
object FallbackReportFormatter {

    /**
     * Format a structured report as patient-facing text.
     * Returns null if the report has no useful content.
     */
    fun format(report: MedicationSafetyReport): String? {
        val resolved = report.normalized_medications
            .filter { it.resolved }
            .mapNotNull { it.canonical_name }
            .distinct()
        val unresolved = report.unresolved_medications
            .map { it.input_name }
            .distinct()

        if (resolved.isEmpty() && unresolved.isEmpty()) return null

        val lines = mutableListOf<String>()

        // Header: what was identified
        if (resolved.isNotEmpty()) {
            lines += if (resolved.size >= 2) {
                "I checked these medicines: ${resolved.joinToString(", ")}."
            } else {
                "I identified: ${resolved.joinToString(", ")}."
            }
        }

        // Findings
        if (report.findings.isNotEmpty()) {
            for (finding in report.findings.take(3)) {
                val severity = finding.severity ?: "flagged"
                val pair = "${finding.drug_a} + ${finding.drug_b}"
                val effects = finding.effects.take(3).map { it.adverse_effect }

                lines += "**$pair** — flagged as **$severity**."
                if (effects.isNotEmpty()) {
                    lines += "Main concerns: ${effects.joinToString(", ")}."
                }

                // Practical guidance if available
                finding.practical_guidance?.let { guidance ->
                    guidance.practical_summary?.let { summary ->
                        lines += summary
                    }
                }

                // Source regions
                val regions = finding.source_regions.take(3)
                if (regions.isNotEmpty()) {
                    lines += "Evidence from: ${regions.joinToString(", ")}."
                }
            }
        } else if (report.checked_pair_count > 0) {
            lines += "No flagged interaction found among the identified medicines in the local evidence."
        }

        // Duplicate ingredient warnings
        if (report.duplicate_ingredient_warnings.isNotEmpty()) {
            for (warning in report.duplicate_ingredient_warnings.take(2)) {
                lines += "Note: ${warning.ingredient} appears in multiple products (${warning.input_names.joinToString(", ")}). ${warning.practical_summary}"
            }
        }

        // Unresolved medicines
        if (unresolved.isNotEmpty()) {
            lines += "I could not identify: ${unresolved.joinToString(", ")}. Please type the active ingredient name for a more complete check."
        }

        // Insufficient medicines
        if (resolved.size < 2 && unresolved.isEmpty()) {
            lines += "I need at least two medicine names to check for interactions."
        }

        // Disclaimer
        lines += "This is a local evidence check, not medical advice. Consult your doctor or pharmacist."

        return lines.joinToString("\n\n").takeIf { it.isNotBlank() }
    }

    /**
     * Format a "no local finding" message when we have resolved medicines but
     * no flagged interaction.
     */
    fun formatNoFinding(resolvedNames: List<String>, unresolvedNames: List<String>): String {
        val lines = mutableListOf<String>()
        if (resolvedNames.isNotEmpty()) {
            lines += "I checked: ${resolvedNames.joinToString(", ")}."
        }
        lines += "No flagged interaction found in the local evidence."
        if (unresolvedNames.isNotEmpty()) {
            lines += "I could not identify: ${unresolvedNames.joinToString(", ")}. Please type the active ingredient name."
        }
        lines += "This is a local evidence check, not medical advice. Consult your doctor or pharmacist."
        return lines.joinToString("\n\n")
    }

    /**
     * Format a message when none of the input names could be resolved.
     */
    fun formatUnresolved(unresolvedNames: List<String>): String {
        val lines = mutableListOf<String>()
        lines += "I could not identify any of these medicines: ${unresolvedNames.joinToString(", ")}."
        lines += "Try typing the active ingredient name (e.g., 'acetaminophen' instead of 'Dolo')."
        lines += "This is a local evidence check, not medical advice. Consult your doctor or pharmacist."
        return lines.joinToString("\n\n")
    }
}
