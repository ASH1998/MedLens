package com.medlens.core.agent

import com.medlens.core.agent.model.ChatSession
import com.medlens.core.data.SafetyRepository
import com.medlens.core.data.model.AliasSearchResult
import com.medlens.core.data.model.CommonMedicineProfile
import com.medlens.core.data.model.CommonMedicineRow
import com.medlens.core.data.model.DrugInteractionList
import com.medlens.core.data.model.DuplicateIngredientWarning
import com.medlens.core.data.model.EvidenceImportFile
import com.medlens.core.data.model.FindPairsByEffectResult
import com.medlens.core.data.model.ImportIssue
import com.medlens.core.data.model.InteractionEffect
import com.medlens.core.data.model.KnownInteraction
import com.medlens.core.data.model.MedicationSafetyReport
import com.medlens.core.data.model.NormalizedMedication
import com.medlens.core.data.model.PairEffectsResult
import com.medlens.core.data.model.RawDdiSignal
import com.medlens.core.data.model.RegionSeverity
import com.medlens.core.data.model.SeverityConsensusResult
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * JVM parity tests for the ToolDispatcher dispatch layer.
 *
 * These mirror the Python-side behaviors:
 *  - normalization (alias resolution, multi-ingredient expansion)
 *  - pair checking (Advil + Warfarin → Major)
 *  - structured reports (unresolved names in limitations)
 *  - evidence source listing
 *  - getPairEffects / severityConsensus / listImportIssues
 *
 * Because SqliteSafetyRepository depends on android.database.sqlite,
 * we test through ToolDispatcher + a fixture-backed SafetyRepository.
 */
class ToolParityTest {

    // ── 1. Dolo normalizes to acetaminophen (via alias table) ──────────

    @Test
    fun `Dolo normalizes to acetaminophen`() = runBlocking {
        val repo = ParityRepository()
        val dispatcher = ToolDispatcher(repo)
        val session = ChatSession()

        val result = dispatcher.dispatch(
            name = "normalize_medications",
            args = mapOf("names" to """["Dolo"]"""),
            session = session,
        )

        assertNotNull("dispatch must return json", result["json"])
        val json = result["json"]!!
        assertTrue("must contain canonical_name acetaminophen", json.contains("acetaminophen"))
        assertTrue("must contain input_name Dolo", json.contains("Dolo"))
        assertTrue("must be resolved", json.contains("\"resolved\":true"))
        assertFalse("must not be an error", result.containsKey("error"))
    }

    // ── 2. Clavam expands to amoxicillin + clavulanate ingredients ────

    @Test
    fun `Clavam expands to amoxicillin and clavulanate ingredients`() = runBlocking {
        val repo = ParityRepository()
        val dispatcher = ToolDispatcher(repo)
        val session = ChatSession()

        val result = dispatcher.dispatch(
            name = "normalize_medications",
            args = mapOf("names" to """["Clavam"]"""),
            session = session,
        )

        assertNotNull("dispatch must return json", result["json"])
        val json = result["json"]!!
        assertTrue("must contain amoxicillin", json.contains("amoxicillin"))
        assertTrue("must contain clavulanic acid", json.contains("clavulanic acid"))
        assertFalse("must not be an error", result.containsKey("error"))
    }

    // ── 3. Advil and Warfarin returns a flagged finding (Major) ───────

    @Test
    fun `Advil and Warfarin returns Major severity finding`() = runBlocking {
        val repo = ParityRepository()
        val dispatcher = ToolDispatcher(repo)
        val session = ChatSession()

        val result = dispatcher.dispatch(
            name = "build_structured_report",
            args = mapOf("medication_names" to """["Advil","Warfarin"]"""),
            session = session,
        )

        assertNotNull("dispatch must return json", result["json"])
        val json = result["json"]!!
        assertTrue("overall severity must be Major", json.contains("\"overall_severity\":\"Major\""))
        assertTrue("must contain drug ibuprofen", json.contains("ibuprofen"))
        assertTrue("must contain drug warfarin", json.contains("warfarin"))
        assertTrue("must contain severity Major in finding", json.contains("\"severity\":\"Major\""))
        assertTrue("must contain adverse effect bleeding", json.contains("bleeding"))
        assertTrue("must reference USA region", json.contains("USA"))
        assertFalse("must not be an error", result.containsKey("error"))
        assertTrue("session lastReport must be set", session.lastReport != null)
    }

