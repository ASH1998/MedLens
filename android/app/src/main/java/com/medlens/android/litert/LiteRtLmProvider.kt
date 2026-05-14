package com.medlens.android.litert

import android.content.Context
import android.util.Log
import com.google.ai.edge.litertlm.Backend
import com.google.ai.edge.litertlm.Content
import com.google.ai.edge.litertlm.Contents
import com.google.ai.edge.litertlm.ConversationConfig
import com.google.ai.edge.litertlm.Engine
import com.google.ai.edge.litertlm.EngineConfig
import com.google.ai.edge.litertlm.ExperimentalApi
import com.google.ai.edge.litertlm.ExperimentalFlags
import com.google.ai.edge.litertlm.OpenApiTool
import com.google.ai.edge.litertlm.tool
import com.medlens.core.agent.model.AgentMessage
import com.medlens.core.agent.model.NativeToolProvider
import com.medlens.core.agent.model.ToolCall
import com.medlens.core.agent.model.ToolModelResponse
import com.medlens.core.agent.model.ToolSchema
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.encodeToJsonElement
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

enum class LiteRtBackendChoice { CPU, GPU }

class LiteRtLmProvider(
    private val context: Context,
    private val modelPath: String,
    private val backendChoice: LiteRtBackendChoice = LiteRtBackendChoice.CPU,
) : NativeToolProvider {
    override val name: String = "litert-lm"

    private val mutex = Mutex()
    private var engine: Engine? = null

    override suspend fun generateWithTools(
        systemPrompt: String,
        messages: List<AgentMessage>,
        tools: List<ToolSchema>,
    ): ToolModelResponse = withContext(Dispatchers.IO) {
        mutex.withLock {
            val activeEngine = engine ?: createEngine().also { engine = it }
            val prompt = renderPrompt(messages)
            val conversation = activeEngine.createConversation(
                ConversationConfig(
                    systemInstruction = Contents.of(systemPrompt),
                    tools = tools.map { tool(MedLensOpenApiTool(it)) },
                    automaticToolCalling = false,
                ),
            )
            conversation.use {
                val response = it.sendMessage(prompt)
                val toolCalls = response.toolCalls.mapIndexed { index, call ->
                    ToolCall(
                        id = "litert-$index",
                        name = call.name,
                        args = call.arguments.mapValues { (_, value) -> encodeToolArgument(value) },
                    )
                }
                ToolModelResponse(
                    text = response.toString().trim(),
                    tool_calls = toolCalls,
                )
            }
        }
    }

    suspend fun extractMedicineCandidatesFromImage(
        imagePath: String,
        userText: String,
    ): String = withContext(Dispatchers.IO) {
        mutex.withLock {
            val activeEngine = engine ?: createEngine().also { engine = it }
            val conversation = activeEngine.createConversation(
                ConversationConfig(
                    systemInstruction = Contents.of(IMAGE_EXTRACTION_SYSTEM_PROMPT),
                    automaticToolCalling = false,
                ),
            )
            conversation.use {
                val response = it.sendMessage(
                    Contents.of(
                        Content.ImageFile(imagePath),
                        Content.Text(imageExtractionUserPrompt(userText)),
                    ),
                )
                response.toString().trim()
            }
        }
    }

    fun close() {
        engine?.close()
        engine = null
    }

    private fun createEngine(): Engine {
        enableSpeculativeDecoding()
        val backend = when (backendChoice) {
            LiteRtBackendChoice.GPU -> Backend.GPU()
            LiteRtBackendChoice.CPU -> Backend.CPU()
        }
        return runCatching {
            val config = EngineConfig(
                modelPath = modelPath,
                backend = backend,
                visionBackend = backend,
                maxNumImages = 1,
                cacheDir = context.cacheDir.path,
            )
            Engine(config).also { it.initialize() }
        }.getOrElse { error ->
            if (backendChoice != LiteRtBackendChoice.GPU) throw error
            Log.w(TAG, "GPU LiteRT backend failed; retrying this model session on CPU.", error)
            val cpuConfig = EngineConfig(
                modelPath = modelPath,
                backend = Backend.CPU(),
                visionBackend = Backend.CPU(),
                maxNumImages = 1,
                cacheDir = context.cacheDir.path,
            )
            Engine(cpuConfig).also { it.initialize() }
        }
    }

    private fun renderPrompt(messages: List<AgentMessage>): String = buildString {
        messages.takeLast(6).forEach { message ->
            append(message.role.uppercase())
            when (message.role) {
                "assistant" -> {
                    append(": ")
                    append(compactMessageContent(message))
                    if (message.tool_calls.isNotEmpty()) {
                        append("\nTOOL_CALLS:")
                        message.tool_calls.forEach { call ->
                            append("\n- ")
                            append(call.name)
                            append("(")
                            append(call.args.entries.joinToString(", ") { "${it.key}=${it.value}" })
                            append(")")
                        }
                    }
                }
                "tool" -> {
                    append(" ")
                    append(message.name ?: "tool")
                    message.tool_call_id?.let {
                        append(" [")
                        append(it)
                        append("]")
                    }
                    append(": ")
                    append(compactMessageContent(message, TOOL_MESSAGE_CHAR_LIMIT))
                }
                else -> {
                    append(": ")
                    append(compactMessageContent(message))
                }
            }
            append("\n")
        }
    }.takeLast(PROMPT_CHAR_BUDGET)

    private fun compactMessageContent(message: AgentMessage, limit: Int = MESSAGE_CHAR_LIMIT): String {
        val content = message.content.trim()
        return if (content.length <= limit) content else content.take(limit) + "\n[truncated]"
    }

    private fun parseArgs(raw: String): Map<String, String> {
        if (raw.isBlank()) return emptyMap()
        return runCatching {
            Json.parseToJsonElement(raw).jsonObject.mapValues { (_, value) ->
                when (value) {
                    is JsonPrimitive -> value.content
                    else -> value.toString()
                }
            }
        }.getOrElse { emptyMap() }
    }

    private fun encodeToolArgument(value: Any?): String = when (value) {
        null -> ""
        is String -> value
        is Number, is Boolean -> value.toString()
        is List<*> -> buildJsonArray {
            value.forEach { add(JsonPrimitive(it?.toString().orEmpty())) }
        }.toString()
        is Array<*> -> buildJsonArray {
            value.forEach { add(JsonPrimitive(it?.toString().orEmpty())) }
        }.toString()
        is Map<*, *> -> buildJsonObject {
            value.forEach { (key, item) ->
                put(key.toString(), Json.encodeToJsonElement(item?.toString()))
            }
        }.toString()
        else -> value.toString()
    }
}

