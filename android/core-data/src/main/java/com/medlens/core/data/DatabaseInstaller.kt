package com.medlens.core.data

import android.content.Context
import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

data class InstalledDatabases(
    val normalizationPath: String,
    val evidenceMobilePath: String,
    val brandIngredientMapPath: String,
)

class DatabaseInstaller(
    private val context: Context,
) {
    suspend fun ensureInstalled(): InstalledDatabases = withContext(Dispatchers.IO) {
        val dbDir = File(context.filesDir, "medlens-db").apply { mkdirs() }
        val normalization = copyIfNeeded("normalization.sqlite", dbDir)
        val evidence = copyIfNeeded("evidence.mobile.sqlite", dbDir)
        val brandIngredientMap = copyIfNeeded("india_common_brand_ingredient_map.csv", dbDir, alwaysCopy = true)
        InstalledDatabases(
            normalizationPath = normalization.absolutePath,
            evidenceMobilePath = evidence.absolutePath,
            brandIngredientMapPath = brandIngredientMap.absolutePath,
        )
    }

    private fun copyIfNeeded(assetName: String, dbDir: File, alwaysCopy: Boolean = false): File {
        val target = File(dbDir, assetName)
        val assetLength = runCatching { context.assets.openFd(assetName).length }.getOrNull()
        if (!alwaysCopy && target.exists() && assetLength != null && target.length() == assetLength) {
            return target
        }
        context.assets.open(assetName).use { input ->
            target.outputStream().use { output ->
                input.copyTo(output)
            }
        }
        return target
    }
}
