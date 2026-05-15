package com.medlens.core.agent

import com.medlens.core.agent.model.AgentMessage
import com.medlens.core.agent.model.ChatSession
import com.medlens.core.agent.model.NativeToolProvider
import com.medlens.core.agent.model.ToolCall
import com.medlens.core.agent.model.ToolModelResponse
import com.medlens.core.agent.model.ToolResultPayload
import com.medlens.core.agent.model.ToolSchema
import com.medlens.core.agent.model.TurnSession
import com.medlens.core.data.SafetyRepository
import com.medlens.core.data.model.AliasSearchResult
import com.medlens.core.data.model.CommonMedicineProfile
import com.medlens.core.data.model.CommonMedicineRow
import com.medlens.core.data.model.DuplicateIngredientWarning
import com.medlens.core.data.model.DrugInteractionList
import com.medlens.core.data.model.EvidenceImportFile
import com.medlens.core.data.model.KnownInteraction
import com.medlens.core.data.model.MedicationSafetyReport
import com.medlens.core.data.model.NormalizedMedication
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
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
    fun blankModelOutputFallsBackToDeterministicTextFromReport() = runBlocking {
        val session = ChatSession()
        val orchestrator = AgentOrchestrator(
            dispatcher = ToolDispatcher(LargeReportRepository()),
            provider = StaticProvider(""),
            repository = LargeReportRepository(),
        )

        val result = orchestrator.runTurn(session, "warfarin with zanubrutinib")

        assertTrue("falls back to deterministic text", result.finalText.contains("warfarin"))
        assertTrue("includes severity wording", result.finalText.contains("Major"))
        assertTrue("includes sources block", result.finalText.contains("Sources:"))
        assertTrue("non-empty transcript", session.transcript.isNotEmpty())
        assertEquals("assistant", session.transcript.last().role)
    }

    @Test
    fun finalTextComesFromProviderWhenAvailable() = runBlocking {
        val session = ChatSession()
        val orchestrator = AgentOrchestrator(
            dispatcher = ToolDispatcher(NoopSafetyRepository()),
            provider = StaticProvider("This is an LLM answer."),
            repository = NoopSafetyRepository(),
        )

        val result = orchestrator.runTurn(session, "zanubrutinib with warfarin")

        assertEquals("This is an LLM answer.", result.finalText)
        assertEquals("This is an LLM answer.", session.transcript.last().content)
    }

    @Test
    fun contradictoryModelAnswerFallsBackToDeterministicReport() = runBlocking {
        val session = ChatSession()
        val orchestrator = AgentOrchestrator(
            dispatcher = ToolDispatcher(LargeReportRepository()),
            provider = ContradictoryReportProvider(),
            repository = LargeReportRepository(),
        )

        val result = orchestrator.runTurn(session, "what about warfarin and zanubrutinib")

        assertTrue("keeps deterministic Major finding", result.finalText.contains("Major"))
        assertTrue("keeps warfarin", result.finalText.contains("warfarin"))
        assertTrue("keeps zanubrutinib", result.finalText.contains("zanubrutinib"))
        assertFalse("rejects false no-finding text", result.finalText.contains("did not find a flagged interaction"))
    }

    @Test
    fun toolResultsAreCompactedBeforeReturningToProvider() = runBlocking {
        val provider = CapturingProvider()
        val orchestrator = AgentOrchestrator(
            dispatcher = ToolDispatcher(LargeReportRepository()),
            provider = provider,
            repository = LargeReportRepository(),
        )

        orchestrator.runTurn(ChatSession(), "zanubrutinib with warfarin")

        val toolPayload = provider.toolResultsSeenOnRound2.single()
        val content = toolPayload.content
        assertTrue("payload is bounded", content.length <= 3600 + "\n[truncated]".length)
        assertFalse("raw_signals are stripped", content.contains("raw_signals"))
        assertFalse("source_signal_id is stripped", content.contains("source_signal_id"))
        assertTrue("keeps the top effect", content.contains("gastrointestinal bleeding"))
        assertTrue("keeps source URLs", content.contains("https://example.com/source-1"))
    }

    @Test
    fun multipleRoundsKeepUserContextNatively() = runBlocking {
        val provider = MultiRoundProvider()
        val orchestrator = AgentOrchestrator(
            dispatcher = ToolDispatcher(LargeReportRepository()),
            provider = provider,
            repository = LargeReportRepository(),
        )

        val result = orchestrator.runTurn(ChatSession(), "is warfarin with zanubrutinib safe?")

        assertEquals("Final pharmacist answer.", result.finalText)
        // The first prompt must carry the user's question to the provider.
        assertTrue(provider.userMessagesSent.first().contains("zanubrutinib"))
        // Round 2 happens via sendToolResults, which has no separate user re-send —
        // it relies on the native Conversation state, not on re-sending the user text.
        assertEquals(1, provider.userMessagesSent.size)
        assertEquals(3, provider.totalSends)
    }

    @Test
    fun completeCurrentTurnPairWinsOverPriorSessionPair() = runBlocking {
        val session = ChatSession()
        val repository = PairTrackingRepository()
        val orchestrator = AgentOrchestrator(
            dispatcher = ToolDispatcher(repository),
            provider = StaticProvider("I did not find a flagged interaction in the evidence I checked."),
            repository = repository,
        )

        orchestrator.runTurn(session, "what about warfarin and zanubrutinib")
        val result = orchestrator.runTurn(session, "amiodarone and fluorouracil?")
        orchestrator.runTurn(session, "can you take fluorouracil and")

        assertTrue("uses current complete pair", result.finalText.contains("amiodarone"))
        assertTrue("uses current complete pair", result.finalText.contains("fluorouracil"))
        assertFalse("does not fall back to old pair text", result.finalText.contains("zanubrutinib"))
        assertEquals(
            listOf(
                listOf("warfarin", "zanubrutinib"),
                listOf("amiodarone", "fluorouracil"),
            ),
            repository.requests,
        )
    }

    @Test
    fun duplicateIngredientWarningOverridesPlainNoFindingText() = runBlocking {
        val session = ChatSession()
        val repository = PairTrackingRepository()
        val orchestrator = AgentOrchestrator(
            dispatcher = ToolDispatcher(repository),
            provider = StaticProvider("I did not find a flagged interaction between Crocin and DOLO 650 in the evidence I checked."),
            repository = repository,
        )

        val result = orchestrator.runTurn(session, "what about crocin and DOLO 650?")

        assertTrue("keeps duplicate ingredient warning", result.finalText.contains("same active ingredient"))
        assertTrue("names acetaminophen", result.finalText.contains("acetaminophen"))
    }
}