    // ── 4. Unresolved names appear in report limitations ──────────────

    @Test
    fun `unresolved names appear in report limitations`() = runBlocking {
        val repo = ParityRepository()
        val dispatcher = ToolDispatcher(repo)
        val session = ChatSession()

        val result = dispatcher.dispatch(
            name = "build_structured_report",
            args = mapOf("medication_names" to """["Advil","MysteryPill"]"""),
            session = session,
        )

        assertNotNull("dispatch must return json", result["json"])
        val json = result["json"]!!
        assertTrue(
            "limitations must mention MysteryPill",
            json.contains("MysteryPill"),
        )
        assertTrue(
            "must contain unresolved_medications",
            json.contains("unresolved_medications"),
        )
        assertTrue(
            "must mention could not be normalized in limitations",
            json.contains("could not be normalized") || json.contains("not checked"),
        )
        assertFalse("must not be an error", result.containsKey("error"))
    }

    // ── 5. Evidence source listing returns imported source files ──────

    @Test
    fun `evidence source listing returns imported source files`() = runBlocking {
        val repo = ParityRepository()
        val dispatcher = ToolDispatcher(repo)
        val session = ChatSession()

        val result = dispatcher.dispatch(
            name = "list_evidence_sources",
            args = emptyMap(),
            session = session,
        )

        assertNotNull("dispatch must return json", result["json"])
        val json = result["json"]!!
        assertTrue("must contain FDA_DailyMed source", json.contains("FDA_DailyMed"))
        assertTrue("must contain EU_EMA source", json.contains("EU_EMA"))
        assertTrue("must contain region us", json.contains("\"region\":\"us\""))
        assertFalse("must not be an error", result.containsKey("error"))
    }

    // ── 6. getPairEffects returns effects for a known pair ────────────

    @Test
    fun `getPairEffects returns effects for known pair`() = runBlocking {
        val repo = ParityRepository()
        val dispatcher = ToolDispatcher(repo)
        val session = ChatSession()

        val result = dispatcher.dispatch(
            name = "get_pair_effects",
            args = mapOf("drug_a" to "ibuprofen", "drug_b" to "warfarin"),
            session = session,
        )

        assertNotNull("dispatch must return json", result["json"])
        val json = result["json"]!!
        assertTrue("must be found", json.contains("\"found\":true"))
        assertTrue("must contain drug_a ibuprofen", json.contains("ibuprofen"))
        assertTrue("must contain drug_b warfarin", json.contains("warfarin"))
        assertTrue("must contain adverse effect", json.contains("increased bleeding risk"))
        assertFalse("must not be an error", result.containsKey("error"))
    }

    // ── 7. severityConsensus returns correct rolled-up severity ──────

    @Test
    fun `severityConsensus returns correct rolled up severity`() = runBlocking {
        val repo = ParityRepository()
        val dispatcher = ToolDispatcher(repo)
        val session = ChatSession()

        val result = dispatcher.dispatch(
            name = "severity_consensus",
            args = mapOf("drug_a" to "ibuprofen", "drug_b" to "warfarin"),
            session = session,
        )

        assertNotNull("dispatch must return json", result["json"])
        val json = result["json"]!!
        assertTrue("must be found", json.contains("\"found\":true"))
        assertTrue("rolled_up_severity must be Major", json.contains("\"rolled_up_severity\":\"Major\""))
        assertTrue("must contain region USA", json.contains("USA"))
        assertTrue("must contain region EU", json.contains("EU"))
        assertFalse("must not be an error", result.containsKey("error"))
    }

    // ── 8. listImportIssues returns unresolved rows ───────────────────

    @Test
    fun `listImportIssues returns unresolved rows`() = runBlocking {
        val repo = ParityRepository()
        val dispatcher = ToolDispatcher(repo)
        val session = ChatSession()

        val result = dispatcher.dispatch(
            name = "list_import_issues",
            args = emptyMap(),
            session = session,
        )

        assertNotNull("dispatch must return json", result["json"])
        val json = result["json"]!!
        assertTrue("must contain drug1 unknown_x", json.contains("unknown_x"))
        assertTrue("must contain drug2 unknown_y", json.contains("unknown_y"))
        assertTrue("must contain reason", json.contains("unresolved"))
        assertTrue("must contain source_file", json.contains("FDA_DailyMed"))
        assertFalse("must not be an error", result.containsKey("error"))
    }

