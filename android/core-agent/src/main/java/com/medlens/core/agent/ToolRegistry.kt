package com.medlens.core.agent

import com.medlens.core.agent.model.ChatSession
import com.medlens.core.agent.model.ToolCallRecord
import com.medlens.core.agent.model.ToolSchema
import com.medlens.core.data.SafetyRepository
import com.medlens.core.data.model.NormalizedMedication
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlin.system.measureTimeMillis

val TOOL_SCHEMAS: List<ToolSchema> = listOf(
    ToolSchema("add_medications", "Add medication names to the current chat session."),
    ToolSchema("remove_medications", "Remove medication names from the current chat session."),
    ToolSchema("clear_medications", "Clear the current medication list."),
    ToolSchema("list_medications", "List current medications."),
    ToolSchema("normalize_medications", "Normalize medication names through the local alias index."),
    ToolSchema("lookup_pair", "Look up a known local DDI reference pair."),
    ToolSchema("list_interactions_for_drug", "List known local DDI reference interactions involving one medication."),
    ToolSchema("build_structured_report", "Build a deterministic safety report for supplied or session medications."),
    ToolSchema("search_drug_aliases", "Search local medication aliases."),
    ToolSchema("get_common_medicine_profile", "Look up India common-medicine metadata for a medicine."),
    ToolSchema("list_evidence_sources", "List DDI source files loaded into the evidence artifact."),
    ToolSchema("current_session_summary", "Return provider and session summary."),
    ToolSchema("evidence_about", "Explain local evidence sources, severity scale, or limitations."),
)

class ToolDispatcher(
    private val store: SafetyRepository,
    private val json: Json = Json { prettyPrint = false },
) {
    suspend fun dispatch(
        name: String,
        args: Map<String, String>,
        session: ChatSession,
    ): Map<String, String> {
        var result = emptyMap<String, String>()
        var error: String? = null
        val duration = measureTimeMillis {
            try {
                result = dispatchInner(name, args, session)
            } catch (t: Throwable) {
                error = t.message ?: t::class.java.simpleName
            }
        }
        session.lastTrace += ToolCallRecord(
            name = name,
            args = args,
            resultSummary = result["json"] ?: result["text"],
            error = error,
            duration_ms = duration,
        )
        if (error != null) {
            return mapOf("error" to error.orEmpty())
        }
        return result
    }

    private suspend fun dispatchInner(
        name: String,
        args: Map<String, String>,
        session: ChatSession,
    ): Map<String, String> = when (name) {
        "add_medications" -> {
            val names = decodeNames(args["names"])
            session.medications.clear()
            session.medications.addAll(store.normalizeMedications(names))
            mapOf("json" to json.encodeToString(session.medications))
        }
        "remove_medications" -> {
            val names = decodeNames(args["names"]).map { it.lowercase() }.toSet()
            session.medications.removeAll { it.input_name.lowercase() in names }
            mapOf("json" to json.encodeToString(session.medications))
        }
        "clear_medications" -> {
            session.medications.clear()
            mapOf("text" to "Cleared the current medication list.")
        }
        "list_medications" -> mapOf("json" to json.encodeToString(session.medicationInputs()))
        "normalize_medications" -> {
            val names = decodeNames(args["names"])
            mapOf("json" to json.encodeToString(store.normalizeMedications(names)))
        }
        "lookup_pair" -> {
            val drugA = args["drug_a"].orEmpty()
            val drugB = args["drug_b"].orEmpty()
            mapOf("json" to json.encodeToString(store.lookupKnownInteraction(drugA, drugB)))
        }
        "list_interactions_for_drug" -> {
            val result = store.listInteractionsForDrug(
                drug = args["drug"].orEmpty(),
                limit = args["limit"]?.toIntOrNull() ?: 20,
                minSeverity = args["min_severity"],
                region = args["region"],
                riskFlag = args["risk_flag"],
            )
            mapOf("json" to json.encodeToString(result))
        }
        "build_structured_report" -> {
            val names = decodeNames(args["medication_names"]).ifEmpty { session.medicationInputs() }
            val report = store.buildStructuredReport(names)
            session.lastReport = report
            mapOf("json" to json.encodeToString(report))
        }
        "search_drug_aliases" -> {
            val query = args["query"].orEmpty()
            mapOf("json" to json.encodeToString(store.searchDrugAliases(query)))
        }
        "get_common_medicine_profile" -> {
            val nameArg = args["name"].orEmpty()
            mapOf("json" to json.encodeToString(store.getCommonMedicineProfile(nameArg)))
        }
        "list_evidence_sources" -> mapOf("json" to json.encodeToString(store.listEvidenceSources()))
        "current_session_summary" -> mapOf(
            "json" to json.encodeToString(
                mapOf(
                    "provider" to session.providerName,
                    "medications" to session.medicationInputs(),
                ),
            ),
        )
        "evidence_about" -> mapOf(
            "text" to "MedLens uses the bundled normalization and DDI evidence SQLite artifacts as the authority. Severity and sources come directly from those local tables.",
        )
        else -> mapOf("error" to "Tool not yet ported in Android scaffold: $name")
    }

    private fun decodeNames(raw: String?): List<String> {
        if (raw.isNullOrBlank()) return emptyList()
        val trimmed = raw.trim()
        if (trimmed.startsWith("[") && trimmed.endsWith("]")) {
            return runCatching { json.decodeFromString<List<String>>(trimmed) }
                .getOrElse { emptyList() }
                .map { it.trim() }
                .filter { it.isNotBlank() }
        }
        return trimmed.split("|").map { it.trim() }.filter { it.isNotBlank() }
    }
}

fun syncSessionMedications(
    session: ChatSession,
    normalized: List<NormalizedMedication>,
) {
    session.medications.clear()
    session.medications.addAll(normalized)
}