private const val TAG = "LiteRtLmProvider"
private const val IMAGE_EXTRACTION_SYSTEM_PROMPT = """
You extract medication names from images for MedLens.

Return only visible candidate medicine names, active ingredients, strengths, and dosage forms.
Do not give medication safety advice.
Do not infer interactions, severity, adverse effects, mechanisms, or sources.
If text is unclear, say which parts are unreadable and ask for a clearer photo.
Keep the answer short and structured as plain text.
"""
private const val PROMPT_CHAR_BUDGET = 9000
private const val MESSAGE_CHAR_LIMIT = 900
private const val TOOL_MESSAGE_CHAR_LIMIT = 3200

private fun imageExtractionUserPrompt(userText: String): String = buildString {
    append("Read this medicine image. Extract visible medicine candidates for a safety check.")
    if (userText.isNotBlank()) {
        append("\nUser context/question: ")
        append(userText.trim())
    }
}

@OptIn(ExperimentalApi::class)
private fun enableSpeculativeDecoding() {
    ExperimentalFlags.enableSpeculativeDecoding = true
}

private class MedLensOpenApiTool(
    private val schema: ToolSchema,
) : OpenApiTool {
    override fun getToolDescriptionJsonString(): String {
        return buildJsonObject {
            put("name", JsonPrimitive(schema.name))
            put("description", JsonPrimitive(schema.description))
            put("parameters", Json.parseToJsonElement(schema.inputSchemaJson))
        }.toString()
    }

    override fun execute(paramsJsonString: String): String =
        "{\"error\":\"MedLens executes tools manually in the Android agent loop.\"}"
}
