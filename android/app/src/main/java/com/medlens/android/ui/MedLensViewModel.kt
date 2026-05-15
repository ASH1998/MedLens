package com.medlens.android.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.medlens.android.data.ConversationStore
import com.medlens.android.data.LiteRtBackendPref
import com.medlens.android.data.PersistedConversation
import com.medlens.android.data.UiMessage
import com.medlens.android.litert.LiteRtBackendChoice
import com.medlens.android.litert.LiteRtLmProvider
import com.medlens.android.model.GemmaModelManager
import com.medlens.android.model.ModelState
import com.medlens.core.agent.AgentOrchestrator
import com.medlens.core.agent.ToolDispatcher
import com.medlens.core.agent.model.AgentMessage
import com.medlens.core.agent.model.ChatSession
import com.medlens.core.agent.model.ToolCallRecord
import com.medlens.core.data.DatabaseInstaller
import com.medlens.core.data.SafetyRepository
import com.medlens.core.data.SqliteSafetyRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.io.File

sealed interface SetupStage {
    data object Checking : SetupStage
    data object Copying : SetupStage
    data object Ready : SetupStage
    data class Error(val message: String) : SetupStage
}

data class MedLensUiState(
    val setupStage: SetupStage = SetupStage.Checking,
    val conversations: List<PersistedConversation> = listOf(ConversationStore.newConversation()),
    val activeConversationId: String = "",
    val busy: Boolean = false,
    val trace: List<ToolCallRecord> = emptyList(),
    val modelState: ModelState = ModelState.NotDownloaded,
    val providerLabel: String = "Gemma not ready",
    val backendPref: LiteRtBackendPref = LiteRtBackendPref.CPU,
    val audienceStyle: AudienceStyle = AudienceStyle.Regular,
)

enum class AudienceStyle {
    Regular,
    Clinician,
    Simple,
}

