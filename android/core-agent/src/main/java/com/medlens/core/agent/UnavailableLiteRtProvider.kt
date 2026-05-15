package com.medlens.core.agent

import com.medlens.core.agent.model.AgentMessage
import com.medlens.core.agent.model.NativeToolProvider
import com.medlens.core.agent.model.ToolSchema
import com.medlens.core.agent.model.TurnSession

class UnavailableLiteRtProvider(
    private val reason: String,
) : NativeToolProvider {
    override val name: String = "litert-lm"

    override suspend fun startTurn(
        systemPrompt: String,
        priorTranscript: List<AgentMessage>,
        tools: List<ToolSchema>,
    ): TurnSession {
        throw IllegalStateException(reason)
    }
}
