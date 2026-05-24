package com.medlens.android.ui

import android.app.Application
import android.util.Log
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
import com.medlens.android.ocr.MlKitOcrManager
import com.medlens.core.agent.AgentOrchestrator
import com.medlens.core.agent.FallbackReportFormatter
import com.medlens.core.agent.ToolDispatcher
import com.medlens.core.agent.model.AgentMessage
import com.medlens.core.agent.model.ChatSession
import com.medlens.core.agent.model.ToolCallRecord
import com.medlens.core.data.DatabaseInstaller
import com.medlens.core.data.SafetyRepository
import com.medlens.core.data.SqliteSafetyRepository
import kotlinx.coroutines.delay
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

private const val MIN_SETUP_SCREEN_MILLIS = 1_500L

data class MedLensUiState(
    val setupStage: SetupStage = SetupStage.Checking,
    val conversations: List<PersistedConversation> = listOf(ConversationStore.newConversation()),
    val activeConversationId: String = "",
    val busy: Boolean = false,
    val trace: List<ToolCallRecord> = emptyList(),
    val lastReport: com.medlens.core.data.model.MedicationSafetyReport? = null,
    val modelState: ModelState = ModelState.NotDownloaded,
    val providerLabel: String = "Gemma not ready",
    val backendPref: LiteRtBackendPref = LiteRtBackendPref.CPU,
    val audienceStyle: AudienceStyle = AudienceStyle.Regular,
    val remoteTtsEnabled: Boolean = false,
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
    private val mlKitOcr = MlKitOcrManager()
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
                _uiState.update { it.copy(modelState = state, providerLabel = if (state is ModelState.Ready) "LiteRT-LM (${currentBackend.name})" else "Local (deterministic)") }
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
        viewModelScope.launch {
            conversationStore.remoteTtsEnabled.collect { enabled ->
                _uiState.update { it.copy(remoteTtsEnabled = enabled) }
            }
        }
        bootstrap()
    }

    fun bootstrap() {
        viewModelScope.launch {
            val setupStartedAt = System.currentTimeMillis()
            _uiState.update { it.copy(setupStage = SetupStage.Copying) }
            runCatching {
                val installed = installer.ensureInstalled()
                repository?.close()
                repository = SqliteSafetyRepository(installed)
            }.onSuccess {
                hydrateSession(activeConversation())
                delayRemainingSetupTime(setupStartedAt)
                _uiState.update { state -> state.copy(setupStage = SetupStage.Ready) }
            }.onFailure { error ->
                delayRemainingSetupTime(setupStartedAt)
                _uiState.update { state -> state.copy(setupStage = SetupStage.Error(error.message ?: "Setup failed")) }
            }
        }
    }

    private suspend fun delayRemainingSetupTime(setupStartedAt: Long) {
        val remaining = MIN_SETUP_SCREEN_MILLIS - (System.currentTimeMillis() - setupStartedAt)
        if (remaining > 0) delay(remaining)
    }

    fun sendMessage(message: String) {
        Log.i("MedLens", "VM.sendMessage user text='${message.take(200)}' (${message.length} chars)")
        if (message.isBlank() || _uiState.value.busy) {
            Log.w("MedLens", "VM.sendMessage skipped (blank=${message.isBlank()}, busy=${_uiState.value.busy})")
            return
        }
        val repo = repository ?: run {
            Log.e("MedLens", "VM.sendMessage no repository ready")
            return
        }
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

            val provider = liteRtProvider
            if (provider != null) {
                // Model is ready — use full agent loop
                val orchestrator = AgentOrchestrator(
                    dispatcher = ToolDispatcher(repo),
                    provider = provider,
                    repository = repo,
                )
                runCatching {
                    orchestrator.runTurn(session, message, audiencePrompt = _uiState.value.audienceStyle.prompt())
                }.onSuccess { result ->
                    Log.i("MedLens", "VM.sendMessage SUCCESS tools=${result.usedTools} finalText=${result.finalText.length} chars")
                    val active = activeConversation()
                    val updatedConversation = active?.copy(
                        messages = active.messages.map {
                            if (it.id == assistantId) it.copy(content = result.finalText, pending = false) else it
                        },
                        medications = session.medicationInputs(),
                        updatedAt = System.currentTimeMillis(),
                    )
                    if (updatedConversation != null) saveConversation(updatedConversation)
                    _uiState.update { it.copy(trace = result.trace, lastReport = result.report, busy = false) }
                }.onFailure { error ->
                    Log.e("MedLens", "VM.sendMessage runTurn threw ${error::class.java.simpleName}: ${error.message}", error)
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
            } else {
                // Model not ready — use deterministic fallback
                Log.i("MedLens", "VM.sendMessage using deterministic fallback (model not ready)")
                val fallbackText = runCatching {
                    runDeterministicFallback(repo, message)
                }.getOrElse { error ->
                    Log.e("MedLens", "VM.sendMessage fallback failed: ${error.message}", error)
                    "I couldn't check that right now. Please try again, or download the model for full explanations."
                }
                val active = activeConversation()
                val updatedConversation = active?.copy(
                    messages = active.messages.map {
                        if (it.id == assistantId) it.copy(content = fallbackText, pending = false) else it
                    },
                    updatedAt = System.currentTimeMillis(),
                )
                if (updatedConversation != null) saveConversation(updatedConversation)
                _uiState.update { it.copy(lastReport = session.lastReport, busy = false) }
            }
        }
    }

    fun sendImageMessage(imagePath: String, userText: String) {
        sendImageMessage(listOf(imagePath), userText)
    }

    fun sendImageMessage(imagePaths: List<String>, userText: String) {
        Log.i("MedLens", "VM.sendImageMessage ${imagePaths.size} images, userText='${userText.take(120)}'")
        if (_uiState.value.busy) {
            Log.w("MedLens", "VM.sendImageMessage skipped (busy)")
            return
        }
        val safeImagePaths = imagePaths.take(MAX_IMAGE_ATTACHMENTS)
        if (safeImagePaths.isEmpty()) {
            Log.w("MedLens", "VM.sendImageMessage skipped (no images)")
            return
        }
        val repo = repository ?: run {
            Log.e("MedLens", "VM.sendImageMessage no repository ready")
            return
        }
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
                // Step 1: ML Kit OCR first (deterministic, offline)
                val mlKitCandidates = mutableListOf<String>()
                val mlKitRawTexts = mutableListOf<String>()
                for ((index, imagePath) in safeImagePaths.withIndex()) {
                    Log.i("MedLens", "VM image[${index + 1}/${safeImagePaths.size}] ML Kit OCR start: $imagePath")
                    val candidates = mlKitOcr.recognizeCandidatesFromFile(appContext, imagePath)
                    val rawText = mlKitOcr.recognizeRawText(appContext, imagePath)
                    Log.i("MedLens", "VM image[${index + 1}] ML Kit OCR done: ${candidates.size} candidates, ${rawText.length} raw chars")
                    mlKitCandidates.addAll(candidates)
                    if (rawText.isNotBlank()) mlKitRawTexts.add("Image ${index + 1}:\n${rawText.trim()}")
                }
                val mlKitDistinct = mlKitCandidates.distinct().take(12)
                Log.i("MedLens", "VM ML Kit candidates: $mlKitDistinct")

                // Step 2: Determine OCR text and candidates
                var ocrText = mlKitRawTexts.joinToString("\n\n")
                var allCandidates = medicationCandidatesFromExtraction(ocrText)
                    .ifEmpty { mlKitDistinct }
                val candidatesFromUserText = medicationCandidatesFromUserText(userText)

                // Step 3: If ML Kit produced nothing useful AND model is ready, try Gemma vision
                if (allCandidates.isEmpty() && liteRtProvider != null) {
                    Log.i("MedLens", "VM ML Kit produced no candidates, trying Gemma vision fallback")
                    val visionProvider = liteRtProvider!!
                    val visionParts = safeImagePaths.mapIndexed { index, imagePath ->
                        Log.i("MedLens", "VM image[${index + 1}] Gemma vision start: $imagePath")
                        val text = visionProvider.extractMedicineCandidatesFromImage(imagePath, userText)
                        Log.i("MedLens", "VM image[${index + 1}] Gemma vision done (${text.length} chars)")
                        "Image ${index + 1}:\n${text.trim()}"
                    }.filter { it.isNotBlank() }
                    ocrText = visionParts.joinToString("\n\n")
                    allCandidates = medicationCandidatesFromExtraction(ocrText)
                }

                if (ocrText.isBlank() && allCandidates.isEmpty()) {
                    error("No visible medicine text was extracted from the attached image.")
                }

                val candidates = (allCandidates + candidatesFromUserText)
                    .distinctBy { it.lowercase() }
                    .take(8)
                Log.i("MedLens", "VM combined candidates (final): $candidates")

                val provider = liteRtProvider
                if (provider != null) {
                    // Model is ready — use full agent loop
                    val orchestrator = AgentOrchestrator(
                        dispatcher = ToolDispatcher(repo),
                        provider = provider,
                        repository = repo,
                    )
                    val agentMessage = imageAgentMessage(ocrText, candidates, userText)
                    Log.i("MedLens", "VM handing off to orchestrator (agentMessage ${agentMessage.length} chars)")
                    orchestrator.runTurn(session, agentMessage, audiencePrompt = _uiState.value.audienceStyle.prompt())
                } else {
                    // Model not ready — use deterministic fallback with OCR candidates
                    Log.i("MedLens", "VM using deterministic fallback for image (model not ready)")
                    if (candidates.size >= 2) {
                        val report = repo.buildStructuredReport(candidates)
                        session.lastReport = report
                        mergeResolvedMedications(session, report.normalized_medications)
                        FallbackReportFormatter.format(report)
                            ?: "I couldn't produce a safety report from the visible medicines. Please type the active ingredient names."
                    } else if (candidates.size == 1) {
                        val normalized = repo.normalizeMedications(candidates)
                        val resolved = normalized.filter { it.resolved }
                        if (resolved.isNotEmpty()) {
                            val interactions = repo.listInteractionsForDrug(resolved.first().canonical_name!!, limit = 5)
                            val lines = mutableListOf<String>()
                            lines += "I identified: ${resolved.first().canonical_name}."
                            if (interactions.interactions.isNotEmpty()) {
                                lines += "Known interactions include:"
                                for (interaction in interactions.interactions.take(5)) {
                                    val severity = interaction.severity ?: "flagged"
                                    val partner = if (interaction.drug_a.equals(resolved.first().canonical_name, ignoreCase = true))
                                        interaction.drug_b else interaction.drug_a
                                    lines += "• $partner — $severity"
                                }
                            } else {
                                lines += "No flagged interactions found in the local evidence."
                            }
                            lines += "This is a local evidence check, not medical advice. Consult your doctor or pharmacist."
                            lines.joinToString("\n\n")
                        } else {
                            "I saw '${candidates.first()}' in the image but couldn't match it to a known medicine. Please type the active ingredient name."
                        }
                    } else {
                        "I couldn't identify medicine names from the image. Please try a clearer photo, or type the medicine names directly."
                    }
                }
            }.onSuccess { result ->
                val resultText = if (result is String) result else (result as? com.medlens.core.agent.model.AgentTurnResult)?.finalText ?: result.toString()
                val trace = (result as? com.medlens.core.agent.model.AgentTurnResult)?.trace ?: emptyList()
                Log.i("MedLens", "VM.sendImageMessage SUCCESS resultText=${resultText.length} chars")
                val active = activeConversation()
                val updatedConversation = active?.copy(
                    messages = active.messages.map {
                        if (it.id == assistantId) it.copy(content = resultText, pending = false) else it
                    },
                    medications = session.medicationInputs(),
                    updatedAt = System.currentTimeMillis(),
                )
                if (updatedConversation != null) saveConversation(updatedConversation)
                _uiState.update { it.copy(trace = trace, lastReport = session.lastReport, busy = false) }
            }.onFailure { error ->
                Log.e("MedLens", "VM.sendImageMessage failed: ${error::class.java.simpleName}: ${error.message}", error)
                val active = activeConversation()
                val failureText = imageTurnFailureMessage(error)
                val updatedConversation = active?.copy(
                    messages = active.messages.map {
                        if (it.id == assistantId) it.copy(content = failureText, pending = false) else it
                    },
                    updatedAt = System.currentTimeMillis(),
                )
                if (updatedConversation != null) saveConversation(updatedConversation)
                _uiState.update { it.copy(lastReport = null, busy = false) }
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

    fun setRemoteTtsEnabled(enabled: Boolean) {
        _uiState.update { it.copy(remoteTtsEnabled = enabled) }
        viewModelScope.launch { conversationStore.saveRemoteTtsEnabled(enabled) }
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
            append("\nIf the user typed another medicine in their question, it is already included in this list. Check all listed names together.")
        }
        append("\n\nDo not mention internal tools, extraction steps, databases, or that the user provided images unless they explicitly ask how the app read them.")
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

    private fun medicationCandidatesFromUserText(userText: String): List<String> {
        val compact = userText.replace(Regex("\\s+"), " ").trim()
        if (compact.isBlank()) return emptyList()
        if (!Regex("(?i)\\b(and|with|plus)\\b|[+&]").containsMatchIn(compact)) return emptyList()

        val rejectedTerms = listOf(
            "this med",
            "these meds",
            "this medicine",
            "these medicines",
            "medicine",
            "medication",
            "medications",
            "fever",
            "safe",
            "okay",
            "together",
            "interaction",
        )
        return compact
            .split(Regex("(?i)\\s+(?:and|with|plus)\\s+|\\s*[+&]\\s*"))
            .map { raw ->
                raw
                    .replace(
                        Regex(
                            "(?i)^(what\\s+about|what\\s+if\\s+i\\s+take|can\\s+i\\s+take|can\\s+you\\s+take|is\\s+it\\s+(?:ok|okay)\\s+to\\s+take|if\\s+i\\s+take|i\\s+take|taking|take)\\s+",
                        ),
                        "",
                    )
                    .replace(Regex("(?i)\\b(?:safe|okay|ok|together|interaction|interactions)\\b"), " ")
                    .replace(Regex("\\s+"), " ")
                    .trim()
                    .trim('-', ':', ',', '.', '?', '!')
                    .trim()
            }
            .filter { item ->
                item.length in 3..60 &&
                    item.any { it.isLetter() } &&
                    rejectedTerms.none { item.equals(it, ignoreCase = true) }
            }
            .take(4)
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

    /**
     * Deterministic fallback path: parse medication candidates from user text,
     * build a structured report, and format it as patient-facing text without
     * requiring the LLM model.
     */
    private fun runDeterministicFallback(repo: SafetyRepository, message: String): String {
        val candidates = medicationCandidatesFromUserText(message)
            .ifEmpty {
                // Try simpler pattern: just split on common separators
                message.replace(Regex("\\s+"), " ").trim()
                    .split(Regex("[,;+]"))
                    .map { it.trim() }
                    .filter { it.length in 2..60 && it.any { c -> c.isLetter() } }
                    .take(6)
            }
        Log.i("MedLens", "Fallback candidates: $candidates")

        if (candidates.isEmpty()) {
            return "I couldn't identify medicine names from your message. Please type the medicine names you'd like to check (e.g., 'Advil and Warfarin')."
        }

        if (candidates.size < 2) {
            // Single medicine — list interactions
            val normalized = repo.normalizeMedications(candidates)
            val resolved = normalized.filter { it.resolved }
            if (resolved.isEmpty()) {
                return FallbackReportFormatter.formatUnresolved(candidates)
            }
            val interactions = repo.listInteractionsForDrug(resolved.first().canonical_name!!, limit = 5)
            val lines = mutableListOf<String>()
            lines += "I identified: ${resolved.first().canonical_name}."
            if (interactions.interactions.isNotEmpty()) {
                lines += "Known interactions include:"
                for (interaction in interactions.interactions.take(5)) {
                    val severity = interaction.severity ?: "flagged"
                    val partner = if (interaction.drug_a.equals(resolved.first().canonical_name, ignoreCase = true))
                        interaction.drug_b else interaction.drug_a
                    lines += "• $partner — $severity"
                }
            } else {
                lines += "No flagged interactions found in the local evidence for this medicine."
            }
            lines += "This is a local evidence check, not medical advice. Consult your doctor or pharmacist."
            return lines.joinToString("\n\n")
        }

        // Two or more medicines — build structured report
        val report = repo.buildStructuredReport(candidates)
        session.lastReport = report
        mergeResolvedMedications(session, report.normalized_medications)

        return FallbackReportFormatter.format(report)
            ?: "I couldn't produce a safety report from those names. Please try typing the active ingredient names."
    }

    private fun turnFailureMessage(error: Throwable): String =
        "I had trouble checking that just now. Please try again, or type the medicine names directly."

    private fun imageTurnFailureMessage(error: Throwable): String =
        "I couldn't read the attached image clearly enough to answer. Try a sharper photo, or type the medicine names directly."

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
        _uiState.update { it.copy(lastReport = null, trace = emptyList()) }
    }
}

private const val MAX_IMAGE_ATTACHMENTS = 3
