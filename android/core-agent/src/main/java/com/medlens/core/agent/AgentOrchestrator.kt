package com.medlens.core.agent

import com.medlens.core.agent.model.AgentMessage
import com.medlens.core.agent.model.AgentTurnResult
import com.medlens.core.agent.model.ChatSession
import com.medlens.core.agent.model.NativeToolProvider
import com.medlens.core.agent.model.ToolResultPayload
import com.medlens.core.data.SafetyRepository
import com.medlens.core.data.model.KnownInteraction
import com.medlens.core.data.model.MedicationSafetyReport
import com.medlens.core.data.model.NormalizedMedication
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonObjectBuilder
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject

class AgentOrchestrator(
    private val dispatcher: ToolDispatcher,
    private val provider: NativeToolProvider?,
    private val repository: SafetyRepository? = null,
) {
    private val json = Json { prettyPrint = false; ignoreUnknownKeys = true }

    suspend fun runTurn(
        session: ChatSession,
        userMessage: String,
        audiencePrompt: String? = null,
        effectLimit: Int = 5,
        maxRounds: Int = 6,
        maxToolCallsPerRound: Int = 4,
        timeBudgetMillis: Long = 30_000L,
    ): AgentTurnResult {
        session.clearTurnTrace()
        val priorTranscript = textTranscript(session.transcript)
        val systemPrompt = audiencePrompt?.let { "$TOOL_LOOP_SYSTEM_PROMPT\n\nAudience style:\n$it" }
            ?: TOOL_LOOP_SYSTEM_PROMPT

        val activeProvider = provider ?: throw IllegalStateException("Gemma is not ready yet.")
        val currentTurnReport = precheckCurrentTurnReport(session, userMessage, effectLimit)

        var finalText = ""
        var fallbackUsed = false

        try {
            withTimeout(timeBudgetMillis) {
                activeProvider.startTurn(systemPrompt, priorTranscript, TOOL_SCHEMAS).use { turnSession ->
                    val firstUserContent = userContent(userMessage, session.medicationInputs())
                    var response = turnSession.sendUser(firstUserContent)
                    var roundsRemaining = maxRounds

                    while (true) {
                        if (response.tool_calls.isEmpty()) {
                            finalText = response.text.trim()
                            break
                        }
                        if (roundsRemaining <= 0) {
                            fallbackUsed = true
                            break
                        }
                        roundsRemaining -= 1

                        val limited = response.tool_calls.take(maxToolCallsPerRound)
                        val payloads = mutableListOf<ToolResultPayload>()
                        for (call in limited) {
                            val args = call.args.toMutableMap()
                            if (call.name == "build_structured_report" && !args.containsKey("limit")) {
                                args["limit"] = effectLimit.toString()
                            }
                            val result = dispatcher.dispatch(call.name, args, session)
                            val rawContent = result["json"] ?: result["text"] ?: result["error"].orEmpty()
                            payloads += ToolResultPayload(
                                name = call.name,
                                content = compactToolContent(call.name, rawContent),
                            )
                        }
                        response = turnSession.sendToolResults(payloads)
                    }
                }
            }
        } catch (timeout: TimeoutCancellationException) {
            fallbackUsed = true
        }

        if (fallbackUsed || finalText.isBlank()) {
            finalText = buildDeterministicFallback(session, effectLimit)
            fallbackUsed = true
        }
        val reportForVerification = currentTurnReport ?: session.lastReport
        finalText = verifiedFinalText(finalText, reportForVerification)
        if (currentTurnReport != null) {
            session.lastReport = currentTurnReport
        }

        session.transcript.clear()
        session.transcript += priorTranscript
        session.transcript += AgentMessage(role = "user", content = userMessage)
        session.transcript += AgentMessage(role = "assistant", content = finalText)

        return AgentTurnResult(
            finalText = finalText,
            trace = session.lastTrace.toList(),
            report = reportForVerification,
            usedTools = session.lastTrace.map { it.name },
            providerName = activeProvider.name,
        )
    }

    private suspend fun precheckCurrentTurnReport(
        session: ChatSession,
        userMessage: String,
        effectLimit: Int,
    ): MedicationSafetyReport? {
        val repo = repository ?: return null
        val candidates = explicitMedicationCandidates(userMessage)
        if (candidates.size < 2) return null
        val report = repo.buildStructuredReport(candidates, effectLimit = effectLimit)
        mergeResolvedMedications(session, report.normalized_medications)
        session.lastReport = report
        return report
    }

    private fun mergeResolvedMedications(session: ChatSession, normalized: List<NormalizedMedication>) {
        val existingInputs = session.medications.map { it.input_name.lowercase() }.toMutableSet()
        val existingCanonicals = session.medications.mapNotNull { it.canonical_name }.toMutableSet()
        normalized.filter { it.resolved }.forEach { item ->
            val inputKey = item.input_name.lowercase()
            val canonical = item.canonical_name
            val exists = inputKey in existingInputs || (canonical != null && canonical in existingCanonicals)
            if (!exists) {
                session.medications += item
                existingInputs += inputKey
                if (canonical != null) existingCanonicals += canonical
            }
        }
    }

    private suspend fun buildDeterministicFallback(session: ChatSession, effectLimit: Int): String {
        val repo = repository
        val report = if (repo != null) {
            runCatching { repo.buildStructuredReport(session.medicationInputs(), effectLimit = effectLimit) }
                .onSuccess { session.lastReport = it }
                .getOrNull()
        } else {
            session.lastReport
        }
        return if (report != null) {
            deterministicTextFromReport(report)
        } else {
            "I could not finish that turn and I do not have a local report to fall back on. Please try again."
        }
    }

    private fun verifiedFinalText(modelText: String, report: MedicationSafetyReport?): String {
        if (report == null) return modelText
        val lower = modelText.lowercase()
        val contradictsFinding = listOf(
            "did not find a flagged interaction",
            "didn't find a flagged interaction",
            "no flagged interaction",
            "not find any flagged interaction",
            "no specific flagged interaction",
            "no known interaction",
        ).any { it in lower }
        val omitsDuplicateWarning = report.duplicate_ingredient_warnings.isNotEmpty() &&
            report.duplicate_ingredient_warnings.none { warning -> warning.ingredient.lowercase() in lower }
        return if ((report.findings.isNotEmpty() && contradictsFinding) || omitsDuplicateWarning) {
            deterministicTextFromReport(report)
        } else {
            modelText
        }
    }

    private fun textTranscript(messages: List<AgentMessage>): List<AgentMessage> =
        messages.filter { it.role == "user" || it.role == "assistant" }
            .takeLast(12)

    private fun userContent(message: String, current: List<String>): String = buildString {
        append(message)
        if (current.isNotEmpty()) append("\n\nCurrent session medications: ${current.joinToString(", ")}")
    }

    private fun compactToolContent(toolName: String, content: String): String {
        val compact = when (toolName) {
            "build_structured_report" -> compactStructuredReport(content)
            "lookup_pair" -> compactKnownInteraction(content)
            "list_interactions_for_drug" -> compactInteractionList(content)
            "normalize_medications", "add_medications" -> compactNormalizedList(content)
            "search_drug_aliases" -> compactAliasSearch(content)
            else -> content
        }
        return compact.limitForModel()
    }

    private fun compactStructuredReport(content: String): String = runCatching {
        val root = json.parseToJsonElement(content).jsonObject
        json.encodeToString(
            JsonObject.serializer(),
            buildJsonObject {
                copy(root, "input_medications")
                copy(root, "checked_pair_count")
                copy(root, "overall_severity")
                copy(root, "evidence_status")
                put(
                    "unresolved_medications",
                    compactNormalizedArray(root["unresolved_medications"], limit = 4),
                )
                put(
                    "findings",
                    buildJsonArray {
                        root["findings"]?.jsonArrayOrEmpty()?.take(3)?.forEach { item ->
                            add(compactInteractionObject(item.jsonObject))
                        }
                    },
                )
                put(
                    "duplicate_ingredient_warnings",
                    root["duplicate_ingredient_warnings"]?.jsonArrayOrEmpty()?.take(4)?.let { JsonArray(it) }
                        ?: JsonArray(emptyList()),
                )
            },
        )
    }.getOrElse { content }

    private fun compactKnownInteraction(content: String): String = runCatching {
        val root = json.parseToJsonElement(content).jsonObject
        json.encodeToString(JsonObject.serializer(), compactInteractionObject(root))
    }.getOrElse { content }

    private fun compactInteractionList(content: String): String = runCatching {
        val root = json.parseToJsonElement(content).jsonObject
        json.encodeToString(
            JsonObject.serializer(),
            buildJsonObject {
                put("normalized", compactNormalizedObject(root["normalized"]?.jsonObjectOrNull()))
                put(
                    "interactions",
                    buildJsonArray {
                        root["interactions"]?.jsonArrayOrEmpty()?.take(8)?.forEach { item ->
                            add(compactInteractionObject(item.jsonObject))
                        }
                    },
                )
            },
        )
    }.getOrElse { content }

    private fun compactNormalizedList(content: String): String = runCatching {
        val items = json.parseToJsonElement(content).jsonArray
        json.encodeToString(JsonArray.serializer(), compactNormalizedArray(JsonArray(items), limit = 8))
    }.getOrElse { content }

    private fun compactAliasSearch(content: String): String = runCatching {
        val items = json.parseToJsonElement(content).jsonArray
        json.encodeToString(
            JsonArray.serializer(),
            buildJsonArray {
                items.take(5).forEach { item ->
                    val obj = item.jsonObject
                    add(
                        buildJsonObject {
                            copy(obj, "canonical")
                            put("aliases", obj["aliases"]?.jsonArrayOrEmpty()?.take(6)?.let { JsonArray(it) } ?: JsonArray(emptyList()))
                        },
                    )
                }
            },
        )
    }.getOrElse { content }

    private fun compactInteractionObject(obj: JsonObject): JsonObject = buildJsonObject {
        copy(obj, "found")
        copy(obj, "drug_a")
        copy(obj, "drug_b")
        copy(obj, "severity")
        copy(obj, "row_count")
        put("effects", compactEffects(obj["effects"], limit = 5))
        copyLimitedArray(obj, "source_regions", 4)
        copyLimitedArray(obj, "source_bases", 4)
        copyLimitedArray(obj, "source_urls", 9)
        copyLimitedArray(obj, "mechanisms", 3)
        copyLimitedArray(obj, "risk_flags", 4)
        copyLimitedArray(obj, "evidence_bases", 4)
        obj["practical_guidance"]?.jsonObjectOrNull()?.let { guidance ->
            put(
                "practical_guidance",
                buildJsonObject {
                    copy(guidance, "rule_id")
                    copy(guidance, "practical_risk_tier")
                    copy(guidance, "practical_summary")
                    copy(guidance, "dose_context_needed")
                    copy(guidance, "risk_factor_questions")
                    copyLimitedArray(guidance, "source_urls", 4)
                },
            )
        }
    }

    private fun compactEffects(element: JsonElement?, limit: Int): JsonArray = buildJsonArray {
        element?.jsonArrayOrEmpty()?.take(limit)?.forEach { item ->
            val obj = item.jsonObject
            add(
                buildJsonObject {
                    copy(obj, "adverse_effect")
                    copy(obj, "severity")
                    copy(obj, "row_count")
                    copyLimitedArray(obj, "source_regions", 4)
                },
            )
        }
    }

    private fun compactNormalizedArray(element: JsonElement?, limit: Int): JsonArray = buildJsonArray {
        element?.jsonArrayOrEmpty()?.take(limit)?.forEach { item ->
            add(compactNormalizedObject(item.jsonObjectOrNull()))
        }
    }

    private fun compactNormalizedObject(obj: JsonObject?): JsonObject = buildJsonObject {
        if (obj == null) return@buildJsonObject
        copy(obj, "input_name")
        copy(obj, "canonical_name")
        copy(obj, "matched_alias")
        copy(obj, "resolved")
    }

    private fun JsonObjectBuilder.copy(source: JsonObject, key: String) {
        source[key]?.let { put(key, it) }
    }

    private fun JsonObjectBuilder.copyLimitedArray(source: JsonObject, key: String, limit: Int) {
        val values = source[key]?.jsonArrayOrEmpty()?.take(limit) ?: return
        put(key, JsonArray(values))
    }

    private fun JsonElement.jsonArrayOrEmpty(): List<JsonElement> =
        (this as? JsonArray)?.toList() ?: emptyList()

    private fun JsonElement.jsonObjectOrNull(): JsonObject? = this as? JsonObject

    private fun String.limitForModel(): String =
        if (length <= TOOL_RESULT_CHAR_LIMIT) this else take(TOOL_RESULT_CHAR_LIMIT) + "\n[truncated]"

    private companion object {
        const val TOOL_RESULT_CHAR_LIMIT = 3600
    }
}

