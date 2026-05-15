package com.medlens.core.agent

import com.medlens.core.agent.model.ChatSession
import com.medlens.core.agent.model.ToolCallRecord
import com.medlens.core.agent.model.ToolSchema
import com.medlens.core.data.SafetyRepository
import com.medlens.core.data.model.NormalizedMedication
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.encodeToJsonElement
import kotlin.system.measureTimeMillis

val TOOL_SCHEMAS: List<ToolSchema> = listOf(
    ToolSchema(
        "add_medications",
        "Add medication names to the current chat session.",
        objectSchema(
            """
            "names": {"type": "array", "items": {"type": "string"}}
            """.trimIndent(),
            required = listOf("names"),
        ),
    ),
    ToolSchema(
        "remove_medications",
        "Remove medication names from the current chat session.",
        objectSchema(
            """
            "names": {"type": "array", "items": {"type": "string"}}
            """.trimIndent(),
            required = listOf("names"),
        ),
    ),
    ToolSchema("clear_medications", "Clear the current medication list.", objectSchema()),
    ToolSchema("list_medications", "List current medications.", objectSchema()),
    ToolSchema(
        "normalize_medications",
        "Normalize medication names through the local alias index.",
        objectSchema(
            """
            "names": {"type": "array", "items": {"type": "string"}}
            """.trimIndent(),
            required = listOf("names"),
        ),
    ),
    ToolSchema(
        "lookup_pair",
        "Look up a known DDI reference pair.",
        objectSchema(
            """
            "drug_a": {"type": "string"},
            "drug_b": {"type": "string"},
            "limit": {"type": "integer"}
            """.trimIndent(),
            required = listOf("drug_a", "drug_b"),
        ),
    ),
    ToolSchema(
        "list_interactions_for_drug",
        "List known DDI reference interactions involving one medication, with optional filters for minimum severity, region, and risk flag.",
        objectSchema(
            """
            "drug": {"type": "string"},
            "limit": {"type": "integer"},
            "min_severity": {"type": "string"},
            "region": {"type": "string"},
            "risk_flag": {"type": "string"}
            """.trimIndent(),
            required = listOf("drug"),
        ),
    ),
    ToolSchema(
        "build_structured_report",
        "Build a deterministic safety report for supplied or session medications.",
        objectSchema(
            """
            "medication_names": {"type": "array", "items": {"type": "string"}},
            "limit": {"type": "integer"}
            """.trimIndent(),
        ),
    ),
    ToolSchema(
        "search_drug_aliases",
        "Search local medication aliases.",
        objectSchema(
            """
            "query": {"type": "string"},
            "limit": {"type": "integer"}
            """.trimIndent(),
            required = listOf("query"),
        ),
    ),
    ToolSchema(
        "get_common_medicine_profile",
        "Look up India common-medicine metadata for a medicine.",
        objectSchema(
            """
            "name": {"type": "string"},
            "limit": {"type": "integer"}
            """.trimIndent(),
            required = listOf("name"),
        ),
    ),
    ToolSchema(
        "search_common_medicines",
        "Search India common-medicine metadata by query and optional category or risk filters.",
        objectSchema(
            """
            "query": {"type": "string"},
            "therapeutic_category": {"type": "string"},
            "otc_or_rx": {"type": "string"},
            "nlem_or_jan_aushadhi": {"type": "string"},
            "risk_flag": {"type": "string"},
            "limit": {"type": "integer"}
            """.trimIndent(),
        ),
    ),
    ToolSchema("list_evidence_sources", "List DDI source files loaded into the evidence artifact.", objectSchema()),
    ToolSchema("current_session_summary", "Return provider and session summary.", objectSchema()),
    ToolSchema(
        "evidence_about",
        "Explain evidence sources, severity scale, or limitations.",
        objectSchema(
            """
            "topic": {"type": "string"}
            """.trimIndent(),
            required = listOf("topic"),
        ),
    ),
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
            val normalized = store.normalizeMedications(names)
            val existingInputs = session.medications.map { it.input_name.lowercase() }.toMutableSet()
            val existingCanonicals = session.medications.mapNotNull { it.canonical_name }.toMutableSet()
            val added = mutableListOf<com.medlens.core.data.model.NormalizedMedication>()
            val alreadyPresent = mutableListOf<com.medlens.core.data.model.NormalizedMedication>()
            val unresolved = mutableListOf<com.medlens.core.data.model.NormalizedMedication>()
            for (item in normalized) {
                val inputKey = item.input_name.lowercase()
                val canonicalKey = item.canonical_name
                val duplicate = inputKey in existingInputs ||
                    (canonicalKey != null && canonicalKey in existingCanonicals)
                if (duplicate) {
                    alreadyPresent += item
                    continue
                }
                session.medications.add(item)
                existingInputs += inputKey
                if (canonicalKey != null) existingCanonicals += canonicalKey
                if (item.resolved) added += item else unresolved += item
            }
            session.lastReport = null
            mapOf(
                "json" to buildJsonObject {
                    put("added", json.encodeToJsonElement(added))
                    put("already_present", json.encodeToJsonElement(alreadyPresent))
                    put("unresolved", json.encodeToJsonElement(unresolved))
                }.toString(),
            )
        }
        "remove_medications" -> {
            val names = decodeNames(args["names"])
            val normalized = store.normalizeMedications(names)
            val removeInputs = normalized.map { it.input_name.lowercase() }.toSet()
            val removeCanonicals = normalized.mapNotNull { it.canonical_name }.toSet()
            val removed = mutableListOf<com.medlens.core.data.model.NormalizedMedication>()
            val iterator = session.medications.iterator()
            while (iterator.hasNext()) {
                val item = iterator.next()
                val match = item.input_name.lowercase() in removeInputs ||
                    (item.canonical_name != null && item.canonical_name in removeCanonicals)
                if (match) {
                    removed += item
                    iterator.remove()
                }
            }
            val removedInputs = removed.map { it.input_name.lowercase() }.toSet()
            val removedCanonicals = removed.mapNotNull { it.canonical_name }.toSet()
            val notFound = normalized
                .filter { it.input_name.lowercase() !in removedInputs && (it.canonical_name == null || it.canonical_name !in removedCanonicals) }
                .map { it.input_name }
            session.lastReport = null
            mapOf(
                "json" to buildJsonObject {
                    put("removed", json.encodeToJsonElement(removed))
                    put("not_found", json.encodeToJsonElement(notFound))
                }.toString(),
            )
        }
        "clear_medications" -> {
            session.medications.clear()
            session.lastReport = null
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
            val report = store.buildStructuredReport(names, effectLimit = args["limit"]?.toIntOrNull() ?: 8)
            session.lastReport = report
            mapOf("json" to json.encodeToString(report))
        }
        "search_drug_aliases" -> {
            val query = args["query"].orEmpty()
            mapOf("json" to json.encodeToString(store.searchDrugAliases(query, limit = args["limit"]?.toIntOrNull() ?: 10)))
        }
        "get_common_medicine_profile" -> {
            val nameArg = args["name"].orEmpty()
            mapOf("json" to json.encodeToString(store.getCommonMedicineProfile(nameArg, limit = args["limit"]?.toIntOrNull() ?: 10)))
        }
        "search_common_medicines" -> {
            mapOf(
                "json" to json.encodeToString(
                    store.searchCommonMedicines(
                        query = args["query"],
                        therapeuticCategory = args["therapeutic_category"],
                        otcOrRx = args["otc_or_rx"],
                        nlemOrJanAushadhi = args["nlem_or_jan_aushadhi"],
                        riskFlag = args["risk_flag"],
                        limit = args["limit"]?.toIntOrNull() ?: 10,
                    ),
                ),
            )
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
            "text" to "MedLens grounds medication safety answers in curated interaction evidence. Severity and sources come directly from the checked evidence.",
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

private fun objectSchema(properties: String = "", required: List<String> = emptyList()): String {
    val requiredJson = if (required.isEmpty()) "" else """, "required": [${required.joinToString(",") { "\"$it\"" }}]"""
    val body = properties.trim().trim(',').takeIf { it.isNotBlank() } ?: ""
    return """{"type":"object","properties":{${if (body.isBlank()) "" else body}}$requiredJson}"""
}

fun syncSessionMedications(
    session: ChatSession,
    normalized: List<NormalizedMedication>,
) {
    session.medications.clear()
    session.medications.addAll(normalized)
}
