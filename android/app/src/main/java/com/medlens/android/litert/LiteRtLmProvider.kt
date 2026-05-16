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
import com.medlens.core.agent.model.AgentMessage
import com.medlens.core.agent.model.NativeToolProvider
import com.medlens.core.agent.model.TurnSession
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

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
    ): TurnSession = withContext(Dispatchers.IO) {
        Log.i(TAG, "startTurn: waiting for turnMutex")
        turnMutex.lock()
        Log.i(TAG, "startTurn: acquired turnMutex")
        try {
            val engineStart = System.currentTimeMillis()
            val activeEngine = engineMutex.withLock { engine ?: createEngine().also { engine = it } }
            Log.i(TAG, "startTurn: engine ready (acquire/create took ${System.currentTimeMillis() - engineStart}ms)")
            val initialMessages = priorTranscript.mapNotNull { msg ->
                when (msg.role) {
                    "user" -> Message.user(msg.content)
                    "assistant" -> Message.model(Contents.of(msg.content), emptyList(), emptyMap())
                    else -> null
                }
            }
            Log.i(TAG, "startTurn: creating conversation (system=${systemPrompt.length} chars, ${initialMessages.size} prior msgs, backend=$backendChoice)")
            val convStart = System.currentTimeMillis()
            val conversation = activeEngine.createConversation(
                ConversationConfig(
                    systemInstruction = Contents.of(systemPrompt),
                    initialMessages = initialMessages,
                    automaticToolCalling = false,
                ),
            )
            Log.i(TAG, "startTurn: conversation created in ${System.currentTimeMillis() - convStart}ms")
            LiteRtTurnSession(conversation) {
                Log.i(TAG, "TurnSession.close: releasing turnMutex")
                turnMutex.unlock()
            }
        } catch (t: Throwable) {
            Log.e(TAG, "startTurn FAILED: ${t::class.java.simpleName}: ${t.message}", t)
            if (turnMutex.isLocked) turnMutex.unlock()
            throw t
        }
    }

    suspend fun extractMedicineCandidatesFromImage(
        imagePath: String,
        userText: String,
    ): String = withContext(Dispatchers.IO) {
        Log.i(TAG, "extractMedicineCandidatesFromImage: $imagePath")
        turnMutex.withLock {
            val activeEngine = engineMutex.withLock { engine ?: createEngine().also { engine = it } }
            val conversation = activeEngine.createConversation(
                ConversationConfig(
                    systemInstruction = Contents.of(IMAGE_EXTRACTION_SYSTEM_PROMPT),
                    automaticToolCalling = false,
                ),
            )
            val ocrStart = System.currentTimeMillis()
            conversation.use {
                val response = it.sendMessage(
                    Contents.of(
                        Content.ImageFile(imagePath),
                        Content.Text(imageExtractionUserPrompt(userText)),
                    ),
                    emptyMap(),
                )
                val extracted = extractText(response)
                Log.i(TAG, "image OCR done in ${System.currentTimeMillis() - ocrStart}ms (${extracted.length} chars):\n${extracted.take(500)}")
                extracted
            }
        }
    }

    fun close() {
        engine?.close()
        engine = null
    }

    private fun createEngine(): Engine {
        Log.i(TAG, "createEngine: modelPath=$modelPath backend=$backendChoice")
        val initStart = System.currentTimeMillis()
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
            Engine(config).also {
                it.initialize()
                Log.i(TAG, "createEngine: initialized $backendChoice engine in ${System.currentTimeMillis() - initStart}ms")
            }
        }.getOrElse { error ->
            if (backendChoice != LiteRtBackendChoice.GPU) {
                Log.e(TAG, "createEngine FAILED on $backendChoice: ${error.message}", error)
                throw error
            }
            Log.w(TAG, "GPU LiteRT backend failed; retrying this model session on CPU.", error)
            val cpuConfig = EngineConfig(
                modelPath = modelPath,
                backend = Backend.CPU(),
                visionBackend = Backend.CPU(),
                maxNumImages = 1,
                cacheDir = context.cacheDir.path,
            )
            Engine(cpuConfig).also {
                it.initialize()
                Log.i(TAG, "createEngine: fell back to CPU and initialized in ${System.currentTimeMillis() - initStart}ms")
            }
        }
    }
}

private class LiteRtTurnSession(
    private val conversation: Conversation,
    private val releaseTurnLock: () -> Unit,
) : TurnSession {
    private var closed = false
    private var sendCounter = 0

    override suspend fun sendMessage(content: String): String = withContext(Dispatchers.IO) {
        sendCounter += 1
        val sendId = sendCounter
        Log.i("MedLens", "LiteRT sendMessage #$sendId BEGIN (${content.length} chars)")
        val start = System.currentTimeMillis()
        try {
            val message = Message.user(content)
            val reply = conversation.sendMessage(message, emptyMap())
            val text = extractText(reply)
            Log.i("MedLens", "LiteRT sendMessage #$sendId END in ${System.currentTimeMillis() - start}ms (${text.length} chars out)")
            text
        } catch (t: Throwable) {
            Log.e("MedLens", "LiteRT sendMessage #$sendId THREW after ${System.currentTimeMillis() - start}ms: ${t::class.java.simpleName}: ${t.message}", t)
            throw t
        }
    }

    override fun close() {
        if (closed) return
        closed = true
        Log.i("MedLens", "LiteRtTurnSession.close")
        runCatching { conversation.close() }
        releaseTurnLock()
    }
}

private fun extractText(message: Message): String =
    message.contents.contents
        .filterIsInstance<Content.Text>()
        .joinToString("") { it.text }
        .trim()

private const val TAG = "MedLens"
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