private fun explicitMedicationCandidates(message: String): List<String> {
    val compact = message.replace('\n', ' ').replace(Regex("\\s+"), " ").trim()
    if (compact.isBlank()) return emptyList()
    if (compact.startsWith("The user attached", ignoreCase = true)) return emptyList()
    if (!Regex("(?i)\\b(and|with|plus)\\b|[+&]").containsMatchIn(compact)) return emptyList()

    return compact
        .split(Regex("(?i)\\s+(?:and|with|plus)\\s+|\\s*[+&]\\s*"))
        .mapNotNull { explicitMedicationPiece(it) }
        .distinctBy { it.lowercase() }
        .take(4)
}

private fun explicitMedicationPiece(raw: String): String? {
    var value = raw
        .replace(Regex("(?i)\\b(current session medications|user question/context|user intent)\\b.*$"), "")
        .replace(Regex("^[\\s,:;?.!]+|[\\s,:;?.!]+$"), "")
        .trim()
    value = value
        .replace(
            Regex(
                "(?i)^(what\\s+about|what\\s+if\\s+i\\s+take|can\\s+i\\s+take|can\\s+you\\s+take|is\\s+it\\s+(?:ok|okay)\\s+to\\s+take|if\\s+i\\s+take|i\\s+take|taking|take)\\s+",
            ),
            "",
        )
        .replace(Regex("(?i)\\b(?:safe|okay|ok|together|interaction|interactions)\\b"), " ")
        .replace(Regex("\\s+"), " ")
        .trim()
        .trim('-', ':', ',', '.', '?', '!')
        .trim()
    return value.takeIf { candidate ->
        candidate.length in 3..60 && candidate.any { it.isLetter() }
    }
}