    // ── 9. add_medications + session tracking ─────────────────────────

    @Test
    fun `add_medications adds resolved drug to session`() = runBlocking {
        val repo = ParityRepository()
        val dispatcher = ToolDispatcher(repo)
        val session = ChatSession()

        dispatcher.dispatch(
            name = "add_medications",
            args = mapOf("names" to """["Dolo"]"""),
            session = session,
        )

        assertTrue("session must have 1 medication", session.medications.size == 1)
        assertEquals("acetaminophen", session.medications[0].canonical_name)
        assertTrue("medication must be resolved", session.medications[0].resolved)
    }

    // ── 10. clear_medications resets session ───────────────────────────

    @Test
    fun `clear_medications empties session list`() = runBlocking {
        val repo = ParityRepository()
        val dispatcher = ToolDispatcher(repo)
        val session = ChatSession()

        // Add first
        dispatcher.dispatch(
            name = "add_medications",
            args = mapOf("names" to """["Dolo"]"""),
            session = session,
        )
        assertTrue("session must have medication after add", session.medications.isNotEmpty())

        // Clear
        val result = dispatcher.dispatch(
            name = "clear_medications",
            args = emptyMap(),
            session = session,
        )
        assertTrue("session must be empty after clear", session.medications.isEmpty())
        assertTrue("must return text response", result.containsKey("text"))
    }

    // ── 11. list_interactions_for_drug returns interactions ────────────

    @Test
    fun `list_interactions_for_drug returns interaction list`() = runBlocking {
        val repo = ParityRepository()
        val dispatcher = ToolDispatcher(repo)
        val session = ChatSession()

        val result = dispatcher.dispatch(
            name = "list_interactions_for_drug",
            args = mapOf("drug" to "warfarin"),
            session = session,
        )

        assertNotNull("dispatch must return json", result["json"])
        val json = result["json"]!!
        assertTrue("must contain interactions array", json.contains("interactions"))
        assertTrue("must contain ibuprofen pair", json.contains("ibuprofen"))
        assertFalse("must not be an error", result.containsKey("error"))
    }

    // ── 12. search_drug_aliases returns alias matches ─────────────────

    @Test
    fun `search_drug_aliases returns alias matches`() = runBlocking {
        val repo = ParityRepository()
        val dispatcher = ToolDispatcher(repo)
        val session = ChatSession()

        val result = dispatcher.dispatch(
            name = "search_drug_aliases",
            args = mapOf("query" to "dolo"),
            session = session,
        )

        assertNotNull("dispatch must return json", result["json"])
        val json = result["json"]!!
        assertTrue("must contain acetaminophen canonical", json.contains("acetaminophen"))
        assertFalse("must not be an error", result.containsKey("error"))
    }

    // ── 13. find_pairs_by_effect returns matching pairs ───────────────

    @Test
    fun `find_pairs_by_effect returns matching pairs for bleeding`() = runBlocking {
        val repo = ParityRepository()
        val dispatcher = ToolDispatcher(repo)
        val session = ChatSession()

        val result = dispatcher.dispatch(
            name = "find_pairs_by_effect",
            args = mapOf("effect" to "bleeding"),
            session = session,
        )

        assertNotNull("dispatch must return json", result["json"])
        val json = result["json"]!!
        assertTrue("must contain effect_query", json.contains("bleeding"))
        assertTrue("must contain ibuprofen", json.contains("ibuprofen"))
        assertTrue("must contain warfarin", json.contains("warfarin"))
        assertFalse("must not be an error", result.containsKey("error"))
    }

    // ── 14. unknown tool returns error ────────────────────────────────

    @Test
    fun `unknown tool dispatch returns error`() = runBlocking {
        val repo = ParityRepository()
        val dispatcher = ToolDispatcher(repo)
        val session = ChatSession()

        val result = dispatcher.dispatch(
            name = "nonexistent_tool",
            args = emptyMap(),
            session = session,
        )

        assertTrue("must contain error key", result.containsKey("error"))
        assertTrue(
            "error must mention tool not ported",
            result["error"]!!.contains("not yet ported") || result["error"]!!.contains("nonexistent_tool"),
        )
    }

