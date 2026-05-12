package com.medlens.core.data

import com.medlens.core.data.model.AliasSearchResult
import com.medlens.core.data.model.CommonMedicineProfile
import com.medlens.core.data.model.CommonMedicineRow
import com.medlens.core.data.model.DrugInteractionList
import com.medlens.core.data.model.EvidenceImportFile
import com.medlens.core.data.model.KnownInteraction
import com.medlens.core.data.model.MedicationSafetyReport
import com.medlens.core.data.model.NormalizedMedication

interface SafetyRepository {
    suspend fun normalizeMedications(names: List<String>): List<NormalizedMedication>
    suspend fun searchDrugAliases(query: String, limit: Int = 10): List<AliasSearchResult>
    suspend fun lookupKnownInteraction(
        drugA: String,
        drugB: String,
        effectLimit: Int = 8,
        rawSignalLimit: Int = 20,
    ): KnownInteraction
    suspend fun listInteractionsForDrug(
        drug: String,
        limit: Int = 20,
        effectLimit: Int = 3,
        minSeverity: String? = null,
        region: String? = null,
        riskFlag: String? = null,
    ): DrugInteractionList
    suspend fun buildStructuredReport(
        medicationNames: List<String>,
        effectLimit: Int = 8,
    ): MedicationSafetyReport
    suspend fun getCommonMedicineProfile(
        name: String,
        limit: Int = 10,
    ): CommonMedicineProfile
    suspend fun searchCommonMedicines(
        query: String?,
        therapeuticCategory: String? = null,
        otcOrRx: String? = null,
        nlemOrJanAushadhi: String? = null,
        riskFlag: String? = null,
        limit: Int = 10,
    ): List<CommonMedicineRow>
    suspend fun listEvidenceSources(): List<EvidenceImportFile>
    fun close()
}
