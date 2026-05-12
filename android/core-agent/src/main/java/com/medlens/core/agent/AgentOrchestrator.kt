package com.medlens.core.agent

import com.medlens.core.agent.model.AgentMessage
import com.medlens.core.agent.model.AgentTurnResult
import com.medlens.core.agent.model.ChatSession
import com.medlens.core.agent.model.NativeToolProvider
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonObjectBuilder
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

class AgentOrchestrator(
    private val dispatcher: ToolDispatcher,
    private val provider: NativeToolProvider?,
) {
    private val json = Json { prettyPrint = false; ignoreUnknownKeys = true }

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
        val activeProvider = provider ?: throw IllegalStateException("Gemma is not ready yet.")

        for (round in 0 until maxRounds) {
            val response = activeProvider.generateWithTools(systemPrompt, messages, TOOL_SCHEMAS)
            val responseText = response.text.trim()
            if (response.tool_calls.isEmpty()) {
                finalText = responseText
                break
            }

            val limited = response.tool_calls.take(maxToolCallsPerRound)
            messages += AgentMessage(
                role = "assistant",
                content = responseText,
                tool_calls = limited,
            )
            for (call in limited) {
                val args = call.args.toMutableMap()
                if (call.name == "build_structured_report" && !args.containsKey("limit")) {
                    args["limit"] = effectLimit.toString()
                }
                val result = dispatcher.dispatch(call.name, args, session)
                val rawContent = result["json"] ?: result["text"] ?: result["error"].orEmpty()
                messages += AgentMessage(
                    role = "tool",
                    content = compactToolContent(call.name, rawContent),
                    tool_call_id = call.id,
                    name = call.name,
                )
            }
        }

        if (finalText.isBlank()) throw IllegalStateException("Gemma did not return an answer.")

        session.transcript.clear()
        session.transcript += priorMessages
        session.transcript += AgentMessage(role = "user", content = userMessage)
        session.transcript += AgentMessage(role = "assistant", content = finalText)

        return AgentTurnResult(
            finalText = finalText,
            trace = session.lastTrace.toList(),
            report = session.lastReport,
            usedTools = session.lastTrace.map { it.name },
            providerName = activeProvider.name,
        )
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