    // ── 15. Duplicate ingredient warning for Dolo + Paracetamol ───────

    @Test
    fun `duplicate ingredient warning for same ingredient inputs`() = runBlocking {
        val repo = ParityRepository()
        val dispatcher = ToolDispatcher(repo)
        val session = ChatSession()

        val result = dispatcher.dispatch(
            name = "build_structured_report",
            args = mapOf("medication_names" to """["Dolo","Paracetamol"]"""),
            session = session,
        )

        assertNotNull("dispatch must return json", result["json"])
        val json = result["json"]!!
        assertTrue(
            "must contain duplicate_ingredient_warnings",
            json.contains("duplicate_ingredient_warnings"),
        )
        assertTrue("must mention acetaminophen", json.contains("acetaminophen"))
        assertTrue("must list both input names", json.contains("Dolo") && json.contains("Paracetamol"))
        assertFalse("must not be an error", result.containsKey("error"))
    }
}

// ────────────────────────────────────────────────────────────────────────
// Fixture repository that mimics real SqliteSafetyRepository behaviour
// with hand-crafted data for each parity scenario.
// ────────────────────────────────────────────────────────────────────────

private class ParityRepository : SafetyRepository {

    // ── normalization fixture ──────────────────────────────────────────

    // Canonical drugs (id → canonical_name)
    private val drugs = mapOf(
        1L to "acetaminophen",
        2L to "ibuprofen",
        3L to "warfarin",
        4L to "amoxicillin",
        5L to "clavulanic acid",
    )

    // Aliases (alias → (drug_id, canonical_name))
    private val aliases = mapOf(
        "dolo" to (1L to "acetaminophen"),
        "paracetamol" to (1L to "acetaminophen"),
        "tylenol" to (1L to "acetaminophen"),
        "advil" to (2L to "ibuprofen"),
        "motrin" to (2L to "ibuprofen"),
        "warfarin" to (3L to "warfarin"),
        "coumadin" to (3L to "warfarin"),
        "amoxicillin" to (4L to "amoxicillin"),
        "clavulanate" to (5L to "clavulanic acid"),
        "clavam" to (4L to "amoxicillin"), // brand alias maps to amoxicillin only
    )

    // Brand-to-ingredient map for multi-ingredient expansion (like Clavam 625)
    private val brandIngredientMap = mapOf(
        "clavam" to listOf("amoxicillin", "clavulanate"),
        "clavam 625" to listOf("amoxicillin", "clavulanate"),
        "augmentin" to listOf("amoxicillin", "clavulanate"),
    )

    // ── evidence fixtures ──────────────────────────────────────────────

    private val ibuprofenWarfarinInteraction = KnownInteraction(
        found = true,
        drug_a = "ibuprofen",
        drug_b = "warfarin",
        severity = "Major",
        row_count = 12,
        source_regions = listOf("USA", "EU"),
        evidence_bases = listOf("DailyMed", "FDA_FAERS"),
        source_bases = listOf("DailyMed", "FDA_FAERS"),
        source_urls = listOf("https://dailymed.nlm.nih.gov", "https://fda.gov/faers"),
        mechanisms = listOf("NSAIDs inhibit platelet aggregation, increasing anticoagulant bleeding risk"),
        risk_flags = listOf("elderly", "anticoagulant users"),
        dataset_types = listOf("label", "faers"),
        use_case_notes = emptyList(),
        effects = listOf(
            InteractionEffect(
                adverse_effect = "increased bleeding risk",
                severity = "Major",
                row_count = 8,
                source_regions = listOf("USA", "EU"),
            ),
            InteractionEffect(
                adverse_effect = "gastrointestinal hemorrhage",
                severity = "Major",
                row_count = 4,
                source_regions = listOf("USA"),
            ),
        ),
        raw_signals = emptyList(),
        evidence_source = "ddi_reference",
    )

    private val evidenceSources = listOf(
        EvidenceImportFile(
            source_file = "FDA_DailyMed",
            region = "us",
            rows_seen = 5200,
            rows_imported = 4800,
            rows_unresolved = 400,
            unique_pairs_imported = 1200,
        ),
        EvidenceImportFile(
            source_file = "EU_EMA",
            region = "eu/eea",
            rows_seen = 3100,
            rows_imported = 2900,
            rows_unresolved = 200,
            unique_pairs_imported = 800,
        ),
    )