private class ContradictoryReportProvider : NativeToolProvider {
    override val name: String = "contradictory-llm"

    override suspend fun startTurn(
        systemPrompt: String,
        priorTranscript: List<AgentMessage>,
        tools: List<ToolSchema>,
    ): TurnSession = object : TurnSession {
        private var sentReport = false

        override suspend fun sendUser(content: String): ToolModelResponse =
            ToolModelResponse(
                text = "",
                tool_calls = listOf(
                    ToolCall(
                        id = "call-1",
                        name = "build_structured_report",
                        args = mapOf("medication_names" to """["warfarin","zanubrutinib"]"""),
                    ),
                ),
            )

        override suspend fun sendToolResults(results: List<ToolResultPayload>): ToolModelResponse {
            sentReport = true
            return ToolModelResponse(
                text = "I did not find a flagged interaction between warfarin and zanubrutinib in the evidence I checked.",
                tool_calls = emptyList(),
            )
        }

        override fun close() {
            assertTrue("provider received the report first", sentReport)
        }
    }
}

private class StaticProvider(
    private val text: String,
) : NativeToolProvider {
    override val name: String = "test-llm"
    override suspend fun startTurn(
        systemPrompt: String,
        priorTranscript: List<AgentMessage>,
        tools: List<ToolSchema>,
    ): TurnSession = object : TurnSession {
        override suspend fun sendUser(content: String): ToolModelResponse =
            ToolModelResponse(text = text, tool_calls = emptyList())
        override suspend fun sendToolResults(results: List<ToolResultPayload>): ToolModelResponse =
            ToolModelResponse(text = text, tool_calls = emptyList())
        override fun close() = Unit
    }
}

private class CapturingProvider : NativeToolProvider {
    override val name: String = "capturing-llm"
    var calls = 0
    var toolResultsSeenOnRound2: List<ToolResultPayload> = emptyList()

    override suspend fun startTurn(
        systemPrompt: String,
        priorTranscript: List<AgentMessage>,
        tools: List<ToolSchema>,
    ): TurnSession = object : TurnSession {
        override suspend fun sendUser(content: String): ToolModelResponse {
            calls += 1
            return ToolModelResponse(
                text = "",
                tool_calls = listOf(
                    ToolCall(
                        id = "call-1",
                        name = "build_structured_report",
                        args = mapOf("medication_names" to """["zanubrutinib","warfarin"]"""),
                    ),
                ),
            )
        }
        override suspend fun sendToolResults(results: List<ToolResultPayload>): ToolModelResponse {
            calls += 1
            toolResultsSeenOnRound2 = results
            return ToolModelResponse(text = "LLM final answer.", tool_calls = emptyList())
        }
        override fun close() = Unit
    }
}

private class MultiRoundProvider : NativeToolProvider {
    override val name: String = "multi-round-llm"
    val userMessagesSent = mutableListOf<String>()
    var totalSends = 0