internal fun deterministicTextFromReport(report: MedicationSafetyReport): String {
    val lines = mutableListOf<String>()
    val unresolvedNames = report.unresolved_medications.joinToString(", ") { it.input_name }

    if (report.duplicate_ingredient_warnings.isNotEmpty()) {
        report.duplicate_ingredient_warnings.take(3).forEach { warning ->
            lines += warning.practical_summary
            if (warning.dose_context_needed.isNotBlank()) {
                lines += warning.dose_context_needed
            }
            lines += ""
        }
    }

    if (report.findings.isNotEmpty()) {
        if (report.findings.size == 1) {
            val finding = report.findings.first()
            val severity = finding.severity ?: "flagged"
            lines += "This combination is flagged as $severity: ${finding.drug_a} with ${finding.drug_b}."
        } else {
            lines += "I found ${report.findings.size} flagged interaction(s) in the medicines checked."
        }
        report.findings.take(3).forEach { finding ->
            lines += ""
            lines += deterministicFindingLines(finding, includeLead = report.findings.size != 1)
            val sourceLines = sourceLinesFromFinding(finding)
            if (sourceLines.isNotEmpty()) {
                lines += ""
                lines += "Sources:"
                lines += sourceLines
            } else {
                lines += ""
                lines += "Sources:"
                lines += "- ${finding.drug_a} + ${finding.drug_b}: no URL on file"
            }
        }
        if (report.findings.size > 3) {
            lines += ""
            lines += "There are ${report.findings.size - 3} more findings available. Ask for details and I can walk through them."
        }
    } else if (report.checked_pair_count > 0) {
        lines += "I did not find a flagged interaction among the medicines I could identify in the evidence I checked."
    }

    if (report.unresolved_medications.isNotEmpty()) {
        if (lines.isNotEmpty()) lines += ""
        lines += "I could not identify these confidently, so I did not check them: $unresolvedNames."
    }

    return lines.joinToString("\n").trim()
}

