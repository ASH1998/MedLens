package com.medlens.core.agent

import com.medlens.core.data.model.KnownInteraction
import com.medlens.core.data.model.MedicationSafetyReport

fun deterministicTextFromReport(report: MedicationSafetyReport): String {
    val lines = mutableListOf(
        "I checked ${pairCountText(report.checked_pair_count)}. In my local reference set, this is marked ${report.overall_severity}.",
    )
    if (report.findings.isNotEmpty()) {
        report.findings.take(3).forEach { finding ->
            lines += deterministicFindingLines(finding)
            lines += ""
            lines += "Sources:"
            lines += sourceLinesFromFinding(finding).ifEmpty {
                listOf("- ${finding.drug_a} + ${finding.drug_b}: no URL on file")
            }
        }
        if (report.findings.size > 3) {
            lines += ""
            lines += "There are ${report.findings.size - 3} more findings available."
        }
    } else {
        lines += "I did not find a flagged interaction for these medicines in the local evidence. That does not prove the combination is safe."
    }
    if (report.unresolved_medications.isNotEmpty()) {
        lines += "I could not match this locally, so I did not check it: ${report.unresolved_medications.joinToString(", ") { it.input_name }}."
    }
    return lines.joinToString("\n")
}

private fun deterministicFindingLines(finding: KnownInteraction): List<String> {
    val lines = mutableListOf("I found a ${finding.severity ?: "flagged"} interaction between ${finding.drug_a} and ${finding.drug_b}.")
    val effects = finding.effects.take(3).map { it.adverse_effect }
    if (effects.isNotEmpty()) {
        lines += "The main concern is ${effects.joinToString(", ")}."
    }
    if (finding.severity == "Major") {
        lines += "Because this is marked Major, it is worth asking a pharmacist or prescriber before using them together."
    }
    return lines
}

private fun sourceLinesFromFinding(finding: KnownInteraction): List<String> {
    val meta = mutableListOf<String>()
    if (finding.source_regions.isNotEmpty()) {
        meta += "regions: ${finding.source_regions.joinToString(", ")}"
    }
    if (finding.source_bases.isNotEmpty()) {
        meta += "basis: ${finding.source_bases.take(3).joinToString("; ")}"
    }
    val suffix = if (meta.isEmpty()) "" else " (${meta.joinToString("; ")})"
    return finding.source_urls.take(6).map { url ->
        "- ${finding.drug_a} + ${finding.drug_b}: $url$suffix"
    }
}

private fun pairCountText(count: Int): String = when (count) {
    0 -> "no medication pairs"
    1 -> "1 medication pair"
    else -> "$count medication pairs"
}
