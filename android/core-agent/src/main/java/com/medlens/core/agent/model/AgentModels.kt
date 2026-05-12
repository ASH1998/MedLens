package com.medlens.core.agent.model

import com.medlens.core.data.model.MedicationSafetyReport
import com.medlens.core.data.model.NormalizedMedication
import kotlinx.serialization.Serializable

@Serializable
data class ToolCall(
    val id: String,
    val name: String,
    val args: Map<String, String>,
)

@Serializable
data class ToolModelResponse(
    val text: String,
    val tool_calls: List<ToolCall>,
)

@Serializable
data class AgentMessage(
    val role: String,
    val content: String,
)

interface NativeToolProvider {
    val name: String
    suspend fun generateWithTools(
        systemPrompt: String,
        messages: List<AgentMessage>,
        tools: List<ToolSchema>,
    ): ToolModelResponse
}

@Serializable
data class ToolCallRecord(
    val name: String,
    val args: Map<String, String>,
    val resultSummary: String? = null,
    val error: String? = null,
    val duration_ms: Long? = null,
)

@Serializable
data class ChatSession(
    val medications: MutableList<NormalizedMedication> = mutableListOf(),
    val transcript: MutableList<AgentMessage> = mutableListOf(),
    var lastReport: MedicationSafetyReport? = null,
    val lastTrace: MutableList<ToolCallRecord> = mutableListOf(),
    var providerName: String = "litert-lm",
) {
    fun medicationInputs(): List<String> = medications.map { it.input_name }
    fun clearTurnTrace() = lastTrace.clear()
}

@Serializable
data class ToolSchema(
    val name: String,
    val description: String,
)

@Serializable
data class AgentTurnResult(
    val finalText: String,
    val trace: List<ToolCallRecord>,
    val report: MedicationSafetyReport?,
    val usedTools: List<String>,
    val providerName: String,
    val fallbackUsed: Boolean,
)