    private val importIssues = listOf(
        ImportIssue(
            source_file = "FDA_DailyMed",
            row_number = 42,
            drug1 = "unknown_x",
            drug2 = "unknown_y",
            normalized_drug1 = "unknown_x",
            normalized_drug2 = "unknown_y",
            reason = "unresolved alias: neither drug could be normalized",
        ),
        ImportIssue(
            source_file = "EU_EMA",
            row_number = 99,
            drug1 = "foo-drug",
            drug2 = "bar-drug",
            normalized_drug1 = "foo drug",
            normalized_drug2 = "bar drug",
            reason = "unresolved alias: pair not in reference set",
        ),
    )

    // ── SafetyRepository implementation ────────────────────────────────

    override suspend fun normalizeMedications(names: List<String>): List<NormalizedMedication> =
        names.flatMap { name ->
            val lower = name.lowercase().trim()

            // Check multi-ingredient brand map first (Clavam 625, Augmentin, etc.)
            val brandKey = lower
            val brandEntry = brandIngredientMap[brandKey]
            if (brandEntry != null) {
                return@flatMap brandEntry.map { ingredient ->
                    val ingredientLower = ingredient.lowercase()
                    val match = aliases[ingredientLower]
                    NormalizedMedication(
                        input_name = name,
                        normalized_input = lower,
                        canonical_name = match?.second ?: ingredient,
                        drug_id = match?.first,
                        matched_alias = brandKey,
                        resolved = true,
                    )
                }
            }

            // Standard alias lookup
            val match = aliases[lower]
            if (match != null) {
                listOf(
                    NormalizedMedication(
                        input_name = name,
                        normalized_input = lower,
                        canonical_name = match.second,
                        drug_id = match.first,
                        matched_alias = lower,
                        resolved = true,
                    ),
                )
            } else {
                listOf(
                    NormalizedMedication(
                        input_name = name,
                        normalized_input = lower,
                        canonical_name = null,
                        drug_id = null,
                        matched_alias = null,
                        resolved = false,
                    ),
                )
            }
        }

    override suspend fun searchDrugAliases(query: String, limit: Int): List<AliasSearchResult> {
        val q = query.lowercase().trim()
        val grouped = linkedMapOf<String, MutableList<String>>()
        aliases.forEach { (alias, pair) ->
            if (alias.contains(q) || pair.second.contains(q)) {
                grouped.getOrPut(pair.second) { mutableListOf() }.add(alias)
            }
        }
        return grouped.entries.take(limit).map { (canonical, aliasList) ->
            AliasSearchResult(canonical = canonical, aliases = aliasList.take(5))
        }
    }

    override suspend fun lookupKnownInteraction(
        drugA: String,
        drugB: String,
        effectLimit: Int,
        rawSignalLimit: Int,
    ): KnownInteraction {
        val normA = drugA.lowercase().trim()
        val normB = drugB.lowercase().trim()
        val pair = setOf(normA, normB)
        return if (pair == setOf("ibuprofen", "warfarin")) {
            ibuprofenWarfarinInteraction.copy(
                effects = ibuprofenWarfarinInteraction.effects.take(effectLimit),
            )
        } else {
            notFound(normA, normB)
        }
    }

    override suspend fun listInteractionsForDrug(
        drug: String,
        limit: Int,
        effectLimit: Int,
        minSeverity: String?,
        region: String?,
        riskFlag: String?,
    ): DrugInteractionList {
        val normalized = normalizeMedications(listOf(drug)).first()
        val interactions = if (normalized.canonical_name == "warfarin") {
            listOf(ibuprofenWarfarinInteraction.copy(effects = ibuprofenWarfarinInteraction.effects.take(effectLimit)))
        } else {
            emptyList()
        }
        return DrugInteractionList(normalized = normalized, interactions = interactions.take(limit))
    }

