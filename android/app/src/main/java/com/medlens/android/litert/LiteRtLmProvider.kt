package com.medlens.android.litert

import android.content.Context
import android.util.Log
import com.google.ai.edge.litertlm.Backend
import com.google.ai.edge.litertlm.Content
import com.google.ai.edge.litertlm.Contents
import com.google.ai.edge.litertlm.Conversation
import com.google.ai.edge.litertlm.ConversationConfig
import com.google.ai.edge.litertlm.Engine
import com.google.ai.edge.litertlm.EngineConfig
import com.google.ai.edge.litertlm.ExperimentalApi
import com.google.ai.edge.litertlm.ExperimentalFlags
import com.google.ai.edge.litertlm.Message
import com.google.ai.edge.litertlm.OpenApiTool
import com.google.ai.edge.litertlm.Role
import com.google.ai.edge.litertlm.tool
import com.medlens.core.agent.model.AgentMessage
import com.medlens.core.agent.model.NativeToolProvider
import com.medlens.core.agent.model.ToolCall
import com.medlens.core.agent.model.ToolModelResponse
import com.medlens.core.agent.model.ToolResultPayload
import com.medlens.core.agent.model.ToolSchema
import com.medlens.core.agent.model.TurnSession
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject

enum class LiteRtBackendChoice { CPU, GPU }

class LiteRtLmProvider(
    private val context: Context,
    private val modelPath: String,
    private val backendChoice: LiteRtBackendChoice = LiteRtBackendChoice.CPU,
) : NativeToolProvider {
    override val name: String = "litert-lm"

    private val engineMutex = Mutex()
    private val turnMutex = Mutex()
    private var engine: Engine? = null

    override suspend fun startTurn(
        systemPrompt: String,
        priorTranscript: List<AgentMessage>,
        tools: List<ToolSchema>,
    ): TurnSession = withContext(Dispatchers.IO) {
        turnMutex.lock()
        try {
            val activeEngine = engineMutex.withLock { engine ?: createEngine().also { engine = it } }
            val initialMessages = priorTranscript.mapNotNull { msg ->
                when (msg.role) {
                    "user" -> Message.user(msg.content)
                    "assistant" -> Message.model(Contents.of(msg.content), emptyList(), emptyMap())
                    else -> null
                }
            }
            val conversation = activeEngine.createConversation(
                ConversationConfig(
                    systemInstruction = Contents.of(systemPrompt),
                    initialMessages = initialMessages,
                    tools = tools.map { tool(MedLensOpenApiTool(it)) },
                    automaticToolCalling = false,
                ),
            )
            LiteRtTurnSession(conversation) { turnMutex.unlock() }
        } catch (t: Throwable) {
            if (turnMutex.isLocked) turnMutex.unlock()
            throw t
        }
    }

    suspend fun extractMedicineCandidatesFromImage(
        imagePath: String,
        userText: String,
    ): String = withContext(Dispatchers.IO) {
        turnMutex.withLock {
            val activeEngine = engineMutex.withLock { engine ?: createEngine().also { engine = it } }
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
                    emptyMap(),
                )
                extractText(response)
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
}

private class LiteRtTurnSession(
    private val conversation: Conversation,
    private val releaseTurnLock: () -> Unit,
) : TurnSession {
    private var roundCounter = 0
    private var closed = false

    override suspend fun sendUser(content: String): ToolModelResponse = withContext(Dispatchers.IO) {
        val message = Message.user(content)
        toModelResponse(conversation.sendMessage(message, emptyMap()))
    }

    override suspend fun sendToolResults(results: List<ToolResultPayload>): ToolModelResponse = withContext(Dispatchers.IO) {
        val contents = if (results.isEmpty()) {
            Contents.of("")
        } else {
            Contents.of(*results.map { Content.ToolResponse(it.name, it.content) }.toTypedArray())
        }
        val message = Message.tool(contents)
        toModelResponse(conversation.sendMessage(message, emptyMap()))
    }

    override fun close() {
        if (closed) return
        closed = true
        runCatching { conversation.close() }
        releaseTurnLock()
    }

    private fun toModelResponse(reply: Message): ToolModelResponse {
        val text = extractText(reply)
        val round = roundCounter++
        val mappedCalls = reply.toolCalls.mapIndexed { index, call ->
            ToolCall(
                id = "litert-r$round-$index",
                name = call.name,
                args = call.arguments.mapValues { (_, value) -> encodeToolArgument(value) },
            )
        }
        return ToolModelResponse(text = text, tool_calls = mappedCalls)
    }
}

private fun extractText(message: Message): String =
    message.contents.contents
        .filterIsInstance<Content.Text>()
        .joinToString("") { it.text }
        .trim()

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
            put(key.toString(), JsonPrimitive(item?.toString().orEmpty()))
        }
    }.toString()
    else -> value.toString()
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

private fun imageExtractionUserPrompt(userText: String): String = buildString {
    append("Read this medicine image. Extract visible medicine candidates for a safety check.")
    if (userText.isNotBlank()) {
        append("\nUser context/question: ")
        append(userText.trim())
    }
}

@OptIn(ExperimentalApi::class)
private fun enableSpeculativeDecoding() {
    runCatching { ExperimentalFlags.enableSpeculativeDecoding = true }
        .onFailure { Log.w(TAG, "Speculative decoding flag not applied: ${it.message}") }
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
