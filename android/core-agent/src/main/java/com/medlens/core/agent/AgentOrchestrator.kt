package com.medlens.core.agent

import android.util.Log as AndroidLog
import com.medlens.core.agent.model.AgentMessage
import com.medlens.core.agent.model.AgentTurnResult
import com.medlens.core.agent.model.ChatSession
import com.medlens.core.agent.model.NativeToolProvider
import com.medlens.core.data.SafetyRepository
import com.medlens.core.data.model.MedicationSafetyReport
import com.medlens.core.data.model.NormalizedMedication
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonObjectBuilder
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

private const val DEFAULT_MAX_TOOL_ROUNDS = 5
private const val DEFAULT_TURN_TIME_BUDGET_MILLIS = 120_000L

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
        maxRounds: Int = DEFAULT_MAX_TOOL_ROUNDS,
        timeBudgetMillis: Long = DEFAULT_TURN_TIME_BUDGET_MILLIS,
    ): AgentTurnResult {
        val turnStart = System.currentTimeMillis()
        Log.i(TAG, "==== runTurn START ====")
        Log.i(TAG, "userMessage (${userMessage.length} chars): ${userMessage.take(300)}")
        Log.i(TAG, "audiencePrompt: ${audiencePrompt?.take(80) ?: "<none>"}")
        Log.i(TAG, "effectLimit=$effectLimit maxRounds=$maxRounds timeBudgetMs=$timeBudgetMillis")
        Log.i(TAG, "prior session: ${session.medications.size} meds, ${session.transcript.size} transcript msgs, lastReport=${session.lastReport != null}")

        session.clearTurnTrace()
        val priorTranscript = textTranscript(session.transcript)
        val systemPrompt = audiencePrompt?.let { "$TOOL_LOOP_SYSTEM_PROMPT\n\nAudience style:\n$it" }
            ?: TOOL_LOOP_SYSTEM_PROMPT
        Log.i(TAG, "systemPrompt size=${systemPrompt.length} chars; priorTranscript truncated to ${priorTranscript.size} msgs")

        val activeProvider = provider ?: run {
            Log.e(TAG, "no provider configured — throwing")
            throw IllegalStateException("Gemma is not ready yet.")
        }
        Log.i(TAG, "provider=${activeProvider.name}")

        val currentTurnReport = runCatching {
            precheckCurrentTurnReport(session, userMessage, effectLimit)
        }.onFailure { Log.w(TAG, "precheck failed: ${it.message}", it) }.getOrNull()
        if (currentTurnReport != null) {
            Log.i(TAG, "precheck report: severity=${currentTurnReport.overall_severity} findings=${currentTurnReport.findings.size} resolved=${currentTurnReport.normalized_medications.count { it.resolved }} unresolved=${currentTurnReport.unresolved_medications.size}")
        } else {
            Log.i(TAG, "precheck: no report (no explicit pair detected or no repository)")
        }

        var finalText = ""
        try {
            withTimeout(timeBudgetMillis) {
                Log.i(TAG, "startTurn — opening LiteRT conversation")
                activeProvider.startTurn(systemPrompt, priorTranscript).use { turnSession ->
                    Log.i(TAG, "startTurn — conversation opened, entering protocol loop")
                    finalText = runProtocolLoop(
                        turnSession = turnSession,
                        session = session,
                        userMessage = userMessage,
                        currentTurnReport = currentTurnReport,
                        effectLimit = effectLimit,
                        maxRounds = maxRounds,
                    )
                }
                Log.i(TAG, "startTurn — conversation closed")
            }
        } catch (t: TimeoutCancellationException) {
            Log.w(TAG, "runTurn TIMEOUT after ${System.currentTimeMillis() - turnStart}ms (budget=${timeBudgetMillis}ms)")
            finalText = ""
        } catch (t: Throwable) {
            Log.e(TAG, "runTurn threw ${t::class.java.simpleName}: ${t.message}", t)
            finalText = ""
        }

        if (finalText.isBlank()) {
            Log.w(TAG, "finalText is blank — using HARD_FAILURE_MESSAGE")
            finalText = HARD_FAILURE_MESSAGE
        } else {
            Log.i(TAG, "finalText OK (${finalText.length} chars): ${finalText.take(200)}")
        }
        Log.i(TAG, "==== runTurn END (${System.currentTimeMillis() - turnStart}ms total) ====")

        val reportForResult = currentTurnReport ?: session.lastReport

        session.transcript.clear()
        session.transcript += priorTranscript
        session.transcript += AgentMessage(role = "user", content = userMessage)
        session.transcript += AgentMessage(role = "assistant", content = finalText)

        return AgentTurnResult(
            finalText = finalText,
            trace = session.lastTrace.toList(),
            report = reportForResult,
            usedTools = session.lastTrace.map { it.name },
            providerName = activeProvider.name,
        )
    }

    private suspend fun runProtocolLoop(
        turnSession: com.medlens.core.agent.model.TurnSession,
        session: ChatSession,
        userMessage: String,
        currentTurnReport: MedicationSafetyReport?,
        effectLimit: Int,
        maxRounds: Int,
    ): String {
        val initialMessage = composeInitialUserMessage(
            userMessage = userMessage,
            session = session,
            seededReport = currentTurnReport,
        )
        Log.i(TAG, "initial LLM message (${initialMessage.length} chars):\n${initialMessage.take(800)}")
        var nextMessage = initialMessage
        var roundsRemaining = maxRounds
        var round = 0

        while (roundsRemaining > 0) {
            roundsRemaining -= 1
            round += 1
            Log.i(TAG, "---- round $round: sending to LLM (${nextMessage.length} chars) ----")
            val roundStart = System.currentTimeMillis()
            val reply = runCatching { turnSession.sendMessage(nextMessage) }
                .onFailure { Log.e(TAG, "round $round sendMessage failed: ${it::class.java.simpleName}: ${it.message}", it) }
                .getOrNull()
                .orEmpty()
            Log.i(TAG, "round $round LLM reply (${reply.length} chars, took ${System.currentTimeMillis() - roundStart}ms):\n${reply.take(800)}")
            val parsed = parseProtocol(reply)
            Log.i(TAG, "round $round parsed as: ${parsed::class.java.simpleName}")
            when (parsed) {
                is ProtocolReply.Answer -> {
                    Log.i(TAG, "round $round -> ANSWER (${parsed.text.length} chars)")
                    return parsed.text
                }
                is ProtocolReply.Ask -> {
                    Log.i(TAG, "round $round -> ASK: ${parsed.text}")
                    val report = session.lastReport ?: currentTurnReport
                    if (shouldSuppressReportClarification(parsed.text, report)) {
                        Log.w(TAG, "round $round ASK requested data already present in report; forcing final ANSWER")
                        return forceAnswerFromReport(turnSession, report!!, parsed.text)
                    }
                    return parsed.text
                }
                is ProtocolReply.Calls -> {
                    Log.i(TAG, "round $round -> CALLS: ${parsed.calls.joinToString { "${it.name}(${it.args})" }}")
                    val toolResult = dispatchCalls(parsed.calls, session, effectLimit)
                    nextMessage = formatToolResultMessage(parsed.calls, toolResult)
                    Log.i(TAG, "round $round next message (${nextMessage.length} chars):\n${nextMessage.take(800)}")
                }
                is ProtocolReply.Unstructured -> {
                    val report = session.lastReport ?: currentTurnReport
                    if (shouldSuppressReportClarification(parsed.text, report)) {
                        Log.w(TAG, "round $round unstructured reply requested data already present in report; forcing final ANSWER")
                        return forceAnswerFromReport(turnSession, report!!, parsed.text)
                    }
                    Log.w(TAG, "round $round -> UNSTRUCTURED (no verb detected). Using raw text as answer.")
                    return parsed.text
                }
            }
        }

        Log.w(TAG, "loop exhausted after $round rounds — sending wrap-up to model")
        val wrapUp = buildString {
            append("You have used your tool budget for this turn.\n")
            append("Reply now with one ANSWER: <pharmacist reply> based on what you already learned. ")
            append("Do not emit another CALL.")
        }
        val wrapStart = System.currentTimeMillis()
        val finalReply = runCatching { turnSession.sendMessage(wrapUp) }
            .onFailure { Log.e(TAG, "wrap-up sendMessage failed: ${it::class.java.simpleName}: ${it.message}", it) }
            .getOrNull()
            .orEmpty()
        Log.i(TAG, "wrap-up reply (${finalReply.length} chars, took ${System.currentTimeMillis() - wrapStart}ms):\n${finalReply.take(800)}")
        return when (val parsed = parseProtocol(finalReply)) {
            is ProtocolReply.Answer -> {
                Log.i(TAG, "wrap-up -> ANSWER")
                parsed.text
            }
            is ProtocolReply.Ask -> {
                Log.i(TAG, "wrap-up -> ASK")
                val report = session.lastReport ?: currentTurnReport
                if (shouldSuppressReportClarification(parsed.text, report)) {
                    Log.w(TAG, "wrap-up ASK requested data already present in report; using report fallback")
                    return reportFallbackText(report!!)
                }
                parsed.text
            }
            is ProtocolReply.Unstructured -> {
                Log.w(TAG, "wrap-up -> UNSTRUCTURED, using raw text")
                parsed.text
            }
            is ProtocolReply.Calls -> {
                Log.e(TAG, "wrap-up STILL emitted CALL — giving up on this turn")
                ""
            }
        }
    }

    private suspend fun forceAnswerFromReport(
        turnSession: com.medlens.core.agent.model.TurnSession,
        report: MedicationSafetyReport,
        rejectedQuestion: String,
    ): String {
        val prompt = buildString {
            append("Do not ask that clarification question. It is already answered by the structured report.\n")
            append("Rejected question: ")
            append(rejectedQuestion)
            append("\n\nStructured report to use:\n")
            append(compactStructuredReport(json.encodeToString(MedicationSafetyReport.serializer(), report)))
            append("\n\nReply now with ANSWER: <pharmacist reply> only. ")
            append("Use the normalized/canonical medicines from normalized_medications as the active ingredients. ")
            append("If some raw product names are unresolved, mention only those as not checked; do not ask for active ingredients for resolved medicines.")
            append(" Keep it human: use **Bottom line:**, brief reason, no row counts, no report-like wording.")
        }
        val reply = runCatching { turnSession.sendMessage(prompt) }
            .onFailure { Log.e(TAG, "force-answer sendMessage failed: ${it::class.java.simpleName}: ${it.message}", it) }
            .getOrNull()
            .orEmpty()
        Log.i(TAG, "force-answer reply (${reply.length} chars):\n${reply.take(800)}")
        return when (val parsed = parseProtocol(reply)) {
            is ProtocolReply.Answer -> parsed.text
            is ProtocolReply.Unstructured -> parsed.text.ifBlank { reportFallbackText(report) }
            is ProtocolReply.Ask,
            is ProtocolReply.Calls -> reportFallbackText(report)
        }
    }

    private suspend fun dispatchCalls(
        calls: List<ParsedCall>,
        session: ChatSession,
        effectLimit: Int,
    ): List<Pair<ParsedCall, String>> {
        val results = mutableListOf<Pair<ParsedCall, String>>()
        for ((index, call) in calls.withIndex()) {
            val args = call.args.toMutableMap()
            if (call.name == "build_structured_report" && !args.containsKey("limit")) {
                args["limit"] = effectLimit.toString()
            }
            Log.i(TAG, "TOOL [${index + 1}/${calls.size}] dispatch: ${call.name} args=$args")
            val toolStart = System.currentTimeMillis()
            val dispatched = dispatcher.dispatch(call.name, args, session)
            val toolMs = System.currentTimeMillis() - toolStart
            val rawContent = dispatched["json"] ?: dispatched["text"] ?: dispatched["error"].orEmpty()
            val hasError = dispatched.containsKey("error")
            if (hasError) {
                Log.w(TAG, "TOOL ${call.name} returned error in ${toolMs}ms: ${dispatched["error"]}")
            } else {
                Log.i(TAG, "TOOL ${call.name} OK in ${toolMs}ms, raw ${rawContent.length} chars: ${rawContent.take(300)}")
            }
            val compact = compactToolContent(call.name, rawContent)
            Log.i(TAG, "TOOL ${call.name} compacted to ${compact.length} chars: ${compact.take(300)}")
            results += call to compact
        }
        return results
    }

    private fun formatToolResultMessage(
        calls: List<ParsedCall>,
        results: List<Pair<ParsedCall, String>>,
    ): String = buildString {
        append("TOOL_RESULT")
        if (results.size == 1) {
            val (call, content) = results.first()
            append(" for ").append(call.name).append(":\n")
            append(content)
        } else {
            append(":\n")
            results.forEachIndexed { index, (call, content) ->
                append("\n[").append(index + 1).append("] ").append(call.name).append("\n")
                append(content)
                append("\n")
            }
        }
        append("\n\nNow reply with CALL: ..., ASK: ..., or ANSWER: ...")
        if (calls.any { it.name == "build_structured_report" }) {
            append("\nFor build_structured_report, normalized_medications already contains the active ingredients/canonical names. Do not ASK for active ingredients for resolved medicines; ANSWER using the report.")
            append("\nWrite the ANSWER like a pharmacist talking to a patient: practical first sentence, brief reason, no row counts, no report-like wording.")
            append("\nUse light markdown emphasis that the app can render: **Bottom line:**, **Why:**, **Watch for:**, and bold the severity only when it matters.")
            append("\nFor no flagged local finding, do not lead with \"I don't see\" or \"I did not find\". Give a practical general-medication answer from your clinical knowledge, with **Bottom line:** and **Watch for:**. Do not invent a flagged interaction, severity, or source.")
            append("\nFor Major findings, start with **Bottom line:**, say you would be careful with this pair, bold **Major**, explain the concern plainly, then include Sources only if URLs were returned.")
        }
    }

    private fun composeInitialUserMessage(
        userMessage: String,
        session: ChatSession,
        seededReport: MedicationSafetyReport?,
    ): String = buildString {
        append(userMessage)
        val current = session.medicationInputs()
        if (current.isNotEmpty()) {
            append("\n\nSession medications already on file: ")
            append(current.joinToString(", "))
        }
        if (seededReport != null) {
            // Hint the model that the names are pre-canonicalized; it can skip normalize and go
            // straight to build_structured_report. We do NOT paste the full report into the
            // prompt — that bloats Gemma 3n's context. Let the tool call return it.
            val canonical = seededReport.normalized_medications.filter { it.resolved }
                .mapNotNull { it.canonical_name }
                .distinct()
            if (canonical.isNotEmpty()) {
                append("\n\nPre-resolved canonical names for this question: ")
                append(canonical.joinToString(", "))
                append("\nGo straight to CALL: build_structured_report with these names.")
            }
        }
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

    private fun textTranscript(messages: List<AgentMessage>): List<AgentMessage> =
        messages.filter { it.role == "user" || it.role == "assistant" }
            .takeLast(12)

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
                put(
                    "normalized_medications",
                    compactNormalizedArray(root["normalized_medications"], limit = 10),
                )
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

    private fun shouldSuppressReportClarification(
        text: String,
        report: MedicationSafetyReport?,
    ): Boolean {
        if (report == null || !report.hasUsableResolvedReport()) return false
        val lower = text.lowercase()
        return "active ingredient" in lower ||
            "active ingredients" in lower ||
            "ingredient" in lower ||
            "ingredients" in lower ||
            "composition" in lower
    }

    private fun MedicationSafetyReport.hasUsableResolvedReport(): Boolean =
        checked_pair_count > 0 ||
            findings.isNotEmpty() ||
            duplicate_ingredient_warnings.isNotEmpty() ||
            normalized_medications.count { it.resolved && it.canonical_name != null } >= 2

    private fun reportFallbackText(report: MedicationSafetyReport): String {
        val resolved = report.normalized_medications
            .filter { it.resolved }
            .mapNotNull { it.canonical_name }
            .distinct()
        val unresolved = report.unresolved_medications.map { it.input_name }.distinct()
        val lines = mutableListOf<String>()
        if (resolved.isNotEmpty()) {
            lines += "I identified and checked: ${resolved.joinToString(", ")}."
        }
        if (report.findings.isNotEmpty()) {
            val finding = report.findings.first()
            val severity = finding.severity ?: "flagged"
            lines += "${finding.drug_a} with ${finding.drug_b} is flagged as $severity in the local evidence."
            val effects = finding.effects.take(3).map { it.adverse_effect }
            if (effects.isNotEmpty()) lines += "Main concern: ${effects.joinToString(", ")}."
        } else if (report.checked_pair_count > 0) {
            lines += "I did not find a flagged interaction among the medicines I could identify in the local evidence checked."
        }
        if (unresolved.isNotEmpty()) {
            lines += "I could not identify these confidently, so I did not check them: ${unresolved.joinToString(", ")}."
        }
        return lines.joinToString("\n\n").ifBlank { HARD_FAILURE_MESSAGE }
    }

    internal data class ParsedCall(val name: String, val args: Map<String, String>)

    internal sealed interface ProtocolReply {
        data class Answer(val text: String) : ProtocolReply
        data class Ask(val text: String) : ProtocolReply
        data class Calls(val calls: List<ParsedCall>) : ProtocolReply
        data class Unstructured(val text: String) : ProtocolReply
    }

    internal fun parseProtocol(raw: String): ProtocolReply {
        val text = stripMarkup(raw).trim()
        if (text.isEmpty()) return ProtocolReply.Unstructured("")

        val answerMatch = ANSWER_REGEX.find(text)
        val askMatch = ASK_REGEX.find(text)
        val callMatches = CALL_REGEX.findAll(text).toList()

        val earliest = listOfNotNull(
            answerMatch?.let { Verb.ANSWER to it.range.first },
            askMatch?.let { Verb.ASK to it.range.first },
            callMatches.firstOrNull()?.let { Verb.CALL to it.range.first },
        ).minByOrNull { it.second }

        return when (earliest?.first) {
            Verb.ANSWER -> {
                val captured = answerMatch!!.groupValues[1].trim()
                ProtocolReply.Answer(captured.ifEmpty { text })
            }
            Verb.ASK -> {
                val captured = askMatch!!.groupValues[1].trim()
                ProtocolReply.Ask(captured.ifEmpty { text })
            }
            Verb.CALL -> {
                val parsed = callMatches.mapNotNull { match ->
                    val name = match.groupValues[1].trim().lowercase()
                    val argsRaw = match.groupValues.getOrNull(2)?.trim().orEmpty()
                    if (name.isBlank()) null else ParsedCall(name = name, args = parseCallArgs(argsRaw))
                }
                if (parsed.isEmpty()) ProtocolReply.Unstructured(text) else ProtocolReply.Calls(parsed)
            }
            null -> ProtocolReply.Unstructured(text)
        }
    }

    private fun stripMarkup(raw: String): String {
        // Some small models wrap their reply in code fences or angle-bracket tokens.
        // Strip the most common noise so the verb regex can match cleanly.
        var text = raw
        text = text.replace(Regex("```[a-zA-Z0-9_-]*"), "")
        text = text.replace("```", "")
        text = text.replace(Regex("<\\|[^|>]*\\|?>"), "")
        text = text.replace(Regex("</?tool_call>", RegexOption.IGNORE_CASE), "")
        return text
    }

    private fun parseCallArgs(raw: String): Map<String, String> {
        if (raw.isBlank()) return emptyMap()
        return runCatching {
            val element = json.parseToJsonElement(raw)
            if (element !is JsonObject) return@runCatching emptyMap<String, String>()
            buildMap {
                element.forEach { (key, value) ->
                    put(key, jsonElementToArgString(value))
                }
            }
        }.getOrElse { emptyMap() }
    }

    private fun jsonElementToArgString(element: JsonElement): String = when (element) {
        is JsonPrimitive -> element.contentOrNull.orEmpty()
        is JsonArray -> json.encodeToString(JsonArray.serializer(), JsonArray(
            element.map { item ->
                when (item) {
                    is JsonPrimitive -> JsonPrimitive(item.contentOrNull.orEmpty())
                    else -> JsonPrimitive(item.toString())
                }
            },
        ))
        is JsonObject -> element.toString()
    }

    private enum class Verb { ANSWER, ASK, CALL }

    private companion object {
        const val TAG = "MedLens"
        const val TOOL_RESULT_CHAR_LIMIT = 3600
        const val HARD_FAILURE_MESSAGE = "I had trouble checking that just now. Please try again, or type the medicine names directly."

        val ANSWER_REGEX = Regex(
            """(?:^|\n)\s*ANSWER\s*[:\-]\s*([\s\S]+)""",
            setOf(RegexOption.IGNORE_CASE),
        )
        val ASK_REGEX = Regex(
            """(?:^|\n)\s*ASK\s*[:\-]\s*([^\n\r]+)""",
            setOf(RegexOption.IGNORE_CASE),
        )
        val CALL_REGEX = Regex(
            """(?:^|\n)\s*CALL\s*[:\-]\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*(\{[^\n}]*\})?""",
            setOf(RegexOption.IGNORE_CASE),
        )
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

private object Log {
    fun i(tag: String, message: String) {
        runCatching { AndroidLog.i(tag, message) }
            .onFailure { println("I/$tag: $message") }
    }

    fun w(tag: String, message: String) {
        runCatching { AndroidLog.w(tag, message) }
            .onFailure { println("W/$tag: $message") }
    }

    fun w(tag: String, message: String, throwable: Throwable) {
        runCatching { AndroidLog.w(tag, message, throwable) }
            .onFailure {
                println("W/$tag: $message")
                throwable.printStackTrace()
            }
    }

    fun e(tag: String, message: String) {
        runCatching { AndroidLog.e(tag, message) }
            .onFailure { println("E/$tag: $message") }
    }

    fun e(tag: String, message: String, throwable: Throwable) {
        runCatching { AndroidLog.e(tag, message, throwable) }
            .onFailure {
                println("E/$tag: $message")
                throwable.printStackTrace()
            }
    }
}
