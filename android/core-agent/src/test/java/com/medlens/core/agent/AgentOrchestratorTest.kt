package com.medlens.core.agent

import com.medlens.core.agent.model.AgentMessage
import com.medlens.core.agent.model.ChatSession
import com.medlens.core.agent.model.NativeToolProvider
import com.medlens.core.agent.model.TurnSession
import com.medlens.core.data.SafetyRepository
import com.medlens.core.data.model.AliasSearchResult
import com.medlens.core.data.model.CommonMedicineProfile
import com.medlens.core.data.model.CommonMedicineRow
import com.medlens.core.data.model.DrugInteractionList
import com.medlens.core.data.model.EvidenceImportFile
import com.medlens.core.data.model.KnownInteraction
import com.medlens.core.data.model.MedicationSafetyReport
import com.medlens.core.data.model.NormalizedMedication
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class AgentOrchestratorTest {

    @Test
    fun noProviderThrowsBeforeTouchingSession() {
        val session = ChatSession()
        val orchestrator = AgentOrchestrator(
            dispatcher = ToolDispatcher(NoopSafetyRepository()),
            provider = null,
        )

        assertThrows(IllegalStateException::class.java) {
            runBlocking { orchestrator.runTurn(session, "zanubrutinib with warfarin") }
        }
        assertEquals(emptyList<AgentMessage>(), session.transcript)
    }

    @Test
    fun answerFromLlmIsUsedDirectly() = runBlocking {
        val session = ChatSession()
        val orchestrator = AgentOrchestrator(
            dispatcher = ToolDispatcher(NoopSafetyRepository()),
            provider = ScriptedProvider(listOf("ANSWER: This is an LLM answer.")),
            repository = NoopSafetyRepository(),
        )

        val result = orchestrator.runTurn(session, "is paracetamol safe for headaches?")

        assertEquals("This is an LLM answer.", result.finalText)
        assertEquals("This is an LLM answer.", session.transcript.last().content)
    }

    @Test
    fun unstructuredReplyIsTreatedAsAnswer() = runBlocking {
        val orchestrator = AgentOrchestrator(
            dispatcher = ToolDispatcher(NoopSafetyRepository()),
            provider = ScriptedProvider(listOf("just some text with no verb")),
            repository = NoopSafetyRepository(),
        )

        val result = orchestrator.runTurn(ChatSession(), "hello")

        assertEquals("just some text with no verb", result.finalText)
    }

    @Test
    fun askVerbStopsTheLoopAndIsShownToUser() = runBlocking {
        val orchestrator = AgentOrchestrator(
            dispatcher = ToolDispatcher(NoopSafetyRepository()),
            provider = ScriptedProvider(listOf("ASK: which strength of crocin are you taking?")),
            repository = NoopSafetyRepository(),
        )

        val result = orchestrator.runTurn(ChatSession(), "crocin question")

        assertEquals("which strength of crocin are you taking?", result.finalText)
    }

    @Test
    fun callVerbDispatchesToolThenAnswers() = runBlocking {
        val provider = ScriptedProvider(
            listOf(
                """CALL: build_structured_report {"medication_names": ["warfarin", "zanubrutinib"]}""",
                "ANSWER: Combining warfarin with zanubrutinib is flagged as Major.",
            ),
        )
        val orchestrator = AgentOrchestrator(
            dispatcher = ToolDispatcher(LargeReportRepository()),
            provider = provider,
            repository = LargeReportRepository(),
        )

        val result = orchestrator.runTurn(ChatSession(), "is warfarin safe with zanubrutinib?")

        assertEquals("Combining warfarin with zanubrutinib is flagged as Major.", result.finalText)
        // The second user message the LLM saw should be a TOOL_RESULT message.
        assertTrue(
            "second send must include tool result block",
            provider.sentMessages[1].contains("TOOL_RESULT"),
        )
        assertTrue(
            "tool result must carry findings",
            provider.sentMessages[1].contains("warfarin"),
        )
        assertTrue(
            "structured report tool result must include normalized medicines",
            provider.sentMessages[1].contains("normalized_medications"),
        )
    }

    @Test
    fun askForActiveIngredientsAfterStructuredReportIsForcedToAnswer() = runBlocking {
        val provider = ScriptedProvider(
            listOf(
                """CALL: build_structured_report {"medication_names": ["Crocin 650", "Montelukast LC"]}""",
                "ASK: Could you please tell me the active ingredients?",
                "ANSWER: I checked acetaminophen with montelukast and levocetirizine.",
            ),
        )
        val repository = LargeReportRepository()
        val orchestrator = AgentOrchestrator(
            dispatcher = ToolDispatcher(repository),
            provider = provider,
            repository = repository,
        )

        val result = orchestrator.runTurn(ChatSession(), "can I take these together?")

        assertEquals("I checked acetaminophen with montelukast and levocetirizine.", result.finalText)
        assertTrue(
            "third prompt should reject the unnecessary active-ingredient question",
            provider.sentMessages[2].contains("Do not ask that clarification question"),
        )
    }

    @Test
    fun strayMarkupAroundVerbIsTolerated() = runBlocking {
        val provider = ScriptedProvider(
            listOf("```\n<|tool_call|>ANSWER: clean reply.\n```"),
        )
        val orchestrator = AgentOrchestrator(
            dispatcher = ToolDispatcher(NoopSafetyRepository()),
            provider = provider,
            repository = NoopSafetyRepository(),
        )

        val result = orchestrator.runTurn(ChatSession(), "hi")

        assertEquals("clean reply.", result.finalText.trim())
    }

    @Test
    fun stalledLoopFallsBackToHardFailureMessageWithoutTemplatedReport() = runBlocking {
        val infiniteCallProvider = ScriptedProvider(
            generateSequence { """CALL: list_medications {}""" }.take(20).toList(),
        )
        val orchestrator = AgentOrchestrator(
            dispatcher = ToolDispatcher(NoopSafetyRepository()),
            provider = infiniteCallProvider,
            repository = NoopSafetyRepository(),
        )

        val result = orchestrator.runTurn(ChatSession(), "what's safe?")

        // No deterministic templated report — just a short human apology.
        assertFalse("no Sources block leaks", result.finalText.contains("Sources:"))
        assertTrue(
            "short human fallback message",
            result.finalText.contains("trouble") || result.finalText.contains("try"),
        )
    }

    @Test
    fun providerExceptionDoesNotLeakToUser() = runBlocking {
        val orchestrator = AgentOrchestrator(
            dispatcher = ToolDispatcher(NoopSafetyRepository()),
            provider = ThrowingProvider("INVALID_ARGUMENT: failed to parse tool calls from response: <|tool_call>..."),
            repository = NoopSafetyRepository(),
        )

        val result = orchestrator.runTurn(ChatSession(), "anything")

        assertFalse(
            "raw parser/internal error must not appear in the user-visible text",
            result.finalText.contains("INVALID_ARGUMENT") || result.finalText.contains("tool_call"),
        )
        assertTrue(result.finalText.contains("trouble") || result.finalText.contains("try"))
    }
}

