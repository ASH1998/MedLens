package com.medlens.android.model

import android.content.Context
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkInfo
import androidx.work.WorkManager
import java.io.File
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

class GemmaModelManager(
    private val context: Context,
) {
    private val workManager = WorkManager.getInstance(context)

    fun observeState(): Flow<ModelState> {
        val target = modelFile()
        if (target.exists() && target.length() == GEMMA_4_E4B_DESCRIPTOR.sizeBytes) {
            return kotlinx.coroutines.flow.flowOf(ModelState.Ready(target.absolutePath))
        }
        return workManager.getWorkInfosForUniqueWorkFlow(WORK_NAME).map { infos ->
            val info = infos.firstOrNull()
            when {
                target.exists() && target.length() == GEMMA_4_E4B_DESCRIPTOR.sizeBytes ->
                    ModelState.Ready(target.absolutePath)
                info == null -> ModelState.NotDownloaded
                info.state == WorkInfo.State.RUNNING || info.state == WorkInfo.State.ENQUEUED ->
                    ModelState.Downloading(
                        progress = info.progress.getFloat(KEY_PROGRESS, 0f).coerceIn(0f, 1f),
                    )
                info.state == WorkInfo.State.SUCCEEDED ->
                    ModelState.Ready(info.outputData.getString("path") ?: target.absolutePath)
                info.state == WorkInfo.State.FAILED ->
                    ModelState.Error(info.outputData.getString("error") ?: "Model download failed")
                else -> ModelState.NotDownloaded
            }
        }
    }

    fun enqueueDownload() {
        val request = OneTimeWorkRequestBuilder<GemmaModelDownloadWorker>().build()
        workManager.enqueueUniqueWork(WORK_NAME, ExistingWorkPolicy.KEEP, request)
    }

    private fun modelFile(): File = File(File(context.filesDir, "models"), GEMMA_4_E4B_DESCRIPTOR.fileName)

    companion object {
        private const val WORK_NAME = "gemma_model_download"
        const val KEY_PROGRESS = "progress"
    }
}
