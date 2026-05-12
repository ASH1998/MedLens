package com.medlens.core.agent

import com.medlens.core.agent.model.AgentMessage
import com.medlens.core.agent.model.ChatSession
import com.medlens.core.agent.model.NativeToolProvider
import com.medlens.core.agent.model.ToolCall
import com.medlens.core.agent.model.ToolModelResponse
import com.medlens.core.agent.model.ToolSchema
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
    fun noProviderThrowsInsteadOfGeneratingFallbackText() {
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
    fun blankModelOutputThrowsInsteadOfGeneratingFallbackText() {
        val session = ChatSession()
        val orchestrator = AgentOrchestrator(
            dispatcher = ToolDispatcher(NoopSafetyRepository()),
            provider = StaticProvider(""),
        )

        assertThrows(IllegalStateException::class.java) {
            runBlocking { orchestrator.runTurn(session, "zanubrutinib with warfarin") }
        }
        assertEquals(emptyList<AgentMessage>(), session.transcript)
    }

    @Test
    fun finalTextComesFromProvider() = runBlocking {
        val session = ChatSession()
        val orchestrator = AgentOrchestrator(
            dispatcher = ToolDispatcher(NoopSafetyRepository()),
            provider = StaticProvider("This is an LLM answer."),
        )

        val result = orchestrator.runTurn(session, "zanubrutinib with warfarin")

        assertEquals("This is an LLM answer.", result.finalText)
        assertEquals("This is an LLM answer.", session.transcript.last().content)
    }

    @Test
    fun toolResultsAreCompactedBeforeReturningToProvider() = runBlocking {
        val provider = CapturingProvider()
        val orchestrator = AgentOrchestrator(
            dispatcher = ToolDispatcher(LargeReportRepository()),
            provider = provider,
        )

        orchestrator.runTurn(ChatSession(), "zanubrutinib with warfarin")

        val toolMessage = provider.secondMessages.single { it.role == "tool" }
        assertTrue(toolMessage.content.length <= 3600 + "\n[truncated]".length)
        assertFalse(toolMessage.content.contains("raw_signals"))
        assertFalse(toolMessage.content.contains("source_signal_id"))
        assertTrue(toolMessage.content.contains("gastrointestinal bleeding"))
        assertTrue(toolMessage.content.contains("https://example.com/source-1"))
    }
}

private class StaticProvider(
    private val text: String,
) : NativeToolProvider {
    override val name: String = "test-llm"

    override suspend fun generateWithTools(
        systemPrompt: String,
        messages: List<AgentMessage>,
        tools: List<ToolSchema>,
    ): ToolModelResponse = ToolModelResponse(text = text, tool_calls = emptyList())
}

private class CapturingProvider : NativeToolProvider {
    override val name: String = "capturing-llm"
    var calls = 0
    var secondMessages: List<AgentMessage> = emptyList()

    override suspend fun generateWithTools(
        systemPrompt: String,
        messages: List<AgentMessage>,
        tools: List<ToolSchema>,
    ): ToolModelResponse {
        calls += 1
        if (calls == 1) {
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
        secondMessages = messages
        return ToolModelResponse(text = "LLM final answer.", tool_calls = emptyList())
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
        error("Not used")

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