private fun deterministicFindingLines(finding: KnownInteraction, includeLead: Boolean = true): List<String> {
    val drugA = finding.drug_a
    val drugB = finding.drug_b
    val severity = finding.severity ?: "flagged"
    val effects = finding.effects.take(3).map { it.adverse_effect }

    val lines = mutableListOf<String>()
    if (includeLead) {
        lines += "$drugA with $drugB is flagged as $severity."
    }
    if (effects.isNotEmpty()) {
        lines += "The main concern is ${effects.joinToString(", ")}."
        plainEffectNote(effects.first())?.let { lines += it }
    }
    finding.practical_guidance?.let { guidance ->
        if (guidance.practical_summary.isNotBlank()) {
            lines += guidance.practical_summary
        }
        if (guidance.dose_context_needed.isNotBlank()) {
            lines += guidance.dose_context_needed
        }
    }
    if (severity.equals("Major", ignoreCase = true)) {
        lines += "Because this is marked Major, check with a pharmacist or prescriber before taking them together."
    } else if (effects.isNotEmpty()) {
        lines += "If you are using them together, keep an eye on those symptoms and ask a pharmacist if they show up."
    }
    return lines
}

private fun plainEffectNote(effectName: String): String? {
    val normalized = effectName.lowercase()
    return when {
        "gastrointestinal bleeding" in normalized ->
            "In plain language, gastrointestinal bleeding means bleeding in the stomach or intestines."
        "intracranial hemorrhage" in normalized ->
            "In plain language, intracranial hemorrhage means bleeding inside the skull."
        "qt prolongation" in normalized ->
            "In plain language, QT prolongation is an electrical heart-rhythm change that can become dangerous in some people."
        "torsades" in normalized ->
            "In plain language, torsades de pointes is a dangerous abnormal heart rhythm."
        "acute anemia" in normalized ->
            "In plain language, acute anemia means a sudden drop in red blood cells or hemoglobin."
        else -> null
    }
}