    override suspend fun startTurn(
        systemPrompt: String,
        priorTranscript: List<AgentMessage>,
        tools: List<ToolSchema>,
    ): TurnSession = object : TurnSession {
        var round = 0
        override suspend fun sendUser(content: String): ToolModelResponse {
            userMessagesSent += content
            totalSends += 1
            round += 1
            return ToolModelResponse(
                text = "",
                tool_calls = listOf(
                    ToolCall(id = "call-1", name = "normalize_medications", args = mapOf("names" to """["warfarin","zanubrutinib"]""")),
                ),
            )
        }
        override suspend fun sendToolResults(results: List<ToolResultPayload>): ToolModelResponse {
            totalSends += 1
            round += 1
            return if (round == 2) {
                ToolModelResponse(
                    text = "",
                    tool_calls = listOf(
                        ToolCall(id = "call-2", name = "build_structured_report", args = mapOf("medication_names" to """["warfarin","zanubrutinib"]""")),
                    ),
                )
            } else {
                ToolModelResponse(text = "Final pharmacist answer.", tool_calls = emptyList())
            }
        }
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
            normalized_medications = emptyList(),
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
        raw_signals = (1..50).map {
            com.medlens.core.data.model.RawDdiSignal(
                source_file = "source.csv",
                source_row_number = it,
                source_signal_id = "raw-$it",
                region = "us",
                drug1_raw = "warfarin",
                drug2_raw = "zanubrutinib",
                adverse_effect = "gastrointestinal bleeding",
                severity = "Major",
                mechanism_or_rationale = "very long raw mechanism ".repeat(50),
                interaction_category = "bleeding",
                interaction_direction = "",
                evidence_basis = "basis",
                source_basis = "basis",
                source_urls = "https://example.com/raw-$it",
                population_relevance = "",
                patient_risk_flags = "",
                dataset_type = "",
                use_case_note = "",
            )
        },
        evidence_source = "fixture",
    )
}

private class PairTrackingRepository : NoopSafetyRepository() {
    val requests = mutableListOf<List<String>>()

    override suspend fun buildStructuredReport(medicationNames: List<String>, effectLimit: Int): MedicationSafetyReport {
        requests += medicationNames
        val lowerNames = medicationNames.map { it.lowercase() }
        val finding = when {
            "warfarin" in lowerNames && "zanubrutinib" in lowerNames ->
                interaction("warfarin", "zanubrutinib", "gastrointestinal bleeding")
            "amiodarone" in lowerNames && "fluorouracil" in lowerNames ->
                interaction("amiodarone", "fluorouracil", "QT prolongation or infection risk")
            else -> null
        }
        val duplicateWarnings = if ("crocin" in lowerNames && "dolo 650" in lowerNames) {
            listOf(
                DuplicateIngredientWarning(
                    ingredient = "acetaminophen",
                    input_names = medicationNames,
                    practical_risk_tier = "dose_limit_check",
                    practical_summary = "Crocin and DOLO 650 may contain the same active ingredient, acetaminophen.",
                    dose_context_needed = "Check the total daily acetaminophen dose before taking them together.",
                    risk_factor_questions = "",
                    source_urls = emptyList(),
                ),
            )
        } else {
            emptyList()
        }
        return MedicationSafetyReport(
            input_medications = medicationNames,
            normalized_medications = medicationNames.mapIndexed { index, name ->
                NormalizedMedication(
                    input_name = name,
                    normalized_input = name.lowercase(),
                    canonical_name = if (name.equals("crocin", ignoreCase = true) || name.equals("dolo 650", ignoreCase = true)) {
                        "acetaminophen"
                    } else {
                        name.lowercase()
                    },
                    drug_id = index.toLong(),
                    matched_alias = name,
                    resolved = true,
                )
            },
            unresolved_medications = emptyList(),
            checked_pair_count = if (medicationNames.size >= 2) 1 else 0,
            overall_severity = if (finding == null) "None" else "Major",
            evidence_status = if (finding == null) "no_reference_findings" else "verified_reference_findings",
            limitations = emptyList(),
            duplicate_ingredient_warnings = duplicateWarnings,
            findings = listOfNotNull(finding),
        )
    }

    private fun interaction(drugA: String, drugB: String, effect: String): KnownInteraction =
        KnownInteraction(
            found = true,
            drug_a = drugA,
            drug_b = drugB,
            severity = "Major",
            row_count = 1,
            source_regions = listOf("india"),
            evidence_bases = listOf("screening signal"),
            source_bases = listOf("screening signal"),
            source_urls = listOf("https://example.com/$drugA-$drugB"),
            mechanisms = emptyList(),
            risk_flags = emptyList(),
            dataset_types = emptyList(),
            use_case_notes = emptyList(),
            effects = listOf(
                com.medlens.core.data.model.InteractionEffect(
                    adverse_effect = effect,
                    severity = "Major",
                    row_count = 1,
                    source_regions = listOf("india"),
                ),
            ),
            raw_signals = emptyList(),
            evidence_source = "fixture",
        )
}