    override suspend fun buildStructuredReport(
        medicationNames: List<String>,
        effectLimit: Int,
    ): MedicationSafetyReport {
        val normalized = normalizeMedications(medicationNames)
        val unresolved = normalized.filterNot { it.resolved }
        val resolvedUnique = dedupeResolved(normalized)
        val findings = mutableListOf<KnownInteraction>()
        var checkedPairCount = 0

        for (i in resolvedUnique.indices) {
            for (j in i + 1 until resolvedUnique.size) {
                val left = resolvedUnique[i]
                val right = resolvedUnique[j]
                if (left.canonical_name == null || right.canonical_name == null) continue
                checkedPairCount += 1
                val interaction = lookupKnownInteraction(
                    left.canonical_name!!, right.canonical_name!!, effectLimit,
                )
                if (interaction.found) findings += interaction
            }
        }

        val ranked = findings.sortedWith(
            compareByDescending<KnownInteraction> { severityRank(it.severity) }
                .thenByDescending { it.row_count },
        )

        val duplicateWarnings = duplicateIngredientWarnings(normalized)

        return MedicationSafetyReport(
            input_medications = medicationNames,
            normalized_medications = normalized,
            unresolved_medications = unresolved,
            checked_pair_count = checkedPairCount,
            findings = ranked,
            duplicate_ingredient_warnings = duplicateWarnings,
            overall_severity = overallSeverity(ranked),
            evidence_status = evidenceStatus(ranked, unresolved, checkedPairCount),
            limitations = reportLimitations(unresolved, checkedPairCount, ranked),
        )
    }

    override suspend fun getCommonMedicineProfile(
        name: String,
        limit: Int,
    ): CommonMedicineProfile = CommonMedicineProfile(
        query = name,
        normalized = normalizeMedications(listOf(name)).first(),
        aliases = emptyList(),
        matches = emptyList(),
    )

    override suspend fun searchCommonMedicines(
        query: String?,
        therapeuticCategory: String?,
        otcOrRx: String?,
        nlemOrJanAushadhi: String?,
        riskFlag: String?,
        limit: Int,
    ): List<CommonMedicineRow> = emptyList()

    override suspend fun listEvidenceSources(): List<EvidenceImportFile> = evidenceSources

    override suspend fun getPairEffects(drugA: String, drugB: String, limit: Int): PairEffectsResult {
        val interaction = lookupKnownInteraction(drugA, drugB, effectLimit = limit)
        return PairEffectsResult(
            drug_a = interaction.drug_a,
            drug_b = interaction.drug_b,
            found = interaction.found,
            effects = interaction.effects,
        )
    }

    override suspend fun getRawSignals(
        drugA: String,
        drugB: String,
        limit: Int,
    ): List<RawDdiSignal> = emptyList()

    override suspend fun getFullRawSignals(
        drugA: String,
        drugB: String,
        limit: Int,
    ): List<RawDdiSignal> = emptyList()

    override suspend fun severityConsensus(
        drugA: String,
        drugB: String,
    ): SeverityConsensusResult {
        val normA = normalizeMedications(listOf(drugA)).filter { it.resolved }
        val normB = normalizeMedications(listOf(drugB)).filter { it.resolved }
        if (normA.isEmpty() || normB.isEmpty()) {
            return SeverityConsensusResult(
                drug_a = drugA.lowercase().trim(),
                drug_b = drugB.lowercase().trim(),
                found = false,
                rolled_up_severity = null,
                region_severities = emptyList(),
            )
        }

        val left = normA.first().canonical_name!!
        val right = normB.first().canonical_name!!
        val pair = setOf(left, right)
        return if (pair == setOf("ibuprofen", "warfarin")) {
            SeverityConsensusResult(
                drug_a = "ibuprofen",
                drug_b = "warfarin",
                found = true,
                rolled_up_severity = "Major",
                region_severities = listOf(
                    RegionSeverity(region = "USA", severity = "Major", row_count = 8),
                    RegionSeverity(region = "EU", severity = "Moderate", row_count = 3),
                ),
            )
        } else {
            SeverityConsensusResult(
                drug_a = left, drug_b = right, found = false,
                rolled_up_severity = null, region_severities = emptyList(),
            )
        }
    }

    override suspend fun findPairsByEffect(
        effect: String,
        limit: Int,
    ): FindPairsByEffectResult {
        val q = effect.lowercase().trim()
        val pairs = if ("bleeding" in q || "hemorrhage" in q) {
            listOf(
                com.medlens.core.data.model.EffectPairMatch(
                    drug_a = "ibuprofen",
                    drug_b = "warfarin",
                    severity = "Major",
                    matching_effect = "increased bleeding risk",
                    row_count = 8,
                ),
            )
        } else {
            emptyList()
        }
        return FindPairsByEffectResult(effect_query = effect, pairs = pairs.take(limit))
    }