class MedLensViewModel(
    application: Application,
) : AndroidViewModel(application) {
    private val appContext = application.applicationContext
    private val conversationStore = ConversationStore(appContext)
    private val installer = DatabaseInstaller(appContext)
    private val modelManager = GemmaModelManager(appContext)
    private val session = ChatSession()

    private var repository: SafetyRepository? = null
    private var liteRtProvider: LiteRtLmProvider? = null
    private var currentModelPath: String? = null
    private var currentBackend: LiteRtBackendPref = LiteRtBackendPref.CPU

    private val _uiState = MutableStateFlow(MedLensUiState())
    val uiState: StateFlow<MedLensUiState> = _uiState.asStateFlow()

    init {
        viewModelScope.launch {
            conversationStore.state.collect { state ->
                _uiState.update {
                    it.copy(
                        conversations = state.conversations,
                        activeConversationId = state.activeId,
                    )
                }
                hydrateSession(state.conversations.firstOrNull { it.id == state.activeId })
            }
        }
        viewModelScope.launch {
            modelManager.observeState().collect { state ->
                if (state is ModelState.Ready) {
                    currentModelPath = state.path
                    if (liteRtProvider == null) {
                        liteRtProvider = LiteRtLmProvider(appContext, state.path, currentBackend.toChoice())
                    }
                } else {
                    currentModelPath = null
                    liteRtProvider?.close()
                    liteRtProvider = null
                }
                _uiState.update { it.copy(modelState = state, providerLabel = if (state is ModelState.Ready) "LiteRT-LM (${currentBackend.name})" else "Gemma not ready") }
            }
        }
        viewModelScope.launch {
            conversationStore.backendPref.collect { pref ->
                currentBackend = pref
                if (pref != _uiState.value.backendPref) {
                    val modelPath = currentModelPath
                    liteRtProvider?.close()
                    liteRtProvider = if (modelPath != null) LiteRtLmProvider(appContext, modelPath, pref.toChoice()) else null
                }
                _uiState.update { state ->
                    state.copy(
                        backendPref = pref,
                        providerLabel = if (state.modelState is ModelState.Ready) "LiteRT-LM (${pref.name})" else state.providerLabel,
                    )
                }
            }
        }
        bootstrap()
    }

    fun bootstrap() {
        viewModelScope.launch {
            _uiState.update { it.copy(setupStage = SetupStage.Copying) }
            runCatching {
                val installed = installer.ensureInstalled()
                repository?.close()
                repository = SqliteSafetyRepository(installed)
            }.onSuccess {
                hydrateSession(activeConversation())
                _uiState.update { state -> state.copy(setupStage = SetupStage.Ready) }
            }.onFailure { error ->
                _uiState.update { state -> state.copy(setupStage = SetupStage.Error(error.message ?: "Setup failed")) }
            }
        }
    }

    fun sendMessage(message: String) {
        if (message.isBlank() || _uiState.value.busy) return
        val repo = repository ?: return
        val provider = liteRtProvider ?: return
        val orchestrator = AgentOrchestrator(
            dispatcher = ToolDispatcher(repo),
            provider = provider,
            repository = repo,
        )
        val current = activeConversation() ?: return
        val assistantId = java.util.UUID.randomUUID().toString()

        viewModelScope.launch {
            _uiState.update { it.copy(busy = true) }
            val userMessage = UiMessage(id = java.util.UUID.randomUUID().toString(), role = "user", content = message)
            val pendingAssistant = UiMessage(id = assistantId, role = "assistant", content = "", pending = true)
            saveConversation(
                current.copy(
                    title = if (current.messages.isEmpty()) ConversationStore.titleFromMessage(message) else current.title,
                    messages = current.messages + listOf(userMessage, pendingAssistant),
                    updatedAt = System.currentTimeMillis(),
                ),
            )

            runCatching {
                orchestrator.runTurn(session, message, audiencePrompt = _uiState.value.audienceStyle.prompt())
            }.onSuccess { result ->
                val active = activeConversation()
                val updatedConversation = active?.copy(
                    messages = active.messages.map {
                        if (it.id == assistantId) it.copy(content = result.finalText, pending = false) else it
                    },
                    medications = session.medicationInputs(),
                    updatedAt = System.currentTimeMillis(),
                )
                if (updatedConversation != null) saveConversation(updatedConversation)
                _uiState.update { it.copy(trace = result.trace, busy = false) }
            }.onFailure { error ->
                val active = activeConversation()
                val failureText = turnFailureMessage(error)
                val updatedConversation = active?.copy(
                    messages = active.messages.map {
                        if (it.id == assistantId) it.copy(content = failureText, pending = false) else it
                    },
                    updatedAt = System.currentTimeMillis(),
                )
                if (updatedConversation != null) saveConversation(updatedConversation)
                _uiState.update { it.copy(busy = false) }
            }
        }
    }

    fun sendImageMessage(imagePath: String, userText: String) {
        sendImageMessage(listOf(imagePath), userText)
    }

    fun sendImageMessage(imagePaths: List<String>, userText: String) {
        if (_uiState.value.busy) return
        val safeImagePaths = imagePaths.take(MAX_IMAGE_ATTACHMENTS)
        if (safeImagePaths.isEmpty()) return
        val repo = repository ?: return
        val provider = liteRtProvider ?: return
        val orchestrator = AgentOrchestrator(
            dispatcher = ToolDispatcher(repo),
            provider = provider,
            repository = repo,
        )
        val current = activeConversation() ?: return
        val displayText = userText.trim()
        val titleText = displayText.ifBlank { "Image attached" }
        val assistantId = java.util.UUID.randomUUID().toString()

        viewModelScope.launch {
            _uiState.update { it.copy(busy = true) }
            val userMessage = UiMessage(
                id = java.util.UUID.randomUUID().toString(),
                role = "user",
                content = displayText,
                imagePath = safeImagePaths.firstOrNull(),
                imagePaths = safeImagePaths,
            )
            val pendingAssistant = UiMessage(id = assistantId, role = "assistant", content = "", pending = true)
            saveConversation(
                current.copy(
                    title = if (current.messages.isEmpty()) ConversationStore.titleFromMessage(titleText) else current.title,
                    messages = current.messages + listOf(userMessage, pendingAssistant),
                    updatedAt = System.currentTimeMillis(),
                ),
            )

            runCatching {
                val extractedParts = safeImagePaths.mapIndexed { index, imagePath ->
                    val text = provider.extractMedicineCandidatesFromImage(imagePath, userText)
                    "Image ${index + 1}:\n${text.trim()}"
                }.filter { it.isNotBlank() }
                val extracted = extractedParts.joinToString("\n\n")
                if (extracted.isBlank()) error("No visible medicine text was extracted from the attached image.")
                val candidates = medicationCandidatesFromExtraction(extracted)
                val agentMessage = imageAgentMessage(extracted, candidates, userText)
                orchestrator.runTurn(session, agentMessage, audiencePrompt = _uiState.value.audienceStyle.prompt())
            }.onSuccess { result ->
                val active = activeConversation()
                val updatedConversation = active?.copy(
                    messages = active.messages.map {
                        if (it.id == assistantId) it.copy(content = result.finalText, pending = false) else it
                    },
                    medications = session.medicationInputs(),
                    updatedAt = System.currentTimeMillis(),
                )
                if (updatedConversation != null) saveConversation(updatedConversation)
                _uiState.update { it.copy(trace = result.trace, busy = false) }
            }.onFailure { error ->
                val active = activeConversation()
                val failureText = imageTurnFailureMessage(error)
                val updatedConversation = active?.copy(
                    messages = active.messages.map {
                        if (it.id == assistantId) it.copy(content = failureText, pending = false) else it
                    },
                    updatedAt = System.currentTimeMillis(),
                )
                if (updatedConversation != null) saveConversation(updatedConversation)
                _uiState.update { it.copy(busy = false) }
            }
        }
    }

    fun createConversation() {
        viewModelScope.launch {
            val next = ConversationStore.newConversation()
            val current = _uiState.value.conversations
            conversationStore.save(listOf(next) + current, next.id)
        }
    }

    fun selectConversation(id: String) {
        viewModelScope.launch {
            conversationStore.save(_uiState.value.conversations, id)
        }
    }

    fun deleteConversation(id: String) {
        viewModelScope.launch {
            activeConversation(id)?.messages
                ?.flatMap { message -> message.imagePaths.ifEmpty { listOfNotNull(message.imagePath) } }
                ?.distinct()
                ?.forEach { runCatching { File(it).delete() } }
            val remaining = _uiState.value.conversations.filterNot { it.id == id }
            val next = if (remaining.isEmpty()) listOf(ConversationStore.newConversation()) else remaining
            val nextActive = if (_uiState.value.activeConversationId == id) next.first().id else _uiState.value.activeConversationId
            conversationStore.save(next, nextActive)
        }
    }

    fun enqueueModelDownload() {
        modelManager.enqueueDownload()
    }

    fun setBackendPref(pref: LiteRtBackendPref) {
        viewModelScope.launch {
            conversationStore.saveBackendPref(pref)
        }
    }

    fun setAudienceStyle(style: AudienceStyle) {
        _uiState.update { it.copy(audienceStyle = style) }
    }

    override fun onCleared() {
        liteRtProvider?.close()
        repository?.close()
        super.onCleared()
    }

    private suspend fun saveConversation(updated: PersistedConversation) {
        val conversations = _uiState.value.conversations.map { if (it.id == updated.id) updated else it }
        conversationStore.save(conversations, updated.id)
    }

    private fun activeConversation(): PersistedConversation? =
        _uiState.value.conversations.firstOrNull { it.id == _uiState.value.activeConversationId }

    private fun activeConversation(id: String): PersistedConversation? =
        _uiState.value.conversations.firstOrNull { it.id == id }

    private fun LiteRtBackendPref.toChoice(): LiteRtBackendChoice = when (this) {
        LiteRtBackendPref.CPU -> LiteRtBackendChoice.CPU
        LiteRtBackendPref.GPU -> LiteRtBackendChoice.GPU
    }

    private fun AudienceStyle.prompt(): String = when (this) {
        AudienceStyle.Regular -> "The user is a regular patient. Explain clearly, but include the practical mechanism when it helps them understand the risk."
        AudienceStyle.Clinician -> "The user is a doctor or clinician. Use concise clinical language, include mechanism, severity, evidence regions, and monitoring implications."
        AudienceStyle.Simple -> "Use simple language for an older adult or non-technical patient. Keep sentences short, explain medical terms, and focus on what to do next."
    }

    private fun imageAgentMessage(
        extracted: String,
        candidates: List<String>,
        userText: String,
    ): String = buildString {
        append("The user attached one or more medicine images. The OCR/vision output below may contain text from multiple photos; treat it as one combined medicine list, not as separate conversations.\n")
        append("Visible medicine candidates:\n")
        append(extracted.trim())
        if (candidates.isNotEmpty()) {
            append("\n\nMedication names to check together. Treat each bullet as one product/name from the combined images. Normalize these names, add only the resolved medicines, then call build_structured_report before answering:\n")
            candidates.forEach { candidate ->
                append("- ")
                append(candidate)
                append("\n")
            }
        }
        append("\n\nUse only deterministic local evidence to answer. Do not mention internal tools, extraction steps, databases, or that the user provided images unless they explicitly ask how the app read them.")
        append("\nAnswer naturally as if the user typed the medicine names. Do not start with a preface like \"you provided an image\" or \"I see an image\".")
        if (userText.isBlank()) {
            append("\nUser intent: identify the visible medicines and check relevant medication safety concerns among them.")
        } else {
            append("\nUser question/context: ")
            append(userText.trim())
        }
    }

    private fun medicationCandidatesFromExtraction(extracted: String): List<String> {
        val rejectedTerms = listOf(
            "unreadable",
            "unclear",
            "unable",
            "cannot",
            "could not",
            "not visible",
            "safety",
            "interaction",
            "question",
            "context",
        )
        val candidates = linkedSetOf<String>()
        extracted
            .replace(Regex("(?i)\\bImage\\s+\\d+\\s*:"), "\n")
            .split(Regex("[\\n;,]"))
            .map { raw ->
                raw.trim()
                    .replace(Regex("^[-*•\\d.)\\s]+"), "")
                    .replace(Regex("(?i)^(medicine|medication|brand|name|candidate|visible text|product)\\s*:?\\s*"), "")
                    .trim()
            }
            .filter { item ->
                item.length in 3..80 &&
                    rejectedTerms.none { item.contains(it, ignoreCase = true) } &&
                    item.any { it.isLetter() }
            }
            .forEach { item ->
                candidates += item
                simplifiedCandidate(item)?.let { candidates += it }
            }
        return candidates.take(8)
    }

    private fun simplifiedCandidate(value: String): String? {
        val simplified = value
            .replace(Regex("(?i)\\b\\d+(?:\\.\\d+)?\\s*(?:mg|mcg|g|ml|iu)\\b"), " ")
            .replace(Regex("(?i)\\b(tablets?|tabs?|capsules?|caps?|syrup|injection|cream|ointment|strip|bottle)\\b"), " ")
            .replace(Regex("\\s+"), " ")
            .trim()
            .trim('-', ':')
            .trim()
        return simplified.takeIf { it.length >= 3 && !it.equals(value, ignoreCase = true) }
    }

    private fun turnFailureMessage(error: Throwable): String {
        val detail = error.message?.takeIf { it.isNotBlank() } ?: error::class.java.simpleName
        return "I couldn't finish that turn — $detail. You can try rephrasing or asking again."
    }

    private fun imageTurnFailureMessage(error: Throwable): String {
        val detail = error.message?.takeIf { it.isNotBlank() } ?: error::class.java.simpleName
        return "I couldn't read the attached image clearly enough to answer — $detail. Try a sharper photo, or type the medicine names directly."
    }

    private suspend fun hydrateSession(conversation: PersistedConversation?) {
        session.transcript.clear()
        session.lastTrace.clear()
        session.lastReport = null
        session.medications.clear()
        if (conversation == null) return
        conversation.messages.forEach { message ->
            session.transcript += AgentMessage(role = message.role, content = message.content)
        }
        repository?.let { repo ->
            session.medications += repo.normalizeMedications(conversation.medications)
        }
    }
}

private const val MAX_IMAGE_ATTACHMENTS = 3
