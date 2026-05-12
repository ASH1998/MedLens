package com.medlens.android.litert

import android.content.Context
import com.google.ai.edge.litertlm.Backend
import com.google.ai.edge.litertlm.Contents
import com.google.ai.edge.litertlm.ConversationConfig
import com.google.ai.edge.litertlm.Engine
import com.google.ai.edge.litertlm.EngineConfig
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
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

class LiteRtLmProvider(
    private val context: Context,
    private val modelPath: String,
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
                        args = call.arguments.mapValues { (_, value) -> value?.toString().orEmpty() },
                    )
                }
                ToolModelResponse(
                    text = response.toString().trim(),
                    tool_calls = toolCalls,
                )
            }
        }
    }

    fun close() {
        engine?.close()
        engine = null
    }

    private fun createEngine(): Engine {
        val config = EngineConfig(
            modelPath = modelPath,
            backend = Backend.GPU(),
            cacheDir = context.cacheDir.path,
        )
        return Engine(config).also { it.initialize() }
    }

    private fun renderPrompt(messages: List<AgentMessage>): String = buildString {
        messages.takeLast(14).forEach { message ->
            append(message.role.uppercase())
            append(": ")
            append(message.content)
            append("\n")
        }
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
}

private class MedLensOpenApiTool(
    private val schema: ToolSchema,
) : OpenApiTool {
    override fun getToolDescriptionJsonString(): String {
        val params = buildJsonObject {
            put("type", JsonPrimitive("object"))
            put("properties", JsonObject(emptyMap()))
        }
        return buildJsonObject {
            put("name", JsonPrimitive(schema.name))
            put("description", JsonPrimitive(schema.description))
            put("parameters", params)
        }.toString()
    }

    override fun execute(paramsJsonString: String): String =
        "{\"error\":\"MedLens executes tools manually in the Android agent loop.\"}"
}
