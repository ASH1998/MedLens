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
data class AgentMessage(
    val role: String,
    val content: String,
    val tool_calls: List<ToolCall> = emptyList(),
    val tool_call_id: String? = null,
    val name: String? = null,
)

interface TurnSession : AutoCloseable {
    suspend fun sendMessage(content: String): String
    override fun close()
}

interface NativeToolProvider {
    val name: String
    suspend fun startTurn(
        systemPrompt: String,
        priorTranscript: List<AgentMessage>,
    ): TurnSession
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
    val inputSchemaJson: String = """{"type":"object","properties":{}}""",
)

@Serializable
data class AgentTurnResult(
    val finalText: String,
    val trace: List<ToolCallRecord>,
    val report: MedicationSafetyReport?,
    val usedTools: List<String>,
    val providerName: String,
)
