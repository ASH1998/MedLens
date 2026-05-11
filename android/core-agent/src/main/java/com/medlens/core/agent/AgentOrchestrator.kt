package com.medlens.core.agent

import com.medlens.core.agent.model.AgentMessage
import com.medlens.core.agent.model.AgentTurnResult
import com.medlens.core.agent.model.ChatSession
import com.medlens.core.agent.model.NativeToolProvider
import com.medlens.core.data.SafetyRepository
import com.medlens.core.data.util.normalizeLookupText

class AgentOrchestrator(
    private val store: SafetyRepository,
    private val dispatcher: ToolDispatcher,
    private val provider: NativeToolProvider?,
) {
    suspend fun runTurn(
        session: ChatSession,
        userMessage: String,
        audiencePrompt: String? = null,
        effectLimit: Int = 5,
        maxRounds: Int = 6,
        maxToolCallsPerRound: Int = 4,
    ): AgentTurnResult {
        session.clearTurnTrace()
        val priorMessages = textTranscript(session.transcript)
        val messages = priorMessages.toMutableList()
        messages += AgentMessage(
            role = "user",
            content = userContent(userMessage, session.medicationInputs()),
        )
        val systemPrompt = audiencePrompt?.let { "$TOOL_LOOP_SYSTEM_PROMPT\n\nAudience style:\n$it" }
            ?: TOOL_LOOP_SYSTEM_PROMPT

        var finalText = ""
        var fallbackUsed = provider == null

        val activeProvider = provider
        if (activeProvider != null) {
            for (round in 0 until maxRounds) {
                val response = try {
                    activeProvider.generateWithTools(systemPrompt, messages, TOOL_SCHEMAS)
                } catch (_: Throwable) {
                    fallbackUsed = true
                    break
                }
                finalText = response.text.trim()
                if (response.tool_calls.isEmpty()) break

                val limited = response.tool_calls.take(maxToolCallsPerRound)
                messages += AgentMessage(
                    role = "assistant",
                    content = finalText,
                )
                for (call in limited) {
                    val args = call.args.toMutableMap()
                    if (call.name == "build_structured_report" && !args.containsKey("limit")) {
                        args["limit"] = effectLimit.toString()
                    }
                    val result = dispatcher.dispatch(call.name, args, session)
                    messages += AgentMessage(
                        role = "tool",
                        content = result["json"] ?: result["text"] ?: result["error"].orEmpty(),
                    )
                }
                if (round == maxRounds - 1) fallbackUsed = true
            }
        }

        if (fallbackUsed || finalText.isBlank()) {
            finalText = deterministicFallback(session, userMessage, effectLimit)
        }

        session.transcript.clear()
        session.transcript += priorMessages
        session.transcript += AgentMessage(role = "user", content = userMessage)
        session.transcript += AgentMessage(role = "assistant", content = finalText)

        return AgentTurnResult(
            finalText = finalText,
            trace = session.lastTrace.toList(),
            report = session.lastReport,
            usedTools = session.lastTrace.map { it.name },
            providerName = provider?.name ?: "deterministic-fallback",
            fallbackUsed = fallbackUsed,
        )
    }

    private suspend fun deterministicFallback(
        session: ChatSession,
        userMessage: String,
        effectLimit: Int,
    ): String {
        val broadDrug = anchoredDrugQuestion(userMessage)
        if (broadDrug != null) {
            val payload = dispatcher.dispatch(
                name = "list_interactions_for_drug",
                args = mapOf("drug" to broadDrug),
                session = session,
            )
            return payload["json"] ?: payload["text"] ?: "No local interactions found."
        }

        val profileDrug = medicineProfileQuestion(userMessage)
        if (profileDrug != null) {
            val payload = dispatcher.dispatch(
                name = "get_common_medicine_profile",
                args = mapOf("name" to profileDrug),
                session = session,
            )
            return payload["json"] ?: payload["text"] ?: "No local medicine profile found."
        }

        val extractedMeds = extractMedicationCandidates(userMessage)
        val resolved = resolveMedicationCandidates(extractedMeds)
        if (resolved.isNotEmpty()) {
            syncSessionMedications(session, resolved)
        }

        val reportNames = session.medicationInputs().ifEmpty { extractedMeds }
        if (reportNames.size >= 2) {
            dispatcher.dispatch(
                name = "build_structured_report",
                args = mapOf("medication_names" to reportNames.joinToString("|"), "limit" to effectLimit.toString()),
                session = session,
            )
            session.lastReport?.let { return deterministicTextFromReport(it) }
        }

        return "Tell me the medicines you want checked, for example: Advil and Warfarin."
    }

    private fun textTranscript(messages: List<AgentMessage>): List<AgentMessage> =
        messages.filter { it.role == "user" || it.role == "assistant" }
            .takeLast(12)

    private fun userContent(message: String, current: List<String>): String = buildString {
        append(message)
        if (current.isNotEmpty()) append("\n\nCurrent session medications: ${current.joinToString(", ")}")
    }

    private fun anchoredDrugQuestion(message: String): String? {
        val patterns = listOf(
            Regex("(?i)what medicines .* with ([a-z0-9\\- ]+)"),
            Regex("(?i)interact(?:ions)? with ([a-z0-9\\- ]+)"),
            Regex("(?i)cant be taken with ([a-z0-9\\- ]+)"),
        )
        return patterns.firstNotNullOfOrNull { regex ->
            regex.find(message)?.groupValues?.getOrNull(1)?.trim()?.trimEnd('?', '.', '!')
        }
    }

    private fun medicineProfileQuestion(message: String): String? {
        val patterns = listOf(
            Regex("(?i)what is ([a-z0-9\\- ]+)"),
            Regex("(?i)tell me about ([a-z0-9\\- ]+)"),
        )
        return patterns.firstNotNullOfOrNull { regex ->
            regex.find(message)?.groupValues?.getOrNull(1)?.trim()?.trimEnd('?', '.', '!')
        }
    }

    private suspend fun resolveMedicationCandidates(candidates: List<String>) = buildList {
        for (candidate in candidates) {
            val normalized = store.normalizeMedications(listOf(candidate)).first()
            if (normalized.resolved) {
                add(normalized)
                continue
            }
            val aliasMatch = store.searchDrugAliases(candidate, 1).firstOrNull()
            if (aliasMatch != null) {
                add(store.normalizeMedications(listOf(aliasMatch.canonical)).first())
            } else {
                add(normalized)
            }
        }
    }

    private fun extractMedicationCandidates(message: String): List<String> {
        val explicit = message
            .split(Regex("(?i)\\s*(?:\\+|,|;|\\band\\b|\\bwith\\b)\\s*"))
            .map { it.replace(Regex("[^A-Za-z0-9 -]"), " ").replace(Regex("\\s+"), " ").trim() }
            .filter { it.length >= 3 }
            .filterNot { STOP_WORDS.contains(normalizeLookupText(it)) }
        if (explicit.size >= 2) return explicit.distinct()

        val cleaned = message
            .replace(Regex("[^A-Za-z0-9 ]"), " ")
            .replace(Regex("\\s+"), " ")
            .trim()
        if (cleaned.isBlank()) return emptyList()
        val words = cleaned.split(" ")
            .filter { it.isNotBlank() }
            .filterNot { STOP_WORDS.contains(normalizeLookupText(it)) }

        val candidates = linkedSetOf<String>()
        for (window in 2 downTo 1) {
            for (index in 0..words.size - window) {
                val phrase = words.subList(index, index + window).joinToString(" ")
                if (phrase.length >= 3) candidates += phrase
            }
        }
        return candidates.toList()
    }

    private companion object {
        val STOP_WORDS = setOf(
            "i", "take", "and", "is", "that", "okay", "what", "with", "can", "be",
            "taken", "about", "tell", "me", "my", "medicines", "medicine", "the",
        )
    }
}