private fun sourceLinesFromFinding(finding: KnownInteraction): List<String> {
    val urls = finding.source_urls.take(20)
    if (urls.isEmpty()) return emptyList()
    val regions = finding.source_regions.take(4)
    val bases = compactBasisItems(finding.source_bases, 3)
    val metaParts = mutableListOf<String>()
    if (regions.isNotEmpty()) metaParts += "regions: ${regions.joinToString(", ")}"
    if (bases.isNotEmpty()) metaParts += "basis: ${bases.joinToString("; ")}"
    val meta = if (metaParts.isEmpty()) "" else " (${metaParts.joinToString("; ")})"
    val visible = urls.take(3).map { "- ${finding.drug_a} + ${finding.drug_b}: $it$meta" }
    return if (urls.size > 3) {
        visible + "- ${finding.drug_a} + ${finding.drug_b}: ${urls.size - 3} more source URL(s) on file."
    } else {
        visible
    }
}

private fun compactBasisItems(values: List<String>, limit: Int): List<String> {
    val seen = mutableListOf<String>()
    for (raw in values) {
        for (piece in raw.split(";")) {
            val item = piece.trim()
            if (item.isNotEmpty() && item !in seen) {
                seen += item
                if (seen.size >= limit) return seen
            }
        }
    }
    return seen
}
