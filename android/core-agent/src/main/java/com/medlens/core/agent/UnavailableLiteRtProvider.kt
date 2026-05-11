package com.medlens.core.agent

import com.medlens.core.agent.model.AgentMessage
import com.medlens.core.agent.model.NativeToolProvider
import com.medlens.core.agent.model.ToolModelResponse
import com.medlens.core.agent.model.ToolSchema

class UnavailableLiteRtProvider(
    private val reason: String,
) : NativeToolProvider {
    override val name: String = "litert-lm"

    override suspend fun generateWithTools(
        systemPrompt: String,
        messages: List<AgentMessage>,
        tools: List<ToolSchema>,
    ): ToolModelResponse {
        throw IllegalStateException(reason)
    }
}