private class ScriptedProvider(
    private val script: List<String>,
) : NativeToolProvider {
    override val name: String = "scripted-llm"
    val sentMessages = mutableListOf<String>()

    override suspend fun startTurn(
        systemPrompt: String,
        priorTranscript: List<AgentMessage>,
    ): TurnSession = object : TurnSession {
        private var index = 0
        override suspend fun sendMessage(content: String): String {
            sentMessages += content
            val reply = if (index < script.size) script[index] else script.lastOrNull().orEmpty()
            index += 1
            return reply
        }
        override fun close() = Unit
    }
}

private class ThrowingProvider(private val message: String) : NativeToolProvider {
    override val name: String = "throwing-llm"
    override suspend fun startTurn(
        systemPrompt: String,
        priorTranscript: List<AgentMessage>,
    ): TurnSession = object : TurnSession {
        override suspend fun sendMessage(content: String): String = throw RuntimeException(message)
        override fun close() = Unit
    }
}

private open class NoopSafetyRepository : SafetyRepository {
    override suspend fun normalizeMedications(names: List<String>): List<NormalizedMedication> = emptyList()
    override suspend fun searchDrugAliases(query: String, limit: Int): List<AliasSearchResult> = emptyList()
    override suspend fun lookupKnownInteraction(drugA: String, drugB: String, effectLimit: Int, rawSignalLimit: Int): KnownInteraction =
        error("Not used")

