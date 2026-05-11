package com.medlens.core.data

import android.content.Context
import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

data class InstalledDatabases(
    val normalizationPath: String,
    val evidenceMobilePath: String,
)

class DatabaseInstaller(
    private val context: Context,
) {
    suspend fun ensureInstalled(): InstalledDatabases = withContext(Dispatchers.IO) {
        val dbDir = File(context.filesDir, "medlens-db").apply { mkdirs() }
        val normalization = copyIfNeeded("normalization.sqlite", dbDir)
        val evidence = copyIfNeeded("evidence.mobile.sqlite", dbDir)
        InstalledDatabases(
            normalizationPath = normalization.absolutePath,
            evidenceMobilePath = evidence.absolutePath,
        )
    }

    private fun copyIfNeeded(assetName: String, dbDir: File): File {
        val target = File(dbDir, assetName)
        val assetLength = context.assets.openFd(assetName).length
        if (target.exists() && target.length() == assetLength) {
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