    override suspend fun listImportIssues(
        sourceFile: String?,
        query: String?,
        limit: Int,
    ): List<ImportIssue> {
        var filtered = importIssues
        if (!sourceFile.isNullOrBlank()) {
            filtered = filtered.filter { it.source_file == sourceFile }
        }
        if (!query.isNullOrBlank()) {
            val q = query.lowercase().trim()
            filtered = filtered.filter {
                it.drug1.lowercase().contains(q) || it.drug2.lowercase().contains(q) ||
                    it.normalized_drug1.lowercase().contains(q) || it.normalized_drug2.lowercase().contains(q)
            }
        }
        return filtered.take(limit)
    }

    override fun close() = Unit

    // ── helpers ────────────────────────────────────────────────────────

    private fun dedupeResolved(items: List<NormalizedMedication>): List<NormalizedMedication> {
        val seen = mutableSetOf<String>()
        return items.filter { it.resolved && it.canonical_name != null && seen.add(it.canonical_name!!) }
    }

    private fun severityRank(severity: String?): Int = when (severity) {
        "Major" -> 3
        "Moderate" -> 2
        "Minor" -> 1
        else -> 0
    }

    private fun overallSeverity(findings: List<KnownInteraction>): String =
        findings.maxByOrNull { severityRank(it.severity) }?.severity ?: "None"

    private fun evidenceStatus(
        findings: List<KnownInteraction>,
        unresolved: List<NormalizedMedication>,
        checkedPairCount: Int,
    ): String = when {
        findings.isNotEmpty() && unresolved.isEmpty() -> "verified_reference_findings"
        findings.isNotEmpty() -> "verified_reference_findings_with_unresolved_inputs"
        checkedPairCount > 0 && unresolved.isNotEmpty() -> "no_reference_findings_with_unresolved_inputs"
        checkedPairCount > 0 -> "no_reference_findings"
        else -> "insufficient_resolved_medications"
    }

    private fun reportLimitations(
        unresolved: List<NormalizedMedication>,
        checkedPairCount: Int,
        findings: List<KnownInteraction>,
    ): List<String> {
        val items = mutableListOf(
            "This report uses curated DDI reference signals; it is a screening output, not patient-specific medical advice.",
        )
        if (unresolved.isNotEmpty()) {
            items += "Some medications could not be normalized and were not checked: ${unresolved.joinToString(", ") { it.input_name }}."
        }
        if (checkedPairCount == 0) {
            items += "Fewer than two medications were resolved, so no pairwise interaction check was possible."
        }
        if (findings.isEmpty() && checkedPairCount > 0) {
            items += "No known/reference DDI signal was found for the resolved medication pairs."
        }
        return items
    }

    private fun duplicateIngredientWarnings(
        normalized: List<NormalizedMedication>,
    ): List<DuplicateIngredientWarning> {
        val grouped = normalized
            .filter { it.resolved && it.canonical_name != null }
            .groupBy { it.canonical_name.orEmpty() }
            .mapValues { (_, items) -> items.map { it.input_name }.toSet().sorted() }

        return grouped.entries
            .filter { it.value.size > 1 }
            .map { (ingredient, inputNames) ->
                DuplicateIngredientWarning(
                    ingredient = ingredient,
                    input_names = inputNames,
                    practical_risk_tier = "duplicate_dose_risk",
                    practical_summary = "$ingredient appears in more than one product. This can turn an intended combination into duplicate dosing.",
                    dose_context_needed = "Ask the dose, frequency, duration, and whether any other medicine contains the same active ingredient.",
                    risk_factor_questions = "Liver disease or heavy alcohol use for acetaminophen/paracetamol.",
                    source_urls = emptyList(),
                )
            }
    }

    private fun notFound(drugA: String, drugB: String) = KnownInteraction(
        found = false,
        drug_a = drugA,
        drug_b = drugB,
        severity = null,
        row_count = 0,
        source_regions = emptyList(),
        evidence_bases = emptyList(),
        source_bases = emptyList(),
        source_urls = emptyList(),
        mechanisms = emptyList(),
        risk_flags = emptyList(),
        dataset_types = emptyList(),
        use_case_notes = emptyList(),
        effects = emptyList(),
        raw_signals = emptyList(),
        evidence_source = "ddi_reference",
    )
}
