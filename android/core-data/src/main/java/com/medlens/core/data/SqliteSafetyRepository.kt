package com.medlens.core.data

import android.database.Cursor
import android.database.sqlite.SQLiteDatabase
import com.medlens.core.data.model.AliasSearchResult
import com.medlens.core.data.model.CommonMedicineProfile
import com.medlens.core.data.model.CommonMedicineRow
import com.medlens.core.data.model.DrugInteractionList
import com.medlens.core.data.model.EvidenceImportFile
import com.medlens.core.data.model.InteractionEffect
import com.medlens.core.data.model.KnownInteraction
import com.medlens.core.data.model.MedicationSafetyReport
import com.medlens.core.data.model.NormalizedMedication
import com.medlens.core.data.model.RawDdiSignal
import com.medlens.core.data.util.normalizeLookupText
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlin.math.max
import kotlin.math.min

class SqliteSafetyRepository(
    private val installedDatabases: InstalledDatabases,
) : SafetyRepository {
    private val json = Json { ignoreUnknownKeys = true }
    private val openMutex = Mutex()
    private var normalizationDb: SQLiteDatabase? = null
    private var evidenceDb: SQLiteDatabase? = null

    override suspend fun normalizeMedications(names: List<String>): List<NormalizedMedication> =
        withContext(Dispatchers.IO) {
            val db = normalizationDb()
            names.map { name ->
                val normalizedInput = normalizeLookupText(name)
                val match = normalizationCandidates(normalizedInput).firstNotNullOfOrNull { candidate ->
                    lookupAlias(db, candidate)
                }
                if (match != null) {
                    NormalizedMedication(
                        input_name = name,
                        normalized_input = normalizedInput,
                        canonical_name = match.canonicalName,
                        drug_id = match.drugId,
                        matched_alias = match.alias,
                        resolved = true,
                    )
                } else {
                    NormalizedMedication(
                        input_name = name,
                        normalized_input = normalizedInput,
                        canonical_name = null,
                        drug_id = null,
                        matched_alias = null,
                        resolved = false,
                    )
                }
            }
        }

    private fun lookupAlias(db: SQLiteDatabase, normalizedInput: String): AliasLookup? =
        db.rawQuery(
            """
            SELECT d.id AS id, d.canonical_name AS canonical_name, a.alias AS alias
            FROM drug_alias a
            JOIN drug d ON d.id = a.drug_id
            WHERE a.normalized_alias = ?
            """.trimIndent(),
            arrayOf(normalizedInput),
        ).use { cursor ->
            if (!cursor.moveToFirst()) return@use null
            AliasLookup(
                drugId = cursor.long("id"),
                canonicalName = cursor.string("canonical_name"),
                alias = cursor.string("alias"),
            )
        }

    private fun normalizationCandidates(normalizedInput: String): List<String> {
        if (normalizedInput.isBlank()) return emptyList()
        val candidates = linkedSetOf(normalizedInput)
        val spacedDose = normalizedInput
            .replace(Regex("([a-z])([0-9])"), "$1 $2")
            .replace(Regex("([0-9])([a-z])"), "$1 $2")
            .replace(Regex("\\s+"), " ")
            .trim()
        candidates += spacedDose
        candidates += spacedDose
            .replace(Regex("\\s+(mg|mcg|g|ml|iu)$"), "")
            .trim()
        return candidates.filter { it.isNotBlank() }
    }

    override suspend fun searchDrugAliases(query: String, limit: Int): List<AliasSearchResult> =
        withContext(Dispatchers.IO) {
            val db = normalizationDb()
            val normalizedQuery = normalizeLookupText(query)
            if (normalizedQuery.isBlank()) return@withContext emptyList()
            val pattern = "%$normalizedQuery%"
            db.rawQuery(
                """
                SELECT d.canonical_name AS canonical_name, a.alias AS alias
                FROM drug_alias a
                JOIN drug d ON d.id = a.drug_id
                WHERE a.normalized_alias LIKE ? OR d.canonical_name LIKE ?
                ORDER BY
                    CASE WHEN a.normalized_alias = ? THEN 0 ELSE 1 END,
                    LENGTH(a.alias),
                    d.canonical_name,
                    a.alias
                LIMIT ?
                """.trimIndent(),
                arrayOf(pattern, pattern, normalizedQuery, max(1, limit * 4).toString()),
            ).use { cursor ->
                val grouped = linkedMapOf<String, MutableList<String>>()
                while (cursor.moveToNext()) {
                    val canonical = cursor.string("canonical_name")
                    val alias = cursor.string("alias")
                    val aliases = grouped.getOrPut(canonical) { mutableListOf() }
                    if (!aliases.contains(alias)) {
                        aliases += alias
                    }
                    if (grouped.size >= limit && grouped.values.all { it.size >= 3 }) {
                        break
                    }
                }
                val exactMatches = grouped.entries.take(limit).map { (canonical, aliases) ->
                    AliasSearchResult(canonical = canonical, aliases = aliases.take(5))
                }
                exactMatches.ifEmpty { fuzzyDrugAliases(db, normalizedQuery, limit) }
            }
        }

    private fun fuzzyDrugAliases(
        db: SQLiteDatabase,
        normalizedQuery: String,
        limit: Int,
    ): List<AliasSearchResult> {
        val queryLength = normalizedQuery.length
        if (queryLength < 4 || queryLength > 40) return emptyList()
        db.rawQuery(
            """
            SELECT d.canonical_name AS canonical_name, a.alias AS alias, a.normalized_alias AS normalized_alias
            FROM drug_alias a
            JOIN drug d ON d.id = a.drug_id
            WHERE LENGTH(a.normalized_alias) BETWEEN ? AND ?
            """.trimIndent(),
            arrayOf(max(1, queryLength - 2).toString(), (queryLength + 2).toString()),
        ).use { cursor ->
            val ranked = mutableListOf<Triple<String, String, Int>>()
            while (cursor.moveToNext()) {
                val canonical = cursor.string("canonical_name")
                val alias = cursor.string("alias")
                val normalizedAlias = cursor.string("normalized_alias")
                val score = min(
                    editDistance(normalizedQuery, normalizedAlias),
                    editDistance(normalizedQuery, normalizeLookupText(canonical)),
                )
                if (score <= max(1, queryLength / 4)) {
                    ranked += Triple(canonical, alias, score)
                }
            }
            val grouped = linkedMapOf<String, MutableList<String>>()
            ranked.sortedWith(compareBy<Triple<String, String, Int>> { it.third }.thenBy { it.second.length })
                .forEach { (canonical, alias, _) ->
                    val aliases = grouped.getOrPut(canonical) { mutableListOf() }
                    if (!aliases.contains(alias)) aliases += alias
                }
            return grouped.entries.take(limit).map { (canonical, aliases) ->
                AliasSearchResult(canonical = canonical, aliases = aliases.take(5))
            }
        }
    }

    private fun editDistance(a: String, b: String): Int {
        if (a == b) return 0
        if (a.isEmpty()) return b.length
        if (b.isEmpty()) return a.length
        val previous = IntArray(b.length + 1) { it }
        val current = IntArray(b.length + 1)
        for (i in 1..a.length) {
            current[0] = i
            for (j in 1..b.length) {
                current[j] = min(
                    min(previous[j] + 1, current[j - 1] + 1),
                    previous[j - 1] + if (a[i - 1] == b[j - 1]) 0 else 1,
                )
            }
            for (j in previous.indices) previous[j] = current[j]
        }
        return previous[b.length]
    }

    override suspend fun lookupKnownInteraction(
        drugA: String,
        drugB: String,
        effectLimit: Int,
        rawSignalLimit: Int,
    ): KnownInteraction = withContext(Dispatchers.IO) {
        val normalized = normalizeMedications(listOf(drugA, drugB))
        val left = normalized[0]
        val right = normalized[1]
        val pairA = left.canonical_name ?: left.normalized_input
        val pairB = right.canonical_name ?: right.normalized_input
        val sorted = listOf(pairA, pairB).sorted()
        if (!left.resolved || !right.resolved) {
            return@withContext notFound(sorted[0], sorted[1])
        }

        val db = evidenceDb()
        db.rawQuery(
            "SELECT * FROM known_interaction WHERE drug_a = ? AND drug_b = ?",
            arrayOf(sorted[0], sorted[1]),
        ).use { cursor ->
            if (!cursor.moveToFirst()) {
                return@withContext notFound(sorted[0], sorted[1])
            }
            val interactionId = cursor.long("id")
            val effects = selectEffects(db, interactionId, effectLimit)
            val rawSignals = if (rawSignalLimit > 0) {
                selectRawSignals(db, interactionId, rawSignalLimit)
            } else {
                emptyList()
            }
            KnownInteraction(
                found = true,
                drug_a = cursor.string("drug_a"),
                drug_b = cursor.string("drug_b"),
                severity = cursor.string("severity"),
                row_count = cursor.int("row_count"),
                source_regions = jsonList(cursor.string("source_regions_json")),
                evidence_bases = jsonList(cursor.string("evidence_bases_json")),
                source_bases = jsonList(cursor.string("source_bases_json")),
                source_urls = jsonList(cursor.string("source_urls_json")),
                mechanisms = jsonList(cursor.string("mechanisms_json")),
                risk_flags = jsonList(cursor.string("risk_flags_json")),
                dataset_types = jsonList(cursor.string("dataset_types_json")),
                use_case_notes = jsonList(cursor.string("use_case_notes_json")),
                effects = effects,
                raw_signals = rawSignals,
                evidence_source = "ddi_reference",
            )
        }
    }

    override suspend fun listInteractionsForDrug(
        drug: String,
        limit: Int,
        effectLimit: Int,
        minSeverity: String?,
        region: String?,
        riskFlag: String?,
    ): DrugInteractionList = withContext(Dispatchers.IO) {
        val normalized = normalizeMedications(listOf(drug)).first()
        if (!normalized.resolved || normalized.canonical_name == null) {
            return@withContext DrugInteractionList(normalized = normalized, interactions = emptyList())
        }
        val filters = buildInteractionFilters(
            drugCanonical = normalized.canonical_name,
            effect = null,
            minSeverity = minSeverity,
            region = region,
            riskFlag = riskFlag,
        )
        val db = evidenceDb()
        db.rawQuery(
            """
            SELECT ki.drug_a, ki.drug_b
            FROM known_interaction ki
            WHERE ${filters.where}
            ORDER BY ki.severity_rank DESC, ki.row_count DESC, ki.drug_a, ki.drug_b
            LIMIT ?
            """.trimIndent(),
            (filters.params + max(1, limit).toString()).toTypedArray(),
        ).use { cursor ->
            val results = mutableListOf<KnownInteraction>()
            while (cursor.moveToNext()) {
                results += lookupKnownInteraction(
                    cursor.string("drug_a"),
                    cursor.string("drug_b"),
                    effectLimit = effectLimit,
                    rawSignalLimit = 0,
                )
            }
            DrugInteractionList(normalized = normalized, interactions = results)
        }
    }

    override suspend fun buildStructuredReport(
        medicationNames: List<String>,
        effectLimit: Int,
    ): MedicationSafetyReport = withContext(Dispatchers.IO) {
        val normalized = normalizeMedications(medicationNames)
        val unresolved = normalized.filterNot { it.resolved }
        val resolvedUnique = dedupeResolved(normalized)
        val findings = mutableListOf<KnownInteraction>()
        var checkedPairCount = 0

        for (leftIndex in resolvedUnique.indices) {
            for (rightIndex in leftIndex + 1 until resolvedUnique.size) {
                val left = resolvedUnique[leftIndex]
                val right = resolvedUnique[rightIndex]
                if (left.canonical_name == null || right.canonical_name == null) continue
                checkedPairCount += 1
                val interaction = lookupKnownInteraction(left.canonical_name, right.canonical_name, effectLimit)
                if (interaction.found) {
                    findings += interaction
                }
            }
        }

        val ranked = findings.sortedWith(
            compareByDescending<KnownInteraction> { severityRank(it.severity) }
                .thenByDescending { it.row_count }
                .thenBy { it.drug_a }
                .thenBy { it.drug_b },
        )
        MedicationSafetyReport(
            input_medications = medicationNames,
            normalized_medications = normalized,
            unresolved_medications = unresolved,
            checked_pair_count = checkedPairCount,
            findings = ranked,
            overall_severity = overallSeverity(ranked),
            evidence_status = evidenceStatus(ranked, unresolved, checkedPairCount),
            limitations = reportLimitations(unresolved, checkedPairCount, ranked),
        )
    }

    override suspend fun getCommonMedicineProfile(
        name: String,
        limit: Int,
    ): CommonMedicineProfile = withContext(Dispatchers.IO) {
        var normalized = normalizeMedications(listOf(name)).first()
        val aliases = mutableListOf<String>()
        val matches = mutableListOf<CommonMedicineRow>()
        val db = normalizationDb()

        fun loadForDrugId(drugId: Long) {
            db.rawQuery(
                """
                SELECT m.*, d.canonical_name
                FROM india_common_medicine m
                JOIN drug d ON d.id = m.drug_id
                WHERE m.drug_id = ?
                ORDER BY m.source_row_number
                LIMIT ?
                """.trimIndent(),
                arrayOf(drugId.toString(), max(1, limit).toString()),
            ).use { cursor ->
                while (cursor.moveToNext()) {
                    matches += cursor.toCommonMedicineRow()
                }
            }
            db.rawQuery(
                """
                SELECT alias
                FROM drug_alias
                WHERE drug_id = ?
                ORDER BY
                    CASE alias_type WHEN 'canonical' THEN 0 WHEN 'brand' THEN 1 ELSE 2 END,
                    LENGTH(alias),
                    alias
                LIMIT 20
                """.trimIndent(),
                arrayOf(drugId.toString()),
            ).use { cursor ->
                while (cursor.moveToNext()) {
                    aliases += cursor.string("alias")
                }
            }
        }

        if (normalized.resolved && normalized.drug_id != null) {
            loadForDrugId(normalized.drug_id)
        }

        if (matches.isEmpty() && !normalized.resolved) {
            val aliasMatch = searchDrugAliases(name, 1).firstOrNull()
            if (aliasMatch != null) {
                normalized = normalizeMedications(listOf(aliasMatch.canonical)).first()
                normalized.drug_id?.let(::loadForDrugId)
            }
        }

        val profileSearchMatches = if (matches.isEmpty()) searchCommonMedicines(name, limit = limit) else emptyList()
        CommonMedicineProfile(
            query = name,
            normalized = normalized,
            aliases = aliases.distinct(),
            matches = if (matches.isEmpty()) profileSearchMatches else matches,
        )
    }

    override suspend fun searchCommonMedicines(
        query: String?,
        therapeuticCategory: String?,
        otcOrRx: String?,
        nlemOrJanAushadhi: String?,
        riskFlag: String?,
        limit: Int,
    ): List<CommonMedicineRow> = withContext(Dispatchers.IO) {
        val db = normalizationDb()
        val clauses = mutableListOf<String>()
        val params = mutableListOf<String>()

        if (!query.isNullOrBlank()) {
            val normalizedQuery = normalizeLookupText(query)
            val pattern = "%$normalizedQuery%"
            val textPattern = "%${query.lowercase().trim()}%"
            clauses += """
                (
                    m.normalized_generic_name LIKE ?
                    OR lower(m.common_brand_examples_india) LIKE ?
                    OR lower(m.common_daily_life_use_india) LIKE ?
                    OR lower(d.canonical_name) LIKE ?
                )
            """.trimIndent()
            params += listOf(pattern, textPattern, textPattern, textPattern)
        }
        if (!therapeuticCategory.isNullOrBlank()) {
            clauses += "lower(m.therapeutic_category) LIKE ?"
            params += "%${therapeuticCategory.lowercase().trim()}%"
        }
        if (!otcOrRx.isNullOrBlank()) {
            clauses += "lower(m.otc_or_rx) = ?"
            params += otcOrRx.lowercase().trim()
        }
        if (!nlemOrJanAushadhi.isNullOrBlank()) {
            clauses += "lower(m.nlem_or_jan_aushadhi_presence) LIKE ?"
            params += "%${nlemOrJanAushadhi.lowercase().trim()}%"
        }
        if (!riskFlag.isNullOrBlank()) {
            clauses += "lower(m.patient_risk_flags_india) LIKE ?"
            params += "%${riskFlag.lowercase().trim()}%"
        }

        val where = if (clauses.isEmpty()) "1=1" else clauses.joinToString(" AND ")
        db.rawQuery(
            """
            SELECT m.*, d.canonical_name
            FROM india_common_medicine m
            JOIN drug d ON d.id = m.drug_id
            WHERE $where
            ORDER BY d.canonical_name, m.source_row_number
            LIMIT ?
            """.trimIndent(),
            (params + max(1, limit).toString()).toTypedArray(),
        ).use { cursor ->
            buildList {
                while (cursor.moveToNext()) {
                    add(cursor.toCommonMedicineRow())
                }
            }
        }
    }

    override suspend fun listEvidenceSources(): List<EvidenceImportFile> = withContext(Dispatchers.IO) {
        val db = evidenceDb()
        db.rawQuery(
            """
            SELECT source_file, region, rows_seen, rows_imported, rows_unresolved, unique_pairs_imported
            FROM evidence_import_file
            ORDER BY source_file
            """.trimIndent(),
            emptyArray(),
        ).use { cursor ->
            buildList {
                while (cursor.moveToNext()) {
                    add(
                        EvidenceImportFile(
                            source_file = cursor.string("source_file"),
                            region = cursor.string("region"),
                            rows_seen = cursor.int("rows_seen"),
                            rows_imported = cursor.int("rows_imported"),
                            rows_unresolved = cursor.int("rows_unresolved"),
                            unique_pairs_imported = cursor.int("unique_pairs_imported"),
                        ),
                    )
                }
            }
        }
    }

    override fun close() {
        normalizationDb?.close()
        evidenceDb?.close()
        normalizationDb = null
        evidenceDb = null
    }

    private suspend fun normalizationDb(): SQLiteDatabase = openMutex.withLock {
        normalizationDb ?: SQLiteDatabase.openDatabase(
            installedDatabases.normalizationPath,
            null,
            SQLiteDatabase.OPEN_READONLY,
        ).also { normalizationDb = it }
    }

    private suspend fun evidenceDb(): SQLiteDatabase = openMutex.withLock {
        evidenceDb ?: SQLiteDatabase.openDatabase(
            installedDatabases.evidenceMobilePath,
            null,
            SQLiteDatabase.OPEN_READONLY,
        ).also { evidenceDb = it }
    }

    private fun selectEffects(db: SQLiteDatabase, interactionId: Long, effectLimit: Int): List<InteractionEffect> {
        db.rawQuery(
            """
            SELECT adverse_effect, severity, row_count, source_regions_json
            FROM known_interaction_effect
            WHERE known_interaction_id = ?
            ORDER BY severity_rank DESC, row_count DESC, adverse_effect
            LIMIT ?
            """.trimIndent(),
            arrayOf(interactionId.toString(), max(1, effectLimit).toString()),
        ).use { cursor ->
            return buildList {
                while (cursor.moveToNext()) {
                    add(
                        InteractionEffect(
                            adverse_effect = cursor.string("adverse_effect"),
                            severity = cursor.string("severity"),
                            row_count = cursor.int("row_count"),
                            source_regions = jsonList(cursor.string("source_regions_json")),
                        ),
                    )
                }
            }
        }
    }

    private fun selectRawSignals(
        db: SQLiteDatabase,
        interactionId: Long,
        rawSignalLimit: Int,
    ): List<RawDdiSignal> {
        db.rawQuery(
            """
            SELECT *
            FROM ddi_raw_signal
            WHERE known_interaction_id = ?
            ORDER BY severity_rank DESC, source_file, source_row_number
            LIMIT ?
            """.trimIndent(),
            arrayOf(interactionId.toString(), max(1, rawSignalLimit).toString()),
        ).use { cursor ->
            return buildList {
                while (cursor.moveToNext()) {
                    add(
                        RawDdiSignal(
                            source_file = cursor.string("source_file"),
                            source_row_number = cursor.int("source_row_number"),
                            source_signal_id = cursor.string("source_signal_id"),
                            region = cursor.string("region"),
                            drug1_raw = cursor.string("drug1_raw"),
                            drug2_raw = cursor.string("drug2_raw"),
                            adverse_effect = cursor.string("adverse_effect"),
                            severity = cursor.string("severity"),
                            mechanism_or_rationale = cursor.string("mechanism_or_rationale"),
                            interaction_category = cursor.string("interaction_category"),
                            interaction_direction = cursor.string("interaction_direction"),
                            evidence_basis = cursor.string("evidence_basis"),
                            source_basis = cursor.string("source_basis"),
                            source_urls = cursor.string("source_urls"),
                            population_relevance = cursor.string("population_relevance"),
                            patient_risk_flags = cursor.string("patient_risk_flags"),
                            dataset_type = cursor.string("dataset_type"),
                            use_case_note = cursor.string("use_case_note"),
                        ),
                    )
                }
            }
        }
    }

    private fun Cursor.toCommonMedicineRow(): CommonMedicineRow =
        CommonMedicineRow(
            medicine_id = string("medicine_id"),
            canonical_name = string("canonical_name"),
            generic_or_common_name = string("generic_or_common_name"),
            composition_or_strength_pattern = string("composition_or_strength_pattern"),
            dosage_form = string("dosage_form"),
            therapeutic_category = string("therapeutic_category"),
            common_daily_life_use_india = string("common_daily_life_use_india"),
            common_brand_examples_india = string("common_brand_examples_india"),
            availability_context_india = string("availability_context_india"),
            otc_or_rx = string("otc_or_rx"),
            nlem_or_jan_aushadhi_presence = string("nlem_or_jan_aushadhi_presence"),
            india_relevance = string("india_relevance"),
            patient_risk_flags_india = string("patient_risk_flags_india"),
            source_basis = string("source_basis"),
            source_urls = string("source_urls"),
            dataset_note = string("dataset_note"),
        )

    private fun buildInteractionFilters(
        drugCanonical: String?,
        effect: String?,
        minSeverity: String?,
        region: String?,
        riskFlag: String?,
    ): InteractionFilters {
        val clauses = mutableListOf<String>()
        val params = mutableListOf<String>()
        var needsEffectJoin = false

        if (!drugCanonical.isNullOrBlank()) {
            clauses += "(ki.drug_a = ? OR ki.drug_b = ?)"
            params += drugCanonical
            params += drugCanonical
        }
        val rank = inputSeverityRank(minSeverity)
        if (rank > 0) {
            clauses += "ki.severity_rank >= ?"
            params += rank.toString()
        }
        val regions = canonicalizeRegion(region)
        if (regions.isNotEmpty()) {
            clauses += regions.joinToString(" OR ", prefix = "(", postfix = ")") {
                "ki.source_regions_json LIKE ?"
            }
            params += regions.map { "%\"$it\"%" }
        }
        if (!riskFlag.isNullOrBlank()) {
            clauses += "lower(ki.risk_flags_json) LIKE ?"
            params += "%${riskFlag.lowercase().trim()}%"
        }
        if (!effect.isNullOrBlank()) {
            clauses += "lower(kie.adverse_effect) LIKE ?"
            params += "%${effect.lowercase().trim()}%"
            needsEffectJoin = true
        }

        return InteractionFilters(
            where = if (clauses.isEmpty()) "1=1" else clauses.joinToString(" AND "),
            params = params,
            needsEffectJoin = needsEffectJoin,
        )
    }

    private fun canonicalizeRegion(region: String?): List<String> {
        val key = region?.lowercase()?.trim().orEmpty()
        return when (key) {
            "us", "usa", "united states", "united states of america" -> listOf("us")
            "eu", "eea", "europe", "european union", "eu/eea" -> listOf("eu/eea")
            "in", "india" -> listOf("india", "india_expanded", "india_common_generic")
            "india_expanded" -> listOf("india_expanded")
            "india_common_generic" -> listOf("india_common_generic")
            "" -> emptyList()
            else -> listOf(key)
        }
    }

    private fun inputSeverityRank(severity: String?): Int = when (severity?.lowercase()?.trim()) {
        "major", "high" -> 3
        "moderate", "medium" -> 2
        "minor", "low" -> 1
        else -> 0
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

    private fun dedupeResolved(items: List<NormalizedMedication>): List<NormalizedMedication> {
        val seen = mutableSetOf<String>()
        return items.filter { item ->
            val canonical = item.canonical_name
            item.resolved && canonical != null && seen.add(canonical)
        }
    }

    private fun jsonList(raw: String?): List<String> {
        if (raw.isNullOrBlank()) return emptyList()
        return runCatching { json.decodeFromString<List<String>>(raw) }.getOrElse { emptyList() }
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

    private data class InteractionFilters(
        val where: String,
        val params: List<String>,
        val needsEffectJoin: Boolean,
    )

    private data class AliasLookup(
        val drugId: Long,
        val canonicalName: String,
        val alias: String,
    )
}

private fun Cursor.columnIndex(name: String): Int = getColumnIndexOrThrow(name)
private fun Cursor.string(name: String): String = getString(columnIndex(name)) ?: ""
private fun Cursor.int(name: String): Int = getInt(columnIndex(name))
private fun Cursor.long(name: String): Long = getLong(columnIndex(name))
