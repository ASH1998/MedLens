package com.medlens.android.model

import android.content.Context
import android.util.Log
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkInfo
import androidx.work.WorkManager
import java.io.File
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.map

class GemmaModelManager(
    private val context: Context,
) {
    private val workManager = WorkManager.getInstance(context)

    /**
     * Observes model state. Checks file existence + metadata sidecar first.
     * Falls back to re-verifying SHA if sidecar is missing (first run after
     * upgrade). Returns Error with checksum message if verification fails.
     */
    fun observeState(): Flow<ModelState> {
        val target = modelFile()
        if (target.exists() && target.length() == GEMMA_4_E4B_DESCRIPTOR.sizeBytes) {
            val sidecar = sidecarFile()
            if (sidecar.exists()) {
                // Sidecar present — trust it without re-hashing 3.66 GB
                return flowOf(ModelState.Ready(target.absolutePath))
            }
            // Sidecar missing — verify SHA once and write sidecar
            val actualSha = sha256(target)
            if (actualSha == GEMMA_4_E4B_DESCRIPTOR.sha256) {
                writeSidecar(target, actualSha)
                return flowOf(ModelState.Ready(target.absolutePath))
            }
            // Size matches but SHA is wrong — corrupt file
            Log.e(TAG, "Model file exists with correct size but SHA mismatch: expected=${GEMMA_4_E4B_DESCRIPTOR.sha256}, actual=$actualSha")
            target.delete()
            sidecar.delete()
            return flowOf(ModelState.Error("Model file is corrupt. Please re-download."))
        }
        return workManager.getWorkInfosForUniqueWorkFlow(WORK_NAME).map { infos ->
            val info = infos.firstOrNull()
            when {
                target.exists() && target.length() == GEMMA_4_E4B_DESCRIPTOR.sizeBytes -> {
                    val sidecar = sidecarFile()
                    if (sidecar.exists() || sha256(target) == GEMMA_4_E4B_DESCRIPTOR.sha256) {
                        if (!sidecar.exists()) writeSidecar(target, GEMMA_4_E4B_DESCRIPTOR.sha256)
                        ModelState.Ready(target.absolutePath)
                    } else {
                        target.delete()
                        sidecar.delete()
                        ModelState.Error("Model file is corrupt. Please re-download.")
                    }
                }
                info == null -> ModelState.NotDownloaded
                info.state == WorkInfo.State.RUNNING || info.state == WorkInfo.State.ENQUEUED ->
                    ModelState.Downloading(
                        progress = info.progress.getFloat(KEY_PROGRESS, 0f).coerceIn(0f, 1f),
                    )
                info.state == WorkInfo.State.SUCCEEDED -> {
                    // Download worker already verified SHA, write sidecar
                    writeSidecar(target, GEMMA_4_E4B_DESCRIPTOR.sha256)
                    ModelState.Ready(info.outputData.getString("path") ?: target.absolutePath)
                }
                info.state == WorkInfo.State.FAILED ->
                    ModelState.Error(info.outputData.getString("error") ?: "Model download failed")
                else -> ModelState.NotDownloaded
            }
        }
    }

    /**
     * Enqueues model download. Deletes any corrupt target/partial files first
     * so the worker starts clean.
     */
    fun enqueueDownload() {
        val target = modelFile()
        val partial = File(target.parentFile, target.name + ".part")
        val sidecar = sidecarFile()
        // Clean up corrupt files so download can retry
        if (target.exists()) {
            val actualSha = sha256(target)
            if (actualSha != GEMMA_4_E4B_DESCRIPTOR.sha256) {
                Log.w(TAG, "Deleting corrupt model file before retry")
                target.delete()
                sidecar.delete()
            }
        }
        partial.delete()

        val request = OneTimeWorkRequestBuilder<GemmaModelDownloadWorker>().build()
        workManager.enqueueUniqueWork(WORK_NAME, ExistingWorkPolicy.KEEP, request)
    }

    /**
     * Deletes corrupt model files (target + sidecar) so the user can retry
     * a clean download.
     */
    fun deleteCorruptFiles() {
        val target = modelFile()
        val partial = File(target.parentFile, target.name + ".part")
        val sidecar = sidecarFile()
        target.delete()
        partial.delete()
        sidecar.delete()
        Log.i(TAG, "Deleted model files for retry")
    }

    private fun modelFile(): File = File(File(context.filesDir, "models"), GEMMA_4_E4B_DESCRIPTOR.fileName)

    /**
     * Metadata sidecar — written after successful SHA verification so startup
     * does not need to hash 3.66 GB every time.
     */
    private fun sidecarFile(): File = File(File(context.filesDir, "models"), GEMMA_4_E4B_DESCRIPTOR.fileName + ".verified.json")

    private fun writeSidecar(modelFile: File, sha: String) {
        try {
            sidecarFile().writeText(
                """{"fileName":"${GEMMA_4_E4B_DESCRIPTOR.fileName}","sha256":"$sha","sizeBytes":${modelFile.length()},"verifiedAt":${System.currentTimeMillis()}}""",
            )
        } catch (e: Exception) {
            Log.w(TAG, "Failed to write sidecar: ${e.message}")
        }
    }

    private fun sha256(file: File): String {
        val digest = java.security.MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
            while (true) {
                val read = input.read(buffer)
                if (read <= 0) break
                digest.update(buffer, 0, read)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    companion object {
        private const val TAG = "GemmaModelManager"
        private const val WORK_NAME = "gemma_model_download"
        const val KEY_PROGRESS = "progress"
    }
}