    override suspend fun listInteractionsForDrug(
        drug: String,
        limit: Int,
        effectLimit: Int,
        minSeverity: String?,
        region: String?,
        riskFlag: String?,
    ): DrugInteractionList = error("Not used")

    override suspend fun buildStructuredReport(medicationNames: List<String>, effectLimit: Int): MedicationSafetyReport =
        MedicationSafetyReport(
            input_medications = medicationNames,
            normalized_medications = emptyList(),
            unresolved_medications = emptyList(),
            checked_pair_count = 0,
            findings = emptyList(),
            overall_severity = "None",
            evidence_status = "insufficient_resolved_medications",
            limitations = emptyList(),
        )

    override suspend fun getCommonMedicineProfile(name: String, limit: Int): CommonMedicineProfile = error("Not used")

    override suspend fun searchCommonMedicines(
        query: String?,
        therapeuticCategory: String?,
        otcOrRx: String?,
        nlemOrJanAushadhi: String?,
        riskFlag: String?,
        limit: Int,
    ): List<CommonMedicineRow> = emptyList()

    override suspend fun listEvidenceSources(): List<EvidenceImportFile> = emptyList()
    override fun close() = Unit
}

private class LargeReportRepository : NoopSafetyRepository() {
    override suspend fun buildStructuredReport(medicationNames: List<String>, effectLimit: Int): MedicationSafetyReport =
        MedicationSafetyReport(
            input_medications = medicationNames,
            normalized_medications = listOf(
                NormalizedMedication(
                    input_name = medicationNames.getOrElse(0) { "warfarin" },
                    normalized_input = medicationNames.getOrElse(0) { "warfarin" }.lowercase(),
                    canonical_name = "warfarin",
                    drug_id = 1,
                    matched_alias = medicationNames.getOrElse(0) { "warfarin" },
                    resolved = true,
                ),
                NormalizedMedication(
                    input_name = medicationNames.getOrElse(1) { "zanubrutinib" },
                    normalized_input = medicationNames.getOrElse(1) { "zanubrutinib" }.lowercase(),
                    canonical_name = "zanubrutinib",
                    drug_id = 2,
                    matched_alias = medicationNames.getOrElse(1) { "zanubrutinib" },
                    resolved = true,
                ),
                NormalizedMedication(
                    input_name = medicationNames.getOrElse(1) { "levocetirizine" },
                    normalized_input = medicationNames.getOrElse(1) { "levocetirizine" }.lowercase(),
                    canonical_name = "levocetirizine",
                    drug_id = 3,
                    matched_alias = medicationNames.getOrElse(1) { "levocetirizine" },
                    resolved = true,
                ),
            ),
            unresolved_medications = emptyList(),
            checked_pair_count = 1,
            overall_severity = "Major",
            evidence_status = "verified_reference_findings",
            limitations = emptyList(),
            findings = listOf(largeInteraction()),
        )

    private fun largeInteraction(): KnownInteraction = KnownInteraction(
        found = true,
        drug_a = "warfarin",
        drug_b = "zanubrutinib",
        severity = "Major",
        row_count = 99,
        source_regions = listOf("us", "eu/eea"),
        evidence_bases = listOf("DailyMed", "FDA_FAERS"),
        source_bases = listOf("DailyMed", "FDA_FAERS"),
        source_urls = (1..40).map { "https://example.com/source-$it" },
        mechanisms = listOf("BTK inhibitors can impair platelet signaling; anticoagulants add bleeding risk."),
        risk_flags = listOf("hematologic malignancy", "older adults"),
        dataset_types = emptyList(),
        use_case_notes = emptyList(),
        effects = listOf(
            com.medlens.core.data.model.InteractionEffect(
                adverse_effect = "gastrointestinal bleeding",
                severity = "Major",
                row_count = 8,
                source_regions = listOf("us"),
            ),
        ),
        raw_signals = emptyList(),
        evidence_source = "fixture",
    )
}
